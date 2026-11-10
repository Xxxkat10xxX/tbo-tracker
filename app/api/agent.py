"""API бортового агента tbo-agent. Доступ — по токену устройства (Authorization: Bearer …), не по сессии.

Агент управляет только своим бортом: подключение определяется токеном, номера полётов и команд сверяются.
Все поля ограничены по размеру и диапазону (NaN/Infinity не принимаются — агент передаёт null).
"""
from __future__ import annotations

import asyncio
import json
import math
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_session
from ..errors import DomainError
from ..models import DroneLink
from ..schemas import BaseModel
from ..security import client_ip
from ..services import dronelog, drones
from ..timeutil import iso, parse_dt, utcnow

router = APIRouter(prefix="/api/agent")


def _finite(v):
    """NaN/∞ в свободных полях — null: PostgreSQL (JSONB) их не принимает, и журнал полёта отклонился бы целиком."""
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, dict):
        return {k: _finite(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_finite(x) for x in v]
    return v


def _small_dict(v: dict, limit: int = 4000) -> dict:
    v = _finite(v)
    if len(json.dumps(v, ensure_ascii=False, default=str)) > limit:
        raise ValueError(f"слишком большой объект (более {limit} байт)")
    return v


def _track_point_ok(p) -> bool:
    return (isinstance(p, (list, tuple)) and len(p) == 6 and isinstance(p[5], str)
            and all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in p[:5]))


class HelloIn(BaseModel):
    agent_version: str = Field("", max_length=40)
    executor: str = Field("", max_length=40)
    vehicle: dict = Field(default_factory=dict)
    # возможности агента (например, yaw — курс в плане); прежние агенты их не сообщают
    features: list[Annotated[str, Field(max_length=40)]] = Field(default_factory=list, max_length=30)

    @field_validator("vehicle")
    @classmethod
    def limit_vehicle(cls, v):
        return _small_dict(v)


class CheckIn(BaseModel):
    name: str = Field(max_length=80)
    ok: bool
    detail: str = Field("", max_length=300)
    warn: bool = False                      # предупреждение: не запрещает старт


class TelemetryIn(BaseModel):
    connected: bool | None = None
    armed: bool | None = None
    mode: str | None = Field(None, max_length=40)
    in_air: bool | None = None
    lat: float | None = Field(None, ge=-90, le=90)
    lon: float | None = Field(None, ge=-180, le=180)
    alt_rel: float | None = Field(None, ge=-1000, le=10000)
    heading: float | None = Field(None, ge=-360, le=720)
    speed: float | None = Field(None, ge=0, le=300)
    climb: float | None = Field(None, ge=-200, le=200)
    battery_pct: float | None = Field(None, ge=0, le=100)
    battery_v: float | None = Field(None, ge=0, le=200)
    gps_fix: int | None = Field(None, ge=-1, le=10)
    sats: int | None = Field(None, ge=0, le=200)
    home_dist_m: float | None = Field(None, ge=0, le=1e7)
    wp_current: int | None = Field(None, ge=0, le=10000)
    wp_total: int | None = Field(None, ge=0, le=10000)
    flight_time_s: float | None = Field(None, ge=0, le=1e7)
    status_text: str | None = Field(None, max_length=200)


class StateIn(BaseModel):
    state: Literal[drones.AGENT_STATES]  # type: ignore[valid-type]
    detail: str | None = Field(None, max_length=500)
    run_id: int | None = None
    plan_hash: str | None = Field(None, max_length=64)
    checks: list[CheckIn] | None = Field(None, max_length=30)
    telemetry: TelemetryIn | None = None


class ResultIn(BaseModel):
    ok: bool
    message: str = Field("", max_length=500)


# Данные датчиков проверяет анализ (services/sensors.py): ошибка в них — предупреждение в итогах импорта, а не 422.
# Иначе один сбойный отсчёт отклонил бы журнал целиком: агент отложил бы его в rejected/, и полёт с наработкой
# не попал бы в учёт (правило «некорректные данные датчиков не отменяют учёт полёта», LOG_FORMAT.md).
class ImuIn(BaseModel):
    model_config = ConfigDict(allow_inf_nan=True)        # сбойный отсчёт NaN/∞ — пропуск в анализе, не 422
    rate_hz: float | None = None
    t0_s: float | None = 0
    accel_g: list[float | None] = Field(max_length=2_000_000)


class EventIn(BaseModel):
    model_config = ConfigDict(allow_inf_nan=True)
    type: str = Field(max_length=100)
    t_s: float | None = None
    peak_g: float | None = None


class FlightIn(BaseModel):
    start: datetime
    end: datetime
    imu: ImuIn | None = None
    events: list[EventIn] = Field(default_factory=list, max_length=1000)


class LogIn(BaseModel):
    status: Literal["completed", "aborted"]
    reason: str | None = Field(None, max_length=500)
    flight: FlightIn | None = None
    track: list[tuple[float, float, float, float, float, Annotated[str, Field(max_length=20)]]] = \
        Field(default_factory=list, max_length=50_000)
    summary: dict = Field(default_factory=dict)

    @field_validator("track", mode="before")
    @classmethod
    def drop_bad_points(cls, v):
        """Трек — только для карты: точка с NaN/∞ или неверной формы отбрасывается, а не отклоняет журнал
        (иначе агент отложил бы журнал в rejected/, и полёт с наработкой не попал бы в учёт)."""
        return [p for p in v if _track_point_ok(p)] if isinstance(v, list) else v

    @field_validator("summary")
    @classmethod
    def limit_summary(cls, v):
        return _small_dict(v, 64000)


def device(request: Request, s: Session = Depends(get_session)) -> DroneLink:
    link = s.get(DroneLink, getattr(request.state, "device", None) or 0)
    if link is None or not link.enabled:
        raise DomainError("Устройство не авторизовано", 401)
    return link


def _seen(link: DroneLink, request: Request):
    link.last_seen_at, link.last_ip = utcnow(), client_ip(request)


@router.post("/hello")
def hello(data: HelloIn, request: Request, link: DroneLink = Depends(device), s: Session = Depends(get_session)):
    _seen(link, request)
    if data.executor != "check":           # «tbo-agent check» только проверяет связь — сведения об агенте не трогает
        link.agent = {"version": data.agent_version, "executor": data.executor, "vehicle": data.vehicle,
                      "features": data.features, "hello_at": iso(utcnow())}
    s.commit()
    a = link.aircraft
    return {"link_id": link.id, "aircraft": {"serial": a.serial, "name": a.name}, "server_time": iso(utcnow()),
            "active_run_id": link.run_id, "poll_wait_s": 8}


def _take(link_id: int) -> dict | None:
    with SessionLocal() as s:
        link = s.get(DroneLink, link_id)
        if link is None or not link.enabled:
            return None
        with drones.guard(s, link):
            return drones.next_command(s, link, utcnow())


@router.get("/commands")
async def commands(request: Request, wait: int = Query(8, ge=0, le=25)):
    """Долгий опрос: команда сразу, как только она появилась (проверка раз в секунду), иначе пустой ответ.
    Агент ждёт 8 с — меньше, чем сервер даёт запросам при остановке (10 с), чтобы перезапуск был чистым."""
    lid = request.state.device
    deadline = asyncio.get_running_loop().time() + wait
    while True:
        cmd = await run_in_threadpool(_take, lid)
        if cmd or asyncio.get_running_loop().time() >= deadline or await request.is_disconnected():
            return {"command": cmd, "server_time": iso(utcnow())}
        await asyncio.sleep(1)


@router.post("/commands/{cid}/result")
def command_result(cid: int, data: ResultIn, request: Request, link: DroneLink = Depends(device),
                   s: Session = Depends(get_session)):
    with drones.guard(s, link):
        _seen(link, request)
        drones.command_result(s, link, cid, data.ok, data.message, utcnow())
    return {"ok": True, "server_time": iso(utcnow())}


@router.post("/state")
def state(data: StateIn, request: Request, link: DroneLink = Depends(device), s: Session = Depends(get_session)):
    payload = data.model_dump()
    if data.telemetry is not None:
        payload["telemetry"] = data.telemetry.model_dump(exclude_none=True)
    with drones.guard(s, link):
        return drones.agent_state(s, link, payload, client_ip(request), utcnow())


@router.post("/runs/{rid}/log")
def upload_log(rid: int, data: LogIn, request: Request, link: DroneLink = Depends(device), s: Session = Depends(get_session)):
    payload = data.model_dump()
    if data.flight is not None and parse_dt(data.flight.end) <= parse_dt(data.flight.start):
        raise DomainError("Окончание полёта раньше начала")
    payload["track"] = [list(p) for p in data.track]
    with drones.guard(s, link):
        _seen(link, request)
        res = dronelog.ingest(s, link, rid, payload, utcnow())
    return {**res, "server_time": iso(utcnow())}
