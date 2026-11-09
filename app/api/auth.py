"""API входа, учётных записей и журнала аудита."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import API_DOCS, DEMO_MODE, SESSION_MAX_HOURS
from ..db import get_session
from ..errors import DomainError
from ..models import ROLES, User
from ..schemas import BaseModel
from ..security import COOKIE, COOKIE_SECURE_NAME, client_ip, cookie_secure, session_token
from ..services import auth

router = APIRouter(prefix="/api")


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class SetupIn(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    username: str = Field(min_length=2, max_length=64)
    display_name: str = Field("", max_length=200)
    password: str = Field(min_length=1, max_length=256)


class PasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class UserIn(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    display_name: str = Field("", max_length=200)
    role: Literal["admin", "engineer", "viewer"] = "viewer"
    password: str = Field(min_length=1, max_length=256)
    must_change_password: bool = True


class UserPatch(BaseModel):
    display_name: str | None = Field(None, max_length=200)
    role: Literal["admin", "engineer", "viewer"] | None = None
    is_active: bool | None = None


class ResetIn(BaseModel):
    password: str = Field(min_length=1, max_length=256)
    must_change_password: bool = True


def current(request: Request) -> auth.AuthSession:
    row = getattr(request.state, "auth", None)
    if row is None:
        raise DomainError("Требуется вход", 401)
    return row


def _login_response(request: Request, s: Session, user: User, payload: dict) -> JSONResponse:
    old = session_token(request)
    if old:
        auth.end_session(s, old)
    token, row = auth.create_session(s, user, client_ip(request), request.headers.get("user-agent"))
    s.commit()
    resp = JSONResponse({**payload, **auth.session_dict(row)}, headers={"Cache-Control": "no-store"})
    secure = cookie_secure(request)
    resp.set_cookie(COOKIE_SECURE_NAME if secure else COOKIE, token, max_age=SESSION_MAX_HOURS * 3600, path="/",
                    httponly=True, samesite="strict", secure=secure)
    return resp


def _clear_cookies(resp):
    resp.delete_cookie(COOKIE, path="/")
    resp.delete_cookie(COOKIE_SECURE_NAME, path="/", secure=True, httponly=True, samesite="strict")


@router.get("/auth/state")
def auth_state(request: Request, s: Session = Depends(get_session)):
    """Что показать интерфейсу: первичную настройку, вход или приложение."""
    row = auth.resolve_session(s, session_token(request))
    return {"setup_required": auth.setup_required(s), "user": auth.user_dict(row.user) if row else None,
            "csrf_token": row.csrf_token if row else None, "demo_mode": DEMO_MODE, "api_docs": API_DOCS,
            "roles": ROLES}         # версию без входа не сообщаем (она — на странице «Сервер»)


@router.post("/auth/setup")
def auth_setup(data: SetupIn, request: Request, s: Session = Depends(get_session)):
    ip = client_ip(request)
    with auth.setup_lock:
        if not auth.setup_required(s):
            raise DomainError("Первичная настройка уже выполнена — войдите", 409)
        if not auth.check_setup_code(data.code, ip):
            auth.audit("setup_failed", None, ip, details="неверный код настройки")
            raise DomainError("Неверный код настройки. Код — в журнале сервиса и в файле setup-code.txt каталога данных", 403)
        user = auth.create_user(s, data.username, data.password, "admin", data.display_name or "Администратор")
        s.commit()
        auth.finish_setup()
    auth.audit("user_created", user.username, ip, target=user.username, details="первичная настройка, роль admin")
    return _login_response(request, s, user, {"message": "Администратор создан"})


@router.post("/auth/login")
def auth_login(data: LoginIn, request: Request, s: Session = Depends(get_session)):
    ip = client_ip(request)
    try:
        user = auth.authenticate(s, data.username, data.password, ip)
    except auth.AuthError as e:
        auth.audit("login_failed", (data.username or "").strip().lower()[:64], ip, status=e.status_code, details=e.message)
        raise
    resp = _login_response(request, s, user, {"message": "Вход выполнен"})
    auth.audit("login", user.username, ip, status=200)
    return resp


@router.post("/auth/logout")
def auth_logout(request: Request, s: Session = Depends(get_session)):
    row = getattr(request.state, "auth", None)
    if row is not None:
        auth.audit("logout", row.user.username, client_ip(request))
    auth.end_session(s, session_token(request))
    resp = JSONResponse({"message": "Выход выполнен"}, headers={"Cache-Control": "no-store"})
    _clear_cookies(resp)
    return resp


@router.get("/auth/me")
def auth_me(request: Request):
    return auth.session_dict(current(request))


@router.post("/auth/password")
def auth_password(data: PasswordIn, request: Request, s: Session = Depends(get_session)):
    row = current(request)
    user = s.get(User, row.user_id)
    if auth.password_limiter.blocked(user.username):
        auth.end_session(s, session_token(request))          # подбор пароля из чужой сессии — выход
        raise DomainError("Слишком много неверных попыток. Войдите снова", 401)
    if not auth.verify_password(user.password_hash, data.current_password):
        auth.password_limiter.hit(user.username)
        auth.audit("password_change_failed", user.username, client_ip(request), details="неверный текущий пароль")
        raise DomainError("Текущий пароль указан неверно", 403)
    auth.password_limiter.reset(user.username)
    if data.new_password == data.current_password:
        raise DomainError("Новый пароль должен отличаться от текущего")
    auth.set_password(s, user, data.new_password, must_change=False, keep_session_id=row.id)
    s.commit()
    auth.audit("password_changed", user.username, client_ip(request), target=user.username)
    return {"message": "Пароль изменён. Сессии на других устройствах завершены"}


# ---------------------------------------------------------------- учётные записи (администратор)
def _actor(request: Request, s: Session) -> User:
    return s.get(User, current(request).user_id)


@router.get("/auth/users")
def users_list(s: Session = Depends(get_session)):
    return [auth.user_dict(u) for u in s.scalars(select(User).order_by(User.username))]


@router.post("/auth/users", status_code=201)
def users_create(data: UserIn, request: Request, s: Session = Depends(get_session)):
    actor = _actor(request, s)
    u = auth.create_user(s, data.username, data.password, data.role, data.display_name, must_change=data.must_change_password)
    s.commit()
    auth.audit("user_created", actor.username, client_ip(request), target=u.username, details=f"роль {u.role}")
    return {**auth.user_dict(u), "message": f"Пользователь {u.username} создан"}


@router.patch("/auth/users/{uid}")
def users_update(uid: int, data: UserPatch, request: Request, s: Session = Depends(get_session)):
    actor = _actor(request, s)
    target = auth.get_user(s, uid)
    auth.update_user(s, actor, target, data.display_name, data.role, data.is_active)
    s.commit()
    auth.audit("user_updated", actor.username, client_ip(request), target=target.username,
               details=", ".join(f"{k}={v}" for k, v in data.model_dump(exclude_none=True).items()))
    return {**auth.user_dict(target), "message": "Изменения сохранены"}


@router.post("/auth/users/{uid}/password")
def users_reset_password(uid: int, data: ResetIn, request: Request, s: Session = Depends(get_session)):
    actor = _actor(request, s)
    target = auth.get_user(s, uid)
    auth.set_password(s, target, data.password, must_change=data.must_change_password)
    s.commit()
    auth.audit("password_reset", actor.username, client_ip(request), target=target.username)
    return {"message": f"Пароль пользователя {target.username} изменён, его сессии завершены"}


@router.post("/auth/users/{uid}/unlock")
def users_unlock(uid: int, request: Request, s: Session = Depends(get_session)):
    actor = _actor(request, s)
    target = auth.get_user(s, uid)
    auth.unlock(s, target)
    s.commit()
    auth.audit("user_unlocked", actor.username, client_ip(request), target=target.username)
    return {"message": f"Учётная запись {target.username} разблокирована"}


@router.delete("/auth/users/{uid}")
def users_delete(uid: int, request: Request, s: Session = Depends(get_session)):
    actor = _actor(request, s)
    target = auth.get_user(s, uid)
    name = target.username
    auth.delete_user(s, actor, target)
    s.commit()
    auth.audit("user_deleted", actor.username, client_ip(request), target=name)
    return {"message": f"Пользователь {name} удалён"}


@router.get("/audit")
def audit_log(limit: int = Query(200, ge=1, le=1000), username: str | None = Query(None, max_length=64),
              action: str | None = Query(None, max_length=64), before_id: int | None = None,
              s: Session = Depends(get_session)):
    return auth.audit_list(s, limit, username, action, before_id)
