"""Симулятор борта без ROS: проверка агента и всей цепочки tbo-tracker без дрона (python3 -m tbo_agent --sim).

Последовательность как у PX4 в AUTO.MISSION: арминг → взлёт 2,5 м/с до высоты → полёт к точкам с крейсерской
скоростью → удержание → возврат домой (если в плане; PX4 набирает 5 м, локальная миссия — на текущей высоте,
как программа на борту) → посадка 1,5 м/с → выключение моторов через 2 с.
Команды AUTO.RTL и AUTO.LAND прерывают маршрут. Батарея расходуется, акселерометр — 1 g с вибрацией,
касание — 1,4 g (touchdown_g); fault="collision@N" — удар 5,5 g на подлёте к точке N (проверка анализа датчиков на сервере).
Курс (heading, от севера по часовой — он же «верх» карты миссии): yaw_mode=route — нос к цели отрезка, keep — не
меняется; в точке с yaw_deg борт разворачивается к нему (90°/с), в режиме keep этот курс сохраняется дальше.
Время модели ускоряется (time_scale): пятиминутный полёт при ×50 — около 6 с.
"""
from __future__ import annotations

import math
import random
import threading
import time

from .plan import R_EARTH, frame_of, plan_hash, to_xy, yaw_desc, yaw_mode
from .vehicle import Vehicle, VehicleError

CLIMB, DESCENT, VZ = 2.5, 1.5, 2.0
YAW_RATE = 90.0                 # °/с


def _bearing(dx: float, dy: float) -> float:
    """Направление (восток dx, север dy) → курс от севера по часовой, °."""
    return math.degrees(math.atan2(dx, dy)) % 360.0


class SimVehicle(Vehicle):
    executor = "sim"
    DT = 0.05

    def __init__(self, home=None, time_scale: float = 1.0, battery_pct: float = 96.0, fault: str = None, sats: int = 14,
                 touchdown_g: float = 1.4):
        self._lock = threading.Lock()
        self._scale = max(0.1, float(time_scale))
        self._t = 0.0
        self._origin = tuple(home) if home else None      # (lat, lon) — точка «дом» и начало координат модели
        self.x = self.y = self.z = 0.0
        self.vx = self.vy = self.vz = 0.0
        self.armed, self.in_air, self.mode = False, False, "AUTO.LOITER"
        self.battery, self.sats = float(battery_pct), sats
        self.items, self.idx, self.hold_left, self.wp_reached = [], 0, None, 0
        self.hdg, self._route, self._carry = 0.0, False, None     # курс; режим route; курс, который держать (keep)
        self._plan_hash, self._speed = None, 5.0
        self._rtl_alt = 5.0                               # RTL_RETURN_ALT; в помещении — 0 (текущая высота)
        self._fault_wp = int(fault.split("@")[1]) if fault and fault.startswith("collision@") else None
        self._fault_done = False
        self._imu, self._imu_next = [], 0.0
        self._touch = False
        self._touchdown_g = float(touchdown_g)             # перегрузка при касании земли
        self._disarm_at = None
        self._status = None
        self._stop = threading.Event()
        self._thread = None
        self._rnd = random.Random(7)

    # ---------------------------------------------------------------- интерфейс
    def open(self):
        self._thread = threading.Thread(target=self._loop, name="sim", daemon=True)
        self._thread.start()

    def close(self):
        self._stop.set()

    def info(self) -> dict:
        return {"autopilot": "симулятор tbo-agent", "time_scale": self._scale}

    def now(self) -> float:
        with self._lock:
            return self._t

    def time_scale(self) -> float:
        return self._scale

    def snapshot(self) -> dict:
        with self._lock:
            if self._origin is None:
                lat = lon = None
            else:
                lat = self._origin[0] + math.degrees(self.y / R_EARTH)
                lon = self._origin[1] + math.degrees(self.x / (R_EARTH * math.cos(math.radians(self._origin[0]))))
            spd = math.hypot(self.vx, self.vy)
            n_wp = sum(1 for it in self.items if it[0] == "wp")
            return {"connected": True, "armed": self.armed, "mode": self.mode, "in_air": self.in_air, "lat": lat, "lon": lon,
                    "alt_rel": round(self.z, 2), "speed": round(spd, 2), "climb": round(self.vz, 2),
                    "heading": round(self.hdg % 360, 1),
                    "battery_pct": round(self.battery, 1), "battery_v": round(6 * (3.5 + 0.7 * self.battery / 100), 2),
                    "gps_fix": 3, "sats": self.sats, "home": self._origin, "local_ok": True, "wp_total": n_wp,
                    "wp_current": min(n_wp, self.wp_reached + 1) if self.armed else 0, "wp_reached": self.wp_reached,
                    "status_text": self._status}

    def drain_imu(self) -> list:
        with self._lock:
            out, self._imu = self._imu, []
        return out

    def place_for(self, plan: dict):
        with self._lock:
            if self._origin is None and not self.armed:   # симулятор «ставят» в точку взлёта миссии
                self._origin = (float(plan["home"]["lat"]), float(plan["home"]["lon"]))

    def upload(self, plan: dict, position: dict) -> str:
        self.place_for(plan)
        with self._lock:
            if self.armed:
                raise VehicleError("борт заармлен — план не загружается")
            lat0, lon0 = self._origin
            items = [("takeoff", float(plan["takeoff_alt"]))]
            for w in plan["waypoints"]:
                x, y = to_xy(lat0, lon0, float(w["lat"]), float(w["lon"]))
                yaw = None if w.get("yaw_deg") is None else float(w["yaw_deg"]) % 360.0
                items.append(("wp", x, y, float(w["alt"]), float(w.get("hold_s") or 0), yaw))
            items.append(("rtl",) if plan.get("return_home", True) else ("land",))
            self.items, self.idx, self.wp_reached = items, 0, 0
            self._route, self._carry = yaw_mode(plan) == "route", None
            self._plan_hash, self._speed = plan_hash(plan), float(plan["speed_ms"])
            self._rtl_alt = 0.0 if frame_of(plan) == "local" else 5.0
        return "миссия загружена в симулятор: %d элементов" % len(items) + yaw_desc(plan)

    def verify(self, plan: dict):
        if self._plan_hash != plan_hash(plan):
            raise VehicleError("план в симуляторе не совпадает с подготовленным")

    def clear(self):
        with self._lock:
            if not self.armed:
                self.items, self._plan_hash = [], None

    def start_mission(self, plan: dict) -> str:
        with self._lock:
            if not self.items:
                raise VehicleError("в симуляторе нет плана")
            if self.armed:
                raise VehicleError("борт уже заармлен")
            self.armed, self.mode, self.idx, self.hold_left = True, "AUTO.MISSION", 0, None
            self._status, self._disarm_at, self._touch = "Armed by external command", None, False
        return "моторы включены, режим AUTO.MISSION (симулятор)"

    def rtl(self) -> str:
        return self._mode("AUTO.RTL")

    def land(self) -> str:
        return self._mode("AUTO.LAND")

    def _mode(self, m: str) -> str:
        with self._lock:
            if not self.armed:
                raise VehicleError("борт не в полёте")
            self.mode = m
        return "режим %s" % m

    # ---------------------------------------------------------------- модель
    def _loop(self):
        while not self._stop.is_set():
            with self._lock:
                self._step(self.DT)
            time.sleep(self.DT / self._scale)

    def _yaw_to(self, want, dt):
        if want is not None:
            err = (want - self.hdg + 180.0) % 360.0 - 180.0
            self.hdg = (self.hdg + max(-YAW_RATE * dt, min(YAW_RATE * dt, err))) % 360.0

    def _leg_yaw(self, tx, ty, d, dt):
        """Курс на отрезке: route — нос к цели (у самой цели — без изменений), keep — прежний."""
        self._yaw_to(_bearing(tx - self.x, ty - self.y) if self._route and d > 1.0 else self._carry, dt)

    def _go(self, tx, ty, tz, speed, dt) -> float:
        dx, dy = tx - self.x, ty - self.y
        d = math.hypot(dx, dy)
        v = min(speed, d / dt) if d > 1e-6 else 0.0
        self.vx, self.vy = (dx / d * v, dy / d * v) if d > 1e-6 else (0.0, 0.0)
        self.vz = max(-VZ, min(VZ, (tz - self.z) / dt))
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.z += self.vz * dt
        return d

    def _step(self, dt: float):
        self._t += dt
        extra = 0.0
        if self.armed:
            self.battery = max(0.0, self.battery - (0.05 if self.in_air else 0.002) * dt)
            if self.mode == "AUTO.MISSION" and self.idx < len(self.items):
                it = self.items[self.idx]
                if it[0] == "takeoff":
                    self.vx = self.vy = 0.0
                    self.vz = CLIMB
                    self.z = min(it[1], self.z + CLIMB * dt)
                    if self.z >= it[1] - 0.05:
                        self.idx += 1
                elif it[0] == "wp":
                    d = self._go(it[1], it[2], it[3], self._speed, dt)
                    n = sum(1 for k in self.items[:self.idx + 1] if k[0] == "wp")
                    if self._fault_wp == n and not self._fault_done and d < 15:
                        self._fault_done, extra = True, 4.5
                        self._status = "Collision suspected (simulated fault)"
                    at_point = d < 1.0 and abs(self.z - it[3]) < 0.5
                    if at_point and it[5] is not None:            # курс, заданный в точке
                        self._yaw_to(it[5], dt)
                        self._carry = self._carry if self._route else it[5]
                    else:
                        self._leg_yaw(it[1], it[2], d, dt)
                    if at_point:
                        self.hold_left = it[4] if self.hold_left is None else self.hold_left - dt
                        if self.hold_left <= 0:
                            self.hold_left, self.wp_reached = None, n
                            self.idx += 1
                elif it[0] == "rtl":
                    self.mode = "AUTO.RTL"
                else:
                    self.mode = "AUTO.LAND"
            if self.mode == "AUTO.RTL":
                d = self._go(0.0, 0.0, max(self.z, self._rtl_alt), self._speed, dt)
                self._leg_yaw(0.0, 0.0, d, dt)
                if d < 1.0:
                    self.mode = "AUTO.LAND"
            if self.mode == "AUTO.LAND":
                self.vx = self.vy = 0.0
                self.vz = -DESCENT if self.z > 0 else 0.0
                self.z = max(0.0, self.z - DESCENT * dt)
            if self.z > 0.3:
                self.in_air = True
            if self.in_air and self.z <= 0.0:
                self.in_air, self.vz = False, 0.0
                self._touch, extra = True, max(extra, self._touchdown_g - 1.0)
                self._disarm_at = self._t + 2.0
                self._status = "Landing detected"
            if self._disarm_at is not None and self._t >= self._disarm_at:
                self.armed, self.mode, self._disarm_at = False, "AUTO.LOITER", None
                self.vx = self.vy = self.vz = 0.0
                self._status = "Disarmed by landing"
        while self._imu_next <= self._t:
            vib = (0.04 * math.sin(self._imu_next * 37.0) + 0.02 * self._rnd.uniform(-1, 1)) if self.armed else 0.0
            self._imu.append((self._imu_next, round(1.0 + vib + extra, 3)))
            extra = 0.0
            self._imu_next += 1.0 / 25
        if len(self._imu) > 50000:
            del self._imu[:-50000]
