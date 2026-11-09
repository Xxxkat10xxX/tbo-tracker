"""Регистратор полёта: трек (1 Гц), акселерометр |a| (25 Гц, с сохранением пиков), события и итог.

Время — по часам борта (vehicle.now(), в симуляторе ускоренное); в журнал полёт записывается по часам сервера:
окончание — момент посадки, начало = окончание − длительность полёта.
"""
from __future__ import annotations

import math

from .plan import to_xy

IMU_HZ = 25
IMU_MAX_G = 200.0             # выше не бывает и у ударных датчиков: значение — сбой, не перегрузка
GROUND_HIT_G = 4.0            # удар на земле до взлёта (опрокидывание при запуске) — как порог столкновения на сервере
TRACK_MAX = 40000             # сервер принимает до 50 000 точек трека: длинный полёт прореживается, а не теряется
MODE_PHASE = {"AUTO.TAKEOFF": "takeoff", "AUTO.MISSION": "mission", "AUTO.LOITER": "hold", "AUTO.RTL": "rtl",
              "AUTO.LAND": "land", "AUTO.PRECLAND": "land", "OFFBOARD": "mission"}
MANUAL_MODES = ("MANUAL", "STABILIZED", "ALTCTL", "POSCTL", "ACRO", "RATTITUDE")


class Recorder:
    def __init__(self, armed_at: float):
        self.armed_at = armed_at
        self.takeoff_t = None
        self.land_t = None
        self.last_air_t = None
        self.track = []
        self.imu = []                 # 25 Гц: |a| в g с момента взлёта
        self._bucket = None           # (номер интервала, значение с наибольшим отклонением от 1 g)
        self.ground_peak = 0.0        # наибольшая перегрузка от арминга до взлёта
        self.status_texts = []
        self.mode_changes = []
        self.max_alt = 0.0
        self.bat_start = self.bat_end = None
        self.min_voltage = None
        self.wp_reached = 0
        self.manual_override = False
        self._last_track_t = -1e9
        self._last_mode = None

    @property
    def took_off(self) -> bool:
        return self.takeoff_t is not None

    def phase(self, snap: dict, flying_state: str) -> str:
        if flying_state == "returning":
            return "rtl"
        if flying_state == "landing":
            return "land"
        mode = (snap.get("mode") or "").upper()
        if mode in MANUAL_MODES:
            return "manual"
        return snap.get("phase") or MODE_PHASE.get(mode, "mission")

    def sample(self, t: float, snap: dict, imu: list, flying_state: str):
        in_air = bool(snap.get("in_air"))
        if in_air and self.takeoff_t is None:
            self.takeoff_t = t
            self.bat_start = snap.get("battery_pct")
        if in_air:
            self.last_air_t = t
        mode = snap.get("mode")
        if mode and mode != self._last_mode:
            self._last_mode = mode
            if len(self.mode_changes) < 50:
                self.mode_changes.append([round(t - self.armed_at, 1), mode])
            if mode.upper() in MANUAL_MODES and self.took_off:
                self.manual_override = True
        txt = snap.get("status_text")
        if txt and (not self.status_texts or self.status_texts[-1] != txt):
            self.status_texts = (self.status_texts + [txt])[-15:]
        if snap.get("alt_rel") is not None:
            self.max_alt = max(self.max_alt, float(snap["alt_rel"]))
        if snap.get("battery_pct") is not None:
            self.bat_end = snap["battery_pct"]
        if snap.get("battery_v") is not None:
            v = float(snap["battery_v"])
            self.min_voltage = v if self.min_voltage is None else min(self.min_voltage, v)
        self.wp_reached = max(self.wp_reached, int(snap.get("wp_reached") or 0))
        for ti, g in imu:
            if self.took_off:
                self._imu(ti, g)
            elif ti >= self.armed_at and math.isfinite(g):   # опрокидывание при запуске: полёта нет, удар показать
                # (отсчёты до арминга — например, толчок, пока борт несли, — накопленные в очереди, не в счёт)
                self.ground_peak = max(self.ground_peak, min(g, IMU_MAX_G))
        if t - self._last_track_t >= 1.0 and snap.get("lat") is not None:
            self._last_track_t = t
            self.track.append([round(t - self.armed_at, 1), round(float(snap["lat"]), 7), round(float(snap["lon"]), 7),
                               round(float(snap.get("alt_rel") or 0), 1), round(float(snap.get("speed") or 0), 1),
                               self.phase(snap, flying_state)])
            if len(self.track) > TRACK_MAX:
                self.track = self.track[::2]

    def _imu(self, t: float, g: float):
        if t < self.takeoff_t or not math.isfinite(g):
            return
        g = min(g, IMU_MAX_G)
        k = int((t - self.takeoff_t) * IMU_HZ)
        if self._bucket is None or self._bucket[0] != k:
            self._flush_bucket()
            self._bucket = [k, g]
        elif abs(g - 1.0) > abs(self._bucket[1] - 1.0):
            self._bucket[1] = g

    def _flush_bucket(self):
        if self._bucket is None:
            return
        k, g = self._bucket
        self._bucket = None
        # Пропуск потока — нейтральный 1 g. Не «последним значением»: повтор одного провала до 0,3 g за время
        # пропуска дорисовал бы невесомость, и обычный толчок 3,5 g после него стал бы «падением» (а повтор пика —
        # затянул бы удар). Нейтральное значение не создаёт и не продлевает событий.
        while len(self.imu) < k:
            self.imu.append(1.0)
        if len(self.imu) == k:
            self.imu.append(round(g, 3))

    def landed(self, t: float):
        self.land_t = t
        self._flush_bucket()

    def distance_m(self) -> float:
        d = 0.0
        for a, b in zip(self.track, self.track[1:]):
            x, y = to_xy(a[1], a[2], b[1], b[2])
            d += math.hypot(x, y)
        return round(d, 1)

    def build(self, end_wall: float, status: str, reason: str, extra: dict) -> dict:
        """Тело запроса журнала для сервера (POST /api/agent/runs/{id}/log)."""
        from .client import iso
        summary = {"distance_m": self.distance_m(), "max_alt_m": round(self.max_alt, 1),
                   "battery_start_pct": self.bat_start, "battery_end_pct": self.bat_end, "min_voltage": self.min_voltage,
                   "wp_reached": self.wp_reached, "status_texts": self.status_texts, "mode_changes": self.mode_changes,
                   "reason": reason, **extra}
        body = {"status": status, "reason": reason, "track": self.track, "summary": summary, "flight": None}
        if self.took_off:
            end_t = self.land_t if self.land_t is not None else (self.last_air_t or self.takeoff_t)
            dur = max(1.0, end_t - self.takeoff_t)
            summary["duration_s"] = round(dur, 1)
            if self.manual_override:
                summary["manual_override"] = True
            body["flight"] = {"start": iso(end_wall - dur), "end": iso(end_wall),
                              "imu": {"rate_hz": IMU_HZ, "t0_s": 0, "accel_g": self.imu} if self.imu else None,
                              "events": []}
        return body
