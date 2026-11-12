"""Автоматический прогон испытаний программной части и запись датированного протокола (модуль B).

    python scripts/run_acceptance.py            # протокол → docs/protocols/protocol-<дата>.md и .json
    python scripts/run_acceptance.py --quick    # 3 повтора замера вместо 10

Программа и методика испытаний: module_b/tbo-tracker-pmi.md (характеристики Х1–Х7, модули М1–М3, сквозной сценарий).
Здесь измеряются характеристики программной части — Х4, Х5 (синтетические записи), Х6, Х7 — и проверяются
требования кейса автотестами. Х1–Х3 и Х5 на реальных полётах измеряются на борту: отчёт по каждому полёту —
sudo tbo-ctl flight-report N --video-s С (раздел 5 программы и методики).
"""
from __future__ import annotations

import argparse
import json
import math
import platform
import random
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import benchmark_vs_logbook as bench  # noqa: E402  (настраивает временную базу)
from app.services import autopilot, sensors  # noqa: E402

PMI = "module_b/tbo-tracker-pmi.md"
TESTS = {
    1: ["test_autonomous_mission_flight_accrues_hours", "test_mission_crud_validation_and_export_roundtrip",
        "test_mission_fault_detects_fall_and_preflight_blocks", "test_drones"],
    2: ["test_component_passport_uid_specs_residual", "test_individual_resource_component"],
    3: ["test_sensor_detector_thresholds", "test_fall_from_log_triggers_inspection_and_counters",
        "test_mission_fault_detects_fall_and_preflight_blocks"],
    4: ["test_1_log_import_accrues_to_aircraft_and_all_installed_components",
        "test_late_import_uses_historical_configuration", "test_3_reimport_does_not_double_count"],
    5: ["test_excel_movements_and_pdf_duplicate", "test_pdf_movements_alone", "test_excel_flights_with_collision_column",
        "test_3d_invalid_rows_rejected", "test_movement_reference_set"],
    6: ["test_2_transfer_keeps_history_and_accrues_on_new_aircraft", "test_board_and_move_recalculates_passport"],
    7: ["test_notifications_three_categories", "test_4_reminders_hours_cycles_calendar",
         "test_5_perform_maintenance_resets_interval_not_total"],
    8: ["test_server", "test_auth"],
    9: ["test_scenarios"],
    10: ["test_board_and_move_recalculates_passport"],
}
TITLES = {
    1: "Автономный полёт по заданному маршруту",
    2: "Электронные паспорта заменяемых компонентов",
    3: "Мониторинг падений и столкновений по датчикам",
    4: "Учёт фактической наработки: часы налёта и циклы",
    5: "Загрузка журналов Excel/PDF: установка, демонтаж, перестановка",
    6: "Расчёт наработки и остаточного ресурса с учётом истории",
    7: "Регламентное ТО и автоматические уведомления",
    8: "Веб-интерфейс: карточки, история замен, состояние, предстоящие работы",
    9: "Демонстрация на тестовых данных",
    10: "Перемещение узлов между карточками с пересчётом паспорта",
}
BOARD = "на борту — отчёт tbo-ctl flight-report (раздел 5 ПМИ)"


def run_pytest(names: list[str], cwd: Path = ROOT, target: str | None = "tests") -> dict:
    t = time.perf_counter()
    args = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"] + ([target] if target else []) + ["-k", " or ".join(names)]
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    last = (p.stdout.strip().splitlines() or [""])[-1]
    passed = int(last.split(" passed")[0].split()[-1]) if " passed" in last else 0
    failed = int(last.split(" failed")[0].split()[-1]) if " failed" in last else 0
    return {"passed": passed, "failed": failed, "ok": p.returncode == 0 and passed > 0,
            "seconds": round(time.perf_counter() - t, 1), "summary": last}


# ---------------------------------------------------------------- п. 1: маршрут в симуляторе (предварительно)
def missions(n: int, seed: int) -> dict:
    rnd = random.Random(seed)
    xte, done, dist_err = [], 0, []
    for _ in range(n):
        lat0, lon0 = 54.63 + rnd.uniform(-0.05, 0.05), 39.74 + rnd.uniform(-0.05, 0.05)
        wps = []
        for _ in range(rnd.randint(3, 6)):
            x, y = rnd.uniform(-600, 600), rnd.uniform(-600, 600)
            lat, lon = autopilot.to_ll(lat0, lon0, x, y)
            wps.append({"lat": lat, "lon": lon, "alt": rnd.uniform(20, 60), "hold_s": rnd.choice([0, 0, 5])})
        m = {"home_lat": lat0, "home_lon": lon0, "takeoff_alt": 20, "speed_ms": rnd.uniform(5, 12),
             "return_home": True, "waypoints": wps}
        sim = autopilot.simulate(m, wind_ms=rnd.uniform(0, 8), wind_from_deg=rnd.uniform(0, 360))
        done += sim["status"] == "completed"
        xte.append(sim["max_xte_m"])
        land = sim["track"][-1]
        dist_err.append(math.dist(autopilot.to_xy(lat0, lon0, land[1], land[2]), (0, 0)))
    return {"missions": n, "completed": done, "max_xte_m": round(max(xte), 2), "mean_xte_m": round(sum(xte) / n, 2),
            "max_landing_offset_m": round(max(dist_err), 2)}


# ---------------------------------------------------------------- Х5: детектор на синтетических записях
def detector(n_each: int, seed: int) -> dict:
    rnd = random.Random(seed)
    hz = 25

    def base(sec):
        a = [1.0 + rnd.gauss(0, 0.08) for _ in range(sec * hz)]
        for _ in range(rnd.randint(0, 4)):                       # манёвры
            i = rnd.randrange(len(a) - 25)
            for k in range(rnd.randint(5, 25)):
                a[i + k] += rnd.uniform(0.3, 0.8)
        j = len(a) - 10                                          # жёсткая посадка
        a[j] = rnd.uniform(1.6, 2.8)
        return a

    cases = []
    for _ in range(n_each):
        cases.append(("none", base(60)))
        a = base(60)
        i = rnd.randrange(200, 1200)
        ff = round(rnd.uniform(0.3, 1.2) * hz)
        a[i:i + ff] = [rnd.uniform(0.0, 0.25) for _ in range(ff)]
        a[i + ff + rnd.randint(0, 5)] = rnd.uniform(3.5, 9.0)
        cases.append(("fall", a))
        a = base(60)
        i = rnd.randrange(200, 1200)
        for k in range(rnd.randint(1, 3)):
            a[i + k] = rnd.uniform(4.5, 10.0)
        cases.append(("collision", a))
    tp = fn = fp = wrong = 0
    for kind, a in cases:
        ev = sensors.detect(a, hz)
        if kind == "none":
            fp += bool(ev)
        elif not ev:
            fn += 1
        else:
            tp += 1
            wrong += ev[0]["kind"] != kind
    pos = 2 * n_each
    return {"records": len(cases), "recall": round(tp / pos, 4), "false_alarm_rate": round(fp / n_each, 4),
            "misclassified": wrong, "missed": fn, "false_alarms": fp}


# ---------------------------------------------------------------- Х6: эталонный набор журнала движения узлов
def movement_reference() -> dict:
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "movements_reference.py"), "--check", "--json"],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return {"rows": 0, "valid": 0, "invalid": 0, "errors": None, "steps": [], "failure": (p.stderr or p.stdout)[-2000:]}


def git_rev() -> str:
    try:
        rev = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
        dirty = subprocess.call(["git", "diff", "--quiet"], cwd=ROOT) != 0
        return rev + (" (+ незафиксированные изменения)" if dirty else "")
    except Exception:
        return "нет данных"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    runs = 3 if args.quick else 10
    started = datetime.now(timezone.utc).astimezone()

    print("Автотесты по требованиям кейса…", flush=True)
    items = {k: run_pytest(v) for k, v in TESTS.items()}
    print("Полёты по маршруту в симуляторе (предварительно)…", flush=True)
    mis = missions(50, args.seed)
    print("Детектор падений и столкновений (Х5)…", flush=True)
    det = detector(100, args.seed)
    print("Эталонный набор журнала движения узлов (Х6)…", flush=True)
    mref = movement_reference()
    print(f"Сравнительный замер (Х4, Х7), повторов: {runs}…", flush=True)
    br = bench.run(runs, args.seed)
    bs = bench.summary(br)
    print("Полный набор автотестов сервера и агента…", flush=True)
    full = run_pytest(["test_"])
    agent = run_pytest(["test_"], ROOT / "agent", "agent_tests") if (ROOT / "agent" / "agent_tests").is_dir() else None

    status_mm = sum(x["tracker_status_mismatch"] for x in br)
    notify_mm = sum(x["tracker_notify_mismatch"] for x in br)
    chars = [
        ("Х1", "1", "Доля достигнутых точек маршрута на реальном борту", "%", "100", None, None, BOARD),
        ("Х2", "1", "Наибольший промах в точке маршрута на реальном борту", "м", "≤ 1,0", None, None, BOARD),
        ("Х3", "4", "Расхождение начисленной длительности полёта с фактической (по видео); циклы", "ч; цикл",
         "≤ 0,01; +1", None, None, BOARD),
        ("Х4", "4, 6", "Отклонение наработки узла от эталона при заменах и перестановках", "ч", "≤ 0,01",
         bs["tracker_max_err_h"]["max"], bs["tracker_max_err_h"]["max"] <= 0.01,
         f"замер: {runs} повторов, ≈1 150 полётов, 56 узлов, 15–29 операций в повторе"),
        ("Х5", "3", "Обнаружение падений и столкновений / ложные срабатывания (синтетические записи)", "%",
         "≥ 95 / ≤ 2", f"{det['recall'] * 100:.1f} / {det['false_alarm_rate'] * 100:.1f}",
         det["recall"] >= 0.95 and det["false_alarm_rate"] <= 0.02,
         f"{det['records']} записей 25 Гц; на реальных полётах (порог 0 шт.) — {BOARD}"),
        ("Х6", "5", "Ошибки обработки строк журналов движения узлов Excel и PDF", "шт.", "0",
         mref.get("errors"), mref.get("errors") == 0,
         f"эталонный набор: {mref.get('rows')} строк ({mref.get('valid')} верных, {mref.get('invalid')} ошибочных), "
         f"{len(mref.get('steps') or [])} загрузок"),
        ("Х7", "7", "Расхождения статуса ТО и уведомлений с эталоном", "шт.", "0", f"{status_mm} / {notify_mm}",
         status_mm == 0 and notify_mm == 0,
         f"{runs} × {bs['ref_items']['max']} пар «узел × работа»; уведомлений по эталону — {bs['ref_notifications']['min']}–"
         f"{bs['ref_notifications']['max']} в повторе"),
    ]
    report = {
        "date": started.isoformat(timespec="seconds"), "commit": git_rev(), "pmi": PMI,
        "environment": {"python": platform.python_version(), "os": platform.platform(), "cpu": platform.processor() or platform.machine()},
        "items": {k: {"title": TITLES[k], **v} for k, v in items.items()},
        "missions": mis, "detector": det, "movement_reference": mref, "benchmark_runs": br, "benchmark_summary": bs,
        "full_suite": full, "agent_suite": agent,
        "characteristics": [dict(zip(["id", "item", "name", "unit", "threshold", "value", "ok", "how"], c)) for c in chars],
    }

    out = ROOT / "docs" / "protocols"
    out.mkdir(parents=True, exist_ok=True)
    stem = f"protocol-{started:%Y-%m-%d}"
    (out / f"{stem}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / f"{stem}.md").write_text(render(report, runs), encoding="utf-8")
    print(f"Протокол: docs/protocols/{stem}.md")
    ok = (all(v["ok"] for v in items.values()) and all(c[6] for c in chars if c[6] is not None)
          and full["ok"] and (agent is None or agent["ok"]))
    print("ИТОГ:", "ПРОЙДЕНО" if ok else "ЕСТЬ ЗАМЕЧАНИЯ")
    return 0 if ok else 1


def render(r: dict, runs: int) -> str:
    bs, mis, det, mref = r["benchmark_summary"], r["missions"], r["detector"], r["movement_reference"]

    def yes(b):
        return "—" if b is None else ("соответствует" if b else "**не соответствует**")

    def val(v):
        return "—" if v is None else str(v).replace(".", ",")

    L = [
        f"# Протокол испытаний программной части tbo-tracker № {r['date'][:10]}",
        "",
        f"- Дата и время прогона: {r['date']}",
        f"- Версия кода: `{r['commit']}`",
        f"- Среда: Python {r['environment']['python']}, {r['environment']['os']}",
        f"- Программа и методика испытаний: [{r['pmi']}](../../{r['pmi']})",
        "- Входные данные: случайно сгенерированы скриптами репозитория (модель парка, синтетические записи акселерометра, "
        "случайные миссии симулятора) и эталонный набор журнала движения узлов (`scripts/movements_reference.py`).",
        "- Воспроизведение: `python scripts/run_acceptance.py` (данные прогона — файл `.json` рядом)",
        "",
        "## 1. Характеристики",
        "",
        "| № | П. кейса | Характеристика | Ед. | Порог | Результат | Вывод | Как измерено |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for c in r["characteristics"]:
        L.append(f"| {c['id']} | {c['item']} | {c['name']} | {c['unit']} | {c['threshold']} | {val(c['value'])} | "
                 f"{yes(c['ok'])} | {c['how']} |")
    L += ["", "Х1–Х3 и Х5 на реальных полётах измеряются лётными испытаниями на борту по разделу 5 программы и методики: "
          "отчёт по каждому полёту — `sudo tbo-ctl flight-report N --video-s С` (точки, промах, начисление до/после, датчики).",
          "", "## 2. Требования кейса (автотесты)", "",
          "| П. | Требование | Тестов пройдено | Время, с | Вывод |", "|---|---|---|---|---|"]
    for k, v in r["items"].items():
        L.append(f"| {k} | {v['title']} | {v['passed']} из {v['passed'] + v['failed']} | {v['seconds']} | {yes(v['ok'])} |")
    f, a = r["full_suite"], r.get("agent_suite")
    L += ["", f"Полный набор автотестов сервера (`pytest tests`): {f['summary']}.", ""]
    if a:
        L += [f"Автотесты бортового агента (`pytest agent/agent_tests`, модель PX4/MAVROS и симулятор борта): {a['summary']}.", ""]
    L += ["## 3. Эталонный набор журнала движения узлов (Х6)", "",
          f"{mref.get('rows')} строк: {mref.get('valid')} верных операций, {mref.get('invalid')} ошибочных (у каждой своя причина). "
          "Ожидаемый итог по строкам — [docs/examples/movements-reference.md](../examples/movements-reference.md).", "",
          "| Шаг | Применено | Повтор | Отклонено | Ошибок обработки |", "|---|---|---|---|---|"]
    for s in mref.get("steps") or []:
        L.append(f"| {s['step']} | {s['applied']} | {s['duplicates']} | {s['rejected']} | {len(s['errors'])} |")
    for s in mref.get("steps") or []:
        for e in s["errors"]:
            L.append(f"\n- {s['step']}, строка {e['line']} ({e['serial']}): ожидалось {e['expected']}, получено {e['got']}")
    if mref.get("failure"):
        L += ["", f"Проверка не выполнена: `{mref['failure'][-300:]}`"]
    L += ["", "## 4. Сравнение с учётом по формуляру борта (Х4, Х7)", "",
          f"Модель: 6 бортов TBT-M4, 90 дней эксплуатации, случайные замены и перестановки узлов, {runs} повторов "
          "с разными исходными данными. Эталон — независимый расчёт наработки и сроков ТО по истории установок.", "",
          "| Показатель | мин. | сред. | макс. |", "|---|---|---|---|"]
    names = {"flights": "Полётов за период", "fleet_hours": "Налёт парка, ч", "operations": "Замен и перестановок узлов",
             "tracker_max_err_h": "tbo-tracker: макс. отклонение наработки узла от эталона, ч (Х4)",
             "ref_items": "Пар «узел × работа» под контролем", "ref_overdue": "Эталон: просроченных работ",
             "tracker_missed_overdue": "tbo-tracker: просроченных работ не показано",
             "tracker_status_mismatch": "tbo-tracker: расхождений статуса ТО с эталоном (Х7)",
             "ref_notifications": "Эталон: уведомлений (просрочено, предельный ресурс, приближается)",
             "tracker_notify_mismatch": "tbo-tracker: расхождений журнала уведомлений с эталоном (Х7)",
             "logbook_lost_hours": "Формуляр: наработка узлов, отсутствующая в учёте, ч",
             "logbook_parts_with_lost_hours": "Формуляр: узлов с неполной наработкой (из 42 установленных)",
             "logbook_max_lost_h": "Формуляр: наибольшая потеря по одному узлу, ч",
             "missed_overdue": "Формуляр: просроченных работ, не видимых в учёте",
             "missed_due_soon": "Формуляр: приближающихся работ, не видимых в учёте",
             "import_s": "Время импорта журнала за период, с", "operation_ms_max": "Время операции с узлом (макс.), мс"}
    for k, label in names.items():
        x = bs[k]
        L.append(f"| {label} | {x['min']} | {x['mean']} | {x['max']} |")
    L += ["", "Эталон сроков ТО и уведомлений считается в скрипте замера независимо от кода сервиса: наработка, циклы и календарь "
          "по истории установок, интервалы — из справочника регламентов; уведомления — из журнала сервера после штатной "
          "периодической проверки.", "",
          "## 5. Детектор падений и столкновений (Х5, синтетические записи)", "",
          f"Синтетических записей акселерометра 25 Гц по 60 с: {det['records']} (штатные полёты с манёврами и жёсткой "
          f"посадкой до 2,8 g; падения; столкновения). Обнаружено: {det['recall'] * 100:.1f} %; пропущено: {det['missed']}; "
          f"неверный тип события: {det['misclassified']}; ложных срабатываний: {det['false_alarms']}.", "",
          "Ограничение: записи синтетические. На реальных полётах модуля B ложные срабатывания считаются по отчётам полётов.", "",
          "## 6. Дополнительно: автономный полёт в симуляторе (п. 1, предварительно)", "",
          f"Миссий: {mis['missions']} (3–6 точек, радиус до 600 м, скорость 5–12 м/с, ветер 0–8 м/с). "
          f"Выполнено: {mis['completed']}. Отклонение от линии пути: среднее {mis['mean_xte_m']} м, "
          f"максимум {mis['max_xte_m']} м. Наибольший промах посадки от точки взлёта: {mis['max_landing_offset_m']} м. "
          "Не заменяет лётное испытание: Х1 и Х2 подтверждаются только на реальном борту.", "",
          "## Подписи", "", "Испытания провёл: ____________________ / [ФИО] /", "",
          "Протокол проверил: ____________________ / [ФИО] /", ""]
    return "\n".join(L)


if __name__ == "__main__":
    sys.exit(main())
