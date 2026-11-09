"""Интерфейс борта для агента. Реализации: RosVehicle (MAVROS + PX4, Technic simple_offboard) и SimVehicle."""
from __future__ import annotations


class VehicleError(Exception):
    """Отказ борта с понятной оператору причиной (её видно в интерфейсе tbo-tracker)."""


class Vehicle:
    executor = "?"

    def open(self):
        """Подключиться (подписки, сервисы). Может ждать полётный контроллер."""

    def close(self):
        pass

    def info(self) -> dict:
        return {}

    def now(self) -> float:
        """Монотонное время борта, с (в симуляторе — ускоренное)."""
        raise NotImplementedError

    def time_scale(self) -> float:
        return 1.0

    def snapshot(self) -> dict:
        """connected, armed, mode, in_air, lat, lon, alt_rel, heading, speed, climb, battery_pct, battery_v, gps_fix,
        sats, home (lat, lon) | None, wp_current, wp_total, wp_reached, status_text, phase."""
        raise NotImplementedError

    def drain_imu(self) -> list:
        """[(t, |a| в g)] — накопленные отсчёты акселерометра с прошлого вызова."""
        return []

    def extra_checks(self, plan: dict) -> list:
        """Проверки, которые знает только этот борт: параметры PX4, другие клиенты управления и т. п.
        [{"name", "ok", "detail", "warn"?}] — warn=True не запрещает старт, только предупреждает."""
        return []

    def place_for(self, plan: dict):
        """Перед проверками подготовки. Реальный борт стоит там, где стоит; симулятор ставится в точку взлёта."""

    def upload(self, plan: dict, position: dict) -> str:
        """Загрузить план; position — текущие координаты борта (точка взлёта). Возвращает описание."""
        raise NotImplementedError

    def verify(self, plan: dict):
        """Перед стартом: план в контроллере не изменён (например, из QGroundControl). Иначе VehicleError."""

    def clear(self):
        """Снять план (отмена подготовки)."""

    def start_mission(self, plan: dict) -> str:
        """Взлёт и выполнение плана. Возвращается, когда моторы включены; иначе VehicleError с причиной."""
        raise NotImplementedError

    def rtl(self) -> str:
        raise NotImplementedError

    def land(self) -> str:
        raise NotImplementedError
