"""Журнал реального полёта от агента → штатный импорт и итог выполнения миссии.

Сервер сам задаёт борт и номер полёта (агент не может начислить наработку чужому борту), журнал проходит
обычный импорт: наработка и циклы борту и установленным узлам, анализ акселерометра (падения, столкновения),
напоминания ТО. Повторная отправка того же журнала (обрыв связи после приёма) не начисляет наработку дважды.

Полёт вне tbo-tracker (взлёт из QGroundControl, «Start Mission» в QGC, с пульта) — ingest_external: миссии
и выполнения нет, агент присваивает полёту собственный номер flight_id; полёт учитывается за бортом подключения
с ключом «<серийный номер>-<flight_id>», поэтому повторная отправка не создаёт второй полёт.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import NotFound
from ..models import DroneLink, Flight, LogImport, MissionRun
from ..timeutil import iso, parse_dt
from . import logs
from .auth import audit
from .drones import run_dict

log = logging.getLogger("tbo.drones")
SUMMARY_KEYS = ("duration_s", "distance_m", "max_alt_m", "battery_start_pct", "battery_end_pct", "min_voltage",
                "wp_reached", "wp_total", "executor", "status_texts", "mode_changes", "reason", "agent_version")


def _final(run: MissionRun, link: DroneLink):
    if link.run_id == run.id:
        link.run_id = None


def ingest(s: Session, link: DroneLink, run_id: int, data: dict, now: datetime) -> dict:
    run = s.get(MissionRun, run_id)
    if run is None or run.aircraft_id != link.aircraft_id or run.source != "drone":
        raise NotFound("Полёт не найден")
    if run.import_id is not None or (run.status in ("completed", "aborted") and run.summary.get("logged")):
        return {"run": run_dict(run), "already": True, "import": None}
    a = link.aircraft
    sm = run.summary or {}
    extra = {k: v for k, v in (data.get("summary") or {}).items() if k in SUMMARY_KEYS}
    flight = data.get("flight")
    status = data["status"]
    if not flight:                                  # не взлетел (отказ при запуске, отмена до взлёта)
        run.status, run.ended_at = ("aborted" if sm.get("started_by") or status == "aborted" else "failed"), now
        run.track = data.get("track") or []
        run.summary = {**sm, **extra, "logged": True, "error": data.get("reason") or sm.get("error"), "no_flight": True}
        _final(run, link)
        s.commit()
        return {"run": run_dict(run), "already": False, "import": None}

    start, end = parse_dt(flight["start"]), parse_dt(flight["end"])
    entry = {"flight_id": f"{a.serial}-R{run.id}", "start": iso(start), "end": iso(end), "cycles": 1,
             "mission": f"Миссия «{sm.get('mission_name', run.mission_id)}» (борт, автономный полёт)",
             "pilot": f"автопилот · старт: {sm.get('started_by') or sm.get('start_requested_by') or '—'}"}
    if flight.get("imu"):
        entry["imu"] = flight["imu"]
    if flight.get("events"):
        entry["events"] = flight["events"]
    doc = {"format": "tbo-flightlog/1.0", "aircraft_serial": a.serial,
           "source": f"tbo-agent {extra.get('agent_version', '')} · {extra.get('executor', '')}".strip(" ·"),
           "flights": [entry]}
    res = logs.import_log(s, json.dumps(doc, ensure_ascii=False).encode(),
                          f"Борт {a.name} · миссия «{sm.get('mission_name', '')}» · полёт R{run.id}", f"агент · {a.name}")
    ok = res["flights_new"] == 1 or (res["status"] == "duplicate") or res["flights_duplicate"] == 1
    if res.get("id"):
        imp = s.get(LogImport, res["id"])
        if imp is not None:
            imp.format = "drone"
    run.track = data.get("track") or []
    run.started_at, run.ended_at = start, end
    if ok:
        run.import_id = res.get("id")
        run.status = "aborted" if status == "aborted" or sm.get("abort") else "completed"
    else:
        run.status = "failed"
        log.warning("drone log rejected run=%s: %s", run.id, "; ".join(res.get("messages") or []))
    run.summary = {**sm, **extra, "logged": True, "hours_added": res.get("hours_added", 0),
                   "sensor_events": res.get("summary", {}).get("sensor_events", []),
                   "alerts": res.get("summary", {}).get("alerts", []),
                   "components": len(res.get("summary", {}).get("components", [])),
                   "import_messages": (res.get("messages") or [])[:10]}
    _final(run, link)
    s.commit()
    audit("drone_flight_logged", None, None, target=a.name,
          details=f"полёт R{run.id}, {RUN_STATUS_RU.get(run.status, run.status)}, импорт №{res.get('id')}")
    return {"run": run_dict(run), "already": False, "import": res}


RUN_STATUS_RU = {"completed": "выполнена", "aborted": "прервана", "failed": "не учтена"}



EXTERNAL_LABEL = "полёт вне tbo-tracker (QGroundControl / пульт)"


def ingest_external(s: Session, link: DroneLink, flight_id: str, data: dict, now: datetime) -> dict:
    """Журнал полёта, начатого не из tbo-tracker: штатный импорт за бортом подключения, без выполнения миссии."""
    a = link.aircraft
    ext = f"{a.serial}-{flight_id}"
    extra = {k: v for k, v in (data.get("summary") or {}).items() if k in SUMMARY_KEYS}
    flight = data.get("flight")
    base = {"flight_id": flight_id, "external_id": ext, "status": data["status"], "reason": data.get("reason") or EXTERNAL_LABEL}
    if not flight:                                  # моторы включили и выключили на земле — полёта нет, учитывать нечего
        return {**base, "already": False, "no_flight": True, "import": None}
    known = s.scalar(select(Flight).where(Flight.aircraft_id == a.id, Flight.external_id == ext).limit(1))
    if known is not None:
        return {**base, "already": True, "import": None, "import_id": known.import_id}
    start, end = parse_dt(flight["start"]), parse_dt(flight["end"])
    reason = (data.get("reason") or EXTERNAL_LABEL)[:200]
    entry = {"flight_id": ext, "start": iso(start), "end": iso(end), "cycles": 1,
             "mission": ("Полёт вне tbo-tracker (QGroundControl / пульт)" + ("" if reason == EXTERNAL_LABEL else f" · {reason}"))[:300],
             "pilot": "пилот · QGroundControl / пульт"}
    if flight.get("imu"):
        entry["imu"] = flight["imu"]
    if flight.get("events"):
        entry["events"] = flight["events"]
    doc = {"format": "tbo-flightlog/1.0", "aircraft_serial": a.serial,
           "source": f"tbo-agent {extra.get('agent_version', '')} · {extra.get('executor', '')}".strip(" ·"),
           "flights": [entry]}
    res = logs.import_log(s, json.dumps(doc, ensure_ascii=False).encode(),
                          f"Борт {a.name} · полёт вне tbo-tracker {flight_id}", f"агент · {a.name}")
    ok = res["flights_new"] == 1 or res["status"] == "duplicate" or res["flights_duplicate"] == 1
    if res.get("id"):
        imp = s.get(LogImport, res["id"])
        if imp is not None:
            imp.format = "drone"
            # итог полёта от агента (трек не хранится: у полёта без миссии нет выполнения, к которому он относится)
            imp.summary = {**(imp.summary or {}), "agent": {**base, **extra, "track_points": len(data.get("track") or [])}}
    if not ok:
        log.warning("drone external log rejected %s: %s", ext, "; ".join(res.get("messages") or []))
    s.commit()
    audit("drone_flight_logged", None, None, target=a.name,
          details=f"{EXTERNAL_LABEL} {flight_id}, {'учтён' if ok else 'не учтён'}, импорт №{res.get('id')}")
    return {**base, "already": False, "accepted": ok, "import": res}
