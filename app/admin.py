"""Командная строка администратора сервера (используется tbo-ctl на Raspberry Pi и в Docker).

    python -m app.admin status                       состояние сервера и данных
    python -m app.admin users                        список учётных записей
    python -m app.admin create-user ИМЯ --role admin создать пользователя (пароль — скрытый ввод или --password-stdin)
    python -m app.admin passwd ИМЯ                   задать пароль (--temporary — сменить при входе)
    python -m app.admin unlock ИМЯ                   снять блокировку после неудачных попыток входа
    python -m app.admin disable ИМЯ | enable ИМЯ     отключить / включить учётную запись
    python -m app.admin setup-code                   показать одноразовый код первичной настройки
    python -m app.admin backup | backups             создать копию / список копий
    python -m app.admin restore ИМЯ|ПУТЬ             восстановить из копии (останавливайте сервис перед восстановлением)
    python -m app.admin reset-demo                   пересоздать демо-данные (только при TBO_DEMO_MODE=1)
    python -m app.admin init                         создать/обновить структуру базы
    python -m app.admin tick | notify-test           фоновая проверка / тестовое уведомление
    python -m app.admin drone-add СЕРИЙНЫЙ --name ИМЯ   подключить борт (агент tbo-agent): создать борт при необходимости,
                                                     выпустить токен устройства (--rotate — новый токен, --token-only — только токен)
    python -m app.admin drones                       подключённые борты и их состояние

Пароль никогда не передаётся аргументом командной строки (его видно в списке процессов и истории оболочки).
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

COMMANDS = ["status", "users", "create-user", "passwd", "unlock", "disable", "enable", "setup-code", "backup", "backups",
            "restore", "reset-demo", "init", "tick", "notify-test", "drone-add", "drones"]
DRONE_TYPE = {"code": "SKYRIS-T6S", "name": "Skyris Technic 6S", "category": "multirotor", "manufacturer": "Skyris"}


def _fmt_size(n: int | None) -> str:
    if n is None:
        return "—"
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if n < 1024 or unit == "ТБ":
            return f"{n:.0f} {unit}" if unit == "Б" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def _read_password(from_stdin: bool, confirm: bool = True) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    if not sys.stdin.isatty():
        raise SystemExit("Нет терминала для скрытого ввода пароля: используйте --password-stdin")
    p1 = getpass.getpass("Пароль: ")
    if confirm and getpass.getpass("Ещё раз: ") != p1:
        raise SystemExit("Пароли не совпадают")
    return p1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.admin", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=COMMANDS)
    ap.add_argument("target", nargs="?", help="имя пользователя / имя копии или путь к .zip")
    ap.add_argument("--json", action="store_true", help="вывод в JSON")
    ap.add_argument("--kind", default="manual", choices=["manual", "pre-update"], help="тип копии (для backup)")
    ap.add_argument("--role", default="viewer", choices=["admin", "engineer", "viewer"], help="роль (create-user)")
    ap.add_argument("--name", default="", help="отображаемое имя (create-user)")
    ap.add_argument("--password-stdin", action="store_true", help="прочитать пароль из стандартного ввода (одна строка)")
    ap.add_argument("--temporary", action="store_true", help="пароль временный — сменить при первом входе")
    ap.add_argument("--rotate", action="store_true", help="drone-add: борт уже подключён — выпустить новый токен")
    ap.add_argument("--token-only", action="store_true", help="drone-add: вывести только токен (для скрипта развёртывания)")
    a = ap.parse_args(argv)

    from . import models  # noqa: F401 — регистрирует таблицы перед create_all
    from .db import Base, SessionLocal, engine, migrate, wait_for_database
    from .errors import DomainError
    wait_for_database()
    Base.metadata.create_all(engine)
    migrate()
    from .models import User
    from .services import auth, backup, notify, scheduler, serverconf, sysinfo
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
            print(f"База:         {st['app']['database']} · данные {_fmt_size(d['db_bytes'])}, фото {_fmt_size(d['uploads_bytes'])}, "
                  f"копий {d['backups']} ({_fmt_size(d['backups_bytes'])})")
            with SessionLocal() as s:
                print(f"Пользователи: {auth.users_count(s)} (администраторов активно: {auth.active_admins(s)})")
            print("Адреса:       " + "  ".join(st["urls"]))
            for w in st["warnings"]:
                print(f"ВНИМАНИЕ:     {w}")
        elif a.command == "users":
            with SessionLocal() as s:
                rows = [auth.user_dict(u) for u in s.query(User).order_by(User.username)]
            if a.json:
                print(json.dumps(rows, ensure_ascii=False, indent=2))
                return 0
            for u in rows:
                flags = [x for x, on in (("отключён", not u["is_active"]), ("заблокирован", u["locked"]),
                                         ("сменить пароль", u["must_change_password"])) if on]
                print(f"{u['username']:<20} {u['role_label']:<15} {u['display_name']:<30} {', '.join(flags)}")
        elif a.command == "create-user":
            if not a.target:
                ap.error("укажите имя пользователя")
            pw = _read_password(a.password_stdin)
            with SessionLocal() as s:
                u = auth.create_user(s, a.target, pw, a.role, a.name, must_change=a.temporary)
                s.commit()
                auth.finish_setup()
            auth.audit("user_created", "cli", None, target=u.username, details=f"роль {u.role}, командная строка")
            print(f"Пользователь {u.username} ({u.role}) создан")
        elif a.command in ("passwd", "unlock", "disable", "enable"):
            if not a.target:
                ap.error("укажите имя пользователя")
            with SessionLocal() as s:
                u = s.query(User).filter(User.username == a.target.strip().lower()).one_or_none()
                if u is None:
                    raise DomainError(f"Нет пользователя {a.target}")
                if a.command == "passwd":
                    auth.set_password(s, u, _read_password(a.password_stdin), must_change=a.temporary)
                    msg = f"Пароль пользователя {u.username} изменён, его сессии завершены"
                elif a.command == "unlock":
                    auth.unlock(s, u)
                    msg = f"Учётная запись {u.username} разблокирована"
                else:
                    if a.command == "disable" and u.role == "admin" and u.is_active and auth.active_admins(s) <= 1:
                        raise DomainError("Должен остаться хотя бы один активный администратор")
                    u.is_active = a.command == "enable"
                    if not u.is_active:
                        s.query(auth.AuthSession).filter(auth.AuthSession.user_id == u.id).delete()
                    msg = f"Учётная запись {u.username} {'включена' if u.is_active else 'отключена'}"
                s.commit()
            auth.audit(f"cli_{a.command}", "cli", None, target=a.target.strip().lower())
            print(msg)
        elif a.command == "setup-code":
            if auth.SETUP_FILE.exists():
                print(auth.SETUP_FILE.read_text().strip())
            else:
                with SessionLocal() as s:
                    print("Первичная настройка не требуется: учётные записи уже есть" if auth.users_count(s)
                          else "Код появится после запуска сервиса (файл setup-code.txt в каталоге данных)")
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
            if p.is_file() and p.resolve().parent != backup.BACKUP_DIR.resolve():
                name = backup.save_upload(p.read_bytes())["name"]
            elif p.is_file():
                name = p.name
            print(backup.restore(name)["message"])
        elif a.command == "reset-demo":
            from .config import DEMO_MODE
            if not DEMO_MODE:
                raise DomainError("Демо-режим выключен (TBO_DEMO_MODE=0): сброс стёр бы рабочие данные")
            from .seed import reset_and_seed
            print(reset_and_seed()["message"])
        elif a.command == "init":
            from .config import DEMO_MODE
            if DEMO_MODE:
                from .seed import seed_if_empty
                seed_if_empty()
            print("База готова")
        elif a.command == "tick":
            print(json.dumps(scheduler.tick(), ensure_ascii=False))
        elif a.command == "drone-add":
            token, msg = _drone_add(SessionLocal, a)
            if a.token_only:
                print(token)
            else:
                print(msg)
                print(f"Токен устройства (показывается один раз): {token}")
        elif a.command == "drones":
            from .services import drones
            from .models import DroneLink
            with SessionLocal() as s:
                rows = [drones.link_dict(s, link) for link in s.query(DroneLink).order_by(DroneLink.id)]
            if a.json:
                print(json.dumps(rows, ensure_ascii=False))
            for r in rows if not a.json else []:
                t = r["telemetry"]
                print(f"{r['aircraft']['serial']:<20} {r['state_label']:<28} последний обмен: {r['last_seen_at'] or '—'}  "
                      f"агент: {r['agent'].get('version', '—')} {r['agent'].get('executor', '')}  батарея: {t.get('battery_pct', '—')}")
            if not rows and not a.json:
                print("Подключённых бортов нет: python -m app.admin drone-add СЕРИЙНЫЙ --name ИМЯ")
        elif a.command == "notify-test":
            res = notify.send([{"category": "overdue", "title": "Проверка канала уведомлений tbo-tracker", "text": "тест",
                                "href": "#/maintenance"}], serverconf.load())
            print(json.dumps(res, ensure_ascii=False) if res else "Нет включённых каналов (страница «Сервер» → «Уведомления вне браузера»)")
    except DomainError as e:
        print(f"Ошибка: {e.message}", file=sys.stderr)
        return 1
    return 0


def _drone_add(SessionLocal, a) -> tuple[str, str]:
    """Борт для агента: тип «Skyris Technic 6S» и борт создаются при необходимости, затем — токен устройства."""
    from sqlalchemy import select

    from .errors import DomainError
    from .models import Aircraft, AircraftType, DroneLink
    from .services import auth, drones
    from .timeutil import utcnow
    serial = (a.target or "").strip()
    if not serial or len(serial) > 100:
        raise DomainError("Укажите серийный номер борта: drone-add СЕРИЙНЫЙ --name ИМЯ")
    with SessionLocal() as s:
        ac = s.scalar(select(Aircraft).where(Aircraft.serial == serial))
        link = s.scalar(select(DroneLink).where(DroneLink.aircraft_id == ac.id)) if ac is not None else None
        if a.rotate and link is None:        # новый токен — только подключённому борту: опечатка не создаёт лишний борт
            raise DomainError(f"Борт {serial} не подключён: сначала drone-add {serial} --name ИМЯ")
        created = ac is None
        if created:
            t = s.scalar(select(AircraftType).where(AircraftType.code == DRONE_TYPE["code"]))
            if t is None:
                t = AircraftType(**DRONE_TYPE, slots=[])
                s.add(t)
                s.flush()
            ac = Aircraft(serial=serial, name=(a.name or serial)[:200], type_id=t.id, status="active", in_service_at=utcnow(),
                          initial_hours=0.0, initial_cycles=0, notes="Создан командой drone-add (агент tbo-agent)")
            s.add(ac)
            s.flush()
        if link is None:
            link, token = drones.create_link(s, ac.id, "tbo-ctl")
            action = "drone_link_created"
        elif a.rotate:
            token = drones.rotate_token(s, link)
            action = "drone_token_rotated"
        else:
            raise DomainError(f"Борт {ac.name} уже подключён. Новый токен: drone-add {serial} --rotate")
        s.commit()
        name = ac.name
    auth.audit(action, "tbo-ctl", None, target=name, details="командная строка сервера")
    auth.flush_audit(5)
    msg = (f"Борт {name} ({serial}) {'создан и ' if created else ''}подключён. "
           "Агент на борту: sudo ./install.sh (адрес сервера и этот токен).")
    return token, msg


if __name__ == "__main__":
    sys.exit(main())
