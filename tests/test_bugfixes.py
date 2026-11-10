"""Регрессионные тесты найденных и исправленных багов (входные данные, целостность, уведомления)."""
import json

from tests.conftest import by_serial


def _imp(client, flight: dict, name="x.json"):
    """Журнал отдельного тестового борта без полётов — чтобы не пересекаться с демо-историей."""
    if not any(a["serial"] == "BUG-1" for a in client.get("/api/aircraft").json()):
        tid = client.get("/api/meta").json()["aircraft_types"][0]["id"]
        client.post("/api/aircraft", json={"serial": "BUG-1", "name": "Тестовый", "type_id": tid, "in_service_at": "2026-01-01T00:00:00Z"})
    doc = {"aircraft_serial": "BUG-1", "flights": [flight]}
    return client.post("/api/imports", files={"file": (name, json.dumps(doc).encode())})


def _f(n, **kw):
    return {"flight_id": f"BUG-{n}", "start": f"2026-10-0{n}T08:00:00Z", "end": f"2026-10-0{n}T08:30:00Z", **kw}


# ------------------------------------------------------------------ 500 на неверных данных журнала
def test_bad_cycles_and_text_fields_are_rejected_not_500(client):
    for i, bad in enumerate(([1], {"a": 1}, "inf", "1e400", "nan", 1.5), 1):
        r = _imp(client, _f(1, cycles=bad, flight_id=f"C{i}"))
        assert r.status_code == 200, (bad, r.text)
        assert r.json()["status"] == "rejected" and "цикл" in r.json()["messages"][0]
    r = _imp(client, _f(2, pilot=["a", "b"]))
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    r = _imp(client, _f(3, mission="М" * 1000)).json()
    assert r["status"] == "imported"                                            # длинный текст обрезается


def test_bad_sensor_data_keeps_flight_and_warns(client):
    for i, extra in enumerate(({"imu": {"rate_hz": "abc", "accel_g": [1, 2]}}, {"imu": {"accel_g": ["x", None]}},
                               {"imu": [1, 2]}, {"events": 5}, {"events": ["fall"]},
                               {"events": [{"type": "fall", "peak_g": "big"}]}, {"falls": "inf"}), 1):
        r = _imp(client, _f(i, flight_id=f"S{i}", **extra))
        assert r.status_code == 200, (extra, r.text)
        j = r.json()
        assert j["status"] == "imported" and any("датчиков не учтены" in m for m in j["messages"]), (extra, j["messages"])
    assert by_serial(client, "BUG-1")["events"] == {"fall": 0, "collision": 0}


def test_sensor_event_time_clamped_to_flight(client):
    r = _imp(client, _f(4, events=[{"type": "collision", "t_s": 999999, "peak_g": 5}])).json()
    ev = r["summary"]["sensor_events"][0]
    assert ev["at"] == "2026-10-04T08:30:00Z"                                  # не позже посадки


def test_nan_in_json_body_is_422(client):
    r = client.post("/api/components/1/usage", content=b'{"hours": NaN, "cycles": 1, "kind": "bench"}',
                    headers={"content-type": "application/json"})
    assert r.status_code == 422 and r.json()["detail"][0]["loc"][-1] == "hours"
    r = client.post("/api/analytics/estimate", content=b'{"failure_probability": Infinity}', headers={"content-type": "application/json"})
    assert r.status_code == 422


def test_bad_mission_files_are_400(client):
    for content in (b"QGC WPL 110\n0\t1\t0\t16\t0\t0\t0\t0\tx\ty\tz\t1\n",
                    json.dumps({"mission": {"plannedHomePosition": "x", "items": [{"type": "SimpleItem", "command": 16,
                                                                                  "params": ["a", 0, 0, 0, "b", "c", "d"]}]}}).encode(),
                    json.dumps({"mission": {"items": 5}}).encode(),
                    json.dumps({"mission": {"plannedHomePosition": [95, 0, 0], "items": [{"type": "SimpleItem", "command": 16,
                                                                                         "params": [0, 0, 0, 0, 91, 0, 10]}]}}).encode()):
        r = client.post("/api/missions/import", files={"file": ("m.plan", content)})
        assert r.status_code == 400, (content[:40], r.status_code, r.text)


def test_mission_fault_beyond_waypoints_and_russian_status(client):
    ms = client.get("/api/missions").json()
    m = next(x for x in ms if x["name"].startswith("Аэрофото"))
    r = client.post(f"/api/missions/{m['id']}/run", json={"fault_at_wp": 150})
    assert r.status_code == 400 and "точек" in r.json()["detail"]
    a = by_serial(client, "TBT-M4-003")                                         # борт на обслуживании
    r = client.post(f"/api/missions/{m['id']}/run", json={"aircraft_id": a["id"]})
    assert "в статусе «На обслуживании»" in r.json()["detail"] and "maintenance" not in r.json()["detail"]


# ------------------------------------------------------------------ целостность данных
def test_cannot_retire_aircraft_with_installed_components(client):
    a = by_serial(client, "TBT-VT2-011")
    r = client.patch(f"/api/aircraft/{a['id']}", json={"status": "retired"})
    assert r.status_code == 400 and "снимите" in r.json()["detail"]
    assert by_serial(client, "TBT-VT2-011")["status"] == "active"
    assert client.patch(f"/api/aircraft/{a['id']}", json={"name": "  "}).status_code == 400
    assert client.patch(f"/api/aircraft/{a['id']}", json={"name": None}).status_code == 400
    assert client.patch(f"/api/aircraft/{a['id']}", json={"name": " Борт 11Б "}).status_code == 200
    assert by_serial(client, "TBT-VT2-011")["name"] == "Борт 11Б"


def test_service_files_cannot_be_used_as_photos(client):
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    up = client.post("/api/drone-images", files=[("files", ("p.png", png, "image/png"))]).json()["added"][0]
    a = by_serial(client, "TBT-M4-001")
    assert client.put(f"/api/aircraft/{a['id']}/image", json={"image": "/media/index.json"}).status_code == 400
    assert client.delete("/api/drone-images", params={"url": "/media/index.json"}).status_code == 400
    assert client.put(f"/api/aircraft/{a['id']}/image", json={"image": up}).status_code == 200


# ------------------------------------------------------------------ уведомления
def test_first_notification_on_empty_install_is_not_silent(client):
    """Пустая установка (без демо-данных): журнал уведомлений пуст, но первое событие должно рассылаться."""
    from app.db import Base, SessionLocal, engine
    from app.models import Aircraft, AircraftType, MaintenanceRule
    from app.services import notify
    from app.timeutil import utcnow
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as s:
        assert notify.scan(s) == []                                             # пустая база — тихая отметка
        t = AircraftType(code="Q", name="Q", slots=[])
        s.add(t)
        s.flush()
        s.add(Aircraft(serial="Q-1", name="Q-1", type_id=t.id, in_service_at=utcnow().replace(year=2020)))
        s.add(MaintenanceRule(code="CAL", name="Календарное ТО", target="aircraft", kind="periodic", interval_days=30))
        s.commit()
        new = notify.scan(s)
        assert [e.category for e in new] == ["overdue"] and not new[0].silent


def test_failed_delivery_is_retried(client, monkeypatch):
    from app.db import SessionLocal
    from app.services import notify, serverconf
    client.get("/api/notifications/feed")                                       # первичное заполнение
    serverconf.save({"webhook": {"enabled": True, "url": "http://127.0.0.1:9/hook"}})
    calls = []
    monkeypatch.setattr(notify, "_http_post", lambda url, payload, timeout=10: calls.append(1) or "ошибка: нет связи")
    client.post("/api/demo-logs/07_TBT-VT2-011_padenie.json/import")
    with SessionLocal() as s:
        notify.scan(s)
        assert notify.deliver_pending(s) == {"webhook": "ошибка: нет связи"}
        monkeypatch.setattr(notify, "_http_post", lambda url, payload, timeout=10: calls.append(1) or "ok (200)")
        assert notify.deliver_pending(s) == {"webhook": "ok (200)"}            # повтор после восстановления связи
        assert notify.deliver_pending(s) == {}
    assert len(calls) == 2


def test_partial_import_can_be_reapplied_after_fixing_data(client):
    """Строка с незарегистрированным узлом отклонена → узел зарегистрировали → тот же файл применяет только её."""
    r1 = client.post("/api/demo-logs/08_dvizhenie_uzlov.xlsx/import").json()
    assert r1["status"] == "partial" and r1["flights_new"] == 5 and r1["flights_rejected"] == 1
    tid = next(t["id"] for t in client.get("/api/meta").json()["component_types"] if t["code"] == "flight_controller")
    client.post("/api/components", json={"serial": "FC7-9999", "type_id": tid, "in_service_at": "2026-01-01T00:00:00Z"})
    r2 = client.post("/api/demo-logs/08_dvizhenie_uzlov.xlsx/import").json()
    assert r2["status"] != "duplicate", r2["messages"]
    assert r2["flights_duplicate"] == 5                                         # уже применённые строки не повторяются
    applied = r2["summary"]["movements"]
    assert [m["serial"] for m in applied] == ["FC7-9999"] and r2["flights_rejected"] == 0
    # полностью успешный файл по-прежнему распознаётся как повтор целиком
    client.post("/api/demo-logs/01_TBT-M4-001_smena.json/import")
    assert client.post("/api/demo-logs/01_TBT-M4-001_smena.json/import").json()["status"] == "duplicate"


def test_seed_cli_on_fresh_data_dir(tmp_path):
    """python -m app.seed на пустом каталоге данных (каталог базы создаётся сам)."""
    import os
    import subprocess
    import sys
    env = {**os.environ, "TBO_DATA_DIR": str(tmp_path / "fresh"), "TBO_DATABASE_URL": ""}
    r = subprocess.run([sys.executable, "-m", "app.seed"], env=env, capture_output=True, text=True, timeout=300,
                       cwd=os.path.dirname(os.path.dirname(__file__)))
    assert r.returncode == 0, r.stderr[-2000:]
    assert (tmp_path / "fresh" / "tbo.db").exists()


# ------------------------------------------------------------------ одновременные запросы (двойной щелчок)
def test_db_rejects_second_open_installation(client):
    """Проверки «прочитал — записал» в inventory не спасают от двух одновременных запросов: гарантия — индекс БД."""
    import pytest
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    from app.db import SessionLocal
    from app.models import Component, Installation
    from app.timeutil import utcnow

    with SessionLocal() as s:
        inst = s.scalar(select(Installation).where(Installation.removed_at.is_(None)).limit(1))
        spare = s.scalar(select(Component).where(Component.status == "stock", Component.type_id == inst.component.type_id).limit(1))
        cases = [  # тот же узел ещё в одном месте; занятая позиция — ещё один узел
            dict(component_id=inst.component_id, aircraft_id=inst.aircraft_id, position="X-DUP"),
            dict(component_id=spare.id, aircraft_id=inst.aircraft_id, position=inst.position),
        ]
    for kw in cases:
        with SessionLocal() as s:
            s.add(Installation(installed_at=utcnow(), **kw))
            with pytest.raises(IntegrityError):
                s.commit()


def _xlsx(rows):
    import io

    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["Дата", "Операция", "Узел", "Борт", "Позиция", "Состояние после снятия", "С борта"])
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_movement_log_positions_sources_statuses_dates(client):
    meta = client.get("/api/meta").json()
    spare = next(c for c in client.get("/api/components?status=stock").json())
    ctype = spare["type"]["code"]
    tid = client.post("/api/aircraft-types", json={"code": "MV-LOW", "name": "Тип с позициями в нижнем регистре",
                                                   "slots": [{"position": "slot1", "label": "Место 1", "component_type": ctype}]}).json()["id"]
    assert meta["aircraft_types"]
    client.post("/api/aircraft", json={"serial": "MV-1", "name": "Борт МВ", "type_id": tid, "in_service_at": "2026-01-01T00:00:00Z"})
    rows = [
        ["05.11.2026 09:00:30", "Установка", spare["serial"], "MV-1", "SLOT1", "", ""],     # регистр позиции и секунды
        ["05.11.2026 10:00", "Снятие", spare["serial"], "Борт 01", "", "", ""],            # снимают не с того борта
        ["05.11.2026 10:30", "Снятие", spare["serial"], "", "", "утилизация", ""],         # неизвестное состояние
        ["31.02.2026 10:00", "Снятие", spare["serial"], "", "", "", ""],                   # несуществующая дата
    ]
    r = client.post("/api/imports", files={"file": ("mv.xlsx", _xlsx(rows))}).json()
    msgs = " | ".join(r["messages"])
    assert r["flights_new"] == 1 and r["flights_rejected"] == 3, msgs
    assert "числится на борту Борт МВ" in msgs and "неизвестное состояние" in msgs and "не распознана" in msgs
    c = next(x for x in client.get("/api/components").json() if x["serial"] == spare["serial"])
    assert c["status"] == "installed" and c["location"]["position"] == "slot1"


def test_generator_does_not_overlap_its_own_flights(client):
    r = client.post("/api/log-generator", json={"aircraft_serials": ["TBT-M4-001"], "date_from": "2026-10-10T00:00:00Z",
                                                "date_to": "2026-10-13T00:00:00Z", "fly_probability": 1, "flights_min": 4,
                                                "flights_max": 4, "duration_min": 500, "duration_max": 600, "seed": 3}).json()
    iv = sorted((f["start"], f["end"]) for f in r["preview"])
    assert len(iv) >= 4 and all(a[1] <= b[0] for a, b in zip(iv, iv[1:]))            # полёты не пересекаются


def test_analytics_does_not_count_crash_inspection_as_prevented_miss(client):
    client.post("/api/demo-logs/07_TBT-VT2-011_padenie.json/import")
    an = client.get("/api/analytics").json()
    shock = [p for p in an["prevented"] if p["rule"]["code"] == "IMU-SHOCK" and p["entity"]["serial"] == "IMU3-0204"]
    assert shock == []                       # удар был на этом же борту — учёт «по борту» его тоже видит


def test_notification_message_fits_telegram_limit():
    from app.services.notify import format_message
    ev = [{"category": "overdue", "title": f"КОМПОНЕНТ-{i} · Борт {i} (ESC{i}) — " + "Очень длинное название регламента " * 8,
           "text": "Часы: 120 из 100 ч"} for i in range(25)]
    msg = format_message(ev, {"public_url": "https://tbo.example.ru"})
    assert len(msg) <= 4096 and "… и ещё" in msg and msg.endswith("/#/maintenance")
    assert format_message(ev[:2], {}).count("\n") == 2                     # короткий пакет — целиком


def test_restore_of_broken_or_inconsistent_backup_is_clear_and_harmless(client):
    import io
    import json as js
    import zipfile

    b = client.post("/api/server/backups").json()
    src = zipfile.ZipFile(io.BytesIO(client.get(f"/api/server/backups/{b['name']}").content))
    flights_before = len(client.get("/api/flights?limit=2000").json())

    def patched(table, data):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for n in src.namelist():
                z.writestr(n, data if n == f"tables/{table}.json" else src.read(n))
        up = client.post("/api/server/backups/upload", files={"file": ("x.zip", buf.getvalue())}).json()
        return client.post(f"/api/server/backups/{up['name']}/restore")

    r = patched("flights", b'[{"id": 1, "aircr')                              # обрезанный файл в архиве
    assert r.status_code == 400 and "Повреждена таблица flights" in r.json()["detail"]
    inst = js.loads(src.read("tables/installations.json"))
    open_ = next(i for i in inst if i["removed_at"] is None)
    dup = dict(open_, id=max(i["id"] for i in inst) + 1, position="DUP")       # узел «открыт» в двух местах
    r = patched("installations", js.dumps(inst + [dup]).encode())
    assert r.status_code == 400 and "не восстановлена" in r.json()["detail"]
    assert len(client.get("/api/flights?limit=2000").json()) == flights_before  # база прежняя


def test_mission_files_keep_takeoff_alt_and_convert_amsl():
    import json as js

    from app.services.autopilot import parse_mission_file, to_plan, to_waypoints
    m = {"name": "M", "home_lat": 55.0, "home_lon": 37.0, "takeoff_alt": 20.0, "speed_ms": 6.0, "return_home": True,
         "waypoints": [{"lat": 55.001, "lon": 37.0, "alt": 35.0, "hold_s": 2}, {"lat": 55.001, "lon": 37.002, "alt": 40.0, "hold_s": 0}]}
    for name, body in (("m.plan", js.dumps(to_plan(m, "px4")).encode()), ("m.waypoints", to_waypoints(m).encode())):
        back = parse_mission_file(body, name)
        assert back["takeoff_alt"] == 20.0, name                              # прежде — высота первой точки (35)
        assert [w["alt"] for w in back["waypoints"]] == [35.0, 40.0], name
    # точки в кадре 0 (над уровнем моря) — от высоты «дома»; кадр 3 — как есть
    wpl = ("QGC WPL 110\n0\t1\t0\t16\t0\t0\t0\t0\t55.0\t37.0\t150.0\t1\n"
           "1\t0\t0\t16\t0\t0\t0\t0\t55.001\t37.0\t180.0\t1\n2\t0\t3\t16\t0\t0\t0\t0\t55.001\t37.002\t40.0\t1\n")
    assert [w["alt"] for w in parse_mission_file(wpl.encode(), "a.waypoints")["waypoints"]] == [30.0, 40.0]
    plan = {"fileType": "Plan", "mission": {"plannedHomePosition": [55.0, 37.0, 150.0], "items": [
        {"type": "SimpleItem", "command": 16, "frame": 0, "params": [0, 0, 0, None, 55.001, 37.0, 175.0]}]}}
    assert parse_mission_file(js.dumps(plan).encode(), "b.plan")["waypoints"][0]["alt"] == 25.0
