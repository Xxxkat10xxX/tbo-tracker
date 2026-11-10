"""Запуск: python3 -m tbo_agent [--config /etc/tbo-agent/agent.conf] [run | check] [--sim …]

run    — работа агента (служба tbo-agent);
check  — проверить настройки, токен и связь с сервером (полётный контроллер не трогается);
--sim  — симулятор борта вместо ROS/PX4 (проверка без дрона): --sim-speed 20 --sim-fault collision@2.
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys

from . import VERSION
from .client import Api, ApiError
from .config import DEFAULT_PATH, Config, ConfigError


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="tbo-agent", description="Бортовой агент tbo-tracker %s" % VERSION)
    ap.add_argument("command", nargs="?", default="run", choices=("run", "check"))
    ap.add_argument("--config", default=DEFAULT_PATH)
    ap.add_argument("--sim", action="store_true", help="симулятор борта вместо ROS/MAVROS")
    ap.add_argument("--sim-speed", type=float, default=1.0, help="ускорение времени симулятора")
    ap.add_argument("--sim-fault", default=None, help="например collision@2 — удар у точки 2")
    ap.add_argument("--sim-home", default=None, help="широта,долгота борта (иначе — точка взлёта первой миссии)")
    a = ap.parse_args(argv)
    try:
        cfg = Config(a.config)
    except ConfigError as e:
        print("tbo-agent: %s" % e, file=sys.stderr)
        return 2
    logging.basicConfig(level=getattr(logging, cfg.log_level, logging.INFO),
                        format="%(asctime)s %(levelname)s %(threadName)s: %(message)s")
    log = logging.getLogger("tbo_agent")
    api = Api(cfg.url, cfg.token, cfg.ca_file, cfg.timeout_s)
    if cfg.url.startswith("http://"):
        log.warning("связь с сервером без HTTPS: токен и команды передаются открыто — включите HTTPS на сервере")
    if a.command == "check":
        try:
            r = api.hello("check", {"check": True})
        except ApiError as e:
            print("Нет доступа к серверу %s: %s" % (cfg.url, e.message), file=sys.stderr)
            return 1
        print("Связь с сервером в порядке: борт %s (%s), расхождение часов %.1f с"
              % (r["aircraft"]["name"], r["aircraft"]["serial"], api.clock_offset))
        return 0
    if a.sim:
        from .vehicle_sim import SimVehicle
        home = tuple(float(x) for x in a.sim_home.split(",")) if a.sim_home else None
        vehicle = SimVehicle(home=home, time_scale=a.sim_speed, fault=a.sim_fault)
    else:
        from .vehicle_ros import RosVehicle
        vehicle = RosVehicle(cfg.executor, cfg.fcu_wait_s, cfg.offboard_tolerance_m, cfg.takeoff_speed_ms, cfg.local_tolerance_m)
    from .agent import Agent
    agent = Agent(cfg, vehicle, api)
    signal.signal(signal.SIGTERM, lambda *_: agent.stop())
    signal.signal(signal.SIGINT, lambda *_: agent.stop())
    log.info("tbo-agent %s, сервер %s, борт: %s", VERSION, cfg.url, "симулятор" if a.sim else "ROS/MAVROS (%s)" % cfg.executor)
    try:
        agent.run()
    except Exception as e:  # noqa: BLE001 — понятное сообщение в журнал службы; systemd перезапустит агента
        log.exception("агент остановлен из-за ошибки: %s", e)
        return 1
    finally:
        vehicle.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
