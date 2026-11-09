"""API бортового агента tbo-agent. Доступ — по токену устройства (Authorization: Bearer …), не по сессии.

Агент управляет только своим бортом: подключение определяется токеном, номера полётов и команд сверяются.
Все поля ограничены по размеру и диапазону (NaN/Infinity не принимаются — агент передаёт null).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import Field, field_validator
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_session
from ..errors import DomainError
from ..models import DroneLink
from ..schemas import BaseModel
from ..security import client_ip
from ..services import dronelog, drones
from ..timeutil import iso, parse_dt, utcnow

router = APIRouter(prefix="/api/agent")


def _small_dict(v: dict, limit: int = 4000) -> dict:
    if len(json.dumps(v, ensure_ascii=False, default=str)) > limit:
        raise ValueError(f"слишком большой объект (более {limit} байт)")
    return v


class HelloIn(BaseModel):
    agent_version: str = Field("", max_length=40)
    executor: str = Field("", max_length=40)
    vehicle: dict = Field(default_factory=dict)

    @field_validator("vehicle")
    @classmethod
    def limit_vehicle(cls, v):
        return _small_dict(v)


class CheckIn(BaseModel):
    name: str = Field(max_length=80)
    ok: bool
    detail: str = Field("", max_length=300)


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


class ImuIn(BaseModel):
    rate_hz: float = Field(ge=1, le=1000)
    t0_s: float = Field(0, ge=-60, le=86400)
    accel_g: list[float] = Field(max_length=2_000_000)


class EventIn(BaseModel):
    type: str = Field(max_length=20)
    t_s: float = Field(ge=0, le=86400)
    peak_g: float | None = Field(None, ge=0, le=200)


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
    link.agent = {"version": data.agent_version, "executor": data.executor, "vehicle": data.vehicle, "hello_at": iso(utcnow())}
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
