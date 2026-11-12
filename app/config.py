"""Конфигурация из переменных окружения (TBO_*). Значения проверяются при запуске.

Паролей и секретов по умолчанию нет: пароль БД и начальный пароль администратора читаются только
из файлов (Docker secrets, права 600) — TBO_DATABASE_PASSWORD_FILE, TBO_ADMIN_PASSWORD_FILE.
"""
import ipaddress
import os
from pathlib import Path

VERSION = "1.2.0"
BASE_DIR = Path(__file__).resolve().parent.parent


def _env(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    return default if v is None or v.strip() == "" else v.strip()


def _int(name: str, default: int, lo: int, hi: int) -> int:
    raw = _env(name, str(default))
    try:
        v = int(raw)
    except ValueError:
        raise SystemExit(f"Неверное значение {name}={raw!r}: ожидается целое число") from None
    if not lo <= v <= hi:
        raise SystemExit(f"Неверное значение {name}={v}: допустимо {lo}–{hi}")
    return v


def _flag(name: str, default: bool) -> bool:
    raw = _env(name, "1" if default else "0").lower()
    if raw not in ("1", "0", "true", "false", "yes", "no", "on", "off"):
        raise SystemExit(f"Неверное значение {name}={raw!r}: ожидается 1 или 0")
    return raw in ("1", "true", "yes", "on")


DATA_DIR = Path(_env("TBO_DATA_DIR", str(BASE_DIR / "data")))
DATABASE_URL = _env("TBO_DATABASE_URL", f"sqlite:///{DATA_DIR / 'tbo.db'}")
DATABASE_PASSWORD_FILE = _env("TBO_DATABASE_PASSWORD_FILE")   # пароль PostgreSQL из файла (Docker secret)
DEMO_LOGS_DIR = DATA_DIR / "demo_logs"
UPLOADS_DIR = DATA_DIR / "uploads"            # загруженные фото бортов (раздаются по /media после входа)
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_UPLOAD_MB = _int("TBO_MAX_UPLOAD_MB", 25, 1, 1024)          # журналы, фото, миссии
MAX_BACKUP_UPLOAD_MB = _int("TBO_MAX_BACKUP_UPLOAD_MB", 512, 1, 4096)

# Демонстрационный режим: демо-данные при пустой базе, «Сбросить демо-данные», сценарии проверки.
# В рабочей эксплуатации выключен (сброс демо-данных стирает базу).
DEMO_MODE = _flag("TBO_DEMO_MODE", _flag("TBO_AUTO_SEED", False))
# Документация API (/docs) — только по явному включению и только администратору
API_DOCS = _flag("TBO_API_DOCS", False)
SCHEDULER = _flag("TBO_SCHEDULER", True)

# Сеть и доступ
HOST = _env("TBO_HOST", "127.0.0.1")
PORT = _int("TBO_PORT", 8000, 1, 65535)
PUBLIC_URL = _env("TBO_PUBLIC_URL").rstrip("/")        # https://tbo.example.com — включает Secure-cookie и HSTS
if PUBLIC_URL and not PUBLIC_URL.startswith(("http://", "https://")):
    raise SystemExit("TBO_PUBLIC_URL должен начинаться с http:// или https://")
COOKIE_SECURE = _env("TBO_COOKIE_SECURE", "auto").lower()   # auto | 1 | 0
if COOKIE_SECURE not in ("auto", "1", "0"):
    raise SystemExit("TBO_COOKIE_SECURE: auto, 1 или 0")


def _networks(raw: str):
    out = []
    for part in filter(None, (p.strip() for p in raw.split(","))):
        try:
            out.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            raise SystemExit(f"TBO_TRUSTED_PROXIES: неверный адрес или сеть {part!r}") from None
    return out


# Обратные прокси (Caddy, nginx), которым доверяем X-Forwarded-For / X-Forwarded-Proto
TRUSTED_PROXIES = _networks(_env("TBO_TRUSTED_PROXIES"))
# Имена сайта, принимаемые в заголовке Host (защита от DNS rebinding), через запятую; пусто — любые.
# localhost/127.0.0.1/::1 и имя из TBO_PUBLIC_URL разрешены всегда.
ALLOWED_HOSTS = {h.strip().lower().rstrip(".") for h in _env("TBO_ALLOWED_HOSTS").split(",") if h.strip()}

# Сессии и вход
SESSION_IDLE_MIN = _int("TBO_SESSION_IDLE_MIN", 120, 5, 24 * 60)        # бездействие до выхода
SESSION_MAX_HOURS = _int("TBO_SESSION_MAX_HOURS", 12, 1, 24 * 30)       # предельный срок сессии
LOGIN_MAX_FAILS = _int("TBO_LOGIN_MAX_FAILS", 5, 3, 100)                # неудачных попыток до блокировки
LOGIN_LOCK_MIN = _int("TBO_LOGIN_LOCK_MIN", 15, 1, 24 * 60)             # первая блокировка, далее удваивается
ADMIN_USERNAME = _env("TBO_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD_FILE = _env("TBO_ADMIN_PASSWORD_FILE")                    # начальный администратор (Docker secret)
AUDIT_RETENTION_DAYS = _int("TBO_AUDIT_RETENTION_DAYS", 365, 7, 3650)

# Связь с бортами (агент tbo-agent на бортовом компьютере): пределы маршрута для реального полёта.
# Агент на борту проверяет и свои пределы (agent.conf) — действует более строгое из двух значений.
DRONE_ONLINE_S = 15                                                          # борт на связи, если обмен не старше
DRONE_MAX_ALT_M = _int("TBO_DRONE_MAX_ALT_M", 120, 5, 500)                   # высота точек над точкой взлёта
DRONE_MAX_DIST_M = _int("TBO_DRONE_MAX_DIST_M", 2000, 50, 20000)             # удаление точек от точки взлёта
DRONE_MAX_HOME_OFFSET_M = _int("TBO_DRONE_MAX_HOME_OFFSET_M", 50, 5, 5000)   # борт не дальше от точки взлёта миссии
DRONE_MIN_BATTERY_PCT = _int("TBO_DRONE_MIN_BATTERY_PCT", 50, 10, 100)       # заряд для старта
# Локальная навигация (Technic 6S в помещении: Optical Flow + дальномер VL53L1X до 4 м, ArUco): высота и удаление, м
DRONE_LOCAL_MAX_ALT_M = _int("TBO_DRONE_LOCAL_MAX_ALT_M", 3, 1, 10)
DRONE_LOCAL_MAX_DIST_M = _int("TBO_DRONE_LOCAL_MAX_DIST_M", 20, 2, 100)
DRONE_LOCAL_MAX_SPEED_MS = 2.0
# Видео с борта: web_video_server образа Technic (порт 8080) через сервер — браузер не ходит на борт напрямую
# (политика безопасности страниц: картинки только с этого сайта). 0 — видео выключено.
DRONE_VIDEO_PORT = _int("TBO_DRONE_VIDEO_PORT", 8080, 0, 65535)
DRONE_VIDEO_MAX = _int("TBO_DRONE_VIDEO_MAX", 3, 1, 20)                      # одновременных просмотров на сервер
DRONE_VIDEO_MAX_MIN = _int("TBO_DRONE_VIDEO_MAX_MIN", 30, 1, 240)             # один просмотр — не дольше, мин

# Допуск на расхождение часов при проверке «полёт в будущем», минуты
FUTURE_TOLERANCE_MIN = 10
# Максимальная длительность одного полёта, часы (защита от ошибок в журнале)
MAX_FLIGHT_HOURS = 24
# Окно (дней) для расчёта средней интенсивности эксплуатации при прогнозе
FORECAST_WINDOW_DAYS = 30
