"""Загрузка миссии tbo-tracker в реальный автопилот (ArduPilot / PX4) по MAVLink и запуск в режиме AUTO.

ВНИМАНИЕ: скрипт-заготовка для стенда/SITL. На реальном борту в рамках прототипа НЕ проверялся.
Перед полётом проверьте миссию в QGroundControl, соблюдайте требования к допуску и безопасности полётов.

    pip install pymavlink requests
    python scripts/run_mission_mavlink.py --mission 1 --connect udp:127.0.0.1:14550           # SITL
    python scripts/run_mission_mavlink.py --mission 1 --connect COM5 --baud 57600 --arm         # телеметрийный модем

Без --arm миссия только загружается в автопилот. После полёта выгрузите бортовой журнал (.bin/.ulg → CSV)
и загрузите его в «Импорт журналов» — наработка будет начислена борту и узлам.
"""
from __future__ import annotations

import argparse
import sys

import requests


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mission", type=int, required=True, help="ID миссии в tbo-tracker")
    ap.add_argument("--server", default="http://localhost:8000")
    ap.add_argument("--connect", required=True, help="строка подключения pymavlink: udp:IP:порт, COM5, /dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=57600)
    ap.add_argument("--arm", action="store_true", help="взвести и перевести в AUTO (иначе только загрузка)")
    args = ap.parse_args()

    try:
        from pymavlink import mavutil
    except ImportError:
        print("Установите pymavlink: pip install pymavlink", file=sys.stderr)
        return 2

    m = requests.get(f"{args.server}/api/missions/{args.mission}", timeout=10).json()
    plan = requests.get(f"{args.server}/api/missions/{args.mission}/export?fmt=plan", timeout=10).json()
    items = [i for i in plan["mission"]["items"] if i.get("type") == "SimpleItem"]
    home = plan["mission"]["plannedHomePosition"]

    link = mavutil.mavlink_connection(args.connect, baud=args.baud)
    link.wait_heartbeat(timeout=30)
    print(f"Связь с автопилотом: система {link.target_system}, компонент {link.target_component}")

    # seq 0 — домашняя точка (требование ArduPilot), далее элементы миссии
    seq = [(mavutil.mavlink.MAV_CMD_NAV_WAYPOINT, 0, [0, 0, 0, 0, home[0], home[1], 0])]
    seq += [(i["command"], i.get("frame", 3), i["params"]) for i in items]
    link.mav.mission_count_send(link.target_system, link.target_component, len(seq))
    sent = set()
    while len(sent) < len(seq):
        req = link.recv_match(type=["MISSION_REQUEST_INT", "MISSION_REQUEST"], blocking=True, timeout=10)
        if req is None:
            print("Автопилот не запросил элементы миссии", file=sys.stderr)
            return 1
        cmd, frame, p = seq[req.seq]
        link.mav.mission_item_int_send(link.target_system, link.target_component, req.seq, frame, cmd, 0, 1,
                                       *(float(x or 0) for x in p[:4]), int(p[4] * 1e7), int(p[5] * 1e7), float(p[6]))
        sent.add(req.seq)
    ack = link.recv_match(type="MISSION_ACK", blocking=True, timeout=10)
    print(f"Миссия «{m['name']}» загружена: {len(seq) - 1} элементов, ответ {getattr(ack, 'type', 'нет')}")

    if args.arm:
        link.set_mode("AUTO")
        link.arducopter_arm()
        link.motors_armed_wait()
        print("Борт взведён, режим AUTO — миссия выполняется. Ctrl+C — прекратить наблюдение.")
        while True:
            msg = link.recv_match(type=["MISSION_ITEM_REACHED", "STATUSTEXT"], blocking=True)
            print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
