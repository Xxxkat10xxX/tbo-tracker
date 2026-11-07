"""Регламенты ТО: расчёт остатка до работы, список предстоящих/просроченных работ, фиксация выполнения.

Логика намеренно прозрачна (требование первого этапа):
  * для периодической работы отсчёт ведётся от даты последнего выполнения этой
    работы (или от ввода в эксплуатацию, если работа ещё не выполнялась);
  * для назначенного ресурса — от начала эксплуатации, выполнение работ его не обнуляет;
  * работа «подходит», если остаток по любому счётчику <= порогу предупреждения
    (по умолчанию 10 % интервала), и «требуется/просрочена», если остаток <= 0;
  * прогноз даты — линейный, по средней интенсивности за последние 30 дней.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..config import FORECAST_WINDOW_DAYS
from ..errors import DomainError, NotFound
from ..models import (
    AIRCRAFT_STATUSES,
    COMPONENT_STATUSES,
    Aircraft,
    Component,
    MaintenanceRecord,
    MaintenanceRule,
)
from ..timeutil import iso, utcnow
from .usage import Usage, aircraft_totals, component_totals, current_installations, entity_usage

STATUS_ORDER = {"overdue": 0, "due_soon": 1, "ok": 2}
METRIC_LABELS = {"hours": "ч", "cycles": "цикл.", "days": "дн."}


def applicable_rules(rules: list[MaintenanceRule], kind: str, entity) -> list[MaintenanceRule]:
    out = []
    for r in rules:
        if not r.active or r.target != kind:
            continue
        if kind == "aircraft" and r.aircraft_type_id not in (None, entity.type_id):
            continue
        if kind == "component" and r.component_type_id not in (None, entity.type_id):
            continue
        out.append(r)
    return out


def last_record(s: Session, kind: str, entity_id: int, rule_id: int, before: datetime | None = None):
    col = MaintenanceRecord.aircraft_id if kind == "aircraft" else MaintenanceRecord.component_id
    q = select(MaintenanceRecord).where(
        MaintenanceRecord.target == kind, col == entity_id, MaintenanceRecord.rule_id == rule_id
    )
    if before is not None:
        q = q.where(MaintenanceRecord.performed_at <= before)
    return s.scalar(q.order_by(MaintenanceRecord.performed_at.desc()).limit(1))


class Evaluator:
    """Считает состояние «объект × регламент». Кэширует интенсивность и итоги."""

    def __init__(self, s: Session, now: datetime | None = None):
        self.s = s
        self.now = now or utcnow()
        self._rate: dict[tuple, Usage] = {}
        self._totals: dict[tuple, Usage] = {}
        self._location = None

    def totals(self, kind, entity) -> Usage:
        key = (kind, entity.id)
        if key not in self._totals:
            fn = aircraft_totals if kind == "aircraft" else component_totals
            self._totals[key] = fn(self.s, [entity])[entity.id]
        return self._totals[key]

    def rate(self, kind, entity) -> Usage:
        key = (kind, entity.id)
        if key not in self._rate:
            since = self.now - timedelta(days=FORECAST_WINDOW_DAYS)
            u = entity_usage(self.s, kind, entity.id, since=since)
            self._rate[key] = Usage(u.hours / FORECAST_WINDOW_DAYS, u.cycles / FORECAST_WINDOW_DAYS, u.flights)
        return self._rate[key]

    def location(self, component: Component):
        if self._location is None:
            self._location = current_installations(self.s)
        inst = self._location.get(component.id)
        return None if inst is None else {"aircraft_id": inst.aircraft_id, "aircraft_name": inst.aircraft.name,
                                           "aircraft_serial": inst.aircraft.serial, "position": inst.position}

    def entity_info(self, kind, entity) -> dict:
        if kind == "aircraft":
            return {"kind": kind, "id": entity.id, "serial": entity.serial, "name": entity.name,
                    "type": entity.type.name, "status": entity.status,
                    "status_label": AIRCRAFT_STATUSES.get(entity.status), "location": None}
        return {"kind": kind, "id": entity.id, "serial": entity.serial,
                "name": f"{entity.type.name} {entity.serial}", "type": entity.type.name,
                "status": entity.status, "status_label": COMPONENT_STATUSES.get(entity.status),
                "location": self.location(entity)}

    def evaluate(self, rule: MaintenanceRule, kind: str, entity) -> dict:
        now = self.now
        last = None
        if rule.kind == "life":
            base_at = getattr(entity, "manufactured_at", None) or entity.in_service_at
            t = self.totals(kind, entity)
            used_h, used_c = t.hours, t.cycles
        else:
            last = last_record(self.s, kind, entity.id, rule.id, before=now)
            if last is not None:
                base_at = last.performed_at
                u = entity_usage(self.s, kind, entity.id, since=base_at, before=now + timedelta(seconds=1))
                used_h, used_c = u.hours, u.cycles
            else:
                # Работа ни разу не выполнялась: консервативно считаем всю наработку
                base_at = entity.in_service_at
                t = self.totals(kind, entity)
                used_h, used_c = t.hours, t.cycles
        used_d = max(0.0, (now - base_at).total_seconds() / 86400)
        rate = self.rate(kind, entity)

        metrics = []
        for metric, interval, warn, used, per_day in (
            ("hours", rule.interval_hours, rule.warn_hours, used_h, rate.hours),
            ("cycles", rule.interval_cycles, rule.warn_cycles, used_c, rate.cycles),
            ("days", rule.interval_days, rule.warn_days, used_d, 1.0),
        ):
            if not interval:
                continue
            warn = warn if warn is not None else interval * 0.1
            remaining = interval - used
            status = "overdue" if remaining <= 0 else ("due_soon" if remaining <= warn else "ok")
            days_left = None
            if remaining > 0 and per_day > 0:
                days_left = remaining / per_day
            metrics.append({
                "metric": metric, "unit": METRIC_LABELS[metric],
                "used": round(used, 2), "limit": interval, "warn": round(warn, 2),
                "remaining": round(remaining, 2), "ratio": round(used / interval, 4),
                "status": status, "days_left": None if days_left is None else round(days_left, 1),
            })
        status = min((m["status"] for m in metrics), key=STATUS_ORDER.get, default="ok")
        driver = max(metrics, key=lambda m: (STATUS_ORDER["ok"] - STATUS_ORDER[m["status"]], m["ratio"]), default=None)
        lefts = [m["days_left"] for m in metrics if m["days_left"] is not None]
        forecast = None if status == "overdue" or not lefts else now + timedelta(days=min(lefts))
        return {
            "key": f"{kind}:{entity.id}:{rule.id}",
            "entity": self.entity_info(kind, entity),
            "rule": {"id": rule.id, "code": rule.code, "name": rule.name, "kind": rule.kind},
            "status": status,
            "base_at": iso(base_at),
            "last_performed_at": iso(last.performed_at) if last else None,
            "metrics": metrics,
            "driver": driver,
            "forecast_date": iso(forecast),
        }


def due_items(s: Session, now: datetime | None = None, aircraft_id: int | None = None,
              component_id: int | None = None, statuses: set[str] | None = None,
              evaluator: Evaluator | None = None) -> list[dict]:
    ev = evaluator or Evaluator(s, now)
    rules = list(s.scalars(select(MaintenanceRule).where(MaintenanceRule.active.is_(True))))
    targets: list[tuple[str, object]] = []
    if component_id is not None:
        c = s.get(Component, component_id)
        if c:
            targets.append(("component", c))
    elif aircraft_id is not None:
        a = s.get(Aircraft, aircraft_id)
        if a:
            targets.append(("aircraft", a))
            installed = [cid for cid, inst in current_installations(s).items() if inst.aircraft_id == aircraft_id]
            if installed:
                targets += [("component", c) for c in s.scalars(select(Component).where(Component.id.in_(installed)))]
    else:
        targets += [("aircraft", a) for a in s.scalars(select(Aircraft).where(Aircraft.status != "retired"))]
        targets += [("component", c) for c in s.scalars(select(Component).where(Component.status != "retired"))]

    items = []
    for kind, ent in targets:
        for rule in applicable_rules(rules, kind, ent):
            item = ev.evaluate(rule, kind, ent)
            if statuses is None or item["status"] in statuses:
                items.append(item)
    items.sort(key=lambda i: (STATUS_ORDER[i["status"]], -(i["driver"]["ratio"] if i["driver"] else 0)))
    return items


def record_maintenance(s: Session, *, target: str, entity_id: int, rule_id: int | None, performed_at: datetime | None,
                       performed_by: str | None, work_type: str, description: str | None, result: str | None,
                       new_status: str | None) -> MaintenanceRecord:
    now = utcnow()
    performed_at = performed_at or now
    if performed_at > now + timedelta(minutes=10):
        raise DomainError("Дата выполнения работы не может быть в будущем")
    if target == "aircraft":
        ent = s.get(Aircraft, entity_id)
        statuses = AIRCRAFT_STATUSES
    elif target == "component":
        ent = s.get(Component, entity_id)
        statuses = COMPONENT_STATUSES
    else:
        raise DomainError("target должен быть aircraft или component")
    if ent is None:
        raise NotFound("Объект обслуживания не найден")
    if performed_at < ent.in_service_at:
        raise DomainError("Дата работы раньше ввода объекта в эксплуатацию")

    rule = None
    if rule_id is not None:
        rule = s.get(MaintenanceRule, rule_id)
        if rule is None:
            raise NotFound("Регламент не найден")
        if not applicable_rules([rule], target, ent):
            raise DomainError(f"Регламент «{rule.name}» не применим к этому объекту")
        if rule.kind == "life":
            raise DomainError(
                "Назначенный ресурс не обнуляется выполнением работ — узел подлежит замене/списанию. "
                "Используйте операцию снятия узла"
            )

    on_aircraft_id = None
    if target == "component":
        inst = current_installations(s).get(ent.id)
        on_aircraft_id = inst.aircraft_id if inst else None
        totals = component_totals(s, [ent], before=performed_at + timedelta(seconds=1))[ent.id]
    else:
        totals = aircraft_totals(s, [ent], before=performed_at + timedelta(seconds=1))[ent.id]

    if new_status:
        if new_status not in statuses:
            raise DomainError("Недопустимый статус")
        if target == "component":
            if (new_status == "installed") != (on_aircraft_id is not None):
                raise DomainError("Статус «Установлен» меняется только операциями установки/снятия")
        ent.status = new_status

    rec = MaintenanceRecord(
        target=target,
        aircraft_id=ent.id if target == "aircraft" else None,
        component_id=ent.id if target == "component" else None,
        rule_id=rule.id if rule else None,
        work_type=work_type or ("scheduled" if rule else "unscheduled"),
        performed_at=performed_at, performed_by=performed_by, description=description, result=result,
        hours_at=round(totals.hours, 2), cycles_at=totals.cycles,
        on_aircraft_id=on_aircraft_id if target == "component" else None,
        status_after=new_status,
    )
    s.add(rec)
    s.flush()
    return rec


def record_dict(r: MaintenanceRecord) -> dict:
    ent = r.aircraft if r.target == "aircraft" else r.component
    return {
        "id": r.id, "target": r.target,
        "aircraft_id": r.aircraft_id, "component_id": r.component_id,
        "entity_serial": ent.serial if ent else None,
        "entity_name": (ent.name if r.target == "aircraft" else f"{ent.type.name} {ent.serial}") if ent else None,
        "rule": None if r.rule is None else {"id": r.rule.id, "code": r.rule.code, "name": r.rule.name},
        "work_type": r.work_type, "performed_at": iso(r.performed_at), "performed_by": r.performed_by,
        "description": r.description, "result": r.result, "hours_at": r.hours_at, "cycles_at": r.cycles_at,
        "on_aircraft": None if r.on_aircraft is None else {"id": r.on_aircraft.id, "name": r.on_aircraft.name},
        "status_after": r.status_after,
    }


def records_query(aircraft_id=None, component_id=None, include_components=False, s: Session | None = None):
    q = select(MaintenanceRecord)
    if component_id is not None:
        q = q.where(MaintenanceRecord.component_id == component_id)
    elif aircraft_id is not None:
        if include_components:
            q = q.where(or_(MaintenanceRecord.aircraft_id == aircraft_id, MaintenanceRecord.on_aircraft_id == aircraft_id))
        else:
            q = q.where(MaintenanceRecord.aircraft_id == aircraft_id)
    return q.order_by(MaintenanceRecord.performed_at.desc(), MaintenanceRecord.id.desc())
