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
    waypoints: list[WaypointIn] = Field(default_factory=list, max_length=200)
    notes: str | None = None


class MissionEstimateIn(MissionIn):
    name: str = "—"


class MissionRunIn(BaseModel):
    aircraft_id: int | None = None
    wind_ms: float = Field(3, ge=0, le=20)
    wind_from_deg: float = Field(250, ge=0, le=360)
    fault_at_wp: int | None = Field(None, ge=1, le=200)
