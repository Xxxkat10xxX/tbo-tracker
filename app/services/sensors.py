"""Мониторинг состояния БПЛА по бортовым датчикам: падения и столкновения.

Источник — модуль ускорения |a| (в g) с акселерометра ИНС/полётного контроллера.
Прозрачные пороги (первый этап, без моделей машинного обучения):

* падение   — участок невесомости |a| < FREE_FALL_G длительностью ≥ FREE_FALL_MIN_S,
              за которым в течение IMPACT_WINDOW_S следует удар |a| ≥ FALL_IMPACT_G;
* столкновение — удар |a| ≥ COLLISION_G без предшествующей невесомости.

Соседние срабатывания ближе REFRACTORY_S объединяются в одно событие.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ..models import Installation, SensorEvent

FREE_FALL_G = 0.35
FREE_FALL_MIN_S = 0.2
FALL_IMPACT_G = 3.0
COLLISION_G = 4.0
IMPACT_WINDOW_S = 1.5
REFRACTORY_S = 1.0
KIND_LABELS = {"fall": "Падение", "collision": "Столкновение"}


def magnitudes(samples) -> list[float]:
    """Принимает [[ax, ay, az], …] или [|a|, …] в g."""
    out = []
    for s in samples:
        if isinstance(s, (list, tuple)):
            out.append(math.sqrt(sum(float(v) ** 2 for v in s[-3:])))
        else:
            out.append(abs(float(s)))
    return out


def detect(samples, rate_hz: float) -> list[dict]:
    """Возвращает [{"t": секунды от начала записи, "kind": "fall"|"collision", "peak_g": …}]."""
    if not samples or rate_hz <= 0:
        return []
    a = magnitudes(samples)
    dt = 1.0 / rate_hz
    ff_need = max(1, round(FREE_FALL_MIN_S * rate_hz))
    events, i, n = [], 0, len(a)
    last_ff_end = None
    run = 0
    while i < n:
        if a[i] < FREE_FALL_G:
            run += 1
            if run >= ff_need:
                last_ff_end = i
        else:
            run = 0
        impact_thr = FALL_IMPACT_G if last_ff_end is not None and (i - last_ff_end) * dt <= IMPACT_WINDOW_S else COLLISION_G
        if a[i] >= impact_thr:
            j = i
            while j + 1 < n and a[j + 1] >= impact_thr:   # пик удара
                j += 1
            peak = max(a[i:j + 1])
            kind = "fall" if impact_thr == FALL_IMPACT_G else "collision"
            t = i * dt
            if events and t - events[-1]["t"] < REFRACTORY_S:
                if peak > events[-1]["peak_g"]:
                    events[-1]["peak_g"] = round(peak, 2)
                if kind == "fall":
                    events[-1]["kind"] = "fall"
            else:
                events.append({"t": round(t, 3), "kind": kind, "peak_g": round(peak, 2)})
            last_ff_end = None
            i = j + 1
            continue
        i += 1
    return events


def count_events(s: Session, kind_entity: str, entity_id: int, since: datetime | None = None,
                 before: datetime | None = None) -> int:
    """Число событий борта или узла (по истории установок: узел «переживает» удар только на том борту, где стоял)."""
    if kind_entity == "aircraft":
        q = select(func.count(SensorEvent.id)).where(SensorEvent.aircraft_id == entity_id)
    else:
        q = select(func.count(SensorEvent.id)).join(Installation, and_(
            Installation.aircraft_id == SensorEvent.aircraft_id,
            Installation.installed_at <= SensorEvent.occurred_at,
            or_(Installation.removed_at.is_(None), SensorEvent.occurred_at < Installation.removed_at),
        )).where(Installation.component_id == entity_id)
    if since is not None:
        q = q.where(SensorEvent.occurred_at >= since)
    if before is not None:
        q = q.where(SensorEvent.occurred_at < before)
    return s.scalar(q) or 0


def counts_by_kind(s: Session, aircraft_id: int) -> dict:
    rows = s.execute(select(SensorEvent.kind, func.count(SensorEvent.id)).where(SensorEvent.aircraft_id == aircraft_id)
                     .group_by(SensorEvent.kind))
    d = {"fall": 0, "collision": 0}
    d.update({k: n for k, n in rows})
    return d


def event_dict(e: SensorEvent) -> dict:
    from ..timeutil import iso
    return {"id": e.id, "flight_id": e.flight_id, "aircraft_id": e.aircraft_id, "occurred_at": iso(e.occurred_at),
            "kind": e.kind, "kind_label": KIND_LABELS.get(e.kind, e.kind), "peak_g": e.peak_g, "source": e.source,
            "details": e.details}


def events_for_flight(flight_start: datetime, raw: dict | None) -> list[dict]:
    """Из записи полёта журнала: imu-выборка и/или готовые события бортового ПО."""
    if not raw:
        return []
    out = []
    imu = raw.get("imu")
    if isinstance(imu, dict) and imu.get("accel_g"):
        rate = float(imu.get("rate_hz") or 50)
        t0 = float(imu.get("t0_s") or 0)          # выборка может покрывать окно внутри полёта
        for ev in detect(imu["accel_g"], rate):
            out.append({"at": flight_start + timedelta(seconds=t0 + ev["t"]), "kind": ev["kind"], "peak_g": ev["peak_g"],
                        "source": "imu", "details": f"анализ акселерометра, {rate:g} Гц"})
    for ev in raw.get("events") or []:
        kind = {"fall": "fall", "падение": "fall", "collision": "collision", "столкновение": "collision"}.get(str(ev.get("type", "")).lower())
        if not kind:
            continue
        off = float(ev.get("t_s") or 0)
        out.append({"at": flight_start + timedelta(seconds=off), "kind": kind, "peak_g": ev.get("peak_g"),
                    "source": "log", "details": "событие бортового журнала"})
    for key, kind in (("collisions", "collision"), ("falls", "fall")):   # счётчики из CSV/Excel
        try:
            n = int(float(raw.get(key) or 0))
        except (TypeError, ValueError):
            n = 0
        for _ in range(max(0, min(n, 50))):
            out.append({"at": flight_start, "kind": kind, "peak_g": None, "source": "log", "details": "счётчик в журнале"})
    return out
