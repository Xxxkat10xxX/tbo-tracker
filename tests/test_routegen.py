"""Шаблоны маршрутов (services/routegen): съёмка площади с перекрытием, двойная сетка, периметр, облёт по кругу."""
import math

import pytest

from app.errors import DomainError
from app.services import autopilot
from app.services.routegen import footprint, generate

HOME = (54.9512, 38.2034)


def params(**kw):
    p = dict(kind="survey", home_lat=HOME[0], home_lon=HOME[1], frame="local", takeoff_alt=1.5, speed_ms=0.8, return_home=True,
             center_x=0.0, center_y=3.0, width=4.5, length=7.5, angle_deg=0.0, alt=1.5, fov_across_deg=88.0, fov_along_deg=72.0,
             image_px_across=320, side_overlap=0.6, front_overlap=0.7, photo_points=False, photo_hold_s=1.0, radius=2.0,
             points=12, direction="cw", laps=1, face_center=True)
    p.update(kw)
    return p


def uv(p, xy):
    """Точка в осях участка: вдоль галсов (a) и поперёк (b) от центра."""
    a = math.radians(p["angle_deg"])
    dx, dy = xy[0] - p["center_x"], xy[1] - p["center_y"]
    return dx * math.sin(a) + dy * math.cos(a), dx * math.cos(a) - dy * math.sin(a)


def test_survey_spacing_overlap_and_coverage():
    p = params()
    r = generate(p)
    fp = footprint(1.5, 88)
    assert r["info"]["footprint_m"][0] == pytest.approx(fp, abs=0.01)
    assert r["info"]["line_spacing_m"] == pytest.approx(fp * 0.4, abs=0.01)                 # боковое перекрытие 60 %
    assert r["info"]["photo_spacing_m"] == pytest.approx(footprint(1.5, 72) * 0.3, abs=0.01)
    offs = sorted({round(uv(p, q)[1], 3) for q in r["xy"]})
    assert len(offs) == r["info"]["lines"] == 3
    assert all(b - a == pytest.approx(r["info"]["line_spacing_m"], abs=0.02) for a, b in zip(offs, offs[1:]))
    assert offs[0] - fp / 2 <= -p["width"] / 2 + 1e-6 and offs[-1] + fp / 2 >= p["width"] / 2 - 1e-6   # края закрыты
    assert offs[0] == pytest.approx(-offs[-1], abs=0.01)                                   # галсы — симметрично
    assert len(r["waypoints"]) == 6 and r["yaw_mode"] == "keep"                            # концы галсов
    assert r["waypoints"][0]["yaw_deg"] == 0.0 and all("yaw_deg" not in w for w in r["waypoints"][1:])
    assert r["info"]["gsd_cm"] == pytest.approx(fp / 320 * 100, abs=0.01)
    ends = [math.dist(q, (0, 0)) for q in r["xy"]]
    assert ends[0] == pytest.approx(min(ends[0], ends[1], ends[-2], ends[-1]))             # старт — у точки взлёта
    for a, b in zip(r["xy"][0::2], r["xy"][1::2]):                                         # змейка: галсы туда и обратно
        assert abs(uv(p, a)[0]) == pytest.approx(p["length"] / 2, abs=0.01) and uv(p, a)[0] == pytest.approx(-uv(p, b)[0], abs=0.01)
    autopilot.validate({**p, "yaw_mode": r["yaw_mode"], "waypoints": r["waypoints"]})


def test_survey_photo_points_and_rotation():
    p = params(photo_points=True, photo_hold_s=1.5, angle_deg=30.0, width=6.0, length=5.0)
    r = generate(p)
    per_line = len(r["xy"]) // r["info"]["lines"]
    assert len(r["xy"]) == r["info"]["photos"] == per_line * r["info"]["lines"]
    assert all(w["hold_s"] == 1.5 for w in r["waypoints"])
    for q in r["xy"]:                                                                      # внутри повёрнутого участка
        a, b = uv(p, q)
        assert abs(a) <= p["length"] / 2 + 0.01 and abs(b) <= p["width"] / 2 + 0.01         # xy — с точностью 1 см
    line = r["xy"][:per_line]
    steps = [math.dist(x, y) for x, y in zip(line, line[1:])]
    assert max(steps) <= r["info"]["photo_spacing_m"] + 0.01
    assert r["waypoints"][0]["yaw_deg"] == 30.0


def test_crosshatch_second_grid_is_perpendicular():
    p = params(kind="crosshatch", angle_deg=10.0)
    r = generate(p)
    yaws = [(i, w["yaw_deg"]) for i, w in enumerate(r["waypoints"]) if "yaw_deg" in w]
    assert yaws[0] == (0, 10.0) and yaws[1][1] == 100.0
    k = yaws[1][0]
    a0, b0 = r["xy"][k], r["xy"][k + 1]
    assert abs(uv(p, a0)[0] - uv(p, b0)[0]) < 0.01                                          # второй галс — поперёк первых
    assert math.dist(r["xy"][k - 1], a0) < math.dist(r["xy"][k - 1], r["xy"][-1])          # вторая сетка — от конца первой


def test_orbit_nose_to_center_and_perimeter():
    p = params(kind="orbit", center_x=3.0, center_y=0.0, radius=2.0, points=8, laps=2)
    r = generate(p)
    assert len(r["xy"]) == 8 * 2 + 1 and r["xy"][0] == r["xy"][-1]
    assert all(math.dist(q, (3, 0)) == pytest.approx(2.0, abs=0.01) for q in r["xy"])
    assert r["xy"][0] == pytest.approx([1.0, 0.0], abs=0.01)                               # ближайшая к взлёту точка
    assert r["xy"][1][1] > 0                                                               # по часовой: с запада на север
    for q, w in zip(r["xy"], r["waypoints"]):                                              # нос к центру
        want = math.degrees(math.atan2(3 - q[0], 0 - q[1])) % 360
        assert abs((w["yaw_deg"] - want + 180) % 360 - 180) < 0.2
    assert r["yaw_mode"] == "keep"
    per = generate(params(kind="perimeter", direction="ccw"))
    assert len(per["xy"]) == 5 and per["xy"][0] == per["xy"][-1] and per["yaw_mode"] == "route"
    assert per["xy"][1][0] > per["xy"][0][0]                                               # против часовой: вправо по низу


def test_limits_and_errors():
    with pytest.raises(DomainError, match="больше 200"):
        generate(params(photo_points=True, width=20.0, length=20.0, alt=0.6, side_overlap=0.8, front_overlap=0.8))
    with pytest.raises(DomainError, match="Перекрытие"):
        generate(params(side_overlap=0.999))
    with pytest.raises(DomainError, match="от точки взлёта"):
        generate(params(center_y=150.0))                                                   # локально — до 100 м
    assert generate(params(alt=4.0))["warnings"]                                           # выше предела агента 3 м


def test_pattern_api_and_mission_roundtrip(client):
    body = params(frame="global", takeoff_alt=30.0, speed_ms=8.0, center_y=80.0, width=60.0, length=100.0, alt=20.0, angle_deg=45.0)
    r = client.post("/api/missions/pattern", json=body)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["stats"]["waypoints"] == len(d["waypoints"]) and d["info"]["lines"] >= 2
    m = client.post("/api/missions", json={"name": "Съёмка участка", "home_lat": HOME[0], "home_lon": HOME[1], "takeoff_alt": 30,
                                          "speed_ms": 8, "yaw_mode": d["yaw_mode"], "waypoints": d["waypoints"]})
    assert m.status_code == 201, m.text
    assert client.get(f"/api/missions/{m.json()['id']}").json()["waypoints"][0]["yaw_deg"] == 45.0
    bad = client.post("/api/missions/pattern", json=dict(body, photo_points=True, alt=5.0, width=400.0, length=400.0))
    assert bad.status_code == 400 and "200" in bad.json()["detail"]
