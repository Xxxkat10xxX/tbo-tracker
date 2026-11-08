"""API полётных заданий: маршрут, автономный полёт (симулятор), обмен с QGroundControl/ArduPilot/PX4."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..errors import DomainError, NotFound
from ..models import Aircraft, LogImport, Mission, MissionRun
from ..schemas import MissionIn, MissionRunIn
from ..services import autopilot, logs
from ..services.maintenance import due_items
from ..services.usage import current_installations
from ..timeutil import iso, utcnow

router = APIRouter(prefix="/api")


def _mdict(m: Mission) -> dict:
    return {"id": m.id, "name": m.name, "aircraft_id": m.aircraft_id, "aircraft_name": m.aircraft.name if m.aircraft else None,
            "home_lat": m.home_lat, "home_lon": m.home_lon, "takeoff_alt": m.takeoff_alt, "speed_ms": m.speed_ms,
            "return_home": m.return_home, "waypoints": m.waypoints or [], "notes": m.notes, "updated_at": iso(m.updated_at)}


def _rdict(r: MissionRun, with_track=False) -> dict:
    d = {"id": r.id, "mission_id": r.mission_id, "aircraft_id": r.aircraft_id, "import_id": r.import_id,
         "started_at": iso(r.started_at), "ended_at": iso(r.ended_at), "status": r.status, "summary": r.summary}
    if with_track:
        d["track"] = r.track
    return d


def _get(s, mid) -> Mission:
    m = s.get(Mission, mid)
    if m is None:
        raise NotFound("Миссия не найдена")
    return m


def preflight(s: Session, a: Aircraft) -> list[str]:
    """Предполётная проверка допуска: статус, комплектность обязательных позиций, просроченные работы."""
    problems = []
    if a.status != "active":
        problems.append(f"борт в статусе «{a.status}», а не «В эксплуатации»")
    filled = {i.position for i in current_installations(s).values() if i.aircraft_id == a.id}
    empty = [sl["label"] for sl in a.type.slots or [] if sl.get("required", True) and sl["position"] not in filled]
    if empty:
        problems.append("не установлены узлы: " + ", ".join(empty))
    for it in due_items(s, aircraft_id=a.id, statuses={"overdue"}):
        who = it["entity"]["name"] if it["entity"]["kind"] == "aircraft" else it["entity"]["serial"]
        problems.append(f"просрочено: {it['rule']['name']} ({who})")
    return problems


@router.get("/missions")
def list_missions(s: Session = Depends(get_session)):
    out = []
    for m in s.scalars(select(Mission).order_by(Mission.id)):
        d = _mdict(m)
        d["stats"] = autopilot.plan_stats(d) if m.waypoints else None
        last = s.scalar(select(MissionRun).where(MissionRun.mission_id == m.id).order_by(MissionRun.id.desc()).limit(1))
        d["last_run"] = _rdict(last) if last else None
        out.append(d)
    return out


@router.get("/missions/{mid}")
def get_mission(mid: int, s: Session = Depends(get_session)):
    m = _get(s, mid)
    d = _mdict(m)
    d["stats"] = autopilot.plan_stats(d) if m.waypoints else None
    d["runs"] = [_rdict(r) for r in s.scalars(select(MissionRun).where(MissionRun.mission_id == mid).order_by(MissionRun.id.desc()))]
    return d


def _apply(m: Mission, data: MissionIn, s: Session):
    if data.aircraft_id is not None and s.get(Aircraft, data.aircraft_id) is None:
        raise NotFound("Борт не найден")
    payload = data.model_dump()
    payload["waypoints"] = [w for w in payload["waypoints"]]
    if payload["waypoints"]:
        autopilot.validate(payload)
    for k, v in payload.items():
        setattr(m, k, v)
    m.updated_at = utcnow()


@router.post("/missions", status_code=201)
def create_mission(data: MissionIn, s: Session = Depends(get_session)):
    m = Mission()
    _apply(m, data, s)
    s.add(m)
    s.commit()
    return _mdict(m)


@router.put("/missions/{mid}")
def update_mission(mid: int, data: MissionIn, s: Session = Depends(get_session)):
    m = _get(s, mid)
    _apply(m, data, s)
    s.commit()
    return _mdict(m)


@router.delete("/missions/{mid}")
def delete_mission(mid: int, s: Session = Depends(get_session)):
    m = _get(s, mid)
    for r in s.scalars(select(MissionRun).where(MissionRun.mission_id == mid)):
        s.delete(r)
    s.delete(m)
    s.commit()
    return {"message": "Миссия удалена (учтённые полёты остаются в журнале)"}


@router.post("/missions/{mid}/run")
def run_mission(mid: int, data: MissionRunIn, s: Session = Depends(get_session)):
    """Автономный полёт по маршруту в симуляторе → журнал полёта → штатный импорт (наработка, датчики, напоминания)."""
    m = _get(s, mid)
    aid = data.aircraft_id or m.aircraft_id
    if not aid:
        raise DomainError("Выберите борт для полёта")
    a = s.get(Aircraft, aid)
    if a is None:
        raise NotFound("Борт не найден")
    problems = preflight(s, a)
    if problems:
        raise DomainError(f"Предполётная проверка не пройдена — борт {a.name} не допущен к полёту: " + "; ".join(problems))
    md = _mdict(m)
    sim = autopilot.simulate(md, data.wind_ms, data.wind_from_deg, data.fault_at_wp)
    end = utcnow()
    run = MissionRun(mission_id=m.id, aircraft_id=a.id, started_at=end, ended_at=end, status=sim["status"], summary={}, track=[])
    s.add(run)
    s.flush()
    content = autopilot.flight_log(md, sim, a.serial, end, f"M{m.id}R{run.id}")
    res = logs.import_log(s, content, f"Миссия «{m.name}» · {a.name}", "автопилот")
    if res["flights_new"] != 1:
        s.rollback()
        raise DomainError("Полёт не записан: " + "; ".join(res["messages"]))
    imp = s.get(LogImport, res["id"])
    imp.format = "mission"
    from datetime import timedelta
    run.started_at = end - timedelta(seconds=sim["duration_s"])
    run.import_id = imp.id
    run.track = sim["track"]
    run.summary = {"duration_s": sim["duration_s"], "distance_m": sim["distance_m"], "max_alt_m": sim["max_alt_m"],
                   "max_xte_m": sim["max_xte_m"], "wind": sim["wind"], "events": sim["events"],
                   "hours_added": res["hours_added"], "aircraft_name": a.name,
                   "sensor_events": res["summary"].get("sensor_events", []), "alerts": res["summary"].get("alerts", []),
                   "components": len(res["summary"].get("components", []))}
    s.commit()
    return {**_rdict(run, with_track=True), "import": res}


@router.get("/missions/{mid}/export")
def export_mission(mid: int, fmt: str = "plan", firmware: str = "ardupilot", s: Session = Depends(get_session)):
    m = _get(s, mid)
    if not m.waypoints:
        raise DomainError("В миссии нет точек")
    import json
    md = _mdict(m)
    safe = "".join(ch if ch.isalnum() else "_" for ch in m.name)[:40] or "mission"
    if fmt == "waypoints":
        body, ctype, name = autopilot.to_waypoints(md).encode(), "text/plain", f"{safe}.waypoints"
    else:
        body = json.dumps(autopilot.to_plan(md, firmware), ensure_ascii=False, indent=2).encode()
        ctype, name = "application/json", f"{safe}.plan"
    from urllib.parse import quote
    return Response(body, media_type=ctype, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


@router.post("/missions/import", status_code=201)
async def import_mission(file: UploadFile = File(...), s: Session = Depends(get_session)):
    data = autopilot.parse_mission_file(await file.read(), file.filename or "mission")
    payload = MissionIn(**data)
    m = Mission()
    _apply(m, payload, s)
    s.add(m)
    s.commit()
    return {**_mdict(m), "message": f"Миссия импортирована: {len(m.waypoints)} точек"}
