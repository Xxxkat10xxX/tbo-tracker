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
