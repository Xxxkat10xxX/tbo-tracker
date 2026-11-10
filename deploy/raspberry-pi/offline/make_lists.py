"""Списки колёс для офлайн-установки сервера на Raspberry Pi (deploy/field/deploy.sh): SHA-256, имя, адрес.

Запускать после изменения requirements.lock, нужен интернет:
    python3 deploy/raspberry-pi/offline/make_lists.py
"""
import hashlib
import json
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCK = HERE.parent.parent.parent / "requirements.lock"
TARGETS = {"311": "aarch64", "313": "aarch64"}           # Raspberry Pi OS Bookworm (3.11) и Trixie (3.13), 64 бит


def build(ver: str, arch: str):
    with tempfile.TemporaryDirectory() as d:
        plats = [f"manylinux_2_17_{arch}", f"manylinux2014_{arch}", f"manylinux_2_28_{arch}", f"linux_{arch}"]
        cmd = [sys.executable, "-m", "pip", "download", "-q", "--no-deps", "--only-binary=:all:", "--implementation", "cp",
               "--python-version", ver, "-r", str(LOCK), "-d", d] + [x for p in plats for x in ("--platform", p)]
        subprocess.run(cmd, check=True)
        lines = [f"# Колёса для Raspberry Pi ({arch}, CPython {ver}) по requirements.lock: SHA-256, имя, адрес.",
                 "# Создаётся make_lists.py при обновлении requirements.lock. Используется deploy/field/deploy.sh."]
        for f in sorted(Path(d).glob("*.whl")):
            name, v = f.name.split("-")[:2]
            meta = json.load(urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{v}/json", timeout=30))
            url = next(u for u in meta["urls"] if u["filename"] == f.name)
            h = hashlib.sha256(f.read_bytes()).hexdigest()
            if h != url["digests"]["sha256"]:
                raise SystemExit(f"SHA-256 не совпадает с PyPI: {f.name}")
            lines.append(f"{h}  {f.name}  {url['url']}")
    out = HERE / f"wheels-cp{ver}-{arch}.txt"
    out.write_text("\n".join(lines) + "\n")
    print(out.name, len(lines) - 2, "пакетов")


if __name__ == "__main__":
    for ver, arch in TARGETS.items():
        build(ver, arch)
