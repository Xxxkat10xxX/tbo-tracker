"""Сравнительный замер: tbo-tracker против учёта «по формуляру борта».

Что делает:
  1. На чистой копии базы заводит отдельный парк: 6 квадрокоптеров TBT-M4 по 7 узлов и резерв
     из бывших в употреблении узлов (с наработкой до регистрации).
  2. Моделирует 90 дней эксплуатации: полёты каждый день, случайные замены и перестановки узлов
     между бортами (через API, как это делает инженер).
  3. Загружает все полёты одним журналом через штатный импорт.
  4. Независимо, без кода сервиса, считает эталонную наработку каждого узла по истории установок.
  5. Сравнивает:
     - наработку узлов в tbo-tracker с эталоном (точность);
     - учёт «по формуляру борта» (узлу пишутся только часы на текущем борту) с эталоном
       (потерянные часы и пропущенные сроки ТО).

Запуск:  python scripts/benchmark_vs_logbook.py --runs 10 --seed 1
"""
from __future__ import annotations

import argparse
import io
import os
import random
import statistics
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if "TBO_DATA_DIR" not in os.environ:
    _tmp = tempfile.mkdtemp(prefix="tbo-bench-")
    os.environ["TBO_DATA_DIR"] = _tmp
    os.environ["TBO_DATABASE_URL"] = f"sqlite:///{_tmp}/bench.db"
os.environ["TBO_DEMO_MODE"] = "1"
os.environ["TBO_SCHEDULER"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from tests.conftest import login, reset_all  # noqa: E402

DAYS = 90
AIRCRAFT = 6
SLOTS = [("FC", "flight_controller"), ("IMU", "imu"), ("ESC1", "esc"), ("ESC2", "esc"),
         ("ESC3", "esc"), ("ESC4", "esc"), ("CPU", "computer")]
SPARES = {"flight_controller": 3, "imu": 3, "esc": 6, "computer": 2}
SWAP_P = 0.25            # вероятность операции с узлами в день


def _iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def one_run(c: TestClient, seed: int) -> dict:
    rnd = random.Random(seed)
    meta = c.get("/api/meta").json()
    ac_type = next(t["id"] for t in meta["aircraft_types"] if t["code"] == "TBT-M4")
    ct = {t["code"]: t["id"] for t in meta["component_types"]}
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    t0 = (now - timedelta(days=DAYS + 1)).replace(hour=0)
    tag = f"B{seed}"

    # --- парк и резерв
    aircraft = []
    for i in range(AIRCRAFT):
        r = c.post("/api/aircraft", json={"serial": f"{tag}-AC{i+1:02d}", "name": f"{tag} борт {i+1}",
                                          "type_id": ac_type, "in_service_at": _iso(t0)})
        aircraft.append({"id": r.json()["id"], "serial": f"{tag}-AC{i+1:02d}", "slots": {}})
    comps = {}          # id -> {serial, type, initial}
    n = 0

    def new_comp(ctype, initial):
        nonlocal n
        n += 1
        serial = f"{tag}-{ctype[:3].upper()}-{n:03d}"
        r = c.post("/api/components", json={"serial": serial, "type_id": ct[ctype], "initial_hours": initial,
                                            "in_service_at": _iso(t0)})
        assert r.status_code == 201, r.text
        comps[r.json()["id"]] = {"serial": serial, "type": ctype, "initial": initial}
        return r.json()["id"]

    stints = []          # [component_id, aircraft_index, from, to|None]
    for ai, a in enumerate(aircraft):
        for pos, ctype in SLOTS:
            cid = new_comp(ctype, 0.0)
            r = c.post(f"/api/components/{cid}/install", json={"aircraft_id": a["id"], "position": pos,
                                                               "at": _iso(t0 + timedelta(hours=1))})
            assert r.status_code == 200, r.text
            a["slots"][pos] = cid
            stints.append([cid, ai, t0 + timedelta(hours=1), None])
    stock = {ctype: [new_comp(ctype, round(rnd.uniform(20, 180), 1)) for _ in range(k)] for ctype, k in SPARES.items()}

    # --- 90 дней: полёты и операции с узлами
    flights = []         # (aircraft_index, start, end)
    ops = 0
    api_ms = []
    for d in range(1, DAYS + 1):
        day = t0 + timedelta(days=d)
        if rnd.random() < SWAP_P:
            at = day + timedelta(hours=3)
            pos, ctype = rnd.choice(SLOTS)
            a_i, b_i = rnd.sample(range(AIRCRAFT), 2)
            a, b = aircraft[a_i], aircraft[b_i]
            if rnd.random() < 0.5 and stock[ctype]:
                # замена: узел A уходит в резерв, на его место — узел из резерва (со своей наработкой)
                old, spare = a["slots"][pos], stock[ctype].pop(0)
                t = time.perf_counter()
                assert c.post(f"/api/components/{old}/remove", json={"at": _iso(at)}).status_code == 200
                r = c.post(f"/api/components/{spare}/install", json={"aircraft_id": a["id"], "position": pos,
                                                                     "at": _iso(at + timedelta(minutes=5))})
                api_ms.append((time.perf_counter() - t) * 1000)
                assert r.status_code == 200, r.text
                for st in stints:
                    if st[0] == old and st[3] is None:
                        st[3] = at
                stints.append([spare, a_i, at + timedelta(minutes=5), None])
                a["slots"][pos] = spare
                stock[ctype].append(old)
            else:
                # перестановка: узел B снимается в резерв, узел A переходит на B, A получает узел из резерва
                if not stock[ctype]:
                    continue
                b_old, moving, spare = b["slots"][pos], a["slots"][pos], stock[ctype].pop(0)
                t = time.perf_counter()
                assert c.post(f"/api/components/{b_old}/remove", json={"at": _iso(at)}).status_code == 200
                r = c.post(f"/api/components/{moving}/transfer", json={"aircraft_id": b["id"], "position": pos,
                                                                       "at": _iso(at + timedelta(minutes=5))})
                assert r.status_code == 200, r.text
                r = c.post(f"/api/components/{spare}/install", json={"aircraft_id": a["id"], "position": pos,
                                                                     "at": _iso(at + timedelta(minutes=10))})
                assert r.status_code == 200, r.text
                api_ms.append((time.perf_counter() - t) * 1000)
                for st in stints:
                    if st[0] in (b_old, moving) and st[3] is None:
                        st[3] = at if st[0] == b_old else at + timedelta(minutes=5)
                stints.append([moving, b_i, at + timedelta(minutes=5), None])
                stints.append([spare, a_i, at + timedelta(minutes=10), None])
                b["slots"][pos], a["slots"][pos] = moving, spare
                stock[ctype].append(b_old)
            ops += 1
        for ai in range(AIRCRAFT):
            t = day + timedelta(hours=6, minutes=rnd.randint(0, 59))
            for _ in range(rnd.choice([0, 1, 2, 2, 3, 3, 4])):
                dur = timedelta(minutes=rnd.randint(12, 55))
                flights.append((ai, t, t + dur))
                t += dur + timedelta(minutes=rnd.randint(20, 90))

    # --- один журнал за весь период, штатный импорт
    buf = io.StringIO()
    buf.write("# tbo-flightlog/1.0 CSV\naircraft_serial,flight_id,start_utc,end_utc,cycles,mission,pilot\n")
    for k, (ai, s, e) in enumerate(flights):
        sr = aircraft[ai]["serial"]
        buf.write(f"{sr},{sr}-{k:05d},{_iso(s)},{_iso(e)},1,Замер,Тест\n")
    t = time.perf_counter()
    r = c.post("/api/imports", files={"file": (f"{tag}.csv", buf.getvalue().encode(), "text/csv")})
    import_s = time.perf_counter() - t
    assert r.status_code == 200, r.text
    imp = r.json()
    assert imp["flights_new"] == len(flights), imp

    # --- эталон: независимый расчёт по истории установок
    def h(s, e):
        return (e - s).total_seconds() / 3600

    ref_total = {cid: v["initial"] for cid, v in comps.items()}
    ref_onboard = {}
    for cid, ai, frm, to in stints:
        hrs = sum(h(s, e) for a_i, s, e in flights if a_i == ai and s >= frm and (to is None or e <= to))
        ref_total[cid] += hrs
        if to is None:
            ref_onboard[cid] = hrs

    ref_cycles = {cid: 0 for cid in comps}
    for cid, ai, frm, to in stints:
        ref_cycles[cid] += sum(1 for a_i, s, e in flights if a_i == ai and s >= frm and (to is None or e <= to))

    # --- эталонные сроки ТО (работы не выполнялись: интервал считается по всей наработке)
    type_ids = {v: k for k, v in ct.items()}
    rules = [r for r in c.get("/api/rules").json() if r["target"] == "component" and r.get("component_type_id")]
    days_used = (datetime.now(timezone.utc) - t0).total_seconds() / 86400
    ref_status = {}
    for cid, v in comps.items():
        for r in rules:
            if type_ids.get(r["component_type_id"]) != v["type"]:
                continue
            st = "ok"
            for interval, warn, used in ((r.get("interval_hours"), r.get("warn_hours"), ref_total[cid]),
                                         (r.get("interval_cycles"), r.get("warn_cycles"), ref_cycles[cid]),
                                         (r.get("interval_days") if r["kind"] == "periodic" else None, r.get("warn_days"), days_used)):
                if not interval:
                    continue
                warn = interval * 0.1 if warn is None else warn
                rem = interval - used
                cur = "overdue" if rem <= 0 else ("due_soon" if rem <= warn else "ok")
                st = min(st, cur, key=["overdue", "due_soon", "ok"].index)
            if any((r.get(k)) for k in ("interval_hours", "interval_cycles", "interval_days")):
                ref_status[(v["serial"], r["code"])] = st
    due = {(i["entity"]["serial"], i["rule"]["code"]): i["status"]
           for i in c.get("/api/maintenance/due", params={"status": ""}).json() if i["entity"]["serial"].startswith(tag + "-")}
    ref_over = [k for k, st in ref_status.items() if st == "overdue"]
    tracker_missed = sum(1 for k in ref_over if due.get(k) != "overdue")
    status_mismatch = sum(1 for k, st in ref_status.items() if due.get(k) != st)

    # --- уведомления: журнал сервера (services/notify.scan) против эталона по каждой паре «узел × работа»
    # просрочено → «просрочено» (по ресурсу — «предельный ресурс»), приближается → «приближается», в норме — уведомления нет
    from sqlalchemy import select as _select

    from app.db import SessionLocal
    from app.models import NotificationEvent
    from app.services import notify
    rule_by_id = {r["id"]: r for r in rules}
    with SessionLocal() as s:
        notify.scan(s)
        opened = [e.key for e in s.scalars(_select(NotificationEvent).where(NotificationEvent.resolved_at.is_(None)))]
    got_notes: dict[tuple, set] = {}
    for key in opened:
        cat, _, ref = key.partition("|")
        kind, _, rest = ref.partition(":")
        eid, _, rid = rest.partition(":")
        if kind != "component" or not eid.isdigit() or not rid.isdigit() or int(eid) not in comps:
            continue
        rule = rule_by_id.get(int(rid))
        if rule is not None:
            got_notes.setdefault((comps[int(eid)]["serial"], rule["code"]), set()).add(cat)
    code_kind = {r["code"]: r["kind"] for r in rules}
    want_cat = {"overdue": lambda code: {"limit" if code_kind.get(code) == "life" else "overdue"},
                "due_soon": lambda code: {"approaching"}, "ok": lambda code: set()}
    notify_mismatch = sum(1 for (serial, code), st in ref_status.items()
                          if got_notes.get((serial, code), set()) & {"limit", "overdue", "approaching"} != want_cat[st](code))
    ref_notes = sum(1 for st in ref_status.values() if st != "ok")

    # --- tbo-tracker
    got = {x["serial"]: x for x in c.get("/api/components", params={"q": tag}).json()}
    err = [abs(got[v["serial"]]["hours"] - ref_total[cid]) for cid, v in comps.items()]
    installed = list(ref_onboard)
    lost = [ref_total[cid] - ref_onboard[cid] for cid in installed]
    an = c.get("/api/analytics").json()
    prevented = [p for p in an["prevented"] if p["entity"]["serial"].startswith(tag + "-")]
    return {
        "seed": seed, "flights": len(flights), "fleet_hours": round(sum(h(s, e) for _, s, e in flights), 1),
        "operations": ops, "components": len(comps),
        "tracker_max_err_h": round(max(err), 4),
        "ref_overdue": len(ref_over), "ref_items": len(ref_status),
        "tracker_missed_overdue": tracker_missed, "tracker_status_mismatch": status_mismatch,
        "ref_notifications": ref_notes, "tracker_notify_mismatch": notify_mismatch,
        "logbook_lost_hours": round(sum(lost), 1),
        "logbook_parts_with_lost_hours": sum(1 for x in lost if x > 0.05),
        "logbook_parts_installed": len(installed),
        "logbook_max_lost_h": round(max(lost), 1),
        "missed_overdue": sum(1 for p in prevented if p["real_status"] == "overdue"),
        "missed_due_soon": sum(1 for p in prevented if p["real_status"] == "due_soon"),
        "import_s": round(import_s, 2),
        "operation_ms_max": round(max(api_ms), 1) if api_ms else 0,
    }


def run(runs: int, seed: int) -> list[dict]:
    res = []
    for k in range(runs):
        reset_all()
        with TestClient(app) as c:
            login(c)
            res.append(one_run(c, seed + k))
    return res


def summary(res: list[dict]) -> dict:
    def agg(key):
        v = [r[key] for r in res]
        return {"min": min(v), "mean": round(statistics.mean(v), 4), "max": max(v)}
    keys = ["flights", "fleet_hours", "operations", "tracker_max_err_h", "ref_items", "ref_overdue",
            "tracker_missed_overdue", "tracker_status_mismatch", "ref_notifications", "tracker_notify_mismatch",
            "logbook_lost_hours",
            "logbook_parts_with_lost_hours", "logbook_max_lost_h", "missed_overdue", "missed_due_soon",
            "import_s", "operation_ms_max"]
    return {k: agg(k) for k in keys}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    res = run(args.runs, args.seed)
    for r in res:
        print(r)
    print(summary(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
