"""Расчёт наработки (часы / циклы / полёты) из журнала полётов и истории установок."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ..models import Aircraft, AircraftUsageEntry, Component, ComponentUsageEntry, Flight, Installation


@dataclass
class Usage:
    hours: float = 0.0
    cycles: int = 0
    flights: int = 0

    def as_dict(self):
        return {"hours": round(self.hours, 2), "cycles": int(self.cycles), "flights": int(self.flights)}


def _install_join():
    """Полёт засчитывается узлу, если в момент начала полёта узел был на этом борту."""
    return and_(
        Flight.aircraft_id == Installation.aircraft_id,
        Flight.started_at >= Installation.installed_at,
        or_(Installation.removed_at.is_(None), Flight.started_at < Installation.removed_at),
    )


def _time_filters(q, since: datetime | None, before: datetime | None):
    if since is not None:
        q = q.where(Flight.started_at >= since)
    if before is not None:
        q = q.where(Flight.started_at < before)
    return q


def component_usage(
    s: Session,
    component_ids: list[int] | None = None,
    since: datetime | None = None,
    before: datetime | None = None,
    aircraft_id: int | None = None,
) -> dict[int, Usage]:
    q = (
        select(
            Installation.component_id,
            func.coalesce(func.sum(Flight.duration_h), 0.0),
            func.coalesce(func.sum(Flight.cycles), 0),
            func.count(Flight.id),
        )
        .join(Flight, _install_join())
        .group_by(Installation.component_id)
    )
    if component_ids is not None:
        q = q.where(Installation.component_id.in_(component_ids))
    if aircraft_id is not None:
        q = q.where(Installation.aircraft_id == aircraft_id)
    q = _time_filters(q, since, before)
    out = {cid: Usage(h, c, n) for cid, h, c, n in s.execute(q)}
    if aircraft_id is None:
        # внебортовая наработка (стенд, испытания, корректировки) — только самому узлу
        for cid, (h, c) in entry_usage(s, component_ids, since, before).items():
            u = out.setdefault(cid, Usage())
            u.hours += h
            u.cycles += c
    return out


def entry_usage(s: Session, component_ids=None, since=None, before=None) -> dict[int, tuple[float, int]]:
    q = select(
        ComponentUsageEntry.component_id,
        func.coalesce(func.sum(ComponentUsageEntry.hours), 0.0),
        func.coalesce(func.sum(ComponentUsageEntry.cycles), 0),
    ).group_by(ComponentUsageEntry.component_id)
    if component_ids is not None:
        q = q.where(ComponentUsageEntry.component_id.in_(component_ids))
    if since is not None:
        q = q.where(ComponentUsageEntry.started_at >= since)
    if before is not None:
        q = q.where(ComponentUsageEntry.started_at < before)
    return {cid: (h, c) for cid, h, c in s.execute(q)}


def installation_usage(s: Session, installation_ids: list[int]) -> dict[int, Usage]:
    """Наработка, набранная узлом за конкретный интервал установки («стоянку» на борту)."""
    if not installation_ids:
        return {}
    q = (
        select(
            Installation.id,
            func.coalesce(func.sum(Flight.duration_h), 0.0),
            func.coalesce(func.sum(Flight.cycles), 0),
            func.count(Flight.id),
        )
        .join(Flight, _install_join())
        .where(Installation.id.in_(installation_ids))
        .group_by(Installation.id)
    )
    return {iid: Usage(h, c, n) for iid, h, c, n in s.execute(q)}


def aircraft_usage(
    s: Session,
    aircraft_ids: list[int] | None = None,
    since: datetime | None = None,
    before: datetime | None = None,
) -> dict[int, Usage]:
    q = select(
        Flight.aircraft_id,
        func.coalesce(func.sum(Flight.duration_h), 0.0),
        func.coalesce(func.sum(Flight.cycles), 0),
        func.count(Flight.id),
    ).group_by(Flight.aircraft_id)
    if aircraft_ids is not None:
        q = q.where(Flight.aircraft_id.in_(aircraft_ids))
    q = _time_filters(q, since, before)
    out = {aid: Usage(h, c, n) for aid, h, c, n in s.execute(q)}
    # ручные начисления борту без полёта (формуляр, наземные прогоны): часы и циклы, полётов не прибавляют
    e = select(AircraftUsageEntry.aircraft_id, func.coalesce(func.sum(AircraftUsageEntry.hours), 0.0),
               func.coalesce(func.sum(AircraftUsageEntry.cycles), 0)).group_by(AircraftUsageEntry.aircraft_id)
    if aircraft_ids is not None:
        e = e.where(AircraftUsageEntry.aircraft_id.in_(aircraft_ids))
    if since is not None:
        e = e.where(AircraftUsageEntry.started_at >= since)
    if before is not None:
        e = e.where(AircraftUsageEntry.started_at < before)
    for aid, h, c in s.execute(e):
        u = out.setdefault(aid, Usage())
        u.hours += h
        u.cycles += c
    return out


def component_totals(s: Session, components: list[Component], before: datetime | None = None) -> dict[int, Usage]:
    """Полная (сквозная) наработка узла с начала эксплуатации = начальная + из журналов."""
    used = component_usage(s, [c.id for c in components], before=before)
    out = {}
    for c in components:
        u = used.get(c.id, Usage())
        out[c.id] = Usage(c.initial_hours + u.hours, c.initial_cycles + u.cycles, u.flights)
    return out


def aircraft_totals(s: Session, aircraft: list[Aircraft], before: datetime | None = None) -> dict[int, Usage]:
    used = aircraft_usage(s, [a.id for a in aircraft], before=before)
    out = {}
    for a in aircraft:
        u = used.get(a.id, Usage())
        out[a.id] = Usage(a.initial_hours + u.hours, a.initial_cycles + u.cycles, u.flights)
    return out


def entity_usage(s: Session, kind: str, entity_id: int, since=None, before=None) -> Usage:
    if kind == "aircraft":
        return aircraft_usage(s, [entity_id], since, before).get(entity_id, Usage())
    return component_usage(s, [entity_id], since, before).get(entity_id, Usage())


def installed_at(s: Session, aircraft_id: int, moment: datetime) -> list[Installation]:
    """Состав борта на заданный момент времени."""
    q = select(Installation).where(
        Installation.aircraft_id == aircraft_id,
        Installation.installed_at <= moment,
        or_(Installation.removed_at.is_(None), Installation.removed_at > moment),
    )
    return list(s.scalars(q))


def current_installation(s: Session, component_id: int) -> Installation | None:
    return s.scalar(
        select(Installation).where(Installation.component_id == component_id, Installation.removed_at.is_(None))
    )


def current_installations(s: Session) -> dict[int, Installation]:
    return {i.component_id: i for i in s.scalars(select(Installation).where(Installation.removed_at.is_(None)))}
