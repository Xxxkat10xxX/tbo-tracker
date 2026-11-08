"""Точка входа: FastAPI-приложение, раздающее REST API и одностраничный веб-интерфейс.

Вся бизнес-логика выполняется на сервере (app/services): расчёт наработки и ресурса, регламенты,
уведомления и их рассылка, перестановки узлов, сценарии проверки, резервные копии.
Браузер только отображает данные и отправляет действия пользователя.
"""
import asyncio
import base64
import binascii
import secrets
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .api.missions import router as missions_router
from .api.routes import router
from .api.server import router as server_router
from .config import AUTH_PASSWORD, AUTH_USER, AUTO_SEED, SCHEDULER, UPLOADS_DIR, VERSION
from .db import Base, engine, migrate
from .errors import DomainError

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(engine)
    migrate()
    if AUTO_SEED:
        from .seed import seed_if_empty
        seed_if_empty()
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


app = FastAPI(
    title="tbo-tracker · Обслуживание БПЛА по фактической наработке",
    description="Прототип модуля «Виртуального гаража автономных систем»: поэкземплярный учёт ресурса бортов и узлов.",
    version=VERSION,
    lifespan=lifespan,
)


def _authorized(header: str) -> bool:
    if not header.startswith("Basic "):
        return False
    try:
        user, _, pwd = base64.b64decode(header[6:]).decode("utf-8").partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return False
    return secrets.compare_digest(user.encode(), AUTH_USER.encode()) & secrets.compare_digest(pwd.encode(), AUTH_PASSWORD.encode())


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    """Вход по паролю для всего сервиса (кроме /health), если задан TBO_AUTH_PASSWORD."""
    if AUTH_PASSWORD and request.url.path != "/health" and not _authorized(request.headers.get("authorization", "")):
        return Response("Требуется вход: имя пользователя и пароль tbo-tracker", status_code=401,
                        headers={"WWW-Authenticate": 'Basic realm="tbo-tracker", charset="UTF-8"'},
                        media_type="text/plain; charset=utf-8")
    return await call_next(request)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_req: Request, exc: RequestValidationError):
    """422 без эха входных значений: NaN/Infinity во входе иначе ломают сериализацию ответа (500)."""
    return JSONResponse(status_code=422, content={"detail": [
        {"loc": list(e.get("loc", [])), "msg": e.get("msg", ""), "type": e.get("type", "")} for e in exc.errors()]})


@app.exception_handler(DomainError)
async def domain_error_handler(_req: Request, exc: DomainError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


app.include_router(router)
app.include_router(missions_router)
app.include_router(server_router)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=UPLOADS_DIR), name="media")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/health", include_in_schema=False)
def health():
    return {"status": "ok", "version": VERSION}
