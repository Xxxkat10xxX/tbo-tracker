import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("TBO_DATA_DIR", BASE_DIR / "data"))
DATABASE_URL = os.environ.get("TBO_DATABASE_URL", f"sqlite:///{DATA_DIR / 'tbo.db'}")
DEMO_LOGS_DIR = DATA_DIR / "demo_logs"
# Автозаполнение демо-данными при первом запуске (пустая БД)
AUTO_SEED = os.environ.get("TBO_AUTO_SEED", "1") == "1"
# Допуск на расхождение часов при проверке «полёт в будущем», минуты
FUTURE_TOLERANCE_MIN = 10
# Максимальная длительность одного полёта, часы (защита от ошибок в журнале)
MAX_FLIGHT_HOURS = 24
# Окно (дней) для расчёта средней интенсивности эксплуатации при прогнозе
FORECAST_WINDOW_DAYS = 30
