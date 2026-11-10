"""Модель PX4 + MAVROS + simple_offboard (Technic) для тестов RosVehicle без ROS и без дрона.

Регистрирует сервисы с теми же именами, что на борту, и публикует топики MAVROS ~50 раз в секунду.
Скорости завышены (40 м/с по горизонтали), чтобы полёт в тесте занимал секунды.
"""
import math
import threading
import time
from types import SimpleNamespace as NS

import rospy
from geometry_msgs.msg import PoseStamped, TwistStamped
from mavros_msgs.msg import ExtendedState, HomePosition, State, StatusText, Waypoint, WaypointList, WaypointReached
from sensor_msgs.msg import BatteryState, Imu, NavSatFix
from std_msgs.msg import Float64, UInt32

R = 6371000.0


class FakePX4:
    # заводские параметры Technic для полётов в помещении (docs.skyris.ru → «Параметры полетного контроллера»)
    TECHNIC_INDOOR = {"EKF2_GPS_CTRL": 0, "SYS_HAS_MAG": 0, "EKF2_MAG_TYPE": 5, "COM_DISARM_LAND": 2.0}

    def __init__(self, lat=55.75, lon=37.6, mission_plugin=True, technic=True, refuse_arm=None, speed=40.0, params=None,
                 target_lag_s=0.0, hover_offset=0.0, tel_delay=None, map_spike_every=0):
        """target_lag_s — кадр navigate_target показывает прежнюю цель столько секунд после команды (TF отстаёт);
        hover_offset — в OFFBOARD борт устанавливается в стольких метрах от цели (оптический поток «плавает»)."""
        rospy.reset()
        self.lat0, self.lon0 = lat, lon
        self.x = self.y = self.z = 0.0
        self.armed, self.mode, self.landed_state = False, "POSCTL", 1
        self.items, self.seq, self.target = [], 0, None
        self.refuse_arm, self.speed = refuse_arm, speed
        self.target_lag_s, self.hover_offset = target_lag_s, hover_offset
        # перегруженный борт: get_telemetry отвечает с задержкой (по кадрам), каждый N-й запрос кадра map — 6 с
        self.tel_delay, self.map_spike_every, self._map_calls = dict(tel_delay or {}), map_spike_every, 0
        self._prev_target, self._target_at = None, 0.0
        self.calls = []
        self.params = dict(self.TECHNIC_INDOOR if params is None else params)
        self._stop = threading.Event()
        s = rospy._services
        s["/mavros/param/get"] = self._param_get
        s["/mavros/cmd/arming"] = self._arming
        s["/mavros/set_mode"] = self._set_mode
        if mission_plugin:
            s.update({"/mavros/mission/push": self._push, "/mavros/mission/pull": self._pull, "/mavros/mission/clear": self._clear})
        if technic:
            s.update({"/get_telemetry": self._telemetry, "/navigate": self._navigate, "/navigate_global": self._navigate_global,
                      "/land": self._land, "/simple_offboard/release": self._release})
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self._stop.set()

    # ------------------------------------------------------------ геометрия
    def xy(self, lat, lon):
        return (math.radians(lon - self.lon0) * R * math.cos(math.radians(self.lat0)), math.radians(lat - self.lat0) * R)

    def ll(self):
        return (self.lat0 + math.degrees(self.y / R), self.lon0 + math.degrees(self.x / (R * math.cos(math.radians(self.lat0)))))

    # ------------------------------------------------------------ сервисы MAVROS
    def _arming(self, value):
        self.calls.append(("arming", value))
        if value and self.refuse_arm:
            rospy.publish("/mavros/statustext/recv", StatusText(severity=3, text=self.refuse_arm))
            return NS(success=False, result=1)
        self.armed = bool(value)
        return NS(success=True, result=0)

    def _set_mode(self, base_mode, custom_mode):
        self.calls.append(("set_mode", custom_mode))
        self.mode = custom_mode
        if custom_mode == "AUTO.MISSION" and not self.armed:
            self.seq = 0
        return NS(mode_sent=True)

    def _push(self, start_index, waypoints):
        self.calls.append(("push", len(waypoints)))
        self.items = [Waypoint(**w.__dict__) for w in waypoints]
        return NS(success=True, wp_transfered=len(self.items))

    def _pull(self):
        rospy.publish("/mavros/mission/waypoints", WaypointList(current_seq=self.seq, waypoints=list(self.items)))
        return NS(success=True, wp_received=len(self.items))

    def _clear(self):
        self.calls.append(("clear",))
        self.items = []
        return NS(success=True)

    def _param_get(self, param_id):
        if param_id not in self.params:
            return NS(success=False, value=NS(integer=0, real=0.0))
        v = self.params[param_id]
        return NS(success=True, value=NS(integer=v if isinstance(v, int) else 0, real=float(v) if isinstance(v, float) else 0.0))

    # ------------------------------------------------------------ simple_offboard (Technic)
    def _set_target(self, target):
        self._prev_target, self._target_at, self.target = self.target, time.time(), target

    def _telemetry(self, frame_id=""):
        if self.tel_delay.get(frame_id):
            time.sleep(self.tel_delay[frame_id])
        if frame_id == "map" and self.map_spike_every:
            self._map_calls += 1
            if self._map_calls % self.map_spike_every == 0:
                time.sleep(6.0)
        if frame_id == "navigate_target":
            tgt = self.target
            if self._prev_target is not None and time.time() - self._target_at < self.target_lag_s:
                tgt = self._prev_target                       # TF цели ещё не обновился
            if tgt is None:
                return NS(x=float("nan"), y=float("nan"), z=float("nan"))
            return NS(x=tgt[0] - self.x, y=tgt[1] - self.y, z=tgt[2] - self.z)
        lat, lon = self.ll()
        return NS(x=self.x, y=self.y, z=self.z, yaw=0.0, lat=lat, lon=lon, armed=self.armed, mode=self.mode, connected=True)

    def _navigate(self, x, y, z, yaw, speed, frame_id, auto_arm):
        self.calls.append(("navigate", frame_id, z, auto_arm))
        if auto_arm:
            self.mode, self.armed = "OFFBOARD", True
        self._set_target((self.x + x, self.y + y, self.z + z) if frame_id == "body" else (x, y, z))
        return NS(success=True, message="")

    def _navigate_global(self, lat, lon, z, yaw, speed, frame_id, auto_arm):
        self.calls.append(("navigate_global", round(lat, 6), round(lon, 6), z, frame_id))
        tx, ty = self.xy(lat, lon)
        self._set_target((tx, ty, z))
        return NS(success=True, message="")

    def _land(self):
        self.calls.append(("land",))
        self.mode = "AUTO.LAND"
        return NS(success=True, message="")

    def _release(self):
        self.calls.append(("release",))
        return NS(success=True, message="")

    # ------------------------------------------------------------ модель полёта
    def _move(self, tx, ty, tz, dt):
        dx, dy = tx - self.x, ty - self.y
        d = math.hypot(dx, dy)
        if d > 1e-9:
            k = min(1.0, self.speed * dt / d)
            self.x += dx * k
            self.y += dy * k
        self.z += max(-20 * dt, min(20 * dt, tz - self.z))
        return math.hypot(tx - self.x, ty - self.y), abs(tz - self.z)

    def _reach(self):
        rospy.publish("/mavros/mission/reached", WaypointReached(wp_seq=self.seq))
        self.seq += 1

    def _step(self, dt):
        if not self.armed:
            self.landed_state = 1
            return
        if self.mode == "AUTO.MISSION" and self.seq < len(self.items):
            it = self.items[self.seq]
            if it.command == 22:
                self.z = min(it.z_alt, self.z + 20 * dt)
                if self.z >= it.z_alt - 0.01:
                    self._reach()
            elif it.command == 16:
                dh, dv = self._move(*self.xy(it.x_lat, it.y_long), it.z_alt, dt)
                if dh < 0.5 and dv < 0.3:
                    self._reach()
            elif it.command == 20:
                self.mode = "AUTO.RTL"
            elif it.command == 21:
                self.mode = "AUTO.LAND"
            else:
                self._reach()
        elif self.mode == "OFFBOARD" and self.target is not None:
            tx, ty, tz = self.target
            self._move(tx + self.hover_offset, ty, tz, dt)
        elif self.mode == "AUTO.RTL":                 # как PX4: режим AUTO.RTL до посадки (долёт, затем снижение)
            if math.hypot(self.x, self.y) > 0.5:
                self._move(0.0, 0.0, max(self.z, 5.0), dt)
            else:
                self.z = max(0.0, self.z - 15 * dt)
        elif self.mode == "AUTO.LAND":
            self.z = max(0.0, self.z - 15 * dt)
        if self.z > 0.3:
            self.landed_state = 2
        if self.landed_state == 2 and self.z <= 0.0:
            self.landed_state, self.armed, self.mode = 1, False, "AUTO.LOITER"     # автоматическое выключение моторов

    def _loop(self):
        dt = 0.02
        while not self._stop.is_set():
            self._step(dt)
            lat, lon = self.ll()
            p = rospy.publish
            p("/mavros/state", State(connected=True, armed=self.armed, mode=self.mode))
            p("/mavros/extended_state", ExtendedState(landed_state=self.landed_state))
            p("/mavros/global_position/global", NavSatFix(latitude=lat, longitude=lon, altitude=150 + self.z, status=NS(status=0)))
            p("/mavros/global_position/raw/fix", NavSatFix(latitude=lat, longitude=lon, status=NS(status=0)))
            p("/mavros/global_position/raw/satellites", UInt32(data=15))
            p("/mavros/global_position/rel_alt", Float64(data=self.z))
            p("/mavros/local_position/velocity_local", TwistStamped(twist=NS(linear=NS(x=0.0, y=0.0, z=0.0))))
            p("/mavros/local_position/pose", PoseStamped(pose=NS(position=NS(x=self.x, y=self.y, z=self.z),
                                                                 orientation=NS(x=0.0, y=0.0, z=0.0, w=1.0))))
            p("/mavros/battery", BatteryState(percentage=0.9, voltage=24.6))
            p("/mavros/home_position/home", HomePosition(geo=NS(latitude=self.lat0, longitude=self.lon0)))
            p("/mavros/imu/data", Imu(linear_acceleration=NS(x=0.0, y=0.0, z=9.80665)))
            time.sleep(dt)
