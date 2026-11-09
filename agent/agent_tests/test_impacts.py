"""Удары и падения на борту: регистратор акселерометра, поток IMU из MAVROS, проверка потока перед полётом."""
import time
from types import SimpleNamespace as NS

from tbo_agent.recorder import Recorder
from tbo_agent.vehicle_ros import RosVehicle

ACC_CHECK = "Акселерометр (падения и столкновения)"


def _rec(samples, t_end=4.0):
    rec = Recorder(0.0)
    rec.sample(0.0, {"in_air": True}, [], "in_flight")          # взлёт в t = 0
    rec.sample(t_end, {"in_air": True}, samples, "in_flight")
    rec.landed(t_end)
    return rec.imu


def test_stream_gap_is_neutral_not_last_value():
    # один провал 0,3 g, пропуск потока 0,36 с, толчок 3,5 g: прежде пропуск заполнялся последним значением,
    # получалась «невесомость» 0,4 с, и толчок становился падением
    imu = _rec([(i / 50, 1.0) for i in range(100)] + [(2.0, 0.3), (2.36, 3.5)] + [(2.36 + i / 50, 1.0) for i in range(1, 50)])
    assert imu[50] == 0.3 and imu[51:59] == [1.0] * 8 and imu[59] == 3.5
    # пики и провалы в пределах интервала 40 мс сохраняются (значение с наибольшим отклонением от 1 g)
    imu = _rec([(1.000, 1.1), (1.010, 0.05), (1.020, 0.9), (2.000, 1.2), (2.010, 7.5), (2.020, 0.8)])
    assert imu[25] == 0.05 and imu[50] == 7.5


def test_glitches_are_clipped_and_ground_hit_kept():
    imu = _rec([(1.0, 1e9), (1.04, float("inf")), (1.08, float("nan")), (1.12, 1.0)])
    assert imu[25] == 200.0 and imu[26] == 1.0 and max(imu) == 200.0       # сбой датчика не превращается в 1e9 g
    rec = Recorder(10.0)                                                  # арминг в t = 10
    rec.sample(10.5, {"in_air": False}, [(3.0, 5.0), (10.1, 1.0), (10.2, 6.3), (10.3, 1.0)], "starting")
    assert not rec.took_off and rec.ground_peak == 6.3 and rec.imu == []  # опрокинулся при запуске
    rec = Recorder(10.0)
    rec.sample(10.5, {"in_air": False}, [(3.0, 5.0), (10.1, 1.0)], "starting")   # толчок, пока борт несли, — до арминга
    assert rec.ground_peak == 1.0


def test_mavros_zero_vector_is_not_free_fall():
    v = RosVehicle("auto")
    v._on_imu(NS(linear_acceleration=NS(x=0.0, y=0.0, z=0.0)))          # MAVROS без HIGHRES_IMU отдаёт нули
    v._on_imu(NS(linear_acceleration=NS(x=float("nan"), y=0.0, z=9.8)))
    assert v.drain_imu() == [] and v._acc_last is None
    v._on_imu(NS(linear_acceleration=NS(x=0.0, y=0.0, z=9.80665)))
    (t, g), = v.drain_imu()
    assert abs(g - 1.0) < 1e-9


def test_imu_time_from_message_stamp_and_monotonic():
    v = RosVehicle("auto")
    now = [100.0]
    v.rospy = NS(get_time=lambda: now[0])
    stamp = lambda s: NS(header=NS(stamp=NS(to_sec=lambda: s)), linear_acceleration=NS(x=0.0, y=0.0, z=9.80665))  # noqa: E731
    v._on_imu(stamp(99.80))                  # пачка: три сообщения пришли разом, измерены с шагом 0,1 с
    v._on_imu(stamp(99.90))
    v._on_imu(stamp(100.00))
    t = [x[0] for x in v.drain_imu()]
    assert 0.17 < t[2] - t[0] < 0.23 and t == sorted(t)
    v._on_imu(stamp(50.0))                   # метка из прошлого (нет синхронизации) — время прихода, не назад
    assert v.drain_imu()[0][0] >= t[2]


def test_accelerometer_stream_check():
    v = RosVehicle("auto")
    plan = {"frame": "local"}
    assert ACC_CHECK in {c["name"] for c in v.extra_checks(plan)}           # данных нет вовсе
    v._on_imu(NS(linear_acceleration=NS(x=0.0, y=0.0, z=9.80665)))
    assert ACC_CHECK not in {c["name"] for c in v.extra_checks(plan)}
    v._acc_last = (time.monotonic() - 10, 1.0)                              # поток пропал
    c = {c["name"]: c for c in v.extra_checks(plan)}[ACC_CHECK]
    assert c["ok"] and c["warn"] and "HIGHRES_IMU" in c["detail"]           # предупреждение, старт не запрещён
