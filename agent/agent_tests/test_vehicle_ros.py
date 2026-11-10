"""RosVehicle на модели PX4/MAVROS/simple_offboard: миссия, сверка, арминг, OFFBOARD, перехват управления пилотом."""
import time

import pytest
from fakepx4 import FakePX4

from tbo_agent.vehicle import VehicleError
from tbo_agent.vehicle_ros import RosVehicle

HOME = (55.75, 37.6)


def plan():
    return {"format": "tbo-plan/1", "home": {"lat": HOME[0], "lon": HOME[1]}, "takeoff_alt": 10.0, "speed_ms": 8.0,
            "return_home": True, "waypoints": [{"lat": HOME[0] + 0.0009, "lon": HOME[1], "alt": 15.0, "hold_s": 0},
                                               {"lat": HOME[0] + 0.0009, "lon": HOME[1] + 0.0012, "alt": 20.0, "hold_s": 1}]}


def wait(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def opened(executor="auto"):
    v = RosVehicle(executor, fcu_wait_s=5, tolerance_m=1.0, takeoff_speed=5.0)
    v.open()
    assert wait(lambda: v.snapshot()["lat"] is not None)
    return v


@pytest.fixture()
def px4():
    fc = FakePX4()
    yield fc
    fc.stop()


@pytest.fixture()
def px4_no_mission():
    fc = FakePX4(mission_plugin=False)
    yield fc
    fc.stop()


def test_mission_upload_verify_and_flight(px4):
    v = opened()
    assert v.executor == "mission" and v.technic
    p = plan()
    assert "2 точек" in v.upload(p, {"lat": HOME[0], "lon": HOME[1]})
    assert [w.command for w in px4.items] == [22, 178, 16, 16, 20]          # взлёт, скорость, точки, возврат домой
    assert px4.items[0].is_current and px4.items[0].z_alt == 10.0 and px4.items[1].param2 == 8.0
    assert px4.items[3].param1 == 1.0 and px4.items[3].z_alt == 20.0      # удержание и высота точки 2
    v.verify(p)
    px4.items[2].x_lat += 0.001                                            # миссию заменили (например, в QGroundControl)
    with pytest.raises(VehicleError, match="отличается"):
        v.verify(p)
    v.upload(p, {"lat": HOME[0], "lon": HOME[1]})
    v.verify(p)
    assert "AUTO.MISSION" in v.start_mission(p)
    assert ("set_mode", "AUTO.MISSION") in px4.calls and ("arming", True) in px4.calls
    assert wait(lambda: v.snapshot()["in_air"])
    assert wait(lambda: not v.snapshot()["armed"], 20)                     # посадка после возврата домой
    s = v.snapshot()
    assert s["wp_reached"] == 2 and not s["in_air"] and s["gps_fix"] == 3 and s["sats"] == 15 and s["battery_pct"] == 90.0
    imu = v.drain_imu()
    assert imu and abs(imu[-1][1] - 1.0) < 1e-6


def test_arming_refused_reports_px4_reason_and_restores_mode(px4):
    px4.refuse_arm = "Preflight Fail: ekf2 missing data"
    v = opened()
    v.upload(plan(), {"lat": HOME[0], "lon": HOME[1]})
    with pytest.raises(VehicleError, match="ekf2 missing data"):
        v.start_mission(plan())
    assert not px4.armed and px4.mode == "POSCTL"         # прежний режим: арминг с пульта не запустит миссию


def test_executor_selection(px4_no_mission):
    with pytest.raises(VehicleError, match="waypoint"):
        RosVehicle("mission", fcu_wait_s=1).open()
    assert opened("auto").executor == "offboard"


def test_offboard_flight_as_in_technic_examples(px4_no_mission):
    fc = px4_no_mission
    v = opened()
    p = plan()
    assert "OFFBOARD" in v.upload(p, {"lat": HOME[0], "lon": HOME[1]})
    assert "OFFBOARD" in v.start_mission(p)
    assert wait(lambda: not v.snapshot()["armed"] and v.snapshot()["wp_reached"] == 2, 30)
    assert ("navigate", "body", 10.0, True) in fc.calls                     # взлёт: navigate(z, frame_id='body', auto_arm)
    goals = [c for c in fc.calls if c[0] == "navigate_global"]
    assert [g[3] for g in goals] == [15.0, 20.0] and all(g[4] == "map" for g in goals)
    assert ("set_mode", "AUTO.RTL") in fc.calls and v._fly_error is None


def test_mission_items_heading_param4():
    """Миссия PX4: курс точки — param4 (градусы от севера). «Не менять» — курс борта на земле (компас), после точки
    с заданным курсом — он; «по линии маршрута» — NaN (PX4 по MPC_YAW_MODE направляет нос к точке)."""
    import math
    fc = FakePX4(heading=45.0)
    try:
        v = opened()
        assert wait(lambda: v.snapshot()["heading"] == 45.0)
        p = plan()
        p["waypoints"][1]["yaw_deg"] = 270.0
        p["waypoints"].append(dict(p["waypoints"][0]))
        assert "заданный курс в 1 точке" in v.upload(p, {"lat": HOME[0], "lon": HOME[1]})
        assert [round(w.param4, 1) for w in fc.items if w.command == 16] == [45.0, 270.0, 270.0]
        p["yaw_mode"] = "route"
        assert "нос по линии маршрута" in v.upload(p, {"lat": HOME[0], "lon": HOME[1]})
        y = [w.param4 for w in fc.items if w.command == 16]
        assert math.isnan(y[0]) and y[1] == 270.0 and math.isnan(y[2])
        fc.heading = None
        v._s.pop("heading")
        p["yaw_mode"] = "keep"
        v.upload(p, {"lat": HOME[0], "lon": HOME[1]})
        y = [w.param4 for w in fc.items if w.command == 16]
        assert math.isnan(y[0]) and y[1] == 270.0 and y[2] == 270.0          # компаса нет — до точки с курсом как в PX4
    finally:
        fc.stop()


def test_offboard_gps_route_mode_and_point_heading():
    """Программа на борту по GPS: «по линии маршрута» — разворот на месте и navigate_global с курсом отрезка
    (кадр map: 0 — восток, 90° — север); курс точки (компас 180° — юг) — разворот после прилёта."""
    fc = FakePX4(mission_plugin=False)
    try:
        v = opened()
        p = plan()
        p["yaw_mode"] = "route"
        p["waypoints"][1]["yaw_deg"] = 180.0
        v.upload(p, {"lat": HOME[0], "lon": HOME[1]})
        v.start_mission(p)
        assert wait(lambda: not v.snapshot()["armed"] and v.snapshot()["wp_reached"] == 2, 30)
        assert v._fly_error is None and ("set_mode", "AUTO.RTL") in fc.calls
        got = [(s, round(y)) for s, y in fc.yaw_cmds]
        assert got == [("set_yaw", 90), ("navigate_global", 90), ("set_yaw", 0), ("navigate_global", 0), ("set_yaw", -90)], got
    finally:
        fc.stop()


def test_offboard_route_not_skipped_when_target_frame_lags():
    """Кадр navigate_target отстаёт от navigate_global: прежде точка 1 «достигалась» по старой цели (точке взлёта),
    и борт летел сразу к точке 2, минуя точку 1."""
    import math
    fc = FakePX4(mission_plugin=False, speed=20.0, target_lag_s=0.6)
    try:
        v = opened()
        p = plan()
        v.upload(p, {"lat": HOME[0], "lon": HOME[1]})
        v.start_mission(p)
        wp1 = fc.xy(p["waypoints"][0]["lat"], p["waypoints"][0]["lon"])
        closest = [1e9]

        def done():
            closest[0] = min(closest[0], math.hypot(fc.x - wp1[0], fc.y - wp1[1]))
            s = v.snapshot()
            return not s["armed"] and s["wp_reached"] == 2
        assert wait(done, 60)
        assert closest[0] < 2.0 and v._fly_error is None                      # побывал у точки 1
    finally:
        fc.stop()


def test_offboard_pilot_override_stops_program_without_mode_changes():
    fc = FakePX4(mission_plugin=False, speed=4.0)
    try:
        v = opened()
        v.upload(plan(), {"lat": HOME[0], "lon": HOME[1]})
        v.start_mission(plan())
        assert wait(lambda: fc.z > 8 and any(c[0] == "navigate_global" for c in fc.calls))
        fc.mode = "POSCTL"                                                  # пилот переключил режим на пульте
        assert wait(lambda: not v._fly_thread.is_alive(), 5)
        assert "пилот" in (v._fly_error or "")
        time.sleep(0.3)
        assert fc.mode == "POSCTL" and ("set_mode", "AUTO.RTL") not in fc.calls
    finally:
        fc.stop()


def test_offboard_rtl_command_stops_program():
    fc = FakePX4(mission_plugin=False, speed=4.0)
    try:
        v = opened()
        v.upload(plan(), {"lat": HOME[0], "lon": HOME[1]})
        v.start_mission(plan())
        assert wait(lambda: fc.z > 8)
        assert "AUTO.RTL" in v.rtl()
        assert not v._fly_thread.is_alive() and ("release",) in fc.calls and fc.mode in ("AUTO.RTL", "AUTO.LAND")
        assert wait(lambda: not fc.armed, 20)
    finally:
        fc.stop()
