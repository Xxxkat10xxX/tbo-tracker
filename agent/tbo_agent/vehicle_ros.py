"""Борт на ROS Noetic: MAVROS (PX4) и simple_offboard платформы Technic (Skyris Technic 6S; основа — COEX Clover).

Телеметрия — топики MAVROS. Исполнители плана (agent.conf → [vehicle] executor):

* mission  — план загружается в PX4 как миссия (mavros/mission/push) и сверяется обратным чтением; старт — режим
             AUTO.MISSION и арминг (как «Start Mission» в QGroundControl). Полёт ведёт PX4 и он виден в QGroundControl;
             при сбое бортового компьютера миссия продолжается.
* offboard — полёт программой на борту по образцу Technic (docs.skyris.ru → «Программирование автономного полёта»):
             navigate (взлёт, auto_arm) → navigate_global по точкам → AUTO.RTL или land.
* auto     — mission, если в MAVROS доступен плагин миссий (waypoint), иначе offboard.

Пульт всегда главнее: переключение режима на пульте забирает управление (штатно для PX4); исполнитель offboard
при этом прекращает работу и ничего не переключает. В tbo-tracker — «Вернуть домой» и «Посадить».
"""
from __future__ import annotations

import math
import threading
import time
from collections import deque

from .plan import dist_m
from .vehicle import Vehicle, VehicleError

G = 9.80665
NAN = float("nan")
FRAME_GLOBAL_REL_ALT, FRAME_MISSION = 3, 2
CMD_WAYPOINT, CMD_RTL, CMD_LAND, CMD_TAKEOFF, CMD_CHANGE_SPEED = 16, 20, 21, 22, 178
IN_AIR_STATES = (2, 3, 4)          # mavros_msgs/ExtendedState: IN_AIR, TAKEOFF, LANDING


class _Stop(Exception):
    """Прерывание программы offboard: команда оператора или перехват управления пилотом."""


def call(fn, timeout: float, what: str, **kw):
    """Вызов сервиса ROS с ограничением времени: зависший сервис не блокирует агента."""
    box = {}

    def run():
        try:
            box["r"] = fn(**kw)
        except Exception as e:  # noqa: BLE001 — любой сбой сервиса → понятная ошибка оператору
            box["e"] = e

    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        raise VehicleError("%s: нет ответа за %.0f с" % (what, timeout))
    if "e" in box:
        raise VehicleError("%s: %s" % (what, box["e"]))
    return box["r"]


def _fin(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


class RosVehicle(Vehicle):
    def __init__(self, executor: str = "auto", fcu_wait_s: float = 60.0, tolerance_m: float = 1.5, takeoff_speed: float = 1.0):
        self.want, self.fcu_wait_s, self.tol, self.takeoff_speed = executor, fcu_wait_s, tolerance_m, takeoff_speed
        self.executor = executor
        self._lock = threading.Lock()
        self._s = {}
        self._imu = deque(maxlen=20000)
        self._texts = deque(maxlen=10)
        self._modes = deque(maxlen=50)          # (время, режим) — для ожидания смены режима
        self._wp_list, self._wp_version = [], 0
        self._items, self._seq_to_wp = None, {}
        self._plan = None
        self._fly_thread, self._stop_fly = None, threading.Event()
        self._fly_error, self._phase, self._wp_reached = None, None, 0
        self.technic = False

    # ---------------------------------------------------------------- подключение
    def open(self):
        import rospy
        from geometry_msgs.msg import TwistStamped
        from mavros_msgs.msg import ExtendedState, HomePosition, State, StatusText, WaypointList, WaypointReached
        from mavros_msgs.srv import CommandBool, SetMode, WaypointClear, WaypointPull, WaypointPush
        from sensor_msgs.msg import BatteryState, Imu, NavSatFix
        from std_msgs.msg import Float64, UInt32

        self.rospy = rospy
        rospy.init_node("tbo_agent", anonymous=False, disable_signals=True)
        sub = rospy.Subscriber
        sub("/mavros/state", State, self._on_state, queue_size=5)
        sub("/mavros/extended_state", ExtendedState, lambda m: self._put(landed_state=m.landed_state), queue_size=5)
        sub("/mavros/global_position/global", NavSatFix, self._on_global, queue_size=5)
        sub("/mavros/global_position/raw/fix", NavSatFix, self._on_raw_fix, queue_size=5)
        sub("/mavros/global_position/raw/satellites", UInt32, lambda m: self._put(sats=int(m.data)), queue_size=5)
        sub("/mavros/global_position/rel_alt", Float64, lambda m: self._put(alt_rel=_fin(m.data)), queue_size=5)
        sub("/mavros/global_position/compass_hdg", Float64, lambda m: self._put(heading=_fin(m.data)), queue_size=5)
        for topic in ("/mavros/local_position/velocity_local", "/mavros/local_position/velocity"):
            sub(topic, TwistStamped, self._on_vel, queue_size=5)
        sub("/mavros/battery", BatteryState, self._on_battery, queue_size=5)
        sub("/mavros/home_position/home", HomePosition, self._on_home, queue_size=5)
        sub("/mavros/mission/waypoints", WaypointList, self._on_wp_list, queue_size=5)
        sub("/mavros/mission/reached", WaypointReached, self._on_reached, queue_size=5)
        sub("/mavros/statustext/recv", StatusText, self._on_text, queue_size=20)
        sub("/mavros/imu/data", Imu, self._on_imu, queue_size=100)
        try:
            from mavros_msgs.msg import GPSRAW
            sub("/mavros/gpsstatus/gps1/raw", GPSRAW, lambda m: self._put(gps_fix_raw=int(m.fix_type),
                                                                      sats=int(m.satellites_visible)), queue_size=5)
        except ImportError:
            pass
        sp = rospy.ServiceProxy
        self._arm_srv = sp("/mavros/cmd/arming", CommandBool)
        self._mode_srv = sp("/mavros/set_mode", SetMode)
        self._push_srv = sp("/mavros/mission/push", WaypointPush)
        self._pull_srv = sp("/mavros/mission/pull", WaypointPull)
        self._clear_srv = sp("/mavros/mission/clear", WaypointClear)
        have_mission = self._has("/mavros/mission/push") and self._has("/mavros/mission/pull")
        have_offboard = self._open_technic()
        if self.want == "mission" and not have_mission:
            raise VehicleError("исполнитель mission: нет сервиса /mavros/mission/push — включите плагин waypoint MAVROS "
                               "(уберите его из plugin_blacklist в mavros.launch) или задайте executor = offboard")
        if self.want == "offboard" and not have_offboard:
            raise VehicleError("исполнитель offboard: нет сервисов simple_offboard (navigate_global) — служба technic запущена?")
        self.executor = self.want if self.want != "auto" else ("mission" if have_mission else "offboard" if have_offboard else "none")
        if self.executor == "none":
            raise VehicleError("нет ни сервисов миссии MAVROS, ни simple_offboard — проверьте службу technic")
        deadline = time.monotonic() + self.fcu_wait_s
        while time.monotonic() < deadline and not self._s.get("connected"):
            time.sleep(0.5)

    def _open_technic(self) -> bool:
        try:
            try:
                from technic import srv as tsrv
            except ImportError:
                from clover import srv as tsrv
            from std_srvs.srv import Trigger
        except ImportError:
            return False
        if not (self._has("/navigate_global") and self._has("/get_telemetry")):
            return False
        sp = self.rospy.ServiceProxy
        self._get_telemetry = sp("/get_telemetry", tsrv.GetTelemetry)
        self._navigate = sp("/navigate", tsrv.Navigate)
        self._navigate_global = sp("/navigate_global", tsrv.NavigateGlobal)
        self._land_srv = sp("/land", Trigger)
        self._release = sp("/simple_offboard/release", Trigger)
        self.technic = True
        return True

    def _has(self, name: str, timeout: float = 3.0) -> bool:
        try:
            self.rospy.wait_for_service(name, timeout=timeout)
            return True
        except Exception:  # noqa: BLE001 — ROSException при отсутствии сервиса
            return False

    def info(self) -> dict:
        return {"autopilot": "PX4 (MAVROS)", "executor": self.executor, "technic": self.technic}

    def now(self) -> float:
        return time.monotonic()

    # ---------------------------------------------------------------- подписки
    def _put(self, **kw):
        with self._lock:
            self._s.update(kw)

    def _on_state(self, m):
        self._put(connected=bool(m.connected), armed=bool(m.armed), mode=str(m.mode))
        if not self._modes or self._modes[-1][1] != str(m.mode):
            self._modes.append((time.monotonic(), str(m.mode)))

    def _on_global(self, m):
        ok = m.status.status >= 0
        self._put(lat=_fin(m.latitude) if ok else None, lon=_fin(m.longitude) if ok else None)

    def _on_raw_fix(self, m):
        self._put(gps_status=int(m.status.status))

    def _on_vel(self, m):
        v = m.twist.linear
        self._put(speed=_fin(math.hypot(v.x, v.y)), climb=_fin(v.z))

    def _on_battery(self, m):
        pct = _fin(m.percentage)
        self._put(battery_pct=round(pct * 100, 1) if pct is not None and 0 <= pct <= 1 else None, battery_v=_fin(m.voltage))

    def _on_home(self, m):
        lat, lon = _fin(m.geo.latitude), _fin(m.geo.longitude)
        self._put(home=(lat, lon) if lat is not None and lon is not None else None)

    def _on_wp_list(self, m):
        with self._lock:
            self._wp_list = list(m.waypoints)
            self._wp_version += 1
            self._s["current_seq"] = int(m.current_seq)

    def _on_reached(self, m):
        wp = self._seq_to_wp.get(int(m.wp_seq))
        if wp:
            with self._lock:
                self._wp_reached = max(self._wp_reached, wp)

    def _on_text(self, m):
        if m.severity <= 4:                    # EMERGENCY … WARNING: причины отказа арминга и т. п.
            self._texts.append((time.monotonic(), str(m.text)[:200]))

    def _on_imu(self, m):
        a = m.linear_acceleration
        self._imu.append((time.monotonic(), math.sqrt(a.x * a.x + a.y * a.y + a.z * a.z) / G))

    def _last_text(self, within: float = 30.0):
        if self._texts and time.monotonic() - self._texts[-1][0] <= within:
            return self._texts[-1][1]
        return None

    # ---------------------------------------------------------------- состояние
    def snapshot(self) -> dict:
        with self._lock:
            s = dict(self._s)
            reached = self._wp_reached
        landed = s.get("landed_state")
        armed = bool(s.get("armed"))
        in_air = landed in IN_AIR_STATES if landed else bool(armed and (s.get("alt_rel") or 0) > 1.0)
        fix = s.get("gps_fix_raw")
        if fix is None and s.get("gps_status") is not None:
            fix = 3 if s["gps_status"] >= 0 else 0
        n_wp = len((self._plan or {}).get("waypoints") or [])
        return {"connected": bool(s.get("connected")), "armed": armed, "mode": s.get("mode"), "in_air": in_air,
                "lat": s.get("lat"), "lon": s.get("lon"), "alt_rel": s.get("alt_rel"), "heading": s.get("heading"),
                "speed": s.get("speed"), "climb": s.get("climb"), "battery_pct": s.get("battery_pct"),
                "battery_v": s.get("battery_v"), "gps_fix": fix, "sats": s.get("sats"), "home": s.get("home"),
                "wp_total": n_wp, "wp_reached": reached, "wp_current": min(n_wp, reached + 1) if armed and n_wp else 0,
                "status_text": self._last_text(), "phase": self._phase}

    def drain_imu(self) -> list:
        out = []
        while self._imu:
            out.append(self._imu.popleft())
        return out

    # ---------------------------------------------------------------- режимы
    def _set_mode(self, mode: str, wait: float = 5.0):
        """Режим считается включённым, если контроллер сообщил его после запроса (/mavros/state — около 1 Гц;
        режим мог уже смениться дальше, например AUTO.RTL → AUTO.LAND над точкой взлёта)."""
        t0 = time.monotonic()
        r = call(self._mode_srv, 5, "смена режима на %s" % mode, base_mode=0, custom_mode=mode)
        if not getattr(r, "mode_sent", True):
            raise VehicleError("контроллер отклонил режим %s" % mode)
        end = time.monotonic() + wait
        while time.monotonic() < end:
            if self._s.get("mode") == mode or any(ts >= t0 and m == mode for ts, m in list(self._modes)):
                return
            time.sleep(0.1)
        raise VehicleError("контроллер не перешёл в режим %s%s" % (mode, self._why()))

    def _why(self) -> str:
        t = self._last_text(10)
        return " (PX4: %s)" % t if t else ""

    def _arm(self, wait: float = 5.0):
        r = call(self._arm_srv, 5, "включение моторов", value=True)
        end = time.monotonic() + wait
        while time.monotonic() < end:
            if self._s.get("armed"):
                return
            time.sleep(0.1)
        raise VehicleError("моторы не включились%s" % (self._why() or ("" if getattr(r, "success", False)
                                                                         else " (PX4 отклонил арминг)")))

    # ---------------------------------------------------------------- план: миссия PX4
    def _wp(self, frame, cmd, p1=0.0, p2=0.0, p3=0.0, p4=NAN, lat=0.0, lon=0.0, alt=0.0, current=False):
        from mavros_msgs.msg import Waypoint
        return Waypoint(frame=frame, command=cmd, is_current=current, autocontinue=True, param1=float(p1), param2=float(p2),
                        param3=float(p3), param4=float(p4), x_lat=float(lat), y_long=float(lon), z_alt=float(alt))

    def mission_items(self, plan: dict, position: dict) -> list:
        """Взлёт (над текущей точкой) → скорость → точки (удержание) → возврат домой или посадка."""
        items = [self._wp(FRAME_GLOBAL_REL_ALT, CMD_TAKEOFF, lat=position["lat"], lon=position["lon"],
                          alt=plan["takeoff_alt"], current=True),
                 self._wp(FRAME_MISSION, CMD_CHANGE_SPEED, p1=1, p2=plan["speed_ms"], p3=-1, p4=0)]
        self._seq_to_wp = {}
        for i, w in enumerate(plan["waypoints"], 1):
            self._seq_to_wp[len(items)] = i
            items.append(self._wp(FRAME_GLOBAL_REL_ALT, CMD_WAYPOINT, p1=w.get("hold_s") or 0, lat=w["lat"], lon=w["lon"], alt=w["alt"]))
        if plan.get("return_home", True):
            items.append(self._wp(FRAME_MISSION, CMD_RTL, p4=0))
        else:
            last = plan["waypoints"][-1]
            items.append(self._wp(FRAME_GLOBAL_REL_ALT, CMD_LAND, lat=last["lat"], lon=last["lon"], alt=0))
        return items

    def _pull(self) -> list:
        with self._lock:
            v0 = self._wp_version
        r = call(self._pull_srv, 30, "чтение миссии из контроллера")
        if not getattr(r, "success", False):
            raise VehicleError("контроллер не отдал миссию")
        end = time.monotonic() + 5
        while time.monotonic() < end:
            with self._lock:
                if self._wp_version != v0 and len(self._wp_list) == int(r.wp_received):
                    return list(self._wp_list)
            time.sleep(0.1)
        raise VehicleError("не получен список точек миссии из MAVROS")

    @staticmethod
    def _same(a, b) -> bool:
        if int(a.command) != int(b.command):
            return False
        if int(a.command) in (CMD_WAYPOINT, CMD_TAKEOFF, CMD_LAND):
            return (abs(a.x_lat - b.x_lat) < 2e-6 and abs(a.y_long - b.y_long) < 2e-6
                    and abs(a.z_alt - b.z_alt) < 0.1 and abs(a.param1 - b.param1) < 0.5)
        return True

    def _check_fc(self):
        got = self._pull()
        if len(got) != len(self._items) or not all(self._same(a, b) for a, b in zip(self._items, got)):
            raise VehicleError("миссия в контроллере отличается от плана (%d элементов вместо %d) — "
                               "возможно, загружена другая из QGroundControl" % (len(got), len(self._items)))

    def upload(self, plan: dict, position: dict) -> str:
        self._plan = plan
        with self._lock:
            self._wp_reached = 0
        if self.executor == "offboard":
            if not self._s.get("connected"):
                raise VehicleError("нет связи с полётным контроллером")
            return "план принят: полёт программой на борту (OFFBOARD, simple_offboard Technic)"
        self._items = self.mission_items(plan, position)
        r = call(self._clear_srv, 15, "очистка миссии в контроллере")
        if not getattr(r, "success", False):
            raise VehicleError("контроллер не очистил прежнюю миссию")
        r = call(self._push_srv, 60, "загрузка миссии", start_index=0, waypoints=self._items)
        if not getattr(r, "success", False) or int(getattr(r, "wp_transfered", 0)) != len(self._items):
            raise VehicleError("контроллер принял %s из %d элементов миссии%s"
                               % (getattr(r, "wp_transfered", 0), len(self._items), self._why()))
        self._check_fc()
        return "миссия загружена в PX4 и сверена: взлёт, %d точек, %s" % (
            len(plan["waypoints"]), "возврат домой" if plan.get("return_home", True) else "посадка в последней точке")

    def verify(self, plan: dict):
        if self.executor == "mission":
            if self._items is None:
                raise VehicleError("миссия не загружена")
            self._check_fc()

    def clear(self):
        self._plan, self._items = None, None
        if self.executor == "mission" and not self._s.get("armed"):
            try:
                call(self._clear_srv, 15, "очистка миссии")
            except VehicleError:
                pass

    # ---------------------------------------------------------------- старт и прерывание
    def start_mission(self, plan: dict) -> str:
        if self.executor == "offboard":
            return self._start_offboard(plan)
        prev = self._s.get("mode")
        self._set_mode("AUTO.MISSION")
        try:
            self._arm()
        except VehicleError:
            if prev and prev != "AUTO.MISSION" and not self._s.get("armed"):
                try:                                # без этого арминг с пульта запустил бы миссию
                    self._set_mode(prev, wait=3)
                except VehicleError:
                    pass
            raise
        return "моторы включены, режим AUTO.MISSION — PX4 выполняет миссию"

    def rtl(self) -> str:
        self._halt_offboard()
        self._set_mode("AUTO.RTL")
        self._phase = "rtl"
        return "режим AUTO.RTL: возврат домой и посадка"

    def land(self) -> str:
        self._halt_offboard()
        if self.executor == "offboard" and self.technic:
            r = call(self._land_srv, 5, "посадка (land)")
            if not getattr(r, "success", False):
                self._set_mode("AUTO.LAND")
        else:
            self._set_mode("AUTO.LAND")
        self._phase = "land"
        return "посадка на месте (AUTO.LAND)"

    # ---------------------------------------------------------------- исполнитель offboard (Technic)
    def _start_offboard(self, plan: dict) -> str:
        if self._fly_thread and self._fly_thread.is_alive():
            raise VehicleError("программа полёта уже выполняется")
        self._stop_fly.clear()
        self._fly_error, self._phase = None, "takeoff"
        self._fly_thread = threading.Thread(target=self._fly, args=(plan,), name="offboard", daemon=True)
        self._fly_thread.start()
        end = time.monotonic() + 10
        while time.monotonic() < end:
            if self._s.get("armed"):
                return "моторы включены, режим OFFBOARD — программа полёта на борту"
            if self._fly_error:
                break
            time.sleep(0.1)
        self._halt_offboard()
        raise VehicleError("моторы не включились: %s%s" % (self._fly_error or "нет ответа", self._why()))

    def _halt_offboard(self):
        if self.executor != "offboard":
            return
        if self._fly_thread and self._fly_thread.is_alive():
            self._stop_fly.set()
            self._fly_thread.join(3)
        if self.technic:
            try:
                call(self._release, 3, "остановка setpoint")
            except VehicleError:
                pass

    def _tel(self, frame: str):
        return call(self._get_telemetry, 3, "телеметрия", frame_id=frame)

    def _wait_arrival(self, timeout: float, tol: float):
        end = time.monotonic() + timeout
        while True:
            if self._stop_fly.is_set():
                raise _Stop()
            if self._s.get("mode") not in (None, "OFFBOARD"):
                raise _Stop("пилот перехватил управление (режим %s)" % self._s.get("mode"))
            t = self._tel("navigate_target")
            d = _fin(math.sqrt(t.x ** 2 + t.y ** 2 + t.z ** 2)) if None not in (_fin(t.x), _fin(t.y), _fin(t.z)) else None
            if d is not None and d < tol:
                return
            if time.monotonic() > end:
                raise VehicleError("точка не достигнута за %.0f с" % timeout)
            time.sleep(0.2)

    def _sleep(self, seconds: float):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self._stop_fly.wait(0.2) or self._s.get("mode") not in (None, "OFFBOARD"):
                raise _Stop()

    def _fly(self, plan: dict):
        try:
            t0 = self._tel("map")
            z0 = _fin(t0.z) or 0.0
            call(self._navigate, 5, "взлёт (navigate)", x=0.0, y=0.0, z=float(plan["takeoff_alt"]), yaw=NAN,
                 speed=self.takeoff_speed, frame_id="body", auto_arm=True)
            end = time.monotonic() + 8                 # simple_offboard включает OFFBOARD и арминг
            while not (self._s.get("mode") == "OFFBOARD" and self._s.get("armed")):
                if self._stop_fly.is_set():
                    raise _Stop()
                if time.monotonic() > end:
                    raise VehicleError("контроллер не перешёл в OFFBOARD с включёнными моторами%s" % self._why())
                time.sleep(0.1)
            self._wait_arrival(float(plan["takeoff_alt"]) / self.takeoff_speed * 2 + 30, 0.5)
            self._phase = "mission"
            lat0, lon0 = self._s.get("lat") or plan["home"]["lat"], self._s.get("lon") or plan["home"]["lon"]
            speed = float(plan["speed_ms"])
            for i, w in enumerate(plan["waypoints"], 1):
                d = dist_m(lat0, lon0, w["lat"], w["lon"])
                call(self._navigate_global, 5, "полёт к точке %d (navigate_global)" % i, lat=float(w["lat"]),
                     lon=float(w["lon"]), z=z0 + float(w["alt"]), yaw=NAN, speed=speed, frame_id="map", auto_arm=False)
                self._wait_arrival(d / speed * 2 + 60, self.tol)
                with self._lock:
                    self._wp_reached = i
                lat0, lon0 = w["lat"], w["lon"]
                if w.get("hold_s"):
                    self._phase = "hold"
                    self._sleep(float(w["hold_s"]))
                    self._phase = "mission"
            if plan.get("return_home", True):
                call(self._release, 3, "остановка setpoint")
                self._set_mode("AUTO.RTL")
                self._phase = "rtl"
            else:
                call(self._land_srv, 5, "посадка (land)")
                self._phase = "land"
        except _Stop as e:
            self._fly_error = str(e) or None            # команда оператора или пульт: режимы не трогаем
        except Exception as e:  # noqa: BLE001 — сбой программы в воздухе → безопасный возврат домой
            self._fly_error = str(e)
            if self._s.get("armed") and self._s.get("mode") == "OFFBOARD":
                try:
                    call(self._release, 3, "остановка setpoint")
                    self._set_mode("AUTO.RTL")
                    self._phase = "rtl"
                except VehicleError:
                    pass
