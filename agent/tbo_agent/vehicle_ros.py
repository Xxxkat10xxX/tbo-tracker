"""Борт на ROS Noetic: MAVROS (PX4) и simple_offboard платформы Technic (Skyris Technic 6S; основа — COEX Clover).

Телеметрия — топики MAVROS. План по GPS выполняет исполнитель из agent.conf → [vehicle] executor:

* mission  — план загружается в PX4 как миссия (mavros/mission/push) и сверяется обратным чтением; старт — режим
             AUTO.MISSION и арминг (как «Start Mission» в QGroundControl). Полёт ведёт PX4 и он виден в QGroundControl.
* offboard — программа на борту (offboard.py): navigate → navigate_global по точкам → AUTO.RTL или land.
* auto     — mission, если в MAVROS доступен плагин миссий (waypoint), иначе offboard.

Локальный план (в помещении, без GPS) всегда выполняет программа на борту в кадре map (offboard.py).
Пульт главнее: переключение режима на пульте забирает управление (в документации Skyris — из Position в Stab).
"""
from __future__ import annotations

import logging
import math
import os
import subprocess
import threading
import time
from collections import deque
from urllib.parse import urlsplit, urlunsplit

from .offboard import OffboardMixin
from .plan import frame_of, uses_yaw, yaw_desc, yaw_mode
from .vehicle import Vehicle, VehicleError

G = 9.80665
NAN = float("nan")
FRAME_GLOBAL_REL_ALT, FRAME_MISSION = 3, 2
CMD_WAYPOINT, CMD_RTL, CMD_LAND, CMD_TAKEOFF, CMD_CHANGE_SPEED = 16, 20, 21, 22, 178
IN_AIR_STATES = (2, 3, 4)          # mavros_msgs/ExtendedState: IN_AIR, TAKEOFF, LANDING
PARAMS = ("EKF2_GPS_CTRL", "EKF2_AID_MASK", "SYS_HAS_MAG", "EKF2_MAG_TYPE", "COM_DISARM_LAND")


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


def service_active(name: str) -> bool:
    """Запущена ли служба systemd (например, technic-show — второй клиент управления дроном)."""
    try:
        return subprocess.run(["systemctl", "is-active", "--quiet", name], timeout=3).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def keep_agent_log(handlers, level: int) -> None:
    """Вернуть сообщения агента в журнал службы (journalctl) после rospy.init_node.

    init_node настраивает logging по python_logging.conf ROS: у корневого журнала остаётся только файл
    $ROS_LOG_DIR/tbo_agent.log, обработчик службы снимается — и в journalctl после запуска не было ни «подключён к …»,
    ни ошибок связи. Снятые обработчики (handlers — корневые до init_node) и уровень из agent.conf (level) получает
    журнал агента «tbo_agent», а не корневой: служебные сообщения rospy остаются только в файле ROS, а сообщения
    агента по-прежнему пишутся и туда.
    """
    root, agent = logging.getLogger(), logging.getLogger("tbo_agent")
    lost = [h for h in handlers if h not in root.handlers and h not in agent.handlers]
    if not lost:                      # ROS обработчики не тронул — добавлять нечего, иначе строки задвоились бы
        return
    for h in lost:
        agent.addHandler(h)
    if agent.level == logging.NOTSET:
        agent.setLevel(level)


LOOPBACK = "127.0.0.1"


def loopback_ros_env(env) -> dict:
    """ROS-сеть агента — по адресу 127.0.0.1, как у служб образа Technic (technic.service: ROS_MASTER_URI
    http://127.0.0.1:11311, ROS_IP=127.0.0.1): все узлы ROS — на этой же плате.

    Агент обращался к master по http://localhost:11311 (по умолчанию setup.bash ROS), а свой адрес объявлял
    именем <hostname>.local. В /etc/hosts образа Technic нет строки localhost, и каждый вызов сервиса
    simple_offboard (navigate, set_yaw, get_telemetry — новое соединение с запросом к master) начинался с DNS-запроса
    к точке доступа по Wi-Fi. При слабом Wi-Fi такой запрос ждал секунды, вызовы не укладывались в 5 с — борт висел
    на месте, а затем программа его сажала.

    Меняет env на месте: localhost в ROS_MASTER_URI → 127.0.0.1; если адрес узла не задан (ни ROS_IP,
    ни ROS_HOSTNAME), а master на этой плате — ROS_IP=127.0.0.1. Явно заданные значения (ros.env) не трогает.
    Возвращает изменённые переменные — для журнала."""
    changed = {}
    uri = env.get("ROS_MASTER_URI") or "http://localhost:11311"
    try:
        parts = urlsplit(uri)
        host, port = parts.hostname, parts.port
    except ValueError:
        return changed
    if host == "localhost":
        host = LOOPBACK
        uri = urlunsplit((parts.scheme or "http", "%s:%d" % (host, port or 11311), parts.path, parts.query, parts.fragment))
    if env.get("ROS_MASTER_URI") != uri:
        env["ROS_MASTER_URI"] = changed["ROS_MASTER_URI"] = uri
    if not env.get("ROS_IP") and not env.get("ROS_HOSTNAME") and host and host.startswith("127."):
        env["ROS_IP"] = changed["ROS_IP"] = LOOPBACK
    return changed


def gps_enabled(p: dict):
    """GPS в навигации PX4: EKF2_GPS_CTRL (PX4 ≥ 1.14, бит 0) или EKF2_AID_MASK (раньше, бит 0). None — неизвестно."""
    for name in ("EKF2_GPS_CTRL", "EKF2_AID_MASK"):
        if p.get(name) is not None:
            return int(p[name]) & 1 == 1
    return None


class RosVehicle(OffboardMixin, Vehicle):
    def __init__(self, executor: str = "auto", fcu_wait_s: float = 60.0, tolerance_m: float = 1.5, takeoff_speed: float = 1.0,
                 local_tolerance_m: float = 0.25):
        self.want, self.fcu_wait_s, self.tol, self.takeoff_speed = executor, fcu_wait_s, tolerance_m, takeoff_speed
        self.tol_local = local_tolerance_m
        self.executor = executor
        self._lock = threading.Lock()
        self._s = {}
        self._imu = deque(maxlen=20000)
        self._imu_t = 0.0                       # время последнего отсчёта (отсчёты не идут назад)
        self._acc_last = None                   # (время, |a| в g) — для проверки потока акселерометра
        self._texts = deque(maxlen=10)
        self._modes = deque(maxlen=50)          # (время, режим) — для ожидания смены режима
        self._wp_list, self._wp_version = [], 0
        self._items, self._seq_to_wp = None, {}
        self._plan, self._local = None, None
        self._fly_thread, self._stop_fly = None, threading.Event()
        self._fly_error, self._phase, self._wp_reached = None, None, 0
        self._params, self._params_at, self._param_get = {}, 0.0, None
        self._set_yaw = None                    # simple_offboard set_yaw: разворот на месте (курс в плане)
        self.technic = False

    def _call(self, fn, timeout: float, what: str, **kw):
        return call(fn, timeout, what, **kw)

    # ---------------------------------------------------------------- подключение
    def open(self):
        changed = loopback_ros_env(os.environ)          # до импорта rospy: адреса ROS читаются из окружения
        import rospy
        from geometry_msgs.msg import PoseStamped, TwistStamped
        from mavros_msgs.msg import ExtendedState, HomePosition, State, StatusText, WaypointList, WaypointReached
        from mavros_msgs.srv import CommandBool, SetMode, WaypointClear, WaypointPull, WaypointPush
        from sensor_msgs.msg import BatteryState, Imu, NavSatFix
        from std_msgs.msg import Float64, UInt32

        self.rospy = rospy
        root = logging.getLogger()
        handlers, level = list(root.handlers), root.level
        rospy.init_node("tbo_agent", anonymous=False, disable_signals=True)
        keep_agent_log(handlers, level)
        if changed:
            logging.getLogger("tbo_agent").info("ROS без DNS: %s", ", ".join("%s=%s" % kv for kv in sorted(changed.items())))
        sub = rospy.Subscriber
        sub("/mavros/state", State, self._on_state, queue_size=5)
        sub("/mavros/extended_state", ExtendedState, lambda m: self._put(landed_state=m.landed_state), queue_size=5)
        sub("/mavros/global_position/global", NavSatFix, self._on_global, queue_size=5)
        sub("/mavros/global_position/raw/fix", NavSatFix, self._on_raw_fix, queue_size=5)
        sub("/mavros/global_position/raw/satellites", UInt32, lambda m: self._put(sats=int(m.data)), queue_size=5)
        sub("/mavros/global_position/rel_alt", Float64, lambda m: self._put(alt_rel=_fin(m.data)), queue_size=5)
        sub("/mavros/global_position/compass_hdg", Float64, lambda m: self._put(heading=_fin(m.data)), queue_size=5)
        sub("/mavros/local_position/pose", PoseStamped, self._on_pose, queue_size=5)
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
        try:
            from mavros_msgs.srv import ParamGet
            if self._has("/mavros/param/get", 2):
                self._param_get = sp("/mavros/param/get", ParamGet)
        except ImportError:
            pass
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
        if hasattr(tsrv, "SetYaw") and self._has("/set_yaw", 1.0):
            self._set_yaw = sp("/set_yaw", tsrv.SetYaw)
        self.technic = True
        return True

    def _has(self, name: str, timeout: float = 3.0) -> bool:
        try:
            self.rospy.wait_for_service(name, timeout=timeout)
            return True
        except Exception:  # noqa: BLE001 — ROSException при отсутствии сервиса
            return False

    def params(self) -> dict:
        """Параметры PX4, важные для полёта (через mavros/param/get; кэш 60 с)."""
        if self._param_get is None or time.monotonic() - self._params_at < 60:
            return dict(self._params)
        out = {}
        for name in PARAMS:
            try:
                r = call(self._param_get, 3, "параметр %s" % name, param_id=name)
            except VehicleError:
                continue
            if getattr(r, "success", False):
                v = r.value
                out[name] = v.real if getattr(v, "real", 0.0) else getattr(v, "integer", 0)
        self._params, self._params_at = out, time.monotonic()
        return dict(out)

    def info(self) -> dict:
        sim = False
        try:
            sim = bool(self.rospy.get_param("/use_sim_time", False))
        except Exception:  # noqa: BLE001
            pass
        return {"autopilot": "PX4 (MAVROS)", "executor": self.executor, "technic": self.technic, "simulation": sim,
                "params": self.params(), "technic_show": service_active("technic-show")}

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

    def _on_pose(self, m):
        p, q = m.pose.position, m.pose.orientation
        yaw = math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))
        vals = [_fin(p.x), _fin(p.y), _fin(p.z)]
        self._put(pose=None if None in vals else (vals[0], vals[1], vals[2], yaw, time.monotonic()))

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
        v = (a.x, a.y, a.z)
        if v == (0.0, 0.0, 0.0) or not all(math.isfinite(c) for c in v):
            return          # нет данных: MAVROS отдаёт нули, пока контроллер не передаёт HIGHRES_IMU / RAW_IMU
        g = math.sqrt(a.x * a.x + a.y * a.y + a.z * a.z) / G
        now = time.monotonic()
        t = now
        try:                # время измерения по метке сообщения: пачка сообщений, пришедших разом, не сжимает время
            lag = self.rospy.get_time() - m.header.stamp.to_sec()
            if 0.0 < lag <= 0.5:
                t = now - lag
        except Exception:   # noqa: BLE001 — нет метки: время прихода
            pass
        t = max(t, self._imu_t)
        self._imu_t = t
        self._acc_last = (now, g)
        self._imu.append((t, g))

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
        pose = s.get("pose")
        local_ok = bool(pose and time.monotonic() - pose[4] < 1.5)
        lp = self.local_position() if frame_of(self._plan) == "local" else None
        alt = lp[2] if lp else s.get("alt_rel")
        in_air = landed in IN_AIR_STATES if landed else bool(armed and (alt or 0) > (0.3 if lp else 1.0))
        fix = s.get("gps_fix_raw")
        if fix is None and s.get("gps_status") is not None:
            fix = 3 if s["gps_status"] >= 0 else 0
        n_wp = len((self._plan or {}).get("waypoints") or [])
        home = (self._local["lat0"], self._local["lon0"]) if lp else s.get("home")
        # курс для карты: локально — от «верха» карты (нос при взлёте), по GPS — компас
        heading = self.local_heading() if lp else s.get("heading")
        return {"connected": bool(s.get("connected")), "armed": armed, "mode": s.get("mode"), "in_air": in_air,
                "lat": lp[0] if lp else s.get("lat"), "lon": lp[1] if lp else s.get("lon"), "alt_rel": alt,
                "heading": heading, "speed": s.get("speed"), "climb": s.get("climb"),
                "battery_pct": s.get("battery_pct"), "battery_v": s.get("battery_v"), "gps_fix": fix, "sats": s.get("sats"),
                "home": home, "local_ok": local_ok, "wp_total": n_wp, "wp_reached": reached,
                "wp_current": min(n_wp, reached + 1) if armed and n_wp else 0, "status_text": self._last_text(),
                "phase": self._phase}

    def drain_imu(self) -> list:
        out = []
        while self._imu:
            out.append(self._imu.popleft())
        return out

    def extra_checks(self, plan: dict) -> list:
        out = []
        if service_active("technic-show"):
            out.append({"name": "Нет другого клиента управления", "ok": False,
                        "detail": "запущен Техник-шоу (technic-show.service) — остановите: sudo systemctl stop technic-show"})
        p = self.params()
        if frame_of(plan) == "local":
            out.append({"name": "simple_offboard Technic", "ok": self.technic,
                        "detail": "navigate, get_telemetry, land" if self.technic else "нет сервисов — служба technic запущена?"})
        else:
            gps = gps_enabled(p)
            if gps is not None:
                out.append({"name": "GPS в навигации PX4", "ok": gps, "detail": "включён" if gps else
                            "выключен (EKF2_GPS_CTRL / EKF2_AID_MASK) — параметры Technic для полётов в помещении. Для GPS: "
                            "модуль GPS с компасом (разъёмы GPS и I2C), калибровка компаса, включить GPS в EKF2. Без GPS — "
                            "локальная миссия"})
            if p.get("SYS_HAS_MAG") is not None and int(p["SYS_HAS_MAG"]) == 0:
                out.append({"name": "Магнитометр", "ok": False,
                            "detail": "SYS_HAS_MAG=0 — для полёта по GPS нужен компас модуля GPS (I2C) и его калибровка"})
        acc = self._acc_last
        if acc is None or time.monotonic() - acc[0] > 3 or not 0.5 <= acc[1] <= 1.5:
            out.append({"name": "Акселерометр (падения и столкновения)", "ok": True, "warn": True,
                        "detail": ("нет данных /mavros/imu/data" if acc is None or time.monotonic() - acc[0] > 3 else
                                   "на земле %.2f g вместо ~1 g" % acc[1]) +
                                  " — падения и столкновения в полёте не будут обнаружены. Проверьте поток HIGHRES_IMU "
                                  "от PX4 к MAVROS (режим MAVLink Onboard на порту Orange Pi)"})
        d = p.get("COM_DISARM_LAND")
        if d is not None and float(d) <= 0:
            out.append({"name": "Выключение моторов после посадки", "ok": True, "warn": True,
                        "detail": "COM_DISARM_LAND ≤ 0: моторы не выключатся сами — полёт завершится через 10 с на земле"})
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
        """Взлёт (над текущей точкой) → скорость → точки (удержание) → возврат домой или посадка.

        Курс точки — param4 (градусы от севера по часовой; NaN — по MPC_YAW_MODE, по умолчанию носом к точке):
        route — NaN, кроме точек с заданным курсом; keep — курс борта на земле (компас), а после точки с заданным
        курсом — он. PX4 набирает курс точки по пути к ней и ждёт его на точке (MIS_YAW_ERR, MIS_YAW_TMT)."""
        items = [self._wp(FRAME_GLOBAL_REL_ALT, CMD_TAKEOFF, lat=position["lat"], lon=position["lon"],
                          alt=plan["takeoff_alt"], current=True),
                 self._wp(FRAME_MISSION, CMD_CHANGE_SPEED, p1=1, p2=plan["speed_ms"], p3=-1, p4=0)]
        self._seq_to_wp = {}
        route = yaw_mode(plan) == "route"
        keep = None if route else _fin(self._s.get("heading"))
        for i, w in enumerate(plan["waypoints"], 1):
            self._seq_to_wp[len(items)] = i
            yaw = NAN if keep is None else keep
            if w.get("yaw_deg") is not None:
                yaw = float(w["yaw_deg"]) % 360.0
                keep = None if route else yaw
            items.append(self._wp(FRAME_GLOBAL_REL_ALT, CMD_WAYPOINT, p1=w.get("hold_s") or 0, p4=yaw,
                                  lat=w["lat"], lon=w["lon"], alt=w["alt"]))
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

    def _program(self, plan: dict) -> bool:
        """План выполняет программа на борту (simple_offboard), а не миссия PX4."""
        return frame_of(plan) == "local" or self.executor == "offboard"

    def upload(self, plan: dict, position: dict) -> str:
        self._plan, self._local = plan, None
        with self._lock:
            self._wp_reached = 0
        if self._program(plan) and uses_yaw(plan) and self._set_yaw is None:
            raise VehicleError("в миссии задан курс, а у simple_offboard нет сервиса set_yaw (разворот на месте) — "
                               "обновите ПО Technic или уберите курс из миссии")
        if frame_of(plan) == "local":
            if not self.technic:
                raise VehicleError("для локальной миссии нужен simple_offboard Technic (служба technic)")
            if not self._s.get("connected"):
                raise VehicleError("нет связи с полётным контроллером")
            return "план принят: локальная навигация (OFFBOARD, кадр map, метры от точки взлёта)" + yaw_desc(plan)
        if self.executor == "offboard":
            if not self._s.get("connected"):
                raise VehicleError("нет связи с полётным контроллером")
            return "план принят: полёт программой на борту (OFFBOARD, simple_offboard Technic)" + yaw_desc(plan)
        self._items = self.mission_items(plan, position)
        r = call(self._clear_srv, 15, "очистка миссии в контроллере")
        if not getattr(r, "success", False):
            raise VehicleError("контроллер не очистил прежнюю миссию")
        r = call(self._push_srv, 60, "загрузка миссии", start_index=0, waypoints=self._items)
        if not getattr(r, "success", False) or int(getattr(r, "wp_transfered", 0)) != len(self._items):
            raise VehicleError("контроллер принял %s из %d элементов миссии%s"
                               % (getattr(r, "wp_transfered", 0), len(self._items), self._why()))
        self._check_fc()
        return "миссия загружена в PX4 и сверена: взлёт, %d точек, %s%s" % (
            len(plan["waypoints"]), "возврат домой" if plan.get("return_home", True) else "посадка в последней точке",
            yaw_desc(plan))

    def verify(self, plan: dict):
        if not self._program(plan):
            if self._items is None:
                raise VehicleError("миссия не загружена")
            self._check_fc()

    def clear(self):
        plan, self._plan, self._items, self._local = self._plan, None, None, None
        if plan is not None and not self._program(plan) and not self._s.get("armed"):
            try:
                call(self._clear_srv, 15, "очистка миссии")
            except VehicleError:
                pass

    # ---------------------------------------------------------------- старт и прерывание
    def start_mission(self, plan: dict) -> str:
        if frame_of(plan) == "local":
            return self._start_program(self._fly_local, plan)
        if self.executor == "offboard":
            return self._start_program(self._fly, plan)
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
        if frame_of(self._plan) == "local" and self._local:
            self._halt_program(release=False)       # цель остаётся — PX4 не уходит в failsafe потери OFFBOARD
            self._launch(self._return_local, self._plan)
            return "возврат к точке взлёта и посадка (программа на борту, без GPS)"
        self._halt_program(release=True)
        self._set_mode("AUTO.RTL")
        self._phase = "rtl"
        return "режим AUTO.RTL: возврат домой и посадка"

    def land(self) -> str:
        program = self._plan is not None and self._program(self._plan)
        self._halt_program(release=False)
        if program and self.technic:
            r = call(self._land_srv, 5, "посадка (land)")
            if not getattr(r, "success", False):
                self._set_mode("AUTO.LAND")
        else:
            self._set_mode("AUTO.LAND")
        self._phase = "land"
        return "посадка на месте (AUTO.LAND)"
