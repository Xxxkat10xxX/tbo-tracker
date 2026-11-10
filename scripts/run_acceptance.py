"""Автоматический прогон испытаний по 10 требованиям кейса и запись датированного протокола.

    python scripts/run_acceptance.py            # протокол → docs/protocols/protocol-<дата>.md и .json
    python scripts/run_acceptance.py --quick    # 3 повтора замера вместо 10

Методика: docs/TEST_METHODOLOGY.md. Лётное испытание п. 1 на реальном борту оформляется
отдельным актом (раздел 5 методики) и в этот прогон не входит.
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

TESTS = {
    1: ["test_autonomous_mission_flight_accrues_hours", "test_mission_crud_validation_and_export_roundtrip",
        "test_mission_fault_detects_fall_and_preflight_blocks", "test_drones"],
    2: ["test_component_passport_uid_specs_residual", "test_individual_resource_component"],
    3: ["test_sensor_detector_thresholds", "test_fall_from_log_triggers_inspection_and_counters",
        "test_mission_fault_detects_fall_and_preflight_blocks"],
    4: ["test_1_log_import_accrues_to_aircraft_and_all_installed_components",
        "test_late_import_uses_historical_configuration", "test_3_reimport_does_not_double_count"],
    5: ["test_excel_movements_and_pdf_duplicate", "test_pdf_movements_alone", "test_excel_flights_with_collision_column",
        "test_3d_invalid_rows_rejected"],
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


def run_pytest(names: list[str], cwd: Path = ROOT, target: str | None = None) -> dict:
    t = time.perf_counter()
    args = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"] + ([target] if target else []) + ["-k", " or ".join(names)]
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    last = (p.stdout.strip().splitlines() or [""])[-1]
    passed = int(last.split(" passed")[0].split()[-1]) if " passed" in last else 0
    failed = int(last.split(" failed")[0].split()[-1]) if " failed" in last else 0
    return {"passed": passed, "failed": failed, "ok": p.returncode == 0 and passed > 0,
            "seconds": round(time.perf_counter() - t, 1), "summary": last}


# ---------------------------------------------------------------- п. 1: маршрут в симуляторе
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


# ---------------------------------------------------------------- п. 3: детектор на синтетических записях
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


def git_rev() -> str:
    try:
        rev = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
        dirty = subprocess.call(["git", "diff", "--quiet"], cwd=ROOT) != 0
        return rev + (" (+ незафиксированные изменения)" if dirty else "")
    except Exception:
        return "нет данных"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    runs = 3 if args.quick else 10
    started = datetime.now(timezone.utc).astimezone()

    print("Автотесты по пунктам…", flush=True)
    items = {k: run_pytest(v) for k, v in TESTS.items()}
    print("Полёты по маршруту в симуляторе…", flush=True)
    mis = missions(50, args.seed)
    print("Детектор падений и столкновений…", flush=True)
    det = detector(100, args.seed)
    print(f"Сравнительный замер, повторов: {runs}…", flush=True)
    br = bench.run(runs, args.seed)
    bs = bench.summary(br)
    full = run_pytest(["test_"])
    agent = run_pytest(["test_"], ROOT / "agent", "agent_tests") if (ROOT / "agent" / "agent_tests").is_dir() else None

    # Характеристики, пороги, результат
    chars = [
        ("Х1", 4, "Точность начисления наработки узлу (отклонение от эталона)", "ч", "≤ 0,01",
         bs["tracker_max_err_h"]["max"], bs["tracker_max_err_h"]["max"] <= 0.01),
        ("Х2", 6, "Потеря наработки узлов при перестановках между бортами", "ч", "0 (сверх округления 0,01)",
         bs["tracker_max_err_h"]["max"], bs["tracker_max_err_h"]["max"] <= 0.01),
        ("Х3", 7, "Расхождения статуса ТО с эталоном (узел × работа, все повторы)", "шт.", "0",
         sum(x["tracker_status_mismatch"] for x in br), all(x["tracker_status_mismatch"] == 0 for x in br)),
        ("Х4", 3, "Доля обнаруженных падений и столкновений", "%", "≥ 95", round(det["recall"] * 100, 1),
         det["recall"] >= 0.95),
        ("Х5", 3, "Ложные срабатывания детектора на штатных полётах", "%", "≤ 2",
         round(det["false_alarm_rate"] * 100, 1), det["false_alarm_rate"] <= 0.02),
        ("Х6", 1, "Отклонение от линии пути в симуляторе при ветре до 8 м/с", "м", "≤ 10", mis["max_xte_m"],
         mis["max_xte_m"] <= 10 and mis["completed"] == mis["missions"]),
        ("Х7", 5, "Время импорта журнала за 90 дней парка (≈1 150 полётов)", "с", "≤ 10",
         bs["import_s"]["max"], bs["import_s"]["max"] <= 10),
    ]
    report = {
        "date": started.isoformat(timespec="seconds"), "commit": git_rev(),
        "environment": {"python": platform.python_version(), "os": platform.platform(), "cpu": platform.processor() or platform.machine()},
        "items": {k: {"title": TITLES[k], **v} for k, v in items.items()},
        "missions": mis, "detector": det, "benchmark_runs": br, "benchmark_summary": bs, "full_suite": full, "agent_suite": agent,
        "characteristics": [dict(zip(["id", "item", "name", "unit", "threshold", "value", "ok"], c)) for c in chars],
    }

    out = ROOT / "docs" / "protocols"
    out.mkdir(parents=True, exist_ok=True)
    stem = f"protocol-{started:%Y-%m-%d}"
    (out / f"{stem}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / f"{stem}.md").write_text(render(report, runs), encoding="utf-8")
    print(f"Протокол: docs/protocols/{stem}.md")
    ok = all(v["ok"] for v in items.values()) and all(c[6] for c in chars) and full["ok"] and (agent is None or agent["ok"])
    print("ИТОГ:", "ПРОЙДЕНО" if ok else "ЕСТЬ ЗАМЕЧАНИЯ")
    return 0 if ok else 1


def render(r: dict, runs: int) -> str:
    bs, mis, det = r["benchmark_summary"], r["missions"], r["detector"]
    yes = lambda b: "соответствует" if b else "**не соответствует**"  # noqa: E731
    L = [
        f"# Протокол испытаний tbo-tracker № {r['date'][:10]}",
        "",
        f"- Дата и время прогона: {r['date']}",
        f"- Версия кода: `{r['commit']}`",
        f"- Среда: Python {r['environment']['python']}, {r['environment']['os']}",
        "- Методика: [docs/TEST_METHODOLOGY.md](../TEST_METHODOLOGY.md)",
        "- Воспроизведение: `python scripts/run_acceptance.py` (данные прогона — файл `.json` рядом)",
        "",
        "## 1. Характеристики",
        "",
        "| № | П. кейса | Характеристика | Ед. | Порог | Результат | Вывод |",
        "|---|---|---|---|---|---|---|",
    ]
    for c in r["characteristics"]:
        L.append(f"| {c['id']} | {c['item']} | {c['name']} | {c['unit']} | {c['threshold']} | {c['value']} | {yes(c['ok'])} |")
    L += ["", "## 2. Требования кейса (автотесты)", "",
          "| П. | Требование | Тестов пройдено | Время, с | Вывод |", "|---|---|---|---|---|"]
    for k, v in r["items"].items():
        L.append(f"| {k} | {v['title']} | {v['passed']} из {v['passed'] + v['failed']} | {v['seconds']} | {yes(v['ok'])} |")
    f = r["full_suite"]
    a = r.get("agent_suite")
    L += ["", f"Полный набор автотестов сервера: {f['summary']}.", ""]
    if a:
        L += [f"Автотесты бортового агента (модель PX4/MAVROS и симулятор борта): {a['summary']}.", ""]
    L += [
          "П. 1 на реальном борту проверяется лётным испытанием по разделу 5 методики; акт и видео — отдельно.", "",
          "## 3. Сравнение с учётом по формуляру борта", "",
          f"Модель: 6 бортов TBT-M4, 90 дней эксплуатации, случайные замены и перестановки узлов, {runs} повторов "
          "с разными исходными данными. Эталон — независимый расчёт наработки по истории установок.", "",
          "| Показатель | мин. | сред. | макс. |", "|---|---|---|---|"]
    names = {"flights": "Полётов за период", "fleet_hours": "Налёт парка, ч", "operations": "Замен и перестановок узлов",
             "tracker_max_err_h": "tbo-tracker: макс. отклонение наработки узла от эталона, ч",
             "ref_items": "Пар «узел × работа» под контролем", "ref_overdue": "Эталон: просроченных работ",
             "tracker_missed_overdue": "tbo-tracker: просроченных работ не показано",
             "tracker_status_mismatch": "tbo-tracker: расхождений статуса с эталоном",
             "logbook_lost_hours": "Формуляр: наработка узлов, отсутствующая в учёте, ч",
             "logbook_parts_with_lost_hours": "Формуляр: узлов с неполной наработкой (из 42 установленных)",
             "logbook_max_lost_h": "Формуляр: наибольшая потеря по одному узлу, ч",
             "missed_overdue": "Формуляр: просроченных работ, не видимых в учёте",
             "missed_due_soon": "Формуляр: приближающихся работ, не видимых в учёте",
             "import_s": "Время импорта журнала за период, с", "operation_ms_max": "Время операции с узлом (макс.), мс"}
    for k, label in names.items():
        a = bs[k]
        L.append(f"| {label} | {a['min']} | {a['mean']} | {a['max']} |")
    L += ["", "Эталон сроков ТО считается в скрипте замера независимо от кода сервиса: наработка, циклы и календарь "
          "по истории установок, интервалы — из справочника регламентов.", "",
          "## 4. Автономный полёт в симуляторе (п. 1)", "",
          f"Миссий: {mis['missions']} (3–6 точек, радиус до 600 м, скорость 5–12 м/с, ветер 0–8 м/с). "
          f"Выполнено: {mis['completed']}. Отклонение от линии пути: среднее {mis['mean_xte_m']} м, "
          f"максимум {mis['max_xte_m']} м. Наибольший промах посадки от точки взлёта: {mis['max_landing_offset_m']} м.", "",
          "## 5. Детектор падений и столкновений (п. 3)", "",
          f"Синтетических записей акселерометра 25 Гц по 60 с: {det['records']} (штатные полёты с манёврами и жёсткой "
          f"посадкой до 2,8 g; падения; столкновения). Обнаружено: {det['recall'] * 100:.1f} %; пропущено: {det['missed']}; "
          f"неверный тип события: {det['misclassified']}; ложных срабатываний: {det['false_alarms']}.", "",
          "Ограничение: записи синтетические. На реальных журналах порог проверяется в лётных испытаниях.", "",
          "## Подписи", "", "Испытания провёл: ____________________ / [ФИО] /", "",
          "Протокол проверил: ____________________ / [ФИО] /", ""]
    return "\n".join(L)


if __name__ == "__main__":
    sys.exit(main())
