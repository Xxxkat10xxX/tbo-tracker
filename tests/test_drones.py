"""Связь с бортами: настоящий сервер (uvicorn) ↔ настоящий агент tbo-agent по HTTP с симулятором борта.

План → проверки на борту → «готов» → старт → полёт → журнал → автоимпорт (наработка, датчики) и прерывание
возвратом домой; разделение прав (наблюдатель, токен устройства, сессия пользователя, чужой полёт).
"""
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import Flight, SensorEvent
from app.services import auth
from tests.conftest import login

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))
from tbo_agent.agent import Agent  # noqa: E402
from tbo_agent.client import Api  # noqa: E402
from tbo_agent.vehicle_sim import SimVehicle  # noqa: E402

HOME = (54.9512, 38.2034)


def wait(cond, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        v = cond()
        if v:
            return v
        time.sleep(0.1)
    return None


@pytest.fixture()
def live(client):
    import uvicorn
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off",
                                           timeout_graceful_shutdown=1))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    assert wait(lambda: server.started, 10)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    th.join(5)


class Cfg:
    def __init__(self, url, state_dir):
        self.url, self.state_dir, self.ready_timeout_s = url, state_dir, 600
        self.limits = {"max_alt_m": 120, "max_dist_m": 1000, "max_home_offset_m": 30, "min_battery_pct": 50, "min_sats": 8,
                       "cells": 6, "min_cell_v": 3.75}


def start_agent(url, token, state_dir, **sim):
    v = SimVehicle(time_scale=80, **sim)
    ag = Agent(Cfg(url, str(state_dir)), v, Api(url, token, timeout=10))
    ag.POLL_WAIT_S = 1
    threading.Thread(target=ag.run, daemon=True).start()
    return ag, v


def mission(client, aircraft_id, n=2):
    wps = [{"lat": HOME[0] + 0.0012, "lon": HOME[1], "alt": 25, "hold_s": 0},
           {"lat": HOME[0] + 0.0012, "lon": HOME[1] + 0.0018, "alt": 30, "hold_s": 2}][:n]
    r = client.post("/api/missions", json={"name": "Облёт опоры", "aircraft_id": aircraft_id, "home_lat": HOME[0],
                                           "home_lon": HOME[1], "takeoff_alt": 20, "speed_ms": 8, "return_home": True,
                                           "waypoints": wps})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def link_for(client, serial="TBT-M4-001"):
    a = next(x for x in client.get("/api/aircraft").json() if x["serial"] == serial)
    r = client.post("/api/drones", json={"aircraft_id": a["id"]})
    assert r.status_code == 201, r.text
    return a, r.json()["id"], r.json()["token"]


def get(client, lid):
    return client.get(f"/api/drones/{lid}").json()


def test_real_flight_via_agent_is_imported(client, live, tmp_path):
    a, lid, token = link_for(client)
    mid = mission(client, a["id"])
    ag, v = start_agent(live, token, tmp_path / "ag", fault="collision@2")
    try:
        assert wait(lambda: get(client, lid)["state"] == "idle")
        d = get(client, lid)
        assert d["online"] and d["agent"]["executor"] == "sim" and d["token_hint"].startswith("…")
        r = client.post(f"/api/drones/{lid}/prepare", json={"mission_id": mid})
        assert r.status_code == 200, r.text
        d = wait(lambda: (lambda x: x if x["state"] == "ready" and x["run"]["status"] == "ready" else None)(get(client, lid)), 20)
        assert d, get(client, lid)
        assert all(c["ok"] for c in d["checks"]) and any("План загружен" in c["name"] for c in d["checks"])
        run_id, plan_hash = d["run"]["id"], d["run"]["plan_hash"]
        assert client.post(f"/api/drones/{lid}/start", json={"run_id": run_id, "plan_hash": "0" * 64, "confirm": True}).status_code == 400
        assert client.post(f"/api/drones/{lid}/start", json={"run_id": run_id, "plan_hash": plan_hash}).status_code == 422
        r = client.post(f"/api/drones/{lid}/start", json={"run_id": run_id, "plan_hash": plan_hash, "confirm": True})
        assert r.status_code == 200, r.text
        run = wait(lambda: (lambda m: next((x for x in m["runs"] if x["id"] == run_id and x["status"] in
                                            ("completed", "aborted", "failed")), None))(client.get(f"/api/missions/{mid}").json()), 40)
        assert run and run["status"] == "completed" and run["source"] == "drone" and run["import_id"], run
        sm = run["summary"]
        assert sm["wp_reached"] == 2 and sm["hours_added"] > 0 and sm["executor"] == "sim" and sm["started_by"] == "admin"
        with SessionLocal() as s:
            f = s.query(Flight).filter_by(external_id=f"{a['serial']}-R{run_id}").one()
            assert f.aircraft_id == a["id"] and 60 < (f.ended_at - f.started_at).total_seconds() < 300
            assert s.query(SensorEvent).filter_by(flight_id=f.id, kind="collision").count() == 1     # удар у точки 2
        assert wait(lambda: get(client, lid)["state"] == "idle" and get(client, lid)["run"] is None, 10)
        kinds = {(c["kind"], c["status"]) for c in get(client, lid)["commands"]}
        assert {("prepare", "done"), ("start", "done")} <= kinds
        assert any(x["action"] == "drone_start" for x in client.get("/api/audit?limit=50").json())
    finally:
        ag.stop()
        v.close()


def test_rtl_aborts_flight_and_access_rules(client, live, tmp_path):
    a, lid, token = link_for(client)
    mid = mission(client, a["id"])
    ag, v = start_agent(live, token, tmp_path / "ag")
    try:
        assert wait(lambda: get(client, lid)["state"] == "idle")
        # наблюдатель видит, но не командует; токен устройства не открывает API пользователя и наоборот
        with SessionLocal() as s:
            auth.create_user(s, "watcher", "Смотрю-за-полётом-2026", "viewer", "Наблюдатель")
            s.commit()
        with TestClient(app) as viewer:
            login(viewer, "watcher", "Смотрю-за-полётом-2026")
            assert viewer.get("/api/drones").status_code == 200
            assert viewer.post(f"/api/drones/{lid}/prepare", json={"mission_id": mid}).status_code == 403
            assert viewer.post("/api/drones", json={"aircraft_id": a["id"]}).status_code == 403
        with TestClient(app) as dev:
            dev.headers["Authorization"] = "Bearer " + token
            assert dev.get("/api/drones").status_code == 401
            assert dev.post("/api/agent/state", json={"state": "idle"}).status_code == 200
        assert client.post("/api/agent/state", json={"state": "idle"}).status_code == 401       # сессия ≠ устройство
        r = client.post(f"/api/drones/{lid}/prepare", json={"mission_id": mid})
        assert r.status_code == 200, r.text
        d = wait(lambda: (lambda x: x if x["run"] and x["run"]["status"] == "ready" else None)(get(client, lid)), 20)
        client.post(f"/api/drones/{lid}/start", json={"run_id": d["run"]["id"], "plan_hash": d["run"]["plan_hash"], "confirm": True})
        assert wait(lambda: v.snapshot()["alt_rel"] > 15, 20)
        r = client.post(f"/api/drones/{lid}/rtl")
        assert r.status_code == 200, r.text
        run = wait(lambda: (lambda m: next((x for x in m["runs"] if x["status"] in ("completed", "aborted")), None))(
            client.get(f"/api/missions/{mid}").json()), 40)
        assert run and run["status"] == "aborted" and run["summary"]["abort"]["kind"] == "rtl" and run["import_id"]
        # журнал чужого полёта другим токеном не принимается; отключённое подключение не работает
        b, lid2, token2 = link_for(client, "TBT-M4-002")
        with TestClient(app) as other:
            other.headers["Authorization"] = "Bearer " + token2
            body = {"status": "completed", "flight": None, "track": [], "summary": {}}
            assert other.post(f"/api/agent/runs/{run['id']}/log", json=body).status_code == 404
            client.patch(f"/api/drones/{lid2}", json={"enabled": False})
            assert other.post("/api/agent/hello", json={}).status_code == 403
        assert client.post(f"/api/drones/{lid}/rtl").status_code == 400          # уже не в полёте
    finally:
        ag.stop()
        v.close()


def test_prepare_refused_for_unairworthy_or_out_of_limits(client):
    a, lid, token = link_for(client)
    with TestClient(app) as dev:
        dev.headers["Authorization"] = "Bearer " + token
        dev.post("/api/agent/state", json={"state": "idle", "telemetry": {"lat": HOME[0], "lon": HOME[1]}})
        far = client.post("/api/missions", json={"name": "Далеко", "aircraft_id": a["id"], "home_lat": HOME[0], "home_lon": HOME[1],
                                                 "takeoff_alt": 20, "speed_ms": 8, "return_home": True,
                                                 "waypoints": [{"lat": HOME[0] + 0.05, "lon": HOME[1], "alt": 30, "hold_s": 0}]}).json()
        r = client.post(f"/api/drones/{lid}/prepare", json={"mission_id": far["id"]})
        assert r.status_code == 400 and "TBO_DRONE_MAX_DIST_M" in r.json()["detail"]
        high = client.post("/api/missions", json={"name": "Высоко", "aircraft_id": a["id"], "home_lat": HOME[0], "home_lon": HOME[1],
                                                  "takeoff_alt": 20, "speed_ms": 8, "return_home": True,
                                                  "waypoints": [{"lat": HOME[0] + 0.001, "lon": HOME[1], "alt": 200, "hold_s": 0}]}).json()
        assert "TBO_DRONE_MAX_ALT_M" in client.post(f"/api/drones/{lid}/prepare", json={"mission_id": high["id"]}).json()["detail"]
        client.patch(f"/api/aircraft/{a['id']}", json={"status": "maintenance"})
        ok = mission(client, a["id"])
        r = client.post(f"/api/drones/{lid}/prepare", json={"mission_id": ok})
        assert r.status_code == 400 and "не допущен" in r.json()["detail"]
        client.patch(f"/api/aircraft/{a['id']}", json={"status": "active"})
        other = next(x for x in client.get("/api/aircraft").json() if x["serial"] == "TBT-M4-002")
        foreign = mission(client, other["id"])
        assert "другому борту" in client.post(f"/api/drones/{lid}/prepare", json={"mission_id": foreign}).json()["detail"]
        assert client.post("/api/drones", json={"aircraft_id": a["id"]}).status_code == 409          # одно подключение на борт
        # агент не на связи — команды не принимаются; старая подготовка снимается по сроку команды
        r = client.post(f"/api/drones/{lid}/prepare", json={"mission_id": ok})
        assert r.status_code == 200, r.text
        assert client.post(f"/api/drones/{lid}/cancel").status_code == 200
        assert get(client, lid)["run"] is None
