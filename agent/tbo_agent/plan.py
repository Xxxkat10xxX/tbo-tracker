"""План полёта от сервера (формат tbo-plan/1): проверка целостности, пределов и геометрия."""
from __future__ import annotations

import hashlib
import json
import math

R_EARTH = 6371000.0


class PlanError(Exception):
    pass


def to_xy(lat0: float, lon0: float, lat: float, lon: float):
    """Локальные координаты (восток, север), м."""
    x = math.radians(lon - lon0) * R_EARTH * math.cos(math.radians(lat0))
    y = math.radians(lat - lat0) * R_EARTH
    return x, y


def dist_m(lat0, lon0, lat, lon) -> float:
    x, y = to_xy(lat0, lon0, lat, lon)
    return math.hypot(x, y)


def plan_hash(plan: dict) -> str:
    body = {k: v for k, v in plan.items() if k != "plan_hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def frame_of(plan: dict) -> str:
    return "local" if (plan or {}).get("frame") == "local" else "global"


def effective_limits(server: dict, local: dict, frame: str = "global") -> dict:
    """Действует более строгое: меньший потолок высоты, удаления и скорости, больший минимум заряда.
    Для локальной навигации на борту свои пределы (local_max_*), для полёта по GPS — max_*."""
    mine = dict(local)
    if frame == "local":
        mine["max_alt_m"], mine["max_dist_m"] = local.get("local_max_alt_m", 3.0), local.get("local_max_dist_m", 20.0)
        mine["max_speed_ms"] = local.get("local_max_speed_ms", 2.0)

    def lo(k):
        vals = [float(v[k]) for v in (server, mine) if v.get(k) is not None]
        return min(vals) if vals else None

    out = dict(mine)
    for k in ("max_alt_m", "max_dist_m", "max_home_offset_m", "max_speed_ms"):
        out[k] = lo(k)
    out["min_battery_pct"] = max(float(server.get("min_battery_pct") or 0), float(local.get("min_battery_pct") or 0))
    return out


def validate(plan: dict, serial: str, limits: dict):
    """Исключение PlanError с понятной причиной, если план нельзя выполнять на этом борту."""
    if not isinstance(plan, dict) or plan.get("format") != "tbo-plan/1":
        raise PlanError("неизвестный формат плана")
    if plan.get("plan_hash") != plan_hash(plan):
        raise PlanError("план повреждён при передаче (не совпадает контрольная сумма)")
    if (plan.get("aircraft") or {}).get("serial") != serial:
        raise PlanError("план для другого борта (%s), этот борт — %s" % ((plan.get("aircraft") or {}).get("serial"), serial))
    try:
        home = plan["home"]
        lat0, lon0 = float(home["lat"]), float(home["lon"])
        wps = plan["waypoints"]
        speed, t_alt = float(plan["speed_ms"]), float(plan["takeoff_alt"])
    except (KeyError, TypeError, ValueError):
        raise PlanError("в плане нет обязательных полей") from None
    if not (-90 <= lat0 <= 90 and -180 <= lon0 <= 180):
        raise PlanError("неверная точка взлёта")
    if not isinstance(wps, list) or not 1 <= len(wps) <= 200:
        raise PlanError("в плане должно быть от 1 до 200 точек")
    local = frame_of(plan) == "local"
    max_speed = limits.get("max_speed_ms") or 20
    if not 0.2 <= speed <= max_speed:
        raise PlanError("скорость %.1f м/с вне диапазона 0,2–%.1f м/с" % (speed, max_speed))
    max_alt, max_dist = limits.get("max_alt_m") or 120, limits.get("max_dist_m") or 1000
    min_alt = 0.5 if local else 2.0
    if not min_alt <= t_alt <= max_alt:
        raise PlanError("высота взлёта %.1f м вне диапазона %.1f–%.1f м" % (t_alt, min_alt, max_alt))
    for i, w in enumerate(wps, 1):
        try:
            lat, lon, alt, hold = float(w["lat"]), float(w["lon"]), float(w["alt"]), float(w.get("hold_s") or 0)
        except (KeyError, TypeError, ValueError):
            raise PlanError("точка %d заполнена неверно" % i) from None
        if not all(math.isfinite(v) for v in (lat, lon, alt, hold)) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise PlanError("точка %d: неверные координаты" % i)
        if not min_alt <= alt <= max_alt:
            raise PlanError("точка %d: высота %.1f м вне пределов борта %.1f–%.1f м" % (i, alt, min_alt, max_alt))
        d = dist_m(lat0, lon0, lat, lon)
        if d > max_dist:
            raise PlanError("точка %d: %.0f м от точки взлёта, предел борта %.0f м" % (i, d, max_dist))
        if not 0 <= hold <= 600:
            raise PlanError("точка %d: удержание вне 0–600 с" % i)
