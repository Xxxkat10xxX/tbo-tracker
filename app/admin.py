"""Командная строка администратора сервера (используется tbo-ctl на Raspberry Pi).

    python -m app.admin status            состояние сервера и данных
    python -m app.admin backup            создать резервную копию
    python -m app.admin backups           список копий
    python -m app.admin restore ИМЯ|ПУТЬ  восстановить из копии (останавливайте сервис перед восстановлением)
    python -m app.admin reset-demo        пересоздать демо-данные
    python -m app.admin init              создать базу (и демо-данные, если TBO_AUTO_SEED=1)
    python -m app.admin tick              одна проверка уведомлений / рассылка / копия по расписанию
    python -m app.admin notify-test       тестовое сообщение во включённые каналы
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _fmt_size(n: int | None) -> str:
    if n is None:
        return "—"
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if n < 1024 or unit == "ТБ":
            return f"{n:.0f} {unit}" if unit == "Б" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.admin", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["status", "backup", "backups", "restore", "reset-demo", "init", "tick", "notify-test"])
    ap.add_argument("target", nargs="?", help="имя копии или путь к .zip (для restore)")
    ap.add_argument("--json", action="store_true", help="вывод в JSON")
    ap.add_argument("--kind", default="manual", choices=["manual", "pre-update"], help="тип копии (для backup)")
    a = ap.parse_args(argv)

    from . import models  # noqa: F401 — регистрирует таблицы перед create_all
    from .db import Base, engine, migrate
    from .errors import DomainError
    Base.metadata.create_all(engine)
    migrate()
    from .services import backup, notify, scheduler, serverconf, sysinfo
    try:
        if a.command == "status":
            st = sysinfo.status()
            if a.json:
                print(json.dumps(st, ensure_ascii=False, indent=2))
                return 0
            h, m, sg, d = st["host"], st["memory"], st["storage"], st["data"]
            print(f"Устройство:   {h['model']} · {h['os']} · ядро {h['kernel']}")
            print(f"Температура:  {h['cpu_temp_c'] if h['cpu_temp_c'] is not None else '—'} °C · загрузка {h['load']}")
            print(f"Память:       свободно {_fmt_size(m['available'])} из {_fmt_size(m['total'])}")
            print(f"Хранилище:    {sg['kind']} ({sg['device']}) · свободно {_fmt_size(sg['free'])} из {_fmt_size(sg['total'])}")
            print(f"Данные:       база {_fmt_size(d['db_bytes'])}, фото {_fmt_size(d['uploads_bytes'])}, копий {d['backups']} ({_fmt_size(d['backups_bytes'])})")
            print(f"Вход:         {'по паролю, пользователь ' + st['app']['auth_user'] if st['app']['auth'] else 'без пароля'}")
            print("Адреса:       " + "  ".join(st["urls"]))
            for w in st["warnings"]:
                print(f"ВНИМАНИЕ:     {w}")
        elif a.command == "backup":
            b = backup.create(a.kind)
            print(f"Создана копия {b['name']} ({_fmt_size(b['size'])}) в {backup.BACKUP_DIR}")
        elif a.command == "backups":
            for b in backup.list_backups():
                print(f"{b['name']}\t{_fmt_size(b['size'])}\t{b['created_at']}")
        elif a.command == "restore":
            if not a.target:
                ap.error("укажите имя копии или путь к .zip")
            name = a.target
            p = Path(a.target)
            if p.exists() and p.parent.resolve() != backup.BACKUP_DIR.resolve():
                name = backup.save_upload(p.read_bytes())["name"]
            elif p.exists():
                name = p.name
            print(backup.restore(name)["message"])
        elif a.command == "reset-demo":
            from .seed import reset_and_seed
            print(reset_and_seed()["message"])
        elif a.command == "init":
            from .config import AUTO_SEED
            if AUTO_SEED:
                from .seed import seed_if_empty
                seed_if_empty()
            print("База готова")
        elif a.command == "tick":
            print(json.dumps(scheduler.tick(), ensure_ascii=False))
        elif a.command == "notify-test":
            res = notify.send([{"category": "overdue", "title": "Проверка канала уведомлений tbo-tracker", "text": "тест",
                                "href": "#/maintenance"}], serverconf.load())
            print(json.dumps(res, ensure_ascii=False) if res else "Нет включённых каналов (страница «Сервер» → «Уведомления вне браузера»)")
    except DomainError as e:
        print(f"Ошибка: {e.message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
