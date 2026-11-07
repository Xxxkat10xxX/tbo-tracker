"""Точка входа: FastAPI-приложение, раздающее REST API и одностраничный веб-интерфейс."""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .config import AUTO_SEED
from .db import Base, engine
from .errors import DomainError

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(engine)
    if AUTO_SEED:
        from .seed import seed_if_empty
        seed_if_empty()
    yield


app = FastAPI(
    title="СОКОЛ · Обслуживание БПЛА по фактической наработке",
    description="Прототип модуля «Виртуального гаража автономных систем»: поэкземплярный учёт ресурса бортов и узлов.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.exception_handler(DomainError)
async def domain_error_handler(_req: Request, exc: DomainError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


app.include_router(router)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/health", include_in_schema=False)
def health():
    return {"status": "ok"}
