"""Схемы входных данных API (pydantic)."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


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


class AircraftPatch(BaseModel):
    name: str | None = None
    status: Literal["active", "maintenance", "storage", "retired"] | None = None
    operator: str | None = None
    base: str | None = None
    notes: str | None = None


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


class ComponentPatch(BaseModel):
    model: str | None = None
    firmware: str | None = None
    notes: str | None = None
    status: Literal["stock", "maintenance", "retired"] | None = None


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
    warn_hours: float | None = Field(None, ge=0)
    warn_cycles: int | None = Field(None, ge=0)
    warn_days: int | None = Field(None, ge=0)
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


class EconomicsIn(BaseModel):
    failure_probability: float = Field(ge=0, le=1)
    failure_cost: float = Field(ge=0)
    downtime_days_per_failure: float = Field(ge=0)
    downtime_cost_per_day: float = Field(ge=0)
    manual_minutes_per_flight: float = Field(ge=0)
    manual_minutes_per_operation: float = Field(ge=0)
    engineer_rate: float = Field(ge=0)
