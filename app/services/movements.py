"""Журналы движения узлов из Excel (.xlsx) и PDF: установка, снятие (демонтаж), перестановка между бортами.

Файл читается как набор таблиц (листы Excel, таблицы на страницах PDF). Колонки распознаются
по синонимам (регистр и порядок не важны). Таблица с колонкой «Операция» — журнал движения узлов;
таблица с колонками «Взлёт/Посадка» — журнал полётов (обрабатывается как CSV).

Операции применяются в хронологическом порядке теми же функциями, что и ручные операции
в интерфейсе (проверка совместимости позиции, занятости, статуса узла, дат). Каждая строка —
в отдельной точке сохранения: ошибка в строке не отменяет остальные. Повторная загрузка той же
строки (дата + операция + узел + борт + позиция) распознаётся и пропускается.
"""
from __future__ import annotations

import hashlib
import io
import re
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import DomainError
from ..models import Aircraft, Component, MovementRow
from ..timeutil import iso, parse_dt
from . import inventory
from .usage import current_installation


ALIASES = {
    "date": ("дата", "дата и время", "дата/время", "date", "datetime", "время"),
    "op": ("операция", "действие", "вид операции", "operation", "action"),
    "component": ("узел", "серийный номер узла", "серийный номер", "с/н узла", "component", "serial", "компонент"),
    "aircraft": ("борт", "на борт", "борт (куда)", "куда", "aircraft", "to"),
    "from": ("с борта", "борт (откуда)", "откуда", "from"),
    "position": ("позиция", "место", "посадочное место", "position", "slot"),
    "status": ("состояние после снятия", "после снятия", "статус", "status"),
    "reason": ("основание", "причина", "reason", "комментарий"),
    "by": ("исполнитель", "ответственный", "by", "engineer"),
}
OPS = {"установка": "install", "монтаж": "install", "install": "install",
       "снятие": "remove", "демонтаж": "remove", "remove": "remove",
       "перестановка": "transfer", "перенос": "transfer", "transfer": "transfer"}
STATUSES = {"резерв": "stock", "склад": "stock", "stock": "stock", "ремонт": "maintenance", "обслуживание": "maintenance",
            "на обслуживание": "maintenance", "maintenance": "maintenance", "списание": "retired", "списан": "retired",
            "retired": "retired"}
OP_LABELS = {"install": "Установка", "remove": "Снятие", "transfer": "Перестановка"}
FLIGHT_HEADERS = {"start_utc", "start", "взлёт", "takeoff"}


# ------------------------------------------------------------------ чтение таблиц
def _norm(h) -> str:
    return re.sub(r"\s+", " ", str(h or "").replace("\n", " ")).strip().lower().rstrip(":")


def _is_header(cells) -> bool:
    names = {_norm(c) for c in cells}
    known = {a for al in ALIASES.values() for a in al} | FLIGHT_HEADERS | {"aircraft_serial", "end_utc", "посадка"}
    return len(names & known) >= 2


MAX_XLSX_UNPACKED = 200 * 1024 * 1024      # защита от «zip-бомб» (xlsx — это zip-архив с XML)
MAX_PDF_PAGES = 300
MAX_TABLE_ROWS = 50000


def tables_from_xlsx(content: bytes) -> list[tuple[list, list[list]]]:
    import zipfile

    from openpyxl import load_workbook
    try:
        total = sum(i.file_size for i in zipfile.ZipFile(io.BytesIO(content)).infolist())
    except zipfile.BadZipFile as e:
        raise DomainError("Не удалось прочитать Excel: файл повреждён") from e
    if total > MAX_XLSX_UNPACKED:
        raise DomainError("Файл Excel слишком большой после распаковки (более 200 МБ)")
    try:
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as e:  # noqa: BLE001 — openpyxl бросает разные исключения на битых файлах
        raise DomainError(f"Не удалось прочитать Excel: {e}") from e
    out = []
    for ws in wb.worksheets:
        rows = []
        for r in ws.iter_rows(values_only=True):
            rows.append(list(r))
            if len(rows) > MAX_TABLE_ROWS:
                raise DomainError(f"На листе «{ws.title}» больше {MAX_TABLE_ROWS} строк — разбейте журнал на части")
        out += _split_tables(rows)
    return out


def tables_from_pdf(content: bytes) -> list[tuple[list, list[list]]]:
    import pdfplumber
    try:
        pdf = pdfplumber.open(io.BytesIO(content))
    except Exception as e:  # noqa: BLE001
        raise DomainError(f"Не удалось прочитать PDF: {e}") from e
    rows: list[list] = []
    with pdf:
        if len(pdf.pages) > MAX_PDF_PAGES:
            raise DomainError(f"В PDF больше {MAX_PDF_PAGES} страниц — разбейте журнал на части")
        for page in pdf.pages:
            tables = page.extract_tables()
            if tables:
                for tb in tables:
                    rows += [list(r) for r in tb]
            else:                                    # PDF без разметки таблиц: колонки через 2+ пробела или «|»
                for line in (page.extract_text() or "").splitlines():
                    parts = [p.strip() for p in re.split(r"\s*\|\s*|\s{2,}", line) if p.strip()]
                    if len(parts) >= 3:
                        rows.append(parts)
    if not rows:
        raise DomainError("В PDF не найдено таблиц — нужен текстовый PDF (не скан) с таблицей журнала")
    return _split_tables(rows)


def _split_tables(rows: list[list]) -> list[tuple[list, list[list]]]:
    """Делит строки на таблицы по строкам-заголовкам; повторяющиеся заголовки страниц пропускает."""
    out, header, body = [], None, []
    for r in rows:
        if not any(c not in (None, "") for c in r):
            continue
        if _is_header(r):
            if header is not None and [_norm(c) for c in r] == [_norm(c) for c in header]:
                continue                              # заголовок повторён на новой странице
            if header is not None:
                out.append((header, body))
            header, body = r, []
        elif header is not None:
            body.append(r)
    if header is not None:
        out.append((header, body))
    return out


def classify(header) -> str:
    names = {_norm(c) for c in header}
    if names & set(ALIASES["op"]):
        return "movements"
    if names & FLIGHT_HEADERS:
        return "flights"
    return "unknown"


def as_records(header, body) -> list[dict]:
    keys = [str(h) if h is not None else f"col{i}" for i, h in enumerate(header)]
    return [{k: (row[i] if i < len(row) else None) for i, k in enumerate(keys)} for row in body]


# ------------------------------------------------------------------ применение
def _colmap(header):
    names = {_norm(h): h for h in header if h is not None}
    out = {}
    for key, al in ALIASES.items():
        for a in al:
            if a in names:
                out[key] = names[a]
                break
    return out


def _date(v):
    if isinstance(v, datetime):
        return v.replace(microsecond=0)
    s = str(v or "").strip()
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})(?:[ ,T]+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$", s)
    try:
        if m:
            d, mo, y, hh, mm, ss = m.groups()
            return datetime(int(y), int(mo), int(d), int(hh or 0), int(mm or 0), int(ss or 0))
        return parse_dt(s)
    except ValueError:
        raise ValueError(f"дата «{s[:30]}» не распознана (ожидается 07.11.2026 09:30 или ISO-8601)") from None


def _check_source(s: Session, comp: Component, src_key: str, fleet: dict):
    """Борт «откуда» из журнала должен совпадать с тем, где узел числится: иначе строка описывает другую историю."""
    if not src_key:
        return
    expected = fleet.get(src_key.lower())
    if expected is None:
        raise DomainError(f"борт «{src_key}» не найден")
    inst = current_installation(s, comp.id)
    if inst is not None and inst.aircraft_id != expected.id:
        raise DomainError(f"узел {comp.serial} числится на борту {inst.aircraft.name}, а в журнале — «{src_key}»")


def import_movements(s: Session, tables, imp, dry_run: bool) -> dict:
    """Применяет таблицы журнала движения узлов. imp — запись LogImport (заполняется итогами)."""
    rows, messages = [], []
    for header, body in tables:
        cm = _colmap(header)
        miss = [n for k, n in (("date", "Дата"), ("op", "Операция"), ("component", "Узел")) if k not in cm]
        if miss:
            messages.append(f"Таблица пропущена: нет колонок {', '.join(miss)}")
            continue
        for rec in as_records(header, body):
            rows.append((len(rows) + 1, {k: rec.get(col) for k, col in cm.items()}))

    ops, done, dups, errors = [], [], 0, 0
    for n, r in rows:
        try:
            when = _date(r.get("date"))
            op = OPS.get(_norm(r.get("op")))
            if not op:
                raise ValueError(f"неизвестная операция «{r.get('op')}» (ожидается установка / снятие / перестановка)")
            ops.append((when, n, op, r))
        except (ValueError, TypeError) as e:
            errors += 1
            messages.append(f"строка {n}: {e}")
    ops.sort(key=lambda x: (x[0], x[1]))

    comps = {c.serial: c for c in s.scalars(select(Component))}
    fleet = {}
    for a in s.scalars(select(Aircraft)):
        fleet[a.serial.lower()] = a
        fleet[a.name.lower()] = a
    for when, n, op, r in ops:
        serial = str(r.get("component") or "").strip()
        ac_key = str(r.get("aircraft") or "").strip()
        pos = str(r.get("position") or "").strip().upper()
        key = hashlib.sha1(f"{when.isoformat()}|{op}|{serial}|{ac_key.lower()}|{pos}".encode()).hexdigest()
        if s.get(MovementRow, key):
            dups += 1
            messages.append(f"строка {n}: уже применена ранее — пропущена (повтор)")
            continue
        sp = s.begin_nested()
        try:
            comp = comps.get(serial)
            if comp is None:
                raise DomainError(f"узел «{serial}» не зарегистрирован")
            reason, by = (str(r.get(k)).strip() if r.get(k) not in (None, "") else None for k in ("reason", "by"))
            src_key = str(r.get("from") or "").strip()
            if op == "remove":
                _check_source(s, comp, src_key or ac_key, fleet)      # при снятии «Борт» — откуда снят
                st = _norm(r.get("status"))
                to = STATUSES.get(st) if st else "stock"
                if to is None:
                    raise DomainError(f"неизвестное состояние после снятия «{r.get('status')}» (резерв / ремонт / списание)")
                res = inventory.remove(s, comp, when, to, reason, by)
                target = None
            else:
                target = fleet.get(ac_key.lower())
                if target is None:
                    raise DomainError(f"борт «{ac_key}» не найден")
                if not pos:
                    raise DomainError("не указана позиция")
                if op == "transfer":
                    _check_source(s, comp, src_key, fleet)
                # позиции своих типов могут быть в любом регистре: «esc1» в журнале = «ESC1» у типа
                slot = next((sl for sl in target.type.slots or [] if str(sl["position"]).upper() == pos), None)
                fn = inventory.transfer if op == "transfer" else inventory.install
                res = fn(s, comp, target, slot["position"] if slot else pos, when, reason, by)
            s.add(MovementRow(key=key, import_id=imp.id))
            s.flush()
            sp.commit()
            done.append({"line": n, "at": iso(when), "op": op, "op_label": OP_LABELS[op], "serial": serial,
                         "component_id": comp.id, "aircraft": target.name if target else None,
                         "aircraft_id": target.id if target else None, "position": pos or None, "message": res["message"]})
        except DomainError as e:
            sp.rollback()
            errors += 1
            messages.append(f"строка {n}: {e.message}")
    imp.flights_total = len(rows)
    imp.flights_new = len(done)
    imp.flights_duplicate = dups
    imp.flights_rejected = errors
    if done:
        imp.status = "partial" if (dups or errors) else "imported"
    else:
        imp.status = "duplicate" if dups and not errors else "rejected"
    if not rows:
        messages.append("В файле не найдено строк журнала движения узлов")
    imp.messages = messages
    imp.summary = {"kind": "movements", "movements": done}
    s.flush()
    return {"movements": done}


# ------------------------------------------------------------------ шаблон для пользователей
TEMPLATE_HEADER = ["Дата", "Операция", "Узел", "Борт", "Позиция", "Состояние после снятия", "Основание", "Исполнитель"]


def template_xlsx() -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = "Журнал движения узлов"
    ws.append(TEMPLATE_HEADER)
    ws.append(["07.11.2026 09:00", "Снятие", "ESC40-0302", "", "", "Ремонт", "Нагрев по телеметрии", "Смирнов Д. А."])
    ws.append(["07.11.2026 09:30", "Установка", "ESC40-0324", "Борт 01", "ESC2", "", "Замена из резерва", "Смирнов Д. А."])
    ws.append(["07.11.2026 10:30", "Перестановка", "ESC40-0320", "Борт 11", "ESC4", "", "Перестановка с Борт 12", "Орлов П. С."])
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="EEF1F6")
    for col, w in zip("ABCDEFGH", (18, 14, 14, 12, 10, 22, 30, 18)):
        ws.column_dimensions[col].width = w
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
