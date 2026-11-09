"""Учётные записи и сессии.

* Пароли — только хеш Argon2id (RFC 9106: t=3, m=64 МиБ, p=2) с солью; параметры хранятся в самом хеше,
  при их усилении хеш пересчитывается при следующем входе.
* Сессия — случайный токен 256 бит в cookie (HttpOnly, SameSite=Strict, Secure при HTTPS); в базе хранится
  только SHA-256 от токена. Ограничения: бездействие и предельный срок.
* Перебор паролей: блокировка учётной записи после N неудач (срок удваивается), лимит попыток с одного
  адреса, одинаковое время ответа для существующих и несуществующих имён.
* Паролей по умолчанию нет. Первый администратор создаётся: одноразовым кодом настройки (печатается в журнал
  сервиса и в файл с правами 600), командой ``python -m app.admin create-user`` или из файла-секрета
  (TBO_ADMIN_PASSWORD_FILE, Docker secrets).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import queue
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..config import (
    ADMIN_PASSWORD_FILE,
    ADMIN_USERNAME,
    AUDIT_RETENTION_DAYS,
    DATA_DIR,
    LOGIN_LOCK_MIN,
    LOGIN_MAX_FAILS,
    SESSION_IDLE_MIN,
    SESSION_MAX_HOURS,
)
from ..errors import DomainError, NotFound
from ..models import ROLES, AuditLog, AuthSession, User
from ..timeutil import iso, utcnow

log = logging.getLogger("tbo.auth")

_ph = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=2, hash_len=32, salt_len=16)
_hash_slots = threading.BoundedSemaphore(2)      # не больше двух хеширований одновременно (память Raspberry Pi)
_dummy_hash: str | None = None

USERNAME_RE = re.compile(r"[a-z0-9][a-z0-9._-]{1,63}")
PASSWORD_MIN, PASSWORD_MAX = 10, 128
COMMON_PASSWORDS = {
    "password", "password1", "password123", "passw0rd", "p@ssw0rd", "qwerty", "qwerty123", "qwertyuiop",
    "123456", "1234567", "12345678", "123456789", "1234567890", "0123456789", "111111", "1111111111",
    "123123", "123123123", "000000", "0000000000", "abc123", "abcdef", "iloveyou", "admin", "admin123",
    "admin1234", "administrator", "root", "toor", "letmein", "welcome", "welcome1", "monkey", "dragon",
    "football", "baseball", "sunshine", "princess", "master", "superman", "trustno1", "changeme", "default",
    "secret", "qazwsx", "1q2w3e4r", "1q2w3e4r5t", "zaq12wsx", "asdfghjkl", "zxcvbnm", "пароль", "пароль123",
    "йцукен", "йцукенгшщз", "фывапролд", "qwerty12345", "1qaz2wsx", "q1w2e3r4", "q1w2e3r4t5", "aa123456",
    "tbo-tracker", "tbotracker", "raspberry", "raspberrypi",
}


class AuthError(DomainError):
    pass


# ------------------------------------------------------------------ пароли
def hash_password(password: str) -> str:
    with _hash_slots:
        return _ph.hash(password)


def _dummy() -> str:
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password(secrets.token_hex(16))
    return _dummy_hash


def verify_password(stored_hash: str, password: str) -> bool:
    with _hash_slots:
        try:
            return _ph.verify(stored_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False


def password_problems(password: str, username: str = "", display_name: str = "") -> list[str]:
    p = password or ""
    out = []
    if len(p) < PASSWORD_MIN:
        out.append(f"не короче {PASSWORD_MIN} символов")
    if len(p) > PASSWORD_MAX:
        out.append(f"не длиннее {PASSWORD_MAX} символов")
    low = p.lower()
    if low in COMMON_PASSWORDS or low.rstrip("!.1234567890") in COMMON_PASSWORDS:
        out.append("слишком распространённый пароль")
    if p and len(set(p)) < 4:
        out.append("слишком мало разных символов")
    for word in (username, display_name):
        w = (word or "").strip().lower()
        if len(w) >= 3 and w in low:
            out.append("не должен содержать имя пользователя")
            break
    if any(ord(ch) < 32 for ch in p):
        out.append("недопустимые управляющие символы")
    return out


def validate_password(password: str, username: str = "", display_name: str = ""):
    probs = password_problems(password, username, display_name)
    if probs:
        raise DomainError("Пароль не подходит: " + "; ".join(probs))


def normalize_username(username: str) -> str:
    u = (username or "").strip().lower()
    if not USERNAME_RE.fullmatch(u):
        raise DomainError("Имя пользователя: 2–64 символа — латиница, цифры, точка, дефис, подчёркивание")
    return u


# ------------------------------------------------------------------ учётные записи
def user_dict(u: User) -> dict:
    now = utcnow()
    return {"id": u.id, "username": u.username, "display_name": u.display_name, "role": u.role,
            "role_label": ROLES.get(u.role, u.role), "is_active": u.is_active,
            "must_change_password": u.must_change_password,
            "locked": bool(u.locked_until and u.locked_until > now), "locked_until": iso(u.locked_until),
            "created_at": iso(u.created_at), "last_login_at": iso(u.last_login_at),
            "password_changed_at": iso(u.password_changed_at)}


def users_count(s: Session) -> int:
    return s.scalar(select(func.count(User.id))) or 0


def active_admins(s: Session) -> int:
    return s.scalar(select(func.count(User.id)).where(User.role == "admin", User.is_active.is_(True))) or 0


def create_user(s: Session, username: str, password: str, role: str, display_name: str | None = None,
                must_change: bool = False) -> User:
    uname = normalize_username(username)
    if role not in ROLES:
        raise DomainError("Роль: admin, engineer или viewer")
    if s.scalar(select(User.id).where(User.username == uname)):
        raise DomainError("Пользователь с таким именем уже есть", 409)
    name = (display_name or "").strip()[:200] or uname
    validate_password(password, uname, name)
    now = utcnow()
    u = User(username=uname, display_name=name, password_hash=hash_password(password), role=role, is_active=True,
             must_change_password=must_change, failed_attempts=0, lock_count=0, created_at=now, password_changed_at=now)
    s.add(u)
    s.flush()
    return u


def set_password(s: Session, user: User, password: str, must_change: bool = False, keep_session_id: int | None = None):
    validate_password(password, user.username, user.display_name)
    user.password_hash = hash_password(password)
    user.must_change_password = must_change
    user.password_changed_at = utcnow()
    user.failed_attempts, user.locked_until, user.lock_count = 0, None, 0
    q = delete(AuthSession).where(AuthSession.user_id == user.id)     # выход на всех остальных устройствах
    if keep_session_id is not None:
        q = q.where(AuthSession.id != keep_session_id)
    s.execute(q)
    s.flush()


def get_user(s: Session, uid: int) -> User:
    u = s.get(User, uid)
    if u is None:
        raise NotFound("Пользователь не найден")
    return u


def update_user(s: Session, actor: User, target: User, display_name=None, role=None, is_active=None):
    if role is not None and role not in ROLES:
        raise DomainError("Роль: admin, engineer или viewer")
    demote = role is not None and role != "admin" and target.role == "admin"
    disable = is_active is False and target.is_active
    if target.id == actor.id and (demote or disable):
        raise DomainError("Нельзя понизить роль или отключить собственную учётную запись")
    if (demote or disable) and target.role == "admin" and target.is_active and active_admins(s) <= 1:
        raise DomainError("Должен остаться хотя бы один активный администратор")
    if display_name is not None:
        target.display_name = display_name.strip()[:200] or target.username
    if role is not None:
        target.role = role
    if is_active is not None:
        target.is_active = is_active
        if not is_active:
            s.execute(delete(AuthSession).where(AuthSession.user_id == target.id))
    if role is not None:      # смена прав — пересоздать сессии
        s.execute(delete(AuthSession).where(AuthSession.user_id == target.id))
    s.flush()


def delete_user(s: Session, actor: User, target: User):
    if target.id == actor.id:
        raise DomainError("Нельзя удалить собственную учётную запись")
    if target.role == "admin" and target.is_active and active_admins(s) <= 1:
        raise DomainError("Должен остаться хотя бы один активный администратор")
    s.execute(delete(AuthSession).where(AuthSession.user_id == target.id))
    s.delete(target)
    s.flush()


# ------------------------------------------------------------------ защита от перебора
class RateLimiter:
    """Скользящее окно в памяти процесса (сервис работает одним процессом)."""

    MAX_KEYS = 20000            # потолок памяти при распределённом переборе (множество адресов и имён)

    def __init__(self, limit: int, window_s: float):
        self.limit, self.window = limit, window_s
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, q: deque, now: float):
        while q and q[0] <= now - self.window:
            q.popleft()

    def blocked(self, key: str) -> float:
        """Сколько секунд ждать (0 — можно)."""
        now = time.monotonic()
        with self._lock:
            q = self._hits.get(key)
            if not q:
                return 0
            self._prune(q, now)
            return (q[0] + self.window - now) if len(q) >= self.limit else 0

    def hit(self, key: str):
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            self._prune(q, now)
            q.append(now)
            if len(self._hits) > self.MAX_KEYS:
                for k in list(self._hits):              # сначала — ключи без попыток в текущем окне
                    if not self._hits[k] or self._hits[k][-1] <= now - self.window:
                        del self._hits[k]
                while len(self._hits) > self.MAX_KEYS // 2:     # всё ещё много — забываем самые старые ключи
                    del self._hits[next(iter(self._hits))]      # (блокировка учётных записей в базе при этом действует)

    def reset(self, key: str):
        with self._lock:
            self._hits.pop(key, None)


ip_limiter = RateLimiter(20, 15 * 60)                         # неудачных входов с одного адреса
name_limiter = RateLimiter(LOGIN_MAX_FAILS, LOGIN_LOCK_MIN * 60)   # для несуществующих имён — как блокировка
setup_limiter = RateLimiter(10, 15 * 60)
password_limiter = RateLimiter(LOGIN_MAX_FAILS, LOGIN_LOCK_MIN * 60)   # подбор текущего пароля при смене


def _lock_message(seconds: float) -> str:
    return f"Слишком много неудачных попыток. Повторите через {max(1, int(seconds // 60) + 1)} мин."


def authenticate(s: Session, username: str, password: str, ip: str) -> User:
    generic = AuthError("Неверное имя пользователя или пароль", 401)
    wait = ip_limiter.blocked(ip)
    if wait:
        raise AuthError(f"Слишком много попыток входа с этого адреса. Повторите через {int(wait // 60) + 1} мин.", 429)
    uname = (username or "").strip().lower()[:64]
    user = s.scalar(select(User).where(User.username == uname)) if uname else None
    now = utcnow()
    if user is None:
        verify_password(_dummy(), password or "")                # одинаковое время ответа
        ip_limiter.hit(ip)
        wait = name_limiter.blocked(uname)
        name_limiter.hit(uname)
        if wait or name_limiter.blocked(uname):
            raise AuthError(_lock_message(wait or LOGIN_LOCK_MIN * 60), 429)
        raise generic
    if user.locked_until and user.locked_until > now:
        verify_password(_dummy(), password or "")                # время ответа не выдаёт заблокированную запись
        ip_limiter.hit(ip)
        raise AuthError(_lock_message((user.locked_until - now).total_seconds()), 429)
    ok = verify_password(user.password_hash, password or "")
    if not ok or not user.is_active:
        ip_limiter.hit(ip)
        if ok and not user.is_active:
            raise AuthError("Учётная запись отключена администратором", 403)
        user.failed_attempts = (user.failed_attempts or 0) + 1
        if user.failed_attempts >= LOGIN_MAX_FAILS:
            minutes = min(LOGIN_LOCK_MIN * (2 ** (user.lock_count or 0)), 24 * 60)
            user.locked_until = now + timedelta(minutes=minutes)
            user.lock_count = (user.lock_count or 0) + 1
            user.failed_attempts = 0
            s.commit()
            audit("account_locked", user.username, ip, target=user.username, details=f"на {minutes} мин")
            raise AuthError(_lock_message(minutes * 60), 429)
        s.commit()
        raise generic
    if _ph.check_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.failed_attempts, user.locked_until, user.lock_count = 0, None, 0
    user.last_login_at = now
    s.flush()
    return user


def unlock(s: Session, user: User):
    user.failed_attempts, user.locked_until, user.lock_count = 0, None, 0
    name_limiter.reset(user.username)
    s.flush()


# ------------------------------------------------------------------ сессии
def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(s: Session, user: User, ip: str | None, user_agent: str | None) -> tuple[str, AuthSession]:
    token = secrets.token_urlsafe(32)
    now = utcnow()
    row = AuthSession(token_hash=_token_hash(token), user_id=user.id, csrf_token=secrets.token_urlsafe(32),
                      created_at=now, last_seen_at=now, expires_at=now + timedelta(hours=SESSION_MAX_HOURS),
                      ip=(ip or "")[:64], user_agent=(user_agent or "")[:300])
    s.add(row)
    s.flush()
    return token, row


def resolve_session(s: Session, token: str | None) -> AuthSession | None:
    if not token or len(token) > 200:
        return None
    row = s.scalar(select(AuthSession).where(AuthSession.token_hash == _token_hash(token)))
    if row is None:
        return None
    now = utcnow()
    if row.expires_at <= now or row.last_seen_at + timedelta(minutes=SESSION_IDLE_MIN) <= now or not row.user.is_active:
        s.delete(row)
        s.commit()
        return None
    if (now - row.last_seen_at).total_seconds() > 60:          # реже пишем — бережём накопитель
        row.last_seen_at = now
        s.commit()
    return row


def end_session(s: Session, token: str | None):
    if token:
        s.execute(delete(AuthSession).where(AuthSession.token_hash == _token_hash(token)))
        s.commit()


def csrf_ok(session_row: AuthSession, header_value: str | None) -> bool:
    if not header_value:
        return False
    # сравнение байтов: строки с не-ASCII символами compare_digest не принимает (TypeError → 500)
    return hmac.compare_digest(session_row.csrf_token.encode(), header_value.encode("utf-8", "replace"))


def cleanup(s: Session):
    now = utcnow()
    s.execute(delete(AuthSession).where(AuthSession.expires_at <= now))
    s.execute(delete(AuthSession).where(AuthSession.last_seen_at <= now - timedelta(minutes=SESSION_IDLE_MIN)))
    s.execute(delete(AuditLog).where(AuditLog.at < now - timedelta(days=AUDIT_RETENTION_DAYS)))
    s.commit()


# ------------------------------------------------------------------ аудит
# Запись аудита — через очередь и отдельный поток: запрос никогда не ждёт (в SQLite запись другого
# соединения блокировалась бы до завершения транзакции самого запроса).
_audit_q: "queue.Queue[dict]" = queue.Queue(maxsize=20000)
_audit_thread: threading.Thread | None = None
_audit_start = threading.Lock()


def _audit_writer():
    from ..db import SessionLocal
    while True:
        item = _audit_q.get()
        try:
            for attempt in range(5):
                try:
                    with SessionLocal() as s:
                        s.add(AuditLog(**item))
                        s.commit()
                    break
                except Exception:  # noqa: BLE001 — база занята/недоступна: повтор с паузой
                    if attempt == 4:
                        log.exception("audit write failed: %s", item.get("action"))
                    time.sleep(0.5 * (attempt + 1))
        finally:
            _audit_q.task_done()


def audit(action: str, username: str | None, ip: str | None, target: str | None = None, status: int | None = None,
          details: str | None = None):
    """Поставить запись в журнал аудита (асинхронно; порядок записей сохраняется)."""
    global _audit_thread
    if _audit_thread is None:
        with _audit_start:
            if _audit_thread is None:
                _audit_thread = threading.Thread(target=_audit_writer, name="tbo-audit", daemon=True)
                _audit_thread.start()
    item = {"at": utcnow(), "username": (username or None) and username[:64], "ip": (ip or None) and ip[:64],
            "action": action[:64], "target": (target or None) and target[:300], "status": status,
            "details": (details or None) and details[:2000]}
    try:
        _audit_q.put_nowait(item)
    except queue.Full:
        log.error("audit queue full, dropped: %s %s", action, target)


def flush_audit(timeout: float = 10.0):
    """Дождаться записи всех поставленных в очередь событий (журнал, тесты, завершение работы)."""
    deadline = time.monotonic() + timeout
    while _audit_q.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(0.02)


def audit_list(s: Session, limit: int = 200, username: str | None = None, action: str | None = None,
               before_id: int | None = None) -> list[dict]:
    flush_audit(3)
    q = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    if username:
        q = q.where(AuditLog.username == username.lower())
    if action:
        q = q.where(AuditLog.action == action)
    if before_id:
        q = q.where(AuditLog.id < before_id)
    return [{"id": a.id, "at": iso(a.at), "username": a.username, "ip": a.ip, "action": a.action, "target": a.target,
             "status": a.status, "details": a.details} for a in s.scalars(q)]


# ------------------------------------------------------------------ первичная настройка
SETUP_FILE = DATA_DIR / "setup-code.txt"
_setup = {"hash": None}
setup_lock = threading.Lock()      # первичная настройка — строго один раз (параллельные запросы с верным кодом)
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def ensure_setup_code(s: Session) -> str | None:
    """Если учётных записей нет — выпускает одноразовый код настройки (в журнал и файл с правами 600)."""
    if users_count(s):
        _setup["hash"] = None
        SETUP_FILE.unlink(missing_ok=True)
        return None
    code = "-".join("".join(secrets.choice(_ALPHABET) for _ in range(4)) for _ in range(5))   # ~100 бит
    _setup["hash"] = _token_hash(code)
    try:
        SETUP_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(SETUP_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(code + "\n")
    except OSError:
        log.exception("setup code file")
    log.warning("\n%s\n tbo-tracker: учётных записей нет. Откройте сайт и создайте администратора.\n"
                " Одноразовый код настройки: %s\n (также в файле %s)\n%s", "=" * 64, code, SETUP_FILE, "=" * 64)
    return code


def setup_required(s: Session) -> bool:
    return users_count(s) == 0


def check_setup_code(code: str, ip: str) -> bool:
    if setup_limiter.blocked(ip):
        raise AuthError("Слишком много попыток. Повторите через 15 минут.", 429)
    h = _setup["hash"]
    ok = bool(h) and hmac.compare_digest(h, _token_hash((code or "").strip().upper()))
    if not ok:
        setup_limiter.hit(ip)
    return ok


def finish_setup():
    _setup["hash"] = None
    SETUP_FILE.unlink(missing_ok=True)


def bootstrap_admin_from_file(s: Session) -> bool:
    """TBO_ADMIN_PASSWORD_FILE (Docker secret): создать администратора при пустой базе."""
    if not ADMIN_PASSWORD_FILE or users_count(s):
        return False
    try:
        password = open(ADMIN_PASSWORD_FILE, encoding="utf-8").read().strip("\r\n")
    except OSError as e:
        log.error("TBO_ADMIN_PASSWORD_FILE: не удалось прочитать файл: %s", e)
        return False
    try:
        create_user(s, ADMIN_USERNAME, password, "admin", "Администратор")
    except DomainError as e:
        log.error("Администратор из TBO_ADMIN_PASSWORD_FILE не создан: %s", e.message)
        s.rollback()
        return False
    s.commit()
    audit("user_created", "system", None, target=ADMIN_USERNAME.lower(), details="из файла-секрета при первом запуске")
    log.warning("Создан администратор «%s» из файла-секрета. Файл можно удалить.", ADMIN_USERNAME.lower())
    return True


def now_iso() -> str:
    return iso(utcnow())


def session_dict(row: AuthSession) -> dict:
    return {"user": user_dict(row.user), "csrf_token": row.csrf_token, "expires_at": iso(row.expires_at),
            "idle_minutes": SESSION_IDLE_MIN}


__all__ = ["AuthError", "authenticate", "create_session", "resolve_session", "end_session", "csrf_ok", "audit",
           "create_user", "set_password", "update_user", "delete_user", "unlock", "user_dict", "session_dict",
           "ensure_setup_code", "setup_required", "check_setup_code", "finish_setup", "bootstrap_admin_from_file",
           "cleanup", "audit_list", "get_user", "password_problems", "validate_password", "normalize_username",
           "flush_audit"]
