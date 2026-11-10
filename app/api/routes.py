"""REST API (/api). Каждый изменяющий запрос — одна транзакция."""
from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import or_, select, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, object_session

from ..config import DEMO_LOGS_DIR
from ..db import get_session
from ..errors import DomainError, NotFound, require_demo_mode
from ..models import (
    AIRCRAFT_STATUSES,
    COMPONENT_STATUSES,
    Aircraft,
    AircraftType,
    Component,
    ComponentType,
    ComponentUsageEntry,
    Flight,
    Installation,
    LogImport,
    MaintenanceRecord,
    MaintenanceRule,
    SensorEvent,
    Setting,
)
from ..schemas import (
    AircraftIn,
    AircraftPatch,
    AircraftTypeIn,
    ComponentIn,
    ComponentPatch,
    ComponentTypeIn,
    EconomicsIn,
    ImageIn,
    ImageTitleIn,
    GeneratorIn,
    InstallIn,
    MaintenanceIn,
    ManualFlightIn,
    RemoveIn,
    RuleIn,
    UsageEntryIn,
)
from ..services import images, inventory, logs, manual, sensors
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
        "component_types": [{"id": t.id, "code": t.code, "name": t.name, "description": t.description,
                             "spec_fields": t.spec_fields or []}
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


def _res(e) -> dict:
    return {"hours": e.resource_hours, "cycles": e.resource_cycles, "days": e.resource_days}


def aircraft_brief(a: Aircraft, tot: Usage, summ: dict, positions: set[str]) -> dict:
    """positions — занятые позиции борта. Комплектность считается по обязательным позициям."""
    req = [sl["position"] for sl in a.type.slots or [] if sl.get("required", True)]
    slots = len(req)
    filled = sum(1 for p in req if p in positions)
    return {
        "id": a.id, "serial": a.serial, "name": a.name, "type": {"id": a.type.id, "name": a.type.name, "code": a.type.code},
        "status": a.status, "status_label": AIRCRAFT_STATUSES[a.status], "operator": a.operator, "base": a.base,
        "in_service_at": iso(a.in_service_at), "hours": round(tot.hours, 2), "cycles": tot.cycles, "flights": tot.flights,
        "slots_total": slots, "slots_filled": filled, "resource": _res(a), "image": a.image,
        "events": sensors.counts_by_kind(object_session(a), a.id),
        "slots_all": len(a.type.slots or []), "slots_all_filled": len(positions),
        "maintenance": summ, "airworthy": summ["overdue"] == 0 and filled == slots and a.status == "active",
        "readiness": readiness(a, summ, filled, slots),
    }


def readiness(a: Aircraft, summ: dict, filled: int, slots: int) -> dict:
    """Допуск борта к полётам: уровень (ok / warn / bad / gray) и текст для интерфейса."""
    if a.status != "active":
        return {"level": "gray", "text": AIRCRAFT_STATUSES[a.status]}
    if filled < slots:
        return {"level": "bad", "text": f"Некомплект ({filled}/{slots})"}
    if summ["overdue"]:
        return {"level": "bad", "text": f"Не допущен: просрочено ТО ({summ['overdue']})"}
    if summ["due_soon"]:
        return {"level": "warn", "text": f"Допущен, скоро ТО ({summ['due_soon']})"}
    return {"level": "ok", "text": "Допущен"}


def _residual_by_component(items: list[dict]) -> dict[int, dict]:
    """Остаточный ресурс узла: минимальный остаток по каждому счётчику среди назначенных ресурсов
    (типовые регламенты вида «назначенный ресурс» и индивидуальный ресурс экземпляра)."""
    out: dict[int, dict] = {}
    for it in items:
        if it["entity"]["kind"] != "component" or it["rule"]["kind"] != "life":
            continue
        r = out.setdefault(it["entity"]["id"], {"hours": None, "cycles": None, "days": None, "fraction": None, "limit_by": None})
        for m in it["metrics"]:
            k = m["metric"]
            if r[k] is None or m["remaining"] < r[k]:
                r[k] = m["remaining"]
            frac = max(0.0, 1 - m["ratio"])
            if r["fraction"] is None or frac < r["fraction"]:
                r["fraction"], r["limit_by"] = round(frac, 4), it["rule"]["name"]
    return out


def component_brief(c: Component, tot: Usage, inst: Installation | None, worst: dict | None, residual: dict | None = None) -> dict:
    return {
        "id": c.id, "serial": c.serial, "uid": c.uid, "specs": c.specs or [], "residual": residual,
        "manufactured_at": iso(c.manufactured_at), "in_service_at": iso(c.in_service_at),
        "type": {"id": c.type.id, "code": c.type.code, "name": c.type.name},
        "model": c.model, "part_number": c.part_number, "manufacturer": c.manufacturer, "firmware": c.firmware,
        "status": c.status, "status_label": COMPONENT_STATUSES[c.status],
        "hours": round(tot.hours, 2), "cycles": tot.cycles, "flights": tot.flights,
        "initial_hours": c.initial_hours, "initial_cycles": c.initial_cycles, "resource": _res(c),
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
    filled = defaultdict(set)
    for inst in cur.values():
        filled[inst.aircraft_id].add(inst.position)
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
    filled = defaultdict(set)
    for inst in current_installations(s).values():
        filled[inst.aircraft_id].add(inst.position)
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
    upd = data.model_dump(exclude_unset=True)
    if "name" in upd and not (upd["name"] or "").strip():
        raise DomainError("Название борта не может быть пустым")
    if "status" in upd and upd["status"] is None:
        raise DomainError("Укажите статус борта")
    if upd.get("status") == "retired" and a.status != "retired":
        busy = [i.component.serial for i in s.scalars(select(Installation).where(
            Installation.aircraft_id == aid, Installation.removed_at.is_(None)))]
        if busy:
            raise DomainError(f"На борту установлены узлы ({', '.join(busy)}) — снимите их перед списанием борта, "
                              "иначе они пропадут из учёта резерва")
    for k, v in upd.items():
        setattr(a, k, v.strip() if k == "name" else v)
    _commit(s)
    return {"ok": True}


def _image_usage(s: Session) -> dict[str, list[str]]:
    used = defaultdict(list)
    for a in s.scalars(select(Aircraft).where(Aircraft.image.is_not(None)).order_by(Aircraft.name)):
        used[a.image].append(a.name)
    return used


@router.get("/drone-images")
def drone_images(s: Session = Depends(get_session)):
    used = _image_usage(s)
    return {"presets": [{**p, "used_by": used.get(p["url"], [])} for p in images.preset_list()],
            "uploaded": images.uploaded_list(used)}


@router.post("/drone-images", status_code=201)
async def add_to_pool(files: list[UploadFile] = File(...)):
    """Загрузка своих фото в пул (без назначения борту). Можно несколько файлов сразу."""
    if len(files) > 20:
        raise DomainError("За раз — не больше 20 фото")
    added, errors = [], []
    for f in files:
        try:
            added.append(images.save_upload(await f.read(), images.title_from_filename(f.filename)))
        except DomainError as e:
            errors.append(f"{f.filename}: {e.message}")
    if not added:
        raise DomainError("; ".join(errors) or "Нет файлов")
    msg = f"Добавлено в пул: {len(added)}" + (f". Не принято: {'; '.join(errors)}" if errors else "")
    return {"added": added, "errors": errors, "message": msg}


@router.patch("/drone-images")
def rename_in_pool(data: ImageTitleIn):
    images.rename_upload(data.url, data.title)
    return {"message": "Подпись сохранена"}


@router.delete("/drone-images")
def delete_from_pool(url: str, s: Session = Depends(get_session)):
    images.delete_upload(url, _image_usage(s).get(url, []))
    return {"message": "Фото удалено из пула"}


@router.put("/aircraft/{aid}/image")
def set_aircraft_image(aid: int, data: ImageIn, s: Session = Depends(get_session)):
    a = _get(s, Aircraft, aid, "Борт")
    a.image = images.validate_url(data.image)
    _commit(s)
    return {"image": a.image, "message": "Картинка борта обновлена" if a.image else "Картинка борта убрана"}


@router.post("/aircraft/{aid}/image")
async def upload_aircraft_image(aid: int, file: UploadFile = File(...), s: Session = Depends(get_session)):
    a = _get(s, Aircraft, aid, "Борт")
    a.image = images.save_upload(await file.read(), images.title_from_filename(file.filename))
    _commit(s)
    return {"image": a.image, "message": "Фото загружено и назначено борту"}


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
               "required": slot.get("required", True), "component": None}
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
    mids = _manual_ids(s)
    return {
        **aircraft_brief(a, tot, summ, set(current)),
        "initial_hours": a.initial_hours, "initial_cycles": a.initial_cycles, "notes": a.notes,
        "configuration": config, "due": items, "installations": history,
        "sensor_events": [sensors.event_dict(e) for e in s.scalars(select(SensorEvent).where(SensorEvent.aircraft_id == aid)
                                                                     .order_by(SensorEvent.occurred_at.desc()))],
        "flight_log": [_flight_dict(f, mids) for f in flights],
        "maintenance_records": [record_dict(r) for r in records],
    }


def _manual_ids(s: Session) -> set[int]:
    return set(s.scalars(select(LogImport.id).where(LogImport.format == "manual")))


def _flight_dict(f: Flight, manual_ids: set[int] | None = None) -> dict:
    return {"events": len(f.events),"manual": bool(manual_ids and f.import_id in manual_ids), "source": "flight","id": f.id, "aircraft_id": f.aircraft_id, "aircraft_name": f.aircraft.name, "external_id": f.external_id,
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
    items = due_items(s)
    worst, resid = _worst_by_component(items), _residual_by_component(items)
    return [component_brief(c, tots[c.id], cur.get(c.id), worst.get(c.id), resid.get(c.id)) for c in comps]


@router.post("/components", status_code=201)
def create_component(data: ComponentIn, s: Session = Depends(get_session)):
    _get(s, ComponentType, data.type_id, "Тип узла")
    if s.scalar(select(Component).where(Component.serial == data.serial)):
        raise DomainError("Узел с таким серийным номером уже существует", 409)
    c = Component(**data.model_dump(exclude={"in_service_at", "manufactured_at"}),
                  in_service_at=_dt(data.in_service_at) or utcnow(), manufactured_at=_dt(data.manufactured_at))
    s.add(c)
    _commit(s)
    return {"id": c.id, "uid": c.uid, "serial": c.serial}


@router.patch("/components/{cid}")
def patch_component(cid: int, data: ComponentPatch, s: Session = Depends(get_session)):
    c = _get(s, Component, cid, "Узел")
    upd = data.model_dump(exclude_unset=True)
    if "status" in upd and upd["status"] is None:
        raise DomainError("Укажите статус узла")
    if "status" in upd and c.status == "installed":
        raise DomainError("Статус установленного узла меняется операцией снятия")
    if "manufactured_at" in upd:
        upd["manufactured_at"] = _dt(upd["manufactured_at"])
    if "specs" in upd and upd["specs"] is None:
        upd["specs"] = []
    for k, v in upd.items():
        setattr(c, k, v)
    _commit(s)
    return {"ok": True}


def passport_snapshot(s: Session, cid: int) -> dict:
    """Краткий паспорт узла: местоположение, наработка, остаточный ресурс, статус ТО."""
    c = _get(s, Component, cid, "Узел")
    items = due_items(s, component_id=cid)
    tot = component_totals(s, [c])[c.id]
    cur = next(iter(s.scalars(select(Installation).where(Installation.component_id == cid, Installation.removed_at.is_(None)))), None)
    return component_brief(c, tot, cur, _worst_by_component(items).get(c.id), _residual_by_component(items).get(c.id))


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
        mids = _manual_ids(s)
        fl = [_flight_dict(f, mids) for f in s.scalars(select(Flight).where(or_(*conds))
                                                      .order_by(Flight.started_at.desc()).limit(300))]
    entries = [manual.entry_dict(e) for e in s.scalars(select(ComponentUsageEntry).where(
        ComponentUsageEntry.component_id == cid).order_by(ComponentUsageEntry.started_at.desc()))]
    for e in entries:
        fl.append({"source": "entry", "id": e["id"], "started_at": e["started_at"], "ended_at": None,
                   "duration_h": e["hours"], "cycles": e["cycles"], "mission": e["kind_label"] + (f": {e['note']}" if e["note"] else ""),
                   "aircraft_id": None, "aircraft_name": "вне борта", "external_id": "—", "import_id": None, "manual": True})
        timeline.append({"at": e["started_at"], "kind": "entry",
                         "title": f"{e['kind_label']}: +{e['hours']} ч, +{e['cycles']} цикл. (вне борта)",
                         "details": " · ".join(x for x in (e["note"], e["created_by"]) if x)})
    fl.sort(key=lambda f: f["started_at"], reverse=True)
    timeline.sort(key=lambda e: e["at"], reverse=True)
    worst = _worst_by_component(items).get(c.id)
    return {
        **component_brief(c, tot, cur, worst, _residual_by_component(items).get(c.id)),
        "notes": c.notes,
        "on_board_hours": round(stint.get(cur.id, Usage()).hours, 2) if cur else None,
        "boards_count": len({i.aircraft_id for i in installs}),
        "stints": list(reversed(stints)), "due": items, "maintenance_records": records,
        "timeline": timeline, "flight_log": fl, "usage_entries": entries,
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
@router.post("/components/{cid}/usage", status_code=201)
def add_usage(cid: int, data: UsageEntryIn, s: Session = Depends(get_session)):
    c = _get(s, Component, cid, "Узел")
    e = manual.add_usage_entry(s, c, _dt(data.started_at), data.hours, data.cycles, data.kind, data.note, data.created_by)
    _commit(s)
    return {"entry": manual.entry_dict(e),
            "message": f"Записано вне борта: +{e.hours} ч, +{e.cycles} цикл. Начислено только узлу {c.serial}"}


@router.delete("/components/{cid}/usage/{eid}")
def delete_usage(cid: int, eid: int, s: Session = Depends(get_session)):
    e = _get(s, ComponentUsageEntry, eid, "Запись")
    if e.component_id != cid:
        raise NotFound("Запись не найдена")
    s.delete(e)
    _commit(s)
    return {"message": "Запись внебортовой наработки удалена"}


@router.post("/flights", status_code=201)
def add_flight(data: ManualFlightIn, s: Session = Depends(get_session)):
    a = _get(s, Aircraft, data.aircraft_id, "Борт")
    comp = _get(s, Component, data.component_id, "Узел") if data.component_id else None
    res = manual.add_manual_flight(s, a, parse_dt(data.start), parse_dt(data.end), data.cycles, data.mission,
                                   data.pilot, data.flight_id, comp, data.performed_by)
    _commit(s)
    return res


@router.delete("/flights/{fid}")
def delete_flight(fid: int, s: Session = Depends(get_session)):
    res = manual.delete_flight(s, _get(s, Flight, fid, "Полёт"))
    _commit(s)
    return res


@router.get("/flights")
def list_flights(aircraft_id: int | None = None, import_id: int | None = None, limit: int = Query(200, ge=1, le=2000),
                 s: Session = Depends(get_session)):
    q = select(Flight).order_by(Flight.started_at.desc()).limit(limit)
    if aircraft_id:
        q = q.where(Flight.aircraft_id == aircraft_id)
    if import_id:
        q = q.where(Flight.import_id == import_id)
    mids = _manual_ids(s)
    return [_flight_dict(f, mids) for f in s.scalars(q)]


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


@router.get("/templates/movements.xlsx")
def movements_template():
    from fastapi.responses import Response

    from ..services.movements import template_xlsx
    return Response(template_xlsx(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="tbo-tracker_dvizhenie_uzlov.xlsx"'})


@router.get("/demo-logs")
def demo_logs():
    require_demo_mode()
    import json
    idx = DEMO_LOGS_DIR / "index.json"
    return json.loads(idx.read_text("utf-8")) if idx.exists() else []


@router.get("/demo-logs/{name}")
def demo_log_download(name: str):
    require_demo_mode()
    return FileResponse(_demo_file(name), filename=name)


@router.post("/demo-logs/{name}/import")
def demo_log_import(name: str, dry_run: bool = False, s: Session = Depends(get_session)):
    require_demo_mode()
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
        "warn_days": r.warn_days, "interval_events": r.interval_events, "warn_events": r.warn_events, "active": r.active,
    }


@router.get("/rules")
def list_rules(s: Session = Depends(get_session)):
    return [rule_dict(r) for r in s.scalars(select(MaintenanceRule).order_by(MaintenanceRule.target, MaintenanceRule.code))]


def _validate_rule(data: RuleIn):
    if not (data.interval_hours or data.interval_cycles or data.interval_days or data.interval_events):
        raise DomainError("Задайте хотя бы один порог: часы, циклы, дни или падения/столкновения")
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
                        limit: int = Query(300, ge=1, le=5000), s: Session = Depends(get_session)):
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


# ---------------------------------------------------------------- справочники типов (свои узлы и борта)
@router.post("/component-types", status_code=201)
def create_component_type(data: ComponentTypeIn, s: Session = Depends(get_session)):
    if s.scalar(select(ComponentType).where(ComponentType.code == data.code)):
        raise DomainError("Тип узла с таким кодом уже есть", 409)
    t = ComponentType(**data.model_dump())
    s.add(t)
    _commit(s)
    return {"id": t.id, "message": f"Тип узла «{t.name}» создан. Добавьте позиции для него в типы БПЛА"}


@router.put("/component-types/{tid}")
def update_component_type(tid: int, data: ComponentTypeIn, s: Session = Depends(get_session)):
    t = _get(s, ComponentType, tid, "Тип узла")
    if data.code != t.code:
        raise DomainError("Код типа менять нельзя (на него ссылаются позиции бортов)")
    t.name, t.description, t.spec_fields = data.name, data.description, data.spec_fields
    _commit(s)
    return {"message": "Тип узла обновлён"}


@router.delete("/component-types/{tid}")
def delete_component_type(tid: int, s: Session = Depends(get_session)):
    t = _get(s, ComponentType, tid, "Тип узла")
    if s.scalar(select(Component.id).where(Component.type_id == tid).limit(1)):
        raise DomainError("Есть узлы этого типа — удаление невозможно")
    if s.scalar(select(MaintenanceRule.id).where(MaintenanceRule.component_type_id == tid).limit(1)):
        raise DomainError("На тип ссылаются регламенты — сначала удалите их")
    for at in s.scalars(select(AircraftType)):
        if any(sl["component_type"] == t.code for sl in at.slots or []):
            raise DomainError(f"Тип используется в позициях типа БПЛА «{at.name}»")
    s.delete(t)
    _commit(s)
    return {"message": "Тип узла удалён"}


def _validate_slots(s: Session, slots) -> list[dict]:
    codes = set(s.scalars(select(ComponentType.code)))
    seen = set()
    out = []
    for sl in slots:
        if sl.position in seen:
            raise DomainError(f"Позиция «{sl.position}» указана дважды")
        if sl.component_type not in codes:
            raise DomainError(f"Неизвестный тип узла «{sl.component_type}»")
        seen.add(sl.position)
        out.append(sl.model_dump())
    return out


@router.post("/aircraft-types", status_code=201)
def create_aircraft_type(data: AircraftTypeIn, s: Session = Depends(get_session)):
    if s.scalar(select(AircraftType).where(AircraftType.code == data.code)):
        raise DomainError("Тип БПЛА с таким кодом уже есть", 409)
    t = AircraftType(code=data.code, name=data.name, category=data.category, manufacturer=data.manufacturer,
                     slots=_validate_slots(s, data.slots))
    s.add(t)
    _commit(s)
    return {"id": t.id, "message": f"Тип БПЛА «{t.name}» создан"}


@router.put("/aircraft-types/{tid}")
def update_aircraft_type(tid: int, data: AircraftTypeIn, s: Session = Depends(get_session)):
    t = _get(s, AircraftType, tid, "Тип БПЛА")
    if data.code != t.code:
        raise DomainError("Код типа менять нельзя")
    slots = _validate_slots(s, data.slots)
    new = {sl["position"]: sl for sl in slots}
    ac_ids = list(s.scalars(select(Aircraft.id).where(Aircraft.type_id == tid)))
    if ac_ids:
        for inst in s.scalars(select(Installation).where(Installation.aircraft_id.in_(ac_ids),
                                                         Installation.removed_at.is_(None))):
            sl = new.get(inst.position)
            if sl is None:
                raise DomainError(f"Позиция {inst.position} занята узлом {inst.component.serial} на {inst.aircraft.name} — сначала снимите узел")
            if sl["component_type"] != inst.component.type.code:
                raise DomainError(f"В позиции {inst.position} стоит узел другого типа ({inst.component.serial})")
    t.name, t.category, t.manufacturer, t.slots = data.name, data.category, data.manufacturer, slots
    _commit(s)
    return {"message": "Тип БПЛА обновлён"}


@router.delete("/aircraft-types/{tid}")
def delete_aircraft_type(tid: int, s: Session = Depends(get_session)):
    t = _get(s, AircraftType, tid, "Тип БПЛА")
    if s.scalar(select(Aircraft.id).where(Aircraft.type_id == tid).limit(1)):
        raise DomainError("Есть борта этого типа — удаление невозможно")
    if s.scalar(select(MaintenanceRule.id).where(MaintenanceRule.aircraft_type_id == tid).limit(1)):
        raise DomainError("На тип ссылаются регламенты")
    s.delete(t)
    _commit(s)
    return {"message": "Тип БПЛА удалён"}


# ---------------------------------------------------------------- генератор журналов
@router.post("/log-generator")
def log_generator(data: GeneratorIn, s: Session = Depends(get_session)):
    from ..loggen import GenParams, generate

    p = GenParams(data.aircraft_serials, parse_dt(data.date_from), parse_dt(data.date_to), data.format,
                  data.fly_probability, data.flights_min, data.flights_max, data.duration_min, data.duration_max,
                  data.first_takeoff_hour, data.touch_and_go_probability, data.with_ids, data.avoid_overlap,
                  data.duplicates, data.errors, data.seed)
    if data.import_now and not data.dry_run:
        from .. import config
        if not config.DEMO_MODE:
            raise DomainError("Запись сгенерированных (вымышленных) полётов в базу доступна только в демо-режиме. "
                              "В рабочей эксплуатации скачайте файл или используйте «Проверить импорт (без записи)»", 403)
    res = generate(s, p)
    if data.import_now:
        res["import"] = _run_import(s, res["content"].encode(), res["filename"], "генератор журналов", data.dry_run)
    return res


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
    require_demo_mode()
    from ..seed import reset_and_seed
    return reset_and_seed()


# ---------------------------------------------------------------- центр уведомлений
NOTIFY_CATEGORIES = {
    "approaching": "Приближается срок ТО / ресурса",
    "limit": "Достигнут предельный ресурс",
    "overdue": "Просрочены регламентные работы",
}


@router.get("/notifications")
def notifications(days: int = Query(90, ge=1, le=3650), s: Session = Depends(get_session)):
    """Автоматические уведомления: три категории по расчёту регламентов + свежие события датчиков.

    «Предельный ресурс» — порог регламента вида «назначенный ресурс» (life) или индивидуального ресурса
    экземпляра достигнут: узел/борт подлежит замене или списанию. «Просрочено» — периодическая работа
    не выполнена к сроку. «Приближается» — остаток меньше порога предупреждения.
    """
    from datetime import timedelta

    from ..models import SensorEvent
    from ..services import sensors
    from ..timeutil import utcnow

    groups = {k: [] for k in NOTIFY_CATEGORIES}
    for it in due_items(s, statuses={"overdue", "due_soon"}):
        if it["status"] == "due_soon":
            cat = "approaching"
        else:
            cat = "limit" if it["rule"]["kind"] == "life" else "overdue"
        groups[cat].append({**it, "category": cat})
    since = utcnow() - timedelta(days=days)
    evs = s.scalars(select(SensorEvent).where(SensorEvent.occurred_at >= since).order_by(SensorEvent.occurred_at.desc()))
    events = []
    for e in evs:
        d = sensors.event_dict(e)
        a = s.get(Aircraft, e.aircraft_id)
        d["aircraft"] = {"id": a.id, "name": a.name, "serial": a.serial} if a else None
        events.append(d)
    counts = {k: len(v) for k, v in groups.items()}
    counts["sensor_events"] = len(events)
    counts["total"] = counts["approaching"] + counts["limit"] + counts["overdue"]
    return {"generated_at": iso(utcnow()), "categories": NOTIFY_CATEGORIES, "counts": counts,
            "items": groups, "sensor_events": events, "days": days}


# ---------------------------------------------------------------- доска перестановки узлов (drag & drop)
@router.get("/board")
def board(s: Session = Depends(get_session)):
    """Все борта с позициями и установленными узлами + резерв/ремонт — для интерактивной перестановки."""
    ev = Evaluator(s)
    items = due_items(s, evaluator=ev)
    summ = _summary_by_aircraft(items)
    worst = _worst_by_component(items)
    residual = _residual_by_component(items)
    aircraft = list(s.scalars(select(Aircraft).where(Aircraft.status != "retired").order_by(Aircraft.serial)))
    cur = current_installations(s)
    by_pos = {(i.aircraft_id, i.position): i for i in cur.values()}
    comps = list(s.scalars(select(Component).where(Component.status != "retired")))
    ctot = component_totals(s, comps)
    tots = aircraft_totals(s, aircraft)
    out = []
    for a in aircraft:
        slots, positions = [], set()
        for slot in a.type.slots or []:
            inst = by_pos.get((a.id, slot["position"]))
            if inst:
                positions.add(slot["position"])
            c = inst.component if inst else None
            slots.append({"position": slot["position"], "label": slot["label"], "component_type": slot["component_type"],
                          "required": slot.get("required", True),
                          "component": component_brief(c, ctot[c.id], inst, worst.get(c.id), residual.get(c.id)) if c else None})
        out.append({**aircraft_brief(a, tots[a.id], summ[a.id], positions), "slots": slots})
    spare = [component_brief(c, ctot[c.id], None, worst.get(c.id), residual.get(c.id))
             for c in sorted(comps, key=lambda c: (c.type.name, c.serial)) if c.id not in cur]
    return {"aircraft": out, "spare": spare}
