"""Связь с бортами: агент tbo-agent на бортовом компьютере и команды полёта по миссии.

Агент (каталог agent/) работает на бортовом компьютере (Orange Pi 5 Pro: ROS Noetic, MAVROS, PX4) и сам
обращается к серверу — на дроне не нужны входящие порты:

  hello → команды (долгий опрос) → результат команды; состояние и телеметрия каждые 1–5 с;
  после посадки — журнал полёта → штатный импорт (наработка, циклы, датчики, напоминания ТО) — dronelog.py.

Полёт: «Отправить план» (prepare) → агент загружает план в полётный контроллер, проверяет борт и сообщает
«готов» → «Старт» (start) → полёт → посадка → журнал. В полёте: «Вернуть домой» (rtl) или «Посадить» (land).

Защита:
* токен устройства 256 бит показывается один раз, в базе — SHA-256; агент управляет только своим бортом;
* у команд короткий срок жизни: «старт» живёт 15 с, просроченная команда не доставляется;
* старт — только для плана, который агент подтвердил («готов» с тем же хешем плана), при свежей телеметрии,
  пройденных проверках на борту и допуске борта по ТО; пределы высоты и удаления проверяют сервер и агент.
"""
from __future__ import annotations

import hashlib
import json
import math
import secrets
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import (
    DRONE_LOCAL_MAX_ALT_M,
    DRONE_LOCAL_MAX_DIST_M,
    DRONE_LOCAL_MAX_SPEED_MS,
    DRONE_MAX_ALT_M,
    DRONE_MAX_DIST_M,
    DRONE_MAX_HOME_OFFSET_M,
    DRONE_MIN_BATTERY_PCT,
    DRONE_ONLINE_S,
)
from ..errors import DomainError, NotFound
from ..models import Aircraft, DroneCommand, DroneLink, Mission, MissionRun
from ..timeutil import iso, utcnow
from . import autopilot
from .airworthy import preflight
from .auth import RateLimiter

STATE_LABELS = {
    "offline": "Нет связи", "idle": "На связи, план не загружен", "preparing": "Загрузка плана и проверки",
    "ready": "Готов к старту", "starting": "Запуск", "in_flight": "Полёт по маршруту", "returning": "Возврат домой",
    "landing": "Посадка", "landed": "Посадка выполнена", "uploading": "Передача журнала", "error": "Ошибка на борту",
}
AGENT_STATES = tuple(k for k in STATE_LABELS if k != "offline")
RUN_LABELS = {"preparing": "Подготовка", "ready": "Готов к старту", "in_flight": "В полёте", "landed": "Ожидание журнала",
              "completed": "Выполнена", "aborted": "Прервана", "failed": "Не выполнена", "cancelled": "Отменена"}
ACTIVE_RUN = ("preparing", "ready", "in_flight", "landed")
FLYING = ("starting", "in_flight", "returning", "landing")
AGENT_IDLE = ("idle", "error", "ready")      # агент не ведёт полёт и не передаёт журнал
TTL_S = {"prepare": 120, "start": 15, "rtl": 120, "land": 120, "cancel": 120}      # срок жизни команды, с
PRIORITY = {"rtl": 0, "land": 0, "cancel": 1, "start": 2, "prepare": 3}
KIND_LABELS = {"prepare": "Отправить план", "start": "Старт", "rtl": "Вернуть домой", "land": "Посадить", "cancel": "Отменить подготовку"}
TELEMETRY_FRESH_S = 5
READY_MAX_MIN = 30          # «готов» дольше — подготовка снимается (агент сбрасывает свой план раньше, по своей настройке)
LOST_FLIGHT_MIN = 10        # борт молчит в полёте дольше — полёт можно закрыть (иначе он «висел» бы вечно и блокировал борт)
TOKEN_PREFIX = "tbo_"

auth_limiter = RateLimiter(30, 15 * 60)       # неверные токены устройств с одного адреса
_locks: dict = {}
_locks_guard = threading.Lock()


@contextmanager
def guard(s: Session, link: DroneLink):
    """Изменения по одному борту — строго по очереди: агент шлёт состояние и журнал из разных потоков,
    оператор нажимает кнопки. Под блокировкой данные перечитываются из базы и фиксируются (иначе запоздалое
    «посадка» затёрло бы «выполнена»). Сервер работает одним процессом (app/serve.py), блокировки в памяти достаточно."""
    with _locks_guard:
        lock = _locks.setdefault(link.id, threading.Lock())
    with lock:
        s.expire_all()
        yield
        s.commit()


# ------------------------------------------------------------------ токены устройств
def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8", "replace")).hexdigest()


def _new_token() -> tuple[str, str, str]:
    tok = TOKEN_PREFIX + secrets.token_urlsafe(32)
    return tok, token_hash(tok), tok[-6:]


def create_link(s: Session, aircraft_id: int, actor: str) -> tuple[DroneLink, str]:
    a = s.get(Aircraft, aircraft_id)
    if a is None:
        raise NotFound("Борт не найден")
    if s.scalar(select(DroneLink.id).where(DroneLink.aircraft_id == a.id)):
        raise DomainError(f"Борт {a.name} уже подключён — при необходимости выпустите новый токен", 409)
    tok, h, hint = _new_token()
    link = DroneLink(aircraft_id=a.id, token_hash=h, token_hint=hint, enabled=True, created_at=utcnow(), created_by=actor,
                     state="offline", agent={}, checks=[], telemetry={})
    s.add(link)
    s.flush()
    return link, tok


def rotate_token(s: Session, link: DroneLink) -> str:
    tok, h, hint = _new_token()
    link.token_hash, link.token_hint = h, hint
    s.flush()
    return tok


def resolve_device(s: Session, token: str | None) -> DroneLink | None:
    if not token or len(token) > 200 or not token.startswith(TOKEN_PREFIX):
        return None
    return s.scalar(select(DroneLink).where(DroneLink.token_hash == token_hash(token)))


def get_link(s: Session, lid: int) -> DroneLink:
    link = s.get(DroneLink, lid)
    if link is None:
        raise NotFound("Подключение борта не найдено")
    return link


# ------------------------------------------------------------------ представление
def online(link: DroneLink, now: datetime | None = None) -> bool:
    now = now or utcnow()
    return bool(link.last_seen_at and (now - link.last_seen_at).total_seconds() <= DRONE_ONLINE_S)


def run_dict(r: MissionRun) -> dict:
    sm = r.summary or {}
    return {"id": r.id, "mission_id": r.mission_id, "mission_name": sm.get("mission_name"), "aircraft_id": r.aircraft_id,
            "status": r.status, "status_label": RUN_LABELS.get(r.status, r.status), "started_at": iso(r.started_at),
            "ended_at": iso(r.ended_at), "import_id": r.import_id, "plan_hash": sm.get("plan_hash"), "summary": sm}


def cmd_dict(c: DroneCommand) -> dict:
    return {"id": c.id, "kind": c.kind, "kind_label": KIND_LABELS.get(c.kind, c.kind), "status": c.status,
            "created_at": iso(c.created_at), "created_by": c.created_by, "delivered_at": iso(c.delivered_at),
            "finished_at": iso(c.finished_at), "result": c.result, "run_id": c.run_id}


def link_dict(s: Session, link: DroneLink, now: datetime | None = None, detail: bool = False) -> dict:
    now = now or utcnow()
    on = online(link, now)
    st = link.state if on else "offline"
    run = s.get(MissionRun, link.run_id) if link.run_id else None
    age = round((now - link.telemetry_at).total_seconds(), 1) if link.telemetry_at else None
    d = {"id": link.id, "aircraft": {"id": link.aircraft_id, "name": link.aircraft.name, "serial": link.aircraft.serial},
         "enabled": link.enabled, "online": on, "state": st, "state_label": STATE_LABELS.get(st, st),
         "last_state": link.state, "last_state_label": STATE_LABELS.get(link.state, link.state), "detail": link.detail,
         "checks": link.checks or [], "telemetry": link.telemetry or {}, "telemetry_age_s": age,
         "last_seen_at": iso(link.last_seen_at), "last_ip": link.last_ip, "agent": link.agent or {},
         "token_hint": "…" + link.token_hint, "created_at": iso(link.created_at), "created_by": link.created_by,
         "run": run_dict(run) if run else None}
    if detail:
        d["commands"] = [cmd_dict(c) for c in s.scalars(select(DroneCommand).where(DroneCommand.link_id == link.id)
                                                       .order_by(DroneCommand.id.desc()).limit(15))]
        d["runs"] = [run_dict(r) for r in s.scalars(select(MissionRun).where(
            MissionRun.aircraft_id == link.aircraft_id, MissionRun.source == "drone").order_by(MissionRun.id.desc()).limit(10))]
    return d


# ------------------------------------------------------------------ план полёта
def _dist_m(lat0, lon0, lat, lon) -> float:
    return math.dist((0.0, 0.0), autopilot.to_xy(lat0, lon0, lat, lon))


def limits_for(frame: str) -> dict:
    """Пределы реального полёта: по GPS — высота и удаление; локально (в помещении) — малые высота, удаление, скорость."""
    if frame == "local":
        return {"max_alt_m": DRONE_LOCAL_MAX_ALT_M, "max_dist_m": DRONE_LOCAL_MAX_DIST_M, "max_speed_ms": DRONE_LOCAL_MAX_SPEED_MS,
                "min_battery_pct": DRONE_MIN_BATTERY_PCT, "env": ("TBO_DRONE_LOCAL_MAX_ALT_M", "TBO_DRONE_LOCAL_MAX_DIST_M")}
    return {"max_alt_m": DRONE_MAX_ALT_M, "max_dist_m": DRONE_MAX_DIST_M, "max_home_offset_m": DRONE_MAX_HOME_OFFSET_M,
            "min_battery_pct": DRONE_MIN_BATTERY_PCT, "env": ("TBO_DRONE_MAX_ALT_M", "TBO_DRONE_MAX_DIST_M")}


def check_limits(md: dict):
    """Пределы реального полёта (строже, чем у симулятора): высота, удаление от точки взлёта, для локальной — скорость."""
    lim = limits_for(md.get("frame") or "global")
    env_alt, env_dist = lim["env"]
    if float(md["takeoff_alt"]) > lim["max_alt_m"]:
        raise DomainError(f"Высота взлёта {float(md['takeoff_alt']):g} м выше предела {lim['max_alt_m']} м ({env_alt})")
    if lim.get("max_speed_ms") and float(md["speed_ms"]) > lim["max_speed_ms"]:
        raise DomainError(f"Скорость {float(md['speed_ms']):g} м/с выше {lim['max_speed_ms']:g} м/с для локальной навигации "
                          "(Optical Flow ограничивает скорость)")
    for i, w in enumerate(md["waypoints"], 1):
        if float(w["alt"]) > lim["max_alt_m"]:
            raise DomainError(f"Точка {i}: высота {float(w['alt']):g} м выше предела {lim['max_alt_m']} м для реального полёта "
                              f"({env_alt})")
        d = _dist_m(md["home_lat"], md["home_lon"], float(w["lat"]), float(w["lon"]))
        if d > lim["max_dist_m"]:
            raise DomainError(f"Точка {i}: {d:.0f} м от точки взлёта — больше предела {lim['max_dist_m']} м ({env_dist})")


def plan_hash(plan: dict) -> str:
    body = {k: v for k, v in plan.items() if k != "plan_hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def _plan_wp(w: dict) -> dict:
    out = {"lat": float(w["lat"]), "lon": float(w["lon"]), "alt": float(w["alt"]), "hold_s": float(w.get("hold_s") or 0)}
    if w.get("yaw_deg") is not None:
        out["yaw_deg"] = float(w["yaw_deg"]) % 360.0
    return out


def plan_uses_yaw(plan: dict) -> bool:
    return plan.get("yaw_mode") == "route" or any("yaw_deg" in w for w in plan["waypoints"])


def plan_for(m: Mission, a: Aircraft, run_id: int) -> dict:
    """План для борта (tbo-plan/1). Курс — только если задан (yaw_mode=route, yaw_deg точек): план миссии без курса
    тот же, что у прежних версий, и его выполняет агент любой версии."""
    frame = m.frame or "global"
    md = {"name": m.name, "home_lat": m.home_lat, "home_lon": m.home_lon, "takeoff_alt": m.takeoff_alt,
          "speed_ms": m.speed_ms, "return_home": m.return_home, "frame": frame, "yaw_mode": m.yaw_mode or "keep",
          "waypoints": m.waypoints or []}
    autopilot.validate(md)
    check_limits(md)
    lim = {k: v for k, v in limits_for(frame).items() if k != "env"}
    plan = {"format": "tbo-plan/1", "run_id": run_id, "frame": frame, "mission": {"id": m.id, "name": m.name},
            "aircraft": {"serial": a.serial, "name": a.name}, "home": {"lat": float(m.home_lat), "lon": float(m.home_lon)},
            "takeoff_alt": float(m.takeoff_alt), "speed_ms": float(m.speed_ms), "return_home": bool(m.return_home),
            "waypoints": [_plan_wp(w) for w in m.waypoints], "limits": lim}
    if md["yaw_mode"] == "route":
        plan["yaw_mode"] = "route"
    plan["plan_hash"] = plan_hash(plan)
    return plan


def check_agent_features(link: DroneLink, plan: dict):
    """Курс в плане — только агенту, который его выполняет: прежний агент молча летел бы без курса."""
    if plan_uses_yaw(plan) and "yaw" not in ((link.agent or {}).get("features") or []):
        ver = (link.agent or {}).get("version") or "неизвестна"
        raise DomainError(f"Агент на борту (версия {ver}) не поддерживает курс дрона в миссии — обновите агент "
                          "(deploy/field/deploy.sh --drone-only) или уберите курс: «Курс дрона» — «Не менять», "
                          "курс в точках — пусто")


# ------------------------------------------------------------------ команды пользователя
def _usable(link: DroneLink, now: datetime):
    if not link.enabled:
        raise DomainError("Подключение борта отключено администратором")
    if not online(link, now):
        raise DomainError(f"Борт {link.aircraft.name} не на связи — проверьте питание, Wi-Fi и службу tbo-agent на борту")


def _queue(s: Session, link: DroneLink, kind: str, run: MissionRun | None, payload: dict, actor: str, now: datetime) -> DroneCommand:
    c = DroneCommand(link_id=link.id, run_id=run.id if run else None, kind=kind, payload=payload, status="queued",
                     created_at=now, created_by=actor, expires_at=now + timedelta(seconds=TTL_S[kind]))
    s.add(c)
    s.flush()
    return c


def _supersede(s: Session, link: DroneLink, kinds: tuple, why: str, now: datetime):
    for c in s.scalars(select(DroneCommand).where(DroneCommand.link_id == link.id, DroneCommand.status == "queued",
                                                  DroneCommand.kind.in_(kinds))):
        c.status, c.finished_at, c.result = "expired", now, why


def _run_update(run: MissionRun, **kw):
    run.summary = {**(run.summary or {}), **kw}        # JSON-поле: новое значение, чтобы изменение сохранилось


def active_run(s: Session, link: DroneLink) -> MissionRun | None:
    run = s.get(MissionRun, link.run_id) if link.run_id else None
    return run if run is not None and run.status in ACTIVE_RUN else None


def prepare(s: Session, link: DroneLink, mission_id: int, actor: str) -> tuple[MissionRun, DroneCommand]:
    now = utcnow()
    _usable(link, now)
    if link.state not in ("idle", "error"):
        raise DomainError(f"Борт сейчас: «{STATE_LABELS.get(link.state, link.state)}». План отправляется борту без плана")
    if active_run(s, link):
        raise DomainError("У борта уже есть подготовленный или выполняемый полёт")
    m = s.get(Mission, mission_id)
    if m is None:
        raise NotFound("Миссия не найдена")
    a = link.aircraft
    if m.aircraft_id and m.aircraft_id != a.id:
        raise DomainError(f"Миссия назначена другому борту — назначьте ей борт {a.name} и сохраните")
    problems = preflight(s, a)
    if problems:
        raise DomainError(f"Борт {a.name} не допущен к полёту: " + "; ".join(problems))
    run = MissionRun(mission_id=m.id, aircraft_id=a.id, started_at=now, ended_at=now, status="preparing", source="drone",
                     summary={}, track=[])
    s.add(run)
    s.flush()
    plan = plan_for(m, a, run.id)
    check_agent_features(link, plan)
    run.summary = {"mission_name": m.name, "aircraft_name": a.name, "plan_hash": plan["plan_hash"], "prepared_by": actor,
                   "prepared_at": iso(now), "waypoints": len(plan["waypoints"])}
    _supersede(s, link, tuple(TTL_S), "заменена новой подготовкой", now)
    cmd = _queue(s, link, "prepare", run, {"plan": plan}, actor, now)
    link.run_id = run.id
    s.flush()
    return run, cmd


def start(s: Session, link: DroneLink, run_id: int, plan_hash_: str, actor: str) -> DroneCommand:
    now = utcnow()
    _usable(link, now)
    run = s.get(MissionRun, run_id)
    if run is None or run.source != "drone" or run.id != link.run_id:
        raise DomainError("Этот полёт не подготовлен на борту — отправьте план заново")
    if run.status != "ready" or link.state != "ready":
        raise DomainError("Старт возможен, когда борт сообщил «Готов к старту»")
    if (run.summary or {}).get("plan_hash") != plan_hash_:
        raise DomainError("План отличается от подготовленного на борту — отправьте план заново")
    if not link.telemetry_at or (now - link.telemetry_at).total_seconds() > TELEMETRY_FRESH_S:
        raise DomainError("Телеметрия борта устарела — проверьте связь с бортом")
    failed = [c.get("name", "?") for c in link.checks or [] if not c.get("ok")]
    if failed:
        raise DomainError("На борту не пройдены проверки: " + ", ".join(failed))
    problems = preflight(s, link.aircraft)
    if problems:
        raise DomainError(f"Борт {link.aircraft.name} не допущен к полёту: " + "; ".join(problems))
    m = s.get(Mission, run.mission_id)
    if m is None or plan_for(m, link.aircraft, run.id)["plan_hash"] != plan_hash_:
        raise DomainError("Миссию изменили после отправки на борт — отправьте план заново")
    _supersede(s, link, ("start",), "заменена новым стартом", now)
    cmd = _queue(s, link, "start", run, {"run_id": run.id, "plan_hash": plan_hash_}, actor, now)
    _run_update(run, start_requested_by=actor, start_requested_at=iso(now), last_error=None)
    return cmd


def abort(s: Session, link: DroneLink, kind: str, actor: str) -> DroneCommand:
    """Вернуть домой (rtl) или посадить на месте (land). Доставляется и после кратковременной потери связи."""
    now = utcnow()
    if not link.enabled:
        raise DomainError("Подключение борта отключено администратором")
    run = active_run(s, link)
    if link.state not in FLYING and not (run and run.status == "in_flight"):
        raise DomainError("Борт не в полёте")
    _supersede(s, link, ("start", "prepare", "cancel"), "отменена командой прерывания", now)
    cmd = _queue(s, link, kind, run, {"run_id": run.id if run else None}, actor, now)
    if run:
        _run_update(run, abort={"kind": kind, "by": actor, "at": iso(now)})
    return cmd


def cancel(s: Session, link: DroneLink, actor: str) -> DroneCommand | None:
    """Отмена подготовки. Полёт, связь с которым потеряна дольше LOST_FLIGHT_MIN, закрывается как «не выполнен»:
    без этого он навсегда оставался бы текущим, и борт нельзя было бы ни подготовить, ни отключить. Если агент
    позже передаст журнал (он хранится на борту), полёт будет учтён — импорт не зависит от этого статуса."""
    now = utcnow()
    run = active_run(s, link)
    if run is not None and run.status in ("in_flight", "landed"):
        silent = (now - link.last_seen_at).total_seconds() / 60 if link.last_seen_at else None
        idle_board = online(link, now) and link.state in AGENT_IDLE    # агент на связи, но полёта не ведёт
        if not idle_board and (online(link, now) or (silent is not None and silent < LOST_FLIGHT_MIN)):
            raise DomainError(f"Борт в полёте — «Вернуть домой» или «Посадить». Закрыть полёт без журнала можно, "
                              f"если связи с бортом нет дольше {LOST_FLIGHT_MIN} мин")
        _supersede(s, link, tuple(TTL_S), "полёт закрыт: " + ("борт не ведёт полёт" if idle_board else "нет связи с бортом"), now)
        run.status, run.ended_at = "failed", now
        why = (f"борт на связи, но полёт не ведёт (состояние агента: {link.state})" if idle_board else
               f"связь с бортом потеряна в полёте (последний обмен {iso(link.last_seen_at) or '—'})")
        _run_update(run, error=why + ", журнал не получен; полёт закрыт оператором — если агент передаст журнал позже, "
                                     "он будет учтён", closed_by=actor)
        link.run_id = None
        return None
    if run is None or run.status not in ("preparing", "ready"):
        raise DomainError("Отменить можно только подготовку. В полёте — «Вернуть домой» или «Посадить»")
    _supersede(s, link, ("start", "prepare"), "подготовка отменена", now)
    cmd = _queue(s, link, "cancel", run, {"run_id": run.id}, actor, now)
    run.status, run.ended_at = "cancelled", now
    _run_update(run, cancelled_by=actor)
    link.run_id = None
    return cmd


# ------------------------------------------------------------------ обмен с агентом
def _on_expired(s: Session, c: DroneCommand, now: datetime):
    run = s.get(MissionRun, c.run_id) if c.run_id else None
    if c.kind == "prepare" and run is not None and run.status == "preparing":
        run.status, run.ended_at = "failed", now
        _run_update(run, error="борт не получил план: нет связи или агент не ответил")
        link = s.get(DroneLink, c.link_id)
        if link and link.run_id == run.id:
            link.run_id = None


def next_command(s: Session, link: DroneLink, now: datetime) -> dict | None:
    cmds = list(s.scalars(select(DroneCommand).where(DroneCommand.link_id == link.id, DroneCommand.status == "queued")))
    live = []
    for c in cmds:
        if c.expires_at <= now:
            c.status, c.finished_at, c.result = "expired", now, "срок команды истёк до доставки на борт"
            _on_expired(s, c, now)
        else:
            live.append(c)
    out = None
    if live:
        c = min(live, key=lambda x: (PRIORITY.get(x.kind, 9), x.id))
        c.status, c.delivered_at = "delivered", now
        out = {"id": c.id, "kind": c.kind, "payload": c.payload, "run_id": c.run_id,
               "ttl_s": max(0.0, round((c.expires_at - now).total_seconds(), 1))}
    return out


def command_result(s: Session, link: DroneLink, cid: int, ok: bool, message: str, now: datetime):
    c = s.get(DroneCommand, cid)
    if c is None or c.link_id != link.id:
        raise NotFound("Команда не найдена")
    if c.status not in ("queued", "delivered"):
        return                                   # повтор подтверждения
    c.status, c.finished_at, c.result = ("done" if ok else "failed"), now, (message or "")[:500]
    run = s.get(MissionRun, c.run_id) if c.run_id else None
    if run is None or run.source != "drone":
        return
    if c.kind == "prepare" and not ok and run.status == "preparing":
        run.status, run.ended_at = "failed", now
        _run_update(run, error=c.result)
        if link.run_id == run.id:
            link.run_id = None
    elif c.kind == "start":
        if ok:
            if run.status == "ready":                 # состояние «в полёте» могло прийти раньше подтверждения
                run.status, run.started_at = "in_flight", now
            _run_update(run, started_by=c.created_by)
        else:
            if run.status == "in_flight" and link.state == "ready":
                _start_failed(run, now)
            _run_update(run, last_error=c.result)


def _start_failed(run: MissionRun, now: datetime):
    """Старт не выполнен (например, моторы не включились): агент сообщал «Запуск», и полёт уже считался начатым,
    но борт вернулся в «Готов к старту». Полёт снова «готов» — иначе он навсегда оставался бы «в полёте»:
    «Вернуть домой» отклонял бы борт («не в полёте»), а «Отменить» — сервер («борт в полёте»)."""
    run.status, run.started_at = "ready", now
    _run_update(run, start_failed_at=iso(now))


def agent_state(s: Session, link: DroneLink, data: dict, ip: str, now: datetime) -> dict:
    """Состояние и телеметрия от агента; сверка выполнения миссии. Ответ — какой полёт сервер считает текущим."""
    link.last_seen_at, link.last_ip = now, ip
    link.state, link.detail = data["state"], data.get("detail")
    if data.get("checks") is not None:
        link.checks = data["checks"]
    if data.get("telemetry") is not None:
        link.telemetry, link.telemetry_at = data["telemetry"], now
    run = s.get(MissionRun, data["run_id"]) if data.get("run_id") else None
    if run is not None and (run.aircraft_id != link.aircraft_id or run.source != "drone"):
        run = None
    if run is not None:
        if link.state == "ready" and run.status == "preparing" and data.get("plan_hash") == (run.summary or {}).get("plan_hash"):
            run.status = "ready"
            _run_update(run, ready_at=iso(now))
        elif link.state in FLYING and run.status == "ready":            # подтверждение старта потерялось в сети
            run.status, run.started_at = "in_flight", now
        elif link.state in ("landed", "uploading") and run.status == "in_flight":
            run.status = "landed"
        elif link.state == "ready" and run.status == "in_flight" and data.get("plan_hash") == (run.summary or {}).get("plan_hash"):
            _start_failed(run, now)                                      # «Запуск» ушёл, но моторы не включились
    elif link.state == "idle" and link.run_id:
        cur = s.get(MissionRun, link.run_id)
        if cur is not None and cur.status == "ready":                   # агент сбросил план (перезапуск, ожидание старта)
            cur.status, cur.ended_at = "cancelled", now
            _run_update(cur, error="подготовка сброшена на борту")
            link.run_id = None
    return {"active_run_id": link.run_id, "server_time": iso(now)}


def sweep(s: Session, now: datetime | None = None):
    """Просроченные команды и зависшие подготовки (вызывается планировщиком и при просмотре)."""
    now = now or utcnow()
    lids = set(s.scalars(select(DroneCommand.link_id).where(DroneCommand.status.in_(("queued", "delivered")))))
    lids |= set(s.scalars(select(DroneLink.id).where(DroneLink.run_id.is_not(None))))
    for lid in sorted(lids):
        link = s.get(DroneLink, lid)
        if link is None:
            continue
        with guard(s, link):
            for c in s.scalars(select(DroneCommand).where(DroneCommand.link_id == lid,
                                                          DroneCommand.status.in_(("queued", "delivered")))):
                grace = 0 if c.status == "queued" else 60
                if c.expires_at + timedelta(seconds=grace) <= now:
                    c.status, c.finished_at = "expired", now
                    c.result = "срок команды истёк до доставки" if c.delivered_at is None else "борт не подтвердил выполнение"
                    _on_expired(s, c, now)
            run = s.get(MissionRun, link.run_id) if link.run_id else None
            if run is not None and run.status == "ready" and run.started_at < now - timedelta(minutes=READY_MAX_MIN):
                run.status, run.ended_at = "cancelled", now
                _run_update(run, error=f"старт не выполнен за {READY_MAX_MIN} мин — подготовка снята")
                link.run_id = None
