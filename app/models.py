"""Модель данных.

Ключевая идея: счётчики наработки НЕ хранятся как изменяемые числа.
Они вычисляются из двух неизменяемых журналов:
  * flights        — факты эксплуатации, загруженные из журналов;
  * installations  — интервалы «узел X стоял на борту Y в позиции P с .. по ..».
Наработка узла = начальная наработка + сумма полётов тех бортов, на которых
узел числился установленным в момент начала полёта. Поэтому перестановки,
поздний импорт старых журналов и откат импорта всегда дают согласованный
результат, а двойное начисление исключено уникальными ключами полётов.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .timeutil import utcnow

# ---- справочники статусов ---------------------------------------------------
AIRCRAFT_STATUSES = {
    "active": "В эксплуатации",
    "maintenance": "На обслуживании",
    "storage": "На хранении",
    "retired": "Списан",
}
COMPONENT_STATUSES = {
    "installed": "Установлен",
    "stock": "Резерв / склад",
    "maintenance": "На обслуживании / ремонте",
    "retired": "Списан",
}


class AircraftType(Base):
    __tablename__ = "aircraft_types"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(50), default="multirotor")
    manufacturer: Mapped[str | None] = mapped_column(String(200))
    # Конфигурация посадочных мест: [{"position","label","component_type"}]
    slots: Mapped[list] = mapped_column(JSON, default=list)


class ComponentType(Base):
    __tablename__ = "component_types"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    # Шаблон характеристик паспорта для узлов этого типа (названия полей)
    spec_fields: Mapped[list] = mapped_column(JSON, default=list)


class Aircraft(Base):
    __tablename__ = "aircraft"
    id: Mapped[int] = mapped_column(primary_key=True)
    serial: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    type_id: Mapped[int] = mapped_column(ForeignKey("aircraft_types.id"))
    status: Mapped[str] = mapped_column(String(20), default="active")
    operator: Mapped[str | None] = mapped_column(String(200))
    base: Mapped[str | None] = mapped_column(String(200))
    in_service_at: Mapped[datetime] = mapped_column(DateTime)
    initial_hours: Mapped[float] = mapped_column(Float, default=0.0)
    initial_cycles: Mapped[int] = mapped_column(Integer, default=0)
    # Индивидуальный ресурс экземпляра (максимальный износ по паспорту/формуляру). Пусто — не задан.
    resource_hours: Mapped[float | None] = mapped_column(Float)
    resource_cycles: Mapped[int | None] = mapped_column(Integer)
    resource_days: Mapped[int | None] = mapped_column(Integer)
    # Картинка борта: иллюстрация из набора (/static/drones/…) или загруженное фото (/media/…)
    image: Mapped[str | None] = mapped_column(String(300))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    type: Mapped[AircraftType] = relationship(lazy="joined")


class Component(Base):
    __tablename__ = "components"
    id: Mapped[int] = mapped_column(primary_key=True)
    serial: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    type_id: Mapped[int] = mapped_column(ForeignKey("component_types.id"))
    # Уникальный идентификатор электронного паспорта (не меняется при перемаркировке/перестановках)
    uid: Mapped[str] = mapped_column(String(40), default=lambda: uuid.uuid4().hex, index=True)
    model: Mapped[str | None] = mapped_column(String(200))
    part_number: Mapped[str | None] = mapped_column(String(100))
    # Основные технические характеристики: [{"name": "Ток", "value": "40 А"}, ...]
    specs: Mapped[list] = mapped_column(JSON, default=list)
    manufacturer: Mapped[str | None] = mapped_column(String(200))
    firmware: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="stock")
    manufactured_at: Mapped[datetime | None] = mapped_column(DateTime)
    in_service_at: Mapped[datetime] = mapped_column(DateTime)
    # Наработка до постановки на учёт (стендовые испытания, другой парк)
    initial_hours: Mapped[float] = mapped_column(Float, default=0.0)
    initial_cycles: Mapped[int] = mapped_column(Integer, default=0)
    # Индивидуальный ресурс экземпляра (максимальный износ по паспорту/формуляру). Пусто — не задан.
    resource_hours: Mapped[float | None] = mapped_column(Float)
    resource_cycles: Mapped[int | None] = mapped_column(Integer)
    resource_days: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    type: Mapped[ComponentType] = relationship(lazy="joined")


class Installation(Base):
    """Интервал нахождения узла на борту. removed_at IS NULL — установлен сейчас."""

    __tablename__ = "installations"
    id: Mapped[int] = mapped_column(primary_key=True)
    component_id: Mapped[int] = mapped_column(ForeignKey("components.id"), index=True)
    aircraft_id: Mapped[int] = mapped_column(ForeignKey("aircraft.id"), index=True)
    position: Mapped[str] = mapped_column(String(50))
    installed_at: Mapped[datetime] = mapped_column(DateTime)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime)
    install_reason: Mapped[str | None] = mapped_column(Text)
    remove_reason: Mapped[str | None] = mapped_column(Text)
    installed_by: Mapped[str | None] = mapped_column(String(200))
    removed_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    component: Mapped[Component] = relationship(lazy="joined")
    aircraft: Mapped[Aircraft] = relationship(lazy="joined")

    __table_args__ = (
        CheckConstraint("removed_at IS NULL OR removed_at > installed_at", name="ck_install_interval"),
        Index("ix_install_aircraft_time", "aircraft_id", "installed_at", "removed_at"),
        # Гарантия БД: узел установлен не более чем в одном месте, позиция занята не более чем одним узлом.
        # Проверки в коде («прочитал — записал») не защищают от двух одновременных запросов (двойной щелчок):
        # иначе появились бы два открытых интервала, и полёты начислялись бы узлу дважды.
        Index("ux_install_open_component", "component_id", unique=True,
              sqlite_where=text("removed_at IS NULL"), postgresql_where=text("removed_at IS NULL")),
        Index("ux_install_open_position", "aircraft_id", "position", unique=True,
              sqlite_where=text("removed_at IS NULL"), postgresql_where=text("removed_at IS NULL")),
    )


class LogImport(Base):
    __tablename__ = "log_imports"
    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(300))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    format: Mapped[str | None] = mapped_column(String(50))
    # imported | partial | duplicate | rejected | rolled_back
    status: Mapped[str] = mapped_column(String(20))
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    imported_by: Mapped[str | None] = mapped_column(String(200))
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("log_imports.id"))
    flights_total: Mapped[int] = mapped_column(Integer, default=0)
    flights_new: Mapped[int] = mapped_column(Integer, default=0)
    flights_duplicate: Mapped[int] = mapped_column(Integer, default=0)
    flights_rejected: Mapped[int] = mapped_column(Integer, default=0)
    hours_added: Mapped[float] = mapped_column(Float, default=0.0)
    cycles_added: Mapped[int] = mapped_column(Integer, default=0)
    messages: Mapped[list] = mapped_column(JSON, default=list)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)


class Flight(Base):
    __tablename__ = "flights"
    id: Mapped[int] = mapped_column(primary_key=True)
    aircraft_id: Mapped[int] = mapped_column(ForeignKey("aircraft.id"))
    import_id: Mapped[int | None] = mapped_column(ForeignKey("log_imports.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(200))
    started_at: Mapped[datetime] = mapped_column(DateTime)
    ended_at: Mapped[datetime] = mapped_column(DateTime)
    duration_h: Mapped[float] = mapped_column(Float)
    cycles: Mapped[int] = mapped_column(Integer, default=1)
    mission: Mapped[str | None] = mapped_column(String(300))
    pilot: Mapped[str | None] = mapped_column(String(200))

    aircraft: Mapped[Aircraft] = relationship(lazy="joined")
    events: Mapped[list["SensorEvent"]] = relationship(lazy="selectin", cascade="all, delete-orphan", passive_deletes=True)

    __table_args__ = (
        # Защита от двойного начисления на уровне БД
        UniqueConstraint("aircraft_id", "external_id", name="uq_flight_external"),
        Index("ix_flight_aircraft_start", "aircraft_id", "started_at"),
        CheckConstraint("ended_at > started_at", name="ck_flight_interval"),
    )


class MaintenanceRule(Base):
    """Регламент ТО. Срабатывает по первому достигнутому порогу (часы / циклы / дни)."""

    __tablename__ = "maintenance_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    target: Mapped[str] = mapped_column(String(20))  # aircraft | component
    aircraft_type_id: Mapped[int | None] = mapped_column(ForeignKey("aircraft_types.id"))
    component_type_id: Mapped[int | None] = mapped_column(ForeignKey("component_types.id"))
    # periodic — межсервисный интервал, обнуляется выполнением работы;
    # life — назначенный ресурс с начала эксплуатации, не обнуляется.
    kind: Mapped[str] = mapped_column(String(20), default="periodic")
    interval_hours: Mapped[float | None] = mapped_column(Float)
    interval_cycles: Mapped[int | None] = mapped_column(Integer)
    interval_days: Mapped[int | None] = mapped_column(Integer)
    # Число падений/столкновений с последнего выполнения (мониторинг датчиков)
    interval_events: Mapped[int | None] = mapped_column(Integer)
    warn_hours: Mapped[float | None] = mapped_column(Float)
    warn_cycles: Mapped[int | None] = mapped_column(Integer)
    warn_days: Mapped[int | None] = mapped_column(Integer)
    warn_events: Mapped[int | None] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    aircraft_type: Mapped[AircraftType | None] = relationship(lazy="joined")
    component_type: Mapped[ComponentType | None] = relationship(lazy="joined")


class MaintenanceRecord(Base):
    __tablename__ = "maintenance_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    target: Mapped[str] = mapped_column(String(20))  # aircraft | component
    aircraft_id: Mapped[int | None] = mapped_column(ForeignKey("aircraft.id"), index=True)
    component_id: Mapped[int | None] = mapped_column(ForeignKey("components.id"), index=True)
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("maintenance_rules.id"), index=True)
    work_type: Mapped[str] = mapped_column(String(30), default="scheduled")
    performed_at: Mapped[datetime] = mapped_column(DateTime)
    performed_by: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    result: Mapped[str | None] = mapped_column(Text)
    # Снимок счётчиков на момент работы (для отчётности; расчёт интервалов идёт по времени)
    hours_at: Mapped[float | None] = mapped_column(Float)
    cycles_at: Mapped[int | None] = mapped_column(Integer)
    # Где стоял узел в момент работы
    on_aircraft_id: Mapped[int | None] = mapped_column(ForeignKey("aircraft.id"))
    status_after: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    rule: Mapped[MaintenanceRule | None] = relationship(lazy="joined")
    aircraft: Mapped[Aircraft | None] = relationship(foreign_keys=[aircraft_id], lazy="joined")
    component: Mapped[Component | None] = relationship(lazy="joined")
    on_aircraft: Mapped[Aircraft | None] = relationship(foreign_keys=[on_aircraft_id], lazy="joined")


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


class ComponentUsageEntry(Base):
    """Наработка узла вне борта: стендовые прогоны, испытания, ручная корректировка по формуляру.

    Начисляется только самому узлу (борт и другие узлы её не получают).
    """

    __tablename__ = "component_usage_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    component_id: Mapped[int] = mapped_column(ForeignKey("components.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime)
    hours: Mapped[float] = mapped_column(Float)
    cycles: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(30), default="bench")  # bench | test | correction
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    component: Mapped[Component] = relationship(lazy="joined")

    __table_args__ = (CheckConstraint("hours >= 0 AND cycles >= 0", name="ck_usage_entry_nonneg"),)


class SensorEvent(Base):
    """Падение или столкновение, выявленное анализом бортовых датчиков (акселерометр ИНС/ПК)
    или зафиксированное бортовым журналом. Привязано к полёту: откат импорта удаляет и события."""

    __tablename__ = "sensor_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    flight_id: Mapped[int] = mapped_column(ForeignKey("flights.id", ondelete="CASCADE"), index=True)
    aircraft_id: Mapped[int] = mapped_column(ForeignKey("aircraft.id"), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime)
    kind: Mapped[str] = mapped_column(String(20))           # fall | collision
    peak_g: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(20), default="imu")   # imu — анализ сырых данных, log — событие из журнала
    details: Mapped[str | None] = mapped_column(Text)


class MovementRow(Base):
    """Отпечатки применённых строк журналов движения узлов (Excel/PDF) — защита от повторного применения."""

    __tablename__ = "movement_rows"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    import_id: Mapped[int] = mapped_column(ForeignKey("log_imports.id"))


class Mission(Base):
    """Полётное задание: маршрут из точек (WGS-84, высота над точкой взлёта) для автономного полёта."""

    __tablename__ = "missions"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    aircraft_id: Mapped[int | None] = mapped_column(ForeignKey("aircraft.id"))
    home_lat: Mapped[float] = mapped_column(Float)
    home_lon: Mapped[float] = mapped_column(Float)
    takeoff_alt: Mapped[float] = mapped_column(Float, default=30.0)
    speed_ms: Mapped[float] = mapped_column(Float, default=8.0)
    return_home: Mapped[bool] = mapped_column(Boolean, default=True)
    # global — по GPS; local — метры от точки взлёта (в помещении: Optical Flow, ArUco). Точки хранятся как
    # широта/долгота вокруг «дома» в обоих случаях: редактор, симулятор и оценка маршрута общие.
    frame: Mapped[str | None] = mapped_column(String(10), default="global")
    # [{"lat": …, "lon": …, "alt": м, "hold_s": с}]
    waypoints: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    aircraft: Mapped[Aircraft | None] = relationship(lazy="joined")


class MissionRun(Base):
    """Выполнение миссии (автономный полёт в симуляторе): трек, итог и ссылка на учтённый полёт."""

    __tablename__ = "mission_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    mission_id: Mapped[int] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True)
    aircraft_id: Mapped[int] = mapped_column(ForeignKey("aircraft.id"))
    import_id: Mapped[int | None] = mapped_column(ForeignKey("log_imports.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime)
    ended_at: Mapped[datetime] = mapped_column(DateTime)
    # симулятор: completed | aborted; реальный борт: preparing → ready → in_flight → landed → completed | aborted,
    # а также failed (план не принят / сбой) и cancelled (подготовка отменена)
    status: Mapped[str] = mapped_column(String(20))
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    track: Mapped[list] = mapped_column(JSON, default=list)   # [[t, lat, lon, alt, v, фаза], …]
    source: Mapped[str | None] = mapped_column(String(20), default="sim")   # sim — симулятор, drone — реальный борт


class DroneLink(Base):
    """Связь с бортовым компьютером (агент tbo-agent): токен устройства, состояние, проверки и телеметрия борта."""

    __tablename__ = "drone_links"
    id: Mapped[int] = mapped_column(primary_key=True)
    aircraft_id: Mapped[int] = mapped_column(ForeignKey("aircraft.id", ondelete="CASCADE"), unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)     # SHA-256 токена; сам токен не хранится
    token_hint: Mapped[str] = mapped_column(String(12))                  # последние символы — узнать токен в конфиге борта
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_by: Mapped[str | None] = mapped_column(String(64))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_ip: Mapped[str | None] = mapped_column(String(64))
    agent: Mapped[dict] = mapped_column(JSON, default=dict)              # версия агента, исполнитель, автопилот
    state: Mapped[str] = mapped_column(String(20), default="offline")
    detail: Mapped[str | None] = mapped_column(String(500))
    checks: Mapped[list] = mapped_column(JSON, default=list)             # [{"name", "ok", "detail"}] — проверки на борту
    telemetry: Mapped[dict] = mapped_column(JSON, default=dict)
    telemetry_at: Mapped[datetime | None] = mapped_column(DateTime)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("mission_runs.id", ondelete="SET NULL"))

    aircraft: Mapped[Aircraft] = relationship(lazy="joined")


class DroneCommand(Base):
    """Команда борту. Доставляется агенту при опросе; просроченная не доставляется («старый» старт не выполнится)."""

    __tablename__ = "drone_commands"
    id: Mapped[int] = mapped_column(primary_key=True)
    link_id: Mapped[int] = mapped_column(ForeignKey("drone_links.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("mission_runs.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(20))           # prepare | start | rtl | land | cancel
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="queued")   # queued | delivered | done | failed | expired
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_by: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    result: Mapped[str | None] = mapped_column(String(500))


class NotificationEvent(Base):
    """Журнал уведомлений сервера: когда позиция появилась, когда закрылась, куда доставлена.

    Заполняется периодической проверкой на сервере (services/notify.py), поэтому уведомления
    формируются и отправляются (Telegram, webhook) даже когда ни один браузер не открыт.
    """

    __tablename__ = "notification_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(200), index=True)
    category: Mapped[str] = mapped_column(String(20))       # limit | overdue | approaching | sensor
    title: Mapped[str] = mapped_column(String(300))
    text: Mapped[str | None] = mapped_column(Text)
    href: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    silent: Mapped[bool] = mapped_column(Boolean, default=False)     # первичное заполнение — без рассылки
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime)
    delivery: Mapped[dict | None] = mapped_column(JSON)


# ---------------------------------------------------------------- доступ: пользователи, сессии, аудит
ROLES = {"admin": "Администратор", "engineer": "Инженер", "viewer": "Наблюдатель"}


class User(Base):
    """Учётная запись. Пароль хранится только как хеш Argon2id (с солью и параметрами внутри строки)."""

    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)     # в нижнем регистре
    display_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="viewer")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)
    lock_count: Mapped[int] = mapped_column(Integer, default=0)      # для удваивания срока блокировки
    created_at: Mapped[datetime] = mapped_column(DateTime)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime)


class AuthSession(Base):
    """Сессия входа. В базе хранится только SHA-256 от токена cookie — утечка базы не даёт войти."""

    __tablename__ = "auth_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    user: Mapped[User] = relationship(lazy="joined")


class AuditLog(Base):
    """Журнал безопасности и изменений: входы, учётные записи, все изменяющие запросы к API."""

    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, index=True)
    username: Mapped[str | None] = mapped_column(String(64), index=True)
    ip: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[int | None] = mapped_column(Integer)
    details: Mapped[str | None] = mapped_column(Text)
