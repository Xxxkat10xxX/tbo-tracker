"""Схемы входных данных API (pydantic)."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel as _PydanticModel
from pydantic import ConfigDict, Field


class BaseModel(_PydanticModel):
    """Общая база входных схем: NaN и бесконечность в числах не принимаются (иначе ломают расчёты и JSON)."""
    model_config = ConfigDict(allow_inf_nan=False)


class AircraftIn(BaseModel):
    serial: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    type_id: int
    status: Literal["active", "maintenance", "storage", "retired"] = "active"
    operator: str | None = None
    base: str | None = None
    in_service_at: datetime | None = None
    initial_hours: float = Field(0, ge=0)
    initial_cycles: int = Field(0, ge=0)
    notes: str | None = None
    resource_hours: float | None = Field(None, gt=0)
    resource_cycles: int | None = Field(None, gt=0)
    resource_days: int | None = Field(None, gt=0)


class AircraftPatch(BaseModel):
    name: str | None = None
    status: Literal["active", "maintenance", "storage", "retired"] | None = None
    operator: str | None = None
    base: str | None = None
    notes: str | None = None
    resource_hours: float | None = Field(None, gt=0)
    resource_cycles: int | None = Field(None, gt=0)
    resource_days: int | None = Field(None, gt=0)


class SpecIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    value: str = Field("", max_length=300)


class ComponentIn(BaseModel):
    serial: str = Field(min_length=1, max_length=100)
    type_id: int
    model: str | None = None
    part_number: str | None = None
    manufacturer: str | None = None
    firmware: str | None = None
    status: Literal["stock", "maintenance"] = "stock"
    manufactured_at: datetime | None = None
    in_service_at: datetime | None = None
    initial_hours: float = Field(0, ge=0)
    initial_cycles: int = Field(0, ge=0)
    notes: str | None = None
    resource_hours: float | None = Field(None, gt=0)
    resource_cycles: int | None = Field(None, gt=0)
    resource_days: int | None = Field(None, gt=0)
    specs: list[SpecIn] = Field(default_factory=list, max_length=40)


class ComponentPatch(BaseModel):
    model: str | None = None
    part_number: str | None = None
    manufacturer: str | None = None
    manufactured_at: datetime | None = None
    specs: list[SpecIn] | None = Field(None, max_length=40)
    firmware: str | None = None
    notes: str | None = None
    status: Literal["stock", "maintenance", "retired"] | None = None
    resource_hours: float | None = Field(None, gt=0)
    resource_cycles: int | None = Field(None, gt=0)
    resource_days: int | None = Field(None, gt=0)


class InstallIn(BaseModel):
    aircraft_id: int
    position: str
    at: datetime | None = None
    reason: str | None = None
    performed_by: str | None = None


class RemoveIn(BaseModel):
    at: datetime | None = None
    to_status: Literal["stock", "maintenance", "retired"] = "stock"
    reason: str | None = None
    performed_by: str | None = None


class RuleIn(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=300)
    description: str | None = None
    target: Literal["aircraft", "component"]
    aircraft_type_id: int | None = None
    component_type_id: int | None = None
    kind: Literal["periodic", "life"] = "periodic"
    interval_hours: float | None = Field(None, gt=0)
    interval_cycles: int | None = Field(None, gt=0)
    interval_days: int | None = Field(None, gt=0)
    interval_events: int | None = Field(None, gt=0)
    warn_hours: float | None = Field(None, ge=0)
    warn_cycles: int | None = Field(None, ge=0)
    warn_days: int | None = Field(None, ge=0)
    warn_events: int | None = Field(None, ge=0)
    active: bool = True


class MaintenanceIn(BaseModel):
    target: Literal["aircraft", "component"]
    entity_id: int
    rule_id: int | None = None
    performed_at: datetime | None = None
    performed_by: str | None = None
    work_type: Literal["scheduled", "unscheduled", "repair", "inspection"] = "scheduled"
    description: str | None = None
    result: str | None = None
    new_status: str | None = None


class ImageIn(BaseModel):
    image: str | None = Field(None, max_length=300)


class ImageTitleIn(BaseModel):
    url: str = Field(max_length=300)
    title: str = Field(min_length=1, max_length=80)


class EconomicsIn(BaseModel):
    failure_probability: float = Field(ge=0, le=1)
    failure_cost: float = Field(ge=0)
    downtime_days_per_failure: float = Field(ge=0)
    downtime_cost_per_day: float = Field(ge=0)
    manual_minutes_per_flight: float = Field(ge=0)
    manual_minutes_per_operation: float = Field(ge=0)
    engineer_rate: float = Field(ge=0)


class ManualFlightIn(BaseModel):
    aircraft_id: int
    start: datetime
    end: datetime
    cycles: int = Field(1, ge=0, le=1000)
    mission: str | None = None
    pilot: str | None = None
    flight_id: str | None = Field(None, max_length=200)
    component_id: int | None = None   # если полёт вносится из карточки узла — проверим, что узел стоял на борту
    performed_by: str | None = None


class UsageEntryIn(BaseModel):
    started_at: datetime | None = None
    hours: float = Field(0, ge=0)
    cycles: int = Field(0, ge=0)
    kind: Literal["bench", "test", "correction"] = "bench"
    note: str | None = None
    created_by: str | None = None


class AircraftUsageIn(BaseModel):
    """Ручное начисление борту часов и циклов (износа) без полёта."""
    started_at: datetime | None = None
    hours: float = Field(0, ge=0)
    cycles: int = Field(0, ge=0)
    kind: Literal["correction", "ground", "test"] = "correction"
    note: str | None = Field(None, max_length=500)
    created_by: str | None = Field(None, max_length=200)
    to_components: bool = False             # то же — узлам, стоявшим на борту в эту дату


class ComponentTypeIn(BaseModel):
    code: str = Field(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9_\-]+$")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    spec_fields: list[str] = Field(default_factory=list, max_length=40)


class SlotIn(BaseModel):
    position: str = Field(min_length=1, max_length=50)
    label: str = Field(min_length=1, max_length=200)
    component_type: str
    required: bool = True   # обязательная позиция: без узла борт «некомплектен» и не допущен


class AircraftTypeIn(BaseModel):
    code: str = Field(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9_\-]+$")
    name: str = Field(min_length=1, max_length=200)
    category: str = "multirotor"
    manufacturer: str | None = None
    slots: list[SlotIn] = Field(default_factory=list)


class GeneratorIn(BaseModel):
    aircraft_serials: list[str] = Field(min_length=1)
    date_from: datetime
    date_to: datetime
    format: Literal["json", "csv"] = "json"
    fly_probability: float = Field(0.8, ge=0, le=1)
    flights_min: int = Field(1, ge=0, le=20)
    flights_max: int = Field(4, ge=0, le=20)
    duration_min: int = Field(15, ge=1, le=600)
    duration_max: int = Field(45, ge=1, le=600)
    first_takeoff_hour: int = Field(6, ge=0, le=23)
    touch_and_go_probability: float = Field(0.05, ge=0, le=1)
    with_ids: bool = True
    avoid_overlap: bool = True
    duplicates: int = Field(0, ge=0, le=500)
    errors: bool = False
    seed: int | None = None
    import_now: bool = False
    dry_run: bool = False


class WaypointIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    alt: float = Field(ge=0.5, le=500)          # нижняя граница по типу навигации — autopilot.validate
    hold_s: float = Field(0, ge=0, le=600)
    # курс в точке, ° по часовой от «верха» карты: GPS — от севера, локально — от носа дрона при взлёте; None — не задан
    yaw_deg: float | None = Field(None, ge=0, le=360)


class MissionIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    aircraft_id: int | None = None
    home_lat: float = Field(ge=-90, le=90)
    home_lon: float = Field(ge=-180, le=180)
    takeoff_alt: float = Field(30, ge=0.5, le=500)
    speed_ms: float = Field(8, ge=0.2, le=40)
    return_home: bool = True
    # global — точки по GPS (широта/долгота); local — метры от точки взлёта (Optical Flow / ArUco, без GPS)
    frame: Literal["global", "local"] = "global"
    # курс дрона: keep — не меняется (поворот только в точках с курсом), route — нос по линии маршрута
    yaw_mode: Literal["keep", "route"] = "keep"
    waypoints: list[WaypointIn] = Field(default_factory=list, max_length=200)
    notes: str | None = None


class MissionEstimateIn(MissionIn):
    name: str = "—"


class RoutePatternIn(BaseModel):
    """Шаблон маршрута (services/routegen): участок и камера в метрах от точки взлёта."""
    kind: Literal["survey", "crosshatch", "perimeter", "orbit"] = "survey"
    home_lat: float = Field(ge=-90, le=90)
    home_lon: float = Field(ge=-180, le=180)
    frame: Literal["global", "local"] = "global"
    takeoff_alt: float = Field(30, ge=0.5, le=500)
    speed_ms: float = Field(5, ge=0.2, le=40)
    return_home: bool = True
    center_x: float = Field(0, ge=-20000, le=20000)     # вправо (восток) от точки взлёта, м
    center_y: float = Field(0, ge=-20000, le=20000)     # вперёд (север), м
    width: float = Field(10, ge=0, le=20000)            # поперёк галсов, м
    length: float = Field(10, ge=0, le=20000)           # вдоль галсов, м
    angle_deg: float = Field(0, ge=-360, le=360)        # направление галсов, ° по часовой от «верха» карты
    alt: float = Field(ge=0.5, le=500)
    fov_across_deg: float = Field(88, ge=1, le=170)
    fov_along_deg: float = Field(72, ge=1, le=170)
    image_px_across: int | None = Field(None, ge=16, le=20000)
    side_overlap: float = Field(0.6, ge=0, le=0.95)
    front_overlap: float = Field(0.7, ge=0, le=0.95)
    photo_points: bool = False
    photo_hold_s: float = Field(1, ge=0, le=60)
    radius: float = Field(5, ge=0, le=20000)
    points: int = Field(12, ge=3, le=72)
    direction: Literal["cw", "ccw"] = "cw"
    laps: int = Field(1, ge=1, le=20)
    face_center: bool = True


class MissionRunIn(BaseModel):
    aircraft_id: int | None = None
    wind_ms: float = Field(3, ge=0, le=20)
    wind_from_deg: float = Field(250, ge=0, le=360)
    fault_at_wp: int | None = Field(None, ge=1, le=200)
