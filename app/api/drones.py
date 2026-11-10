"""API подключённых бортов: состояние и телеметрия (все роли), команды полёта (инженер), подключение (администратор).

Права — app/security.py: просмотр — любой вошедший; prepare/start/rtl/land/cancel — инженер и администратор;
создание подключения, новый токен, отключение и удаление — только администратор.
"""
from __future__ import annotations

import io
import tarfile
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response, StreamingResponse
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import BASE_DIR
from ..db import SessionLocal, get_session
from ..errors import DomainError, NotFound
from ..models import DroneLink
from ..schemas import BaseModel
from ..security import client_ip
from ..services import drones, video
from ..services.auth import audit
from ..timeutil import utcnow

router = APIRouter(prefix="/api")


class LinkIn(BaseModel):
    aircraft_id: int


class LinkPatch(BaseModel):
    enabled: bool


class PrepareIn(BaseModel):
    mission_id: int


class StartIn(BaseModel):
    run_id: int
    plan_hash: str = Field(min_length=64, max_length=64)
    confirm: Literal[True]          # оператор подтвердил: зона взлёта свободна, пилот с пультом на месте


def _who(request: Request) -> str:
    return request.state.auth.user.username


def _done(request: Request, action: str, link: DroneLink, details: str | None = None):
    audit(action, _who(request), client_ip(request), target=link.aircraft.name, details=details)


AGENT_FILES = ("install.sh", "run-agent.sh", "tbo-agent.service", "tbo-agent.conf.example", "README.md")


@router.get("/downloads/tbo-agent.tar.gz")
def agent_bundle():
    """Бортовой агент для установки на борт (только код и установщик — без токенов и настроек сервера)."""
    src = BASE_DIR / "agent"
    if not (src / "tbo_agent").is_dir():
        raise NotFound("Файлы агента не найдены в установке сервера (каталог agent/)")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        def add(path, arc: str, mode: int):
            data = path.read_bytes()
            info = tarfile.TarInfo(arc)
            info.size, info.mode, info.mtime = len(data), mode, int(path.stat().st_mtime)
            tar.addfile(info, io.BytesIO(data))
        for f in sorted((src / "tbo_agent").glob("*.py")):
            add(f, f"tbo-agent/tbo_agent/{f.name}", 0o644)
        for name in AGENT_FILES:
            if (src / name).is_file():
                add(src / name, f"tbo-agent/{name}", 0o755 if name.endswith(".sh") else 0o644)
    return Response(buf.getvalue(), media_type="application/gzip",
                    headers={"Content-Disposition": 'attachment; filename="tbo-agent.tar.gz"'})


@router.get("/drones")
def list_links(s: Session = Depends(get_session)):
    drones.sweep(s)
    now = utcnow()
    return [drones.link_dict(s, link, now) for link in s.scalars(select(DroneLink).order_by(DroneLink.id))]


@router.get("/drones/{lid}")
def get_link(lid: int, s: Session = Depends(get_session)):
    drones.sweep(s)
    return drones.link_dict(s, drones.get_link(s, lid), detail=True)


def _video_ip(lid: int) -> str | None:
    """Адрес борта для видео: подключение включено и борт на связи (адрес — с последнего обмена агента)."""
    with SessionLocal() as s:
        link = drones.get_link(s, lid)
        if not link.enabled:
            raise DomainError("Подключение борта отключено администратором", 409)
        if not drones.online(link):
            raise DomainError(f"Борт {link.aircraft.name} не на связи — видео недоступно", 409)
        return link.last_ip


@router.get("/drones/video/streams")
def video_streams():
    return video.streams()


@router.get("/drones/{lid}/video")
async def drone_video(lid: int, request: Request, stream: str = Query("main", max_length=20), probe: bool = False):
    """Видео с борта (MJPEG) через сервер. probe=1 — только проверить доступность потока (JSON)."""
    ip = await run_in_threadpool(_video_ip, lid)
    if probe:
        return await video.probe(ip, stream)
    ctype, body = await video.open_stream(ip, stream, request.is_disconnected)
    return StreamingResponse(body, media_type=ctype, headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.post("/drones", status_code=201)
def create_link(data: LinkIn, request: Request, s: Session = Depends(get_session)):
    link, token = drones.create_link(s, data.aircraft_id, _who(request))
    s.commit()
    _done(request, "drone_link_created", link)
    return {**drones.link_dict(s, link), "token": token,
            "message": "Токен показан один раз: сохраните его в файл токена на борту (install.sh спросит его скрытым вводом)"}


@router.post("/drones/{lid}/token")
def new_token(lid: int, request: Request, s: Session = Depends(get_session)):
    link = drones.get_link(s, lid)
    with drones.guard(s, link):
        if link.state in drones.FLYING and drones.online(link):
            raise DomainError("Борт в полёте — смените токен после посадки")
        token = drones.rotate_token(s, link)
    _done(request, "drone_token_rotated", link)
    return {**drones.link_dict(s, link), "token": token, "message": "Новый токен показан один раз; прежний больше не действует"}


@router.patch("/drones/{lid}")
def update_link(lid: int, data: LinkPatch, request: Request, s: Session = Depends(get_session)):
    link = drones.get_link(s, lid)
    with drones.guard(s, link):
        if not data.enabled and link.state in drones.FLYING and drones.online(link):
            raise DomainError("Борт в полёте — отключить связь можно после посадки")
        link.enabled = data.enabled
    _done(request, "drone_link_updated", link, f"enabled={data.enabled}")
    return drones.link_dict(s, link)


@router.delete("/drones/{lid}")
def delete_link(lid: int, request: Request, s: Session = Depends(get_session)):
    link = drones.get_link(s, lid)
    with drones.guard(s, link):
        if drones.active_run(s, link):
            raise DomainError("У борта подготовленный или выполняемый полёт — дождитесь окончания или отмените подготовку")
        name = link.aircraft.name
        _done(request, "drone_link_deleted", link)
        s.delete(link)
    return {"message": f"Подключение борта {name} удалено; токен больше не действует"}


@router.post("/drones/{lid}/prepare")
def prepare(lid: int, data: PrepareIn, request: Request, s: Session = Depends(get_session)):
    link = drones.get_link(s, lid)
    with drones.guard(s, link):
        run, cmd = drones.prepare(s, link, data.mission_id, _who(request))
    _done(request, "drone_prepare", link, f"миссия «{run.summary.get('mission_name')}», полёт R{run.id}")
    return {**drones.link_dict(s, link), "message": "План отправлен на борт: загрузка в контроллер и проверки…"}


@router.post("/drones/{lid}/start")
def start(lid: int, data: StartIn, request: Request, s: Session = Depends(get_session)):
    link = drones.get_link(s, lid)
    with drones.guard(s, link):
        drones.start(s, link, data.run_id, data.plan_hash, _who(request))
    _done(request, "drone_start", link, f"полёт R{data.run_id}")
    return {**drones.link_dict(s, link), "message": "Команда «Старт» отправлена борту"}


def _abort(kind: str):
    def handler(lid: int, request: Request, s: Session = Depends(get_session)):
        link = drones.get_link(s, lid)
        with drones.guard(s, link):
            drones.abort(s, link, kind, _who(request))
        _done(request, f"drone_{kind}", link)
        msg = "Команда «Вернуть домой» отправлена" if kind == "rtl" else "Команда «Посадить» отправлена"
        if not drones.online(link):
            msg += " — борт сейчас не на связи, команда будет доставлена при восстановлении связи (до 2 мин)"
        return {**drones.link_dict(s, link), "message": msg}
    handler.__name__ = f"drone_{kind}"
    return handler


router.post("/drones/{lid}/rtl")(_abort("rtl"))
router.post("/drones/{lid}/land")(_abort("land"))


@router.post("/drones/{lid}/cancel")
def cancel(lid: int, request: Request, s: Session = Depends(get_session)):
    link = drones.get_link(s, lid)
    with drones.guard(s, link):
        cmd = drones.cancel(s, link, _who(request))
    if cmd is None:
        _done(request, "drone_flight_closed", link, "нет связи с бортом в полёте")
        return {**drones.link_dict(s, link), "message": "Полёт закрыт как «не выполнен»: журнал не получен. "
                                                         "Если борт передаст журнал позже, наработка будет учтена"}
    _done(request, "drone_cancel", link)
    return {**drones.link_dict(s, link), "message": "Подготовка отменена; план на борту будет удалён"}
