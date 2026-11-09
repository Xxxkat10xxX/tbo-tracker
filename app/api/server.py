"""Серверные операции: доска перестановки, сценарии, лента уведомлений, состояние и настройки сервера, резервные копии."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import Field
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_session
from ..errors import DomainError, require_demo_mode
from ..schemas import BaseModel, EconomicsIn
from ..services import backup, board, notify, scenarios, scheduler, serverconf, sysinfo
from ..services.analytics import analytics, estimate

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------- доска перестановки
class MoveTarget(BaseModel):
    kind: Literal["slot", "stock", "maintenance"]
    aircraft_id: int | None = None
    position: str | None = None


class MoveIn(BaseModel):
    component_id: int
    target: MoveTarget
    at: datetime | None = None
    reason: str | None = Field(None, max_length=500)
    performed_by: str | None = Field(None, max_length=200)


@router.post("/board/plan")
def board_plan(data: MoveIn, s: Session = Depends(get_session)):
    """Что произойдёт при перетаскивании: шаги операции, минимально допустимое и предлагаемое время."""
    return board.plan(s, data.component_id, data.target.model_dump())


@router.post("/board/move")
def board_move(data: MoveIn, s: Session = Depends(get_session)):
    """Выполняет операцию целиком в одной транзакции и возвращает паспорта узлов «было → стало»."""
    try:
        res = board.execute(s, data.component_id, data.target.model_dump(), data.at, data.reason, data.performed_by)
        s.commit()
    except Exception:
        s.rollback()
        raise
    return res


# ---------------------------------------------------------------- демо-сценарии
@router.get("/scenarios")
def scenario_list():
    require_demo_mode()
    return [{"n": n, "title": t} for n, t in scenarios.TITLES.items()]


@router.post("/scenarios/{n}/run")
def scenario_run(n: int, s: Session = Depends(get_session)):
    require_demo_mode()
    return scenarios.run(s, n)


# ---------------------------------------------------------------- расчёты
@router.post("/analytics/estimate")
def analytics_estimate(data: EconomicsIn, s: Session = Depends(get_session)):
    """Калькулятор эффекта с произвольными параметрами (без сохранения)."""
    return estimate(analytics(s)["kpi"], data.model_dump())


# ---------------------------------------------------------------- уведомления: лента и журнал
def _deliver_bg():
    with SessionLocal() as s:
        notify.deliver_pending(s)


@router.get("/notifications/feed")
def notifications_feed(bg: BackgroundTasks, after: int | None = None, s: Session = Depends(get_session)):
    """Проверяет уведомления сейчас и возвращает новые записи после ``after`` (без него — только last_id)."""
    res = notify.feed(s, after)
    if res["events"]:
        bg.add_task(_deliver_bg)
    from .routes import notifications
    res["counts"] = notifications(90, s)["counts"]
    return res


@router.get("/notifications/history")
def notifications_history(limit: int = Query(100, ge=1, le=1000), s: Session = Depends(get_session)):
    return notify.history(s, limit)


# ---------------------------------------------------------------- сервер
@router.get("/server/status")
def server_status():
    return {**sysinfo.status(), "scheduler": {**scheduler.STATE}}


class TelegramIn(BaseModel):
    enabled: bool = False
    token: str = Field("", max_length=200)
    chat_id: str = Field("", max_length=100)


class WebhookIn(BaseModel):
    enabled: bool = False
    url: str = Field("", max_length=500)


class BackupConfIn(BaseModel):
    enabled: bool = True
    hour: int = Field(3, ge=0, le=23)
    keep: int = Field(14, ge=1, le=365)


class ServerSettingsIn(BaseModel):
    scan_interval_s: int = Field(60, ge=15, le=3600)
    categories: list[Literal["limit", "overdue", "approaching", "sensor"]] = ["limit", "overdue", "sensor"]
    telegram: TelegramIn = TelegramIn()
    webhook: WebhookIn = WebhookIn()
    backup: BackupConfIn = BackupConfIn()
    public_url: str = Field("", max_length=300)


@router.get("/server/settings")
def server_settings():
    return serverconf.masked()


@router.put("/server/settings")
def server_settings_put(data: ServerSettingsIn):
    # всё проверяем до записи: отклонённый запрос не должен менять сохранённые настройки
    if data.webhook.enabled and not data.webhook.url.startswith(("http://", "https://")):
        raise DomainError("Адрес webhook должен начинаться с http:// или https://")
    if data.public_url and not data.public_url.startswith(("http://", "https://")):
        raise DomainError("Адрес сайта должен начинаться с http:// или https://")
    if data.telegram.enabled:
        new_token = data.telegram.token if data.telegram.token and not data.telegram.token.startswith("•") else ""
        if not ((new_token or serverconf.load()["telegram"]["token"]) and data.telegram.chat_id):
            raise DomainError("Для Telegram нужны токен бота и chat_id")
    return serverconf.masked(serverconf.save(data.model_dump()))


@router.post("/server/notify/test")
def server_notify_test():
    conf = serverconf.load()
    res = notify.send([{"category": "overdue", "title": "Проверка канала уведомлений tbo-tracker", "text": "тестовое сообщение",
                        "href": "#/maintenance"}], conf)
    if not res:
        raise DomainError("Нет включённых каналов: включите Telegram или webhook и сохраните настройки")
    return {"result": res}


@router.get("/server/backups")
def backups_list():
    return backup.list_backups()


@router.post("/server/backups", status_code=201)
def backups_create():
    b = backup.create("manual")
    return {**b, "message": f"Резервная копия создана: {b['name']}"}


@router.post("/server/backups/upload", status_code=201)
async def backups_upload(file: UploadFile = File(...)):
    content = await file.read()
    if len(content) > 512 * 1024 * 1024:
        raise DomainError("Файл больше 512 МБ")
    b = backup.save_upload(content)
    return {**b, "message": f"Копия загружена на сервер: {b['name']}. Нажмите «Восстановить», чтобы применить её."}


@router.get("/server/backups/{name}")
def backups_download(name: str):
    return FileResponse(backup.path_of(name), filename=name, media_type="application/zip")


@router.delete("/server/backups/{name}")
def backups_delete(name: str):
    backup.delete(name)
    return {"message": f"Копия {name} удалена"}


@router.post("/server/backups/{name}/restore")
def backups_restore(name: str):
    return backup.restore(name)
