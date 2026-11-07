"""Блок оценки эффекта: что даёт поэкземплярный учёт по сравнению с учётом «по борту»."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    Aircraft,
    Component,
    Flight,
    Installation,
    LogImport,
    MaintenanceRecord,
    MaintenanceRule,
    Setting,
)
from ..timeutil import iso, parse_dt, utcnow
from .maintenance import STATUS_ORDER, Evaluator, due_items
from .usage import component_totals, component_usage, current_installations, installation_usage

DEFAULT_ECONOMICS = {
    "failure_probability": 0.15,      # вероятность отказа узла при работе сверх регламента
    "failure_cost": 380000,           # средний ущерб от отказа в полёте, ₽ (узел + повреждения + срыв задания)
    "downtime_days_per_failure": 6,   # простой борта после отказа, дней
    "downtime_cost_per_day": 22000,   # упущенная выручка борта за день простоя, ₽
    "manual_minutes_per_flight": 3,   # ручной перенос полёта в бумажный/Excel формуляр (борт + узлы)
    "manual_minutes_per_operation": 20,  # поиск истории узла при перестановке/ТО вручную
    "engineer_rate": 1600,            # стоимость часа инженера, ₽
}


def _status(metrics_spec):
    st = "ok"
    for interval, warn, used in metrics_spec:
        if not interval:
            continue
        warn = warn if warn is not None else interval * 0.1
        rem = interval - used
        cur = "overdue" if rem <= 0 else ("due_soon" if rem <= warn else "ok")
        if STATUS_ORDER[cur] < STATUS_ORDER[st]:
            st = cur
    return st


def economics_settings(s: Session) -> dict:
    row = s.get(Setting, "economics")
    return {**DEFAULT_ECONOMICS, **(row.value if row else {})}


def analytics(s: Session, now: datetime | None = None) -> dict:
    now = now or utcnow()
    ev = Evaluator(s, now)
    items = due_items(s, evaluator=ev)
    rules = {r.id: r for r in s.scalars(select(MaintenanceRule))}
    current = current_installations(s)
    comps = {c.id: c for c in s.scalars(select(Component))}
    aircraft = list(s.scalars(select(Aircraft)))

    # --- 1. «Скрытая» наработка: что потерялось бы при учёте только по борту
    inst_usage = installation_usage(s, [i.id for i in current.values()])
    totals = component_totals(s, list(comps.values()))
    hidden_rows = []
    for cid, inst in current.items():
        c = comps[cid]
        on_board = inst_usage.get(inst.id)
        ob_h = on_board.hours if on_board else 0.0
        tsn = totals[cid].hours
        if tsn - ob_h > 0.05:
            hidden_rows.append({"component_id": cid, "serial": c.serial, "type": c.type.name,
                                "aircraft": inst.aircraft.name, "tsn_hours": round(tsn, 1),
                                "on_board_hours": round(ob_h, 1), "hidden_hours": round(tsn - ob_h, 1),
                                "installed_at": iso(inst.installed_at)})
    hidden_rows.sort(key=lambda r: -r["hidden_hours"])

    # --- 2. Предотвращённые пропуски ТО: поэкземплярный учёт видит срок, «бортовой» — нет
    prevented = []
    for it in items:
        if it["entity"]["kind"] != "component" or it["status"] == "ok":
            continue
        inst = current.get(it["entity"]["id"])
        if inst is None:
            continue
        rule = rules[it["rule"]["id"]]
        base = parse_dt(it["base_at"])
        naive_base = max(base, inst.installed_at) if rule.kind == "periodic" else inst.installed_at
        u = component_usage(s, [inst.component_id], since=naive_base, aircraft_id=inst.aircraft_id).get(inst.component_id)
        nh, nc = (u.hours, u.cycles) if u else (0.0, 0)
        nd = (now - naive_base).total_seconds() / 86400
        naive = _status([(rule.interval_hours, rule.warn_hours, nh), (rule.interval_cycles, rule.warn_cycles, nc),
                         (rule.interval_days, rule.warn_days, nd)])
        if STATUS_ORDER[naive] > STATUS_ORDER[it["status"]]:
            prevented.append({"entity": it["entity"], "rule": it["rule"], "real_status": it["status"],
                              "naive_status": naive, "driver": it["driver"],
                              "naive_hours": round(nh, 1), "naive_cycles": nc})

    # --- 3. Перестановки и движение узлов
    installs = list(s.scalars(select(Installation)))
    boards_per_comp = defaultdict(set)
    for i in installs:
        boards_per_comp[i.component_id].add(i.aircraft_id)
    moved = [cid for cid, b in boards_per_comp.items() if len(b) > 1]
    first_install = {}
    for i in installs:
        if i.component_id not in first_install or i.installed_at < first_install[i.component_id]:
            first_install[i.component_id] = i.installed_at
    ops_90 = sum(1 for i in installs if i.installed_at >= now - timedelta(days=90) and i.installed_at != first_install[i.component_id])
    ops_90 += sum(1 for i in installs if i.removed_at and i.removed_at >= now - timedelta(days=90))

    # --- 4. Интенсивность эксплуатации по месяцам
    since = (now - timedelta(days=182)).replace(day=1, hour=0, minute=0, second=0)
    rows = s.execute(select(Flight.aircraft_id, Flight.started_at, Flight.duration_h, Flight.cycles)
                     .where(Flight.started_at >= since))
    monthly = defaultdict(lambda: defaultdict(float))
    flights_90 = 0
    for aid, st, dur, _cyc in rows:
        monthly[st.strftime("%Y-%m")][aid] += dur
        if st >= now - timedelta(days=90):
            flights_90 += 1
    months = sorted(monthly)
    ac_names = {a.id: a.name for a in aircraft}
    monthly_series = [{"month": m, "by_aircraft": {ac_names[a]: round(h, 1) for a, h in monthly[m].items()},
                       "total": round(sum(monthly[m].values()), 1)} for m in months]

    # --- 5. Сводные показатели
    status_counts = {"overdue": 0, "due_soon": 0, "ok": 0}
    for it in items:
        status_counts[it["status"]] += 1
    total_flights = s.scalar(select(func.count(Flight.id))) or 0
    total_hours = s.scalar(select(func.coalesce(func.sum(Flight.duration_h), 0.0))) or 0.0
    maint_90 = s.scalar(select(func.count(MaintenanceRecord.id))
                        .where(MaintenanceRecord.performed_at >= now - timedelta(days=90))) or 0
    dup_blocked = s.scalar(select(func.coalesce(func.sum(LogImport.flights_duplicate), 0))) or 0
    imports = s.scalar(select(func.count(LogImport.id))) or 0
    overdue_installed = [it for it in items if it["status"] == "overdue" and
                         (it["entity"]["kind"] == "aircraft" or it["entity"]["location"])]
    return {
        "generated_at": iso(now),
        "kpi": {
            "aircraft": len([a for a in aircraft if a.status != "retired"]),
            "components": len([c for c in comps.values() if c.status != "retired"]),
            "components_installed": len(current),
            "components_reserve": len([c for c in comps.values() if c.status in ("stock", "maintenance")]),
            "total_flights": total_flights, "total_hours": round(total_hours, 1),
            "flights_90d": flights_90, "maintenance_90d": maint_90,
            "component_operations_90d": ops_90,
            "components_moved": len(moved),
            "hidden_hours_total": round(sum(r["hidden_hours"] for r in hidden_rows), 1),
            "components_with_hidden_hours": len(hidden_rows),
            "prevented_misses": len(prevented),
            "duplicates_blocked": int(dup_blocked), "imports": imports,
            "overdue": status_counts["overdue"], "due_soon": status_counts["due_soon"], "ok": status_counts["ok"],
            "overdue_in_operation": len(overdue_installed),
        },
        "hidden_hours": hidden_rows,
        "prevented": prevented,
        "monthly": monthly_series,
        "aircraft_names": [a.name for a in aircraft],
        "economics": economics_settings(s),
    }
