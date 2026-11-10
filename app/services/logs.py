"""Импорт журналов эксплуатации и автоматическое начисление наработки.

Поддерживаемые форматы (см. docs/LOG_FORMAT.md):
  * JSON  «tbo-flightlog/1.0» — журнал одного или нескольких бортов;
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
from ..models import Aircraft, Flight, LogImport, SensorEvent
from ..timeutil import iso, parse_dt, utcnow
from . import movements, sensors
from .maintenance import Evaluator, due_items
from .usage import installed_at

FORMAT_ID = "tbo-flightlog/1.0"


class ParsedFlight(dict):
    pass


def _row(aircraft_serial, flight_id, start, end, cycles=None, mission=None, pilot=None, line=None, extra=None):
    return ParsedFlight(aircraft_serial=(str(aircraft_serial or "")).strip(), flight_id=(str(flight_id).strip() if flight_id not in (None, "") else None),
                        start=start, end=end, cycles=cycles, mission=mission, pilot=pilot, line=line, extra=extra or {})


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
        if fmt and not str(fmt).startswith("tbo-flightlog/"):
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
        extra = {k: f[k] for k in ("imu", "events", "collisions", "falls") if k in f}
        rows.append(_row(f.get("aircraft_serial") or default_serial, f.get("flight_id"),
                         f.get("start"), f.get("end"), f.get("cycles"), f.get("mission"), f.get("pilot"), idx, extra))
    return "json", rows, meta


CSV_ALIASES = {
    "aircraft_serial": ("aircraft_serial", "aircraft", "serial", "борт"),
    "flight_id": ("flight_id", "id", "flight"),
    "start": ("start_utc", "start", "takeoff", "взлёт"),
    "end": ("end_utc", "end", "landing", "посадка"),
    "cycles": ("cycles", "циклы"),
    "mission": ("mission", "задание"),
    "pilot": ("pilot", "оператор"),
    "collisions": ("collisions", "столкновения", "столкновений"),
    "falls": ("falls", "падения", "падений"),
}


def _parse_csv(text: str):
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines:
        raise DomainError("CSV не содержит данных")
    delim = ";" if lines[0].count(";") > lines[0].count(",") else ","
    reader = csv.DictReader(io.StringIO("\n".join(lines)), delimiter=delim)
    return "csv", rows_from_table(reader.fieldnames or [], list(reader)), {}


def rows_from_table(fieldnames, records, what="CSV"):
    """Строки таблицы (CSV / Excel / PDF) → записи полётов по синонимам колонок."""
    headers = {str(h).strip().lower(): h for h in fieldnames if h is not None}
    colmap = {}
    for key, aliases in CSV_ALIASES.items():
        for a in aliases:
            if a in headers:
                colmap[key] = headers[a]
                break
    missing = [k for k in ("aircraft_serial", "start", "end") if k not in colmap]
    if missing:
        raise DomainError(f"В {what} нет обязательных колонок: {', '.join(missing)}")
    rows = []
    for idx, r in enumerate(records, 1):
        g = lambda k: (str(r.get(colmap[k]) if r.get(colmap[k]) is not None else "")).strip() if k in colmap else None  # noqa: E731
        extra = {k: g(k) for k in ("collisions", "falls") if k in colmap and g(k)}
        rows.append(_row(g("aircraft_serial"), g("flight_id"), g("start"), g("end"),
                         g("cycles") or None, g("mission") or None, g("pilot") or None, idx, extra))
    return rows


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
            try:
                cyc = 1.0 if r["cycles"] in (None, "") else float(r["cycles"])
            except (TypeError, ValueError, OverflowError):
                raise ValueError(f"некорректное число циклов «{str(r['cycles'])[:20]}»") from None
            if cyc != cyc or cyc < 0 or cyc > 1000 or cyc != int(cyc):
                raise ValueError("число циклов должно быть целым от 0 до 1000")
            cycles = int(cyc)
            for fld, limit in (("mission", 300), ("pilot", 200)):
                if r[fld] is not None and not isinstance(r[fld], (str, int, float)):
                    raise ValueError(f"поле {fld} должно быть строкой")
                r[fld] = None if r[fld] in (None, "") else str(r[fld])[:limit]
        except ValueError as e:
            errors.append(f"{where}: {e}")
            continue
        if r["flight_id"] and len(r["flight_id"]) > 200:
            errors.append(f"{where}: flight_id длиннее 200 символов")
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
    # Файл целиком — повтор, только если в прошлый раз в нём не было отклонённых строк. Иначе (частичный
    # импорт с ошибками) файл обрабатывается снова: после исправления данных (зарегистрировали борт или узел)
    # применяются ранее отклонённые строки, а уже учтённые полёты и операции отсекает защита на уровне строк.
    prev = s.scalar(select(LogImport).where(
        LogImport.sha256 == sha,
        (LogImport.status == "imported") | ((LogImport.status == "partial") & (LogImport.flights_rejected == 0)))
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

    # Excel / PDF: журнал движения узлов или журнал полётов в табличном виде
    if content[:2] == b"PK" or content[:5] == b"%PDF-":
        fmt = "xlsx" if content[:2] == b"PK" else "pdf"
        try:
            try:
                tables = movements.tables_from_xlsx(content) if fmt == "xlsx" else movements.tables_from_pdf(content)
            except DomainError:
                raise
            except Exception as e:  # noqa: BLE001 — повреждённый файл не должен давать 500
                raise DomainError(f"Не удалось разобрать файл {fmt.upper()}: {type(e).__name__}") from e
            kinds = {movements.classify(h) for h, _ in tables}
            if not tables or kinds == {"unknown"}:
                raise DomainError("Не найдена таблица журнала: нужны колонки «Дата, Операция, Узел, Борт, Позиция» "
                                  "(движение узлов) или «Борт, Взлёт, Посадка» (полёты)")
        except DomainError as e:
            imp = LogImport(filename=filename, sha256=sha, format=fmt, status="rejected", imported_at=now,
                            imported_by=imported_by, messages=[e.message], summary={})
            if not dry_run:
                s.add(imp)
                s.flush()
            return import_dict(imp, dry_run=dry_run)
        if "movements" in kinds:
            imp = LogImport(filename=filename, sha256=sha, format=f"{fmt}-движение", status="imported", imported_at=now,
                            imported_by=imported_by, messages=[], summary={})
            s.add(imp)
            s.flush()
            movements.import_movements(s, [t for t in tables if movements.classify(t[0]) == "movements"], imp, dry_run)
            return import_dict(imp, dry_run=dry_run)
        content_rows = []
        for h, body in tables:
            if movements.classify(h) == "flights":
                content_rows += movements.as_records(h, body)
        header = next(h for h, _ in tables if movements.classify(h) == "flights")
    try:
        if content[:2] == b"PK" or content[:5] == b"%PDF-":
            rows, meta = rows_from_table(header, content_rows, "Excel/PDF"), {}
        else:
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
    new_events: list = []
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
        evs, warns = sensors.analyze_flight(start, r.get("extra"), end)
        for w in warns:                     # полёт учитывается; неверная часть данных датчиков — с причиной
            messages.append(f"запись {r['line']}: данные датчиков не учтены — {w}")
        for ev in evs:
            se = SensorEvent(flight_id=f.id, aircraft_id=ac.id, occurred_at=ev["at"], kind=ev["kind"],
                             peak_g=ev["peak_g"], source=ev["source"], details=ev["details"])
            s.add(se)
            new_events.append((ac, se))
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
            if slot.get("required", True) and slot["position"] not in filled:
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
        "sensor_events": [{"aircraft": ac.name, "aircraft_id": ac.id, "at": iso(e.occurred_at), "kind": e.kind,
                           "kind_label": sensors.KIND_LABELS[e.kind], "peak_g": e.peak_g, "source": e.source} for ac, e in new_events],
    }
    for ac, e in new_events:
        messages.append(f"Датчики: {sensors.KIND_LABELS[e.kind].lower()} борта {ac.serial} {e.occurred_at:%d.%m.%Y %H:%M} UTC"
                        + (f", пик {e.peak_g} g" if e.peak_g else ""))
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
    if (imp.summary or {}).get("kind") == "movements":
        raise DomainError("Журнал движения узлов не откатывается целиком: исправьте установки и снятия операциями "
                          "в карточках узлов (история перемещений сохраняется)")
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
