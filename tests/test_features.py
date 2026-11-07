"""Тесты функций: ручной ввод полётов, внебортовая наработка, справочники типов, генератор журналов."""
from datetime import datetime, timedelta, timezone

from tests.conftest import by_serial, due

NOW = lambda: datetime.now(timezone.utc)  # noqa: E731


def _snap(client, serial):
    a = client.get(f"/api/aircraft/{by_serial(client, serial)['id']}").json()
    return a["hours"], {s["component"]["serial"]: s["component"]["hours"] for s in a["configuration"] if s["component"]}


def test_manual_flight_accrues_like_import_and_can_be_deleted(client):
    a1 = by_serial(client, "TBT-M4-001")
    h0, comps0 = _snap(client, "TBT-M4-001")
    start = NOW() - timedelta(hours=3)
    body = {"aircraft_id": a1["id"], "start": start.isoformat(), "end": (start + timedelta(minutes=30)).isoformat(), "cycles": 2}
    r = client.post("/api/flights", json=body)
    assert r.status_code == 201, r.text
    h1, comps1 = _snap(client, "TBT-M4-001")
    assert abs(h1 - h0 - 0.5) < 0.011
    assert all(abs(comps1[k] - comps0[k] - 0.5) < 0.011 for k in comps0)
    # тот же полёт повторно — дубликат
    assert client.post("/api/flights", json=body).status_code == 400
    # пересечение по времени — тоже
    body2 = dict(body, start=(start + timedelta(minutes=10)).isoformat(), end=(start + timedelta(minutes=50)).isoformat())
    assert client.post("/api/flights", json=body2).status_code == 400
    f = client.get("/api/flights", params={"aircraft_id": a1["id"], "limit": 1}).json()[0]
    assert f["manual"] is True
    assert client.delete(f"/api/flights/{f['id']}").status_code == 200
    assert _snap(client, "TBT-M4-001")[0] == h0


def test_manual_flight_from_component_card_checks_location(client):
    imu = by_serial(client, "IMU3-0201")  # на складе
    a2 = by_serial(client, "TBT-M4-002")
    start = NOW() - timedelta(hours=2)
    r = client.post("/api/flights", json={"aircraft_id": a2["id"], "component_id": imu["id"], "start": start.isoformat(),
                                          "end": (start + timedelta(minutes=20)).isoformat()})
    assert r.status_code == 400 and "вне борта" in r.json()["detail"]
    fc = by_serial(client, "FC7-0102")  # стоит на Борт 02
    r = client.post("/api/flights", json={"aircraft_id": a2["id"], "component_id": fc["id"], "start": start.isoformat(),
                                          "end": (start + timedelta(minutes=20)).isoformat()})
    assert r.status_code == 201


def test_imported_flight_cannot_be_deleted_individually(client):
    f = client.get("/api/flights", params={"limit": 1}).json()[0]
    assert f["manual"] is False
    assert client.delete(f"/api/flights/{f['id']}").status_code == 400


def test_off_board_usage_entry(client):
    imu = by_serial(client, "IMU3-0201")
    a_hours = by_serial(client, "TBT-M4-001")["hours"]
    r = client.post(f"/api/components/{imu['id']}/usage", json={"hours": 2.5, "cycles": 4, "kind": "bench", "note": "стенд"})
    assert r.status_code == 201, r.text
    after = by_serial(client, "IMU3-0201")
    assert abs(after["hours"] - imu["hours"] - 2.5) < 0.011 and after["cycles"] == imu["cycles"] + 4
    assert by_serial(client, "TBT-M4-001")["hours"] == a_hours  # борт не затронут
    card = client.get(f"/api/components/{imu['id']}").json()
    assert any(f["source"] == "entry" for f in card["flight_log"])
    # учитывается в регламенте
    assert due(client, "IMU3-0201", "IMU-CAL")["metrics"][0]["used"] >= 2.5
    eid = r.json()["entry"]["id"]
    assert client.delete(f"/api/components/{imu['id']}/usage/{eid}").status_code == 200
    assert by_serial(client, "IMU3-0201")["hours"] == imu["hours"]


def test_bench_entry_rejected_while_installed(client):
    fc = by_serial(client, "FC7-0106")  # установлен
    r = client.post(f"/api/components/{fc['id']}/usage", json={"hours": 1, "kind": "bench"})
    assert r.status_code == 400
    r = client.post(f"/api/components/{fc['id']}/usage", json={"hours": 1, "kind": "correction"})
    assert r.status_code == 201


def test_custom_component_type_slot_and_install(client):
    r = client.post("/api/component-types", json={"code": "gnss", "name": "GNSS-приёмник"})
    assert r.status_code == 201
    meta = client.get("/api/meta").json()
    at = next(t for t in meta["aircraft_types"] if t["code"] == "TBT-M4")
    ct = next(t for t in meta["component_types"] if t["code"] == "gnss")
    body = {k: at[k] for k in ("code", "name", "category", "manufacturer")}
    body["slots"] = at["slots"] + [{"position": "GNSS", "label": "GNSS-приёмник", "component_type": "gnss", "required": False}]
    assert client.put(f"/api/aircraft-types/{at['id']}", json=body).status_code == 200
    a1 = by_serial(client, "TBT-M4-001")
    assert a1["slots_filled"] == a1["slots_total"]  # необязательная позиция не ломает комплектность
    r = client.post("/api/components", json={"serial": "GNSS-0001", "type_id": ct["id"], "initial_hours": 10})
    assert r.status_code == 201
    cid = r.json()["id"]
    r = client.post(f"/api/components/{cid}/install", json={"aircraft_id": a1["id"], "position": "GNSS"})
    assert r.status_code == 200, r.text
    # регламент на новый тип
    r = client.post("/api/rules", json={"code": "GNSS-CHK", "name": "Проверка GNSS", "target": "component",
                                        "component_type_id": ct["id"], "interval_hours": 11})
    assert r.status_code == 201
    assert due(client, "GNSS-0001", "GNSS-CHK")["status"] == "due_soon"
    # занятую позицию удалить нельзя
    body["slots"] = at["slots"]
    assert client.put(f"/api/aircraft-types/{at['id']}", json=body).status_code == 400
    # тип с узлами удалить нельзя
    assert client.delete(f"/api/component-types/{ct['id']}").status_code == 400


def test_new_aircraft_type_and_aircraft(client):
    r = client.post("/api/aircraft-types", json={"code": "TBT-H6", "name": "Гексакоптер", "slots": [
        {"position": "FC", "label": "ПК", "component_type": "flight_controller"}]})
    assert r.status_code == 201
    r = client.post("/api/aircraft", json={"serial": "TBT-H6-001", "name": "Борт 21", "type_id": r.json()["id"]})
    assert r.status_code == 201
    fc = by_serial(client, "FC7-0107")
    assert client.post(f"/api/components/{fc['id']}/install", json={"aircraft_id": r.json()["id"], "position": "FC"}).status_code == 200
    assert by_serial(client, "TBT-H6-001")["slots_filled"] == 1


def test_log_generator_generates_and_imports(client):
    today = NOW().replace(hour=0, minute=0, second=0, microsecond=0)
    body = {"aircraft_serials": ["TBT-M4-002", "TBT-VT2-011"], "date_from": (today - timedelta(days=3)).isoformat(),
            "date_to": (today - timedelta(days=1)).isoformat(), "format": "csv", "fly_probability": 1, "flights_min": 2,
            "flights_max": 2, "seed": 7, "duplicates": 3, "errors": True}
    r = client.post("/api/log-generator", json=body)
    assert r.status_code == 200, r.text
    g = r.json()
    assert g["stats"]["new_flights"] == 12 and g["stats"]["duplicates"] == 3 and g["stats"]["errors"] == 4
    assert g["content"].startswith("# tbo-flightlog/1.0")
    # тот же seed — тот же журнал (кроме строки с датой генерации)
    assert client.post("/api/log-generator", json=body).json()["content"].split("\n")[1:] == g["content"].split("\n")[1:]
    r = client.post("/api/log-generator", json=dict(body, import_now=True)).json()["import"]
    assert r["status"] == "partial" and r["flights_new"] == 12 and r["flights_duplicate"] == 3 and r["flights_rejected"] == 4
    # повторная генерация с avoid_overlap не даёт пересечений
    g2 = client.post("/api/log-generator", json=dict(body, duplicates=0, errors=False, seed=8, import_now=True)).json()
    assert g2["stats"]["skipped_overlap"] > 0
    assert g2["import"]["flights_duplicate"] == 0 and g2["import"]["flights_new"] == g2["stats"]["new_flights"]


def test_log_generator_json_single_and_validation(client):
    today = NOW().replace(hour=0, minute=0, second=0, microsecond=0)
    r = client.post("/api/log-generator", json={"aircraft_serials": ["TBT-M4-001"], "date_from": (today - timedelta(days=2)).isoformat(),
                                                "date_to": (today - timedelta(days=2)).isoformat(), "seed": 1, "fly_probability": 1})
    g = r.json()
    assert '"aircraft_serial": "TBT-M4-001"' in g["content"] and '"format": "tbo-flightlog/1.0"' in g["content"]
    r = client.post("/api/log-generator", json={"aircraft_serials": ["NOPE"], "date_from": today.isoformat(), "date_to": today.isoformat()})
    assert r.status_code == 400


def test_loggen_cli(tmp_path, client):
    from app.loggen import main
    out = tmp_path / "log.json"
    main(["--aircraft", "TBT-M4-002", "--days", "2", "--seed", "3", "-o", str(out)])
    assert out.read_text("utf-8").count("flight_id") >= 0 and "tbo-flightlog" in out.read_text("utf-8")


def test_no_vendor_mentions_in_ui_and_data(client):
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    texts = [p.read_text("utf-8") for p in (root / "app").rglob("*") if p.suffix in (".py", ".js", ".html")]
    texts.append(str(client.get("/api/meta").json()))
    for t in texts:
        for bad in ("СОКОЛ", "SOKOL", "sokol", "СПУТНИК", "ООО"):
            assert bad not in t


def test_individual_resource_component(client):
    c = by_serial(client, "FC7-0106")                     # ≈107 ч
    r = client.patch(f"/api/components/{c['id']}", json={"resource_hours": 115})
    assert r.status_code == 200
    item = due(client, "FC7-0106", "RESOURCE")
    assert item["rule"]["kind"] == "life" and item["status"] == "due_soon"
    assert client.patch(f"/api/components/{c['id']}", json={"resource_hours": 100}).status_code == 200
    assert due(client, "FC7-0106", "RESOURCE")["status"] == "overdue"
    assert by_serial(client, "FC7-0106")["resource"]["hours"] == 100
    # ресурс работой не «закрывается»; аналитика с ним не падает
    assert client.post("/api/maintenance/records", json={"target": "component", "entity_id": c["id"], "rule_id": 0}).status_code in (400, 404)
    assert client.get("/api/analytics").status_code == 200
    # сброс
    client.patch(f"/api/components/{c['id']}", json={"resource_hours": None})
    try:
        due(client, "FC7-0106", "RESOURCE"); assert False
    except KeyError:
        pass
    assert client.patch(f"/api/components/{c['id']}", json={"resource_cycles": 0}).status_code == 422


def test_individual_resource_aircraft_and_create(client):
    a = by_serial(client, "TBT-VT2-012")
    client.patch(f"/api/aircraft/{a['id']}", json={"resource_days": 100})          # в эксплуатации 150 дней
    assert due(client, "TBT-VT2-012", "RESOURCE")["status"] == "overdue"
    ct = client.get("/api/meta").json()["component_types"][0]
    r = client.post("/api/components", json={"serial": "NEW-1", "type_id": ct["id"], "initial_hours": 45, "resource_hours": 50})
    assert r.status_code == 201
    assert due(client, "NEW-1", "RESOURCE")["status"] == "due_soon"


def test_custom_type_rule_targets_new_type(client):
    t = client.post("/api/component-types", json={"code": "batt", "name": "Аккумулятор"}).json()
    client.post("/api/components", json={"serial": "BAT-1", "type_id": t["id"], "initial_cycles": 290})
    r = client.post("/api/rules", json={"code": "BAT-LIFE", "name": "Ресурс АКБ", "target": "component", "kind": "life",
                                        "component_type_id": t["id"], "interval_cycles": 300})
    assert r.status_code == 201
    assert due(client, "BAT-1", "BAT-LIFE")["status"] == "due_soon"


def test_migration_adds_resource_columns(tmp_path):
    from sqlalchemy import create_engine, inspect, text
    from app.db import migrate
    eng = create_engine(f"sqlite:///{tmp_path}/old.db")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE aircraft (id INTEGER PRIMARY KEY)"))
        c.execute(text("CREATE TABLE components (id INTEGER PRIMARY KEY)"))
    migrate(eng)
    cols = {x["name"] for x in inspect(eng).get_columns("components")}
    assert {"resource_hours", "resource_cycles", "resource_days"} <= cols


def test_aircraft_images(client):
    lib = client.get("/api/drone-images").json()
    assert len(lib["presets"]) == 5
    for p in lib["presets"]:
        assert client.get(p["url"]).status_code == 200
    a = by_serial(client, "TBT-M4-001")
    assert a["image"] == "/static/drones/inspection.svg"          # демо-данные с картинками
    r = client.put(f"/api/aircraft/{a['id']}/image", json={"image": "/static/drones/agro.svg"})
    assert r.status_code == 200 and by_serial(client, "TBT-M4-001")["image"].endswith("agro.svg")
    assert client.put(f"/api/aircraft/{a['id']}/image", json={"image": "/etc/passwd"}).status_code == 400
    assert client.put(f"/api/aircraft/{a['id']}/image", json={"image": "/media/../tbo.db"}).status_code == 400
    # загрузка своего фото
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    r = client.post(f"/api/aircraft/{a['id']}/image", files={"file": ("my.png", png, "image/png")})
    assert r.status_code == 200
    url = r.json()["image"]
    assert url.startswith("/media/") and client.get(url).content == png
    assert any(u["url"] == url for u in client.get("/api/drone-images").json()["uploaded"])
    # SVG и прочее не принимаем
    r = client.post(f"/api/aircraft/{a['id']}/image", files={"file": ("x.svg", b"<svg onload=alert(1)/>", "image/svg+xml")})
    assert r.status_code == 400
    # убрать картинку
    assert client.put(f"/api/aircraft/{a['id']}/image", json={"image": None}).json()["image"] is None


def test_photo_pool(client):
    png = b"\x89PNG\r\n\x1a\n" + b"\x01" * 40
    jpg = b"\xff\xd8\xff\xe0" + b"\x02" * 40
    r = client.post("/api/drone-images", files=[("files", ("Борт_01_на_поле.png", png, "image/png")),
                                                 ("files", ("hangar.jpg", jpg, "image/jpeg")),
                                                 ("files", ("bad.svg", b"<svg/>", "image/svg+xml"))])
    assert r.status_code == 201
    j = r.json()
    assert len(j["added"]) == 2 and len(j["errors"]) == 1
    pool = client.get("/api/drone-images").json()["uploaded"]
    titles = {u["url"]: u["title"] for u in pool}
    assert titles[j["added"][0]] == "Борт 01 на поле"
    # подпись
    assert client.patch("/api/drone-images", json={"url": j["added"][1], "title": "Ангар"}).status_code == 200
    assert {u["url"]: u["title"] for u in client.get("/api/drone-images").json()["uploaded"]}[j["added"][1]] == "Ангар"
    # назначенное фото удалить нельзя, свободное — можно
    a = by_serial(client, "TBT-M4-002")
    client.put(f"/api/aircraft/{a['id']}/image", json={"image": j["added"][0]})
    used = next(u for u in client.get("/api/drone-images").json()["uploaded"] if u["url"] == j["added"][0])
    assert used["used_by"] == ["Борт 02"]
    assert client.delete("/api/drone-images", params={"url": j["added"][0]}).status_code == 409
    assert client.delete("/api/drone-images", params={"url": j["added"][1]}).status_code == 200
    assert client.get(j["added"][1]).status_code == 404
    assert client.delete("/api/drone-images", params={"url": "/static/drones/agro.svg"}).status_code == 400
