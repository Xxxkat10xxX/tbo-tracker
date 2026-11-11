"""Шаблоны маршрутов: съёмка площади галсами с перекрытием, двойная сетка, облёт по периметру, облёт по кругу.

Всё считается в метрах от точки взлёта: x — восток (в локальной миссии — вправо от носа дрона при взлёте),
y — север (вперёд). Направление галсов и курсы — градусы по часовой стрелке от «верха» карты, как в редакторе
миссии и в плане для борта (yaw_deg).

Съёмка площади (камера смотрит вниз):
* след кадра на земле: 2·h·tg(угол обзора / 2) — поперёк и вдоль галса;
* шаг между галсами — след поперёк × (1 − боковое перекрытие), шаг снимков — след вдоль × (1 − продольное);
* галсы ставятся симметрично относительно центра участка так, чтобы крайние кадры закрывали его края;
* курс постоянный, по направлению галсов (курс в первой точке, режим «не менять»): дрон летит вдоль галса
  вперёд и назад и боком между галсами, без разворотов — кадр всегда лежит поперёк галса одинаково;
* точки маршрута — концы галсов либо каждая точка съёмки с удержанием (если камера не снимает по расстоянию).
"""
from __future__ import annotations

import math

from ..errors import DomainError
from . import autopilot

MAX_WP = 200
# нижняя камера Technic 6S (fisheye, калибровка technic/camera_info/fisheye_cam.yaml: fx ≈ 332 пикс. при 640×480)
TECHNIC_CAMERA = {"fov_across_deg": 88.0, "fov_along_deg": 72.0, "image_px_across": 320}


def _unit(angle_deg: float):
    """Единичные векторы: u — вдоль галса (angle_deg по часовой от «верха»), v — поперёк, вправо от u."""
    a = math.radians(angle_deg)
    return (math.sin(a), math.cos(a)), (math.cos(a), -math.sin(a))


def _pt(c, u, v, a: float, b: float):
    return (c[0] + a * u[0] + b * v[0], c[1] + a * u[1] + b * v[1])


def _bearing(frm, to) -> float:
    """Курс от точки frm на точку to, ° по часовой от «верха»."""
    return math.degrees(math.atan2(to[0] - frm[0], to[1] - frm[1])) % 360.0


def _even(span: float, step: float):
    """Позиции от −span/2 до +span/2 с шагом не больше step (крайние — на краях); span = 0 — одна позиция."""
    if span <= 1e-9:
        return [0.0]
    n = int(math.ceil(span / step - 1e-9)) + 1
    return [-span / 2 + span * k / (n - 1) for k in range(n)]


def footprint(alt: float, fov_deg: float) -> float:
    if not 1.0 <= fov_deg <= 170.0:
        raise DomainError("Угол обзора камеры: от 1° до 170°")
    return 2.0 * alt * math.tan(math.radians(fov_deg) / 2.0)


def _grid(p: dict, angle: float, start_near):
    """Галсы одной сетки: список галсов (каждый — список точек xy по порядку облёта) и сведения о съёмке."""
    alt, width, length = float(p["alt"]), float(p["width"]), float(p["length"])
    fp_x, fp_y = footprint(alt, float(p["fov_across_deg"])), footprint(alt, float(p["fov_along_deg"]))
    side, front = float(p["side_overlap"]), float(p["front_overlap"])
    spacing, shot = fp_x * (1.0 - side), fp_y * (1.0 - front)
    if spacing <= 0.01 or shot <= 0.01:
        raise DomainError("Перекрытие слишком большое: шаг между галсами или снимками меньше 1 см")
    if width <= fp_x:
        offsets = [0.0]
    else:
        n = int(math.ceil((width - fp_x) / spacing - 1e-9)) + 1
        offsets = [-(n - 1) * spacing / 2 + k * spacing for k in range(n)]
    along = _even(length, shot) if p.get("photo_points") else ([-length / 2, length / 2] if length > 1e-9 else [0.0])
    c, (u, v) = (float(p["center_x"]), float(p["center_y"])), _unit(angle)
    best = None
    for rev_order in (False, True):
        for flip_first in (False, True):
            lines = []
            for k, b in enumerate(reversed(offsets) if rev_order else offsets):
                pts = [_pt(c, u, v, a, b) for a in along]
                if (k % 2 == 1) != flip_first:
                    pts.reverse()
                lines.append(pts)
            d = math.dist(lines[0][0], start_near)
            if best is None or d < best[0] - 1e-6:
                best = (d, lines)
    shots = len(offsets) * len(_even(length, shot))
    info = {"lines": len(offsets), "line_spacing_m": round(spacing, 2), "photo_spacing_m": round(shot, 2),
            "footprint_m": [round(fp_x, 2), round(fp_y, 2)], "photos": shots}
    swaths = [[list(map(_r2, ln[0])), list(map(_r2, ln[-1])), round(fp_x, 2)] for ln in best[1]]
    return best[1], info, swaths


def _r2(x: float) -> float:
    return round(x, 2)


def _rect(p: dict):
    c, (u, v) = (float(p["center_x"]), float(p["center_y"])), _unit(float(p["angle_deg"]))
    w, ln = float(p["width"]), float(p["length"])
    return [_pt(c, u, v, a, b) for a, b in ((-ln / 2, -w / 2), (ln / 2, -w / 2), (ln / 2, w / 2), (-ln / 2, w / 2))]


def generate(p: dict) -> dict:
    """Точки маршрута по шаблону. p — поля schemas.RoutePatternIn. Ответ: waypoints (широта/долгота, как в миссии),
    yaw_mode, xy — те же точки в метрах, outline — контур участка, swaths — полосы съёмки, info, stats, warnings."""
    kind, alt = p["kind"], float(p["alt"])
    lat0, lon0 = float(p["home_lat"]), float(p["home_lon"])
    home = (0.0, 0.0)
    hold = float(p.get("photo_hold_s") or 0) if p.get("photo_points") else 0.0
    pts, yaws, info, swaths, warnings, outline = [], {}, {}, [], [], None
    yaw_mode = "keep"

    if kind in ("survey", "crosshatch"):
        if float(p["width"]) <= 0 or float(p["length"]) < 0:
            raise DomainError("Размеры участка должны быть больше нуля")
        angle = float(p["angle_deg"]) % 360.0
        lines, info, swaths = _grid(p, angle, home)
        yaws[0] = angle
        for ln in lines:
            pts += ln
        if kind == "crosshatch":
            q = dict(p, width=p["length"], length=p["width"])
            lines2, info2, sw2 = _grid(q, (angle + 90.0) % 360.0, pts[-1])
            yaws[len(pts)] = (angle + 90.0) % 360.0
            for ln in lines2:
                pts += ln
            swaths += sw2
            info = {"lines": info["lines"] + info2["lines"], "line_spacing_m": info["line_spacing_m"],
                    "photo_spacing_m": info["photo_spacing_m"], "footprint_m": info["footprint_m"],
                    "photos": info["photos"] + info2["photos"]}
        info["area_m2"] = round(float(p["width"]) * float(p["length"]), 1)
        px = p.get("image_px_across")
        if px:
            info["gsd_cm"] = round(info["footprint_m"][0] / float(px) * 100.0, 2)
        outline = [list(map(_r2, q)) for q in _rect(p)]
    elif kind == "perimeter":
        if float(p["width"]) <= 0 or float(p["length"]) <= 0:
            raise DomainError("Размеры участка должны быть больше нуля")
        corners = _rect(p)
        k0 = min(range(4), key=lambda i: math.dist(corners[i], home))
        order = corners[k0:] + corners[:k0]
        if p.get("direction") == "ccw":
            order = [order[0]] + order[1:][::-1]
        laps = max(1, int(p.get("laps") or 1))
        pts = order * laps + [order[0]]
        yaw_mode = "route"
        outline = [list(map(_r2, q)) for q in corners]
        info = {"perimeter_m": round(2 * (float(p["width"]) + float(p["length"])), 1), "laps": laps}
    elif kind == "orbit":
        r, n = float(p["radius"]), int(p.get("points") or 12)
        if r <= 0:
            raise DomainError("Радиус облёта должен быть больше нуля")
        if not 3 <= n <= 72:
            raise DomainError("Точек на круге: от 3 до 72")
        c = (float(p["center_x"]), float(p["center_y"]))
        start = _bearing(c, home) if math.dist(c, home) > 1e-6 else 180.0
        sign = -1.0 if p.get("direction") == "ccw" else 1.0
        laps = max(1, int(p.get("laps") or 1))
        for k in range(n * laps + 1):
            b = math.radians(start + sign * 360.0 * k / n)
            pts.append((c[0] + r * math.sin(b), c[1] + r * math.cos(b)))
        if p.get("face_center", True):
            for i, q in enumerate(pts):
                yaws[i] = _bearing(q, c)
        else:
            yaw_mode = "route"
        outline = {"center": [_r2(c[0]), _r2(c[1])], "radius": round(r, 2)}
        info = {"circle_m": round(2 * math.pi * r, 1), "laps": laps, "step_m": round(2 * r * math.sin(math.pi / n), 2)}
    else:
        raise DomainError("Неизвестный шаблон маршрута")

    if len(pts) > MAX_WP:
        hint = ("снимите «точку на каждом снимке» или уменьшите перекрытие" if p.get("photo_points")
                else "увеличьте высоту, уменьшите перекрытие или участок")
        raise DomainError(f"Получилось {len(pts)} точек — больше {MAX_WP}: {hint}")
    wps = []
    for i, (x, y) in enumerate(pts):
        lat, lon = autopilot.to_ll(lat0, lon0, x, y)
        w = {"lat": round(lat, 8), "lon": round(lon, 8), "alt": round(alt, 2), "hold_s": hold}
        if i in yaws:
            w["yaw_deg"] = round(yaws[i] % 360.0, 1) % 360.0
        wps.append(w)
    mission = {"home_lat": lat0, "home_lon": lon0, "frame": p.get("frame") or "global", "takeoff_alt": float(p.get("takeoff_alt") or alt),
               "speed_ms": float(p.get("speed_ms") or 5), "return_home": bool(p.get("return_home", True)),
               "yaw_mode": yaw_mode, "waypoints": wps}
    autopilot.validate(mission)
    if mission["frame"] == "local" and alt > 3.0:
        warnings.append("высота больше 3 м — выше предела агента Technic для полёта в помещении")
    if kind in ("survey", "crosshatch") and mission["frame"] == "local" and alt < 1.0:
        warnings.append("ниже 1 м камера видит мало: проверьте, хватает ли следа кадра")
    return {"waypoints": wps, "yaw_mode": yaw_mode, "xy": [[_r2(x), _r2(y)] for x, y in pts], "outline": outline,
            "swaths": swaths, "info": info, "stats": autopilot.plan_stats(mission), "warnings": warnings}
