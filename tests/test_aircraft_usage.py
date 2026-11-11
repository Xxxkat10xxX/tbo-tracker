"""Ручное начисление борту часов и циклов (износа) без полёта; при желании — и узлам, стоявшим на борту."""
from datetime import timedelta

from app.timeutil import iso, utcnow


def _board(client):
    a = next(x for x in client.get("/api/aircraft").json() if x["status"] == "active" and x["slots_filled"])
    return client.get(f"/api/aircraft/{a['id']}").json()


def test_aircraft_accrual_to_board_and_components(client):
    a = _board(client)
    comp = next(r["component"] for r in a["configuration"] if r["component"])
    c0 = client.get(f"/api/components/{comp['id']}").json()
    r = client.post(f"/api/aircraft/{a['id']}/usage", json={"hours": 12.5, "cycles": 7, "kind": "correction",
                                                            "note": "перенос из формуляра", "to_components": True})
    assert r.status_code == 201, r.text
    assert "узлам на борту" in r.json()["message"] and r.json()["entry"]["components"] >= 1
    eid = r.json()["entry"]["id"]
    a1 = client.get(f"/api/aircraft/{a['id']}").json()
    assert a1["hours"] == round(a["hours"] + 12.5, 2) and a1["cycles"] == a["cycles"] + 7
    assert a1["flights"] == a["flights"]                                    # полётов не прибавилось
    row = next(f for f in a1["flight_log"] if f["source"] == "aircraft_entry")
    assert row["id"] == eid and row["duration_h"] == 12.5 and row["cycles"] == 7 and "формуляр" in row["mission"]
    c1 = client.get(f"/api/components/{comp['id']}").json()
    assert c1["hours"] == round(c0["hours"] + 12.5, 2) and c1["cycles"] == c0["cycles"] + 7
    linked = next(f for f in c1["flight_log"] if f["source"] == "entry" and f.get("linked"))
    bad = client.delete(f"/api/components/{comp['id']}/usage/{linked['id']}")   # снимается только с борта целиком
    assert bad.status_code == 400 and "карточке борта" in bad.json()["detail"]
    r = client.delete(f"/api/aircraft/{a['id']}/usage/{eid}")
    assert r.status_code == 200 and "снято и с" in r.json()["message"]
    a2 = client.get(f"/api/aircraft/{a['id']}").json()
    c2 = client.get(f"/api/components/{comp['id']}").json()
    assert (a2["hours"], a2["cycles"]) == (a["hours"], a["cycles"]) and (c2["hours"], c2["cycles"]) == (c0["hours"], c0["cycles"])


def test_aircraft_accrual_board_only_and_validation(client):
    a = _board(client)
    comp = next(r["component"] for r in a["configuration"] if r["component"])
    c0 = client.get(f"/api/components/{comp['id']}").json()
    r = client.post(f"/api/aircraft/{a['id']}/usage", json={"hours": 0, "cycles": 3, "kind": "ground"})
    assert r.status_code == 201 and "узлам — нет" in r.json()["message"]
    assert client.get(f"/api/aircraft/{a['id']}").json()["cycles"] == a["cycles"] + 3              # износ без часов
    assert client.get(f"/api/components/{comp['id']}").json()["cycles"] == c0["cycles"]          # узлам — нет
    assert client.post(f"/api/aircraft/{a['id']}/usage", json={"hours": 0, "cycles": 0}).status_code == 400
    future = iso(utcnow() + timedelta(days=2))
    assert client.post(f"/api/aircraft/{a['id']}/usage", json={"hours": 1, "started_at": future}).status_code == 400
    assert client.post(f"/api/aircraft/{a['id']}/usage", json={"hours": 1, "kind": "bench"}).status_code == 422
    assert client.delete(f"/api/aircraft/{a['id'] + 999}/usage/1").status_code == 404
