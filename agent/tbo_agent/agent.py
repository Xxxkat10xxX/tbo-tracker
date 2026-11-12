"""Агент: команды сервера → проверки → борт; телеметрия; регистратор полёта; журнал после посадки.

Потоки: главный — долгий опрос команд и их выполнение; telemetry — состояние борта на сервер (1–5 с);
flight — наблюдение за полётом и регистратор; watch — включение моторов без команды агента (взлёт из
QGroundControl, «Start Mission» в QGC, с пульта): такой полёт тоже записывается — только наблюдение, без плана
и без управления бортом; outbox — повторная отправка журналов (журнал сначала сохраняется на диск, поэтому
не теряется при отсутствии связи и перезапуске).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time

from . import VERSION
from .checks import failed, preflight, summary
from .client import ApiError
from .plan import PlanError, dist_m, effective_limits, frame_of, validate
from .recorder import GROUND_HIT_G, Recorder
from .vehicle import VehicleError

log = logging.getLogger("tbo_agent")
FLYING = ("starting", "in_flight", "returning", "landing")
TELEMETRY_KEYS = ("connected", "armed", "mode", "in_air", "lat", "lon", "alt_rel", "heading", "speed", "climb",
                  "battery_pct", "battery_v", "gps_fix", "sats", "wp_current", "wp_total", "status_text")
BOUNDS = {"lat": (-90, 90), "lon": (-180, 180), "alt_rel": (-1000, 10000), "heading": (-360, 720), "speed": (0, 300),
          "climb": (-200, 200), "battery_pct": (0, 100), "battery_v": (0, 200), "gps_fix": (-1, 10), "sats": (0, 200),
          "wp_current": (0, 10000), "wp_total": (0, 10000)}
EXTERNAL_REASON = "полёт вне tbo-tracker (QGroundControl / пульт)"
WATCH_STATES = ("idle", "error", "ready")      # агент не ведёт полёт: включение моторов — полёт вне tbo-tracker


def fsync_dir(path: str):
    """Сбросить на диск запись каталога (новое имя файла после os.replace). Только POSIX: на Windows каталог не открыть."""
    if os.name != "posix":
        return
    try:
        fd = os.open(path or ".", os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def durable_write(path: str, text: str, tmp: str = None):
    """Запись, переживающая отключение питания: временный файл → fsync → переименование → fsync каталога.

    На борту Technic корневая ФС смонтирована с commit=600: без fsync журнал полёта, записанный за минуты до отключения
    питания (смена аккумулятора после посадки), пропадал целиком (полёты R86, R91 на испытаниях 11.11.2026)."""
    tmp = tmp or path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    fsync_dir(os.path.dirname(path))


class Outbox:
    """Журналы полётов, ещё не принятые сервером (каталог state_dir/outbox, права 700)."""

    def __init__(self, path: str):
        self.path = path
        os.makedirs(path, mode=0o700, exist_ok=True)

    @staticmethod
    def name(key) -> str:
        """Номер полёта tbo-tracker (int) → run-<номер>.json; номер полёта вне tbo-tracker (str) → flight-<номер>.json."""
        return "run-%d.json" % key if isinstance(key, int) else "flight-%s.json" % key

    def put(self, key, body: dict):
        name = self.name(key)
        durable_write(os.path.join(self.path, name), json.dumps(body, ensure_ascii=False),
                      tmp=os.path.join(self.path, "." + name[:-5] + ".tmp"))

    def pending(self) -> list:
        return sorted(f for f in os.listdir(self.path) if f.startswith(("run-", "flight-")) and f.endswith(".json"))

    def load(self, name: str) -> dict:
        with open(os.path.join(self.path, name), encoding="utf-8") as f:
            return json.load(f)

    def done(self, name: str, folder: str = "sent") -> bool:
        """Перенести журнал в sent/ или rejected/. False — файла уже нет в outbox (его перенёс другой поток)."""
        dst = os.path.join(os.path.dirname(self.path), folder)
        os.makedirs(dst, mode=0o700, exist_ok=True)
        try:
            os.replace(os.path.join(self.path, name), os.path.join(dst, name))
        except FileNotFoundError:
            return False
        fsync_dir(dst)
        fsync_dir(self.path)
        # хранить 30 последних по времени (по имени «run-10» шло бы раньше «run-9» — удалялись бы свежие журналы)
        def mtime(f):
            try:
                return os.path.getmtime(os.path.join(dst, f))
            except OSError:
                return 0.0
        for f in sorted(os.listdir(dst), key=mtime)[:-30]:
            try:
                os.unlink(os.path.join(dst, f))
            except OSError:
                pass
        return True


class Agent:
    POLL_WAIT_S = 8              # долгий опрос команд: короче, чем сервер ждёт запросы при остановке (10 с)

    def __init__(self, cfg, vehicle, api):
        self.cfg, self.v, self.api = cfg, vehicle, api
        self.lock = threading.RLock()
        self.state, self.detail, self.run_id, self.plan, self.checks = "idle", "запуск агента", None, None, []
        self.serial = None
        self.ready_since = None
        self.recorder, self.abort_kind, self.flight_thread = None, None, None
        self.flight_id = None        # номер полёта вне tbo-tracker (взлёт из QGroundControl / с пульта), иначе None
        self.stop_ev, self.wake = threading.Event(), threading.Event()
        self._post_lock = threading.Lock()     # состояние отправляется строго по очереди — последним приходит актуальное
        # журналы отправляет один поток за раз: поток полёта сразу после посадки и фоновый повтор раз в 30 с
        # иначе брали один файл одновременно, второй не находил его и падал (агент навсегда оставался в «uploading»)
        self._flush_lock = threading.Lock()
        os.makedirs(cfg.state_dir, mode=0o700, exist_ok=True)
        self.outbox = Outbox(os.path.join(cfg.state_dir, "outbox"))
        self.ckpt_path = os.path.join(cfg.state_dir, "current.json")

    # ---------------------------------------------------------------- состояние
    def _set(self, state: str, detail: str = None, **kw):
        with self.lock:
            changed = state != self.state
            self.state, self.detail = state, detail
            for k, v in kw.items():
                setattr(self, k, v)
        if changed:
            log.info("состояние: %s — %s", state, detail or "")
        self.wake.set()

    def _telemetry(self) -> dict:
        snap = self.v.snapshot()
        tel = {}
        for k in TELEMETRY_KEYS:
            val = snap.get(k)
            if isinstance(val, float) and val != val:
                val = None
            if val is not None and k in BOUNDS:
                lo, hi = BOUNDS[k]
                val = max(lo, min(hi, val))
            if val is not None:
                tel[k] = val
        home = snap.get("home")
        if home and snap.get("lat") is not None and snap.get("lon") is not None:
            tel["home_dist_m"] = round(dist_m(home[0], home[1], snap["lat"], snap["lon"]), 1)
        rec = self.recorder
        if rec is not None and rec.took_off and self.state in FLYING:
            tel["flight_time_s"] = round((self.v.now() - rec.takeoff_t) / self.v.time_scale(), 1)
        if isinstance(tel.get("status_text"), str):
            tel["status_text"] = tel["status_text"][:200]
        return tel

    def state_body(self) -> dict:
        with self.lock:
            body = {"state": self.state, "detail": (self.detail or "")[:500] or None, "run_id": self.run_id,
                    "plan_hash": (self.plan or {}).get("plan_hash"),
                    "checks": [{"name": str(c.get("name"))[:80], "ok": bool(c.get("ok")), "detail": str(c.get("detail") or "")[:300],
                                "warn": bool(c.get("warn"))} for c in self.checks[:30]]}
        body["telemetry"] = self._telemetry()
        return body

    # ---------------------------------------------------------------- запуск
    def run(self):
        self.v.open()
        self._hello()
        self._recover()
        for target, name in ((self._telemetry_loop, "telemetry"), (self._outbox_loop, "outbox"), (self._watch_loop, "watch")):
            threading.Thread(target=target, name=name, daemon=True).start()
        if self.state not in FLYING:                 # после восстановления полёта состояние уже «в полёте»
            self._set("idle", "на связи, ждёт план")
        backoff = 2.0
        while not self.stop_ev.is_set():
            try:
                cmd = self.api.next_command(wait=self.POLL_WAIT_S)
                backoff = 2.0
            except ApiError as e:
                log.warning("опрос команд: %s", e.message)
                self.stop_ev.wait(30 if e.status in (401, 403) else backoff)
                backoff = min(15.0, backoff * 1.5)
                continue
            if cmd:
                self.handle(cmd)

    def _hello(self):
        while not self.stop_ev.is_set():
            try:
                r = self.api.hello(self.v.executor, self.v.info())
                self.serial = r["aircraft"]["serial"]
                log.info("подключён к %s: борт %s (%s), исполнитель %s", self.cfg.url, r["aircraft"]["name"], self.serial,
                         self.v.executor)
                return
            except ApiError as e:
                log.error("сервер %s: %s", self.cfg.url, e.message)
                self.stop_ev.wait(30 if e.status in (401, 403) else 5)

    def stop(self):
        """Остановка службы. В полёте с исполнителем offboard — сначала возврат домой (программа полёта прервётся).
        Полётом вне tbo-tracker управляет пилот (QGroundControl / пульт) — его агент не трогает."""
        if self.state in FLYING and self.v.executor == "offboard" and self.flight_id is None:
            try:
                self.v.rtl()
            except VehicleError as e:
                log.error("возврат домой при остановке: %s", e)
        self.stop_ev.set()
        self.wake.set()

    # ---------------------------------------------------------------- команды
    def handle(self, cmd: dict):
        kind = cmd.get("kind")
        log.info("команда %s №%s", kind, cmd.get("id"))
        try:
            if kind == "prepare":
                ok, msg = self._prepare(cmd)
            elif kind == "start":
                ok, msg = self._start(cmd)
            elif kind in ("rtl", "land"):
                ok, msg = self._abort(kind)
            elif kind == "cancel":
                ok, msg = self._cancel("подготовка отменена оператором")
            else:
                ok, msg = False, "неизвестная команда %s" % kind
        except VehicleError as e:
            ok, msg = False, str(e)
        except Exception as e:  # noqa: BLE001 — сбой агента не должен ронять службу
            log.exception("команда %s", kind)
            ok, msg = False, "внутренняя ошибка агента: %s" % e
        (log.info if ok else log.warning)("команда %s: %s", kind, msg)
        self._push_state()
        for attempt in range(5):
            try:
                self.api.result(cmd["id"], ok, msg)
                return
            except ApiError as e:
                if 400 <= e.status < 500:
                    return
                time.sleep(1 + attempt)

    def _limits(self, plan: dict) -> dict:
        return effective_limits((plan or {}).get("limits") or {}, self.cfg.limits, frame_of(plan))

    def _checks(self, plan: dict, limits: dict) -> list:
        return preflight(self.v.snapshot(), plan, limits) + list(self.v.extra_checks(plan))

    def _prepare(self, cmd: dict):
        plan, run_id = (cmd.get("payload") or {}).get("plan"), cmd.get("run_id")
        with self.lock:
            if self.state not in ("idle", "error"):
                return False, "борт занят: %s" % self.state
            self._set("preparing", "проверка плана и борта", run_id=run_id, plan=None, checks=[])
        try:
            return self._prepare_steps(plan, run_id)
        except Exception as e:  # noqa: BLE001 — сбой вне ожидаемых (ROS, неверный план): не оставлять «подготовку» навсегда
            try:
                self.v.clear()
            except Exception:  # noqa: BLE001
                pass
            self._set("idle", "подготовка прервана: %s" % e, run_id=None, plan=None, checks=[])
            raise

    def _prepare_steps(self, plan, run_id):
        try:
            limits = self._limits(plan)
            validate(plan, self.serial, limits)
            if plan.get("run_id") != run_id:
                raise PlanError("номер полёта в плане не совпадает с командой")
        except PlanError as e:
            self._set("idle", "план отклонён: %s" % e, run_id=None, checks=[{"name": "План", "ok": False, "detail": str(e)}])
            return False, "План отклонён бортом: %s" % e
        self.v.place_for(plan)
        snap = self.v.snapshot()
        checks = self._checks(plan, limits)
        if failed(checks):
            self._set("idle", summary(checks), run_id=None, checks=checks)
            return False, "Проверки на борту не пройдены: " + summary(checks)
        try:
            desc = self.v.upload(plan, {"lat": snap["lat"], "lon": snap["lon"]})
        except VehicleError as e:
            self.v.clear()
            self._set("idle", "план не загружен: %s" % e, run_id=None, checks=checks)
            return False, "План не загружен в контроллер: %s" % e
        checks = checks + [{"name": "План загружен в контроллер", "ok": True, "detail": desc}]
        self._set("ready", "готов к старту: " + desc, run_id=run_id, plan=plan, checks=checks)
        self.ready_since = time.monotonic()
        self._push_state()
        return True, desc

    def _start(self, cmd: dict):
        p = cmd.get("payload") or {}
        with self.lock:
            if self.state != "ready" or not self.plan:
                return False, "борт не готов к старту (%s)" % self.state
            if p.get("run_id") != self.run_id or p.get("plan_hash") != self.plan.get("plan_hash"):
                return False, "команда «Старт» относится к другому плану"
            plan = self.plan
        if (cmd.get("ttl_s") or 0) <= 0:
            return False, "команда «Старт» просрочена"
        checks = self._checks(plan, self._limits(plan))
        if failed(checks):
            self._set("ready", "перед стартом: " + summary(checks), checks=checks + self.checks[-1:])
            return False, "Перед стартом не пройдены проверки: " + summary(checks)
        try:
            self.v.verify(plan)
        except VehicleError as e:
            self.v.clear()
            self._set("idle", "план в контроллере изменён: %s" % e, run_id=None, plan=None, checks=[])
            return False, "Старт отменён: %s. Отправьте план заново" % e
        with self.lock:                  # моторы могли включить из QGroundControl, пока шли проверки (поток watch)
            if self.flight_id is not None:
                return False, "старт отменён: моторы уже включены вне tbo-tracker (QGroundControl / пульт)"
            if self.state != "ready" or self.recorder is not None:
                return False, "борт не готов к старту (%s)" % self.state
            self.abort_kind = None
            self.recorder = Recorder(self.v.now())
            self._set("starting", "включение моторов")
        try:
            desc = self.v.start_mission(plan)
        except Exception as e:  # noqa: BLE001 — любой сбой: моторы не включились — снова «готов»; включились — следим
            if not self.v.snapshot().get("armed"):
                self.recorder = None
                self._set("ready", "старт не выполнен: %s" % e)
                return False, "Старт не выполнен: %s" % e
            desc = "моторы включены с ошибкой: %s" % e
        self._set("in_flight", desc)
        self.flight_thread = threading.Thread(target=self._flight, name="flight", daemon=True)
        self.flight_thread.start()
        return True, desc

    def _abort(self, kind: str):
        with self.lock:
            if self.state not in FLYING:
                return False, "борт не в полёте"
            if self.flight_id is not None:
                return False, "полёт начат вне tbo-tracker — управляет пилот (QGroundControl / пульт), агент только записывает журнал"
        desc = self.v.rtl() if kind == "rtl" else self.v.land()
        self.abort_kind = kind
        self._set("returning" if kind == "rtl" else "landing", desc)
        return True, desc

    def _cancel(self, why: str):
        with self.lock:
            if self.state not in ("ready", "preparing"):
                return False, "отменить можно только подготовку"
        self.v.clear()
        self._set("idle", why, run_id=None, plan=None, checks=[])
        return True, "план снят с борта"

    # ---------------------------------------------------------------- полёт
    def _save_ckpt(self, rec: Recorder):
        data = {"run_id": self.run_id, "flight_id": self.flight_id, "plan_hash": (self.plan or {}).get("plan_hash"),
                "wp_total": len((self.plan or {}).get("waypoints") or []), "abort_kind": self.abort_kind,
                "takeoff_wall": None, "last_air_wall": None, "saved": self.api.now()}
        scale = self.v.time_scale()
        if rec.took_off:
            data["takeoff_wall"] = self.api.now() - (self.v.now() - rec.takeoff_t) / scale
            data["last_air_wall"] = self.api.now() - (self.v.now() - (rec.last_air_t or rec.takeoff_t)) / scale
        durable_write(self.ckpt_path, json.dumps(data))

    def _drop_ckpt(self):
        try:
            os.unlink(self.ckpt_path)
        except OSError:
            pass

    def _flight(self):
        rec, v = self.recorder, self.v
        period = max(0.005, 0.1 / v.time_scale())
        ground_since, last_ckpt, snap = None, 0.0, v.snapshot()
        last_err = 0.0
        while not self.stop_ev.is_set():
            try:
                t, snap = v.now(), v.snapshot()
                mode = (snap.get("mode") or "").upper()
                st = self.state
                if st in ("starting", "in_flight") and mode == "AUTO.RTL":
                    self._set("returning", "возврат домой (PX4)")
                elif st in ("starting", "in_flight", "returning") and mode in ("AUTO.LAND", "AUTO.PRECLAND"):
                    self._set("landing", "посадка")
                rec.sample(t, snap, v.drain_imu(), self.state)
            except Exception:  # noqa: BLE001 — сбой одного отсчёта не должен останавливать наблюдение (иначе «в полёте» навсегда)
                if time.monotonic() - last_err > 10:
                    last_err = time.monotonic()
                    log.exception("наблюдение за полётом")
                time.sleep(period)
                continue
            if rec.took_off and not snap.get("in_air"):
                ground_since = ground_since if ground_since is not None else t
                if not snap.get("armed") or t - ground_since > 10:
                    break
            else:
                ground_since = None
            if not rec.took_off and ((not snap.get("armed") and t - rec.armed_at > 3) or t - rec.armed_at > 120):
                break                    # моторы выключились без взлёта (или не взлетел за 2 мин)
            if time.monotonic() - last_ckpt > 5:
                last_ckpt = time.monotonic()
                self._save_ckpt(rec)
            time.sleep(period)
        land_t = ground_since if ground_since is not None else v.now()
        rec.landed(land_t)
        if self.stop_ev.is_set() and snap.get("in_air"):
            return                       # агент остановлен в полёте: контрольная точка сохранена
        end_wall = self.api.now() - (v.now() - land_t) / v.time_scale()
        external = self.flight_id is not None
        if external and not rec.took_off:
            # моторы включили и выключили на земле (проверка, отказ взлёта): полёта нет — журнал не создаётся
            detail = "моторы включены и выключены на земле вне tbo-tracker — полёта нет, журнал не создан"
            if rec.ground_peak >= GROUND_HIT_G:
                detail += "; удар %.1f g на земле (опрокидывание?) — осмотрите винты, лучи и подвес" % rec.ground_peak
            log.info("полёт %s: %s", self.flight_id, detail)
            self._drop_ckpt()
            with self.lock:
                self._set("idle", detail, flight_id=None, run_id=None, plan=None, checks=[])
                self.recorder = None
            return
        if external:
            status, reason = "completed", EXTERNAL_REASON
        elif not rec.took_off:
            status, reason = "aborted", "моторы выключились без взлёта%s" % (": " + snap["status_text"] if snap.get("status_text") else "")
            if rec.ground_peak >= GROUND_HIT_G:      # полёта нет — события на сервере не будет, поэтому сообщаем здесь
                reason += "; удар %.1f g на земле (опрокидывание?) — осмотрите винты, лучи и подвес" % rec.ground_peak
        elif self.abort_kind or rec.manual_override:
            status = "aborted"
            reason = {"rtl": "прервана: возврат домой по команде оператора", "land": "прервана: посадка по команде оператора"}.get(
                self.abort_kind, "прервана: управление перехватил пилот")
        else:
            status, reason = "completed", "миссия выполнена"
            wps = (self.plan or {}).get("waypoints") or []
            reached = max(rec.wp_reached, int(snap.get("wp_reached") or 0))
            if wps and all(w is not None for w in wps) and reached < len(wps):
                # борт сел сам до конца маршрута (сбой программы, PX4, оценка высоты…): не «выполнена»
                err = getattr(v, "_fly_error", None)
                status = "aborted"
                reason = "прервана: посадка до конца маршрута — достигнуто точек %d из %d%s" % (
                    reached, len(wps), "; " + str(err)[:200] if err else "")
        extra = {"executor": v.executor, "agent_version": VERSION, "wp_total": len((self.plan or {}).get("waypoints") or [])}
        if external:
            extra["external"] = True
        key = self.flight_id if external else self.run_id
        saved = False
        try:
            body = rec.build(end_wall, status, reason, extra)
            self.outbox.put(key, body)
            saved = True
            self._set("landed", "посадка выполнена: " + reason)
            self._set("uploading", "передача журнала полёта")
            sent = self._flush_outbox()
            detail = "полёт завершён — %s" % ("журнал принят сервером" if sent else "журнал будет передан при появлении связи")
        except Exception as e:  # noqa: BLE001 — сбой при сохранении/передаче журнала не должен оставлять борт «на передаче»
            log.exception("журнал полёта %s", key)
            detail = ("полёт завершён — журнал сохранён на борту и будет передан позже (%s)" % e if saved
                      else "полёт завершён — журнал не сохранён: %s" % e)
        if saved:
            self._drop_ckpt()            # без сохранённого журнала контрольная точка нужна: по ней журнал восстановится
        with self.lock:                  # атомарно: поток watch не должен увидеть «idle» с прежним регистратором
            self._set("idle", detail, run_id=None, flight_id=None, plan=None, checks=[])
            self.recorder = None

    def _recover(self):
        """Перезапуск агента: в воздухе — продолжить наблюдение; на земле после полёта — журнал по контрольной точке."""
        try:
            with open(self.ckpt_path, encoding="utf-8") as f:
                ck = json.load(f)
        except OSError:
            return
        except ValueError:
            # пустая или оборванная контрольная точка (питание отключили во время записи прежней версией агента):
            # в сторону, чтобы не лежала вечно и не путала проверку перед обновлением
            try:
                os.replace(self.ckpt_path, self.ckpt_path + ".corrupt")
            except OSError:
                pass
            log.warning("контрольная точка полёта повреждена — перенесена в %s.corrupt", self.ckpt_path)
            return
        snap = self.v.snapshot()
        fid = ck.get("flight_id") if not ck.get("run_id") else None      # полёт вне tbo-tracker
        key = ck.get("run_id") or fid
        if key and (snap.get("armed") or snap.get("in_air")):
            log.warning("агент перезапущен в полёте (полёт %s) — наблюдение продолжено", key)
            rec = Recorder(self.v.now())
            if ck.get("takeoff_wall"):
                rec.takeoff_t = self.v.now() - (self.api.now() - ck["takeoff_wall"])
            self.recorder, self.abort_kind = rec, ck.get("abort_kind")
            if fid:
                self._set("in_flight", EXTERNAL_REASON + " — наблюдение продолжено после перезапуска агента",
                          run_id=None, flight_id=fid, plan=None)
            else:
                self._set("in_flight", "наблюдение продолжено после перезапуска агента", run_id=ck["run_id"],
                          plan={"plan_hash": ck.get("plan_hash"), "waypoints": [None] * int(ck.get("wp_total") or 0)})
            self.flight_thread = threading.Thread(target=self._flight, name="flight", daemon=True)
            self.flight_thread.start()
            return
        if key and ck.get("takeoff_wall"):
            from .client import iso
            start, end = ck["takeoff_wall"], max(ck["takeoff_wall"] + 1, ck.get("last_air_wall") or ck["takeoff_wall"])
            restored = "журнал восстановлен по контрольной точке после перезапуска агента (без акселерометра)"
            body = {"status": "aborted" if ck.get("abort_kind") else "completed",
                    "reason": EXTERNAL_REASON + "; " + restored if fid else restored,
                    "flight": {"start": iso(start), "end": iso(end), "imu": None, "events": []}, "track": [],
                    "summary": {"duration_s": round(end - start, 1), "executor": self.v.executor, "agent_version": VERSION,
                                "reason": "журнал восстановлен после перезапуска агента"}}
            if fid:
                body["summary"]["external"] = True
            self.outbox.put(key, body)
        if not fid:
            self.v.clear()               # свой план — снять; миссию пилота (QGroundControl) не трогать
        self._drop_ckpt()

    # ---------------------------------------------------------------- фоновые потоки
    def _push_state(self):
        try:
            with self._post_lock:
                resp = self.api.state(self.state_body())
        except ApiError as e:
            if e.status == 422:
                log.error("сервер не принял состояние: %s", e.message)
            return
        active = (resp or {}).get("active_run_id")
        with self.lock:
            stale = self.state in ("ready", "preparing") and self.run_id is not None and active != self.run_id
        if stale:
            self._cancel("подготовка снята сервером")

    def _telemetry_loop(self):
        while not self.stop_ev.is_set():
            self._push_state()
            st = self.state
            if st == "ready" and self.ready_since and time.monotonic() - self.ready_since > self.cfg.ready_timeout_s:
                self._cancel("подготовка снята: старт не выполнен за %.0f мин" % (self.cfg.ready_timeout_s / 60))
            interval = 1.0 if st in FLYING else 2.0 if st in ("preparing", "ready", "landed", "uploading") else 5.0
            self.wake.wait(interval)
            self.wake.clear()

    def _move(self, name: str, folder: str):
        try:
            self.outbox.done(name, folder)
        except OSError as e:
            log.error("журнал %s: не удалось перенести в %s/: %s", name, folder, e)

    def _flush_outbox(self) -> bool:
        """Отправить журналы из outbox. True — всё отправлено (или outbox пуст)."""
        with self._flush_lock:
            ok = True
            for name in self.outbox.pending():
                try:
                    external = name.startswith("flight-")
                    key = name[7:-5] if external else int(name[4:-5])
                    label = key if external else "R%d" % key
                    body = self.outbox.load(name)
                except FileNotFoundError:
                    continue                     # уже отправлен и перенесён
                except (OSError, ValueError) as e:
                    log.error("журнал %s повреждён: %s (перенесён в rejected/)", name, e)
                    self._move(name, "rejected")
                    continue
                try:
                    if external:
                        self.api.upload_flight(key, body)
                    else:
                        self.api.upload_log(key, body)
                except ApiError as e:
                    ok = False
                    if e.status in (400, 404, 409, 422):
                        log.error("журнал %s отклонён сервером: %s (сохранён в rejected/)", label, e.message)
                        self._move(name, "rejected")
                        continue
                    log.warning("журнал %s не передан: %s — повтор позже", label, e.message)
                    break
                self._move(name, "sent")
                log.info("журнал полёта %s принят сервером", label)
            return ok

    def _outbox_tick(self):
        try:
            if self.state not in FLYING and self.outbox.pending():
                self._flush_outbox()
        except Exception:  # noqa: BLE001 — фоновый повтор не должен останавливаться навсегда
            log.exception("повторная отправка журналов")

    def _new_flight_id(self) -> str:
        """Номер полёта вне tbo-tracker: время включения моторов (UTC, по часам сервера) и случайный хвост."""
        return "X" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(self.api.now())) + "-" + os.urandom(2).hex()

    def _watch_tick(self) -> bool:
        """Моторы включены без команды агента → наблюдение и запись журнала (без плана и без управления бортом)."""
        snap = self.v.snapshot()
        if not (snap.get("armed") or snap.get("in_air")):
            return False
        with self.lock:
            if self.recorder is not None or self.state not in WATCH_STATES:
                return False
            dropped = self.state == "ready"
            fid = self._new_flight_id()
            self.abort_kind, self.ready_since = None, None
            self.recorder = Recorder(self.v.now())
            # подготовка tbo-tracker снимается только у агента (сервер закроет её как «сброшенную на борту»):
            # план в контроллере не стирается — по нему, возможно, и летит пилот
            self._set("in_flight", EXTERNAL_REASON + " — запись журнала" + ("; подготовка tbo-tracker снята" if dropped else ""),
                      run_id=None, flight_id=fid, plan=None, checks=[])
            self.flight_thread = threading.Thread(target=self._flight, name="flight", daemon=True)
            self.flight_thread.start()
        log.warning("моторы включены без команды tbo-tracker (режим %s) — запись полёта %s", snap.get("mode"), fid)
        return True

    def _watch_loop(self):
        period = max(0.02, 0.25 / self.v.time_scale())
        while not self.stop_ev.wait(period):
            try:
                self._watch_tick()
            except Exception:  # noqa: BLE001 — сбой одного опроса не должен отключать наблюдение
                log.exception("наблюдение за включением моторов")
                self.stop_ev.wait(5)

    def _outbox_loop(self):
        while not self.stop_ev.wait(30):
            self._outbox_tick()
