"""Предполётные проверки на борту (по телеметрии полётного контроллера) — до загрузки плана и перед стартом."""
from __future__ import annotations

from .plan import dist_m, frame_of

FIX_LABELS = {-1: "нет", 0: "нет", 1: "нет", 2: "2D", 3: "3D", 4: "DGPS", 5: "RTK float", 6: "RTK fixed"}


def _num(v):
    return None if v is None else float(v)


def preflight(snap: dict, plan: dict, limits: dict) -> list:
    """[{"name", "ok", "detail"}] — что проверено и почему не пройдено."""
    out = []

    def add(name, ok, detail=""):
        out.append({"name": name, "ok": bool(ok), "detail": detail})

    add("Связь с полётным контроллером", snap.get("connected"), "" if snap.get("connected") else "MAVROS не видит контроллер")
    add("Моторы выключены, борт на земле", not snap.get("armed") and not snap.get("in_air"),
        "борт заармлен или в воздухе" if snap.get("armed") or snap.get("in_air") else "")
    if frame_of(plan) == "local":
        add("Локальная позиция (Optical Flow / ArUco)", snap.get("local_ok"),
            "позиция есть" if snap.get("local_ok") else "нет локальной позиции — проверьте камеру, дальномер, optical_flow/aruco в technic.launch")
        _battery(add, snap, limits)
        return out
    fix, sats = snap.get("gps_fix"), snap.get("sats")
    need = int(limits.get("min_sats") or 8)
    gps_ok = fix is not None and fix >= 3 and (sats is None or sats >= need) and snap.get("lat") is not None
    add("GPS", gps_ok, "фиксация %s, спутников %s (нужно ≥ %d)" % (FIX_LABELS.get(fix, fix), "—" if sats is None else sats, need))
    add("Точка «дом» задана в контроллере", snap.get("home") is not None, "" if snap.get("home") else "дождитесь фиксации GPS")
    lat, lon = _num(snap.get("lat")), _num(snap.get("lon"))
    if lat is not None and lon is not None:
        off = dist_m(plan["home"]["lat"], plan["home"]["lon"], lat, lon)
        tol = float(limits.get("max_home_offset_m") or 30)
        add("Борт в точке взлёта миссии", off <= tol, "%.0f м от точки взлёта миссии (допуск %.0f м)" % (off, tol))
    else:
        add("Борт в точке взлёта миссии", False, "нет координат борта")
    _battery(add, snap, limits)
    return out


def _battery(add, snap: dict, limits: dict):
    pct, volt = _num(snap.get("battery_pct")), _num(snap.get("battery_v"))
    min_pct = float(limits.get("min_battery_pct") or 50)
    if pct is not None:
        add("Заряд батареи", pct >= min_pct, "%.0f%% (нужно ≥ %.0f%%)" % (pct, min_pct))
    elif volt is not None:
        cells, min_cell = int(limits.get("cells") or 6), float(limits.get("min_cell_v") or 3.75)
        add("Заряд батареи", volt / cells >= min_cell, "%.2f В на ячейку (нужно ≥ %.2f В, %dS)" % (volt / cells, min_cell, cells))
    else:
        add("Заряд батареи", False, "нет данных о батарее")


def failed(checks: list) -> list:
    return [c for c in checks if not c["ok"]]


def summary(checks: list) -> str:
    bad = failed(checks)
    return "проверки пройдены" if not bad else "; ".join("%s: %s" % (c["name"], c["detail"] or "не пройдено") for c in bad)
