"""Связь с бортами: настоящий сервер (uvicorn) ↔ настоящий агент tbo-agent по HTTP с симулятором борта.

План → проверки на борту → «готов» → старт → полёт → журнал → автоимпорт (наработка, датчики) и прерывание
возвратом домой; разделение прав (наблюдатель, токен устройства, сессия пользователя, чужой полёт).
"""
import json
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
    ag, v = start_agent(live, token, tmp_path / "ag", fault="collision@2", touchdown_g=5.5)   # жёсткое касание
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
            # удар у точки 2; касание земли 5,5 g при посадке — не столкновение
            assert s.query(SensorEvent).filter_by(flight_id=f.id, kind="collision").count() == 1     # удар у точки 2
            assert s.query(SensorEvent).filter_by(flight_id=f.id).count() == 1
        sent = tmp_path / "ag" / "sent" / f"run-{run_id}.json"
        assert wait(sent.exists, 10)
        assert max(json.loads(sent.read_text("utf-8"))["flight"]["imu"]["accel_g"][-250:]) >= 5   # касание записано
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


def local_mission(client, aircraft_id, **kw):
    from tbo_agent.offboard import body_to_ll
    p1, p2 = body_to_ll(HOME[0], HOME[1], 3.0, 0.0), body_to_ll(HOME[0], HOME[1], 3.0, -2.0)    # 3 м вперёд, затем 2 м вправо
    body = {"name": "Облёт стеллажа", "aircraft_id": aircraft_id, "home_lat": HOME[0], "home_lon": HOME[1], "frame": "local",
            "takeoff_alt": 1.5, "speed_ms": 0.8, "return_home": True,
            "waypoints": [{"lat": p1[0], "lon": p1[1], "alt": 1.5, "hold_s": 0}, {"lat": p2[0], "lon": p2[1], "alt": 2.0, "hold_s": 1}]}
    body.update(kw)
    return client.post("/api/missions", json=body)


def test_local_mission_rules(client):
    a = next(x for x in client.get("/api/aircraft").json() if x["serial"] == "TBT-M4-001")
    r = local_mission(client, a["id"])
    assert r.status_code == 201 and r.json()["frame"] == "local"
    mid = r.json()["id"]
    assert "QGroundControl" in client.get(f"/api/missions/{mid}/export").json()["detail"]          # только GPS-координаты
    assert local_mission(client, a["id"], speed_ms=3.5).status_code == 400                        # Optical Flow: до 3 м/с
    glob = local_mission(client, a["id"], frame="global")
    assert glob.status_code == 400 and "от 2" in glob.json()["detail"]                            # по GPS точки не ниже 2 м
    # реальный полёт строже: до 3 м и 2 м/с (TBO_DRONE_LOCAL_*)
    _, lid, token = link_for(client)
    with TestClient(app) as dev:
        dev.headers["Authorization"] = "Bearer " + token
        dev.post("/api/agent/state", json={"state": "idle"})
        high = local_mission(client, a["id"], takeoff_alt=4.0).json()
        assert "TBO_DRONE_LOCAL_MAX_ALT_M" in client.post(f"/api/drones/{lid}/prepare", json={"mission_id": high["id"]}).json()["detail"]
        fast = local_mission(client, a["id"], speed_ms=2.5).json()
        assert "2 м/с" in client.post(f"/api/drones/{lid}/prepare", json={"mission_id": fast["id"]}).json()["detail"]
        assert client.post(f"/api/drones/{lid}/prepare", json={"mission_id": mid}).status_code == 200
        cmd = dev.get("/api/agent/commands?wait=0").json()["command"]
        plan = cmd["payload"]["plan"]
        assert plan["frame"] == "local" and plan["limits"]["max_alt_m"] == 3 and plan["limits"]["max_speed_ms"] == 2.0


def test_mission_heading_plan_and_old_agent(client):
    """Курс дрона уходит в план («по линии маршрута», курс в точке). Агенту без поддержки курса такой план не
    отправляется — он молча летел бы без курса; миссия без курса — тот же план, что прежде, для агента любой версии."""
    a = next(x for x in client.get("/api/aircraft").json() if x["serial"] == "TBT-M4-001")
    plain = local_mission(client, a["id"]).json()
    wps = [dict(w) for w in plain["waypoints"]]
    wps[1]["yaw_deg"] = 90
    yaw = local_mission(client, a["id"], yaw_mode="route", waypoints=wps).json()
    assert yaw["yaw_mode"] == "route" and yaw["waypoints"][1]["yaw_deg"] == 90 and yaw["waypoints"][0]["yaw_deg"] is None
    assert local_mission(client, a["id"], waypoints=[dict(wps[0], yaw_deg=400)]).status_code == 422

    def next_plan(dev):
        for _ in range(5):
            c = dev.get("/api/agent/commands?wait=0").json()["command"]
            if c and c["kind"] == "prepare":
                return c["payload"]["plan"]
    _, lid, token = link_for(client)
    with TestClient(app) as dev:
        dev.headers["Authorization"] = "Bearer " + token
        dev.post("/api/agent/hello", json={"agent_version": "1.0.0", "executor": "offboard"})      # прежний агент
        dev.post("/api/agent/state", json={"state": "idle"})
        r = client.post(f"/api/drones/{lid}/prepare", json={"mission_id": yaw["id"]})
        assert r.status_code == 400 and "не поддерживает курс" in r.json()["detail"] and "1.0.0" in r.json()["detail"]
        assert client.post(f"/api/drones/{lid}/prepare", json={"mission_id": plain["id"]}).status_code == 200
        plan = next_plan(dev)
        assert "yaw_mode" not in plan and all("yaw_deg" not in w for w in plan["waypoints"])         # как у прежних версий
        assert client.post(f"/api/drones/{lid}/cancel").status_code == 200
        dev.post("/api/agent/hello", json={"agent_version": "1.1.0", "executor": "offboard", "features": ["yaw"]})
        assert get(client, lid)["agent"]["features"] == ["yaw"]
        assert client.post(f"/api/drones/{lid}/prepare", json={"mission_id": yaw["id"]}).status_code == 200
        plan = next_plan(dev)
        assert plan["yaw_mode"] == "route" and "yaw_deg" not in plan["waypoints"][0] and plan["waypoints"][1]["yaw_deg"] == 90.0


def test_local_flight_via_agent(client, live, tmp_path):
    a, lid, token = link_for(client)
    mid = local_mission(client, a["id"], yaw_mode="route").json()["id"]          # нос по линии маршрута
    ag, v = start_agent(live, token, tmp_path / "ag")
    try:
        assert wait(lambda: get(client, lid)["state"] == "idle")
        assert "yaw" in get(client, lid)["agent"]["features"]                    # агент сообщил, что умеет курс
        assert client.post(f"/api/drones/{lid}/prepare", json={"mission_id": mid}).status_code == 200
        d = wait(lambda: (lambda x: x if x["run"] and x["run"]["status"] == "ready" else None)(get(client, lid)), 20)
        assert d and any(c["name"].startswith("Локальная позиция") and c["ok"] for c in d["checks"])
        assert not any(c["name"] == "GPS" for c in d["checks"])                                     # без GPS-проверок
        client.post(f"/api/drones/{lid}/start", json={"run_id": d["run"]["id"], "plan_hash": d["run"]["plan_hash"], "confirm": True})
        run = wait(lambda: (lambda m: next((x for x in m["runs"] if x["status"] in ("completed", "aborted", "failed")), None))(
            client.get(f"/api/missions/{mid}").json()), 40)
        assert run and run["status"] == "completed" and run["summary"]["wp_reached"] == 2 and run["import_id"]
        assert run["summary"]["max_alt_m"] <= 2.1                       # в помещении домой — на высоте точки, без набора 5 м
    finally:
        ag.stop()
        v.close()


def test_flight_report_and_flightlog_reimport(client, live, tmp_path, capsys):
    """Отчёт по полёту R N для протокола испытаний (Х1, Х2, Х3, Х5) и журнал полёта для повторной загрузки:
    повторная загрузка распознаётся как уже учтённый полёт, наработка второй раз не начисляется."""
    a, lid, token = link_for(client)
    mid = local_mission(client, a["id"]).json()["id"]
    ag, v = start_agent(live, token, tmp_path / "ag")
    try:
        assert wait(lambda: get(client, lid)["state"] == "idle")
        assert client.post(f"/api/drones/{lid}/prepare", json={"mission_id": mid}).status_code == 200
        d = wait(lambda: (lambda x: x if x["run"] and x["run"]["status"] == "ready" else None)(get(client, lid)), 20)
        client.post(f"/api/drones/{lid}/start", json={"run_id": d["run"]["id"], "plan_hash": d["run"]["plan_hash"], "confirm": True})
        run = wait(lambda: (lambda m: next((x for x in m["runs"] if x["status"] in ("completed", "aborted", "failed")), None))(
            client.get(f"/api/missions/{mid}").json()), 40)
        assert run and run["status"] == "completed" and run["import_id"], run
    finally:
        ag.stop()
        v.close()
    rid = run["id"]
    rep = client.get(f"/api/runs/{rid}/report").json()
    assert rep["x1"]["reached"] == rep["x1"]["total"] == 2 and rep["x1"]["pct"] == 100 and rep["x1"]["ok"]
    assert rep["x2"]["track_points"] > 0 and all(p["miss_m"] is not None for p in rep["x2"]["points"])
    assert rep["x2"]["max_miss_m"] <= 1.0 and rep["x2"]["ok"]
    x3 = rep["x3"]
    dur = x3["flight"]["duration_s"]
    assert x3["aircraft"]["delta_cycles"] == 1 and len(x3["components"]) == 7 and x3["same_for_all"] and x3["cycles_ok"]
    assert abs(x3["aircraft"]["delta_h"] - x3["flight"]["duration_h"]) < 1e-6 and x3["ok"] is None   # видео не задано
    assert rep["x5"]["count"] == 0 and rep["x5"]["ok"]
    ok = client.get(f"/api/runs/{rid}/report", params={"video_s": dur + 20}).json()["x3"]    # 20 с < 0,01 ч (36 с)
    assert ok["ok"] and ok["max_diff_h"] < 0.01
    assert client.get(f"/api/runs/{rid}/report", params={"video_s": dur + 60}).json()["x3"]["ok"] is False
    md = client.get(f"/api/runs/{rid}/report", params={"fmt": "md"})
    assert md.status_code == 200 and f"# Отчёт по полёту R{rid}" in md.text and "attachment" in md.headers["content-disposition"]
    # журнал полёта → повторная загрузка через «Импорт журналов»: дубликат, 0 ч
    hours = next(x for x in client.get("/api/aircraft").json() if x["id"] == a["id"])["hours"]
    fl = client.get(f"/api/runs/{rid}/flightlog")
    assert fl.status_code == 200 and f"flight-R{rid}-" in fl.headers["content-disposition"]
    r = client.post("/api/imports", files={"file": (f"flight-R{rid}-{a['serial']}.json", fl.content, "application/json")}).json()
    assert r["status"] == "duplicate" and r["flights_new"] == 0 and r["hours_added"] == 0, r
    assert next(x for x in client.get("/api/aircraft").json() if x["id"] == a["id"])["hours"] == hours
    assert client.get(f"/api/runs/{rid}/report").json()["reimports"][-1]["hours_added"] == 0
    assert client.get("/api/runs/999999/report").status_code == 404
    # командная строка сервера (sudo tbo-ctl flight-report N / flights)
    from app.admin import main
    assert main(["flight-report", f"R{rid}", "--video-s", str(dur)]) == 0
    out = capsys.readouterr().out
    assert f"# Отчёт по полёту R{rid}" in out and "| Х2 |" in out and "соответствует" in out
    assert main(["flights", "--csv"]) == 0
    assert f"R{rid}," in capsys.readouterr().out


def test_admin_cli_drone_add(client, capsys):
    from app.admin import main
    from app.models import Aircraft, AircraftType
    assert main(["drone-add", "technic-2306", "--name", "Technic 6S", "--token-only"]) == 0
    token = capsys.readouterr().out.strip()
    assert token.startswith("tbo_") and len(token) > 40
    with TestClient(app) as dev:
        dev.headers["Authorization"] = "Bearer " + token
        r = dev.post("/api/agent/hello", json={"agent_version": "1.0", "executor": "offboard"})
        assert r.status_code == 200 and r.json()["aircraft"]["serial"] == "technic-2306"
    assert main(["drone-add", "technic-2306"]) == 1                                                  # уже подключён
    assert main(["drone-add", "technic-9999", "--rotate"]) == 1                                      # опечатка в номере
    with SessionLocal() as s:
        assert s.query(Aircraft).filter_by(serial="technic-9999").count() == 0                         # лишний борт не создан
    assert main(["drone-add", "technic-2306", "--rotate", "--token-only"]) == 0
    new = capsys.readouterr().out.strip().splitlines()[-1]
    with TestClient(app) as dev:
        dev.headers["Authorization"] = "Bearer " + token
        assert dev.post("/api/agent/hello", json={}).status_code == 401                              # прежний токен не действует
        dev.headers["Authorization"] = "Bearer " + new
        assert dev.post("/api/agent/hello", json={}).status_code == 200
    with SessionLocal() as s:
        ac = s.query(Aircraft).filter_by(serial="technic-2306").one()
        assert ac.status == "active" and s.get(AircraftType, ac.type_id).code == "SKYRIS-T6S"


def test_flight_lost_in_air_can_be_closed_and_late_log_counts(client):
    """Борт замолчал в полёте и не вернулся: полёт можно закрыть через 10 мин тишины, иначе борт заблокирован навсегда."""
    from datetime import timedelta

    from app.models import DroneLink, MissionRun
    from app.timeutil import utcnow
    a, lid, token = link_for(client)
    mid = mission(client, a["id"])
    with TestClient(app) as dev:
        dev.headers["Authorization"] = "Bearer " + token
        dev.post("/api/agent/state", json={"state": "idle"})
        assert client.post(f"/api/drones/{lid}/prepare", json={"mission_id": mid}).status_code == 200
        run0 = get(client, lid)["run"]
        rid = run0["id"]
        dev.post("/api/agent/state", json={"state": "ready", "run_id": rid, "plan_hash": run0["plan_hash"]})
        dev.post("/api/agent/state", json={"state": "in_flight", "run_id": rid})            # «старт» ушёл, борт в воздухе
        assert get(client, lid)["run"]["status"] == "in_flight"
        early = client.post(f"/api/drones/{lid}/cancel")
        assert early.status_code == 400 and "10 мин" in early.json()["detail"]             # связь есть — закрыть нельзя
        with SessionLocal() as s:                                                          # 11 минут тишины
            s.get(DroneLink, lid).last_seen_at = utcnow() - timedelta(minutes=11)
            s.commit()
        r = client.post(f"/api/drones/{lid}/cancel")
        assert r.status_code == 200 and "не выполнен" in r.json()["message"], r.text
        d = get(client, lid)
        assert d["run"] is None
        with SessionLocal() as s:
            assert s.get(MissionRun, rid).status == "failed"
        # агент вернулся и передал журнал с борта — полёт учтён
        now = utcnow()
        log = {"status": "completed", "reason": "", "track": [], "summary": {"duration_s": 120},
               "flight": {"start": (now - timedelta(minutes=30)).isoformat() + "Z", "end": (now - timedelta(minutes=28)).isoformat() + "Z"}}
        res = dev.post(f"/api/agent/runs/{rid}/log", json=log)
        assert res.status_code == 200, res.text
        with SessionLocal() as s:
            run = s.get(MissionRun, rid)
            assert run.status == "completed" and run.import_id


def test_failed_start_does_not_leave_flight_stuck(client):
    """«Запуск» ушёл на сервер, но моторы не включились (например, отключены): борт снова «готов» — полёт тоже,
    иначе он навсегда оставался бы «в полёте» и миссию нельзя было бы ни прервать, ни отменить."""
    from app.models import MissionRun
    a, lid, token = link_for(client)
    mid = mission(client, a["id"])
    with TestClient(app) as dev:
        dev.headers["Authorization"] = "Bearer " + token
        dev.post("/api/agent/state", json={"state": "idle"})
        assert client.post(f"/api/drones/{lid}/prepare", json={"mission_id": mid}).status_code == 200
        run0 = get(client, lid)["run"]
        rid, ph = run0["id"], run0["plan_hash"]
        dev.post("/api/agent/state", json={"state": "ready", "run_id": rid, "plan_hash": ph, "checks": [],
                                           "telemetry": {"connected": True, "armed": False}})
        r = client.post(f"/api/drones/{lid}/start", json={"run_id": rid, "plan_hash": ph, "confirm": True})
        assert r.status_code == 200, r.text
        cmd = dev.get("/api/agent/commands", params={"wait": 0}).json()["command"]
        assert cmd["kind"] == "start"
        dev.post("/api/agent/state", json={"state": "starting", "run_id": rid, "plan_hash": ph})
        assert get(client, lid)["run"]["status"] == "in_flight"
        dev.post("/api/agent/state", json={"state": "ready", "run_id": rid, "plan_hash": ph})
        dev.post(f"/api/agent/commands/{cmd['id']}/result",
                 json={"ok": False, "message": "Старт не выполнен: моторы не включились"})
        d = get(client, lid)
        assert d["run"]["status"] == "ready"
        r = client.post(f"/api/drones/{lid}/cancel")
        assert r.status_code == 200, r.text
        with SessionLocal() as s:
            assert s.get(MissionRun, rid).status == "cancelled"


def test_online_board_not_flying_flight_can_be_closed(client):
    """Агент на связи и в «ожидании» (например, перезапущен после сбоя старта), а полёт на сервере — «в полёте»."""
    from app.models import MissionRun
    a, lid, token = link_for(client)
    mid = mission(client, a["id"])
    with TestClient(app) as dev:
        dev.headers["Authorization"] = "Bearer " + token
        dev.post("/api/agent/state", json={"state": "idle"})
        assert client.post(f"/api/drones/{lid}/prepare", json={"mission_id": mid}).status_code == 200
        run0 = get(client, lid)["run"]
        rid = run0["id"]
        dev.post("/api/agent/state", json={"state": "ready", "run_id": rid, "plan_hash": run0["plan_hash"]})
        dev.post("/api/agent/state", json={"state": "in_flight", "run_id": rid})
        assert client.post(f"/api/drones/{lid}/cancel").status_code == 400                # борт летит — закрыть нельзя
        dev.post("/api/agent/state", json={"state": "idle"})
        r = client.post(f"/api/drones/{lid}/cancel")
        assert r.status_code == 200, r.text
        assert get(client, lid)["run"] is None
        with SessionLocal() as s:
            assert s.get(MissionRun, rid).status == "failed"


def _external_log(minutes_ago=40, duration_s=300, spike_at_s=None):
    from datetime import timedelta

    from app.timeutil import utcnow
    end = utcnow() - timedelta(minutes=minutes_ago)
    start = end - timedelta(seconds=duration_s)
    imu = [1.0] * (duration_s * 25)
    if spike_at_s is not None:
        imu[spike_at_s * 25] = 6.5
    return {"status": "completed", "reason": "полёт вне tbo-tracker (QGroundControl / пульт)",
            "track": [[0.0, HOME[0], HOME[1], 0.0, 0.0, "takeoff"], [5.0, HOME[0] + 0.0001, HOME[1], 8.0, 3.0, "mission"]],
            "summary": {"duration_s": duration_s, "executor": "mission", "agent_version": "1.2.0", "external": True,
                        "max_alt_m": 8.0},
            "flight": {"start": start.isoformat() + "Z", "end": end.isoformat() + "Z",
                       "imu": {"rate_hz": 25, "t0_s": 0, "accel_g": imu}, "events": []}}


def test_flight_outside_tbo_tracker_counted_once(client):
    """Взлёт из QGroundControl / с пульта: полёт борта без миссии — наработка и акселерометр как у обычного полёта,
    повтор с тем же flight_id (обрыв связи после приёма) второй полёт не создаёт; арминг на земле — не полёт."""
    from app.models import LogImport, MissionRun
    a, lid, token = link_for(client)
    fid = "X20261111T093000Z-ab12"
    ext = f"{a['serial']}-{fid}"
    with SessionLocal() as s:
        n0 = s.query(Flight).filter_by(aircraft_id=a["id"]).count()
        runs0 = s.query(MissionRun).filter_by(aircraft_id=a["id"]).count()
    with TestClient(app) as dev:
        assert dev.post("/api/agent/flights", json={**_external_log(), "flight_id": fid}).status_code == 401
        dev.headers["Authorization"] = "Bearer " + token
        dev.post("/api/agent/state", json={"state": "in_flight", "detail": "полёт вне tbo-tracker (QGroundControl / пульт)",
                                           "telemetry": {"armed": True, "in_air": True}})
        assert get(client, lid)["run"] is None                     # выполнения миссии нет
        r = dev.post("/api/agent/flights", json={**_external_log(spike_at_s=120), "flight_id": fid})
        assert r.status_code == 200, r.text
        res = r.json()
        assert res["accepted"] is True and res["already"] is False and res["external_id"] == ext
        assert res["import"]["flights_new"] == 1 and res["import"]["hours_added"] > 0
        with SessionLocal() as s:
            f = s.query(Flight).filter_by(external_id=ext).one()
            assert f.aircraft_id == a["id"] and "вне tbo-tracker" in f.mission and "QGroundControl" in f.pilot
            assert 299 <= (f.ended_at - f.started_at).total_seconds() <= 301
            assert [e.kind for e in s.query(SensorEvent).filter_by(flight_id=f.id)] == ["collision"]
            imp = s.get(LogImport, f.import_id)
            assert imp.format == "drone" and imp.summary["agent"]["flight_id"] == fid
            assert imp.summary["agent"]["status"] == "completed" and imp.summary["agent"]["track_points"] == 2
            assert s.query(MissionRun).filter_by(aircraft_id=a["id"]).count() == runs0
        # повтор того же журнала и тот же номер с другим содержимым — уже учтён
        for body in (_external_log(spike_at_s=120), _external_log(minutes_ago=39)):
            r = dev.post("/api/agent/flights", json={**body, "flight_id": fid})
            assert r.status_code == 200 and r.json()["already"] is True, r.text
        with SessionLocal() as s:
            assert s.query(Flight).filter_by(aircraft_id=a["id"]).count() == n0 + 1
        # моторы включили и выключили на земле — полёта нет
        r = dev.post("/api/agent/flights", json={"status": "completed", "flight": None, "track": [], "summary": {},
                                                 "flight_id": "X20261111T094000Z-cd34"})
        assert r.status_code == 200 and r.json()["no_flight"] is True and r.json()["import"] is None
        with SessionLocal() as s:
            assert s.query(Flight).filter_by(aircraft_id=a["id"]).count() == n0 + 1
        # номер полёта задаёт агент, но только допустимыми символами
        for bad in ("", "../R1", "X" * 65, "a b"):
            assert dev.post("/api/agent/flights", json={**_external_log(), "flight_id": bad}).status_code == 422
        body = _external_log(minutes_ago=20)
        body["flight"]["end"] = body["flight"]["start"]
        assert dev.post("/api/agent/flights", json={**body, "flight_id": "X20261111T095000Z-ef56"}).status_code == 400
    assert any(x["action"] == "drone_flight_logged" and "вне tbo-tracker" in (x.get("details") or "")
               for x in client.get("/api/audit?limit=50").json())


def test_agent_client_sends_external_flight(client, live):
    """Клиент агента (tbo_agent.client.Api.upload_flight) ↔ настоящий сервер по HTTP."""
    a, lid, token = link_for(client)
    api = Api(live, token, timeout=10)
    res = api.upload_flight("X20261111T100000Z-0001", _external_log(minutes_ago=90, duration_s=120))
    assert res["accepted"] is True and res["import"]["flights_new"] == 1
    assert api.upload_flight("X20261111T100000Z-0001", _external_log(minutes_ago=90, duration_s=120))["already"] is True
    with SessionLocal() as s:
        assert s.query(Flight).filter_by(external_id=f"{a['serial']}-X20261111T100000Z-0001").count() == 1


def test_ready_preparation_closed_when_board_flies_outside_tbo_tracker(client):
    """План загружен («готов к старту»), а взлетели из QGroundControl: подготовка закрывается сразу, полёт борта
    без миссии учитывается отдельно, «Старт» по старому плану уже не отправить."""
    from app.models import MissionRun
    a, lid, token = link_for(client)
    mid = mission(client, a["id"])
    with TestClient(app) as dev:
        dev.headers["Authorization"] = "Bearer " + token
        dev.post("/api/agent/state", json={"state": "idle"})
        assert client.post(f"/api/drones/{lid}/prepare", json={"mission_id": mid}).status_code == 200
        run0 = get(client, lid)["run"]
        dev.post("/api/agent/state", json={"state": "ready", "run_id": run0["id"], "plan_hash": run0["plan_hash"]})
        assert get(client, lid)["run"]["status"] == "ready"
        dev.post("/api/agent/state", json={"state": "in_flight", "run_id": None,
                                           "detail": "полёт вне tbo-tracker (QGroundControl / пульт) — запись журнала"})
        d = get(client, lid)
        assert d["run"] is None and d["state"] == "in_flight"
        with SessionLocal() as s:
            run = s.get(MissionRun, run0["id"])
            assert run.status == "cancelled" and "вне tbo-tracker" in run.summary["error"]
        r = client.post(f"/api/drones/{lid}/start", json={"run_id": run0["id"], "plan_hash": run0["plan_hash"], "confirm": True})
        assert r.status_code == 400
        assert client.post(f"/api/drones/{lid}/prepare", json={"mission_id": mid}).status_code == 400     # борт в полёте
        r = dev.post("/api/agent/flights", json={**_external_log(minutes_ago=5, duration_s=60), "flight_id": "X20261111T110000Z-0a0b"})
        assert r.status_code == 200 and r.json()["accepted"] is True, r.text
