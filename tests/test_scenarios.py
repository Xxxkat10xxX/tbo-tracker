"""Автотесты обязательных сценариев приёмки (раздел 3 ТЗ)."""
from datetime import datetime, timedelta, timezone

from tests.conftest import by_serial, due


def snapshot(client, serial):
    a = client.get(f"/api/aircraft/{by_serial(client, serial)['id']}").json()
    comps = {s["component"]["serial"]: (s["component"]["hours"], s["component"]["cycles"])
             for s in a["configuration"] if s["component"]}
    return (a["hours"], a["cycles"]), comps


def import_demo(client, name):
    r = client.post(f"/api/demo-logs/{name}/import")
    assert r.status_code == 200, r.text
    return r.json()


def test_1_log_import_accrues_to_aircraft_and_all_installed_components(client):
    (h0, c0), comps0 = snapshot(client, "TBT-M4-001")
    r = import_demo(client, "01_TBT-M4-001_smena.json")
    assert r["status"] == "imported" and r["flights_new"] == 8 and r["cycles_added"] == 8
    (h1, c1), comps1 = snapshot(client, "TBT-M4-001")
    assert round(h1 - h0, 2) == 3.52 and c1 - c0 == 8
    assert len(comps1) == 7
    for serial, (h, c) in comps1.items():
        assert abs(h - comps0[serial][0] - 3.5167) < 0.011 and c - comps0[serial][1] == 8
    # другие борта не затронуты
    assert by_serial(client, "TBT-M4-002")["hours"] == snapshot(client, "TBT-M4-002")[0][0]


def test_2_transfer_keeps_history_and_accrues_on_new_aircraft(client):
    fc = by_serial(client, "FC7-0102")
    a2, a3 = by_serial(client, "TBT-M4-002"), by_serial(client, "TBT-M4-003")
    before = client.get(f"/api/components/{fc['id']}").json()
    a2_hours = a2["hours"]
    at = (datetime.now(timezone.utc) - timedelta(days=2)).replace(hour=12, minute=0).isoformat()
    r = client.post(f"/api/components/{fc['id']}/transfer", json={"aircraft_id": a3["id"], "position": "FC", "at": at})
    assert r.status_code == 200, r.text
    mid = client.get(f"/api/components/{fc['id']}").json()
    assert mid["hours"] == before["hours"] and mid["location"]["aircraft_id"] == a3["id"]
    assert due(client, "FC7-0102", "FC-CHK")["status"] == "due_soon"   # интервал не обнулился
    imp = import_demo(client, "02_TBT-M4-003_posle_perestanovki.csv")
    assert imp["flights_new"] == 6
    after = client.get(f"/api/components/{fc['id']}").json()
    assert abs(after["hours"] - before["hours"] - 4.85) < 0.011
    assert after["on_board_hours"] == 4.85
    assert [s["aircraft_name"] for s in after["stints"]] == ["Борт 03", "Борт 02"]
    assert due(client, "FC7-0102", "FC-CHK")["status"] == "overdue"
    # старый борт новую наработку не получил, его FC-позиция пуста
    assert by_serial(client, "TBT-M4-002")["hours"] == a2_hours
    cfg = client.get(f"/api/aircraft/{a2['id']}").json()["configuration"]
    assert next(s for s in cfg if s["position"] == "FC")["component"] is None
    # аналитика видит предотвращённый пропуск
    prevented = client.get("/api/analytics").json()["prevented"]
    assert any(p["entity"]["serial"] == "FC7-0102" for p in prevented)


def test_2b_install_validations(client):
    fc = by_serial(client, "FC7-0102")
    imu = by_serial(client, "IMU3-0201")  # в резерве
    a1 = by_serial(client, "TBT-M4-001")
    # занятая позиция
    r = client.post(f"/api/components/{imu['id']}/install", json={"aircraft_id": a1["id"], "position": "IMU"})
    assert r.status_code == 400 and "занята" in r.json()["detail"]
    # несовместимая позиция
    r = client.post(f"/api/components/{imu['id']}/install", json={"aircraft_id": a1["id"], "position": "FC"})
    assert r.status_code == 400
    # узел в ремонте нельзя установить
    broken = by_serial(client, "FC7-0103")
    r = client.post(f"/api/components/{broken['id']}/install", json={"aircraft_id": by_serial(client, 'TBT-M4-003')['id'], "position": "FC"})
    assert r.status_code == 400 and "обслуживан" in r.json()["detail"]
    # установленный узел — только перестановкой
    r = client.post(f"/api/components/{fc['id']}/install", json={"aircraft_id": a1["id"], "position": "FC"})
    assert r.status_code == 400


def test_3_reimport_does_not_double_count(client):
    import_demo(client, "01_TBT-M4-001_smena.json")
    snap = snapshot(client, "TBT-M4-001")
    for name in ("01_TBT-M4-001_smena.json", "03_TBT-M4-001_smena_POVTOR.json"):
        r = import_demo(client, name)
        assert r["status"] == "duplicate" and r["flights_new"] == 0
        assert snapshot(client, "TBT-M4-001") == snap
    r = import_demo(client, "04_TBT-M4-001_bortovoi_registrator.csv")
    assert r["status"] == "partial" and r["flights_new"] == 2 and r["flights_duplicate"] == 8
    (h, c), _ = snapshot(client, "TBT-M4-001")
    assert c == snap[0][1] + 2


def test_3b_rollback_and_reimport(client):
    (h0, c0), _ = snapshot(client, "TBT-M4-001")
    r = import_demo(client, "01_TBT-M4-001_smena.json")
    client.post(f"/api/imports/{r['id']}/rollback")
    assert snapshot(client, "TBT-M4-001")[0] == (h0, c0)
    assert import_demo(client, "01_TBT-M4-001_smena.json")["status"] == "imported"


def test_3c_dry_run_does_not_write(client):
    snap = snapshot(client, "TBT-M4-001")
    r = client.post("/api/demo-logs/01_TBT-M4-001_smena.json/import", params={"dry_run": True}).json()
    assert r["dry_run"] and r["flights_new"] == 8
    assert snapshot(client, "TBT-M4-001") == snap
    assert import_demo(client, "01_TBT-M4-001_smena.json")["status"] == "imported"


def test_3d_invalid_rows_rejected(client):
    r = import_demo(client, "06_s_oshibkami.csv")
    assert r["status"] == "partial" and r["flights_new"] == 1 and r["flights_rejected"] == 3


def test_4_reminders_hours_cycles_calendar(client):
    assert due(client, "IMU3-0206", "IMU-CAL")["status"] == "due_soon"
    assert due(client, "ESC40-0301", "ESC-DIAG")["status"] == "due_soon"
    assert due(client, "TBT-VT2-011", "AC-ANNUAL")["status"] == "due_soon"   # календарь
    assert due(client, "BVK2-0405", "BVK-SVC")["status"] == "overdue"        # календарь
    assert due(client, "ESC40-0318", "ESC-LIFE")["status"] == "due_soon"     # назначенный ресурс
    r = import_demo(client, "01_TBT-M4-001_smena.json")
    alerts = {(a["entity"]["serial"], a["rule"]["code"]): a["status"] for a in r["summary"]["alerts"]}
    assert alerts[("IMU3-0206", "IMU-CAL")] == "overdue"     # по часам
    assert alerts[("ESC40-0301", "ESC-DIAG")] == "overdue"   # по циклам
    assert due(client, "ESC40-0301", "ESC-DIAG")["driver"]["metric"] == "cycles"


def test_5_perform_maintenance_resets_interval_not_total(client):
    bvk = by_serial(client, "BVK2-0405")
    a12 = by_serial(client, "TBT-VT2-012")
    assert not a12["airworthy"]
    item = due(client, "BVK2-0405", "BVK-SVC")
    r = client.post("/api/maintenance/records", json={"target": "component", "entity_id": bvk["id"],
                                                      "rule_id": item["rule"]["id"], "performed_by": "Тест"})
    assert r.status_code == 201, r.text
    assert due(client, "BVK2-0405", "BVK-SVC")["status"] == "ok"
    assert by_serial(client, "BVK2-0405")["hours"] == bvk["hours"]
    assert by_serial(client, "TBT-VT2-012")["airworthy"]
    # назначенный ресурс выполнением работы не «обнуляется»
    life = due(client, "ESC40-0318", "ESC-LIFE")
    r = client.post("/api/maintenance/records", json={"target": "component", "entity_id": life["entity"]["id"],
                                                      "rule_id": life["rule"]["id"]})
    assert r.status_code == 400


def test_5b_component_status_update_after_repair(client):
    c = by_serial(client, "FC7-0103")
    assert c["status"] == "maintenance"
    r = client.post("/api/maintenance/records", json={"target": "component", "entity_id": c["id"], "work_type": "repair",
                                                      "description": "Замена стабилизатора", "new_status": "stock"})
    assert r.status_code == 201
    assert by_serial(client, "FC7-0103")["status"] == "stock"


def test_late_import_uses_historical_configuration(client):
    """Журнал, загруженный после перестановки, но с полётами ДО неё, начисляется старому составу."""
    fc = by_serial(client, "FC7-0106")  # стоит на Борт 01
    a2 = by_serial(client, "TBT-M4-002")
    # снимаем FC с Борт 02, переносим FC7-0106 на Борт 02 сегодня
    client.post(f"/api/components/{by_serial(client, 'FC7-0102')['id']}/remove", json={"to_status": "stock"})
    before = by_serial(client, "FC7-0106")["hours"]
    r = client.post(f"/api/components/{fc['id']}/transfer", json={"aircraft_id": a2["id"], "position": "FC"})
    assert r.status_code == 200, r.text
    import_demo(client, "01_TBT-M4-001_smena.json")  # полёты вчера на Борт 01 — узел тогда стоял там
    assert abs(by_serial(client, "FC7-0106")["hours"] - before - 3.5167) < 0.011
