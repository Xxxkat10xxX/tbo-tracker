"""Программа полёта на борту через simple_offboard Technic (режим OFFBOARD), как в документации Skyris
(docs.skyris.ru/technic6S → «Программирование автономного полёта», «Режим автономного полёта»).

* по GPS: navigate (взлёт, auto_arm) → navigate_global по точкам (z — в кадре map) → AUTO.RTL или land;
* локально (в помещении, Optical Flow / ArUco, без GPS): точки плана — метры от точки взлёта; «вверх» на карте
  tbo-tracker — вперёд по носу дрона в момент взлёта. Полёт navigate(..., frame_id='map'), возврат к точке взлёта
  и land(). AUTO.RTL без GPS недоступен, поэтому возврат выполняет сама программа.

Перехват управления пилотом (режим на пульте ≠ OFFBOARD, например Stab по документации Skyris) немедленно
останавливает программу без каких-либо переключений режимов.
"""
from __future__ import annotations

import math
import threading
import time

from .plan import R_EARTH, dist_m, to_xy
from .vehicle import VehicleError

NAN = float("nan")


class Stop(Exception):
    """Прерывание программы: команда оператора или перехват управления пилотом."""


# ---------------------------------------------------------------- локальные координаты (чистые функции)
def plan_offsets(plan: dict, w: dict):
    """Смещение точки плана от точки взлёта миссии: (вперёд, влево), м. Север на карте = вперёд, восток = вправо."""
    east, north = to_xy(plan["home"]["lat"], plan["home"]["lon"], float(w["lat"]), float(w["lon"]))
    return north, -east


def body_to_map(x0: float, y0: float, yaw0: float, fwd: float, left: float):
    """Точка (вперёд, влево) относительно взлёта → кадр map (ENU, как у MAVROS/simple_offboard)."""
    return x0 + fwd * math.cos(yaw0) - left * math.sin(yaw0), y0 + fwd * math.sin(yaw0) + left * math.cos(yaw0)


def map_to_body(x0: float, y0: float, yaw0: float, x: float, y: float):
    dx, dy = x - x0, y - y0
    return dx * math.cos(yaw0) + dy * math.sin(yaw0), -dx * math.sin(yaw0) + dy * math.cos(yaw0)


def body_to_ll(lat0: float, lon0: float, fwd: float, left: float):
    """Условные широта/долгота для карты tbo-tracker: вокруг точки взлёта миссии."""
    north, east = fwd, -left
    return (lat0 + math.degrees(north / R_EARTH), lon0 + math.degrees(east / (R_EARTH * math.cos(math.radians(lat0)))))


def _fin(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


class OffboardMixin:
    """Часть RosVehicle: программы полёта simple_offboard. Требует self._s, self._lock, сервисы Technic, _set_mode."""

    def _program_alive(self) -> bool:
        return bool(self._fly_thread and self._fly_thread.is_alive())

    def _launch(self, target, plan: dict):
        """Новый поток программы со СВОИМ флагом остановки: прежняя программа, даже если ещё не завершилась
        (ждёт ответа сервиса), остаётся остановленной и не продолжит маршрут параллельно с новой."""
        ev = threading.Event()
        self._stop_fly = ev
        self._fly_thread = threading.Thread(target=target, args=(plan, ev), name="offboard", daemon=True)
        self._fly_thread.start()

    def _start_program(self, target, plan: dict) -> str:
        if self._program_alive():
            raise VehicleError("программа полёта уже выполняется")
        self._fly_error, self._phase = None, "takeoff"
        self._launch(target, plan)
        end = time.monotonic() + 12
        while time.monotonic() < end:
            if self._s.get("armed") and self._s.get("mode") == "OFFBOARD":
                return "моторы включены, режим OFFBOARD — программа полёта на борту"
            if self._fly_error:
                break
            time.sleep(0.1)
        self._halt_program(release=True)
        raise VehicleError("моторы не включились: %s%s" % (self._fly_error or "нет ответа", self._why()))

    def _halt_program(self, release: bool):
        """Остановить программу. release=True — прекратить setpoint (перед переключением PX4 в AUTO-режим);
        без release текущая цель остаётся и PX4 не уходит в failsafe потери OFFBOARD."""
        self._stop_fly.set()
        if self._program_alive():
            self._fly_thread.join(3)
        if release and self.technic:
            try:
                self._call(self._release, 3, "остановка setpoint")
            except VehicleError:
                pass

    def _tel(self, frame: str):
        return self._call(self._get_telemetry, 3, "телеметрия", frame_id=frame)

    def _guard(self, ev):
        if ev.is_set():
            raise Stop()
        if self._s.get("mode") not in (None, "OFFBOARD"):
            raise Stop("пилот перехватил управление (режим %s)" % self._s.get("mode"))

    def _wait_arrival(self, ev, timeout: float, tol: float):
        end = time.monotonic() + timeout
        while True:
            self._guard(ev)
            t = self._tel("navigate_target")
            xyz = [_fin(t.x), _fin(t.y), _fin(t.z)]
            if None not in xyz and math.sqrt(sum(v * v for v in xyz)) < tol:
                return
            if time.monotonic() > end:
                raise VehicleError("точка не достигнута за %.0f с" % timeout)
            time.sleep(0.2)

    def _sleep(self, ev, seconds: float):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self._guard(ev)
            ev.wait(0.2)

    def _takeoff(self, ev, alt: float):
        self._call(self._navigate, 5, "взлёт (navigate)", x=0.0, y=0.0, z=float(alt), yaw=NAN,
                   speed=self.takeoff_speed, frame_id="body", auto_arm=True)
        end = time.monotonic() + 8                 # simple_offboard включает OFFBOARD и арминг
        while not (self._s.get("mode") == "OFFBOARD" and self._s.get("armed")):
            if ev.is_set():
                raise Stop()
            if time.monotonic() > end:
                raise VehicleError("контроллер не перешёл в OFFBOARD с включёнными моторами%s" % self._why())
            time.sleep(0.1)
        self._wait_arrival(ev, float(alt) / self.takeoff_speed * 2 + 30, 0.3 if alt < 5 else 0.5)

    def _reached(self, i: int):
        with self._lock:
            self._wp_reached = i

    def _hold(self, ev, w: dict):
        if w.get("hold_s"):
            self._phase = "hold"
            self._sleep(ev, float(w["hold_s"]))
            self._phase = "mission"

    # ---------------------------------------------------------------- по GPS
    def _fly(self, plan: dict, ev):
        try:
            z0 = _fin(self._tel("map").z) or 0.0
            self._takeoff(ev, plan["takeoff_alt"])
            self._phase = "mission"
            lat0, lon0 = self._s.get("lat") or plan["home"]["lat"], self._s.get("lon") or plan["home"]["lon"]
            speed = float(plan["speed_ms"])
            for i, w in enumerate(plan["waypoints"], 1):
                d = dist_m(lat0, lon0, w["lat"], w["lon"])
                self._call(self._navigate_global, 5, "полёт к точке %d (navigate_global)" % i, lat=float(w["lat"]),
                           lon=float(w["lon"]), z=z0 + float(w["alt"]), yaw=NAN, speed=speed, frame_id="map", auto_arm=False)
                self._wait_arrival(ev, d / speed * 2 + 60, self.tol)
                self._reached(i)
                lat0, lon0 = w["lat"], w["lon"]
                self._hold(ev, w)
            if plan.get("return_home", True):
                self._call(self._release, 3, "остановка setpoint")
                self._set_mode("AUTO.RTL")
                self._phase = "rtl"
            else:
                self._call(self._land_srv, 5, "посадка (land)")
                self._phase = "land"
        except Stop as e:
            self._fly_error = str(e) or None            # команда оператора или пульт: режимы не трогаем
        except Exception as e:  # noqa: BLE001 — сбой программы в воздухе → безопасный возврат домой
            self._fly_error = str(e)
            if self._s.get("armed") and self._s.get("mode") == "OFFBOARD":
                try:
                    self._call(self._release, 3, "остановка setpoint")
                    self._set_mode("AUTO.RTL")
                    self._phase = "rtl"
                except VehicleError:
                    pass

    # ---------------------------------------------------------------- локально (без GPS)
    def _anchor_local(self, plan: dict):
        """Точка взлёта в кадре map: x0, y0, z0, курс. От неё отсчитываются точки плана."""
        t = self._tel("map")
        vals = [_fin(t.x), _fin(t.y), _fin(t.z), _fin(getattr(t, "yaw", 0.0))]
        if None in vals:
            raise VehicleError("нет локальной позиции (кадр map) — проверьте Optical Flow / ArUco")
        x0, y0, z0, yaw0 = vals
        self._local = {"x0": x0, "y0": y0, "z0": z0, "yaw0": yaw0,
                       "lat0": float(plan["home"]["lat"]), "lon0": float(plan["home"]["lon"]), "alt": float(plan["takeoff_alt"])}

    def _goto_local(self, ev, fwd: float, left: float, alt: float, speed: float, what: str):
        lc = self._local
        x, y = body_to_map(lc["x0"], lc["y0"], lc["yaw0"], fwd, left)
        cur = self._tel("map")
        d = math.hypot(x - (_fin(cur.x) or x), y - (_fin(cur.y) or y))
        self._call(self._navigate, 5, what, x=x, y=y, z=lc["z0"] + float(alt), yaw=NAN, speed=speed, frame_id="map",
                   auto_arm=False)
        lc["alt"] = float(alt)
        self._wait_arrival(ev, d / max(speed, 0.1) * 2 + 20, self.tol_local)

    def _fly_local(self, plan: dict, ev):
        try:
            self._anchor_local(plan)
            self._takeoff(ev, plan["takeoff_alt"])
            self._phase = "mission"
            speed = float(plan["speed_ms"])
            for i, w in enumerate(plan["waypoints"], 1):
                fwd, left = plan_offsets(plan, w)
                self._goto_local(ev, fwd, left, w["alt"], speed, "полёт к точке %d (navigate, кадр map)" % i)
                self._reached(i)
                self._hold(ev, w)
            if plan.get("return_home", True):
                self._phase = "rtl"
                self._goto_local(ev, 0.0, 0.0, self._local["alt"], speed, "возврат к точке взлёта (navigate)")
            self._call(self._land_srv, 5, "посадка (land)")
            self._phase = "land"
        except Stop as e:
            self._fly_error = str(e) or None
        except Exception as e:  # noqa: BLE001 — сбой в помещении: безопаснее сесть на месте, чем лететь «домой»
            self._fly_error = str(e)
            if self._s.get("armed") and self._s.get("mode") == "OFFBOARD":
                try:
                    self._call(self._land_srv, 5, "посадка (land)")
                    self._phase = "land"
                except VehicleError:
                    pass

    def _return_local(self, plan: dict, ev):
        """Команда «Вернуть домой» в локальном полёте: к точке взлёта на текущей высоте, затем посадка."""
        try:
            self._phase = "rtl"
            self._goto_local(ev, 0.0, 0.0, self._local["alt"], max(0.3, float(plan["speed_ms"])), "возврат к точке взлёта")
            self._call(self._land_srv, 5, "посадка (land)")
            self._phase = "land"
        except Stop as e:
            self._fly_error = str(e) or None
        except Exception as e:  # noqa: BLE001
            self._fly_error = str(e)
            try:
                self._call(self._land_srv, 5, "посадка (land)")
                self._phase = "land"
            except VehicleError:
                pass

    def local_position(self):
        """Положение относительно точки взлёта для карты: (широта, долгота, высота) или None."""
        lc, pose = self._local, self._s.get("pose")
        if not lc or not pose or time.monotonic() - pose[4] > 1.5:
            return None
        fwd, left = map_to_body(lc["x0"], lc["y0"], lc["yaw0"], pose[0], pose[1])
        lat, lon = body_to_ll(lc["lat0"], lc["lon0"], fwd, left)
        return lat, lon, pose[2] - lc["z0"]
