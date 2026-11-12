"""Полёт вне tbo-tracker: моторы включили из QGroundControl (или с пульта) без команды агента.

Модель PX4 (fakepx4.py): миссию загружает «QGroundControl», арминг — сервис /mavros/cmd/arming, «Start Mission» —
set_mode AUTO.MISSION. Агент только записывает полёт и передаёт журнал (POST /api/agent/flights), бортом не управляет.
"""
import json
import os
import threading
import time

import pytest
import rospy
from fakepx4 import FakePX4
from mavros_msgs.msg import Waypoint
from test_agent_core import Cfg, FakeApi, wait

from tbo_agent.agent import EXTERNAL_REASON, Agent
from tbo_agent.vehicle_ros import RosVehicle

HOME = (55.75, 37.6)
AGENT_CALLS = ("clear", "push", "navigate", "navigate_global", "land", "release", "set_yaw")


def qgc_mission(fc):
    """Миссия, загруженная из QGroundControl: взлёт, точка, посадка."""
    rospy._services["/mavros/mission/push"](0, [
        Waypoint(command=22, z_alt=8.0, is_current=True),
        Waypoint(command=16, x_lat=HOME[0] + 0.0005, y_long=HOME[1], z_alt=10.0),
        Waypoint(command=21, x_lat=HOME[0] + 0.0005, y_long=HOME[1], z_alt=0.0)])


def qgc_start(fc):
    """«Start Mission» в QGroundControl: режим AUTO.MISSION и арминг."""
    rospy._services["/mavros/set_mode"](0, "AUTO.MISSION")
    rospy._services["/mavros/cmd/arming"](True)


def agent_calls(fc, before):
    """Вызовы сервисов PX4 после «QGroundControl» — их мог сделать только агент."""
    return [c for c in fc.calls[before:] if c[0] in AGENT_CALLS or c[:2] == ("set_mode", "AUTO.RTL")]


@pytest.fixture()
def fc():
    px4 = FakePX4(lat=HOME[0], lon=HOME[1])
    yield px4
    px4.stop()


def start(tmp_path, api=None):
    api = api or FakeApi()
    v = RosVehicle("auto", fcu_wait_s=5, tolerance_m=1.0, takeoff_speed=5.0)
    ag = Agent(Cfg(tmp_path), v, api)
    threading.Thread(target=ag.run, daemon=True).start()
    assert wait(lambda: ag.serial is not None and ag.state in ("idle", "in_flight"), 15)
    return ag, api, v


def test_qgc_mission_flight_recorded_and_sent(fc, tmp_path):
    ag, api, v = start(tmp_path)
    try:
        assert wait(lambda: ag.state == "idle")
        qgc_mission(fc)
        n0 = len(fc.calls)
        qgc_start(fc)
        assert wait(lambda: ag.state == "in_flight" and ag.flight_id, 5)
        fid = ag.flight_id
        assert ag.run_id is None and ag.plan is None
        assert EXTERNAL_REASON in ag.detail
        def flying():
            return [s for s in api.states if s["state"] == "in_flight" and s["telemetry"].get("armed")]
        assert wait(lambda: flying(), 5)
        assert flying()[-1]["run_id"] is None and EXTERNAL_REASON in flying()[-1]["detail"]
        assert wait(lambda: fid in api.flights, 30)
        log = api.flights[fid]
        assert log["status"] == "completed" and log["reason"] == EXTERNAL_REASON
        assert log["flight"] and log["summary"]["duration_s"] >= 1 and log["summary"]["external"] is True
        assert log["flight"]["imu"]["accel_g"] and log["track"]
        assert log["summary"]["max_alt_m"] >= 7
        assert not api.logs                                       # не «выполнение миссии» tbo-tracker
        assert wait(lambda: ag.state == "idle" and ag.flight_id is None and ag.recorder is None)
        assert agent_calls(fc, n0) == []                          # только запись: агент бортом не управлял
        assert not os.path.exists(ag.ckpt_path)
        assert os.listdir(os.path.join(str(tmp_path), "sent")) == ["flight-%s.json" % fid]
    finally:
        ag.stop()


def test_arming_on_ground_creates_no_flight(fc, tmp_path):
    ag, api, v = start(tmp_path)
    try:
        assert wait(lambda: ag.state == "idle")
        rospy._services["/mavros/cmd/arming"](True)               # моторы включили и выключили, не взлетая
        assert wait(lambda: ag.state == "in_flight" and ag.flight_id, 5)
        time.sleep(1.0)
        rospy._services["/mavros/cmd/arming"](False)
        assert wait(lambda: ag.state == "idle" and ag.recorder is None, 15)
        assert "на земле" in ag.detail and ag.flight_id is None
        time.sleep(0.5)
        assert not api.flights and not api.logs and ag.outbox.pending() == []
        assert not os.path.exists(ag.ckpt_path)
    finally:
        ag.stop()


def test_operator_commands_and_stop_do_not_touch_external_flight(fc, tmp_path):
    """Полётом управляет пилот: «Вернуть домой» из tbo-tracker отклоняется, остановка службы не включает возврат."""
    ag, api, v = start(tmp_path)
    try:
        assert wait(lambda: ag.state == "idle")
        rospy._services["/mavros/cmd/arming"](True)
        fc.z = 5.0                                                # пилот поднял борт с пульта (POSCTL)
        assert wait(lambda: ag.recorder is not None and ag.recorder.took_off, 5)
        n0 = len(fc.calls)
        api.send("rtl", ttl=120)
        assert wait(lambda: api.results)
        assert api.results[-1][1] is False and "вне tbo-tracker" in api.results[-1][2]
        ag.stop()
        time.sleep(0.5)
        assert agent_calls(fc, n0) == []
        assert fc.mode == "POSCTL"
    finally:
        ag.stop()
        fc.armed, fc.z = False, 0.0


def test_ready_plan_dropped_but_not_cleared_when_pilot_arms(fc, tmp_path):
    """План tbo-tracker загружен («готов»), а взлетели из QGroundControl: подготовка снимается у агента,
    миссия в контроллере не стирается (по ней, возможно, и летит пилот)."""
    ag, api, v = start(tmp_path)
    try:
        assert wait(lambda: ag.state == "idle")
        api.active_run = 7                                        # сервер считает подготовку текущей
        # дождаться отправки состояния, которая уже видит подготовку на сервере: иначе ответ, полученный до этой
        # строки, мог снять подготовку («снята сервером») сразу после перехода в «готов» — тест падал через раз
        n = len(api.states)
        ag.wake.set()
        assert wait(lambda: len(api.states) > n, 10)
        ag._set("ready", "готов к старту", run_id=7, plan={"plan_hash": "h", "waypoints": []})
        n0 = len(fc.calls)
        rospy._services["/mavros/cmd/arming"](True)
        assert wait(lambda: ag.state == "in_flight" and ag.flight_id, 5)
        assert ag.run_id is None and ag.plan is None and "подготовка tbo-tracker снята" in ag.detail
        rospy._services["/mavros/cmd/arming"](False)
        assert wait(lambda: ag.state == "idle", 15)
        assert agent_calls(fc, n0) == []
    finally:
        ag.stop()


def _ckpt(tmp_path, fid, takeoff_ago):
    now = time.time()
    with open(os.path.join(str(tmp_path), "current.json"), "w", encoding="utf-8") as f:
        json.dump({"run_id": None, "flight_id": fid, "plan_hash": None, "wp_total": 0, "abort_kind": None,
                   "takeoff_wall": now - takeoff_ago, "last_air_wall": now - 2, "saved": now - 2}, f)


def test_restart_in_air_continues_external_flight(fc, tmp_path):
    """Агент перезапущен во время полёта из QGroundControl: наблюдение продолжается по контрольной точке."""
    qgc_mission(fc)
    fc.speed = 3.0                                                # полёт дольше перезапуска агента
    qgc_start(fc)
    assert wait(lambda: fc.z > 3, 5)
    fid = "X20261111T100000Z-ab12"
    _ckpt(tmp_path, fid, 60)
    n0 = len(fc.calls)
    ag, api, v = start(tmp_path)
    try:
        assert ag.flight_id == fid and ag.state == "in_flight" and "после перезапуска" in ag.detail
        fc.speed = 40.0
        assert wait(lambda: fid in api.flights, 40)
        log = api.flights[fid]
        assert log["status"] == "completed" and log["reason"] == EXTERNAL_REASON
        assert log["summary"]["duration_s"] >= 60                 # взлёт — по контрольной точке, а не с перезапуска
        assert wait(lambda: ag.state == "idle" and ag.flight_id is None)
        assert agent_calls(fc, n0) == []
    finally:
        ag.stop()


def test_restart_after_landing_restores_external_log(fc, tmp_path):
    """Агент перезапущен после посадки (журнал не успел сохраниться): журнал восстановлен по контрольной точке,
    миссия пилота в контроллере не стирается."""
    qgc_mission(fc)
    fid = "X20261111T100500Z-cd34"
    _ckpt(tmp_path, fid, 120)
    ag, api, v = start(tmp_path)
    try:
        # восстановление по контрольной точке идёт в потоке агента после приветствия сервера — дождаться, а не
        # проверять сразу (под нагрузкой проверка успевала раньше записи журнала в outbox)
        assert wait(lambda: ag.outbox.pending() == ["flight-%s.json" % fid] and not os.path.exists(ag.ckpt_path), 10)
        ag._outbox_tick()                                         # повтор отправки (фоновый поток — раз в 30 с)
        assert fid in api.flights
        log = api.flights[fid]
        assert EXTERNAL_REASON in log["reason"] and "восстановлен" in log["reason"]
        assert log["flight"] and log["summary"]["duration_s"] >= 100 and log["summary"]["external"] is True
        assert ("clear",) not in fc.calls and len(fc.items) == 3
        assert ag.state == "idle" and not os.path.exists(ag.ckpt_path)
    finally:
        ag.stop()


def test_external_log_kept_when_server_unreachable(fc, tmp_path):
    api = FakeApi()
    api.offline = True
    ag, api, v = start(tmp_path, api)
    try:
        assert wait(lambda: ag.state == "idle")
        qgc_mission(fc)
        qgc_start(fc)
        assert wait(lambda: ag.flight_id, 5)
        fid = ag.flight_id
        assert wait(lambda: ag.state == "idle" and ag.outbox.pending() == ["flight-%s.json" % fid], 30)
        assert "при появлении связи" in ag.detail
        api.offline = False
        ag._outbox_tick()
        assert fid in api.flights and ag.outbox.pending() == []
    finally:
        ag.stop()
