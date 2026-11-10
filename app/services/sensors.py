"""Мониторинг состояния БПЛА по бортовым датчикам: падения и столкновения.

Источник — модуль ускорения |a| (в g) с акселерометра ИНС/полётного контроллера. Акселерометр измеряет
удельную силу: в висении |a| ≈ 1 g, в свободном падении ≈ 0 (так же определяет свободное падение детектор
посадки PX4 — по малой удельной силе в течение заданного времени). Прозрачные пороги (без машинного обучения):

* падение   — участок невесомости |a| < FREE_FALL_G длительностью ≥ FREE_FALL_MIN_S,
              за которым в течение IMPACT_WINDOW_S следует удар |a| ≥ FALL_IMPACT_G;
              запись, оборвавшаяся в невесомости (удар не записан: обрыв питания или связи), — тоже падение;
* столкновение — удар |a| ≥ COLLISION_G без предшествующей невесомости;
* посадка — удар без невесомости в последние LANDING_WINDOW_S с полёта или после его окончания (касание земли,
              выключение моторов) — не столкновение, если он слабее HARD_LANDING_G (сильнее — удар о землю,
              столкновение). Падение при посадке (невесомость, затем удар) остаётся падением.

Соседние срабатывания (от конца одного удара до начала следующего) ближе REFRACTORY_S — одно событие:
отскоки при падении не считаются отдельными ударами. «Невесомость» дольше FREE_FALL_MAX_S — не падение
(с сопротивлением воздуха перегрузка падающего борта за 1–2 с возвращается к 1 g), а неверные данные.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ..models import Installation, SensorEvent

FREE_FALL_G = 0.35
FREE_FALL_MIN_S = 0.2
FREE_FALL_MAX_S = 10.0
FALL_IMPACT_G = 3.0
COLLISION_G = 4.0
IMPACT_WINDOW_S = 1.5
REFRACTORY_S = 1.0
LANDING_WINDOW_S = 8.0     # конец полёта: снижение, касание, распознавание посадки контроллером (PX4: 1–3 с) и до
                           # выключения моторов (COM_DISARM_LAND 2 с); всё после окончания полёта — тоже посадка
HARD_LANDING_G = 10.0      # удар при посадке сильнее — уже не касание (обычно 1,5–6 g), а удар о землю
PX4_FREEFALL_G = 2.0 / 9.80665   # свободное падение по PX4 (MulticopterLandDetector): удельная сила < 2 м/с²
PX4_FREEFALL_S = 0.3             # … в течение 300 мс (FREEFALL_TRIGGER_TIME_US)
LOG_MATCH_S = 2.0          # событие бортового ПО и найденное по акселерометру ближе 2 с — одно и то же событие
KIND_LABELS = {"fall": "Падение", "collision": "Столкновение"}
NEUTRAL_G = 1.0            # отсчёт без значения (null, NaN, ±∞) не может ни создать, ни продлить событие


def magnitudes(samples) -> tuple[list[float], int]:
    """[|a|, …] или [[ax, ay, az], …] в g → (|a|, число отсчётов без значения).

    Отсчёт без значения (null, NaN, ±∞) заменяется нейтральным 1 g и считается — один сбойный отсчёт не должен
    ни отменять анализ всего полёта, ни порождать событие. Нечисловые отсчёты и векторы не из трёх чисел —
    ValueError (формат не распознан: например, пары [t, |a|] нельзя принять за вектор).
    """
    out, skipped = [], 0
    for s in samples:
        if s is None:
            out.append(NEUTRAL_G)
            skipped += 1
            continue
        if isinstance(s, (list, tuple)):
            if len(s) != 3:
                raise ValueError("вектор ускорения должен состоять из трёх чисел [ax, ay, az]")
            vals = [_scalar(v) for v in s]
            if any(v is None or not math.isfinite(v) for v in vals):
                out.append(NEUTRAL_G)
                skipped += 1
                continue
            m = math.sqrt(sum(v * v for v in vals))
        else:
            m = _scalar(s)
            if m is None or not math.isfinite(m):
                out.append(NEUTRAL_G)
                skipped += 1
                continue
            m = abs(m)
        if not math.isfinite(m):              # переполнение при возведении в квадрат огромных значений
            out.append(NEUTRAL_G)
            skipped += 1
            continue
        out.append(m)
    return out, skipped


def _scalar(v):
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        raise ValueError("отсчёт должен быть числом")
    try:
        return float(v)
    except (ValueError, OverflowError):
        raise ValueError(f"«{str(v)[:20]}» — не число") from None


def detect(samples, rate_hz: float) -> list[dict]:
    """Возвращает [{"t": секунды от начала записи, "kind": "fall"|"collision", "peak_g": … | None}]."""
    if not samples or rate_hz <= 0:
        return []
    return _detect(magnitudes(samples)[0], rate_hz)


def _detect(a: list[float], rate_hz: float) -> list[dict]:
    dt = 1.0 / rate_hz
    ff_need = max(1, math.ceil(FREE_FALL_MIN_S * rate_hz - 1e-9))     # не короче 0,2 с при любой частоте
    ff_max = max(ff_need, int(FREE_FALL_MAX_S * rate_hz))
    events, i, n = [], 0, len(a)
    last_ff_end = None          # индекс последнего отсчёта засчитанной невесомости
    last_end_t = None           # время последнего отсчёта удара в последнем событии
    run = 0
    while i < n:
        if a[i] < FREE_FALL_G:
            run += 1
            if run > ff_max:
                last_ff_end = None              # «невесомость» дольше 10 с — неверные данные, не падение
            elif run >= ff_need:
                last_ff_end = i
        else:
            run = 0
        impact_thr = FALL_IMPACT_G if last_ff_end is not None and (i - last_ff_end) * dt <= IMPACT_WINDOW_S else COLLISION_G
        if a[i] >= impact_thr:
            j = i
            while j + 1 < n and a[j + 1] >= impact_thr:   # пик удара
                j += 1
            peak = round(max(a[i:j + 1]), 2)
            kind = "fall" if impact_thr == FALL_IMPACT_G else "collision"
            t = i * dt
            if events and last_end_t is not None and t - last_end_t < REFRACTORY_S:
                prev = events[-1]
                if prev["peak_g"] is None or peak > prev["peak_g"]:
                    prev["peak_g"] = peak
                if kind == "fall":
                    prev["kind"] = "fall"
            else:
                events.append({"t": round(t, 3), "kind": kind, "peak_g": peak})
            last_end_t = j * dt
            last_ff_end = None
            run = 0
            i = j + 1
            continue
        i += 1
    # Запись оборвалась в невесомости — удар не записан (обрыв питания или связи при ударе). Подтверждающего удара
    # нет, поэтому критерий строже — как у детектора свободного падения PX4 (MulticopterLandDetector):
    # удельная сила < 2 м/с² не менее 300 мс.
    tail = 0
    while tail < n and a[n - 1 - tail] < PX4_FREEFALL_G:
        tail += 1
    if max(1, math.ceil(PX4_FREEFALL_S * rate_hz - 1e-9)) <= tail <= ff_max:
        t = (n - tail) * dt
        if events and last_end_t is not None and t - last_end_t < REFRACTORY_S:
            events[-1]["kind"] = "fall"         # удар, затем падение (например, столкновение с препятствием)
            events[-1]["unrecorded_impact"] = True
        else:
            events.append({"t": round(t, 3), "kind": "fall", "peak_g": None, "unrecorded_impact": True})
    return events


def count_events(s: Session, kind_entity: str, entity_id: int, since: datetime | None = None,
                 before: datetime | None = None) -> int:
    """Число событий борта или узла (по истории установок: узел «переживает» удар только на том борту, где стоял)."""
    if kind_entity == "aircraft":
        q = select(func.count(SensorEvent.id)).where(SensorEvent.aircraft_id == entity_id)
    else:
        # distinct: событие считается один раз, даже если ему соответствуют две записи установки
        q = select(func.count(func.distinct(SensorEvent.id))).join(Installation, and_(
            Installation.aircraft_id == SensorEvent.aircraft_id,
            Installation.installed_at <= SensorEvent.occurred_at,
            or_(Installation.removed_at.is_(None), SensorEvent.occurred_at < Installation.removed_at),
        )).where(Installation.component_id == entity_id)
    if since is not None:
        q = q.where(SensorEvent.occurred_at >= since)
    if before is not None:
        q = q.where(SensorEvent.occurred_at < before)
    return s.scalar(q) or 0


def counts_by_kind(s: Session, aircraft_id: int) -> dict:
    rows = s.execute(select(SensorEvent.kind, func.count(SensorEvent.id)).where(SensorEvent.aircraft_id == aircraft_id)
                     .group_by(SensorEvent.kind))
    d = {"fall": 0, "collision": 0}
    d.update({k: n for k, n in rows})
    return d


def event_dict(e: SensorEvent) -> dict:
    from ..timeutil import iso
    return {"id": e.id, "flight_id": e.flight_id, "aircraft_id": e.aircraft_id, "occurred_at": iso(e.occurred_at),
            "kind": e.kind, "kind_label": KIND_LABELS.get(e.kind, e.kind), "peak_g": e.peak_g, "source": e.source,
            "details": e.details}


def _num(v, what: str) -> float:
    if isinstance(v, bool):
        raise ValueError(f"{what}: ожидается число, получено «{v}»")
    try:
        x = float(v)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{what}: ожидается число, получено «{str(v)[:30]}»") from None
    if math.isnan(x) or math.isinf(x):
        raise ValueError(f"{what}: недопустимое значение {v}")
    return x


def is_touchdown(ev: dict, off: float, landing_from: float | None) -> bool:
    """Удар без невесомости в конце полёта слабее HARD_LANDING_G — касание земли при посадке, а не столкновение."""
    return (landing_from is not None and ev["kind"] == "collision" and off >= landing_from
            and ev["peak_g"] is not None and ev["peak_g"] < HARD_LANDING_G)


def _imu_events(imu, landing_from: float | None = None) -> list[dict]:
    """События по выборке акселерометра: [{"off": с от взлёта, "kind", "peak_g", "source", "details"}].

    landing_from — с какого момента (с от начала полёта) идёт посадка: удары без невесомости слабее HARD_LANDING_G
    после него — касание земли, не события."""
    if imu is None:
        return []
    if not isinstance(imu, dict):
        raise ValueError("поле imu должно быть объектом {rate_hz, t0_s, accel_g}")
    samples = imu.get("accel_g")
    if not samples:
        return []
    rate = imu.get("rate_hz")
    rate = 50.0 if rate in (None, "") else _num(rate, "imu.rate_hz")    # 0 — ошибка, а не «по умолчанию 50 Гц»
    if not 1 <= rate <= 10000:
        raise ValueError("imu.rate_hz вне диапазона 1–10000 Гц")
    t0 = imu.get("t0_s")
    t0 = 0.0 if t0 in (None, "") else _num(t0, "imu.t0_s")
    if not isinstance(samples, list):
        raise ValueError("imu.accel_g должно быть массивом")
    if len(samples) > 2_000_000:
        raise ValueError("imu.accel_g: слишком много отсчётов (более 2 млн)")
    try:
        mags, skipped = magnitudes(samples)
    except (TypeError, ValueError, OverflowError) as e:
        raise ValueError(f"imu.accel_g: отсчёты должны быть числами или тройками чисел [ax, ay, az] ({e})") from None
    base = f"анализ акселерометра, {rate:g} Гц" + (f"; отсчётов без значения: {skipped}" if skipped else "")
    out = []
    for ev in _detect(mags, rate):
        off = t0 + ev["t"]
        if is_touchdown(ev, off, landing_from):
            continue
        details = base + ("; свободное падение до конца записи — удар не записан (обрыв питания или связи)"
                          if ev.get("unrecorded_impact") else "")
        if landing_from is not None and ev["kind"] == "collision" and off >= landing_from:
            details += f"; удар при посадке ≥ {HARD_LANDING_G:g} g — не касание, а удар о землю"
        out.append({"off": off, "kind": ev["kind"], "peak_g": ev["peak_g"], "source": "imu", "details": details})
    return out


def _log_events(events) -> list[dict]:
    """Готовые события бортового ПО: [{"type": "fall"|"collision", "t_s", "peak_g"}]."""
    if events in (None, ""):
        return []
    if not isinstance(events, list):
        raise ValueError("поле events должно быть массивом")
    out = []
    for ev in events:
        if not isinstance(ev, dict):
            raise ValueError("элементы events должны быть объектами {type, t_s, peak_g}")
        kind = {"fall": "fall", "падение": "fall", "collision": "collision", "столкновение": "collision"}.get(str(ev.get("type", "")).lower())
        if not kind:
            continue
        t = ev.get("t_s")
        off = 0.0 if t in (None, "") else _num(t, "events.t_s")
        peak = ev.get("peak_g")
        peak = None if peak in (None, "") else round(_num(peak, "events.peak_g"), 2)
        if peak is not None and peak < 0:
            raise ValueError("events.peak_g: перегрузка не может быть отрицательной")
        out.append({"off": off, "kind": kind, "peak_g": peak, "source": "log", "details": "событие бортового журнала"})
    return out


def analyze_flight(flight_start: datetime, raw: dict | None, flight_end: datetime | None = None) -> tuple[list[dict], list[str]]:
    """События полёта по выборке акселерометра, готовым событиям бортового ПО и счётчикам «Падения» / «Столкновения».

    Возвращает (события, предупреждения). Части анализируются независимо: ошибка в одной (например, в events)
    не отменяет другие и не отменяет учёт полёта. Одно физическое событие не считается дважды:
    событие бортового ПО ближе LOG_MATCH_S к найденному по акселерометру объединяется с ним, а счётчик
    в журнале — это общее число событий своего вида за полёт (добавляются только недостающие).
    Время события ограничивается интервалом полёта. Удар без невесомости в последние LANDING_WINDOW_S с полёта
    и после его окончания слабее HARD_LANDING_G — касание земли при посадке, не столкновение.
    """
    if not raw:
        return [], []
    span = (flight_end - flight_start).total_seconds() if flight_end else None
    warnings: list[str] = []
    found: list[dict] = []
    try:
        found = _imu_events(raw.get("imu"), None if span is None else max(0.0, span - LANDING_WINDOW_S))
    except ValueError as e:
        warnings.append(str(e))
    try:
        logged = _log_events(raw.get("events"))
    except ValueError as e:
        warnings.append(str(e))
        logged = []
    matched: set[int] = set()
    for ev in logged:
        best = None
        for idx, f in enumerate(found):
            if f["source"] == "imu" and idx not in matched and abs(f["off"] - ev["off"]) <= LOG_MATCH_S:
                if best is None or abs(f["off"] - ev["off"]) < abs(found[best]["off"] - ev["off"]):
                    best = idx
        if best is None:
            found.append(ev)
            continue
        matched.add(best)
        f = found[best]
        if ev["kind"] == "fall":
            f["kind"] = "fall"
        if ev["peak_g"] is not None and (f["peak_g"] is None or ev["peak_g"] > f["peak_g"]):
            f["peak_g"] = ev["peak_g"]
        f["details"] += " · подтверждено бортовым журналом"
    for key, kind, label, what in (("collisions", "collision", "Столкновения", "столкновений"),
                                   ("falls", "fall", "Падения", "падений")):   # счётчики из CSV/Excel/JSON
        v = raw.get(key)
        if v in (None, ""):
            continue
        try:
            x = _num(v, key)
        except ValueError:
            warnings.append(f"колонка «{label}»: ожидается число, получено «{str(v)[:30]}»")
            continue
        if x != int(x) or not 0 <= x <= 50:
            warnings.append(f"{what} за полёт: {v} — допустимо целое 0–50")
            continue
        have = sum(1 for f in found if f["kind"] == kind)
        for _ in range(int(x) - have):
            found.append({"off": 0.0, "kind": kind, "peak_g": None, "source": "log", "details": "счётчик в журнале"})
    out = []
    for f in sorted(found, key=lambda e: e["off"]):
        off = max(0.0, f["off"]) if span is None else min(max(0.0, f["off"]), span)
        out.append({"at": flight_start + timedelta(seconds=off), "kind": f["kind"], "peak_g": f["peak_g"],
                    "source": f["source"], "details": f["details"]})
    return out, warnings
