"""Эталонный набор журнала движения узлов (Excel и PDF) и проверка характеристики Х6.

    python scripts/movements_reference.py --check            проверка на чистой демо-базе (итог — ошибки обработки, Х6)
    python scripts/movements_reference.py --check --json     то же в JSON (для scripts/run_acceptance.py)
    python scripts/movements_reference.py --export docs/examples
                                                             файлы набора и таблица ожидаемого итога (Markdown)

Набор — 21 строка: 10 верных операций (снятие в ремонт и в резерв, установка из резерва, перестановка между бортами,
списание) и 11 ошибочных, у каждой своя причина отклонения. Строки — app/seed.py, movement_reference(): тот же набор
появляется в демо-журналах (№ 11 и 12) при каждом сбросе демо-данных, с датами, согласованными с демо-базой.

Проверка (каждый шаг — на временной базе, рабочие данные не затрагиваются):
  1. чистая демо-база → Excel: применены ровно 10 верных строк, 11 ошибочных отклонены с ожидаемой причиной;
  2. тот же Excel повторно: не применено ни одной строки (10 — «уже применена», 11 — отклонены);
  3. чистая демо-база → PDF: то же, что в шаге 1;
  4. Excel после PDF на той же базе: не применено ни одной строки.
Ошибка обработки — строка, итог которой не совпал с ожидаемым. Порог Х6 — 0.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if "TBO_DATA_DIR" not in os.environ:
    _tmp = tempfile.mkdtemp(prefix="tbo-movref-")
    os.environ["TBO_DATA_DIR"] = _tmp
    os.environ["TBO_DATABASE_URL"] = f"sqlite:///{_tmp}/movref.db"
os.environ["TBO_DEMO_MODE"] = "1"
os.environ["TBO_SCHEDULER"] = "0"
os.environ.setdefault("TBO_AUTO_SEED", "0")

XLSX, PDF = "movements-reference.xlsx", "movements-reference.pdf"


def _fresh(folder: Path) -> list[dict]:
    """Чистая демо-база и файлы набора с датами этой базы."""
    from app.db import SessionLocal
    from app.models import Setting
    from app.seed import reset_and_seed, write_movement_reference
    reset_and_seed()
    with SessionLocal() as s:
        day0 = datetime.fromisoformat(s.get(Setting, "demo_anchor").value["day0"].rstrip("Z"))
    return write_movement_reference(folder, day0, XLSX, PDF)


def _import(folder: Path, name: str) -> dict:
    from app.db import SessionLocal
    from app.services import logs
    with SessionLocal() as s:
        res = logs.import_log(s, (folder / name).read_bytes(), name, "эталонный набор")
        s.commit()
    return res


def compare(rows: list[dict], res: dict, repeat: bool = False) -> list[dict]:
    """Строки, итог которых не совпал с ожидаемым. repeat — повторная загрузка: применено быть не должно ничего."""
    applied = {m["line"] for m in (res.get("summary") or {}).get("movements", [])}
    by_line: dict[int, list[str]] = {}
    for m in res.get("messages") or []:
        mt = re.match(r"строка (\d+): (.*)", m)
        if mt:
            by_line.setdefault(int(mt.group(1)), []).append(mt.group(2))
    errors = []
    for r in rows:
        n, msgs = r["line"], by_line.get(r["line"], [])
        dup = any("уже применена" in x for x in msgs)
        if repeat:
            ok = n not in applied and (dup if r["expect"] == "applied" else (bool(msgs) and not dup))
            want = "повтор (не применена)" if r["expect"] == "applied" else "отклонена"
        elif r["expect"] == "applied":
            ok, want = n in applied, "применена"
        else:
            ok, want = n not in applied and any(r["why"] in x for x in msgs), f"отклонена: «{r['why']}»"
        if not ok:
            errors.append({"line": n, "serial": r["serial"], "expected": want,
                           "got": "применена" if n in applied else ("; ".join(msgs) or "нет сообщения")})
    return errors


def _step(name: str, rows: list[dict], res: dict, repeat: bool = False) -> dict:
    err = compare(rows, res, repeat)
    return {"step": name, "file": res.get("filename"), "import_id": res.get("id"), "status": res.get("status"),
            "applied": res.get("flights_new"), "duplicates": res.get("flights_duplicate"),
            "rejected": res.get("flights_rejected"), "errors": err}


def check() -> dict:
    folder = Path(tempfile.mkdtemp(prefix="tbo-movref-files-"))
    steps = []
    rows = _fresh(folder)
    steps.append(_step("Excel на чистой демо-базе", rows, _import(folder, XLSX)))
    steps.append(_step("тот же Excel повторно", rows, _import(folder, XLSX), repeat=True))
    rows = _fresh(folder)
    steps.append(_step("PDF на чистой демо-базе", rows, _import(folder, PDF)))
    steps.append(_step("тот же PDF повторно", rows, _import(folder, PDF), repeat=True))
    steps.append(_step("Excel после PDF на той же базе", rows, _import(folder, XLSX), repeat=True))
    valid = sum(1 for r in rows if r["expect"] == "applied")
    return {"rows": len(rows), "valid": valid, "invalid": len(rows) - valid,
            "errors": sum(len(s["errors"]) for s in steps), "steps": steps}


def export(folder: Path, day0: datetime | None = None) -> list[Path]:
    """Файлы набора для документации: Excel, PDF и таблица ожидаемого итога (даты — для демо-базы с этим day0)."""
    from app.seed import write_movement_reference
    from app.timeutil import utcnow
    day0 = (day0 or utcnow()).replace(hour=0, minute=0, second=0, microsecond=0)
    folder.mkdir(parents=True, exist_ok=True)
    rows = write_movement_reference(folder, day0, XLSX, PDF)
    L = ["# Эталонный набор журнала движения узлов (характеристика Х6)", "",
         f"Файлы [{XLSX}]({XLSX}) и [{PDF}]({PDF}) — одни и те же 21 строка: 10 верных операций и 11 ошибочных, "
         "у каждой ошибочной своя причина отклонения.", "",
         f"Даты в этих копиях рассчитаны на демо-базу, созданную {day0:%d.%m.%Y}. На испытательном стенде используйте "
         "демо-журналы № 11 (Excel) и № 12 (PDF) на странице «Импорт журналов»: они создаются при каждом сбросе демо-данных "
         "с датами этой базы. Строки набора — `app/seed.py`, функция `movement_reference()`.", "",
         "Проверка: `python scripts/movements_reference.py --check` (входит в `scripts/run_acceptance.py`).", "",
         "| Строка | Дата | Операция | Узел | С борта | Борт | Позиция | Ожидаемый итог |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        exp = "применяется" if r["expect"] == "applied" else f"отклоняется: «{r['why']}» ({r['reason'].replace('Ошибка: ', '')})"
        L.append(f"| {r['line']} | {r['date']} | {r['op']} | {r['serial']} | {r['from'] or '—'} | {r['to'] or '—'} | "
                 f"{r['position'] or '—'} | {exp} |")
    L += ["", "Ожидаемый итог загрузки:", "",
          "| Шаг | Применено | Повтор | Отклонено |", "|---|---|---|---|",
          "| Excel на только что сброшенных демо-данных | 10 | 0 | 11 |",
          "| тот же Excel повторно | 0 | 10 | 11 |",
          "| PDF на только что сброшенных демо-данных | 10 | 0 | 11 |",
          "| тот же PDF повторно, Excel после PDF | 0 | 10 | 11 |", ""]
    md = folder / "movements-reference.md"
    md.write_text("\n".join(L), "utf-8")
    return [folder / XLSX, folder / PDF, md]


def render(res: dict) -> str:
    L = [f"Эталонный набор: {res['rows']} строк ({res['valid']} верных, {res['invalid']} ошибочных)"]
    for s in res["steps"]:
        L.append(f"  {s['step']}: применено {s['applied']}, повтор {s['duplicates']}, отклонено {s['rejected']}"
                 f" — ошибок обработки {len(s['errors'])}")
        L += [f"    строка {e['line']} ({e['serial']}): ожидалось {e['expected']}, получено {e['got']}" for e in s["errors"]]
    L.append(f"Х6 — ошибки обработки строк: {res['errors']} (порог 0) — {'соответствует' if res['errors'] == 0 else 'НЕ соответствует'}")
    return "\n".join(L)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="проверка на чистой демо-базе (Х6)")
    ap.add_argument("--json", action="store_true", help="итог проверки в JSON")
    ap.add_argument("--export", metavar="КАТАЛОГ", help="записать файлы набора и таблицу ожидаемого итога")
    ap.add_argument("--day0", help="дата демо-базы для --export (ГГГГ-ММ-ДД), по умолчанию сегодня")
    a = ap.parse_args()
    if a.export:
        day0 = datetime.strptime(a.day0, "%Y-%m-%d") if a.day0 else None
        for p in export(Path(a.export), day0):
            print(p)
    if a.check or not a.export:
        res = check()
        print(json.dumps(res, ensure_ascii=False) if a.json else render(res))
        return 0 if res["errors"] == 0 else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
