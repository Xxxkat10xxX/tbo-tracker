"""Ядро агента без сервера и без ROS: план, проверки, регистратор, настройки и логика агента на симуляторе борта."""
import copy
import os
import threading
import time

import pytest

from tbo_agent.agent import Agent
from tbo_agent.checks import preflight
from tbo_agent.client import ApiError, clean, parse_iso
from tbo_agent.config import Config, ConfigError
from tbo_agent.plan import PlanError, effective_limits, plan_hash, validate
from tbo_agent.recorder import Recorder
from tbo_agent.vehicle_sim import SimVehicle

HOME = (54.95, 38.2)
SERIAL = "TBT-M4-001"


def make_plan(run_id=7, **kw):
    p = {"format": "tbo-plan/1", "run_id": run_id, "mission": {"id": 1, "name": "Тест"}, "aircraft": {"serial": SERIAL, "name": "Борт 01"},
         "home": {"lat": HOME[0], "lon": HOME[1]}, "takeoff_alt": 20.0, "speed_ms": 8.0, "return_home": True,
         "waypoints": [{"lat": HOME[0] + 0.001, "lon": HOME[1], "alt": 25.0, "hold_s": 0.0},
                       {"lat": HOME[0] + 0.001, "lon": HOME[1] + 0.0015, "alt": 30.0, "hold_s": 2.0}],
         "limits": {"max_alt_m": 120, "max_dist_m": 2000, "max_home_offset_m": 50, "min_battery_pct": 50}}
    p.update(kw)
    p["plan_hash"] = plan_hash(p)
    return p


LIMITS = {"max_alt_m": 120, "max_dist_m": 1000, "max_home_offset_m": 30, "min_battery_pct": 50, "min_sats": 8, "cells": 6, "min_cell_v": 3.75}


# ------------------------------------------------------------------ план и проверки
def test_plan_validation():
    validate(make_plan(), SERIAL, LIMITS)
    with pytest.raises(PlanError, match="другого борта"):
        validate(make_plan(), "OTHER-1", LIMITS)
    bad = make_plan()
    bad["waypoints"][0]["alt"] = 300.0                       # изменено после подписи хешем
    with pytest.raises(PlanError, match="контрольная сумма"):
        validate(bad, SERIAL, LIMITS)
    with pytest.raises(PlanError, match="выше|вне пределов"):
        validate(make_plan(waypoints=[{"lat": HOME[0], "lon": HOME[1] + 0.001, "alt": 150.0}]), SERIAL, LIMITS)
    with pytest.raises(PlanError, match="предел борта"):
        validate(make_plan(waypoints=[{"lat": HOME[0] + 0.02, "lon": HOME[1], "alt": 30.0}]), SERIAL, LIMITS)
    eff = effective_limits({"max_alt_m": 100, "max_dist_m": 5000, "min_battery_pct": 40}, LIMITS)
    assert eff["max_alt_m"] == 100 and eff["max_dist_m"] == 1000 and eff["min_battery_pct"] == 50   # строже из двух


def test_preflight_checks():
    ok = {"connected": True, "armed": False, "in_air": False, "gps_fix": 3, "sats": 14, "lat": HOME[0], "lon": HOME[1],
          "home": HOME, "battery_pct": 90.0}
    assert all(c["ok"] for c in preflight(ok, make_plan(), LIMITS))
    far = dict(ok, lat=HOME[0] + 0.01)
    assert [c["name"] for c in preflight(far, make_plan(), LIMITS) if not c["ok"]] == ["Борт в точке взлёта миссии"]
    weak = dict(ok, sats=5, battery_pct=None, battery_v=21.0)            # 3,5 В на ячейку при 6S
    bad = {c["name"] for c in preflight(weak, make_plan(), LIMITS) if not c["ok"]}
    assert bad == {"GPS", "Заряд батареи"}


def _sim_headings(mode):
    """Шаги модели вручную (без потока): курс на отрезке 1 (на север), в конце удержания в точке 1 (курс 200°),
    на отрезке 2 (на восток) и на возврате."""
    v = SimVehicle(home=HOME)
    p = make_plan(yaw_mode=mode, waypoints=[
        {"lat": HOME[0] + 0.001, "lon": HOME[1], "alt": 25.0, "hold_s": 2.0, "yaw_deg": 200.0},
        {"lat": HOME[0] + 0.001, "lon": HOME[1] + 0.0015, "alt": 30.0, "hold_s": 0.0}])
    v.upload(p, {"lat": HOME[0], "lon": HOME[1]})
    v.start_mission(p)
    out = {}
    for _ in range(int(600 / v.DT)):
        v._step(v.DT)
        if v.idx == 1 and v.y > 60 and v.hold_left is None:
            out["leg1"] = v.hdg
        if v.idx == 1 and v.hold_left is not None and v.hold_left < 0.1:
            out["hold1"] = v.hdg
        if v.idx == 2 and v.x > 60:
            out["leg2"] = v.hdg
        if v.mode == "AUTO.RTL" and v.x < 40:
            out["rtl"] = v.hdg
        if not v.armed:
            break
    return out


def test_sim_heading_keep_and_route():
    def near(a, b):
        return abs((a - b + 180) % 360 - 180) < 2
    keep = _sim_headings("keep")
    assert near(keep["leg1"], 0) and near(keep["hold1"], 200) and near(keep["leg2"], 200) and near(keep["rtl"], 200), keep
    route = _sim_headings("route")
    assert near(route["leg1"], 0) and near(route["hold1"], 200) and near(route["leg2"], 90), route
    assert 180 < route["rtl"] < 270                                         # к точке взлёта: на юго-запад


def test_recorder_keeps_impact_peaks_and_builds_log():
    r = Recorder(0.0)
    snap = {"in_air": True, "lat": HOME[0], "lon": HOME[1], "alt_rel": 10.0, "speed": 5.0, "battery_pct": 90.0, "mode": "AUTO.MISSION"}
    imu = [(1.0 + i / 100, 1.0) for i in range(300)]
    imu[150] = (2.5, 5.2)                                     # удар 5,2 g — пик сохраняется при прореживании до 25 Гц
    r.sample(1.0, snap, [], "in_flight")
    r.sample(2.0, dict(snap, lat=HOME[0] + 0.0001), imu[:200], "in_flight")
    r.sample(4.0, dict(snap, in_air=False, alt_rel=0.0), imu[200:], "in_flight")
    r.landed(4.0)
    assert max(r.imu) == 5.2 and 70 <= len(r.imu) <= 76
    body = r.build(1_800_000_000.0, "completed", "миссия выполнена", {"executor": "sim"})
    assert body["flight"]["imu"]["rate_hz"] == 25 and body["summary"]["duration_s"] == 3.0
    assert parse_iso(body["flight"]["end"]).timestamp() - parse_iso(body["flight"]["start"]).timestamp() == 3.0
    assert body["track"][0][5] == "mission" and body["summary"]["distance_m"] > 10


def test_clean_and_config_token_permissions(tmp_path):
    assert clean({"a": float("nan"), "b": [1.0, float("inf")], "c": "x"}) == {"a": None, "b": [1.0, None], "c": "x"}
    tok = tmp_path / "token"
    tok.write_text("tbo_" + "x" * 43)
    os.chmod(tok, 0o644)
    conf = tmp_path / "agent.conf"
    conf.write_text("[server]\nurl = http://127.0.0.1:9\ntoken_file = %s\n[agent]\nstate_dir = %s\n" % (tok, tmp_path / "st"))
    if os.name == "posix":                      # права файлов — только POSIX (борт — Linux)
        with pytest.raises(ConfigError, match="chmod 600"):
            Config(str(conf))
    os.chmod(tok, 0o600)
    assert Config(str(conf)).token.startswith("tbo_")
    conf.write_text("[server]\nurl = ftp://x\n")
    with pytest.raises(ConfigError, match="http"):
        Config(str(conf), require_token=False)


# ------------------------------------------------------------------ агент на симуляторе с поддельным сервером
class FakeApi:
    """Сервер в памяти: очередь команд, принятые состояния, результаты и журналы."""

    def __init__(self):
        self.cmds, self.results, self.states, self.logs, self.flights = [], [], [], {}, {}
        self.active_run, self.clock_offset, self.offline = None, 0.0, False
        self.lock = threading.Lock()

    def now(self):
        return time.time()

    def hello(self, executor, vehicle):
        return {"aircraft": {"serial": SERIAL, "name": "Борт 01"}, "server_time": "2026-01-01T00:00:00Z"}

    def next_command(self, wait=20):
        end = time.time() + 0.3
        while time.time() < end:
            with self.lock:
                if self.cmds:
                    return self.cmds.pop(0)
            time.sleep(0.01)
        return None

    def result(self, cid, ok, message):
        self.results.append((cid, ok, message))

    def state(self, body):
        self.states.append(copy.deepcopy(body))
        return {"active_run_id": self.active_run}

    def upload_log(self, run_id, body):
        if self.offline:
            raise ApiError(0, "нет связи")
        self.logs[run_id] = body
        return {"ok": True}

    def upload_flight(self, flight_id, body):
        if self.offline:
            raise ApiError(0, "нет связи")
        self.flights[flight_id] = body
        return {"ok": True}

    def send(self, kind, payload=None, run_id=None, ttl=15):
        with self.lock:
            self.cmds.append({"id": len(self.results) + len(self.cmds) + 100, "kind": kind, "payload": payload or {},
                              "run_id": run_id, "ttl_s": ttl})


class Cfg:
    def __init__(self, d):
        self.url, self.state_dir, self.limits, self.ready_timeout_s = "http://test", str(d), dict(LIMITS), 600


def wait(cond, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture()
def rig(tmp_path):
    api, v = FakeApi(), SimVehicle(time_scale=60, fault="collision@2")
    ag = Agent(Cfg(tmp_path), v, api)
    th = threading.Thread(target=ag.run, daemon=True)
    th.start()
    assert wait(lambda: ag.state == "idle" and ag.serial == SERIAL)
    yield ag, api, v
    ag.stop()
    v.close()


def last_result(api):
    assert wait(lambda: api.results)
    return api.results[-1]


def test_full_flight_logged_with_collision(rig):
    ag, api, v = rig
    p = make_plan()
    api.active_run = 7
    api.send("prepare", {"plan": p}, run_id=7, ttl=120)
    assert wait(lambda: ag.state == "ready")
    ok_state = [s for s in api.states if s["state"] == "ready"][-1]
    assert ok_state["plan_hash"] == p["plan_hash"] and ok_state["run_id"] == 7 and all(c["ok"] for c in ok_state["checks"])
    assert last_result(api)[1] is True
    api.send("start", {"run_id": 7, "plan_hash": p["plan_hash"]}, run_id=7)
    assert wait(lambda: ag.state in ("in_flight", "returning", "landing"))
    assert wait(lambda: 7 in api.logs, 30)
    log = api.logs[7]
    assert log["status"] == "completed" and log["flight"] and log["summary"]["wp_reached"] == 2
    assert 60 < log["summary"]["duration_s"] < 200 and max(log["flight"]["imu"]["accel_g"]) > 5      # удар у точки 2
    assert {pt[5] for pt in log["track"]} >= {"mission", "rtl"}
    assert wait(lambda: ag.state == "idle" and ag.run_id is None)
    flying = [s for s in api.states if s["state"] == "in_flight"]
    assert flying and flying[-1]["telemetry"]["armed"] is True and "lat" in flying[-1]["telemetry"]


def test_rtl_command_marks_flight_aborted(rig):
    ag, api, v = rig
    p = make_plan(run_id=8)
    api.active_run = 8
    api.send("prepare", {"plan": p}, run_id=8, ttl=120)
    assert wait(lambda: ag.state == "ready")
    api.send("start", {"run_id": 8, "plan_hash": p["plan_hash"]}, run_id=8)
    assert wait(lambda: v.snapshot()["alt_rel"] > 10)
    api.send("rtl", run_id=8, ttl=120)
    assert wait(lambda: ag.state in ("returning", "landing") or 8 in api.logs)
    assert wait(lambda: 8 in api.logs, 30)
    assert api.logs[8]["status"] == "aborted" and "возврат домой" in api.logs[8]["reason"]


def test_prepare_rejected_and_stale_preparation_dropped(rig):
    ag, api, v = rig
    api.send("prepare", {"plan": make_plan(aircraft={"serial": "X-9", "name": "чужой"})}, run_id=7, ttl=120)
    assert wait(lambda: api.results and api.results[-1][1] is False)
    assert "другого борта" in api.results[-1][2] and ag.state == "idle"
    p = make_plan(run_id=9)
    api.active_run = 9
    api.send("prepare", {"plan": p}, run_id=9, ttl=120)
    assert wait(lambda: ag.state == "ready")
    api.active_run = None                                  # подготовку отменили на сервере, команда отмены не дошла
    assert wait(lambda: ag.state == "idle", 10)
    api.send("start", {"run_id": 9, "plan_hash": p["plan_hash"]}, run_id=9)
    assert wait(lambda: api.results[-1][1] is False and "не готов" in api.results[-1][2])
    assert not v.snapshot()["armed"]


def test_start_rejected_when_expired_or_other_plan(rig):
    ag, api, v = rig
    p = make_plan(run_id=10)
    api.active_run = 10
    api.send("prepare", {"plan": p}, run_id=10, ttl=120)
    assert wait(lambda: ag.state == "ready")
    api.send("start", {"run_id": 10, "plan_hash": "0" * 64}, run_id=10)
    assert wait(lambda: "другому плану" in api.results[-1][2])
    api.send("start", {"run_id": 10, "plan_hash": p["plan_hash"]}, run_id=10, ttl=0)
    assert wait(lambda: "просрочена" in api.results[-1][2])
    assert ag.state == "ready" and not v.snapshot()["armed"]
    api.send("cancel", run_id=10)
    assert wait(lambda: ag.state == "idle")


def test_log_kept_in_outbox_when_server_unreachable(rig):
    ag, api, v = rig
    p = make_plan(run_id=11, waypoints=[{"lat": HOME[0] + 0.0005, "lon": HOME[1], "alt": 20.0, "hold_s": 0.0}])
    api.active_run, api.offline = 11, True
    api.send("prepare", {"plan": p}, run_id=11, ttl=120)
    assert wait(lambda: ag.state == "ready")
    api.send("start", {"run_id": 11, "plan_hash": p["plan_hash"]}, run_id=11)
    assert wait(lambda: ag.state == "idle" and ag.outbox.pending() == ["run-11.json"], 30)
    api.offline = False
    assert ag._flush_outbox() and 11 in api.logs and not ag.outbox.pending()


class SlowApi(FakeApi):
    """Сервер, отвечающий на журнал с задержкой (импорт на Pi занимает секунды)."""

    def __init__(self, delay=0.3):
        super().__init__()
        self.delay, self.uploads = delay, []

    def upload_log(self, run_id, body):
        self.uploads.append(run_id)
        time.sleep(self.delay)
        return super().upload_log(run_id, body)


def test_parallel_flush_of_same_log_does_not_crash(tmp_path):
    """Поток полёта и фоновый повтор отправляют журналы одновременно: прежде второй не находил файл, падал при переносе
    в rejected/ — и агент навсегда оставался «Передача журнала» (или умирал фоновый повтор)."""
    api = SlowApi()
    ag = Agent(Cfg(tmp_path), SimVehicle(time_scale=60), api)
    ag.outbox.put(31, {"status": "completed", "flight": None})
    errors, results = [], []

    def flush():
        try:
            results.append(ag._flush_outbox())
        except Exception as e:  # noqa: BLE001
            errors.append(e)
    ths = [threading.Thread(target=flush) for _ in range(2)]
    for th in ths:
        th.start()
    for th in ths:
        th.join(10)
    assert not errors and results == [True, True]
    assert api.uploads == [31] and 31 in api.logs                  # отправлен один раз
    assert not ag.outbox.pending() and os.listdir(os.path.join(str(tmp_path), "sent")) == ["run-31.json"]
    assert not os.path.isdir(os.path.join(str(tmp_path), "rejected"))


def test_flush_tolerates_log_already_moved(tmp_path):
    """Файл исчез из outbox во время отправки (перенесён другим процессом): это не «повреждённый журнал»."""
    api = FakeApi()
    ag = Agent(Cfg(tmp_path), SimVehicle(time_scale=60), api)
    ag.outbox.put(32, {"status": "completed", "flight": None})
    orig = api.upload_log

    def upload_and_move(run_id, body):
        orig(run_id, body)
        ag.outbox.done("run-%d.json" % run_id)              # «другой поток» успел перенести файл
    api.upload_log = upload_and_move
    assert ag._flush_outbox() is True and 32 in api.logs
    assert not os.path.isdir(os.path.join(str(tmp_path), "rejected"))


def test_outbox_retry_survives_unexpected_errors(tmp_path):
    ag = Agent(Cfg(tmp_path), SimVehicle(time_scale=60), FakeApi())
    ag.outbox.put(33, {"status": "completed", "flight": None})

    def boom():
        raise RuntimeError("сбой")
    ag._flush_outbox = boom
    ag._outbox_tick()                                       # исключение не выходит наружу — поток повтора жив


def test_log_written_durably_and_corrupt_checkpoint_set_aside(tmp_path, monkeypatch):
    """Журнал в outbox и контрольная точка пишутся с fsync (корень борта — commit=600: без fsync журнал полёта
    пропадал при отключении питания после посадки); оборванная контрольная точка не лежит вечно, а уходит в сторону."""
    import tbo_agent.agent as agent_mod
    synced = []
    real_fsync = os.fsync
    monkeypatch.setattr(agent_mod.os, "fsync", lambda fd: (synced.append(fd), real_fsync(fd))[1])
    ag = Agent(Cfg(tmp_path), SimVehicle(time_scale=60), FakeApi())
    ag.outbox.put(35, {"status": "completed", "flight": None})
    assert synced and ag.outbox.pending() == ["run-35.json"] and ag.outbox.load("run-35.json")["status"] == "completed"
    assert not [f for f in os.listdir(ag.outbox.path) if f.endswith(".tmp")]           # временный файл не остаётся
    with open(ag.ckpt_path, "w", encoding="utf-8") as f:
        f.write('{"run_id": 91, "flight_id": null, "takeoff_wall": 17')                  # питание отключили во время записи
    ag.v.open()
    ag._recover()
    assert not os.path.exists(ag.ckpt_path) and os.path.exists(ag.ckpt_path + ".corrupt")


def test_flight_returns_to_idle_when_log_upload_fails_unexpectedly(rig):
    ag, api, v = rig

    def boom(run_id, body):
        raise RuntimeError("неожиданная ошибка отправки")
    api.upload_log = boom
    p = make_plan(run_id=34, waypoints=[{"lat": HOME[0] + 0.0005, "lon": HOME[1], "alt": 20.0, "hold_s": 0.0}])
    api.active_run = 34
    api.send("prepare", {"plan": p}, run_id=34, ttl=120)
    assert wait(lambda: ag.state == "ready")
    api.send("start", {"run_id": 34, "plan_hash": p["plan_hash"]}, run_id=34)
    assert wait(lambda: ag.state == "idle" and ag.run_id is None, 30), (ag.state, ag.detail)
    assert "передан позже" in ag.detail and ag.outbox.pending() == ["run-34.json"]   # журнал сохранён на борту
    del api.upload_log                                      # связь восстановилась: обычная отправка
    assert ag._flush_outbox() and 34 in api.logs and not ag.outbox.pending()


def test_landing_before_route_end_is_not_reported_completed(rig):
    """Борт сел сам посреди маршрута (без команды оператора и перехвата пилотом): прежде журнал — «миссия выполнена»
    даже при 0 достигнутых точек (так было с полётом R45)."""
    ag, api, v = rig
    p = make_plan(run_id=35)
    api.active_run = 35
    api.send("prepare", {"plan": p}, run_id=35, ttl=120)
    assert wait(lambda: ag.state == "ready")
    api.send("start", {"run_id": 35, "plan_hash": p["plan_hash"]}, run_id=35)
    assert wait(lambda: v.snapshot()["alt_rel"] > 10)
    v.land()                                                # посадка не по команде агента
    assert wait(lambda: 35 in api.logs, 30)
    log = api.logs[35]
    assert log["status"] == "aborted" and "достигнуто точек 0 из 2" in log["reason"], log["reason"]


def test_long_flight_track_is_thinned_not_lost():
    from tbo_agent import recorder
    r = Recorder(0.0)
    snap = {"in_air": True, "lat": HOME[0], "lon": HOME[1], "alt_rel": 10.0, "speed": 5.0}
    for i in range(recorder.TRACK_MAX + 10):
        r.sample(float(i), snap, [], "in_flight")
    assert len(r.track) <= recorder.TRACK_MAX and r.track[0][0] == 0.0


def test_unexpected_vehicle_errors_do_not_leave_agent_stuck(rig):
    """Исключение не из VehicleError (сбой ROS-сервиса и т. п.): прежде агент оставался «preparing»/«starting» навсегда."""
    ag, api, v = rig
    orig_upload, orig_start = v.upload, v.start_mission

    def boom_upload(*a, **kw):
        raise RuntimeError("rospy.ServiceException: transport error")
    v.upload = boom_upload
    p = make_plan(run_id=21)
    api.active_run = 21
    api.send("prepare", {"plan": p}, run_id=21, ttl=120)
    assert wait(lambda: api.results and api.results[-1][1] is False)
    assert wait(lambda: ag.state == "idle") and ag.run_id is None and ag.plan is None
    v.upload = orig_upload                                  # следующая подготовка проходит
    api.send("prepare", {"plan": p}, run_id=21, ttl=120)
    assert wait(lambda: ag.state == "ready"), (ag.state, ag.detail, api.results[-3:])

    def boom_start(plan):
        raise RuntimeError("ошибка вне VehicleError")
    v.start_mission = boom_start
    n = len(api.results)
    api.send("start", {"run_id": 21, "plan_hash": p["plan_hash"]}, run_id=21)
    assert wait(lambda: len(api.results) > n and api.results[-1][1] is False)
    assert ag.state == "ready" and ag.recorder is None       # моторы не включились — снова «готов», а не «запуск»
    v.start_mission = orig_start
