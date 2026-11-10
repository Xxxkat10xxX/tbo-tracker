"""Демо-сценарии проверки: выполняются на сервере, результат — набор блоков для отображения.

Каждый сценарий вызывает те же функции, что и интерфейс/REST API (импорт демо-журнала, перестановка,
регистрация работы, автономный полёт), и возвращает сравнение «до / после». Клиент только рисует блоки:

* ``{"t": "notice", "level": "ok|warn|bad|info", "text": ..., "href": ..., "link": ...}``
* ``{"t": "import", "data": <результат импорта>, "part": "head|full|sensors"}``
* ``{"t": "diff", "rows": [{"label", "sub", "href", "before": {hours, cycles}, "after": {...}}]}``
* ``{"t": "table", "head": [...], "rows": [[cell, ...]]}`` — ячейка: строка или словарь
  ``{text, href, sub, mono, bold, due, readiness, metric, entity, imp_status, dt, uid}``
* ``{"t": "h3", "text": ...}``, ``{"t": "p", "text": ..., "small": bool, "href": ..., "link": ...}``
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import DomainError
from ..models import Aircraft, Component
from ..timeutil import utcnow

TITLES = {
    1: "Расчёт наработки по журналу эксплуатации",
    2: "Замена и перестановка узла между бортами",
    3: "Повторный импорт одного журнала",
    4: "Срабатывание напоминаний по часам, циклам и календарю",
    5: "Проведение обслуживания по напоминанию",
    6: "Автономный полёт по заданному маршруту (К1, К4)",
    7: "Падение по данным датчиков и запрет вылета (К3, К7)",
    8: "Журнал установок/снятий/перестановок из Excel и PDF (К5, К6)",
    9: "Интерактивная перестановка узла между карточками бортов (К10)",
    10: "Автоматические уведомления (К7)",
}
PHASE_RU = {"takeoff": "Взлёт", "mission": "Полёт по маршруту", "hold": "Удержание на точке", "rtl": "Возврат домой",
            "land": "Посадка", "crash": "Аварийное завершение"}


# ------------------------------------------------------------------ форматирование
def _n(v, d=1) -> str:
    return f"{v:,.{d}f}".replace(",", " ").replace(".", ",")


def _h(v) -> str:
    return f"{_n(v, 2)} ч"


def _dur(sec) -> str:
    sec = round(sec or 0)
    return f"{sec // 60} мин {sec % 60:02d} с"


def notice(level, text, href=None, link=None):
    return {"t": "notice", "level": level, "text": text, "href": href, "link": link}


def p(text, small=False, href=None, link=None):
    return {"t": "p", "text": text, "small": small, "href": href, "link": link}


def h3(text):
    return {"t": "h3", "text": text}


def table(head, rows):
    return {"t": "table", "head": head, "rows": rows}


# ------------------------------------------------------------------ данные
def _ac(s: Session, serial: str) -> Aircraft:
    a = s.scalar(select(Aircraft).where(Aircraft.serial == serial))
    if a is None:
        raise DomainError(f"Нет борта {serial} — сбросьте демо-данные")
    return a


def _comp(s: Session, serial: str) -> Component:
    c = s.scalar(select(Component).where(Component.serial == serial))
    if c is None:
        raise DomainError(f"Нет узла {serial} — сбросьте демо-данные")
    return c


def _brief(s: Session, aid: int) -> dict:
    from ..api.routes import list_aircraft
    return next(a for a in list_aircraft(s) if a["id"] == aid)


def _snapshot(s: Session, aid: int) -> list[dict]:
    from ..api.routes import get_aircraft
    a = get_aircraft(aid, s)
    rows = [{"key": "ac", "label": a["name"], "sub": "борт", "href": f"#/aircraft/{aid}",
             "v": {"hours": a["hours"], "cycles": a["cycles"]}}]
    for sl in a["configuration"]:
        c = sl["component"]
        if c:
            rows.append({"key": c["id"], "label": c["serial"], "sub": sl["label"], "href": f"#/components/{c['id']}",
                         "v": {"hours": c["hours"], "cycles": c["cycles"]}})
    return rows


def _diff(before: list[dict], after: list[dict]) -> dict:
    amap = {r["key"]: r for r in after}
    return {"t": "diff", "rows": [{"label": r["label"], "sub": r["sub"], "href": r["href"], "before": r["v"],
                                  "after": amap.get(r["key"], r)["v"]} for r in before]}


def _import(s: Session, name: str) -> dict:
    from ..api.routes import demo_log_import
    return demo_log_import(name, False, s)


def _due(item):
    return {"due": item["status"]} if item else "—"


def _metrics(item) -> str:
    if not item:
        return ""
    names = {"hours": "Часы", "cycles": "Циклы", "days": "Календарь", "events": "События датчиков"}
    return "; ".join(f"{names.get(m['metric'], m['metric'])}: {_n(m['used'], 1)} из {_n(m['limit'], 0)} {m['unit']}" for m in item["metrics"])


# ------------------------------------------------------------------ сценарии
def s1(s):
    ac = _ac(s, "TBT-M4-001")
    before = _snapshot(s, ac.id)
    r = _import(s, "01_TBT-M4-001_smena.json")
    out = [{"t": "import", "data": r, "part": "head"}, _diff(before, _snapshot(s, ac.id))]
    alerts = (r.get("summary") or {}).get("alerts") or []
    if alerts:
        out.append(h3("Сработавшие напоминания"))
        out.append(table(["Объект", "Регламент", "Было", "Стало"],
                         [[{"entity": a["entity"]}, a["rule"]["name"], _due({"status": a["previous_status"]}) if a.get("previous_status") else "—",
                           _due(a)] for a in alerts]))
    if r["status"] == "duplicate":
        out.append(notice("warn", "Журнал уже был загружен ранее — для чистого прогона сбросьте демо-данные."))
    return out


def s2(s):
    from ..api.routes import get_component, patch_aircraft, transfer_component
    from ..schemas import AircraftPatch, InstallIn
    fc, a3 = _comp(s, "FC7-0102"), _ac(s, "TBT-M4-003")
    card = get_component(fc.id, s)
    before = {"hours": card["hours"], "cycles": card["cycles"]}
    due_before = next((i for i in card["due"] if i["rule"]["code"] == "FC-CHK"), None)
    out = []
    if card["location"] and card["location"]["aircraft_id"] == a3.id:
        out.append(notice("warn", "FC7-0102 уже стоит на «Борт 03» — перестановка пропущена."))
    else:
        at = (utcnow() - timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0)
        r = transfer_component(fc.id, InstallIn(aircraft_id=a3.id, position="FC", at=at,
                                                reason="Демо-сценарий: перестановка ПК с «Борт 02» на «Борт 03»", performed_by="Демо"), s)
        out.append(notice("ok", r["message"]))
    patch_aircraft(a3.id, AircraftPatch(status="active"), s)
    mid = get_component(fc.id, s)
    imp = _import(s, "02_TBT-M4-003_posle_perestanovki.csv")
    after = get_component(fc.id, s)
    due_after = next((i for i in after["due"] if i["rule"]["code"] == "FC-CHK"), None)
    out += [
        {"t": "import", "data": imp, "part": "head"},
        {"t": "diff", "rows": [{"label": "FC7-0102", "sub": "сквозная наработка", "href": f"#/components/{fc.id}",
                                "before": before, "after": {"hours": after["hours"], "cycles": after["cycles"]}}]},
        p(f"Сразу после перестановки наработка узла: {_h(mid['hours'])} (не изменилась), на новом борту — "
          f"{_h(mid['on_board_hours'] or 0)}. После журнала на «Борт 03»: на новом борту {_h(after['on_board_hours'] or 0)}.", small=True),
        h3("Борта в истории узла"),
        table(["Борт (позиция)", "Установлен", "Снят", "Налёт на борту"],
              [[f"{st['aircraft_name']} ({st['position']})", {"dt": st["installed_at"]},
                {"dt": st["removed_at"]} if st["removed_at"] else "сейчас", _h(st["hours"])] for st in after["stints"]]),
        h3("Межсервисный интервал «ТО полётного контроллера» не обнулился при перестановке"),
        table(["", "Статус", "Наработка с последнего ТО"],
              [["до перестановки", _due(due_before), _metrics(due_before)], ["после журнала", _due(due_after), _metrics(due_after)]]),
        p("В разделе «Статистика» это предотвращённый пропуск ТО.", small=True, href="#/analytics", link="Открыть «Статистику» →"),
    ]
    return out


def s3(s):
    from ..api.routes import get_aircraft
    ac = _ac(s, "TBT-M4-001")
    rows = []
    for name, title in (("01_TBT-M4-001_smena.json", "Тот же файл ещё раз"),
                        ("03_TBT-M4-001_smena_POVTOR.json", "Копия под другим именем"),
                        ("04_TBT-M4-001_bortovoi_registrator.csv", "Другая выгрузка тех же полётов + 2 новых")):
        b = get_aircraft(ac.id, s)["hours"]
        r = _import(s, name)
        a = get_aircraft(ac.id, s)["hours"]
        rows.append([{"text": title, "bold": True, "sub": name}, {"imp_status": r["status"], "text": r["status_label"],
                                                                 "sub": (r["messages"][0] if r["messages"] else "")[:140]},
                     f"{r['flights_new']} / {r['flights_duplicate']}", f"{_h(b)} → {_h(a)}"])
    return [table(["Загрузка", "Результат", "Новых / дубл.", "Налёт борта до → после"], rows),
            p("Если первая строка показывает «Импортирован», журнал №1 ещё не загружался (сценарий 1 не выполнен) — "
              "это и есть первичная загрузка; последующие строки демонстрируют защиту.", small=True)]


def s4(s):
    from ..services.maintenance import due_items
    items = due_items(s)
    watch = [("IMU3-0206", "IMU-CAL", "часы"), ("ESC40-0301", "ESC-DIAG", "циклы"), ("TBT-VT2-011", "AC-ANNUAL", "календарь"),
             ("BVK2-0405", "BVK-SVC", "календарь"), ("ESC40-0318", "ESC-LIFE", "назначенный ресурс"),
             ("FC7-0102", "FC-CHK", "часы (после перестановки)")]
    rows = []
    for serial, code, kind in watch:
        it = next((i for i in items if i["entity"]["serial"] == serial and i["rule"]["code"] == code), None)
        if it:
            rows.append([{"entity": it["entity"]}, it["rule"]["name"], kind, _due(it), {"metric": it["driver"]}])
    return [table(["Объект", "Регламент", "Тип порога", "Статус", "Счётчик"], rows),
            p("", small=True, href="#/maintenance?tab=due", link="Полный список предстоящих и просроченных работ →")]


def s5(s):
    from ..api.routes import create_record, get_component
    from ..schemas import MaintenanceIn
    c = _comp(s, "BVK2-0405")
    card = get_component(c.id, s)
    aid = card["location"]["aircraft_id"] if card["location"] else None
    ac_before = _brief(s, aid) if aid else None
    it = next((i for i in card["due"] if i["rule"]["code"] == "BVK-SVC"), None)
    if it is None:
        raise DomainError("Регламент BVK-SVC не найден — сбросьте демо-данные")
    r = create_record(MaintenanceIn(target="component", entity_id=c.id, rule_id=it["rule"]["id"], performed_by="Демо: инженер Смирнов Д. А.",
                                    work_type="scheduled", description="Проверка SMART, чистка радиатора, обновление ПО",
                                    result="Выполнено, замечаний нет"), s)
    card2 = get_component(c.id, s)
    it2 = next(i for i in card2["due"] if i["rule"]["code"] == "BVK-SVC")
    rows = [[f"Статус работы «{it['rule']['name']}»", {"due": it["status"], "sub": _metrics(it)}, {"due": it2["status"], "sub": _metrics(it2)}],
            ["Сквозная наработка узла", _h(card["hours"]), {"text": _h(card2["hours"]), "sub": "не изменилась"}]]
    if aid:
        rows.append([f"Допуск {ac_before['name']}", {"readiness": ac_before["readiness"]}, {"readiness": _brief(s, aid)["readiness"]}])
    return [notice("ok", r["message"]), table(["", "До", "После"], rows)]


def s6(s):
    from ..api.missions import list_missions, run_mission
    from ..schemas import MissionRunIn
    m = next((x for x in list_missions(s) if x["name"].startswith("Облёт ЛЭП")), None)
    if m is None:
        raise DomainError("Миссия «Облёт ЛЭП» не найдена — сбросьте демо-данные")
    out, aid = [], m["aircraft_id"]
    if not _brief(s, aid)["airworthy"]:      # предполётная проверка не пропустит — показать отказ и взять допущенный борт
        try:
            run_mission(m["id"], MissionRunIn(), s)
        except DomainError as e:
            out.append(notice("warn", f"Предполётная проверка: {e.message}"))
        from ..api.routes import list_aircraft
        ok = next((a for a in list_aircraft(s) if a["airworthy"]), None)
        if ok is None:
            raise DomainError("Нет допущенных к полёту бортов — выполните сценарий 5 или сбросьте демо-данные")
        aid = ok["id"]
        out.append(p(f"Миссия выполнена на допущенном борту «{ok['name']}».", small=True))
    before = _snapshot(s, aid)
    r = run_mission(m["id"], MissionRunIn(aircraft_id=aid, wind_ms=4, wind_from_deg=250), s)
    sm = r["summary"]
    phases = " → ".join(dict.fromkeys(PHASE_RU.get(pt[5], pt[5]) for pt in r["track"]))
    out.append(notice("ok" if r["status"] == "completed" else "bad",
                      f"Миссия «{m['name']}» {'выполнена' if r['status'] == 'completed' else 'прервана'}: {_dur(sm['duration_s'])}, "
                      f"путь {_n(sm['distance_m'] / 1000, 2)} км, макс. боковое отклонение {_n(sm['max_xte_m'], 1)} м при ветре "
                      f"{sm['wind']['speed_ms']} м/с. Фазы: {phases}.", href=f"#/missions/{m['id']}", link="Открыть миссию с треком →"))
    out.append(_diff(before, _snapshot(s, aid)))
    return out


def s7(s):
    from ..api.missions import list_missions, run_mission
    from ..schemas import MissionRunIn
    ac = _ac(s, "TBT-VT2-011")
    b = _brief(s, ac.id)
    r = _import(s, "07_TBT-VT2-011_padenie.json")
    a = _brief(s, ac.id)
    out = []
    if r["status"] == "duplicate":
        out.append(notice("warn", "Журнал уже загружен — сбросьте демо-данные для чистого прогона."))
    out.append(table(["", "До", "После"], [
        [f"Падений / столкновений {a['name']}", f"{b['events']['fall']} / {b['events']['collision']}",
         {"text": f"{a['events']['fall']} / {a['events']['collision']}", "bold": True}],
        ["Допуск", {"readiness": b["readiness"]}, {"readiness": a["readiness"]}]]))
    out.append({"t": "import", "data": r, "part": "sensors"})
    m = next((x for x in list_missions(s) if x["aircraft_id"] == ac.id), None)
    if m:
        try:
            run_mission(m["id"], MissionRunIn(), s)
            out.append(notice("warn", "Предполётная проверка пропустила борт (осмотр уже выполнен?)"))
        except DomainError as e:
            out.append(notice("bad", f"Вылет запрещён: {e.message}"))
    return out


def s8(s):
    from ..api.routes import get_component
    r1 = _import(s, "08_dvizhenie_uzlov.xlsx")
    r2 = _import(s, "09_dvizhenie_uzlov.pdf")
    c = get_component(_comp(s, "ESC40-0320").id, s)
    loc = f"{c['location']['aircraft_name']} · {c['location']['position']}" if c["location"] else c["status_label"]
    boards = ", ".join(dict.fromkeys(st["aircraft_name"] for st in c["stints"]))
    res = c["residual"] or {}
    return [h3(f"Excel: {r1['status_label']}"), {"t": "import", "data": r1, "part": "full"},
            h3(f"PDF того же журнала: {r2['status_label']}"),
            p("; ".join(r2["messages"][:3]) + (" …" if len(r2["messages"]) > 3 else ""), small=True),
            notice("ok", f"{c['serial']}: {loc}, наработка {_h(c['hours'])}, остаточный ресурс "
                         f"{_h(max(0, res['hours'])) if res.get('hours') is not None else '—'}; борта в истории: {boards}.",
                   href=f"#/components/{c['id']}", link="Паспорт узла →")]


def s9(s):
    from ..api.routes import board as board_view
    from . import board
    b = board_view(s)
    a1 = next(a for a in b["aircraft"] if a["serial"] == "TBT-M4-001")
    a2 = next(a for a in b["aircraft"] if a["serial"] == "TBT-M4-002")
    c1 = next(sl for sl in a1["slots"] if sl["position"] == "ESC1")["component"]
    c2 = next(sl for sl in a2["slots"] if sl["position"] == "ESC1")["component"]
    if not c1 or not c2:
        raise DomainError("Позиции ESC1 не укомплектованы — сбросьте демо-данные")
    res = board.execute(s, c1["id"], {"kind": "slot", "aircraft_id": a2["id"], "position": "ESC1"},
                        reason="Демо-сценарий 9: обмен ESC1 между бортами", performed_by="Демо")
    s.commit()
    return [notice("ok", f"{res['title']} — выполнено той же серверной операцией, что и перетаскивание на доске.",
                   href="#/board", link="Открыть доску →"), {"t": "move", "data": res}]


def s10(s):
    from ..api.routes import notifications
    d = notifications(90, s)
    rows = [[{"text": d["categories"][k], "bold": True}, {"text": str(d["counts"][k]), "bold": True},
             "; ".join(f"{i['entity']['serial']} — {i['rule']['name']}" for i in d["items"][k][:3]) or "—"]
            for k in ("limit", "overdue", "approaching")]
    rows.append([f"Падения и столкновения за {d['days']} дн.", str(d["counts"]["sensor_events"]),
                 "; ".join(f"{(e['aircraft'] or {}).get('name', '')} — {e['kind_label']}" for e in d["sensor_events"][:3]) or "—"])
    return [table(["Категория", "Кол-во", "Примеры"], rows),
            p("Полный список с действиями «Выполнить» / «Заменить», журнал уведомлений и доставка в Telegram / webhook — "
              "раздел «ТО и уведомления».", small=True, href="#/maintenance", link="Открыть →")]


RUNNERS = {1: s1, 2: s2, 3: s3, 4: s4, 5: s5, 6: s6, 7: s7, 8: s8, 9: s9, 10: s10}


def run(s: Session, n: int) -> dict:
    fn = RUNNERS.get(n)
    if fn is None:
        raise DomainError("Нет такого сценария")
    return {"n": n, "title": TITLES[n], "blocks": fn(s)}
