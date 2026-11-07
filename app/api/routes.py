"""REST API (/api). Каждый изменяющий запрос — одна транзакция."""
from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import or_, select, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import DEMO_LOGS_DIR
from ..db import get_session
from ..errors import DomainError, NotFound
from ..models import (
    AIRCRAFT_STATUSES,
    COMPONENT_STATUSES,
    Aircraft,
    AircraftType,
    Component,
    ComponentType,
    Flight,
    Installation,
    LogImport,
    MaintenanceRecord,
    MaintenanceRule,
    Setting,
)
from ..schemas import (
    AircraftIn,
    AircraftPatch,
    ComponentIn,
    ComponentPatch,
    EconomicsIn,
    InstallIn,
    MaintenanceIn,
    RemoveIn,
    RuleIn,
)
from ..services import inventory, logs
from ..services.analytics import analytics
from ..services.maintenance import STATUS_ORDER, Evaluator, due_items, record_dict, record_maintenance, records_query
from ..services.usage import (
    Usage,
    aircraft_totals,
    component_totals,
    current_installations,
    installation_usage,
)
from ..timeutil import iso, parse_dt, utcnow

router = APIRouter(prefix="/api")


def _get(s: Session, model, id_: int, what: str):
    obj = s.get(model, id_)
    if obj is None:
        raise NotFound(f"{what} не найден")
    return obj


def _dt(v):
    return None if v is None else parse_dt(v)


def _commit(s: Session):
    try:
        s.commit()
    except IntegrityError as e:
        s.rollback()
        raise DomainError(f"Нарушение целостности данных: {e.orig}", 409) from e


# ---------------------------------------------------------------- справочники
@router.get("/meta")
def meta(s: Session = Depends(get_session)):
    return {
        "aircraft_statuses": AIRCRAFT_STATUSES,
        "component_statuses": COMPONENT_STATUSES,
        "aircraft_types": [{"id": t.id, "code": t.code, "name": t.name, "category": t.category,
                            "manufacturer": t.manufacturer, "slots": t.slots}
                           for t in s.scalars(select(AircraftType).order_by(AircraftType.id))],
        "component_types": [{"id": t.id, "code": t.code, "name": t.name, "description": t.description}
                            for t in s.scalars(select(ComponentType).order_by(ComponentType.id))],
        "now": iso(utcnow()),
    }


# ---------------------------------------------------------------- помощники представления
def _summary_by_aircraft(items: list[dict]) -> dict[int, dict]:
    out = defaultdict(lambda: {"overdue": 0, "due_soon": 0, "ok": 0, "worst": "ok"})
    for it in items:
        e = it["entity"]
        aid = e["id"] if e["kind"] == "aircraft" else (e["location"] or {}).get("aircraft_id")
        if aid is None:
            continue
        d = out[aid]
        d[it["status"]] += 1
        if STATUS_ORDER[it["status"]] < STATUS_ORDER[d["worst"]]:
            d["worst"] = it["status"]
    return out


def _worst_by_component(items: list[dict]) -> dict[int, dict]:
    out = {}
    for it in items:
        if it["entity"]["kind"] != "component":
            continue
        cid = it["entity"]["id"]
        cur = out.get(cid)
        if cur is None or STATUS_ORDER[it["status"]] < STATUS_ORDER[cur["status"]]:
            out[cid] = {"status": it["status"], "rule": it["rule"]["name"], "driver": it["driver"]}
    return out


def aircraft_brief(a: Aircraft, tot: Usage, summ: dict, filled: int) -> dict:
    slots = len(a.type.slots or [])
    return {
        "id": a.id, "serial": a.serial, "name": a.name, "type": {"id": a.type.id, "name": a.type.name, "code": a.type.code},
        "status": a.status, "status_label": AIRCRAFT_STATUSES[a.status], "operator": a.operator, "base": a.base,
        "in_service_at": iso(a.in_service_at), "hours": round(tot.hours, 2), "cycles": tot.cycles, "flights": tot.flights,
        "slots_total": slots, "slots_filled": filled,
        "maintenance": summ, "airworthy": summ["overdue"] == 0 and filled == slots and a.status == "active",
    }


def component_brief(c: Component, tot: Usage, inst: Installation | None, worst: dict | None) -> dict:
    return {
        "id": c.id, "serial": c.serial, "type": {"id": c.type.id, "code": c.type.code, "name": c.type.name},
        "model": c.model, "part_number": c.part_number, "manufacturer": c.manufacturer, "firmware": c.firmware,
        "status": c.status, "status_label": COMPONENT_STATUSES[c.status],
        "hours": round(tot.hours, 2), "cycles": tot.cycles, "flights": tot.flights,
        "initial_hours": c.initial_hours, "initial_cycles": c.initial_cycles,
        "location": None if inst is None else {"aircraft_id": inst.aircraft_id, "aircraft_name": inst.aircraft.name,
                                                "aircraft_serial": inst.aircraft.serial, "position": inst.position,
                                                "installed_at": iso(inst.installed_at)},
        "maintenance": worst or {"status": "ok"},
    }


# ---------------------------------------------------------------- дашборд
@router.get("/dashboard")
def dashboard(s: Session = Depends(get_session)):
    ev = Evaluator(s)
    items = due_items(s, evaluator=ev)
    summ = _summary_by_aircraft(items)
    aircraft = list(s.scalars(select(Aircraft).where(Aircraft.status != "retired").order_by(Aircraft.serial)))
    tots = aircraft_totals(s, aircraft)
    cur = current_installations(s)
    filled = defaultdict(int)
    for inst in cur.values():
        filled[inst.aircraft_id] += 1
    comps = list(s.scalars(select(Component)))
    imports = list(s.scalars(select(LogImport).order_by(LogImport.id.desc()).limit(6)))
    by_status = defaultdict(int)
    for c in comps:
        by_status[c.status] += 1
    return {
        "fleet": [aircraft_brief(a, tots[a.id], summ[a.id], filled[a.id]) for a in aircraft],
        "due": [i for i in items if i["status"] != "ok"][:12],
        "counts": {"overdue": sum(1 for i in items if i["status"] == "overdue"),
                   "due_soon": sum(1 for i in items if i["status"] == "due_soon")},
        "components_by_status": {k: {"label": COMPONENT_STATUSES[k], "count": by_status.get(k, 0)} for k in COMPONENT_STATUSES},
        "recent_imports": [logs.import_dict(i) for i in imports],
    }


# ---------------------------------------------------------------- борта
@router.get("/aircraft")
def list_aircraft(s: Session = Depends(get_session)):
    aircraft = list(s.scalars(select(Aircraft).order_by(Aircraft.serial)))
    items = due_items(s)
    summ = _summary_by_aircraft(items)
    tots = aircraft_totals(s, aircraft)
    filled = defaultdict(int)
    for inst in current_installations(s).values():
        filled[inst.aircraft_id] += 1
    return [aircraft_brief(a, tots[a.id], summ[a.id], filled[a.id]) for a in aircraft]


@router.post("/aircraft", status_code=201)
def create_aircraft(data: AircraftIn, s: Session = Depends(get_session)):
    _get(s, AircraftType, data.type_id, "Тип БПЛА")
    if s.scalar(select(Aircraft).where(Aircraft.serial == data.serial)):
        raise DomainError("Борт с таким серийным номером уже существует", 409)
    a = Aircraft(**data.model_dump(exclude={"in_service_at"}), in_service_at=_dt(data.in_service_at) or utcnow())
    s.add(a)
    _commit(s)
    return {"id": a.id}


@router.patch("/aircraft/{aid}")
def patch_aircraft(aid: int, data: AircraftPatch, s: Session = Depends(get_session)):
    a = _get(s, Aircraft, aid, "Борт")
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(a, k, v)
    _commit(s)
    return {"ok": True}


@router.get("/aircraft/{aid}")
def get_aircraft(aid: int, s: Session = Depends(get_session)):
    a = _get(s, Aircraft, aid, "Борт")
    ev = Evaluator(s)
    items = due_items(s, aircraft_id=aid, evaluator=ev)
    summ = _summary_by_aircraft(items)[aid]
    worst = _worst_by_component(items)
    tot = aircraft_totals(s, [a])[a.id]
    installs = list(s.scalars(select(Installation).where(Installation.aircraft_id == aid)
                              .order_by(Installation.installed_at.desc())))
    current = {i.position: i for i in installs if i.removed_at is None}
    comps = [i.component for i in current.values()]
    ctot = component_totals(s, comps)
    stint = installation_usage(s, [i.id for i in installs])
    config = []
    for slot in a.type.slots or []:
        inst = current.get(slot["position"])
        row = {"position": slot["position"], "label": slot["label"], "component_type": slot["component_type"],
               "component": None}
        if inst:
            c = inst.component
            row["component"] = component_brief(c, ctot[c.id], inst, worst.get(c.id))
            u = stint.get(inst.id, Usage())
            row["component"]["on_board_hours"] = round(u.hours, 2)
            row["component"]["on_board_cycles"] = u.cycles
        config.append(row)
    history = []
    for i in installs:
        u = stint.get(i.id, Usage())
        history.append({"id": i.id, "component_id": i.component_id, "serial": i.component.serial,
                        "type": i.component.type.name, "position": i.position,
                        "installed_at": iso(i.installed_at), "removed_at": iso(i.removed_at),
                        "install_reason": i.install_reason, "remove_reason": i.remove_reason,
                        "hours": round(u.hours, 2), "cycles": u.cycles, "flights": u.flights})
    flights = s.scalars(select(Flight).where(Flight.aircraft_id == aid).order_by(Flight.started_at.desc()).limit(200))
    records = s.scalars(records_query(aircraft_id=aid, include_components=True))
    return {
        **aircraft_brief(a, tot, summ, len(current)),
        "initial_hours": a.initial_hours, "initial_cycles": a.initial_cycles, "notes": a.notes,
        "configuration": config, "due": items, "installations": history,
        "flight_log": [_flight_dict(f) for f in flights],
        "maintenance_records": [record_dict(r) for r in records],
    }


def _flight_dict(f: Flight) -> dict:
    return {"id": f.id, "aircraft_id": f.aircraft_id, "aircraft_name": f.aircraft.name, "external_id": f.external_id,
            "started_at": iso(f.started_at), "ended_at": iso(f.ended_at), "duration_h": round(f.duration_h, 3),
            "cycles": f.cycles, "mission": f.mission, "pilot": f.pilot, "import_id": f.import_id}


# ---------------------------------------------------------------- узлы
@router.get("/components")
def list_components(status: str | None = None, type_id: int | None = None, q: str | None = None,
                    aircraft_id: int | None = None, s: Session = Depends(get_session)):
    query = select(Component).order_by(Component.type_id, Component.serial)
    if status:
        query = query.where(Component.status.in_(status.split(",")))
    if type_id:
        query = query.where(Component.type_id == type_id)
    if q:
        like = f"%{q}%"
        query = query.where(or_(Component.serial.ilike(like), Component.model.ilike(like)))
    comps = list(s.scalars(query))
    cur = current_installations(s)
    if aircraft_id:
        comps = [c for c in comps if c.id in cur and cur[c.id].aircraft_id == aircraft_id]
    tots = component_totals(s, comps)
    worst = _worst_by_component(due_items(s))
    return [component_brief(c, tots[c.id], cur.get(c.id), worst.get(c.id)) for c in comps]


@router.post("/components", status_code=201)
def create_component(data: ComponentIn, s: Session = Depends(get_session)):
    _get(s, ComponentType, data.type_id, "Тип узла")
    if s.scalar(select(Component).where(Component.serial == data.serial)):
        raise DomainError("Узел с таким серийным номером уже существует", 409)
    c = Component(**data.model_dump(exclude={"in_service_at", "manufactured_at"}),
                  in_service_at=_dt(data.in_service_at) or utcnow(), manufactured_at=_dt(data.manufactured_at))
    s.add(c)
    _commit(s)
    return {"id": c.id}


@router.patch("/components/{cid}")
def patch_component(cid: int, data: ComponentPatch, s: Session = Depends(get_session)):
    c = _get(s, Component, cid, "Узел")
    upd = data.model_dump(exclude_unset=True)
    if "status" in upd and c.status == "installed":
        raise DomainError("Статус установленного узла меняется операцией снятия")
    for k, v in upd.items():
        setattr(c, k, v)
    _commit(s)
    return {"ok": True}


@router.get("/components/{cid}")
def get_component(cid: int, s: Session = Depends(get_session)):
    c = _get(s, Component, cid, "Узел")
    ev = Evaluator(s)
    items = due_items(s, component_id=cid, evaluator=ev)
    tot = component_totals(s, [c])[c.id]
    installs = list(s.scalars(select(Installation).where(Installation.component_id == cid)
                              .order_by(Installation.installed_at)))
    stint = installation_usage(s, [i.id for i in installs])
    cur = next((i for i in installs if i.removed_at is None), None)
    stints = []
    for i in installs:
        u = stint.get(i.id, Usage())
        stints.append({"id": i.id, "aircraft_id": i.aircraft_id, "aircraft_name": i.aircraft.name,
                       "aircraft_serial": i.aircraft.serial, "position": i.position,
                       "installed_at": iso(i.installed_at), "removed_at": iso(i.removed_at),
                       "install_reason": i.install_reason, "remove_reason": i.remove_reason,
                       "hours": round(u.hours, 2), "cycles": u.cycles, "flights": u.flights})
    records = [record_dict(r) for r in s.scalars(records_query(component_id=cid))]
    # Хронология жизни узла
    timeline = [{"at": iso(c.in_service_at), "kind": "service", "title": "Постановка на учёт / ввод в эксплуатацию",
                 "details": f"Начальная наработка {c.initial_hours} ч, {c.initial_cycles} цикл." if c.initial_hours or c.initial_cycles else None}]
    for st in stints:
        timeline.append({"at": st["installed_at"], "kind": "install", "title": f"Установлен на {st['aircraft_name']} ({st['position']})",
                         "details": st["install_reason"]})
        if st["removed_at"]:
            timeline.append({"at": st["removed_at"], "kind": "remove",
                             "title": f"Снят с {st['aircraft_name']} — наработано на борту {st['hours']} ч / {st['cycles']} цикл.",
                             "details": st["remove_reason"]})
    for r in records:
        timeline.append({"at": r["performed_at"], "kind": "maintenance",
                         "title": (r["rule"]["name"] if r["rule"] else "Внеплановая работа") + f" (при {r['hours_at']} ч)",
                         "details": " · ".join(x for x in (r["description"], r["result"], r["performed_by"]) if x)})
    timeline.sort(key=lambda e: e["at"], reverse=True)
    # Полёты, начисленные узлу
    fl = []
    if installs:
        conds = [((Flight.aircraft_id == i.aircraft_id) & (Flight.started_at >= i.installed_at) &
                  ((Flight.started_at < i.removed_at) if i.removed_at else true())) for i in installs]
        fl = [_flight_dict(f) for f in s.scalars(select(Flight).where(or_(*conds))
                                                .order_by(Flight.started_at.desc()).limit(200))]
    worst = _worst_by_component(items).get(c.id)
    return {
        **component_brief(c, tot, cur, worst),
        "manufactured_at": iso(c.manufactured_at), "in_service_at": iso(c.in_service_at), "notes": c.notes,
        "on_board_hours": round(stint.get(cur.id, Usage()).hours, 2) if cur else None,
        "boards_count": len({i.aircraft_id for i in installs}),
        "stints": list(reversed(stints)), "due": items, "maintenance_records": records,
        "timeline": timeline, "flight_log": fl,
    }


@router.post("/components/{cid}/install")
def install_component(cid: int, data: InstallIn, s: Session = Depends(get_session)):
    c = _get(s, Component, cid, "Узел")
    a = _get(s, Aircraft, data.aircraft_id, "Борт")
    res = inventory.install(s, c, a, data.position, _dt(data.at), data.reason, data.performed_by)
    _commit(s)
    return res


@router.post("/components/{cid}/remove")
def remove_component(cid: int, data: RemoveIn, s: Session = Depends(get_session)):
    c = _get(s, Component, cid, "Узел")
    res = inventory.remove(s, c, _dt(data.at), data.to_status, data.reason, data.performed_by)
    _commit(s)
    return res


@router.post("/components/{cid}/transfer")
def transfer_component(cid: int, data: InstallIn, s: Session = Depends(get_session)):
    c = _get(s, Component, cid, "Узел")
    a = _get(s, Aircraft, data.aircraft_id, "Борт")
    res = inventory.transfer(s, c, a, data.position, _dt(data.at), data.reason, data.performed_by)
    _commit(s)
    return res


# ---------------------------------------------------------------- полёты и импорт
@router.get("/flights")
def list_flights(aircraft_id: int | None = None, import_id: int | None = None, limit: int = Query(200, le=2000),
                 s: Session = Depends(get_session)):
    q = select(Flight).order_by(Flight.started_at.desc()).limit(limit)
    if aircraft_id:
        q = q.where(Flight.aircraft_id == aircraft_id)
    if import_id:
        q = q.where(Flight.import_id == import_id)
    return [_flight_dict(f) for f in s.scalars(q)]


@router.get("/imports")
def list_imports(s: Session = Depends(get_session)):
    return [logs.import_dict(i) for i in s.scalars(select(LogImport).order_by(LogImport.id.desc()))]


@router.get("/imports/{iid}")
def get_import(iid: int, s: Session = Depends(get_session)):
    return logs.import_dict(_get(s, LogImport, iid, "Импорт"))


def _run_import(s: Session, content: bytes, filename: str, by: str | None, dry_run: bool) -> dict:
    if len(content) > 20 * 1024 * 1024:
        raise DomainError("Файл больше 20 МБ", 413)
    res = logs.import_log(s, content, filename, by, dry_run=dry_run)
    if dry_run:
        s.rollback()
    else:
        _commit(s)
    return res


@router.post("/imports")
async def upload_import(file: UploadFile = File(...), dry_run: bool = Form(False),
                        imported_by: str | None = Form(None), s: Session = Depends(get_session)):
    content = await file.read()
    return _run_import(s, content, file.filename or "log", imported_by, dry_run)


@router.post("/imports/{iid}/rollback")
def rollback(iid: int, s: Session = Depends(get_session)):
    res = logs.rollback_import(s, _get(s, LogImport, iid, "Импорт"))
    _commit(s)
    return res


def _demo_file(name: str):
    path = (DEMO_LOGS_DIR / name).resolve()
    if path.parent != DEMO_LOGS_DIR.resolve() or not path.is_file():
        raise NotFound("Демо-журнал не найден")
    return path


@router.get("/demo-logs")
def demo_logs():
    import json
    idx = DEMO_LOGS_DIR / "index.json"
    return json.loads(idx.read_text("utf-8")) if idx.exists() else []


@router.get("/demo-logs/{name}")
def demo_log_download(name: str):
    return FileResponse(_demo_file(name), filename=name)


@router.post("/demo-logs/{name}/import")
def demo_log_import(name: str, dry_run: bool = False, s: Session = Depends(get_session)):
    path = _demo_file(name)
    return _run_import(s, path.read_bytes(), name, "демо", dry_run)


# ---------------------------------------------------------------- регламенты
def rule_dict(r: MaintenanceRule) -> dict:
    return {
        "id": r.id, "code": r.code, "name": r.name, "description": r.description, "target": r.target,
        "aircraft_type_id": r.aircraft_type_id, "component_type_id": r.component_type_id,
        "applies_to": (r.aircraft_type.name if r.aircraft_type else "Все типы БПЛА") if r.target == "aircraft"
        else (r.component_type.name if r.component_type else "Все типы узлов"),
        "kind": r.kind, "interval_hours": r.interval_hours, "interval_cycles": r.interval_cycles,
        "interval_days": r.interval_days, "warn_hours": r.warn_hours, "warn_cycles": r.warn_cycles,
        "warn_days": r.warn_days, "active": r.active,
    }


@router.get("/rules")
def list_rules(s: Session = Depends(get_session)):
    return [rule_dict(r) for r in s.scalars(select(MaintenanceRule).order_by(MaintenanceRule.target, MaintenanceRule.code))]


def _validate_rule(data: RuleIn):
    if not (data.interval_hours or data.interval_cycles or data.interval_days):
        raise DomainError("Задайте хотя бы один порог: часы, циклы или дни")
    if data.target == "aircraft" and data.component_type_id:
        raise DomainError("Регламент борта не может ссылаться на тип узла")
    if data.target == "component" and data.aircraft_type_id:
        raise DomainError("Регламент узла не может ссылаться на тип БПЛА")


@router.post("/rules", status_code=201)
def create_rule(data: RuleIn, s: Session = Depends(get_session)):
    _validate_rule(data)
    if s.scalar(select(MaintenanceRule).where(MaintenanceRule.code == data.code)):
        raise DomainError("Регламент с таким кодом уже есть", 409)
    r = MaintenanceRule(**data.model_dump())
    s.add(r)
    _commit(s)
    return rule_dict(r)


@router.put("/rules/{rid}")
def update_rule(rid: int, data: RuleIn, s: Session = Depends(get_session)):
    _validate_rule(data)
    r = _get(s, MaintenanceRule, rid, "Регламент")
    for k, v in data.model_dump().items():
        setattr(r, k, v)
    _commit(s)
    return rule_dict(r)


@router.delete("/rules/{rid}")
def delete_rule(rid: int, s: Session = Depends(get_session)):
    r = _get(s, MaintenanceRule, rid, "Регламент")
    used = s.scalar(select(MaintenanceRecord.id).where(MaintenanceRecord.rule_id == rid).limit(1))
    if used:
        r.active = False  # история работ ссылается на регламент — только деактивируем
        msg = "Регламент деактивирован (по нему есть выполненные работы)"
    else:
        s.delete(r)
        msg = "Регламент удалён"
    _commit(s)
    return {"message": msg}


# ---------------------------------------------------------------- ТО
@router.get("/maintenance/due")
def maintenance_due(status: str | None = "overdue,due_soon", aircraft_id: int | None = None,
                    component_id: int | None = None, s: Session = Depends(get_session)):
    statuses = set(status.split(",")) if status else None
    return due_items(s, aircraft_id=aircraft_id, component_id=component_id, statuses=statuses)


@router.get("/maintenance/records")
def maintenance_records(aircraft_id: int | None = None, component_id: int | None = None,
                        limit: int = Query(300, le=5000), s: Session = Depends(get_session)):
    q = records_query(aircraft_id=aircraft_id, component_id=component_id, include_components=True).limit(limit)
    return [record_dict(r) for r in s.scalars(q)]


@router.post("/maintenance/records", status_code=201)
def create_record(data: MaintenanceIn, s: Session = Depends(get_session)):
    rec = record_maintenance(s, target=data.target, entity_id=data.entity_id, rule_id=data.rule_id,
                             performed_at=_dt(data.performed_at), performed_by=data.performed_by,
                             work_type=data.work_type, description=data.description, result=data.result,
                             new_status=data.new_status)
    _commit(s)
    after = due_items(s, aircraft_id=data.entity_id if data.target == "aircraft" else None,
                      component_id=data.entity_id if data.target == "component" else None)
    if data.target == "aircraft":
        after = [i for i in after if i["entity"]["kind"] == "aircraft"]
    return {"record": record_dict(rec), "due_after": after,
            "message": "Работа зарегистрирована" + (f": {rec.rule.name} — интервал отсчитывается заново" if rec.rule else "")}


# ---------------------------------------------------------------- аналитика
@router.get("/analytics")
def get_analytics(s: Session = Depends(get_session)):
    return analytics(s)


@router.put("/settings/economics")
def put_economics(data: EconomicsIn, s: Session = Depends(get_session)):
    row = s.get(Setting, "economics")
    if row is None:
        s.add(Setting(key="economics", value=data.model_dump()))
    else:
        row.value = data.model_dump()
    _commit(s)
    return data.model_dump()


# ---------------------------------------------------------------- демо
@router.post("/demo/reset")
def demo_reset():
    from ..seed import reset_and_seed
    return reset_and_seed()
