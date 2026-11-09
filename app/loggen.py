"""Генератор журналов эксплуатации (формат tbo-flightlog/1.0, JSON или CSV).

Используется из веб-интерфейса (раздел «Генератор журналов») и из командной строки:

    python -m app.loggen --aircraft TBT-M4-001 --from 2026-11-01 --to 2026-11-05 --format csv -o log.csv
    python -m app.loggen --aircraft TBT-M4-001 TBT-VT2-011 --days 3 --import

Генератор знает о парке (серийные номера, дата ввода в эксплуатацию) и по умолчанию
не создаёт полёты, пересекающиеся с уже учтёнными, и полёты в будущем. Для проверки защиты
импорта можно намеренно добавить дубликаты уже учтённых полётов и ошибочные строки.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .errors import DomainError
from .models import Aircraft, Flight
from .services.logs import FORMAT_ID
from .timeutil import iso, parse_dt, utcnow

MISSIONS = ["Аэрофотосъёмка", "Мониторинг ЛЭП", "Обследование трубопровода", "Картографирование",
            "Мониторинг посевов", "Учебно-тренировочный полёт", "Инспекция кровли", "Поисковая операция"]
PILOTS = ["Иванов А.", "Петрова М.", "Сидоров К.", "Белов Р.", "Орлова Н."]


@dataclass
class GenParams:
    aircraft_serials: list[str]
    date_from: datetime
    date_to: datetime                       # включительно (дата)
    fmt: str = "json"                       # json | csv
    fly_probability: float = 0.8            # вероятность лётного дня
    flights_min: int = 1
    flights_max: int = 4
    duration_min: int = 15                  # минут
    duration_max: int = 45
    first_takeoff_hour: int = 6             # UTC
    touch_and_go_probability: float = 0.05  # вероятность 2 циклов за полёт
    with_ids: bool = True
    avoid_overlap: bool = True
    duplicates: int = 0                     # добавить N копий уже учтённых полётов (с теми же ID)
    errors: bool = False                    # добавить ошибочные строки
    seed: int | None = None
    notes: list[str] = field(default_factory=list)


def _day_start(d: datetime) -> datetime:
    return d.replace(hour=0, minute=0, second=0, microsecond=0)


def generate(s: Session, p: GenParams) -> dict:
    if not p.aircraft_serials:
        raise DomainError("Выберите хотя бы один борт")
    if p.fmt not in ("json", "csv"):
        raise DomainError("Формат: json или csv")
    if p.fmt == "json" and len(p.aircraft_serials) > 1:
        p.notes.append("Несколько бортов в JSON: серийный номер указан в каждом полёте")
    if p.flights_min < 0 or p.flights_max < p.flights_min or p.flights_max > 20:
        raise DomainError("Число полётов в день: от 0 до 20, минимум не больше максимума")
    if p.duration_min < 1 or p.duration_max < p.duration_min or p.duration_max > 600:
        raise DomainError("Длительность полёта: от 1 до 600 минут, минимум не больше максимума")
    d_from, d_to = _day_start(p.date_from), _day_start(p.date_to)
    if d_to < d_from:
        raise DomainError("Дата окончания раньше даты начала")
    if (d_to - d_from).days > 366:
        raise DomainError("Период не больше года")
    rng = random.Random(p.seed)
    now = utcnow()
    aircraft = {a.serial: a for a in s.scalars(select(Aircraft).where(Aircraft.serial.in_(p.aircraft_serials)))}
    unknown = [x for x in p.aircraft_serials if x not in aircraft]
    if unknown:
        raise DomainError(f"Борта не найдены: {', '.join(unknown)}")

    rows: list[dict] = []
    skipped_overlap = 0
    for serial in p.aircraft_serials:
        ac = aircraft[serial]
        existing = []
        if p.avoid_overlap:
            existing = [(f.started_at, f.ended_at) for f in s.scalars(select(Flight).where(
                Flight.aircraft_id == ac.id, Flight.ended_at >= d_from, Flight.started_at < d_to + timedelta(days=1)))]
        day = d_from
        while day <= d_to:
            if rng.random() < p.fly_probability:
                t = day + timedelta(hours=p.first_takeoff_hour, minutes=rng.randint(0, 59))
                for k in range(rng.randint(p.flights_min, p.flights_max)):
                    end = t + timedelta(minutes=rng.randint(p.duration_min, p.duration_max))
                    if end > now or t < ac.in_service_at:
                        break
                    if any(st < end and en > t for st, en in existing):
                        skipped_overlap += 1
                    else:
                        r = {"aircraft_serial": serial, "start": iso(t), "end": iso(end),
                             "cycles": 2 if rng.random() < p.touch_and_go_probability else 1,
                             "mission": rng.choice(MISSIONS), "pilot": rng.choice(PILOTS)}
                        if p.with_ids:
                            r = {"flight_id": f"{serial}-{t:%Y%m%d-%H%M}-G{k + 1}", **r}
                        rows.append(r)
                        if p.avoid_overlap:     # и со своими полётами: длинные полёты дня заходят в следующий день
                            existing.append((t, end))
                    t = end + timedelta(minutes=rng.randint(20, 90))
            day += timedelta(days=1)
    if skipped_overlap:
        p.notes.append(f"Пропущено полётов из-за пересечения с уже учтёнными: {skipped_overlap}")

    gen_count = len(rows)
    gen_hours = sum((parse_dt(r["end"]) - parse_dt(r["start"])).total_seconds() / 3600 for r in rows)
    dup_count = 0
    if p.duplicates:
        ids = [aircraft[x].id for x in p.aircraft_serials]
        known = list(s.scalars(select(Flight).where(Flight.aircraft_id.in_(ids))
                               .order_by(Flight.started_at.desc()).limit(p.duplicates)))
        for f in known:
            rows.append({"flight_id": f.external_id, "aircraft_serial": f.aircraft.serial, "start": iso(f.started_at),
                         "end": iso(f.ended_at), "cycles": f.cycles, "mission": f.mission or "", "pilot": f.pilot or ""})
            dup_count += 1
        if dup_count < p.duplicates:
            p.notes.append(f"Уже учтённых полётов для дублей нашлось только {dup_count}")

    err_count = 0
    if p.errors:
        base = d_from + timedelta(hours=12)
        serial = p.aircraft_serials[0]
        future = _day_start(now) + timedelta(days=3, hours=12)   # не от секунд «сейчас»: тот же seed — тот же журнал
        rows += [
            {"flight_id": "ERR-UNKNOWN", "aircraft_serial": "UNKNOWN-000", "start": iso(base), "end": iso(base + timedelta(minutes=30)), "cycles": 1},
            {"flight_id": "ERR-REVERSED", "aircraft_serial": serial, "start": iso(base + timedelta(hours=2)), "end": iso(base + timedelta(hours=1)), "cycles": 1},
            {"flight_id": "ERR-FUTURE", "aircraft_serial": serial, "start": iso(future), "end": iso(future + timedelta(hours=1)), "cycles": 1},
            {"flight_id": "ERR-DATE", "aircraft_serial": serial, "start": "не дата", "end": "2026-13-45T25:00", "cycles": 1},
        ]
        err_count = 4

    rows.sort(key=lambda r: (r["aircraft_serial"], r["start"]))
    stamp = f"{d_from:%Y%m%d}-{d_to:%Y%m%d}"
    name_ac = p.aircraft_serials[0] if len(p.aircraft_serials) == 1 else f"{len(p.aircraft_serials)}-bortov"
    filename = f"gen_{name_ac}_{stamp}.{p.fmt}"
    content = render(rows, p.fmt, p.aircraft_serials)
    return {
        "filename": filename, "format": p.fmt, "content": content,
        "stats": {"flights": len(rows), "new_flights": gen_count, "duplicates": dup_count, "errors": err_count,
                  "hours_new": round(gen_hours, 2), "skipped_overlap": skipped_overlap},
        "notes": p.notes,
        "preview": rows[:50],
    }


def render(rows: list[dict], fmt: str, serials: list[str]) -> str:
    if fmt == "json":
        single = len(serials) == 1
        flights = [{k: v for k, v in r.items() if not (single and k == "aircraft_serial")} for r in rows]
        doc = {"format": FORMAT_ID, "log_id": f"gen-{utcnow():%Y%m%d%H%M%S}", "source": "Генератор журналов tbo-tracker",
               "generated_at": iso(utcnow()), "flights": flights}
        if single:
            doc = {"format": FORMAT_ID, "aircraft_serial": serials[0], **{k: v for k, v in doc.items() if k != "format"}}
        return json.dumps(doc, ensure_ascii=False, indent=2)
    buf = io.StringIO()
    buf.write(f"# {FORMAT_ID} CSV — сгенерировано {iso(utcnow())}\n")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["aircraft_serial", "flight_id", "start_utc", "end_utc", "cycles", "mission", "pilot"])
    for r in rows:
        w.writerow([r.get("aircraft_serial", ""), r.get("flight_id", ""), r["start"], r["end"], r.get("cycles", 1),
                    r.get("mission", ""), r.get("pilot", "")])
    return buf.getvalue()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Генератор журналов эксплуатации tbo-tracker")
    ap.add_argument("--aircraft", nargs="+", required=True, help="серийные номера бортов")
    ap.add_argument("--from", dest="date_from", help="дата начала YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", help="дата окончания YYYY-MM-DD (включительно)")
    ap.add_argument("--days", type=int, default=3, help="если даты не заданы — последние N дней")
    ap.add_argument("--format", choices=["json", "csv"], default="json")
    ap.add_argument("--flights", default="1-4", help="полётов в лётный день, например 1-4")
    ap.add_argument("--duration", default="15-45", help="длительность полёта в минутах, например 15-45")
    ap.add_argument("--probability", type=float, default=0.8, help="вероятность лётного дня")
    ap.add_argument("--no-ids", action="store_true", help="не указывать flight_id")
    ap.add_argument("--duplicates", type=int, default=0, help="добавить N уже учтённых полётов")
    ap.add_argument("--errors", action="store_true", help="добавить ошибочные строки")
    ap.add_argument("--seed", type=int)
    ap.add_argument("-o", "--output", help="файл результата (по умолчанию — в stdout)")
    ap.add_argument("--import", dest="do_import", action="store_true", help="сразу импортировать в базу")
    a = ap.parse_args(argv)
    from .db import Base, SessionLocal, engine
    from .services.logs import import_log

    Base.metadata.create_all(engine)
    today = _day_start(utcnow())
    d_to = parse_dt(a.date_to) if a.date_to else today
    d_from = parse_dt(a.date_from) if a.date_from else d_to - timedelta(days=a.days - 1)
    fmin, fmax = (int(x) for x in a.flights.split("-"))
    dmin, dmax = (int(x) for x in a.duration.split("-"))
    p = GenParams(a.aircraft, d_from, d_to, a.format, a.probability, fmin, fmax, dmin, dmax,
                  with_ids=not a.no_ids, duplicates=a.duplicates, errors=a.errors, seed=a.seed)
    with SessionLocal() as s:
        res = generate(s, p)
        if a.output:
            with open(a.output, "w", encoding="utf-8") as f:
                f.write(res["content"])
            print(f"Записан {a.output}: {res['stats']}")
        elif not a.do_import:
            print(res["content"])
        for n in res["notes"]:
            print("  ·", n)
        if a.do_import:
            r = import_log(s, res["content"].encode(), a.output or res["filename"], "генератор (CLI)")
            s.commit()
            print(f"Импорт №{r['id']}: {r['status_label']}, новых полётов {r['flights_new']}, "
                  f"+{r['hours_added']} ч, +{r['cycles_added']} цикл.")
            for m in r["messages"]:
                print("  ·", m)


if __name__ == "__main__":
    main()
