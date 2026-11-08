"""Состояние сервера (Raspberry Pi или любой Linux): модель, температура, память, диск, питание, адреса."""
from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from ..config import DATA_DIR, VERSION

_STARTED = time.time()
THROTTLE_BITS = {
    0: "Пониженное напряжение питания сейчас",
    1: "Частота процессора ограничена сейчас",
    2: "Троттлинг сейчас",
    3: "Достигнут мягкий температурный лимит сейчас",
    16: "С момента включения было пониженное напряжение",
    17: "С момента включения частота ограничивалась",
    18: "С момента включения был троттлинг",
    19: "С момента включения достигался мягкий температурный лимит",
}


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text().strip("\x00\n ")
    except OSError:
        return None


def _run(args: list[str]) -> str | None:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=3, check=False)  # noqa: S603
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _meminfo() -> dict:
    out = {}
    txt = _read("/proc/meminfo") or ""
    for line in txt.splitlines():
        k, _, v = line.partition(":")
        if k in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree"):
            out[k] = int(v.split()[0]) * 1024
    return out


def _os_name() -> str:
    txt = _read("/etc/os-release") or ""
    for line in txt.splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip('"')
    return platform.platform()


def _addresses() -> list[str]:
    out = _run(["hostname", "-I"])
    if out:
        return [a for a in out.split() if ":" not in a]          # только IPv4 — их удобнее вводить
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
            sk.connect(("192.0.2.1", 9))                          # пакеты не отправляются, только выбор интерфейса
            return [sk.getsockname()[0]]
    except OSError:
        return []


def _throttled() -> dict | None:
    out = _run(["vcgencmd", "get_throttled"])
    if not out or "=" not in out:
        return None
    try:
        val = int(out.split("=", 1)[1], 16)
    except ValueError:
        return None
    return {"raw": hex(val), "flags": [txt for bit, txt in THROTTLE_BITS.items() if val & (1 << bit)]}


def _storage(path: Path) -> dict:
    src = _run(["findmnt", "-no", "SOURCE", "--target", str(path)]) or ""
    kind = "microSD" if "mmcblk" in src else ("NVMe SSD" if "nvme" in src else ("USB-накопитель" if src.startswith("/dev/sd") else src or "—"))
    du = shutil.disk_usage(path)
    return {"device": src or None, "kind": kind, "total": du.total, "used": du.used, "free": du.free}


def _dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.exists() else 0


def status() -> dict:
    from .backup import BACKUP_DIR, list_backups
    temp = _read("/sys/class/thermal/thermal_zone0/temp")
    up = _read("/proc/uptime")
    model = _read("/proc/device-tree/model")
    mem = _meminfo()
    port = os.environ.get("TBO_PORT", "8000")
    host = socket.gethostname()
    suffix = "" if port in ("80", "") else f":{port}"
    from ..config import DATABASE_URL
    db = Path(DATABASE_URL.removeprefix("sqlite:///")) if DATABASE_URL.startswith("sqlite:///") else None
    db_files = [Path(f"{db}{suf}") for suf in ("", "-wal", "-shm")] if db else []
    backups = list_backups()
    warnings = []
    thr = _throttled()
    if thr and thr["flags"]:
        warnings.append("Питание/охлаждение: " + "; ".join(thr["flags"]) + ". Используйте штатный блок питания 27 Вт и активный кулер.")
    t = round(int(temp) / 1000, 1) if temp and temp.isdigit() else None
    if t is not None and t >= 75:
        warnings.append(f"Высокая температура процессора: {t} °C")
    stg = _storage(DATA_DIR)
    if stg["kind"] == "microSD":
        warnings.append("Данные на microSD-карте. Для круглосуточной работы рекомендуется SSD (M.2 HAT+ или USB 3) и регулярные резервные копии.")
    if stg["free"] < 1 << 30:
        warnings.append("На диске меньше 1 ГБ свободного места")
    if mem.get("MemAvailable") and mem["MemAvailable"] < 200 << 20:
        warnings.append("Мало свободной оперативной памяти")
    return {
        "app": {"version": VERSION, "python": sys.version.split()[0], "pid": os.getpid(),
                "uptime_s": int(time.time() - _started()), "data_dir": str(DATA_DIR),
                "auth": bool(os.environ.get("TBO_AUTH_PASSWORD")), "auth_user": os.environ.get("TBO_AUTH_USER", "admin")},
        "host": {"model": model or platform.machine(), "hostname": host, "os": _os_name(), "kernel": platform.release(),
                 "arch": platform.machine(), "cpus": os.cpu_count(), "load": [round(x, 2) for x in os.getloadavg()] if hasattr(os, "getloadavg") else None,
                 "uptime_s": int(float(up.split()[0])) if up else None, "cpu_temp_c": t, "throttled": thr,
                 "is_raspberry_pi": bool(model and "Raspberry Pi" in model)},
        "memory": {"total": mem.get("MemTotal"), "available": mem.get("MemAvailable"),
                   "swap_total": mem.get("SwapTotal"), "swap_free": mem.get("SwapFree")},
        "storage": stg,
        "data": {"db_bytes": sum(f.stat().st_size for f in db_files if f.exists()), "uploads_bytes": _dir_size(DATA_DIR / "uploads"),
                 "backups_bytes": _dir_size(BACKUP_DIR), "backups": len(backups), "last_backup": backups[0] if backups else None},
        "urls": [f"http://{host}.local{suffix}"] + [f"http://{a}{suffix}" for a in _addresses()],
        "warnings": warnings,
    }


def _started() -> float:
    return _STARTED
