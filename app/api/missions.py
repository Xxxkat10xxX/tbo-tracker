"""API полётных заданий: маршрут, автономный полёт (симулятор), обмен с QGroundControl/ArduPilot/PX4."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..errors import DomainError, NotFound
from ..models import Aircraft, LogImport, Mission, MissionRun
from ..schemas import MissionEstimateIn, MissionIn, MissionRunIn, RoutePatternIn
from ..services import autopilot, flightreport, logs, routegen
from ..services.airworthy import preflight
from ..timeutil import iso, utcnow

router = APIRouter(prefix="/api")


def _mdict(m: Mission) -> dict:
    return {"id": m.id, "name": m.name, "aircraft_id": m.aircraft_id, "aircraft_name": m.aircraft.name if m.aircraft else None,
            "home_lat": m.home_lat, "home_lon": m.home_lon, "takeoff_alt": m.takeoff_alt, "speed_ms": m.speed_ms,
            "return_home": m.return_home, "frame": m.frame or "global", "yaw_mode": m.yaw_mode or "keep",
            "waypoints": m.waypoints or [], "notes": m.notes, "updated_at": iso(m.updated_at)}


def _rdict(r: MissionRun, with_track=False) -> dict:
    from ..services.drones import RUN_LABELS
    d = {"id": r.id, "mission_id": r.mission_id, "aircraft_id": r.aircraft_id, "import_id": r.import_id,
         "started_at": iso(r.started_at), "ended_at": iso(r.ended_at), "status": r.status,
         "status_label": RUN_LABELS.get(r.status, r.status), "source": r.source or "sim", "summary": r.summary}
    if with_track:
        d["track"] = r.track
    return d


def _get(s, mid) -> Mission:
    m = s.get(Mission, mid)
    if m is None:
        raise NotFound("Миссия не найдена")
    return m


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


@router.post("/missions/estimate")
def estimate_mission(data: MissionEstimateIn):
    """Протяжённость и оценка времени маршрута — для редактора миссии (без сохранения)."""
    d = data.model_dump()
    return autopilot.plan_stats(d) if d["waypoints"] else {"distance_m": 0, "duration_s": 0, "waypoints": 0, "max_alt_m": 0}


@router.post("/missions/pattern")
def route_pattern(data: RoutePatternIn):
    """Точки маршрута по шаблону (съёмка площади с перекрытием, двойная сетка, периметр, облёт по кругу) — для
    редактора миссии, без сохранения: точки применяет оператор."""
    return routegen.generate(data.model_dump())


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
    from ..services.drones import ACTIVE_RUN
    if s.scalar(select(MissionRun.id).where(MissionRun.mission_id == mid, MissionRun.status.in_(ACTIVE_RUN)).limit(1)):
        raise DomainError("Миссия готовится к полёту или выполняется на борту — удалить её можно после посадки")
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
    if data.fault_at_wp and data.fault_at_wp > len(m.waypoints or []):
        raise DomainError(f"В миссии {len(m.waypoints or [])} точек — отказ у точки {data.fault_at_wp} задать нельзя")
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
    if (m.frame or "global") == "local":
        raise DomainError("Локальная миссия (метры от точки взлёта, без GPS) не переносится в QGroundControl: "
                          "в файлах .plan и .waypoints только координаты GPS")
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


@router.get("/runs/{rid}/report")
def run_report(rid: int, video_s: float | None = None, fmt: str = "json", s: Session = Depends(get_session)):
    """Отчёт по полёту R N для протокола испытаний: точки маршрута (Х1), промах (Х2), начисление борту и узлам
    до/после и сравнение с длительностью по видео (Х3), события датчиков (Х5). fmt=md — файл Markdown."""
    if video_s is not None and not 0 < video_s < 86400:
        raise DomainError("Длительность по видео — в секундах, от 0 до 86 400")
    rep = flightreport.report(s, rid, video_s)
    if fmt != "md":
        return rep
    return Response(flightreport.markdown(rep).encode(), media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="flight-R{rid}-report.md"'})


@router.get("/runs/{rid}/flightlog")
def run_flightlog(rid: int, s: Session = Depends(get_session)):
    """Журнал полёта R N (tbo-flightlog/1.0) для повторной загрузки: наработка второй раз не начисляется."""
    import json
    doc, name = flightreport.flightlog(s, rid)
    return Response(json.dumps(doc, ensure_ascii=False, indent=1).encode(), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/missions/import", status_code=201)
async def import_mission(file: UploadFile = File(...), s: Session = Depends(get_session)):
    data = autopilot.parse_mission_file(await file.read(), file.filename or "mission")
    try:
        payload = MissionIn(**data)
    except ValidationError as e:
        err = e.errors()[0]
        where = ".".join(str(x) for x in err["loc"])
        raise DomainError(f"Миссия из файла не прошла проверку: {where} — {err['msg']}") from e
    m = Mission()
    _apply(m, payload, s)
    s.add(m)
    s.commit()
    return {**_mdict(m), "message": f"Миссия импортирована: {len(m.waypoints)} точек"}
