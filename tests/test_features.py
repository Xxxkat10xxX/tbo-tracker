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


def test_sensor_detector_thresholds():
    from app.services.sensors import detect
    hover = [1.0] * 100
    assert detect(hover, 50) == []
    fall = detect(hover + [0.1] * 15 + [7.0] + hover, 50)
    assert [e["kind"] for e in fall] == ["fall"] and fall[0]["peak_g"] == 7.0
    hit = detect(hover + [5.0] + hover, 50)
    assert [e["kind"] for e in hit] == ["collision"]
    assert detect(hover + [3.5] + hover, 50) == []                       # 3.5 g без невесомости — не столкновение
    assert len(detect(hover + [6.0, 1.0, 6.5] + hover, 50)) == 1          # одно событие, а не два


def test_fall_from_log_triggers_inspection_and_counters(client):
    a11 = by_serial(client, "TBT-VT2-011")
    assert a11["events"] == {"fall": 0, "collision": 0}
    assert due(client, "TBT-VT2-011", "AC-CRASH")["status"] == "ok"
    r = client.post("/api/demo-logs/07_TBT-VT2-011_padenie.json/import").json()
    assert r["status"] == "imported"
    ev = r["summary"]["sensor_events"]
    assert len(ev) == 1 and ev[0]["kind"] == "fall" and ev[0]["peak_g"] >= 7
    alerts = {(x["entity"]["serial"], x["rule"]["code"]) for x in r["summary"]["alerts"]}
    assert ("TBT-VT2-011", "AC-CRASH") in alerts and ("IMU3-0204", "IMU-SHOCK") in alerts
    assert by_serial(client, "TBT-VT2-011")["events"]["fall"] == 1
    assert not by_serial(client, "TBT-VT2-011")["airworthy"]
    assert due(client, "TBT-VT2-011", "AC-CRASH")["status"] == "overdue"
    assert due(client, "IMU3-0204", "IMU-SHOCK")["status"] == "overdue"     # ИНС на борту в момент удара
    assert due(client, "IMU3-0201", "IMU-SHOCK")["status"] == "ok"          # узел со склада удара не получил
    card = client.get(f"/api/aircraft/{a11['id']}").json()
    assert card["sensor_events"][0]["kind_label"] == "Падение"
    # осмотр выполнен — напоминание закрыто
    rule = due(client, "TBT-VT2-011", "AC-CRASH")["rule"]["id"]
    client.post("/api/maintenance/records", json={"target": "aircraft", "entity_id": a11["id"], "rule_id": rule})
    assert due(client, "TBT-VT2-011", "AC-CRASH")["status"] == "ok"
    # откат импорта удаляет и событие
    client.post(f"/api/imports/{r['id']}/rollback")
    assert by_serial(client, "TBT-VT2-011")["events"]["fall"] == 0


def test_history_collision_already_inspected(client):
    a2 = by_serial(client, "TBT-M4-002")
    assert a2["events"]["collision"] == 1
    assert due(client, "TBT-M4-002", "AC-CRASH")["status"] == "ok"


def test_excel_movements_and_pdf_duplicate(client):
    r = client.post("/api/demo-logs/08_dvizhenie_uzlov.xlsx/import").json()
    assert r["status"] == "partial" and r["flights_new"] == 5 and r["flights_rejected"] == 1
    assert any("FC7-9999" in m for m in r["messages"])
    esc320 = by_serial(client, "ESC40-0320")
    assert esc320["location"]["aircraft_name"] == "Борт 11" and esc320["location"]["position"] == "ESC4"
    assert by_serial(client, "ESC40-0302")["status"] == "maintenance"
    card = client.get(f"/api/components/{esc320['id']}").json()
    assert [s["aircraft_name"] for s in card["stints"]][:2] == ["Борт 11", "Борт 12"]      # перестановка с сохранением истории
    pdf = client.post("/api/demo-logs/09_dvizhenie_uzlov.pdf/import").json()
    assert pdf["status"] == "duplicate" and pdf["flights_duplicate"] == 5
    assert client.post(f"/api/imports/{r['id']}/rollback").status_code == 400          # движение откатывается операциями


def test_pdf_movements_alone(client):
    r = client.post("/api/demo-logs/09_dvizhenie_uzlov.pdf/import").json()
    assert r["status"] == "imported" and r["flights_new"] == 5 and r["format"] == "pdf-движение"
    assert by_serial(client, "ESC40-0325")["location"]["aircraft_name"] == "Борт 12"


def test_excel_flights_with_collision_column(client):
    a12 = by_serial(client, "TBT-VT2-012")
    r = client.post("/api/demo-logs/10_polety_Bort12.xlsx/import").json()
    assert r["status"] == "imported" and r["flights_new"] == 3 and r["format"] == "xlsx"
    after = by_serial(client, "TBT-VT2-012")
    assert after["events"]["collision"] == 1 and after["hours"] > a12["hours"] + 3


def test_movement_template_and_bad_files(client):
    t = client.get("/api/templates/movements.xlsx")
    assert t.status_code == 200 and t.content[:2] == b"PK"
    r = client.post("/api/imports", files={"file": ("x.pdf", b"%PDF-1.4 broken", "application/pdf")}).json()
    assert r["status"] == "rejected"
    # шаблон сам по себе — корректный журнал движения
    r = client.post("/api/imports", data={"dry_run": "true"}, files={"file": ("t.xlsx", t.content, "application/octet-stream")}).json()
    assert r["format"] == "xlsx-движение" and r["dry_run"]


# ------------------------------------------------------------------ К1: миссии и автономный полёт
def _mission(client, name):
    return next(m for m in client.get("/api/missions").json() if m["name"].startswith(name))


def test_autonomous_mission_flight_accrues_hours(client):
    m = _mission(client, "Облёт ЛЭП")
    assert m["stats"]["waypoints"] == 4 and m["stats"]["distance_m"] > 500
    h0, comps0 = _snap(client, "TBT-M4-001")
    r = client.post(f"/api/missions/{m['id']}/run", json={"wind_ms": 4}).json()
    assert r["status"] == "completed"
    assert r["summary"]["max_xte_m"] < 10                          # автопилот держит линию пути при ветре
    track = r["track"]
    assert track[0][5] == "takeoff" and track[-1][5] == "land" and track[-1][3] < 0.5
    assert {p[5] for p in track} >= {"takeoff", "mission", "hold", "rtl", "land"}
    h1, comps1 = _snap(client, "TBT-M4-001")
    assert r["summary"]["hours_added"] > 0 and abs((h1 - h0) - r["summary"]["hours_added"]) < 0.011
    assert all(comps1[k] > comps0[k] for k in comps0 if comps0[k] is not None)   # наработка начислена всем установленным узлам
    imp = client.get("/api/imports").json()[0]
    assert imp["id"] == r["import_id"] and imp["flights_new"] == 1
    assert client.get(f"/api/missions/{m['id']}").json()["runs"][0]["status"] == "completed"


def test_local_mission_simulator_flies_short_legs_without_wind():
    """Локальная миссия (метры, в помещении): точка над точкой взлёта и точка в 2,5 м впереди. Прежде радиус принятия
    точки был 3 м, как по GPS: обе «достигались» сразу, и в симуляторе дрон взлетал и садился на месте; ветер 3 м/с
    при этом сносил его и в помещении."""
    from app.services import autopilot
    lat0, lon0 = 55.0, 37.0

    def wp(fwd, right, alt=1.5):
        lat, lon = autopilot.to_ll(lat0, lon0, right, fwd)
        return {"lat": lat, "lon": lon, "alt": alt, "hold_s": 0}
    m = {"home_lat": lat0, "home_lon": lon0, "takeoff_alt": 1.5, "speed_ms": 0.8, "return_home": True, "frame": "local",
         "waypoints": [wp(0, 0), wp(2.5, 0)]}
    sim = autopilot.simulate(m, wind_ms=3.0)
    fwd = [autopilot.to_xy(lat0, lon0, p[1], p[2])[1] for p in sim["track"]]
    assert sim["status"] == "completed" and max(fwd) > 1.8, max(fwd)          # долетел до точки 2
    assert abs(fwd[-1]) < 0.4 and sim["wind"]["speed_ms"] == 0.0               # вернулся и сел у точки взлёта
    g = autopilot.simulate(dict(m, frame="global", takeoff_alt=20, speed_ms=6,
                                waypoints=[wp(0, 0, 20), wp(200, 0, 20)]), wind_ms=3.0)
    assert g["status"] == "completed" and g["wind"]["speed_ms"] == 3.0         # по GPS — прежний радиус и ветер


def test_mission_fault_detects_fall_and_preflight_blocks(client):
    m = _mission(client, "Аэрофотосъёмка")
    r = client.post(f"/api/missions/{m['id']}/run", json={"fault_at_wp": 3}).json()
    assert r["status"] == "aborted"
    assert [e["kind"] for e in r["summary"]["sensor_events"]] == ["fall"]       # обнаружено по акселерометру
    assert {a["rule"]["code"] for a in r["summary"]["alerts"]} >= {"AC-CRASH", "IMU-SHOCK"}
    again = client.post(f"/api/missions/{m['id']}/run", json={})
    assert again.status_code == 400 and "не допущен" in again.json()["detail"]


def test_mission_crud_validation_and_export_roundtrip(client):
    assert client.post("/api/missions", json={"name": "x", "home_lat": 95, "home_lon": 0, "waypoints": []}).status_code == 422
    body = {"name": "Тест", "home_lat": 55.0, "home_lon": 37.0, "takeoff_alt": 20, "speed_ms": 6, "return_home": True,
            "waypoints": [{"lat": 55.001, "lon": 37.0, "alt": 25, "hold_s": 3}, {"lat": 55.001, "lon": 37.002, "alt": 30, "hold_s": 0}]}
    m = client.post("/api/missions", json=body).json()
    assert client.post(f"/api/missions/{m['id']}/run", json={}).status_code == 400    # борт не выбран
    for fmt in ("plan", "waypoints"):
        f = client.get(f"/api/missions/{m['id']}/export?fmt={fmt}")
        assert f.status_code == 200
        if fmt == "plan":
            plan = f.json()
            cmds = [i["command"] for i in plan["mission"]["items"]]
            assert cmds == [22, 16, 16, 20]                                   # TAKEOFF, WAYPOINT×2, RTL
        back = client.post("/api/missions/import", files={"file": (f"m.{fmt}", f.content)}).json()
        assert len(back["waypoints"]) == 2 and back["return_home"]
        assert abs(back["waypoints"][1]["lon"] - 37.002) < 1e-6 and back["waypoints"][0]["hold_s"] == 3
    bad = client.post("/api/missions/import", files={"file": ("x.plan", b"{oops")})
    assert bad.status_code == 400
    assert client.delete(f"/api/missions/{m['id']}").status_code == 200


# ------------------------------------------------------------------ К7: уведомления, К10: доска перестановки
def test_notifications_three_categories(client):
    d = client.get("/api/notifications").json()
    keys = lambda cat: {(i["entity"]["serial"], i["rule"]["code"]) for i in d["items"][cat]}  # noqa: E731
    assert ("ESC40-0315", "RESOURCE") in keys("limit")             # индивидуальный ресурс исчерпан
    assert ("BVK2-0405", "BVK-SVC") in keys("overdue")              # периодическая работа просрочена
    assert ("ESC40-0318", "ESC-LIFE") in keys("approaching")        # приближение к назначенному ресурсу
    assert all(i["rule"]["kind"] == "life" for i in d["items"]["limit"])
    assert d["counts"]["total"] == sum(len(v) for v in d["items"].values())
    assert any(e["kind"] == "collision" for e in d["sensor_events"])
    # падение из журнала → новое просроченное уведомление (осмотр после падения)
    client.post("/api/demo-logs/07_TBT-VT2-011_padenie.json/import")
    d2 = client.get("/api/notifications").json()
    assert ("TBT-VT2-011", "AC-CRASH") in {(i["entity"]["serial"], i["rule"]["code"]) for i in d2["items"]["overdue"]}
    assert d2["counts"]["sensor_events"] == d["counts"]["sensor_events"] + 1


def test_board_and_move_recalculates_passport(client):
    b = client.get("/api/board").json()
    a1 = next(a for a in b["aircraft"] if a["serial"] == "TBT-M4-001")
    a2 = next(a for a in b["aircraft"] if a["serial"] == "TBT-M4-002")
    slot = next(s for s in a1["slots"] if s["position"] == "ESC1")
    comp = slot["component"]
    assert comp["residual"]["hours"] is not None and comp["uid"]
    spare = {c["serial"] for c in b["spare"]}
    assert "ESC40-0324" in spare and "ESC40-0315" in spare
    # обмен ESC1 между бортами, как делает доска: снять → переставить → установить; дата в прошлом
    other = next(s for s in a2["slots"] if s["position"] == "ESC1")["component"]
    fl = client.get(f"/api/aircraft/{a1['id']}").json()["flight_log"]                   # последние полёты Борт 01
    t0 = datetime.fromisoformat(fl[2]["started_at"].replace("Z", "+00:00")) - timedelta(minutes=5)
    moved = sum(f["duration_h"] for f in fl[:3])
    at, at2, at3 = (t0.isoformat(), (t0 + timedelta(minutes=1)).isoformat(), (t0 + timedelta(minutes=2)).isoformat())
    before = client.get(f"/api/components/{comp['id']}").json()
    assert client.post(f"/api/components/{other['id']}/remove", json={"at": at, "to_status": "stock"}).status_code == 200
    assert client.post(f"/api/components/{comp['id']}/transfer", json={"aircraft_id": a2["id"], "position": "ESC1", "at": at2}).status_code == 200
    assert client.post(f"/api/components/{other['id']}/install", json={"aircraft_id": a1["id"], "position": "ESC1", "at": at3}).status_code == 200
    after = client.get(f"/api/components/{comp['id']}").json()
    assert after["location"]["aircraft_id"] == a2["id"]
    assert after["uid"] == before["uid"]
    # полёты Борт 01 за последние 3 дня больше не начисляются этому узлу, зато начисляются полёты Борт 02
    fl2 = [f for f in client.get(f"/api/aircraft/{a2['id']}").json()["flight_log"]
           if datetime.fromisoformat(f["started_at"].replace("Z", "+00:00")) >= t0 + timedelta(minutes=1)]
    expected = before["hours"] - moved + sum(f["duration_h"] for f in fl2)
    assert moved > 0 and abs(after["hours"] - expected) < 0.02                         # налёт переотнесён по дате операции
    assert [s["aircraft_name"] for s in after["stints"]][:2] == ["Борт 02", "Борт 01"]
    b2 = client.get("/api/board").json()
    assert next(s for s in next(a for a in b2["aircraft"] if a["id"] == a1["id"])["slots"] if s["position"] == "ESC1")["component"]["id"] == other["id"]
    # несовместимая позиция отклоняется
    fc = next(s for s in a1["slots"] if s["position"] == "FC")["component"]
    assert client.post(f"/api/components/{fc['id']}/transfer", json={"aircraft_id": a2["id"], "position": "ESC2"}).status_code == 400


def test_component_passport_uid_specs_residual(client):
    c = client.get(f"/api/components/{by_serial(client, 'FC7-0106')['id']}").json()
    assert len(c["uid"]) == 32 and c["serial"] == "FC7-0106" and c["in_service_at"] and c["model"]
    assert any(s["name"] == "Процессор" for s in c["specs"])
    assert c["residual"]["hours"] == round(2000 - c["hours"], 2) or abs(c["residual"]["hours"] - (2000 - c["hours"])) < 0.01
    # новый узел получает типовой набор характеристик и свой UID
    tid = c["type"]["id"]
    n1 = client.post("/api/components", json={"serial": "FC7-NEW1", "type_id": tid}).json()
    n2 = client.post("/api/components", json={"serial": "FC7-NEW2", "type_id": tid,
                                               "specs": [{"name": "Процессор", "value": "STM32H7"}]}).json()
    assert n1["uid"] != n2["uid"]
    assert client.get(f"/api/components/{n2['id']}").json()["specs"] == [{"name": "Процессор", "value": "STM32H7"}]
    # характеристики редактируются, реквизиты UID и серийный номер — нет
    r = client.patch(f"/api/components/{n1['id']}", json={"specs": [{"name": "Масса", "value": "40 г"}], "uid": "x", "serial": "HACK"})
    assert r.status_code in (200, 422)
    after = client.get(f"/api/components/{n1['id']}").json()
    assert after["uid"] == n1["uid"] and after["serial"] == "FC7-NEW1"
