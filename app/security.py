"""Сетевая безопасность: доступ по сессии и ролям, CSRF, заголовки, лимит размера запросов, доверенные прокси.

Правила доступа собраны в одном месте (``required_role``) и проверяются тестом для каждого маршрута API.
"""
from __future__ import annotations

import ipaddress
import logging
import re
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .config import ALLOWED_HOSTS, API_DOCS, COOKIE_SECURE, MAX_BACKUP_UPLOAD_MB, MAX_UPLOAD_MB, PUBLIC_URL, TRUSTED_PROXIES

log = logging.getLogger("tbo.security")

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
COOKIE = "tbo_session"
COOKIE_SECURE_NAME = "__Host-tbo_session"        # префикс __Host-: только Secure, только этот хост, путь /

# Без входа: оболочка интерфейса, статика, проверка работоспособности, вход и первичная настройка
PUBLIC_EXACT = {"/", "/health", "/favicon.ico", "/api/auth/state", "/api/auth/login", "/api/auth/setup"}
PUBLIC_PREFIX = ("/static/",)
# Только администратор (в том числе чтение): сервер, копии, пользователи, аудит, демо-сброс, сценарии, документация
ADMIN_PREFIX = ("/api/server/", "/api/auth/users", "/api/audit", "/api/demo/", "/api/scenarios", "/api/analytics",
                "/docs", "/redoc", "/openapi.json")
# Изменения, доступные любой роли (свой пароль, выход)
ANY_ROLE_WRITE = {"/api/auth/logout", "/api/auth/password"}
# При обязательной смене пароля доступно только это
PASSWORD_CHANGE_ONLY = {"/api/auth/me", "/api/auth/password", "/api/auth/logout", "/api/auth/state"}
# Бортовой агент: только токен устройства (Authorization: Bearer), без сессии и CSRF
AGENT_PREFIX = "/api/agent/"
# Подключения бортов: создание, токен, отключение, удаление — администратор (команды полёта — инженер)
DRONE_ADMIN = re.compile(r"^/api/drones(?:/[^/]+(?:/token)?)?$")


def is_public(path: str) -> bool:
    return path in PUBLIC_EXACT or path.startswith(PUBLIC_PREFIX)


def required_role(method: str, path: str) -> str | None:
    """None — без входа; 'viewer' — любой вошедший; 'engineer' — изменения данных; 'admin' — администрирование."""
    if is_public(path):
        return None
    if path.startswith(AGENT_PREFIX):
        return "device"
    if path.startswith(ADMIN_PREFIX):
        return "admin"
    if method not in SAFE_METHODS and DRONE_ADMIN.match(path):
        return "admin"
    if method not in SAFE_METHODS and path not in ANY_ROLE_WRITE:
        return "engineer"
    return "viewer"


ROLE_RANK = {"viewer": 0, "engineer": 1, "admin": 2}


# ------------------------------------------------------------------ адрес клиента и схема (с учётом прокси)
def _trusted(ip: str | None) -> bool:
    if not ip or not TRUSTED_PROXIES:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in TRUSTED_PROXIES)


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else ""
    if not _trusted(peer):
        return peer
    chain = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    for hop in reversed(chain):          # самый правый адрес, не являющийся нашим прокси
        if not _trusted(hop):
            return hop[:64]
    return peer


def is_https(request: Request) -> bool:
    if PUBLIC_URL.startswith("https://"):
        return True
    if request.url.scheme == "https":
        return True
    peer = request.client.host if request.client else ""
    return _trusted(peer) and request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"


def cookie_secure(request: Request) -> bool:
    if COOKIE_SECURE == "1":
        return True
    if COOKIE_SECURE == "0":
        return False
    return is_https(request)


def session_token(request: Request) -> str | None:
    return request.cookies.get(COOKIE_SECURE_NAME) or request.cookies.get(COOKIE)


def bearer_token(request: Request) -> str | None:
    h = request.headers.get("authorization", "")
    return h[7:].strip() if h[:7].lower() == "bearer " else None


LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def host_ok(request: Request) -> bool:
    """TBO_ALLOWED_HOSTS: запрос адресован нашему сайту (страница чужого домена, «перепривязанного» на этот сервер,
    получит отказ). Без настройки — любые имена."""
    if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
        return True
    raw = request.headers.get("host", "").strip().lower()
    if not raw or any(ch in raw for ch in "@/\\ ?#"):
        return False
    try:
        name = (urlsplit("//" + raw).hostname or "").rstrip(".")
    except ValueError:
        return False
    if name in LOOPBACK_HOSTS or name in ALLOWED_HOSTS:
        return True
    return bool(PUBLIC_URL) and name == (urlsplit(PUBLIC_URL).hostname or "")


def origin_ok(request: Request) -> bool:
    """Запрос из нашего же интерфейса: заголовок Origin (если есть) указывает на этот хост или TBO_PUBLIC_URL."""
    origin = request.headers.get("origin")
    if not origin:
        return True                     # не браузерный клиент или запрос без Origin (дополнительно — CSRF-токен)
    host = urlsplit(origin).netloc.lower()
    allowed = {request.headers.get("host", "").lower()}
    if PUBLIC_URL:
        allowed.add(urlsplit(PUBLIC_URL).netloc.lower())
    return host in allowed


def deny(status: int, detail: str, code: str | None = None) -> JSONResponse:
    body = {"detail": detail}
    if code:
        body["code"] = code
    return JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store"})


# ------------------------------------------------------------------ заголовки безопасности
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; font-src 'self'; "
       "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
# Swagger UI (только если включён TBO_API_DOCS) грузится с CDN и использует встроенные скрипты
CSP_DOCS = ("default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: https://fastapi.tiangolo.com; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")


def apply_headers(request: Request, response: Response):
    h = response.headers
    path = request.url.path
    h["X-Content-Type-Options"] = "nosniff"
    h["X-Frame-Options"] = "DENY"
    h["Referrer-Policy"] = "no-referrer"
    h["Cross-Origin-Opener-Policy"] = "same-origin"
    h["Cross-Origin-Resource-Policy"] = "same-origin"
    h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()"
    h["Content-Security-Policy"] = CSP_DOCS if API_DOCS and path.startswith(("/docs", "/redoc")) else CSP
    if is_https(request):
        h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if path.startswith(("/api/", "/media/")) or path == "/":
        h["Cache-Control"] = "no-store"
    elif path.startswith("/static/"):
        h.setdefault("Cache-Control", "no-cache")


# ------------------------------------------------------------------ лимит размера тела запроса (чистый ASGI)
class BodyLimitMiddleware:
    """413 для тел больше лимита; 411 для изменяющих запросов без Content-Length (потоковые тела не принимаем)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in SAFE_METHODS or scope["method"] == "DELETE":
            return await self.app(scope, receive, send)
        limit_mb = MAX_BACKUP_UPLOAD_MB if scope["path"] == "/api/server/backups/upload" else MAX_UPLOAD_MB
        limit = limit_mb * 1024 * 1024
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        length = headers.get("content-length")
        if length is None:
            if "chunked" in headers.get("transfer-encoding", "").lower():
                return await _plain(send, 411, "Нужен заголовок Content-Length")
            length = "0"
        try:
            declared = int(length)
        except ValueError:
            return await _plain(send, 400, "Неверный Content-Length")
        if declared > limit:
            return await _plain(send, 413, f"Запрос больше {limit_mb} МБ")
        received = 0

        async def limited_receive():
            nonlocal received
            msg = await receive()
            if msg["type"] == "http.request":
                received += len(msg.get("body", b""))
                if received > max(declared, 0) or received > limit:
                    return {"type": "http.disconnect"}
            return msg

        return await self.app(scope, limited_receive, send)


async def _plain(send, status: int, text: str):
    body = ('{"detail": "%s"}' % text).encode("utf-8")
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json; charset=utf-8"), (b"content-length", str(len(body)).encode()),
                            (b"cache-control", b"no-store")]})
    await send({"type": "http.response.body", "body": body})
