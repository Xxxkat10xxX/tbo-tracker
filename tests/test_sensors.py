"""Падения и столкновения (К3): детектор, разбор данных датчиков, импорт — регрессии найденных ошибок."""
import json
from datetime import datetime, timedelta, timezone

from app.services.sensors import analyze_flight, detect
from tests.conftest import by_serial, due

H = [1.0] * 50
T0 = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(minutes=30)


def test_bounces_are_one_event():
    # удары каждые 0,9 с — одно событие: соседние срабатывания ближе 1 с (прежде мерилось от первого удара → 2)
    assert len(detect(H + [5.0] + [1.0] * 44 + [5.0] + [1.0] * 44 + [5.0] + H, 50)) == 1
    # затяжной удар 0,5 с и пик через 0,6 с после его конца — одно событие (отсчёт от конца удара)
    assert len(detect(H + [5.0] * 25 + [1.0] * 30 + [5.0] + H, 50)) == 1
    assert len(detect(H + [5.0] + [1.0] * 60 + [5.0] + H, 50)) == 2          # через 1,2 с — новое событие


def test_free_fall_lasts_at_least_02_s_at_any_rate():
    for rate in (7, 12.5, 25, 50, 400):
        n = 0
        while not detect([1.0] * 10 + [0.1] * n + [3.5] + [1.0] * 10, rate):
            n += 1
        assert n / rate >= 0.2 - 1e-9, rate                                  # прежде 7 Гц: 0,14 с; 12,5 Гц: 0,16 с


def test_unrecorded_impact_and_invalid_weightlessness():
    ev = detect(H * 3 + [0.05] * 50, 50)                         # запись оборвалась после 1 с невесомости
    assert [(e["kind"], e["peak_g"], e["t"]) for e in ev] == [("fall", None, 3.0)]
    assert detect(H * 3 + [0.25] * 50, 50) == []                 # 0,25 g — не невесомость по критерию PX4 (< 2 м/с²)
    assert detect(H * 3 + [0.05] * 10, 50) == []                 # 0,2 с — меньше 300 мс PX4
    assert detect([0.0] * 50 * 60, 50) == []                     # минута «невесомости» — неверные данные, не падение
    assert [e["kind"] for e in detect([0.0] * 50 * 60 + [5.0] + H, 50)] == ["collision"]
    hit_then_fall = detect(H + [6.0] + [0.05] * 40, 50)           # удар о препятствие, затем падение без записи удара
    assert [(e["kind"], e["peak_g"]) for e in hit_then_fall] == [("fall", 6.0)]


def test_sample_formats_and_bad_values():
    assert len(detect([[0, 0, 1.0]] * 50 + [[0, 3, 4]] + [[0, 0, 1.0]] * 50, 50)) == 1     # тройки [ax, ay, az]
    for bad in ([[0.0, 1.0]] * 100, [[1, 2, 3, 4]], [True], [{"g": 1}], ["x"]):           # пары [t, |a|] — не вектор
        evs, warns = analyze_flight(T0, {"imu": {"rate_hz": 50, "accel_g": bad}}, T1)
        assert evs == [] and warns and "imu.accel_g" in warns[0], bad
    # NaN, ±∞, null — отсчёты без значения: не событие и не отмена анализа всего полёта
    raw = {"imu": {"rate_hz": 50, "accel_g": H + [float("inf"), float("nan"), None] + H + [5.0] + H}}
    evs, warns = analyze_flight(T0, raw, T1)
    assert not warns and [(e["kind"], e["peak_g"]) for e in evs] == [("collision", 5.0)]
    assert "без значения: 3" in evs[0]["details"]
    for rate in (0, -5, "abc"):                                      # 0 Гц — ошибка, а не «по умолчанию 50 Гц»
        evs, warns = analyze_flight(T0, {"imu": {"rate_hz": rate, "accel_g": H + [5.0] + H}}, T1)
        assert evs == [] and "rate_hz" in warns[0], rate


def test_same_physical_event_counted_once():
    imu = {"rate_hz": 50, "t0_s": 300, "accel_g": H + [0.1] * 20 + [7.0] + H}       # падение на 301,4 с
    evs, warns = analyze_flight(T0, {"imu": imu, "events": [{"type": "collision", "t_s": 301.0, "peak_g": 9.1}]}, T1)
    assert not warns and len(evs) == 1
    assert (evs[0]["kind"], evs[0]["peak_g"], evs[0]["source"]) == ("fall", 9.1, "imu") and "бортовым журналом" in evs[0]["details"]
    evs, _ = analyze_flight(T0, {"imu": imu, "events": [{"type": "fall", "t_s": 900}]}, T1)   # далеко по времени — другое
    assert len(evs) == 2
    evs, _ = analyze_flight(T0, {"imu": imu, "falls": 1, "collisions": 1}, T1)               # счётчик — всего за полёт
    assert sorted(e["kind"] for e in evs) == ["collision", "fall"]
    evs, _ = analyze_flight(T0, {"falls": 2}, T1)
    assert [e["kind"] for e in evs] == ["fall", "fall"]


def test_bad_part_does_not_discard_the_rest():
    raw = {"imu": {"rate_hz": 50, "accel_g": H + [5.0] + H}, "events": [{"type": "fall", "peak_g": "big"}]}
    evs, warns = analyze_flight(T0, raw, T1)
    assert [e["kind"] for e in evs] == ["collision"] and len(warns) == 1 and "events.peak_g" in warns[0]
    for bad in (1.5, -1, 51, "inf", True):
        evs, warns = analyze_flight(T0, {"falls": bad}, T1)
        assert evs == [] and warns, bad
    evs, warns = analyze_flight(T0, {"events": [{"type": "fall", "t_s": 10, "peak_g": -3}]}, T1)
    assert evs == [] and "отрицательной" in warns[0]


def _imp(client, content: str, name):
    if not any(a["serial"] == "K3-1" for a in client.get("/api/aircraft").json()):
        tid = client.get("/api/meta").json()["aircraft_types"][0]["id"]
        client.post("/api/aircraft", json={"serial": "K3-1", "name": "Борт К3", "type_id": tid, "in_service_at": "2026-01-01T00:00:00Z"})
    return client.post("/api/imports", files={"file": (name, content.encode())}).json()


def test_infinity_in_log_does_not_break_pages(client):
    # json.loads принимает Infinity/NaN: прежде пик inf сохранялся, и страницы с событиями отдавали 500
    r = _imp(client, '{"aircraft_serial": "K3-1", "flights": [{"flight_id": "K3-INF", "start": "2026-10-05T08:00:00Z", '
                     '"end": "2026-10-05T08:20:00Z", "imu": {"rate_hz": 50, "accel_g": [1.0, Infinity, 1.0, NaN, 6.0, 1.0]}}]}',
             "inf.json")
    assert r["status"] == "imported" and [e["peak_g"] for e in r["summary"]["sensor_events"]] == [6.0]
    a = by_serial(client, "K3-1")
    assert a["events"] == {"fall": 0, "collision": 1}
    for url in (f"/api/aircraft/{a['id']}", "/api/notifications", "/api/dashboard"):
        assert client.get(url).status_code == 200, url


def test_log_with_imu_and_onboard_event_counts_once_and_triggers_inspection(client):
    flight = {"flight_id": "K3-DUP", "start": "2026-10-06T08:00:00Z", "end": "2026-10-06T08:20:00Z",
              "imu": {"rate_hz": 50, "t0_s": 600, "accel_g": H + [0.1] * 20 + [7.0] + H},
              "events": [{"type": "fall", "t_s": 601.3, "peak_g": 7.0}], "falls": 1}
    r = _imp(client, json.dumps({"aircraft_serial": "K3-1", "flights": [flight]}), "dup.json")
    assert r["status"] == "imported" and len(r["summary"]["sensor_events"]) == 1
    assert by_serial(client, "K3-1")["events"]["fall"] == 1
    assert due(client, "K3-1", "AC-CRASH")["status"] == "overdue"


def test_agent_log_with_odd_sensor_values_is_accepted():
    # 422 на журнал агента = журнал в rejected/ и полёт без учёта; данные датчиков проверяет анализ (предупреждение)
    from app.api.agent import LogIn
    log = LogIn.model_validate({"status": "completed", "flight": {
        "start": "2026-10-07T08:00:00Z", "end": "2026-10-07T08:10:00Z",
        "imu": {"rate_hz": 0, "t0_s": -100, "accel_g": [1.0, None, 1.0]},
        "events": [{"type": "something-new", "t_s": -5, "peak_g": 900}]}})
    assert log.flight.imu.accel_g[1] is None and log.flight.events[0].peak_g == 900


def test_agent_log_with_nan_samples_is_accepted():
    from app.api.agent import LogIn
    log = LogIn.model_validate_json('{"status": "completed", "flight": {"start": "2026-10-07T08:00:00Z", "end": "2026-10-07T08:10:00Z",'
                                    '"imu": {"rate_hz": 25, "accel_g": [1.0, NaN, Infinity, 1.0]},'
                                    '"events": [{"type": "fall", "t_s": 1, "peak_g": NaN}]}}')
    assert len(log.flight.imu.accel_g) == 4
    # трек (только для карты) и свободный итог: сбойные значения не отклоняют журнал — полёт учитывается
    log = LogIn.model_validate_json('{"status": "completed", "track": [[NaN, 54.9, 38.2, 10, 5, "mission"], '
                                    '[1, 54.9, 38.2, 10, 5, "mission"]], "summary": {"min_voltage": NaN, "x": [Infinity]}}')
    assert len(log.track) == 1 and log.summary == {"min_voltage": None, "x": [None]}


def test_landing_touchdown_is_not_collision():
    """Касание земли при посадке и выключение моторов (конец полёта) — не столкновение; падение при посадке — падение."""
    end = (T1 - T0).total_seconds()
    hit = H + [5.0] + H                                                   # удар 5 g через 1 с от начала выборки
    mid, _ = analyze_flight(T0, {"imu": {"rate_hz": 50, "t0_s": 600, "accel_g": hit}}, T1)
    assert [(e["kind"], e["peak_g"]) for e in mid] == [("collision", 5.0)]                     # в полёте — столкновение
    for t0 in (end - 6, end - 1, end + 3):                               # касание за 5 с до конца, в конце, после
        evs, warns = analyze_flight(T0, {"imu": {"rate_hz": 50, "t0_s": t0, "accel_g": hit}}, T1)
        assert evs == [] and not warns, t0
    # запись агента: от взлёта до выключения моторов — касание 5,5 g чуть позже распознанной посадки (конец полёта)
    rec = [1.0] * (50 * 60) + [1.0] * 25 + [5.5, 2.0] + [1.0] * 100
    evs, _ = analyze_flight(T0, {"imu": {"rate_hz": 50, "t0_s": 0, "accel_g": rec}}, T0 + timedelta(seconds=60.4))
    assert evs == []
    # удар о землю сильнее 10 g — столкновение и при посадке
    evs, _ = analyze_flight(T0, {"imu": {"rate_hz": 50, "t0_s": end - 2, "accel_g": H + [12.0] + H}}, T1)
    assert [(e["kind"], e["peak_g"]) for e in evs] == [("collision", 12.0)] and "при посадке" in evs[0]["details"]
    # падение при посадке (невесомость 0,4 с, затем удар) — падение
    evs, _ = analyze_flight(T0, {"imu": {"rate_hz": 50, "t0_s": end - 3, "accel_g": H + [0.1] * 20 + [5.0] + H}}, T1)
    assert [e["kind"] for e in evs] == ["fall"]
    # событие бортового ПО «столкновение» при посадке учитывается: его отметил сам контроллер
    evs, _ = analyze_flight(T0, {"imu": {"rate_hz": 50, "t0_s": end - 2, "accel_g": hit},
                                 "events": [{"type": "collision", "t_s": end - 1, "peak_g": 5.0}]}, T1)
    assert [(e["kind"], e["source"]) for e in evs] == [("collision", "log")]
