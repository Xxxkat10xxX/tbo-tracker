"""Локальный полёт Technic 6S (в помещении, без GPS) и проверки по параметрам PX4 — по документации docs.skyris.ru."""
import math
import time
from types import SimpleNamespace as NS

import pytest
from fakepx4 import FakePX4

from tbo_agent import offboard, vehicle_ros
from tbo_agent.offboard import body_to_ll, body_to_map, heading_to_yaw, map_to_body, plan_offsets, yaw_to_heading
from tbo_agent.plan import PlanError, effective_limits, plan_hash, validate
from tbo_agent.vehicle import VehicleError
from tbo_agent.vehicle_ros import RosVehicle

HOME = (55.75, 37.6)
LIM = {"max_alt_m": 120, "max_dist_m": 1000, "max_home_offset_m": 30, "min_battery_pct": 50, "min_sats": 8,
       "local_max_alt_m": 3.0, "local_max_dist_m": 20.0, "local_max_speed_ms": 2.0}


def at(fwd, right):
    """Точка плана: вперёд/вправо от точки взлёта, м → широта/долгота вокруг «дома» миссии."""
    return body_to_ll(HOME[0], HOME[1], fwd, -right)


def local_plan(**kw):
    p1, p2 = at(3.0, 0.0), at(3.0, 2.0)
    p = {"format": "tbo-plan/1", "frame": "local", "run_id": 5, "aircraft": {"serial": "T6S-1"},
         "home": {"lat": HOME[0], "lon": HOME[1]}, "takeoff_alt": 1.5, "speed_ms": 0.8, "return_home": True,
         "waypoints": [{"lat": p1[0], "lon": p1[1], "alt": 1.5, "hold_s": 0.0}, {"lat": p2[0], "lon": p2[1], "alt": 2.0, "hold_s": 0.5}],
         "limits": {"max_alt_m": 3, "max_dist_m": 20, "max_speed_ms": 2.0, "min_battery_pct": 50}}
    p.update(kw)
    p["plan_hash"] = plan_hash(p)
    return p


def wait(cond, timeout=15.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def opened(fc):
    v = RosVehicle("auto", fcu_wait_s=5, tolerance_m=1.0, takeoff_speed=5.0, local_tolerance_m=0.25)
    v.open()
    assert wait(lambda: v.snapshot()["local_ok"])
    return v


def test_local_frame_math():
    for yaw in (0.0, math.radians(90), math.radians(-135)):
        x, y = body_to_map(1.0, 2.0, yaw, 3.0, -2.0)
        f, l = map_to_body(1.0, 2.0, yaw, x, y)
        assert abs(f - 3.0) < 1e-9 and abs(l + 2.0) < 1e-9
    x, y = body_to_map(0.0, 0.0, math.radians(90), 1.0, 0.0)      # нос дрона на север (ENU) → вперёд = +y кадра map
    assert abs(x) < 1e-9 and abs(y - 1.0) < 1e-9
    fwd, left = plan_offsets(local_plan(), local_plan()["waypoints"][1])
    assert abs(fwd - 3.0) < 1e-3 and abs(left + 2.0) < 1e-3


def test_local_plan_limits():
    eff = effective_limits(local_plan()["limits"], LIM, "local")
    assert eff["max_alt_m"] == 3.0 and eff["max_speed_ms"] == 2.0
    validate(local_plan(), "T6S-1", eff)
    with pytest.raises(PlanError, match="высота"):
        validate(local_plan(takeoff_alt=4.0), "T6S-1", eff)                  # выше дальномера VL53L1X
    with pytest.raises(PlanError, match="скорость"):
        validate(local_plan(speed_ms=2.5), "T6S-1", eff)                     # Optical Flow ограничивает скорость
    far = at(30.0, 0.0)
    with pytest.raises(PlanError, match="предел борта"):
        validate(local_plan(waypoints=[{"lat": far[0], "lon": far[1], "alt": 1.5}]), "T6S-1", eff)
    glob = effective_limits({"max_alt_m": 120}, LIM, "global")
    with pytest.raises(PlanError, match="высота"):
        validate(local_plan(frame="global", takeoff_alt=1.5), "T6S-1", glob)   # по GPS — не ниже 2 м


def test_local_flight_in_map_frame_without_gps():
    fc = FakePX4()
    try:
        v = opened(fc)
        p = local_plan()
        assert "локальная навигация" in v.upload(p, {"lat": None, "lon": None})
        names = {c["name"]: c for c in v.extra_checks(p)}
        assert names["simple_offboard Technic"]["ok"] and "GPS в навигации PX4" not in names
        assert "OFFBOARD" in v.start_mission(p)
        assert wait(lambda: v.snapshot()["in_air"])
        assert wait(lambda: v.snapshot()["wp_reached"] >= 1, 20)
        s = v.snapshot()
        assert s["lat"] is not None and s["home"] == HOME and 0.5 < s["alt_rel"] < 2.5      # положение для карты
        assert wait(lambda: not v.snapshot()["armed"], 30)
        goals = [c for c in fc.calls if c[0] == "navigate" and c[1] == "map"]
        assert [round(g[2], 2) for g in goals] == [1.5, 2.0, 2.0]                          # высоты точек и возврата
        assert ("navigate", "body", 1.5, True) in fc.calls and ("land",) in fc.calls
        assert not any(c == ("set_mode", "AUTO.RTL") for c in fc.calls)                  # без GPS RTL не используется
        assert v.snapshot()["wp_reached"] == 2 and abs(fc.x) < 0.3 and abs(fc.y) < 0.3     # сел у точки взлёта
    finally:
        fc.stop()


def _fly(fc, p, done, timeout=30):
    v = opened(fc)
    v.upload(p, {"lat": None, "lon": None})
    assert "OFFBOARD" in v.start_mission(p)
    return v, wait(lambda: done(v), timeout)


def test_waypoint_over_takeoff_point_then_route_is_flown():
    """Первая точка — прямо над точкой взлёта, кадр navigate_target отстаёт от команды (TF). Прежде следующая точка
    «достигалась» мгновенно по старой цели (там, где борт уже висел), и борт садился, не долетев маршрут."""
    fc = FakePX4(speed=2.0, target_lag_s=0.5)
    try:
        here, far = at(0.0, 0.0), at(3.0, 0.0)
        p = local_plan(waypoints=[{"lat": here[0], "lon": here[1], "alt": 1.5, "hold_s": 0.0},
                                  {"lat": far[0], "lon": far[1], "alt": 1.5, "hold_s": 0.0}])
        peak = [0.0]

        def landed(v):
            peak[0] = max(peak[0], fc.x)
            return not fc.armed and v.snapshot()["wp_reached"] == 2
        v, ok = _fly(fc, p, landed)
        assert ok and peak[0] > 2.7, (peak[0], v._fly_error)                 # долетел до точки 2 (3 м вперёд)
        assert v._fly_error is None and ("land",) in fc.calls and abs(fc.x) < 0.3
    finally:
        fc.stop()


def test_hovering_off_target_near_takeoff_point_does_not_abort():
    """Борт держится в 0,35 м от цели — дальше допуска 0,25 м (оптический поток). Прежде он висел у близкой точки
    до тайм-аута, а затем садился на месте с ошибкой «точка не достигнута»."""
    fc = FakePX4(speed=2.0, hover_offset=0.35)
    try:
        near = at(0.5, 0.0)
        p = local_plan(waypoints=[{"lat": near[0], "lon": near[1], "alt": 1.5, "hold_s": 0.0}])
        t0 = time.time()
        v, ok = _fly(fc, p, lambda v: not fc.armed, 40)
        assert ok and v._fly_error is None and v.snapshot()["wp_reached"] == 1 and ("land",) in fc.calls
        assert time.time() - t0 < 25                                          # по 3 с у каждой точки, а не тайм-аут
        goals = [c for c in fc.calls if c[0] == "navigate" and c[1] == "map"]
        assert len(goals) == 2                                                # точка и возврат к точке взлёта
    finally:
        fc.stop()


def test_slow_telemetry_does_not_land_drone_mid_route():
    """Перегруженный Orange Pi (узлы камеры перезапускались ~15 раз/с): get_telemetry('navigate_target') отвечал
    до 6 с. Прежде одна задержка дольше 3 с обрывала программу — посадка после 0–1 точки («миссия выполнена»)."""
    fc = FakePX4(speed=2.0, tel_delay={"navigate_target": 4.0}, map_spike_every=7)
    try:
        far1, far2 = at(2.0, 0.0), at(2.0, 1.5)
        p = local_plan(waypoints=[{"lat": far1[0], "lon": far1[1], "alt": 1.5, "hold_s": 0.0},
                                  {"lat": far2[0], "lon": far2[1], "alt": 1.5, "hold_s": 0.0}])
        v, ok = _fly(fc, p, lambda v: not fc.armed, 90)
        assert ok and v._fly_error is None, v._fly_error
        assert v.snapshot()["wp_reached"] == 2 and ("land",) in fc.calls and abs(fc.x) < 0.4
    finally:
        fc.stop()


def test_local_return_home_command():
    fc = FakePX4(speed=0.8)
    try:
        v = opened(fc)
        p = local_plan()
        v.upload(p, {"lat": None, "lon": None})
        v.start_mission(p)
        assert wait(lambda: fc.x > 1.0, 20)
        first = v._fly_thread
        assert "возврат к точке взлёта" in v.rtl()
        assert not first.is_alive() and v._fly_thread is not first                          # прежняя программа остановлена
        assert wait(lambda: not fc.armed, 30)
        assert abs(fc.x) < 0.3 and abs(fc.y) < 0.3 and ("land",) in fc.calls
        assert not any(c == ("set_mode", "AUTO.RTL") for c in fc.calls) and ("release",) not in fc.calls
    finally:
        fc.stop()


def _deg(a):
    """Угол в градусах в (-180, 180] — для сравнения курсов."""
    return (a + 180.0) % 360.0 - 180.0


def _near(a, b, tol=1.5):
    return abs(_deg(a - b)) <= tol


def test_heading_math():
    up = math.radians(90)                                   # нос при взлёте — на +y кадра map
    assert _near(math.degrees(heading_to_yaw(up, 0)), 90) and _near(math.degrees(heading_to_yaw(up, 90)), 0)   # 90° — вправо
    assert _near(math.degrees(heading_to_yaw(math.pi / 2, 180)), -90)                                         # GPS: юг
    for h in (0.0, 45.0, 90.0, 213.7, 359.0):
        assert _near(yaw_to_heading(up, heading_to_yaw(up, h)), h, 1e-6)


def test_local_route_mode_nose_along_route():
    """Режим «по линии маршрута»: на каждом отрезке нос — по направлению полёта; при повороте больше 30° — сначала
    разворот на месте (set_yaw), затем полёт. Нос при взлёте смотрит на +y кадра map (90°)."""
    fc = FakePX4(speed=2.0, yaw=math.radians(90))
    try:
        v, ok = _fly(fc, local_plan(yaw_mode="route"), lambda v: not fc.armed and v.snapshot()["wp_reached"] == 2, 40)
        assert ok and v._fly_error is None and ("land",) in fc.calls and abs(fc.x) < 0.3 and abs(fc.y) < 0.3
        # кадр map при носе на +y: x — вправо, y — вперёд. Точка 1 — (0, 3), точка 2 — (2, 3), взлёт — (0, 0)
        back = math.degrees(math.atan2(-3.0, -2.0))          # от точки 2 к точке взлёта: −123,7°
        nav = [y for s, y in fc.yaw_cmds if s == "navigate"]
        turns = [y for s, y in fc.yaw_cmds if s == "set_yaw"]
        # отрезки: вперёд (курс взлёта — без разворота), вправо (90° → разворот), назад к взлёту (разворот)
        assert len(nav) == 3 and _near(nav[0], 90) and _near(nav[1], 0) and _near(nav[2], back), fc.yaw_cmds
        assert len(turns) == 2 and _near(turns[0], 0) and _near(turns[1], back), fc.yaw_cmds
    finally:
        fc.stop()


def test_local_route_vertical_leg_keeps_heading():
    """Точка прямо над точкой взлёта (отрезок только по высоте): курс не меняется — встроенный yaw=+inf simple_offboard
    развернул бы борт на 0°."""
    fc = FakePX4(speed=2.0, yaw=math.radians(40))
    try:
        up = at(0.0, 0.0)
        p = local_plan(yaw_mode="route", return_home=False, waypoints=[{"lat": up[0], "lon": up[1], "alt": 2.0, "hold_s": 0.0}])
        v, ok = _fly(fc, p, lambda v: not fc.armed, 30)
        assert ok and v._fly_error is None and fc.yaw_cmds == [] and _near(math.degrees(fc.yaw), 40)
    finally:
        fc.stop()


def test_local_waypoint_heading_keep_mode():
    """Курс в точке: после прилёта разворот на месте (set_yaw), затем удержание; в режиме «не менять» курс сохраняется
    до конца полёта (navigate с yaw=NaN), а карта показывает курс от «верха» (нос при взлёте)."""
    fc = FakePX4(speed=2.0, yaw=math.radians(30))
    try:
        p1, p2 = at(2.0, 0.0), at(2.0, 1.5)
        p = local_plan(waypoints=[{"lat": p1[0], "lon": p1[1], "alt": 1.5, "hold_s": 1.0, "yaw_deg": 90.0},
                                  {"lat": p2[0], "lon": p2[1], "alt": 1.5, "hold_s": 0.0}])
        v = opened(fc)
        assert "заданный курс в 1 точке" in v.upload(p, {"lat": None, "lon": None})
        v.start_mission(p)
        assert wait(lambda: v.snapshot()["wp_reached"] >= 1, 20)
        assert wait(lambda: v.snapshot()["heading"] is not None and _near(v.snapshot()["heading"], 90, 3), 10)
        assert wait(lambda: not fc.armed, 30) and v._fly_error is None and v.snapshot()["wp_reached"] == 2
        assert fc.yaw_cmds == [("set_yaw", -60.0)]                         # 30° (нос при взлёте) − 90° вправо
        assert _near(math.degrees(fc.yaw), -60)                            # курс сохранился до посадки
    finally:
        fc.stop()


def test_turn_not_reached_does_not_land_drone(monkeypatch):
    """Борт не разворачивается (например, курс не оценивается): предупреждение и полёт дальше, а не посадка."""
    monkeypatch.setattr(offboard, "YAW_TIMEOUT_S", 1.0)
    fc = FakePX4(speed=2.0, yaw_rate=0.0)
    try:
        p = local_plan(waypoints=[dict(local_plan()["waypoints"][0], yaw_deg=180.0)])
        v, ok = _fly(fc, p, lambda v: not fc.armed, 30)
        assert ok and v._fly_error is None and v.snapshot()["wp_reached"] == 1 and ("land",) in fc.calls
        assert len(fc.yaw_cmds) == 1 and fc.yaw_cmds[0][0] == "set_yaw" and _near(fc.yaw_cmds[0][1], 180)
    finally:
        fc.stop()


def test_set_yaw_refused_does_not_land_drone():
    """simple_offboard отказал в set_yaw: предупреждение и полёт дальше, а не посадка посреди маршрута."""
    import rospy
    fc = FakePX4(speed=2.0)
    refused = []
    rospy._services["/set_yaw"] = lambda yaw, frame_id: refused.append(yaw) or NS(success=False, message="Busy")
    try:
        p = local_plan(waypoints=[dict(local_plan()["waypoints"][0], yaw_deg=90.0)])
        v, ok = _fly(fc, p, lambda v: not fc.armed, 30)
        assert ok and v._fly_error is None and v.snapshot()["wp_reached"] == 1 and len(refused) == 1
        assert ("land",) in fc.calls and abs(fc.x) < 0.3 and abs(fc.y) < 0.3                 # долетел и вернулся
    finally:
        fc.stop()


def test_route_flight_does_not_wait_for_get_telemetry():
    """Полёты 11.11.2026, «нос по линии маршрута»: вызовы сервисов simple_offboard ждали DNS при слабом Wi-Fi —
    get_telemetry по 5–15 с на отсчёт, борт висел у точки перед разворотом 15–20 с, дважды сел посреди маршрута.
    Положение и курс программа берёт из топика /mavros/local_position/pose: зависший get_telemetry (6 с на запрос —
    дольше тайм-аута агента) полёт не задерживает и не обрывает."""
    fc = FakePX4(speed=2.0, yaw=math.radians(90), tel_delay={"map": 6.0, "navigate_target": 6.0})
    try:
        t0 = time.time()
        v, ok = _fly(fc, local_plan(yaw_mode="route"), lambda v: not fc.armed and v.snapshot()["wp_reached"] == 2, 40)
        assert ok and v._fly_error is None, v._fly_error
        assert fc.tel_calls == [] and time.time() - t0 < 25, (fc.tel_calls, time.time() - t0)
        assert len([y for s, y in fc.yaw_cmds if s == "set_yaw"]) == 2          # развороты на месте — как прежде
        assert ("land",) in fc.calls and abs(fc.x) < 0.3 and abs(fc.y) < 0.3
    finally:
        fc.stop()


def test_navigate_without_answer_is_repeated_not_landed(monkeypatch):
    """navigate к точке не ответил вовремя (на борту — при слабом Wi-Fi): повтор той же цели, а не посадка на месте.
    Борт тем временем держит прежнюю цель, а поздно дошедший первый вызов передаёт ту же точку."""
    import rospy
    monkeypatch.setattr(offboard, "NAV_TIMEOUT_S", 0.5)
    fc = FakePX4(speed=2.0, yaw=math.radians(90))
    real, sent = rospy._services["/navigate"], []

    def late(**kw):
        if kw["frame_id"] == "map":
            sent.append((round(kw["x"], 2), round(kw["y"], 2), round(kw["z"], 2)))
            if len(sent) == 2:
                time.sleep(1.0)                    # ответ и сама команда — позже тайм-аута агента
        return real(**kw)
    rospy._services["/navigate"] = late
    try:
        v, ok = _fly(fc, local_plan(yaw_mode="route"), lambda v: not fc.armed and v.snapshot()["wp_reached"] == 2, 40)
        assert ok and v._fly_error is None, v._fly_error
        assert len(sent) == 4 and sent[1] == sent[2], sent           # точка 2 — повтор той же цели, затем возврат
        assert ("land",) in fc.calls and abs(fc.x) < 0.3 and abs(fc.y) < 0.3
    finally:
        fc.stop()


def test_navigate_never_answering_still_lands(monkeypatch):
    """navigate не отвечает совсем: после NAV_TRIES попыток — посадка на месте, как прежде (а не бесконечное ожидание)."""
    import rospy
    monkeypatch.setattr(offboard, "NAV_TIMEOUT_S", 0.3)
    fc = FakePX4(speed=2.0)
    real, sent = rospy._services["/navigate"], []

    def dead(**kw):
        if kw["frame_id"] == "map":
            sent.append(kw["x"])
            time.sleep(1.0)
            return NS(success=False, message="late")
        return real(**kw)
    rospy._services["/navigate"] = dead
    try:
        v, ok = _fly(fc, local_plan(), lambda v: ("land",) in fc.calls, 20)
        assert ok and "нет ответа" in (v._fly_error or "") and len(sent) == offboard.NAV_TRIES
    finally:
        fc.stop()


def test_pose_topic_silent_position_from_get_telemetry():
    """Топик положения замолчал в полёте — положение и курс из get_telemetry, маршрут выполняется до конца."""
    fc = FakePX4(speed=2.0, yaw=math.radians(90))
    try:
        v = opened(fc)
        p = local_plan(yaw_mode="route")
        v.upload(p, {"lat": None, "lon": None})
        v.start_mission(p)
        assert wait(lambda: v.snapshot()["in_air"])
        fc.publish_pose = False
        assert wait(lambda: not fc.armed, 40) and v._fly_error is None, v._fly_error
        assert v.snapshot()["wp_reached"] == 2 and "map" in fc.tel_calls
        assert ("land",) in fc.calls and abs(fc.x) < 0.3 and abs(fc.y) < 0.3
    finally:
        fc.stop()


def test_heading_needs_set_yaw_and_valid_plan():
    fc = FakePX4(set_yaw=False)
    try:
        v = opened(fc)
        with pytest.raises(VehicleError, match="set_yaw"):
            v.upload(local_plan(yaw_mode="route"), {"lat": None, "lon": None})
        assert "курс" not in v.upload(local_plan(), {"lat": None, "lon": None})        # без курса — как прежде
    finally:
        fc.stop()
    eff = effective_limits(local_plan()["limits"], LIM, "local")
    validate(local_plan(yaw_mode="route", waypoints=[dict(local_plan()["waypoints"][0], yaw_deg=359.5)]), "T6S-1", eff)
    with pytest.raises(PlanError, match="курс"):
        validate(local_plan(waypoints=[dict(local_plan()["waypoints"][0], yaw_deg=400)]), "T6S-1", eff)
    with pytest.raises(PlanError, match="курс"):
        validate(local_plan(waypoints=[dict(local_plan()["waypoints"][0], yaw_deg="север")]), "T6S-1", eff)
    with pytest.raises(PlanError, match="режим курса"):
        validate(local_plan(yaw_mode="spin"), "T6S-1", eff)


def test_px4_params_explain_gps_and_landing(monkeypatch):
    fc = FakePX4(params={"EKF2_GPS_CTRL": 0, "SYS_HAS_MAG": 0, "COM_DISARM_LAND": -1.0})
    try:
        v = opened(fc)
        gp = local_plan(frame="global")
        checks = {c["name"]: c for c in v.extra_checks(gp)}
        assert not checks["GPS в навигации PX4"]["ok"] and "помещени" in checks["GPS в навигации PX4"]["detail"]
        assert not checks["Магнитометр"]["ok"]
        warn = checks["Выключение моторов после посадки"]
        assert warn["ok"] and warn["warn"]
        fc.params = {"EKF2_GPS_CTRL": 7, "SYS_HAS_MAG": 1, "COM_DISARM_LAND": 2.0}
        v._params_at = 0.0
        checks = {c["name"]: c for c in v.extra_checks(gp)}
        assert checks["GPS в навигации PX4"]["ok"] and "Магнитометр" not in checks and "Выключение моторов после посадки" not in checks
        monkeypatch.setattr(vehicle_ros, "service_active", lambda name: name == "technic-show")
        bad = [c for c in v.extra_checks(gp) if not c["ok"]]
        assert bad and "technic-show" in bad[0]["detail"]
        info = v.info()
        assert info["params"]["EKF2_GPS_CTRL"] == 7 and info["technic_show"] is True and info["simulation"] is False
    finally:
        fc.stop()
