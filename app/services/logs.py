"""Импорт журналов эксплуатации и автоматическое начисление наработки.

Поддерживаемые форматы (см. docs/LOG_FORMAT.md):
  * JSON  «sokol-flightlog/1.0» — журнал одного или нескольких бортов;
  * CSV   — одна строка = один полёт (удобно для выгрузки из НСУ/Excel).

Защита от двойного начисления — на двух уровнях:
  1. файл: SHA-256 содержимого; повторная загрузка того же файла ничего не меняет;
  2. полёт: уникальный ключ (борт, flight_id) в БД + проверка пересечения по времени
     с уже учтёнными полётами того же борта (на случай другого ID в другой выгрузке).
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from ..config import FUTURE_TOLERANCE_MIN, MAX_FLIGHT_HOURS
from ..errors import DomainError
from ..models import Aircraft, Flight, LogImport
from ..timeutil import iso, parse_dt, utcnow
from .maintenance import Evaluator, due_items
from .usage import installed_at

FORMAT_ID = "sokol-flightlog/1.0"


class ParsedFlight(dict):
    pass


def _row(aircraft_serial, flight_id, start, end, cycles=None, mission=None, pilot=None, line=None):
    return ParsedFlight(aircraft_serial=(aircraft_serial or "").strip(), flight_id=(str(flight_id).strip() if flight_id not in (None, "") else None),
                        start=start, end=end, cycles=cycles, mission=mission, pilot=pilot, line=line)


def parse_log(content: bytes, filename: str) -> tuple[str, list[ParsedFlight], dict]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = content.decode("cp1251")
        except UnicodeDecodeError as e:  # pragma: no cover
            raise DomainError("Не удалось определить кодировку файла (ожидается UTF-8)") from e
    stripped = text.lstrip()
    if not stripped:
        raise DomainError("Файл пуст")
    if stripped[0] in "{[":
        return _parse_json(stripped)
    return _parse_csv(text)


def _parse_json(text: str):
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise DomainError(f"Ошибка разбора JSON: {e.msg} (строка {e.lineno})") from e
    meta = {}
    flights_raw = []
    if isinstance(data, list):
        flights_raw = data
        default_serial = None
    elif isinstance(data, dict):
        fmt = data.get("format")
        if fmt and not str(fmt).startswith("sokol-flightlog/"):
            raise DomainError(f"Неизвестный формат журнала: {fmt}")
        meta = {k: data.get(k) for k in ("log_id", "source", "generated_at", "operator") if data.get(k)}
        default_serial = data.get("aircraft_serial")
        flights_raw = data.get("flights")
        if not isinstance(flights_raw, list):
            raise DomainError("В журнале нет массива flights")
    else:
        raise DomainError("Корень JSON должен быть объектом или массивом")
    rows = []
    for idx, f in enumerate(flights_raw, 1):
        if not isinstance(f, dict):
            rows.append(_row(None, None, None, None, line=idx))
            continue
        rows.append(_row(f.get("aircraft_serial") or default_serial, f.get("flight_id"),
                         f.get("start"), f.get("end"), f.get("cycles"), f.get("mission"), f.get("pilot"), idx))
    return "json", rows, meta


CSV_ALIASES = {
    "aircraft_serial": ("aircraft_serial", "aircraft", "serial", "борт"),
    "flight_id": ("flight_id", "id", "flight"),
    "start": ("start_utc", "start", "takeoff", "взлёт"),
    "end": ("end_utc", "end", "landing", "посадка"),
    "cycles": ("cycles", "циклы"),
    "mission": ("mission", "задание"),
    "pilot": ("pilot", "оператор"),
}


def _parse_csv(text: str):
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines:
        raise DomainError("CSV не содержит данных")
    delim = ";" if lines[0].count(";") > lines[0].count(",") else ","
    reader = csv.DictReader(io.StringIO("\n".join(lines)), delimiter=delim)
    headers = {h.strip().lower(): h for h in (reader.fieldnames or [])}
    colmap = {}
    for key, aliases in CSV_ALIASES.items():
        for a in aliases:
            if a in headers:
                colmap[key] = headers[a]
                break
    missing = [k for k in ("aircraft_serial", "start", "end") if k not in colmap]
    if missing:
        raise DomainError(f"В CSV нет обязательных колонок: {', '.join(missing)}")
    rows = []
    for idx, r in enumerate(reader, 1):
        g = lambda k: (r.get(colmap[k]) or "").strip() if k in colmap else None  # noqa: E731
        rows.append(_row(g("aircraft_serial"), g("flight_id"), g("start"), g("end"),
                         g("cycles") or None, g("mission") or None, g("pilot") or None, idx))
    return "csv", rows, {}


def _validate(rows, aircraft_by_serial, now):
    """Возвращает список (row, aircraft, start, end, cycles, ext_id) и ошибки."""
    ok, errors = [], []
    for r in rows:
        where = f"запись {r['line']}"
        try:
            if not r["aircraft_serial"]:
                raise ValueError("не указан серийный номер борта")
            ac = aircraft_by_serial.get(r["aircraft_serial"])
            if ac is None:
                raise ValueError(f"борт «{r['aircraft_serial']}» не зарегистрирован в парке")
            if ac.status == "retired":
                raise ValueError(f"борт {ac.serial} списан")
            try:
                start, end = parse_dt(r["start"]), parse_dt(r["end"])
            except (TypeError, ValueError):
                raise ValueError("некорректная дата/время взлёта или посадки (ожидается ISO-8601)")
            if end <= start:
                raise ValueError("время посадки раньше или равно времени взлёта")
            if (end - start) > timedelta(hours=MAX_FLIGHT_HOURS):
                raise ValueError(f"длительность полёта больше {MAX_FLIGHT_HOURS} ч")
            if end > now + timedelta(minutes=FUTURE_TOLERANCE_MIN):
                raise ValueError("полёт датирован будущим временем")
            if start < ac.in_service_at:
                raise ValueError(f"полёт раньше ввода борта {ac.serial} в эксплуатацию")
            cycles = 1 if r["cycles"] in (None, "") else int(float(r["cycles"]))
            if cycles < 0 or cycles > 1000:
                raise ValueError("некорректное число циклов")
        except ValueError as e:
            errors.append(f"{where}: {e}")
            continue
        ext = r["flight_id"] or "auto-" + hashlib.sha1(
            f"{ac.serial}|{start.isoformat()}|{end.isoformat()}".encode()).hexdigest()[:16]
        ok.append((r, ac, start, end, cycles, ext))
    return ok, errors


def import_log(s: Session, content: bytes, filename: str, imported_by: str | None = None,
               dry_run: bool = False, compute_alerts: bool = True) -> dict:
    """Импорт журнала в рамках текущей транзакции. При dry_run изменения откатываются вызывающим кодом."""
    now = utcnow()
    sha = hashlib.sha256(content).hexdigest()
    prev = s.scalar(select(LogImport).where(LogImport.sha256 == sha, LogImport.status.in_(("imported", "partial")))
                    .order_by(LogImport.id).limit(1))
    if prev is not None:
        imp = LogImport(filename=filename, sha256=sha, format=prev.format, status="duplicate",
                        imported_at=now, imported_by=imported_by, duplicate_of_id=prev.id,
                        flights_total=prev.flights_total, flights_duplicate=prev.flights_total,
                        messages=[f"Файл уже был импортирован {iso(prev.imported_at)} (импорт №{prev.id}). "
                                  "Наработка повторно не начислена."], summary={})
        if not dry_run:
            s.add(imp)
            s.flush()
        return import_dict(imp, dry_run=dry_run)

    try:
        fmt, rows, meta = parse_log(content, filename)
    except DomainError as e:
        imp = LogImport(filename=filename, sha256=sha, status="rejected", imported_at=now,
                        imported_by=imported_by, messages=[e.message], summary={})
        if not dry_run:
            s.add(imp)
            s.flush()
        return import_dict(imp, dry_run=dry_run)

    serials = {r["aircraft_serial"] for r in rows if r["aircraft_serial"]}
    aircraft_by_serial = {a.serial: a for a in s.scalars(select(Aircraft).where(Aircraft.serial.in_(serials)))}
    valid, messages = _validate(rows, aircraft_by_serial, now)

    imp = LogImport(filename=filename, sha256=sha, format=fmt, status="imported", imported_at=now,
                    imported_by=imported_by, flights_total=len(rows), messages=[], summary={})
    s.add(imp)
    s.flush()

    # состояние регламентов ДО импорта — чтобы показать, какие напоминания сработали
    affected_aircraft = {ac.id for _, ac, *_ in valid}
    before = _due_snapshot(s, affected_aircraft, now) if compute_alerts else {}

    duplicates = 0
    new_flights: list[Flight] = []
    seen_keys: set[tuple] = set()
    for r, ac, start, end, cycles, ext in sorted(valid, key=lambda v: v[2]):
        key = (ac.id, ext)
        exists = key in seen_keys or s.scalar(
            select(Flight.id).where(Flight.aircraft_id == ac.id, Flight.external_id == ext)) is not None
        if exists:
            duplicates += 1
            continue
        overlap = s.scalar(select(Flight).where(
            Flight.aircraft_id == ac.id, and_(Flight.started_at < end, Flight.ended_at > start)).limit(1))
        if overlap is not None:
            duplicates += 1
            messages.append(f"запись {r['line']}: полёт {ext} пересекается по времени с уже учтённым "
                            f"полётом {overlap.external_id} борта {ac.serial} — пропущен как дубликат")
            continue
        f = Flight(aircraft_id=ac.id, import_id=imp.id, external_id=ext, started_at=start, ended_at=end,
                   duration_h=round((end - start).total_seconds() / 3600, 4), cycles=cycles,
                   mission=r["mission"], pilot=r["pilot"])
        s.add(f)
        s.flush()
        seen_keys.add(key)
        new_flights.append(f)
        if ac.status != "active":
            messages.append(f"запись {r['line']}: борт {ac.serial} на момент импорта числится не в эксплуатации")

    # распределение наработки: борт + все узлы, установленные на момент начала полёта
    per_aircraft: dict[int, dict] = {}
    per_component: dict[int, dict] = {}
    incomplete: dict[int, set] = defaultdict(set)
    for f in new_flights:
        ac = f.aircraft
        a = per_aircraft.setdefault(ac.id, {"aircraft_id": ac.id, "serial": ac.serial, "name": ac.name,
                                             "flights": 0, "hours": 0.0, "cycles": 0,
                                             "first": f.started_at, "last": f.ended_at})
        a["flights"] += 1
        a["hours"] += f.duration_h
        a["cycles"] += f.cycles
        a["first"] = min(a["first"], f.started_at)
        a["last"] = max(a["last"], f.ended_at)
        insts = installed_at(s, ac.id, f.started_at)
        filled = {i.position for i in insts}
        for slot in ac.type.slots or []:
            if slot["position"] not in filled:
                incomplete[ac.id].add(slot["label"])
        for inst in insts:
            c = per_component.setdefault(inst.component_id, {
                "component_id": inst.component_id, "serial": inst.component.serial,
                "type": inst.component.type.name, "aircraft": ac.name, "position": inst.position,
                "flights": 0, "hours": 0.0, "cycles": 0})
            c["flights"] += 1
            c["hours"] += f.duration_h
            c["cycles"] += f.cycles
    for aid, labels in incomplete.items():
        ac = s.get(Aircraft, aid)
        messages.append(f"Внимание: часть полётов борта {ac.serial} выполнена при незаполненных позициях "
                        f"({', '.join(sorted(labels))}) — проверьте даты операций установки")

    after = _due_snapshot(s, affected_aircraft, now) if compute_alerts else {}
    alerts = []
    for key, item in after.items():
        old = before.get(key)
        if item["status"] != "ok" and (old is None or old["status"] != item["status"]):
            alerts.append({"entity": item["entity"], "rule": item["rule"], "status": item["status"],
                           "previous_status": old["status"] if old else None, "driver": item["driver"]})

    hours = sum(f.duration_h for f in new_flights)
    cycles = sum(f.cycles for f in new_flights)
    imp.flights_new = len(new_flights)
    imp.flights_duplicate = duplicates
    imp.flights_rejected = len(rows) - len(valid)
    imp.hours_added = round(hours, 4)
    imp.cycles_added = cycles
    if not new_flights:
        imp.status = "duplicate" if duplicates and not imp.flights_rejected else "rejected"
        if duplicates and imp.flights_rejected:
            imp.status = "rejected"
    elif duplicates or imp.flights_rejected:
        imp.status = "partial"
    if not rows:
        messages.append("Журнал не содержит полётов")
    imp.messages = messages
    imp.summary = {
        "meta": meta,
        "aircraft": [dict(v, hours=round(v["hours"], 2), first=iso(v["first"]), last=iso(v["last"]))
                     for v in per_aircraft.values()],
        "components": [dict(v, hours=round(v["hours"], 2)) for v in per_component.values()],
        "alerts": alerts,
    }
    s.flush()
    return import_dict(imp, dry_run=dry_run)


def _due_snapshot(s: Session, aircraft_ids: set[int], now: datetime) -> dict:
    out = {}
    for aid in aircraft_ids:
        ev = Evaluator(s, now)
        for item in due_items(s, aircraft_id=aid, evaluator=ev):
            out[item["key"]] = item
    return out


def rollback_import(s: Session, imp: LogImport) -> dict:
    if imp.status not in ("imported", "partial"):
        raise DomainError("Откатить можно только успешно выполненный импорт")
    flights = list(s.scalars(select(Flight).where(Flight.import_id == imp.id)))
    for f in flights:
        s.delete(f)
    imp.status = "rolled_back"
    imp.messages = list(imp.messages or []) + [f"Импорт отменён {iso(utcnow())}: удалено полётов {len(flights)}"]
    s.flush()
    return {"message": f"Импорт №{imp.id} отменён, наработка ({imp.hours_added:.2f} ч, {imp.cycles_added} цикл.) "
                       "списана со всех бортов и узлов", "flights_removed": len(flights)}


STATUS_LABELS = {
    "imported": "Импортирован", "partial": "Импортирован частично", "duplicate": "Повтор — не начислено",
    "rejected": "Отклонён", "rolled_back": "Отменён",
}


def import_dict(imp: LogImport, dry_run: bool = False) -> dict:
    return {
        "id": imp.id, "filename": imp.filename, "sha256": imp.sha256, "format": imp.format,
        "status": imp.status, "status_label": STATUS_LABELS.get(imp.status, imp.status),
        "imported_at": iso(imp.imported_at), "imported_by": imp.imported_by,
        "duplicate_of_id": imp.duplicate_of_id, "flights_total": imp.flights_total,
        "flights_new": imp.flights_new, "flights_duplicate": imp.flights_duplicate,
        "flights_rejected": imp.flights_rejected, "hours_added": round(imp.hours_added or 0, 2),
        "cycles_added": imp.cycles_added, "messages": imp.messages or [], "summary": imp.summary or {},
        "dry_run": dry_run,
    }
