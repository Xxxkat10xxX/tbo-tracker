"""Наборы колёс для офлайн-установки на Raspberry Pi соответствуют requirements.lock (иначе Pi без интернета не установится)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def test_offline_wheel_lists_match_lock():
    lock = {}
    for line in (ROOT / "requirements.lock").read_text().splitlines():
        line = line.split("#")[0].strip()
        if line:
            n, v = line.split("==")
            lock[_norm(n)] = v.strip()
    lists = sorted((ROOT / "deploy/raspberry-pi/offline").glob("wheels-cp*-aarch64.txt"))
    assert {p.name for p in lists} >= {"wheels-cp311-aarch64.txt", "wheels-cp313-aarch64.txt"}
    for p in lists:
        got = {}
        for line in p.read_text().splitlines():
            if not line or line.startswith("#"):
                continue
            sha, file, url = line.split()
            assert re.fullmatch(r"[0-9a-f]{64}", sha) and url.startswith("https://files.pythonhosted.org/") and url.endswith("/" + file)
            n, v = file.split("-")[:2]
            got[_norm(n)] = v
        assert got == lock, f"{p.name} не совпадает с requirements.lock — запустите deploy/raspberry-pi/offline/make_lists.py"
