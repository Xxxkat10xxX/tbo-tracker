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

import logging
import math
import threading
import time

from .plan import R_EARTH, dist_m, to_xy
from .vehicle import VehicleError

log = logging.getLogger("tbo_agent")

NAN = float("nan")
ARRIVE_DWELL = 3        # отсчётов подряд (через ~0,2 с) в допуске — точка достигнута
SETTLE_S = 3.0          # держится не дальше 2× допуска столько секунд — тоже достигнута (шум оптического потока)
TARGET_LAG_S = 1.0      # столько ждать признака новой цели в кадре navigate_target (TF может отставать от команды)
TEL_TIMEOUT_S = 5.0     # один запрос get_telemetry (на загруженном Orange Pi бывает > 3 с)
TEL_LOST_S = 15.0       # без единого ответа телеметрии столько секунд — сбой (одна задержка — не повод садиться)


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

    def _tel(self, frame: str, tries: int = 3):
        """get_telemetry с повтором: на загруженном борту ответ бывает дольше 3 с, и одна задержка прежде
        обрывала программу полёта (посадка на месте)."""
        err = None
        for _ in range(max(1, tries)):
            try:
                return self._call(self._get_telemetry, TEL_TIMEOUT_S, "телеметрия", frame_id=frame)
            except VehicleError as e:
                err = e
        raise err

    def _nav(self, srv, timeout: float, what: str, **kw):
        """navigate / navigate_global: отказ simple_offboard (success=False) — ошибка сразу, а не тайм-аут ожидания."""
        r = self._call(srv, timeout, what, **kw)
        if getattr(r, "success", True) is False:
            raise VehicleError("%s: %s" % (what, getattr(r, "message", "") or "отказ simple_offboard"))
        return r

    def _guard(self, ev):
        if ev.is_set():
            raise Stop()
        if self._s.get("mode") not in (None, "OFFBOARD"):
            raise Stop("пилот перехватил управление (режим %s)" % self._s.get("mode"))

    def _dist_navigate_target(self):
        """(по горизонтали, по вертикали) от борта до цели navigate по кадру navigate_target, м; None — нет данных."""
        t = self._tel("navigate_target")
        x, y, z = _fin(t.x), _fin(t.y), _fin(t.z)
        return None if None in (x, y, z) else (math.hypot(x, y), abs(z))

    def _dist_map(self, x: float, y: float, z: float):
        """Расстояние до точки кадра map, вычисленной самой программой: не зависит от того, успел ли simple_offboard
        обновить кадр navigate_target после новой команды."""
        def dist():
            t = self._tel("map")
            px, py, pz = _fin(t.x), _fin(t.y), _fin(t.z)
            return None if None in (px, py, pz) else (math.hypot(px - x, py - y), abs(pz - z))
        return dist

    def _wait_arrival(self, ev, timeout: float, tol: float, dist=None, expect: float = None):
        """Ждать прибытия к цели navigate.

        * Точка достигнута, если борт в допуске ARRIVE_DWELL отсчётов подряд (а не на одном отсчёте: шум, пролёт мимо)
          или держится не дальше 2× допуска SETTLE_S секунд — у близкой точки оптический поток «плавает» сильнее
          допуска, и борт иначе висел бы до тайм-аута, а затем садился, не долетев маршрут.
        * expect — расстояние до новой цели в момент команды. Кадр navigate_target может ещё показывать прежнюю цель
          (там, где борт уже стоит): пока не видно, что цель сменилась (или не прошло TARGET_LAG_S), «прибытие» не
          засчитывается — иначе после точки у старта следующая «достигалась» мгновенно, и борт сразу садился.
        """
        dist = dist or self._dist_navigate_target
        start = time.monotonic()
        end = start + timeout
        fresh = expect is None or expect <= 2 * tol
        run, near_since, d, last_ok, tel_err = 0, None, None, start, None
        while True:
            self._guard(ev)
            try:
                d = dist()
                last_ok = time.monotonic()
            except VehicleError as e:           # задержка телеметрии — пропуск отсчёта, а не обрыв маршрута
                d, tel_err = None, e
                if time.monotonic() - last_ok > TEL_LOST_S:
                    raise VehicleError("нет телеметрии %.0f с: %s" % (TEL_LOST_S, tel_err))
            now = time.monotonic()
            if d is not None:
                h, v = d
                if not fresh and (h > 2 * tol or v > 2 * tol or now - start >= TARGET_LAG_S):
                    fresh = True
                run = run + 1 if fresh and h <= tol and v <= tol else 0
                if fresh and h <= 2 * tol and v <= 2 * tol:
                    near_since = now if near_since is None else near_since
                else:
                    near_since = None
                if run >= ARRIVE_DWELL or (near_since is not None and now - near_since >= SETTLE_S):
                    return
            if now > end:
                left = "" if d is None else " (до цели %.2f м)" % math.hypot(*d)
                raise VehicleError("точка не достигнута за %.0f с%s" % (timeout, left))
            time.sleep(0.2)

    def _sleep(self, ev, seconds: float):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self._guard(ev)
            ev.wait(0.2)

    def _takeoff(self, ev, alt: float):
        # цель взлёта — в кадре map (быстрый запрос); navigate_target на загруженном борту отвечает до секунд
        target = None
        try:
            p = self._tel("map")
            xyz = (_fin(p.x), _fin(p.y), _fin(p.z))
            if None not in xyz:
                target = (xyz[0], xyz[1], xyz[2] + float(alt))
        except VehicleError:
            pass
        self._nav(self._navigate, 5, "взлёт (navigate)", x=0.0, y=0.0, z=float(alt), yaw=NAN,
                  speed=self.takeoff_speed, frame_id="body", auto_arm=True)
        end = time.monotonic() + 8                 # simple_offboard включает OFFBOARD и арминг
        while not (self._s.get("mode") == "OFFBOARD" and self._s.get("armed")):
            if ev.is_set():
                raise Stop()
            if time.monotonic() > end:
                raise VehicleError("контроллер не перешёл в OFFBOARD с включёнными моторами%s" % self._why())
            time.sleep(0.1)
        tol, timeout = (0.3 if alt < 5 else 0.5), float(alt) / self.takeoff_speed * 2 + 30
        if target is not None:
            self._wait_arrival(ev, timeout, tol, dist=self._dist_map(*target))
        else:
            self._wait_arrival(ev, timeout, tol, expect=float(alt))

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
                self._nav(self._navigate_global, 5, "полёт к точке %d (navigate_global)" % i, lat=float(w["lat"]),
                          lon=float(w["lon"]), z=z0 + float(w["alt"]), yaw=NAN, speed=speed, frame_id="map", auto_arm=False)
                self._wait_arrival(ev, d / speed * 2 + 60, self.tol, expect=d)
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
            log.error("программа полёта прервана, возврат домой: %s", e)
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
        z = lc["z0"] + float(alt)
        cur = self._tel("map")
        cx, cy, cz = _fin(cur.x), _fin(cur.y), _fin(cur.z)
        d = 0.0 if None in (cx, cy, cz) else math.hypot(math.hypot(x - cx, y - cy), z - cz)
        self._nav(self._navigate, 5, what, x=x, y=y, z=z, yaw=NAN, speed=speed, frame_id="map", auto_arm=False)
        lc["alt"] = float(alt)
        # прибытие — по своей цели в кадре map, а не по navigate_target (он может ещё показывать прежнюю цель)
        self._wait_arrival(ev, d / max(speed, 0.1) * 2 + 20, self.tol_local, dist=self._dist_map(x, y, z))

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
            log.info("программа полёта остановлена: %s", self._fly_error or "команда оператора")
        except Exception as e:  # noqa: BLE001 — сбой в помещении: безопаснее сесть на месте, чем лететь «домой»
            self._fly_error = str(e)
            log.error("программа полёта прервана, посадка: %s", e)
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
            log.info("программа полёта остановлена: %s", self._fly_error or "команда оператора")
        except Exception as e:  # noqa: BLE001
            self._fly_error = str(e)
            log.error("программа полёта прервана, посадка: %s", e)
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
