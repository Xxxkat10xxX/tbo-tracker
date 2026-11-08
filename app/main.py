"""Точка входа: FastAPI-приложение, раздающее REST API и одностраничный веб-интерфейс."""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.missions import router as missions_router
from .api.routes import router
from .config import AUTO_SEED, UPLOADS_DIR
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
    yield


app = FastAPI(
    title="tbo-tracker · Обслуживание БПЛА по фактической наработке",
    description="Прототип модуля «Виртуального гаража автономных систем»: поэкземплярный учёт ресурса бортов и узлов.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.exception_handler(DomainError)
async def domain_error_handler(_req: Request, exc: DomainError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


app.include_router(router)
app.include_router(missions_router)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=UPLOADS_DIR), name="media")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/health", include_in_schema=False)
def health():
    return {"status": "ok"}
