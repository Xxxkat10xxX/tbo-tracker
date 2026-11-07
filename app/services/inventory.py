"""Движение узлов: установка, снятие, перестановка между бортами."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..config import FUTURE_TOLERANCE_MIN
from ..errors import DomainError
from ..models import COMPONENT_STATUSES, Aircraft, Component, Flight, Installation
from ..timeutil import utcnow
from .usage import current_installation


def slot_for(aircraft: Aircraft, position: str) -> dict | None:
    for slot in aircraft.type.slots or []:
        if slot["position"] == position:
            return slot
    return None


def _check_time(at: datetime, what: str):
    if at > utcnow() + timedelta(minutes=FUTURE_TOLERANCE_MIN):
        raise DomainError(f"Дата {what} не может быть в будущем")


def _flights_after(s: Session, aircraft_id: int, at: datetime, until: datetime | None = None) -> int:
    q = select(func.count(Flight.id)).where(Flight.aircraft_id == aircraft_id, Flight.started_at >= at)
    if until is not None:
        q = q.where(Flight.started_at < until)
    return s.scalar(q) or 0


def _check_install(s: Session, component: Component, aircraft: Aircraft, position: str, at: datetime):
    if aircraft.status == "retired":
        raise DomainError(f"Борт {aircraft.name} списан — установка невозможна")
    slot = slot_for(aircraft, position)
    if slot is None:
        raise DomainError(f"У типа {aircraft.type.name} нет посадочного места «{position}»")
    if slot["component_type"] != component.type.code:
        raise DomainError(
            f"Позиция «{slot['label']}» предназначена для типа «{slot['component_type']}», "
            f"а узел {component.serial} — «{component.type.code}»"
        )
    if at < component.in_service_at:
        raise DomainError("Дата установки раньше даты ввода узла в эксплуатацию")
    _check_time(at, "установки")
    overlap = s.scalar(
        select(Installation).where(
            Installation.component_id == component.id,
            or_(Installation.removed_at.is_(None), Installation.removed_at > at),
        )
    )
    if overlap is not None:
        raise DomainError(
            f"Узел {component.serial} числится на борту {overlap.aircraft.name} "
            f"после указанной даты — сначала снимите его или укажите более позднюю дату"
        )
    busy = s.scalar(
        select(Installation).where(
            Installation.aircraft_id == aircraft.id,
            Installation.position == position,
            or_(Installation.removed_at.is_(None), Installation.removed_at > at),
        )
    )
    if busy is not None:
        raise DomainError(
            f"Позиция «{slot['label']}» борта {aircraft.name} занята узлом {busy.component.serial} — сначала снимите его"
        )


def install(s: Session, component: Component, aircraft: Aircraft, position: str, at: datetime | None,
            reason: str | None = None, by: str | None = None) -> dict:
    at = at or utcnow()
    if component.status == "installed":
        inst = current_installation(s, component.id)
        raise DomainError(
            f"Узел {component.serial} уже установлен на борт {inst.aircraft.name if inst else '?'} — используйте перестановку"
        )
    if component.status in ("maintenance", "retired"):
        raise DomainError(
            f"Узел {component.serial} в статусе «{COMPONENT_STATUSES[component.status]}» — "
            "установка невозможна (сначала завершите обслуживание)"
        )
    _check_install(s, component, aircraft, position, at)
    inst = Installation(component_id=component.id, aircraft_id=aircraft.id, position=position,
                        installed_at=at, install_reason=reason, installed_by=by)
    s.add(inst)
    component.status = "installed"
    s.flush()
    retro = _flights_after(s, aircraft.id, at)
    msg = f"Узел {component.serial} установлен на борт {aircraft.name} ({position})"
    if retro:
        msg += f". Учтено уже загруженных полётов после даты установки: {retro}"
    return {"message": msg, "installation_id": inst.id, "retro_flights": retro}


def remove(s: Session, component: Component, at: datetime | None, to_status: str = "stock",
           reason: str | None = None, by: str | None = None) -> dict:
    at = at or utcnow()
    if to_status not in ("stock", "maintenance", "retired"):
        raise DomainError("Недопустимый статус после снятия")
    inst = current_installation(s, component.id)
    if inst is None:
        raise DomainError(f"Узел {component.serial} не установлен ни на один борт")
    if at <= inst.installed_at:
        raise DomainError("Дата снятия должна быть позже даты установки")
    _check_time(at, "снятия")
    retro = _flights_after(s, inst.aircraft_id, at)
    inst.removed_at = at
    inst.remove_reason = reason
    inst.removed_by = by
    component.status = to_status
    s.flush()
    msg = f"Узел {component.serial} снят с борта {inst.aircraft.name} → «{COMPONENT_STATUSES[to_status]}»"
    if retro:
        msg += f". Полёты борта после даты снятия ({retro}) узлу больше не начисляются"
    return {"message": msg, "installation_id": inst.id, "retro_flights": retro}


def transfer(s: Session, component: Component, aircraft: Aircraft, position: str, at: datetime | None,
             reason: str | None = None, by: str | None = None) -> dict:
    at = at or utcnow()
    inst = current_installation(s, component.id)
    if inst is None:
        raise DomainError(f"Узел {component.serial} не установлен — используйте операцию установки")
    if inst.aircraft_id == aircraft.id and inst.position == position:
        raise DomainError("Узел уже стоит в этой позиции")
    from_name = inst.aircraft.name
    remove(s, component, at, "stock", reason or f"Перестановка на борт {aircraft.name}", by)
    res = install(s, component, aircraft, position, at, reason or f"Перестановка с борта {from_name}", by)
    res["message"] = f"Узел {component.serial} переставлен: {from_name} → {aircraft.name} ({position}). " \
                     "Накопленная наработка и история сохранены за узлом"
    return res
