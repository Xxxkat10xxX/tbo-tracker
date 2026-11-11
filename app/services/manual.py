"""Ручной ввод: полёты (в состав борта) и внебортовая наработка узла (стенд, испытания, корректировки)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..config import FUTURE_TOLERANCE_MIN
from ..errors import DomainError
from ..models import Aircraft, AircraftUsageEntry, Component, ComponentUsageEntry, Flight, Installation, LogImport
from ..timeutil import iso, utcnow
from . import logs, usage

ENTRY_KINDS = {
    "bench": "Стендовый прогон",
    "test": "Испытания / проверка",
    "correction": "Корректировка по формуляру",
    "ground": "Наземный прогон",
}
AIRCRAFT_KINDS = ("correction", "ground", "test")


def installed_on(s: Session, component_id: int, moment: datetime) -> Installation | None:
    return s.scalar(select(Installation).where(
        Installation.component_id == component_id, Installation.installed_at <= moment,
        or_(Installation.removed_at.is_(None), Installation.removed_at > moment)))


def add_manual_flight(s: Session, aircraft: Aircraft, start: datetime, end: datetime, cycles: int = 1,
                      mission: str | None = None, pilot: str | None = None, flight_id: str | None = None,
                      component: Component | None = None, by: str | None = None) -> dict:
    """Полёт, записанный вручную, проходит тот же конвейер, что и журнал: валидация, защита от
    дублей и пересечений, начисление борту и всем узлам, установленным на момент взлёта."""
    if component is not None:
        inst = installed_on(s, component.id, start)
        if inst is None or inst.aircraft_id != aircraft.id:
            where = f"стоял на борту {inst.aircraft.name}" if inst else "не был установлен ни на один борт"
            raise DomainError(
                f"В момент взлёта узел {component.serial} {where}, а не на {aircraft.name}. "
                "Выберите правильный борт или запишите наработку «вне борта»")
    doc = {"format": logs.FORMAT_ID, "aircraft_serial": aircraft.serial, "source": "Ручной ввод",
           "flights": [{k: v for k, v in {"flight_id": flight_id, "start": iso(start), "end": iso(end), "cycles": cycles,
                                         "mission": mission, "pilot": pilot}.items() if v not in (None, "")}]}
    content = json.dumps(doc, ensure_ascii=False).encode()
    res = logs.import_log(s, content, f"Ручной ввод · {aircraft.name}", by or "ручной ввод")
    if res["flights_new"] != 1:
        raise DomainError("Полёт не записан: " + "; ".join(res["messages"] or ["дубликат уже учтённого полёта"]))
    imp = s.get(LogImport, res["id"])
    imp.format = "manual"
    s.flush()
    res["format"] = "manual"
    res["message"] = (f"Полёт записан: {aircraft.name}, {res['hours_added']} ч, {res['cycles_added']} цикл.; "
                      f"начислено узлам: {len(res['summary'].get('components', []))}")
    return res


def delete_flight(s: Session, flight: Flight) -> dict:
    imp = s.get(LogImport, flight.import_id) if flight.import_id else None
    if imp is None or imp.format != "manual":
        raise DomainError("Полёт загружен из журнала — отмените импорт журнала целиком (раздел «Импорт журналов»)")
    res = logs.rollback_import(s, imp)
    return {**res, "message": "Ручной полёт удалён, наработка списана с борта и узлов"}


def add_usage_entry(s: Session, component: Component, started_at: datetime | None, hours: float, cycles: int,
                    kind: str, note: str | None, by: str | None) -> ComponentUsageEntry:
    started_at = started_at or utcnow()
    if kind not in ENTRY_KINDS:
        raise DomainError("Неизвестный вид записи")
    if hours < 0 or cycles < 0 or (hours == 0 and cycles == 0):
        raise DomainError("Укажите часы и/или циклы (неотрицательные, хотя бы одно больше нуля)")
    if hours > 1000 or cycles > 100000:
        raise DomainError("Слишком большое значение — проверьте ввод")
    if started_at > utcnow() + timedelta(minutes=FUTURE_TOLERANCE_MIN):
        raise DomainError("Дата не может быть в будущем")
    if started_at < component.in_service_at and kind != "correction":
        raise DomainError("Дата раньше постановки узла на учёт")
    if kind != "correction":
        inst = installed_on(s, component.id, started_at)
        if inst is not None:
            raise DomainError(f"В это время узел стоял на борту {inst.aircraft.name} — запишите полёт этого борта "
                              "(или используйте «Корректировку по формуляру»)")
    e = ComponentUsageEntry(component_id=component.id, started_at=started_at, hours=round(hours, 4), cycles=cycles,
                            kind=kind, note=note, created_by=by)
    s.add(e)
    s.flush()
    return e


def entry_dict(e: ComponentUsageEntry) -> dict:
    return {"id": e.id, "component_id": e.component_id, "started_at": iso(e.started_at), "hours": round(e.hours, 3),
            "cycles": e.cycles, "kind": e.kind, "kind_label": ENTRY_KINDS.get(e.kind, e.kind), "note": e.note,
            "created_by": e.created_by, "aircraft_entry_id": e.aircraft_entry_id}


def _check_amounts(hours: float, cycles: int, started_at: datetime):
    if hours < 0 or cycles < 0 or (hours == 0 and cycles == 0):
        raise DomainError("Укажите часы и/или циклы (неотрицательные, хотя бы одно больше нуля)")
    if hours > 100000 or cycles > 1000000:
        raise DomainError("Слишком большое значение — проверьте ввод")
    if started_at > utcnow() + timedelta(minutes=FUTURE_TOLERANCE_MIN):
        raise DomainError("Дата не может быть в будущем")


def add_aircraft_entry(s: Session, aircraft: Aircraft, started_at: datetime | None, hours: float, cycles: int, kind: str,
                       note: str | None, by: str | None, to_components: bool = False) -> tuple[AircraftUsageEntry, int]:
    """Начислить борту часы и циклы (износ) без полёта. to_components — то же каждому узлу, стоявшему на борту в эту
    дату (связанные записи узлов удаляются вместе с записью борта). Возвращает (запись, число узлов)."""
    started_at = started_at or utcnow()
    if kind not in AIRCRAFT_KINDS:
        raise DomainError("Неизвестный вид записи")
    _check_amounts(hours, cycles, started_at)
    if started_at < aircraft.in_service_at and kind != "correction":
        raise DomainError("Дата раньше постановки борта на учёт (для переноса старой наработки — «Корректировка по формуляру»)")
    e = AircraftUsageEntry(aircraft_id=aircraft.id, started_at=started_at, hours=round(hours, 4), cycles=cycles, kind=kind,
                           note=note, created_by=by)
    s.add(e)
    s.flush()
    n = 0
    if to_components:
        for inst in usage.installed_at(s, aircraft.id, started_at):
            s.add(ComponentUsageEntry(component_id=inst.component_id, started_at=started_at, hours=e.hours, cycles=cycles,
                                      kind=kind, note=f"с борта {aircraft.name}" + (f": {note}" if note else ""),
                                      created_by=by, aircraft_entry_id=e.id))
            n += 1
        s.flush()
    return e, n


def delete_aircraft_entry(s: Session, e: AircraftUsageEntry) -> int:
    """Удалить начисление борту и связанные записи узлов. Возвращает число снятых записей узлов."""
    linked = list(s.scalars(select(ComponentUsageEntry).where(ComponentUsageEntry.aircraft_entry_id == e.id)))
    for x in linked:
        s.delete(x)
    s.delete(e)
    s.flush()
    return len(linked)


def aircraft_entry_dict(s: Session, e: AircraftUsageEntry) -> dict:
    n = s.scalar(select(func.count(ComponentUsageEntry.id)).where(ComponentUsageEntry.aircraft_entry_id == e.id)) or 0
    return {"id": e.id, "aircraft_id": e.aircraft_id, "started_at": iso(e.started_at), "hours": round(e.hours, 3),
            "cycles": e.cycles, "kind": e.kind, "kind_label": ENTRY_KINDS.get(e.kind, e.kind), "note": e.note,
            "created_by": e.created_by, "components": n}
