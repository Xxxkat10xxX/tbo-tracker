"""Отчёт по полёту на реальном борту — для протокола испытаний (модуль B, характеристики Х1–Х3 и Х5).

По выполнению миссии R N (журнал от бортового агента) считает:

* Х1 — доля достигнутых точек маршрута: число точек, которые агент засчитал (wp_reached), из числа точек плана;
* Х2 — наибольший промах в точке маршрута: расстояние (3D) от каждой точки до ближайшей точки трека 1 Гц.
  Трек и точки — в одной системе: смещения от точки взлёта миссии (локальная миссия) или GPS;
* Х3 — начисление: наработка и циклы борта и каждого установленного узла до и после полёта; при заданной
  длительности по видео (от отрыва до касания) — расхождение с начисленной, ч;
* Х5 (реальные полёты) — события «падение» и «столкновение», найденные в журнале этого полёта.

Тот же журнал можно выгрузить (``flightlog``) и загрузить повторно через «Импорт журналов»: полёт распознаётся
по номеру «<серийный номер>-R<N>» как уже учтённый, наработка второй раз не начисляется.
"""
from __future__ import annotations

import math
import re
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..errors import NotFound
from ..models import Aircraft, Flight, LogImport, Mission, MissionRun
from ..timeutil import iso
from . import autopilot
from .usage import aircraft_totals, component_totals, installed_at

X1_THRESHOLD_PCT = 100.0
X2_THRESHOLD_M = 1.0
X3_THRESHOLD_H = 0.01
RUN_STATUS_RU = {"completed": "выполнена", "aborted": "прервана", "failed": "не учтена", "preparing": "подготовка",
                 "ready": "готов к старту", "in_flight": "в полёте", "landed": "ожидание журнала", "cancelled": "отменена"}
KIND_RU = {"fall": "падение", "collision": "столкновение"}


def _run(s: Session, run_id: int) -> MissionRun:
    run = s.get(MissionRun, run_id)
    if run is None:
        raise NotFound(f"Полёт R{run_id} не найден")
    return run


def flight_of(s: Session, run: MissionRun) -> Flight | None:
    """Полёт журнала, импортированного по этому выполнению: номер «<серийный номер>-R<N>»."""
    a = s.get(Aircraft, run.aircraft_id)
    if a is None:
        return None
    return s.scalar(select(Flight).where(Flight.aircraft_id == a.id, Flight.external_id == f"{a.serial}-R{run.id}").limit(1))


def waypoint_misses(m: Mission, track: list) -> list[dict]:
    """Промах в каждой точке маршрута: расстояние до ближайшей точки трека (3D, м), по горизонтали и по высоте."""
    pts = []
    for p in track or []:
        if len(p) < 3 or p[1] is None or p[2] is None:
            continue
        x, y = autopilot.to_xy(m.home_lat, m.home_lon, float(p[1]), float(p[2]))
        alt = float(p[3]) if len(p) > 3 and p[3] is not None else None
        pts.append((x, y, alt, p[0]))
    out = []
    for i, w in enumerate(m.waypoints or [], 1):
        wx, wy = autopilot.to_xy(m.home_lat, m.home_lon, float(w["lat"]), float(w["lon"]))
        walt = float(w.get("alt") or 0)
        best = None
        for x, y, alt, t in pts:
            dh = math.hypot(x - wx, y - wy)
            dv = abs(alt - walt) if alt is not None else 0.0
            d = math.hypot(dh, dv)
            if best is None or d < best[0]:
                best = (d, dh, dv, t)
        out.append({"n": i, "x_m": round(wx, 2), "y_m": round(wy, 2), "alt_m": walt,
                    "miss_m": round(best[0], 2) if best else None, "horizontal_m": round(best[1], 2) if best else None,
                    "vertical_m": round(best[2], 2) if best else None, "t_s": best[3] if best else None})
    return out


def _plan_matches(s: Session, run: MissionRun, m: Mission) -> bool | None:
    """Совпадает ли нынешняя миссия с планом, по которому летали (хеш плана). None — сравнить нельзя."""
    want = (run.summary or {}).get("plan_hash")
    a = s.get(Aircraft, run.aircraft_id)
    if not want or a is None:
        return None
    try:
        from .drones import plan_for
        return plan_for(m, a, run.id)["plan_hash"] == want
    except Exception:  # noqa: BLE001 — миссия изменена так, что план уже не строится (пределы, точки)
        return False


def report(s: Session, run_id: int, video_s: float | None = None) -> dict:
    run = _run(s, run_id)
    m = s.get(Mission, run.mission_id)
    a = s.get(Aircraft, run.aircraft_id)
    sm = run.summary or {}
    f = flight_of(s, run)
    out = {"run": {"id": run.id, "status": run.status, "status_ru": RUN_STATUS_RU.get(run.status, run.status),
                   "source": run.source or "sim", "started_at": iso(run.started_at), "ended_at": iso(run.ended_at),
                   "import_id": run.import_id, "reason": sm.get("reason") or sm.get("error"),
                   "agent_version": sm.get("agent_version"), "executor": sm.get("executor"),
                   "started_by": sm.get("started_by") or sm.get("start_requested_by")},
           "mission": {"id": m.id if m else run.mission_id, "name": (m.name if m else None) or sm.get("mission_name"),
                       "frame": (m.frame or "global") if m else None, "waypoints": len(m.waypoints or []) if m else None,
                       "plan_matches": _plan_matches(s, run, m) if m else None},
           "aircraft": {"id": a.id, "serial": a.serial, "name": a.name} if a else None}

    # Х1 — точки маршрута
    total = sm.get("wp_total") or (len(m.waypoints or []) if m else 0)
    reached = sm.get("wp_reached")
    pct = round(100.0 * reached / total, 1) if total and reached is not None else None
    out["x1"] = {"reached": reached, "total": total, "pct": pct, "threshold_pct": X1_THRESHOLD_PCT,
                 "ok": pct is not None and pct >= X1_THRESHOLD_PCT}

    # Х2 — промах в точках по треку
    misses = waypoint_misses(m, run.track) if m else []
    worst = max((w["miss_m"] for w in misses if w["miss_m"] is not None), default=None)
    out["x2"] = {"points": misses, "max_miss_m": worst, "track_points": len(run.track or []),
                 "threshold_m": X2_THRESHOLD_M, "ok": worst is not None and worst <= X2_THRESHOLD_M
                 and all(w["miss_m"] is not None for w in misses)}

    # Х3 — начисление борту и узлам: до и после полёта
    x3 = {"flight": None, "aircraft": None, "components": [], "video_s": video_s, "threshold_h": X3_THRESHOLD_H, "ok": None}
    if f is not None and a is not None:
        t0, t1 = f.started_at, f.started_at + timedelta(seconds=1)
        b, af = aircraft_totals(s, [a], before=t0)[a.id], aircraft_totals(s, [a], before=t1)[a.id]
        x3["flight"] = {"id": f.id, "external_id": f.external_id, "import_id": f.import_id, "started_at": iso(f.started_at),
                        "ended_at": iso(f.ended_at), "duration_h": round(f.duration_h, 4),
                        "duration_s": round(f.duration_h * 3600, 1), "cycles": f.cycles}
        x3["aircraft"] = {"hours_before": round(b.hours, 4), "hours_after": round(af.hours, 4),
                          "delta_h": round(af.hours - b.hours, 4), "cycles_before": b.cycles, "cycles_after": af.cycles,
                          "delta_cycles": af.cycles - b.cycles}
        comps = [i for i in installed_at(s, a.id, t0)]
        cb = component_totals(s, [i.component for i in comps], before=t0)
        ca = component_totals(s, [i.component for i in comps], before=t1)
        for i in sorted(comps, key=lambda x: x.position):
            c = i.component
            x3["components"].append({"serial": c.serial, "position": i.position, "hours_before": round(cb[c.id].hours, 4),
                                     "hours_after": round(ca[c.id].hours, 4), "delta_h": round(ca[c.id].hours - cb[c.id].hours, 4),
                                     "delta_cycles": ca[c.id].cycles - cb[c.id].cycles})
        deltas = [x3["aircraft"]["delta_h"]] + [c["delta_h"] for c in x3["components"]]
        cycles_ok = x3["aircraft"]["delta_cycles"] == 1 and all(c["delta_cycles"] == 1 for c in x3["components"])
        if video_s is not None:
            vh = float(video_s) / 3600.0
            x3["video_h"] = round(vh, 4)
            x3["max_diff_h"] = round(max(abs(d - vh) for d in deltas), 4)
            x3["ok"] = x3["max_diff_h"] <= X3_THRESHOLD_H and cycles_ok
        x3["cycles_ok"] = cycles_ok
        x3["same_for_all"] = max(deltas) - min(deltas) < 1e-6
    out["x3"] = x3

    # Х5 — события датчиков реального полёта
    events = [{"kind": e.kind, "kind_ru": KIND_RU.get(e.kind, e.kind), "peak_g": e.peak_g, "at": iso(e.occurred_at),
               "source": e.source, "details": e.details} for e in (f.events if f is not None else [])]
    out["x5"] = {"events": events, "count": len(events), "threshold": 0, "ok": f is not None and not events}

    # Повторные загрузки этого журнала (дубликаты): наработка не начисляется
    rep = []
    if run.import_id is not None:
        q = select(LogImport).where(LogImport.id > run.import_id,
                                    or_(LogImport.duplicate_of_id == run.import_id,
                                        LogImport.filename.like(f"%-R{run.id}-%"), LogImport.filename.like(f"%R{run.id}.%")))
        rep = [{"id": i.id, "filename": i.filename, "status": i.status, "imported_at": iso(i.imported_at),
                "hours_added": round(i.hours_added or 0, 4), "flights_duplicate": i.flights_duplicate}
               for i in s.scalars(q.order_by(LogImport.id))]
    out["reimports"] = rep
    return out


def flightlog(s: Session, run_id: int) -> tuple[dict, str]:
    """Журнал полёта R N в формате tbo-flightlog/1.0 — для повторной загрузки через «Импорт журналов»."""
    run = _run(s, run_id)
    f = flight_of(s, run)
    if f is None:
        raise NotFound(f"Журнал полёта R{run_id} ещё не импортирован (полёт не учтён)")
    a = s.get(Aircraft, run.aircraft_id)
    entry = {"flight_id": f.external_id, "start": iso(f.started_at), "end": iso(f.ended_at), "cycles": f.cycles,
             "mission": f.mission, "pilot": f.pilot}
    doc = {"format": "tbo-flightlog/1.0", "aircraft_serial": a.serial,
           "source": f"tbo-tracker · выгрузка журнала полёта R{run.id} (импорт №{f.import_id})", "flights": [entry]}
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", a.serial)
    return doc, f"flight-R{run.id}-{safe}.json"


def runs(s: Session, source: str | None = "drone") -> list[dict]:
    """Все выполнения миссий (по умолчанию — на реальном борту): номер R, борт, миссия, итог, точки, длительность."""
    q = select(MissionRun).order_by(MissionRun.id)
    if source:
        q = q.where(MissionRun.source == source)
    names = {a.id: (a.serial, a.name) for a in s.scalars(select(Aircraft))}
    out = []
    for r in s.scalars(q):
        sm = r.summary or {}
        serial, name = names.get(r.aircraft_id, ("?", "?"))
        out.append({"run": r.id, "started_at": iso(r.started_at), "ended_at": iso(r.ended_at), "aircraft_serial": serial,
                    "aircraft": name, "mission": sm.get("mission_name"), "status": r.status,
                    "status_ru": RUN_STATUS_RU.get(r.status, r.status), "wp_reached": sm.get("wp_reached"),
                    "wp_total": sm.get("wp_total"), "duration_s": sm.get("duration_s"), "import_id": r.import_id,
                    "hours_added": sm.get("hours_added"), "reason": sm.get("reason") or sm.get("error")})
    return out


# ------------------------------------------------------------------ вывод
def _n(v, d=2) -> str:
    if v is None:
        return "—"
    return f"{v:.{d}f}".replace(".", ",") if isinstance(v, float) else str(v)


def _ok(v) -> str:
    return "—" if v is None else ("соответствует" if v else "**не соответствует**")


def markdown(r: dict) -> str:
    run, ms, ac = r["run"], r["mission"], r["aircraft"] or {}
    x1, x2, x3, x5 = r["x1"], r["x2"], r["x3"], r["x5"]
    L = [f"# Отчёт по полёту R{run['id']}", "",
         "| | |", "|---|---|",
         f"| Борт | {ac.get('name', '—')} ({ac.get('serial', '—')}) |",
         f"| Миссия | №{ms['id']} «{ms['name'] or '—'}», точек: {_n(ms['waypoints'])}, кадр: {ms['frame'] or '—'} |",
         f"| Итог выполнения | {run['status_ru']}" + (f" — {run['reason']}" if run.get("reason") else "") + " |",
         f"| Начало / окончание (UTC) | {run['started_at'] or '—'} / {run['ended_at'] or '—'} |",
         f"| Агент, исполнитель | {run.get('agent_version') or '—'} · {run.get('executor') or '—'} |",
         f"| Импорт журнала | №{_n(run['import_id'])} |"]
    if ms.get("plan_matches") is False:
        L += ["", "**Внимание:** миссия изменена после полёта — точки маршрута в отчёте могут не совпадать с планом полёта."]
    L += ["", "## Характеристики", "",
          "| № | Показатель | Порог | Результат | Вывод |", "|---|---|---|---|---|",
          f"| Х1 | доля достигнутых точек маршрута, % | ≥ {_n(x1['threshold_pct'], 0)} | "
          f"{_n(x1['pct'], 1)} ({_n(x1['reached'])} из {_n(x1['total'])}) | {_ok(x1['ok'])} |",
          f"| Х2 | наибольший промах в точке маршрута, м | ≤ {_n(x2['threshold_m'], 1)} | {_n(x2['max_miss_m'])} | {_ok(x2['ok'])} |"]
    if x3.get("flight"):
        res = (f"начислено {_n(x3['aircraft']['delta_h'], 4)} ч, циклов +{x3['aircraft']['delta_cycles']}"
               + (f"; по видео {_n(x3.get('video_h'), 4)} ч; расхождение {_n(x3.get('max_diff_h'), 4)} ч" if x3.get("video_s") is not None else
                  "; длительность по видео не задана (--video-s)"))
        L.append(f"| Х3 | расхождение начисленной длительности с фактической, ч; циклы | ≤ {_n(x3['threshold_h'])}; +1 | {res} | {_ok(x3['ok'])} |")
    else:
        L.append("| Х3 | расхождение начисленной длительности с фактической, ч; циклы | ≤ 0,01; +1 | журнал не импортирован | — |")
    L.append(f"| Х5 | ложные срабатывания на реальном полёте, шт. | 0 | {x5['count']} | {_ok(x5['ok'])} |")
    L += ["", "## Х2: промах в каждой точке", "", "| Точка | Смещение от взлёта (восток; север), м | Высота, м | Промах 3D, м | По горизонтали, м | По высоте, м | Время от арминга, с |",
          "|---|---|---|---|---|---|---|"]
    for p in x2["points"]:
        L.append(f"| {p['n']} | {_n(p['x_m'])}; {_n(p['y_m'])} | {_n(p['alt_m'], 1)} | {_n(p['miss_m'])} | {_n(p['horizontal_m'])} | "
                 f"{_n(p['vertical_m'])} | {_n(p['t_s'], 1)} |")
    L += ["", f"Точек трека (1 Гц): {x2['track_points']}. Промах — расстояние от точки маршрута до ближайшей точки трека."]
    if x3.get("flight"):
        fl = x3["flight"]
        L += ["", "## Х3: начисление борту и узлам", "",
              f"Полёт {fl['external_id']}: {fl['started_at']} – {fl['ended_at']} UTC, {_n(fl['duration_s'], 1)} с "
              f"({_n(fl['duration_h'], 4)} ч), циклов {fl['cycles']}.", "",
              "| Объект | Позиция | Наработка до, ч | После, ч | Начислено, ч | Циклы |", "|---|---|---|---|---|---|",
              f"| борт {ac.get('name', '')} | — | {_n(x3['aircraft']['hours_before'], 4)} | {_n(x3['aircraft']['hours_after'], 4)} | "
              f"{_n(x3['aircraft']['delta_h'], 4)} | +{x3['aircraft']['delta_cycles']} |"]
        for c in x3["components"]:
            L.append(f"| {c['serial']} | {c['position']} | {_n(c['hours_before'], 4)} | {_n(c['hours_after'], 4)} | "
                     f"{_n(c['delta_h'], 4)} | +{c['delta_cycles']} |")
        if not x3["components"]:
            L.append("| узлы | — | на борту нет установленных узлов | | | |")
    L += ["", "## Х5: события датчиков", ""]
    L += [f"- {e['kind_ru']}, {_n(e['peak_g'], 1)} g, {e['at']} ({e['source']})" for e in x5["events"]] or ["Событий нет."]
    if r["reimports"]:
        L += ["", "## Повторная загрузка журнала", "", "| Импорт | Файл | Итог | Начислено, ч |", "|---|---|---|---|"]
        L += [f"| №{i['id']} | {i['filename']} | {i['status']} | {_n(i['hours_added'], 4)} |" for i in r["reimports"]]
    return "\n".join(L) + "\n"


def runs_csv(rows: list[dict]) -> str:
    import csv
    import io
    buf = io.StringIO()
    cols = ["run", "started_at", "ended_at", "aircraft_serial", "aircraft", "mission", "status_ru", "wp_reached", "wp_total",
            "duration_s", "import_id", "hours_added", "reason"]
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({**r, "run": f"R{r['run']}"})
    return buf.getvalue()
