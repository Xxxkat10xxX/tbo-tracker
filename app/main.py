"""Точка входа: FastAPI-приложение — REST API и одностраничный веб-интерфейс.

Вся бизнес-логика выполняется на сервере (app/services). Доступ — только после входа (сессия + роль),
изменяющие запросы — с CSRF-токеном; правила доступа — app/security.py.
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import DataError, IntegrityError

from . import security
from .api.agent import router as agent_router
from .api.auth import router as auth_router
from .api.drones import router as drones_router
from .api.missions import router as missions_router
from .api.routes import router
from .api.server import router as server_router
from .config import API_DOCS, DATA_DIR, DATABASE_URL, DEMO_MODE, SCHEDULER, UPLOADS_DIR, VERSION
from .db import Base, SessionLocal, engine, migrate, wait_for_database
from .errors import DomainError
from .services.auth import csrf_ok

STATIC = Path(__file__).parent / "static"
log = logging.getLogger("tbo")


class MediaFiles(StaticFiles):
    """Загруженные фото: только .png/.jpg/.webp из корня каталога (служебный index.json и любые другие файлы не отдаются)."""

    async def get_response(self, path: str, scope):
        if os.sep in path or "/" in path or Path(path).suffix.lower() not in (".png", ".jpg", ".webp"):
            raise HTTPException(status_code=404)
        return await super().get_response(path, scope)


def _check_data_dir():
    """Понятная ошибка вместо «unable to open database file», если каталог данных недоступен на запись."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        probe = DATA_DIR / ".write-test"
        probe.write_text("ok")
        probe.unlink()
    except OSError as e:
        raise SystemExit(f"Каталог данных {DATA_DIR} недоступен на запись ({e}). "
                         "В Docker используйте именованный том или выдайте права пользователю uid 10001.") from e


def _startup():
    _check_data_dir()
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    wait_for_database()
    Base.metadata.create_all(engine)
    migrate()
    if DEMO_MODE:
        from .seed import seed_if_empty
        seed_if_empty()
    from .services import auth
    with SessionLocal() as s:
        auth.bootstrap_admin_from_file(s)
        auth.ensure_setup_code(s)
        auth.cleanup(s)
    log.info("tbo-tracker %s: база %s, демо-режим %s", VERSION, DATABASE_URL.split("://")[0],
             "включён" if DEMO_MODE else "выключен")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await run_in_threadpool(_startup)
    stop, task = asyncio.Event(), None
    if SCHEDULER:
        from .services.scheduler import run_forever
        task = asyncio.create_task(run_forever(stop))
    yield
    stop.set()
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task
    from .services import auth
    await run_in_threadpool(auth.flush_audit, 5)      # записать журнал аудита до остановки процесса


app = FastAPI(
    title="tbo-tracker · Обслуживание БПЛА по фактической наработке",
    description="Поэкземплярный учёт ресурса бортов и узлов. Все методы требуют входа; изменения — заголовок X-CSRF-Token.",
    version=VERSION,
    lifespan=lifespan,
    docs_url="/docs" if API_DOCS else None,
    redoc_url=None,
    openapi_url="/openapi.json" if API_DOCS else None,
)


def _resolve(token: str | None):
    from .services import auth
    with SessionLocal() as s:
        return auth.resolve_session(s, token)


def _resolve_device(token: str | None) -> tuple[int | None, str]:
    from .services import drones
    with SessionLocal() as s:
        link = drones.resolve_device(s, token)
        if link is None:
            return None, "bad"
        return (link.id, "") if link.enabled else (None, "disabled")


def _audit_write(method: str, path: str, username: str, ip: str, status: int):
    from .services import auth
    auth.audit("api_write", username, ip, target=f"{method} {path}", status=status)


@app.middleware("http")
async def access_control(request: Request, call_next):
    path, method = request.scope["path"], request.method
    request.state.auth = None
    need = security.required_role(method, path)
    unsafe = method not in security.SAFE_METHODS

    def done(resp):
        security.apply_headers(request, resp)
        return resp

    if not security.host_ok(request):
        return done(security.deny(400, "Неизвестное имя сайта в запросе (TBO_ALLOWED_HOSTS)", "host"))
    if unsafe and not security.origin_ok(request):
        return done(security.deny(403, "Запрос с другого сайта отклонён", "origin"))
    if need == "device":                     # бортовой агент: токен устройства, ограничение неверных попыток
        from .services.drones import auth_limiter
        ip = security.client_ip(request)
        if auth_limiter.blocked(ip):
            return done(security.deny(429, "Слишком много неверных токенов устройства с этого адреса", "device_auth"))
        lid, err = await run_in_threadpool(_resolve_device, security.bearer_token(request))
        if lid is None:
            if err == "bad":
                auth_limiter.hit(ip)
            return done(security.deny(401 if err == "bad" else 403, "Неверный токен устройства" if err == "bad"
                                      else "Подключение борта отключено администратором", "device_auth"))
        request.state.device = lid
        return done(await call_next(request))
    if need is None:
        if unsafe and request.headers.get("x-requested-with") != "tbo":
            return done(security.deny(403, "Запрос должен отправляться из интерфейса tbo-tracker", "csrf"))
        return done(await call_next(request))
    row = await run_in_threadpool(_resolve, security.session_token(request))
    if row is None:
        return done(security.deny(401, "Требуется вход", "auth_required"))
    request.state.auth = row
    user = row.user
    if user.must_change_password and path not in security.PASSWORD_CHANGE_ONLY:
        return done(security.deny(403, "Смените временный пароль, чтобы продолжить", "password_change_required"))
    if security.ROLE_RANK.get(user.role, -1) < security.ROLE_RANK[need]:
        return done(security.deny(403, "Недостаточно прав для этого действия", "forbidden"))
    if unsafe and not csrf_ok(row, request.headers.get("x-csrf-token")):
        return done(security.deny(403, "Сессия устарела — обновите страницу", "csrf"))
    resp = await call_next(request)
    if unsafe and path.startswith("/api/") and not path.startswith("/api/auth/"):
        await run_in_threadpool(_audit_write, method, path, user.username, security.client_ip(request), resp.status_code)
    return done(resp)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_req: Request, exc: RequestValidationError):
    """422 без эха входных значений: NaN/Infinity во входе иначе ломают сериализацию ответа (500)."""
    return JSONResponse(status_code=422, content={"detail": [
        {"loc": list(e.get("loc", [])), "msg": e.get("msg", ""), "type": e.get("type", "")} for e in exc.errors()]})


@app.exception_handler(DomainError)
async def domain_error_handler(_req: Request, exc: DomainError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


@app.exception_handler(DataError)
async def data_error_handler(_req: Request, exc: DataError):
    log.warning("DataError: %s", str(exc.orig)[:300])
    return JSONResponse(status_code=400, content={"detail": "Значение не подходит для хранения (слишком длинное или неверного формата)"})


@app.exception_handler(IntegrityError)
async def integrity_error_handler(_req: Request, exc: IntegrityError):
    log.warning("IntegrityError: %s", str(exc.orig)[:300])
    return JSONResponse(status_code=409, content={"detail": "Нарушение целостности данных (повтор или ссылка на несуществующий объект)"})


@app.exception_handler(Exception)
async def unhandled_error_handler(_req: Request, exc: Exception):
    log.exception("Необработанная ошибка")
    return JSONResponse(status_code=500, content={"detail": "Внутренняя ошибка сервера. Подробности — в журнале сервиса"})


app.include_router(auth_router)
app.include_router(router)
app.include_router(missions_router)
app.include_router(server_router)
app.include_router(drones_router)
app.include_router(agent_router)
app.add_middleware(security.BodyLimitMiddleware)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.mount("/media", MediaFiles(directory=UPLOADS_DIR, check_dir=False), name="media")   # каталог создаётся при запуске


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(STATIC / "favicon.svg", media_type="image/svg+xml")


@app.get("/health", include_in_schema=False)
def health():
    return {"status": "ok"}


if os.environ.get("TBO_LOG_LEVEL", "").strip().lower() in ("critical", "error", "warning", "info", "debug"):
    logging.getLogger("tbo").setLevel(os.environ["TBO_LOG_LEVEL"].strip().upper())
