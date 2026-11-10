"""Автономный полёт по заданному маршруту: кинематический автопилот-симулятор и форматы миссий.

Симулятор воспроизводит штатную последовательность автопилота мультикоптера/VTOL:
предполётная проверка → взлёт на высоту → следование по точкам маршрута (наведение
«преследование точки» с коррекцией бокового отклонения, ограничения скорости и ускорения,
постоянный ветер) → удержание на точке → возврат домой (RTL) → посадка → разоружение.

Выход — трек (t, широта, долгота, высота, скорость, фаза) и выборка модуля ускорения |a| (25 Гц),
которые оформляются как журнал полёта tbo-flightlog и проходят обычный импорт: наработка
начисляется борту и узлам, акселерометр анализируется на падения/столкновения.

Обмен с наземными станциями и автопилотами (ArduPilot, PX4): экспорт/импорт QGroundControl .plan
(JSON) и .waypoints (QGC WPL 110). Команды MAVLink: 22 — взлёт, 16 — точка маршрута, 20 — возврат домой.
"""
from __future__ import annotations

import json
import math
from datetime import timedelta

from ..errors import DomainError

R_EARTH = 6371000.0
CMD_WAYPOINT, CMD_RTL, CMD_LAND, CMD_TAKEOFF = 16, 20, 21, 22
FRAME_REL_ALT = 3          # MAV_FRAME_GLOBAL_RELATIVE_ALT
FRAME_GLOBAL = 0           # MAV_FRAME_GLOBAL: высота над уровнем моря (AMSL) — при импорте пересчитывается от точки «дом»
DT = 0.2                   # шаг интегрирования, с
IMU_HZ = 25
CLIMB, DESCENT, ACCEL = 2.5, 1.5, 2.0     # м/с, м/с, м/с²
ACCEPT_R = 3.0             # радиус принятия точки, м (по GPS)
LOCAL_ACCEPT_R = 0.3       # в помещении (метры от точки взлёта): с 3 м все точки ближе 3 м к старту «достигались» сразу
PHASES = {"preflight": "Предполётная проверка", "takeoff": "Взлёт", "mission": "Полёт по маршруту",
          "hold": "Удержание", "rtl": "Возврат домой", "land": "Посадка", "crash": "Аварийное завершение"}


# ------------------------------------------------------------------ геометрия
def to_xy(lat0, lon0, lat, lon):
    """Локальные координаты (восток, север) в метрах относительно точки взлёта."""
    x = math.radians(lon - lon0) * R_EARTH * math.cos(math.radians(lat0))
    y = math.radians(lat - lat0) * R_EARTH
    return x, y


def to_ll(lat0, lon0, x, y):
    lat = lat0 + math.degrees(y / R_EARTH)
    lon = lon0 + math.degrees(x / (R_EARTH * math.cos(math.radians(lat0))))
    return lat, lon


def plan_stats(m: dict) -> dict:
    """Протяжённость и оценка времени маршрута (без ветра)."""
    pts = [(0.0, 0.0, 0.0)] + [(*to_xy(m["home_lat"], m["home_lon"], w["lat"], w["lon"]), float(w["alt"])) for w in m["waypoints"]]
    dist, hold = 0.0, sum(float(w.get("hold_s") or 0) for w in m["waypoints"])
    for a, b in zip(pts, pts[1:]):
        dist += math.dist(a[:2], b[:2])
    if m.get("return_home", True) and len(pts) > 1:
        dist += math.dist(pts[-1][:2], (0, 0))
    first_alt = pts[1][2] if len(pts) > 1 else float(m.get("takeoff_alt") or 30)
    vert = first_alt / CLIMB + (pts[-1][2] if len(pts) > 1 else first_alt) / DESCENT
    t = dist / max(1.0, float(m["speed_ms"])) * 1.08 + hold + vert + 6
    return {"distance_m": round(dist, 1), "duration_s": round(t), "waypoints": len(m["waypoints"]),
            "max_alt_m": max([p[2] for p in pts[1:]] or [first_alt])}


# Локальная навигация (в помещении: Optical Flow с дальномером VL53L1X до 4 м, ArUco): малые высоты и скорости
LOCAL = {"alt": (0.5, 10.0), "speed": (0.2, 3.0), "dist": 100.0}
GLOBAL = {"alt": (2.0, 500.0), "speed": (0.5, 40.0), "dist": 20000.0}


def validate(m: dict):
    local = (m.get("frame") or "global") == "local"
    lim = LOCAL if local else GLOBAL
    if not m["waypoints"]:
        raise DomainError("Добавьте хотя бы одну точку маршрута")
    if len(m["waypoints"]) > 200:
        raise DomainError("Не более 200 точек маршрута")
    lo, hi = lim["speed"]
    if not (lo <= float(m["speed_ms"]) <= hi):
        raise DomainError(f"Крейсерская скорость: от {lo:g} до {hi:g} м/с" + (" (локальная навигация)" if local else ""))
    lo, hi = lim["alt"]
    if not (lo <= float(m.get("takeoff_alt") or lo) <= hi):
        raise DomainError(f"Высота взлёта: от {lo:g} до {hi:g} м" + (" (локальная навигация)" if local else ""))
    for i, w in enumerate(m["waypoints"], 1):
        if not (-90 <= float(w["lat"]) <= 90 and -180 <= float(w["lon"]) <= 180):
            raise DomainError(f"Точка {i}: некорректные координаты")
        if not (lo <= float(w["alt"]) <= hi):
            raise DomainError(f"Точка {i}: высота должна быть от {lo:g} до {hi:g} м над точкой взлёта"
                              + (" (локальная навигация)" if local else ""))
        if float(w.get("hold_s") or 0) < 0 or float(w.get("hold_s") or 0) > 600:
            raise DomainError(f"Точка {i}: удержание от 0 до 600 с")
        d = math.dist((0, 0), to_xy(m["home_lat"], m["home_lon"], float(w["lat"]), float(w["lon"])))
        if d > lim["dist"]:
            raise DomainError(f"Точка {i}: {d:.0f} м от точки взлёта — больше {lim['dist']:g} м"
                              + (" для локальной навигации" if local else " — проверьте координаты"))


# ------------------------------------------------------------------ симулятор
def simulate(m: dict, wind_ms: float = 3.0, wind_from_deg: float = 250.0, fault_at_wp: int | None = None) -> dict:
    """Кинематическая модель автономного полёта. fault_at_wp — смоделировать столкновение на подлёте к точке N."""
    validate(m)
    local = (m.get("frame") or "global") == "local"
    lat0, lon0 = m["home_lat"], m["home_lon"]
    v_max = float(m["speed_ms"])
    # в помещении: точки в метрах — малый радиус принятия (не меньше шага модели, чтобы не «перепрыгнуть» точку)
    # и без ветра; по высоте — тоже в пределах дециметров, а не метра
    accept_r = max(LOCAL_ACCEPT_R, v_max * DT) if local else ACCEPT_R
    alt_tol = 0.2 if local else 1.0
    if local:
        wind_ms = 0.0
    gust_k = 0.0 if local else 1.0
    wps = [(*to_xy(lat0, lon0, float(w["lat"]), float(w["lon"])), float(w["alt"]), float(w.get("hold_s") or 0)) for w in m["waypoints"]]
    if m.get("return_home", True):
        wps.append((0.0, 0.0, wps[-1][2], 0.0))
    wind_to = math.radians(wind_from_deg + 180)
    wx, wy = wind_ms * math.sin(wind_to), wind_ms * math.cos(wind_to)

    x = y = z = 0.0
    vx = vy = 0.0
    t = 0.0
    track, acc, events_log = [], [], []
    phase, idx, hold_left, max_xte = "takeoff", 0, None, 0.0
    leg_start = (0.0, 0.0)
    status = "completed"
    imu_next = 0.0

    def sample(a_extra=0.0, n=1):
        nonlocal imu_next
        while imu_next <= t:
            vib = 0.05 * math.sin(imu_next * 37.0) + 0.03 * math.sin(imu_next * 91.0)
            acc.append(round(max(0.0, 1.0 + vib + a_extra), 3))
            imu_next += 1.0 / IMU_HZ

    def log_point():
        lat, lon = to_ll(lat0, lon0, x, y)
        track.append([round(t, 1), round(lat, 7), round(lon, 7), round(z, 1), round(math.hypot(vx, vy), 1), phase])

    log_point()
    for _ in range(int(4 * 3600 / DT)):                       # предохранитель: не дольше 4 ч
        if phase == "takeoff":
            target_alt = wps[0][2]
            z = min(target_alt, z + CLIMB * DT)
            sample(0.15 if z < 1 else 0.0)
            if z >= target_alt - 0.05:
                phase = "mission"
        elif phase in ("mission", "rtl"):
            tx, ty, talt, hold = wps[idx]
            # наведение: точка-цель на отрезке маршрута впереди проекции (коррекция бокового отклонения)
            sx, sy = leg_start
            lx, ly = tx - sx, ty - sy
            L = math.hypot(lx, ly) or 1e-6
            ux, uy = lx / L, ly / L
            along = (x - sx) * ux + (y - sy) * uy
            xte = -(x - sx) * uy + (y - sy) * ux
            max_xte = max(max_xte, abs(xte))
            look = min(L, max(along, 0) + max(6.0, v_max * 1.5))
            cx, cy = sx + ux * look, sy + uy * look
            dx, dy = cx - x, cy - y
            dist_wp = math.hypot(tx - x, ty - y)
            d = math.hypot(dx, dy) or 1e-6
            v_cmd = min(v_max, math.sqrt(2 * ACCEL * max(dist_wp - accept_r * 0.5, 0)) + 0.6)
            # желаемая путевая скорость + компенсация ветра (автопилот держит линию пути)
            gvx, gvy = dx / d * v_cmd, dy / d * v_cmd
            ax_, ay_ = (gvx - vx) / DT, (gvy - vy) / DT
            a_mag = math.hypot(ax_, ay_)
            if a_mag > ACCEL:
                ax_, ay_ = ax_ * ACCEL / a_mag, ay_ * ACCEL / a_mag
            gust = (0.35 * math.sin(t * 0.7) + 0.2 * math.sin(t * 1.9)) * gust_k
            vx += ax_ * DT + (wx * 0.04 + gust * 0.05) * DT * 5
            vy += ay_ * DT + (wy * 0.04 - gust * 0.04) * DT * 5
            x += vx * DT
            y += vy * DT
            z += max(-CLIMB * DT, min(CLIMB * DT, talt - z))
            sample(math.hypot(ax_, ay_) / 9.81 * 0.6)
            if fault_at_wp and idx == fault_at_wp - 1 and dist_wp < 25:
                # столкновение с препятствием на подлёте: удар, кратковременная невесомость, падение
                for g in (6.8, 5.2, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 7.6, 1.0):
                    acc.append(g)
                    t += 1.0 / IMU_HZ
                imu_next = t
                z = 0.0
                vx = vy = 0.0
                phase, status = "crash", "aborted"
                events_log.append(f"Столкновение на подлёте к точке {fault_at_wp}: аварийное завершение миссии")
                log_point()
                break
            if dist_wp <= accept_r and abs(z - talt) < alt_tol:
                if hold > 0:
                    phase, hold_left = "hold", hold
                    vx = vy = 0.0
                else:
                    leg_start = (tx, ty)
                    idx += 1
                    if idx >= len(wps):
                        phase = "land"
                    elif m.get("return_home", True) and idx == len(wps) - 1:
                        phase = "rtl"
        elif phase == "hold":
            hold_left -= DT
            sample()
            if hold_left <= 0:
                leg_start = (wps[idx][0], wps[idx][1])
                idx += 1
                phase = "land" if idx >= len(wps) else ("rtl" if m.get("return_home", True) and idx == len(wps) - 1 else "mission")
        elif phase == "land":
            vx = vy = 0.0
            z = max(0.0, z - DESCENT * DT)
            sample(0.25 if z <= 0.05 else 0.0)
            if z <= 0.0:
                log_point()
                break
        t += DT
        if len(track) == 0 or t - track[-1][0] >= 1.0 - 1e-9:
            log_point()
    return {
        "status": status, "duration_s": round(t, 1), "track": track,
        "imu": {"rate_hz": IMU_HZ, "t0_s": 0, "accel_g": acc},
        "max_xte_m": round(max_xte, 2), "events": events_log,
        "distance_m": round(sum(math.dist(_xy(lat0, lon0, a), _xy(lat0, lon0, b)) for a, b in zip(track, track[1:])), 1),
        "max_alt_m": round(max(p[3] for p in track), 1), "wind": {"speed_ms": wind_ms, "from_deg": wind_from_deg},
    }


def _xy(lat0, lon0, p):
    return to_xy(lat0, lon0, p[1], p[2])


def flight_log(m: dict, sim: dict, aircraft_serial: str, end_at, run_tag: str) -> bytes:
    """Результат симуляции → журнал tbo-flightlog (для штатного импорта)."""
    start = end_at - timedelta(seconds=sim["duration_s"])
    from ..timeutil import iso
    doc = {"format": "tbo-flightlog/1.0", "aircraft_serial": aircraft_serial, "source": "Автопилот tbo-tracker (симулятор миссий)",
           "flights": [{"flight_id": f"{aircraft_serial}-{run_tag}", "start": iso(start), "end": iso(end_at), "cycles": 1,
                        "mission": f"Миссия «{m['name']}» (автономный полёт)", "pilot": "автопилот", "imu": sim["imu"]}]}
    return json.dumps(doc, ensure_ascii=False).encode()


# ------------------------------------------------------------------ форматы QGroundControl
def to_plan(m: dict, firmware: str = "ardupilot") -> dict:
    items, jid = [], 1

    def item(cmd, lat, lon, alt, p1=0):
        nonlocal jid
        it = {"AMSLAltAboveTerrain": None, "Altitude": alt, "AltitudeMode": 1, "autoContinue": True, "command": cmd,
              "doJumpId": jid, "frame": FRAME_REL_ALT, "params": [p1, 0, 0, None, lat, lon, alt], "type": "SimpleItem"}
        jid += 1
        return it
    first = m["waypoints"][0]
    # высота взлёта миссии (а не первой точки): та же, что у симулятора и плана для борта
    items.append(item(CMD_TAKEOFF, m["home_lat"], m["home_lon"], float(m.get("takeoff_alt") or first["alt"])))
    for w in m["waypoints"]:
        items.append(item(CMD_WAYPOINT, float(w["lat"]), float(w["lon"]), float(w["alt"]), float(w.get("hold_s") or 0)))
    if m.get("return_home", True):
        it = item(CMD_RTL, 0, 0, 0)
        it["params"] = [0, 0, 0, 0, 0, 0, 0]
        items.append(it)
    return {"fileType": "Plan", "groundStation": "QGroundControl", "version": 1,
            "geoFence": {"circles": [], "polygons": [], "version": 2}, "rallyPoints": {"points": [], "version": 2},
            "mission": {"cruiseSpeed": m["speed_ms"], "hoverSpeed": m["speed_ms"], "firmwareType": 12 if firmware == "px4" else 3,
                        "globalPlanAltitudeMode": 1, "vehicleType": 2, "version": 2, "items": items,
                        "plannedHomePosition": [m["home_lat"], m["home_lon"], 0]}}


def to_waypoints(m: dict) -> str:
    lines = ["QGC WPL 110", f"0\t1\t0\t{CMD_WAYPOINT}\t0\t0\t0\t0\t{m['home_lat']:.7f}\t{m['home_lon']:.7f}\t0.000000\t1"]
    n = 1
    takeoff = float(m.get("takeoff_alt") or m["waypoints"][0]["alt"])
    lines.append(f"{n}\t0\t{FRAME_REL_ALT}\t{CMD_TAKEOFF}\t0\t0\t0\t0\t0.0000000\t0.0000000\t{takeoff:.6f}\t1")
    for w in m["waypoints"]:
        n += 1
        lines.append(f"{n}\t0\t{FRAME_REL_ALT}\t{CMD_WAYPOINT}\t{float(w.get('hold_s') or 0):g}\t0\t0\t0\t"
                     f"{float(w['lat']):.7f}\t{float(w['lon']):.7f}\t{float(w['alt']):.6f}\t1")
    if m.get("return_home", True):
        n += 1
        lines.append(f"{n}\t0\t{FRAME_REL_ALT}\t{CMD_RTL}\t0\t0\t0\t0\t0\t0\t0\t1")
    return "\n".join(lines) + "\n"


def parse_mission_file(content: bytes, filename: str) -> dict:
    """QGroundControl .plan или .waypoints (QGC WPL 110) → поля миссии."""
    try:
        return _parse_mission_file(content, filename)
    except DomainError:
        raise
    except (TypeError, ValueError, IndexError, AttributeError, KeyError, OverflowError) as e:
        raise DomainError(f"Файл миссии повреждён или заполнен неверно: {e}") from e


def _parse_mission_file(content: bytes, filename: str) -> dict:
    text = content.decode("utf-8-sig", errors="replace").strip()
    wps, home, rtl, speed, takeoff, home_amsl = [], None, False, None, None, 0.0

    def rel(alt, frame) -> float:
        """Высота над точкой взлёта: точки в кадре MAV_FRAME_GLOBAL (над уровнем моря) пересчитываются."""
        return float(alt) - home_amsl if frame == FRAME_GLOBAL else float(alt)
    if text.startswith("{"):
        try:
            plan = json.loads(text)
            mi = plan["mission"]
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            raise DomainError("Не похоже на файл QGroundControl .plan") from e
        if not isinstance(mi, dict) or not isinstance(mi.get("items", []), list):
            raise DomainError("Не похоже на файл QGroundControl .plan")
        hp = mi.get("plannedHomePosition") or [None, None]
        if not isinstance(hp, list) or len(hp) < 2:
            hp = [None, None]
        home = (hp[0], hp[1])
        home_amsl = float(hp[2]) if len(hp) > 2 and hp[2] is not None else 0.0
        speed = mi.get("cruiseSpeed")
        for it in mi.get("items", []):
            if it.get("type") != "SimpleItem":
                continue
            cmd, p, frame = it.get("command"), it.get("params") or [], it.get("frame", FRAME_REL_ALT)
            if cmd == CMD_WAYPOINT and len(p) >= 7:
                wps.append({"lat": float(p[4]), "lon": float(p[5]), "alt": rel(p[6], frame), "hold_s": float(p[0] or 0)})
            elif cmd == CMD_TAKEOFF and len(p) >= 7 and p[6] is not None:
                takeoff = rel(p[6], frame)
            elif cmd == CMD_RTL:
                rtl = True
    elif text.startswith("QGC WPL"):
        for i, line in enumerate(text.splitlines()[1:]):
            c = line.split("\t") if "\t" in line else line.split()
            if len(c) < 12:
                continue
            frame, cmd, p1 = int(float(c[2])), int(float(c[3])), float(c[4])
            lat, lon, alt = float(c[8]), float(c[9]), float(c[10])
            if i == 0:
                home, home_amsl = (lat, lon), alt          # строка 0 — «дом», высота над уровнем моря
            elif cmd == CMD_WAYPOINT:
                wps.append({"lat": lat, "lon": lon, "alt": rel(alt, frame), "hold_s": p1})
            elif cmd == CMD_TAKEOFF:
                takeoff = rel(alt, frame)
            elif cmd == CMD_RTL:
                rtl = True
    else:
        raise DomainError("Поддерживаются файлы QGroundControl .plan и .waypoints (QGC WPL 110)")
    if not wps:
        raise DomainError("В файле нет точек маршрута (команда MAV_CMD_NAV_WAYPOINT)")
    if not home or home[0] is None:
        home = (wps[0]["lat"], wps[0]["lon"])
    name = filename.rsplit(".", 1)[0] or "Миссия из файла"
    return {"name": name[:200], "home_lat": float(home[0]), "home_lon": float(home[1]), "return_home": rtl,
            "speed_ms": float(speed or 8.0), "takeoff_alt": float(takeoff if takeoff else wps[0]["alt"]), "waypoints": wps}
