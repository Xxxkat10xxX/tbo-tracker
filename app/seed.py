"""Демонстрационные данные.

Все даты строятся относительно момента заполнения (T0), поэтому календарные
напоминания и сценарии работают в любой день запуска. Генерация детерминирована
(фиксированный seed ГПСЧ). История полётов загружается через тот же сервис импорта,
что и пользовательские журналы — то есть демо-данные проверяют реальный конвейер.

Подготовленные состояния для сценариев проверки (см. docs/SCENARIOS.md):
  * IMU3-0206 на «Борт 01»: после калибровки 46,6 ч из 50 → «скоро»; журнал №1 (+3,5 ч) → «просрочено»;
  * ESC40-0301 на «Борт 01»: после диагностики 195 циклов из 200 → журнал №1 (+8 циклов) → «просрочено»;
  * FC7-0102 на «Борт 02»: 95,5 ч из 100 после ТО; переставляется на «Борт 03» (у него снят ПК),
    журнал №2 (+4,85 ч) → «просрочено» — наработка сохранилась при перестановке;
  * «Борт 11»: годовое ТО через ~9 дней (календарный порог);
  * BVK2-0405 на «Борт 12»: ТО вычислителя просрочено по календарю (сценарий выполнения работы);
  * ESC40-0318 на «Борт 12»: близок к назначенному ресурсу 400 ч.
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path
from datetime import datetime, timedelta

from sqlalchemy import select

from .config import DEMO_LOGS_DIR
from .db import Base, SessionLocal, engine
from .models import (
    Aircraft,
    AircraftType,
    Component,
    ComponentType,
    ComponentUsageEntry,
    Flight,
    Installation,
    LogImport,
    MaintenanceRecord,
    MaintenanceRule,
    Mission,
    SensorEvent,
    Setting,
)
from .services import logs
from .services.usage import component_totals, aircraft_totals
from .timeutil import iso, utcnow

MANUFACTURER = "tbo-tracker (демо)"
ENGINEERS = ["Смирнов Д. А.", "Кузнецова Е. В.", "Орлов П. С."]
PILOTS = ["Иванов А.", "Петрова М.", "Сидоров К.", "Белов Р."]
MISSIONS = ["Аэрофотосъёмка", "Мониторинг ЛЭП", "Обследование трубопровода", "Картографирование",
            "Мониторинг посевов", "Учебно-тренировочный полёт", "Инспекция кровли"]

M4_SLOTS = [
    {"position": "FC", "label": "Полётный контроллер", "component_type": "flight_controller"},
    {"position": "IMU", "label": "Инерциальный модуль", "component_type": "imu"},
    *[{"position": f"ESC{i}", "label": f"Регулятор оборотов №{i}", "component_type": "esc"} for i in range(1, 5)],
    {"position": "CPU", "label": "Бортовой вычислитель", "component_type": "computer"},
]
VT_SLOTS = [
    {"position": "FC", "label": "Полётный контроллер", "component_type": "flight_controller"},
    {"position": "IMU", "label": "Инерциальный модуль", "component_type": "imu"},
    *[{"position": f"ESC{i}", "label": f"Регулятор подъёмного мотора №{i}", "component_type": "esc"} for i in range(1, 5)],
    {"position": "ESC5", "label": "Регулятор маршевого мотора", "component_type": "esc"},
    {"position": "CPU", "label": "Бортовой вычислитель", "component_type": "computer"},
]

# Демо-характеристики для паспортов (типовые значения для демонстрационных моделей)
TYPE_SPECS = {
    "flight_controller": [("Процессор", "STM32H743, 480 МГц"), ("Датчики", "2× IMU, барометр, магнитометр"),
                          ("Интерфейсы", "8× UART, 2× CAN, I²C, SPI"), ("Питание", "4,5–5,5 В, 0,35 А"),
                          ("Масса", "38 г"), ("Рабочая температура", "−20…+60 °C")],
    "imu": [("Гироскоп", "±2000 °/с, дрейф 3 °/ч"), ("Акселерометр", "±16 g"), ("Частота выдачи", "1 кГц"),
            ("Интерфейс", "SPI / CAN"), ("Виброизоляция", "демпферы, 4 точки"), ("Масса", "22 г")],
    "esc": [("Длительный ток", "40 А"), ("Пиковый ток", "60 А (10 с)"), ("Напряжение", "3–6S LiPo"),
            ("Протокол", "DShot600"), ("Телеметрия", "ток, температура, обороты"), ("Масса", "18 г")],
    "computer": [("Процессор", "4× ARM Cortex-A72, 1,8 ГГц"), ("ОЗУ", "8 ГБ"), ("Накопитель", "64 ГБ eMMC"),
                 ("Интерфейсы", "Ethernet, 2× USB 3.0, MIPI CSI"), ("Питание", "5 В, до 15 Вт"), ("Масса", "62 г")],
}

COMPONENT_TYPES = [
    ("flight_controller", "Полётный контроллер", "TBT-FC7", "TBT.FC7.01", "FC7"),
    ("imu", "Инерциальный модуль", "TBT-IMU3", "TBT.IMU3.02", "IMU3"),
    ("esc", "Регулятор оборотов (ESC)", "TBT-ESC40", "TBT.ESC40.03", "ESC40"),
    ("computer", "Бортовой вычислитель", "TBT-BVK2", "TBT.BVK2.04", "BVK2"),
]

TYPE_DESCRIPTIONS = {
    "flight_controller": "Управление полётом, стабилизация, навигация",
    "imu": "Гироскопы и акселерометры, требует периодической калибровки",
    "esc": "Силовой регулятор оборотов электродвигателя",
    "computer": "Обработка полезной нагрузки, связь, автономные задачи",
}

EVENT_RULES = [
    ("AC-CRASH", "Внеплановый осмотр после падения / столкновения", "aircraft", None,
     "Осмотр планера, лучей, винтов и креплений после события, зафиксированного датчиками; до осмотра борт не допускается."),
    ("IMU-SHOCK", "Проверка и калибровка ИНС после удара", "component", "imu",
     "Проверка дрейфа и калибровка инерциального модуля после ударной перегрузки."),
]

RULES = [
    # code, name, target, ac_type, comp_type, kind, ih, ic, idays, wh, wc, wd, description
    ("AC-INSP", "Периодический осмотр борта (планер, ВМГ, крепёж, разъёмы)", "aircraft", None, None, "periodic",
     25, None, 60, 3, None, 7, "Визуальный осмотр, проверка люфтов, затяжки крепежа, состояния воздушных винтов и разъёмов."),
    ("AC-ANNUAL", "Годовое комплексное ТО борта", "aircraft", None, None, "periodic",
     None, None, 180, None, None, 14, "Полная проверка борта, тест каналов управления и связи, контрольный полёт."),
    ("VT-WING", "Осмотр узлов крыла и механизма перехода (VTOL)", "aircraft", "TBT-VT2", None, "periodic",
     None, 150, None, None, 15, None, "Проверка узлов крепления консолей и мотогондол после каждых 150 взлётов-посадок."),
    ("FC-CHK", "ТО полётного контроллера: разъёмы, журналы, обновление ПО", "component", None, "flight_controller",
     "periodic", 100, None, 180, 10, None, 14, "Проверка разъёмов и демпферов, анализ журналов ошибок, обновление прошивки."),
    ("FC-LIFE", "Назначенный ресурс полётного контроллера", "component", None, "flight_controller", "life",
     2000, None, None, 100, None, None, "По достижении ресурса — замена и списание/капитальный ремонт у производителя."),
    ("IMU-CAL", "Калибровка инерциального модуля", "component", None, "imu", "periodic",
     50, None, 90, 5, None, 10, "Калибровка гироскопов и акселерометров на поворотном стенде, проверка дрейфа."),
    ("ESC-DIAG", "Диагностика регулятора оборотов (нагрев, токи, пайка)", "component", None, "esc", "periodic",
     None, 200, 120, None, 20, 10, "Тепловизионный контроль, проверка пульсаций тока, осмотр паек силовых цепей."),
    ("ESC-LIFE", "Назначенный ресурс регулятора оборотов", "component", None, "esc", "life",
     400, None, None, 40, None, None, "Ресурс силовых ключей и конденсаторов. По достижении — замена."),
    ("BVK-SVC", "ТО бортового вычислителя: накопитель, охлаждение, ПО", "component", None, "computer", "periodic",
     150, None, 120, 15, None, 10, "Проверка SMART накопителя, чистка системы охлаждения, обновление ПО."),
]

# serial, name, type, in_service_days_ago, fly_prob, flights_per_day (min,max), duration_min (min,max), base
AIRCRAFT = [
    ("TBT-M4-001", "Борт 01", "TBT-M4", 210, 0.85, (2, 5), (18, 35), "Площадка «Север»"),
    ("TBT-M4-002", "Борт 02", "TBT-M4", 190, 0.60, (1, 4), (18, 36), "Площадка «Север»"),
    ("TBT-M4-003", "Борт 03", "TBT-M4", 170, 0.55, (1, 3), (20, 38), "Площадка «Юг»"),
    ("TBT-VT2-011", "Борт 11", "TBT-VT2", 200, 0.50, (1, 3), (35, 80), "Аэродром «Луговое»"),
    ("TBT-VT2-012", "Борт 12", "TBT-VT2", 150, 0.55, (1, 2), (40, 85), "Аэродром «Луговое»"),
]
HISTORY_END_DAYS = 4
DEMO_IMAGES = {"TBT-M4-001": "inspection", "TBT-M4-002": "delivery", "TBT-M4-003": "rescue",
               "TBT-VT2-011": "mapping", "TBT-VT2-012": "mapping"}  # история полётов заканчивается за 4 дня до T0


class Seeder:
    def __init__(self, s, now: datetime | None = None):
        self.s = s
        self.now = now or utcnow()
        self.day0 = self.now.replace(hour=0, minute=0, second=0, microsecond=0)
        self.rng = random.Random(20261107)
        self.ct: dict[str, ComponentType] = {}
        self.at: dict[str, AircraftType] = {}
        self.ac: dict[str, Aircraft] = {}
        self.comp: dict[str, Component] = {}
        self.rules: dict[str, MaintenanceRule] = {}

    def day(self, n: float, h: int = 0, m: int = 0) -> datetime:
        return self.day0 + timedelta(days=n, hours=h, minutes=m)

    # ------------------------------------------------------------ справочники и парк
    def create_reference(self):
        s = self.s
        for code, name, *_ in COMPONENT_TYPES:
            self.ct[code] = ComponentType(code=code, name=name, description=TYPE_DESCRIPTIONS[code],
                                          spec_fields=[n for n, _ in TYPE_SPECS[code]])
            s.add(self.ct[code])
        self.at["TBT-M4"] = AircraftType(code="TBT-M4", name="TBT-M4 (квадрокоптер)", category="multirotor",
                                           manufacturer=MANUFACTURER, slots=M4_SLOTS)
        self.at["TBT-VT2"] = AircraftType(code="TBT-VT2", name="TBT-VT2 (VTOL)", category="vtol",
                                            manufacturer=MANUFACTURER, slots=VT_SLOTS)
        s.add_all(self.at.values())
        s.flush()
        for (code, name, target, at_code, ct_code, kind, ih, ic, idays, wh, wc, wd, desc) in RULES:
            r = MaintenanceRule(code=code, name=name, target=target, kind=kind, description=desc,
                                aircraft_type_id=self.at[at_code].id if at_code else None,
                                component_type_id=self.ct[ct_code].id if ct_code else None,
                                interval_hours=ih, interval_cycles=ic, interval_days=idays,
                                warn_hours=wh, warn_cycles=wc, warn_days=wd)
            self.rules[code] = r
            s.add(r)
        # регламенты по данным датчиков: внеплановые работы после каждого падения/столкновения
        for code, name, target, ct_code, desc in EVENT_RULES:
            r = MaintenanceRule(code=code, name=name, target=target, kind="periodic", description=desc,
                                component_type_id=self.ct[ct_code].id if ct_code else None, interval_events=1)
            self.rules[code] = r
            s.add(r)
        s.flush()

    def new_component(self, type_code: str, serial: str, in_service: datetime, initial_h=0.0, initial_c=0, status="stock"):
        _, _, model, pn, _ = next(t for t in COMPONENT_TYPES if t[0] == type_code)
        c = Component(serial=serial, type_id=self.ct[type_code].id, model=model, part_number=pn,
                      manufacturer=MANUFACTURER, firmware=self.rng.choice(["2.4.1", "2.4.3", "2.5.0"]),
                      status=status, manufactured_at=in_service - timedelta(days=self.rng.randint(20, 60)),
                      in_service_at=in_service, initial_hours=initial_h, initial_cycles=initial_c,
                      uid=f"{self.rng.getrandbits(128):032x}",
                      specs=[{"name": n, "value": v} for n, v in TYPE_SPECS[type_code]])
        self.s.add(c)
        self.s.flush()
        self.comp[serial] = c
        return c

    def install(self, c: Component, a: Aircraft, pos: str, at: datetime, reason: str):
        self.s.add(Installation(component_id=c.id, aircraft_id=a.id, position=pos, installed_at=at,
                                install_reason=reason, installed_by=self.rng.choice(ENGINEERS)))
        c.status = "installed"

    def remove(self, serial: str, at: datetime, status: str, reason: str):
        c = self.comp[serial]
        inst = self.s.scalar(select(Installation).where(Installation.component_id == c.id,
                                                        Installation.removed_at.is_(None)))
        inst.removed_at = at
        inst.remove_reason = reason
        inst.removed_by = self.rng.choice(ENGINEERS)
        c.status = status
        return inst

    def create_fleet(self):
        counters = {"FC7": 101, "IMU3": 201, "ESC40": 301, "BVK2": 401}
        prefix = {code: p for code, _, _, _, p in COMPONENT_TYPES}

        def next_serial(type_code):
            p = prefix[type_code]
            n = counters[p]
            counters[p] += 1
            return f"{p}-{n:04d}"

        for serial, name, tcode, days_ago, *_rest, base in AIRCRAFT:
            a = Aircraft(serial=serial, name=name, type_id=self.at[tcode].id, status="active",
                         operator="Демо-эксплуатант", base=base, in_service_at=self.day(-days_ago, 9),
                         image=f"/static/drones/{DEMO_IMAGES[serial]}.svg")
            self.s.add(a)
            self.s.flush()
            self.ac[serial] = a
        # Узлы: каждой позиции — свой экземпляр; порядок задаёт серийные номера
        for type_code in ("flight_controller", "imu", "esc", "computer"):
            for serial, *_ in AIRCRAFT:
                a = self.ac[serial]
                for slot in a.type.slots:
                    if slot["component_type"] == type_code:
                        c = self.new_component(type_code, next_serial(type_code), a.in_service_at)
                        self.install(c, a, slot["position"], a.in_service_at, "Первичная комплектация борта")
            spares = {"flight_controller": 2, "imu": 1, "esc": 3, "computer": 1}[type_code]
            for _ in range(spares):
                self.new_component(type_code, next_serial(type_code), self.day(-120, 10))
        self.s.flush()

    def history_events(self):
        """Прошлые замены и перестановки — для наглядной истории жизни узлов."""
        a1, a2, a4, a5 = (self.ac[k] for k in ("TBT-M4-001", "TBT-M4-002", "TBT-VT2-011", "TBT-VT2-012"))
        s = self.s
        # E1: ПК борта 01 → ремонт → установлен на борт 12
        self.remove("FC7-0101", self.day(-90, 10), "maintenance", "Сбой по цепи питания в полёте, отправлен в ремонт")
        s.flush()
        self.install(self.comp["FC7-0106"], a1, "FC", self.day(-90, 11), "Замена неисправного ПК из резерва")
        s.add(MaintenanceRecord(target="component", component_id=self.comp["FC7-0101"].id, work_type="repair",
                                performed_at=self.day(-80, 15), performed_by=ENGINEERS[0],
                                description="Замена DC/DC-преобразователя, прогон на стенде 4 ч",
                                result="Исправен, годен к установке", status_after="stock"))
        self.comp["FC7-0101"].status = "stock"
        self.remove("FC7-0105", self.day(-70, 9), "stock", "Ротация: ПК с ревизией платы B направлен в резерв")
        s.flush()
        self.install(self.comp["FC7-0101"], a5, "FC", self.day(-70, 10), "Установлен после ремонта")
        # E2: ИНС борта 01 — дрейф, замена резервным
        self.remove("IMU3-0201", self.day(-60, 12), "maintenance", "Повышенный дрейф гироскопа по оси Z")
        s.flush()
        self.install(self.comp["IMU3-0206"], a1, "IMU", self.day(-60, 13), "Замена из резерва")
        s.add(MaintenanceRecord(target="component", component_id=self.comp["IMU3-0201"].id, work_type="repair",
                                performed_at=self.day(-50, 11), performed_by=ENGINEERS[1],
                                description="Перекалибровка на поворотном стенде, замена демпферов",
                                result="Дрейф в норме", status_after="stock"))
        self.comp["IMU3-0201"].status = "stock"
        s.add(ComponentUsageEntry(component_id=self.comp["IMU3-0201"].id, started_at=self.day(-50, 13), hours=3.0,
                                  cycles=12, kind="bench", note="Стендовый прогон после ремонта (термоциклирование)",
                                  created_by=ENGINEERS[1]))
        # E3: ESC борта 11 отказал → исправный ESC переставлен с борта 02, на борт 02 — из резерва
        self.remove("ESC40-0315", self.day(-35, 9), "maintenance", "Перегрев силовых ключей, ожидает диагностики")
        # после перегрева изготовитель сократил индивидуальный ресурс экземпляра → предельный ресурс достигнут
        self.comp["ESC40-0315"].resource_hours = 150
        self.comp["ESC40-0315"].notes = "Письмо изготовителя: ресурс экземпляра после перегрева ограничен 150 ч"
        self.remove("ESC40-0306", self.day(-35, 10), "stock", "Перестановка на борт 11")
        s.flush()
        self.install(self.comp["ESC40-0306"], a4, "ESC3", self.day(-35, 10), "Перестановка с борта 02 (срочный вылет)")
        self.install(self.comp["ESC40-0323"], a2, "ESC2", self.day(-35, 11), "Установка из резерва взамен переставленного")
        # E4: ПК борта 03 не проходит самотест — борт ожидает ПК
        self.remove("FC7-0103", self.day(-6, 14), "maintenance", "Не проходит самотестирование при включении")
        self.ac["TBT-M4-003"].status = "maintenance"
        self.ac["TBT-M4-003"].notes = "Ожидает полётный контроллер (FC7-0103 в ремонте)"
        s.flush()

    # ------------------------------------------------------------ полёты
    def gen_flights(self, cfg, start: datetime, end_day: datetime) -> list[dict]:
        serial, _n, _t, _d, prob, per_day, dur, _b = cfg
        out = []
        d = start.replace(hour=0, minute=0, second=0) + timedelta(days=1)
        while d < end_day:
            if self.rng.random() < prob:
                t = d + timedelta(hours=self.rng.randint(5, 7), minutes=self.rng.randint(0, 59))
                for k in range(self.rng.randint(*per_day)):
                    minutes = self.rng.randint(*dur)
                    fend = t + timedelta(minutes=minutes)
                    out.append({"flight_id": f"{serial}-{t:%Y%m%d}-{k + 1:02d}", "start": iso(t), "end": iso(fend),
                                "cycles": 2 if self.rng.random() < 0.05 else 1,
                                "mission": self.rng.choice(MISSIONS), "pilot": self.rng.choice(PILOTS)})
                    t = fend + timedelta(minutes=self.rng.randint(25, 120))
            d += timedelta(days=1)
        return out

    def import_history(self):
        for cfg in AIRCRAFT:
            serial = cfg[0]
            a = self.ac[serial]
            # «Борт 03» последний раз летал до снятия ПК (−6 сут 14:00)
            end = self.day(-6 if serial == "TBT-M4-003" else -HISTORY_END_DAYS)
            flights = self.gen_flights(cfg, a.in_service_at, end)
            by_month: dict[str, list] = {}
            for f in flights:
                by_month.setdefault(f["start"][:7], []).append(f)
            for month, fl in sorted(by_month.items()):
                doc = {"format": logs.FORMAT_ID, "log_id": f"{serial}-{month}", "aircraft_serial": serial,
                       "source": "Наземная станция управления (демо), выгрузка журнала за месяц", "flights": fl}
                content = json.dumps(doc, ensure_ascii=False, indent=1).encode()
                res = logs.import_log(self.s, content, f"{serial}_{month}.json", "архив эксплуатанта",
                                      compute_alerts=False)
                imp = self.s.get(LogImport, res["id"])
                imp.imported_at = min(datetime.fromisoformat(fl[-1]["end"][:-1]) + timedelta(days=1, hours=3), self.now)
        self.s.flush()

    # ------------------------------------------------------------ история ТО
    def _flights_of(self, kind: str, ent) -> list[tuple]:
        if kind == "aircraft":
            q = select(Flight.started_at, Flight.ended_at, Flight.duration_h, Flight.cycles, Flight.aircraft_id).where(
                Flight.aircraft_id == ent.id)
            return [tuple(r) for r in self.s.execute(q.order_by(Flight.started_at))]
        out = []
        for inst in self.s.scalars(select(Installation).where(Installation.component_id == ent.id)):
            q = select(Flight.started_at, Flight.ended_at, Flight.duration_h, Flight.cycles, Flight.aircraft_id).where(
                Flight.aircraft_id == inst.aircraft_id, Flight.started_at >= inst.installed_at)
            if inst.removed_at:
                q = q.where(Flight.started_at < inst.removed_at)
            out += [tuple(r) for r in self.s.execute(q)]
        return sorted(out)

    def _location(self, comp: Component, at: datetime):
        for inst in self.s.scalars(select(Installation).where(Installation.component_id == comp.id)):
            if inst.installed_at <= at and (inst.removed_at is None or inst.removed_at > at):
                return inst.aircraft_id
        return None

    def _add_record(self, kind, ent, rule, at: datetime, description=None):
        rec = MaintenanceRecord(target=kind, rule_id=rule.id, work_type="scheduled", performed_at=at,
                                performed_by=self.rng.choice(ENGINEERS),
                                description=description or rule.description, result="Выполнено, замечаний нет")
        if kind == "aircraft":
            rec.aircraft_id = ent.id
        else:
            rec.component_id = ent.id
            rec.on_aircraft_id = self._location(ent, at)
        self.s.add(rec)

    def simulate(self, kind, ent, rule, until: datetime):
        """Добросовестный эксплуатант: работы выполняются на 80–95 % интервала."""
        flights = [f for f in self._flights_of(kind, ent) if f[0] < until]
        base = ent.in_service_at
        acc_h = getattr(ent, "initial_hours", 0.0) if kind == "component" else 0.0
        acc_c = getattr(ent, "initial_cycles", 0) if kind == "component" else 0
        frac = self.rng.uniform(0.8, 0.95)

        def cal_due():
            return base + timedelta(days=rule.interval_days * frac) if rule.interval_days else None

        for st, en, dur, cyc, _aid in flights:
            due = cal_due()
            while due is not None and due < st and due < until:
                self._add_record(kind, ent, rule, due.replace(minute=0, second=0))
                base, acc_h, acc_c, frac = due.replace(minute=0, second=0), 0.0, 0, self.rng.uniform(0.8, 0.95)
                due = cal_due()
            acc_h += dur
            acc_c += cyc
            if (rule.interval_hours and acc_h >= rule.interval_hours * frac) or \
               (rule.interval_cycles and acc_c >= rule.interval_cycles * frac):
                at = en + timedelta(minutes=10)
                if at < until:
                    self._add_record(kind, ent, rule, at)
                    base, acc_h, acc_c, frac = at, 0.0, 0, self.rng.uniform(0.8, 0.95)
        limit = min(until, self.day(-HISTORY_END_DAYS + 1))
        due = cal_due()
        while due is not None and due < limit:
            self._add_record(kind, ent, rule, due.replace(minute=0, second=0))
            base, frac = due.replace(minute=0, second=0), self.rng.uniform(0.8, 0.95)
            due = cal_due()

    def time_for_usage(self, kind, ent, hours=None, cycles=None) -> datetime:
        """Момент, начиная с которого наработка объекта равна заданной (по журналу полётов)."""
        acc_h, acc_c = 0.0, 0
        flights = self._flights_of(kind, ent)
        for st, en, dur, cyc, _ in reversed(flights):
            acc_h, acc_c = acc_h + dur, acc_c + cyc
            if (hours is not None and acc_h >= hours) or (cycles is not None and acc_c >= cycles):
                return st - timedelta(minutes=5)
        raise RuntimeError(f"Недостаточно истории для {ent.serial}")

    def maintenance_history(self):
        tuned: dict[tuple, datetime] = {
            ("component", "IMU3-0206", "IMU-CAL"): self.time_for_usage("component", self.comp["IMU3-0206"], hours=46.6),
            ("component", "ESC40-0301", "ESC-DIAG"): self.time_for_usage("component", self.comp["ESC40-0301"], cycles=195),
            ("component", "FC7-0102", "FC-CHK"): self.time_for_usage("component", self.comp["FC7-0102"], hours=95.5),
            ("aircraft", "TBT-VT2-011", "AC-ANNUAL"): self.day(-171, 10),
            ("component", "BVK2-0405", "BVK-SVC"): self.day(-126, 11),
        }
        far = self.now + timedelta(days=1)
        targets = [("aircraft", a) for a in self.ac.values()] + [("component", c) for c in self.comp.values()]
        from .services.maintenance import applicable_rules
        rules = list(self.rules.values())
        for kind, ent in targets:
            for rule in applicable_rules(rules, kind, ent):
                if rule.kind != "periodic":
                    continue
                key = (kind, ent.serial, rule.code)
                if key in tuned:
                    self.simulate(kind, ent, rule, until=tuned[key] - timedelta(hours=1))
                    self._add_record(kind, ent, rule, tuned[key])
                else:
                    self.simulate(kind, ent, rule, until=far)
        self.s.flush()
        # Перед сценарием перестановки борт 03 не должен «гореть» по своим регламентам
        # (он на ТО); его осмотр выполнен при снятии ПК.
        a3 = self.ac["TBT-M4-003"]
        for code in ("AC-INSP",):
            self._add_record("aircraft", a3, self.rules[code], self.day(-6, 16), "Осмотр при снятии ПК")
        # Узел-«долгожитель» у ресурса: ESC40-0318 (борт 12) прибыл из другого парка с наработкой
        c = self.comp["ESC40-0318"]
        used = component_totals(self.s, [c])[c.id].hours - c.initial_hours
        c.initial_hours = round(372.0 - used, 1)
        c.notes = "Получен из резервного парка с наработкой (подтверждена формуляром)"
        self.s.flush()
        # Снимки счётчиков в записях ТО
        recs = list(self.s.scalars(select(MaintenanceRecord)))
        for r in recs:
            before = r.performed_at + timedelta(seconds=1)
            if r.target == "aircraft":
                t = aircraft_totals(self.s, [r.aircraft], before=before)[r.aircraft_id]
            else:
                t = component_totals(self.s, [r.component], before=before)[r.component_id]
            r.hours_at, r.cycles_at = round(t.hours, 2), t.cycles
        self.s.flush()

    def missions(self):
        """Демо-миссии (координаты условные, полевой участок): облёт ЛЭП и площадная аэрофотосъёмка."""
        from .services.autopilot import to_ll
        home = (54.9512, 38.2034)

        def wps(points):
            out = []
            for x, y, alt, hold in points:
                lat, lon = to_ll(*home, x, y)
                out.append({"lat": round(lat, 7), "lon": round(lon, 7), "alt": alt, "hold_s": hold})
            return out
        self.s.add(Mission(name="Облёт ЛЭП, пролёты 1–4", aircraft_id=self.ac["TBT-M4-001"].id, home_lat=home[0], home_lon=home[1],
                           takeoff_alt=35, speed_ms=7, return_home=True,
                           notes="Инспекция опор: удержание 6 с у каждой опоры для съёмки изоляторов",
                           waypoints=wps([(40, 60, 35, 6), (110, 150, 38, 6), (180, 240, 38, 6), (250, 330, 35, 6)])))
        lawn = []
        for i, x in enumerate(range(-150, 151, 60)):
            ys = (60, 420) if i % 2 == 0 else (420, 60)
            lawn += [(x, ys[0], 80, 0), (x, ys[1], 80, 0)]
        self.s.add(Mission(name="Аэрофотосъёмка участка №12 (галсы)", aircraft_id=self.ac["TBT-VT2-011"].id,
                           home_lat=home[0], home_lon=home[1], takeoff_alt=80, speed_ms=14, return_home=True,
                           notes="Перекрытие 70 %, высота 80 м", waypoints=wps(lawn)))
        self.s.flush()

    def sensor_history(self):
        """Историческое столкновение «Борт 02» (−40 сут), после которого выполнены осмотр и калибровка ИНС."""
        a2 = self.ac["TBT-M4-002"]
        f = self.s.scalar(select(Flight).where(Flight.aircraft_id == a2.id, Flight.started_at >= self.day(-40))
                          .order_by(Flight.started_at).limit(1))
        if f is None:
            return
        at = f.started_at + (f.ended_at - f.started_at) / 2
        self.s.add(SensorEvent(flight_id=f.id, aircraft_id=a2.id, occurred_at=at, kind="collision", peak_g=5.6,
                               source="imu", details="анализ акселерометра, 50 Гц (касание ветки при облёте)"))
        done = f.ended_at + timedelta(hours=2)
        self.s.add(MaintenanceRecord(target="aircraft", aircraft_id=a2.id, rule_id=self.rules["AC-CRASH"].id,
                                     work_type="inspection", performed_at=done, performed_by=ENGINEERS[2],
                                     description="Осмотр после столкновения: заменён воздушный винт №2", result="Допущен"))
        imu = self.comp["IMU3-0202"]
        self.s.add(MaintenanceRecord(target="component", component_id=imu.id, rule_id=self.rules["IMU-SHOCK"].id,
                                     work_type="inspection", performed_at=done, performed_by=ENGINEERS[1], on_aircraft_id=a2.id,
                                     description="Проверка дрейфа после удара, калибровка", result="В норме"))
        self.s.flush()

    def run(self):
        self.create_reference()
        self.create_fleet()
        self.history_events()
        self.import_history()
        self.maintenance_history()
        self.sensor_history()
        self.missions()
        self.s.add(Setting(key="demo_anchor", value={"day0": iso(self.day0), "seeded_at": iso(self.now)}))
        self.s.commit()
        write_demo_logs(self.day0)


# ------------------------------------------------------------------ демо-журналы для сценариев
def _flights_at(serial: str, day: datetime, start_h: int, durations: list[int], gap=35, with_ids=True, prefix=""):
    out, t = [], day + timedelta(hours=start_h)
    for i, m in enumerate(durations, 1):
        e = t + timedelta(minutes=m)
        f = {"start": iso(t), "end": iso(e), "cycles": 1, "mission": MISSIONS[i % len(MISSIONS)], "pilot": PILOTS[i % 4]}
        if with_ids:
            f = {"flight_id": f"{serial}-{t:%Y%m%d}-{prefix}{i:02d}", **f}
        out.append(f)
        t = e + timedelta(minutes=gap)
    return out


def _csv(rows: list[dict], serial_default=None) -> str:
    head = "aircraft_serial,flight_id,start_utc,end_utc,cycles,mission,pilot"
    lines = ["# tbo-flightlog/1.0 CSV", head]
    for r in rows:
        lines.append(",".join([r.get("aircraft_serial", serial_default) or "", r.get("flight_id", ""), r["start"],
                               r["end"], str(r.get("cycles", 1)), r.get("mission", ""), r.get("pilot", "")]))
    return "\n".join(lines) + "\n"


FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"


def _xlsx(path, title, header, rows):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = title[:31]
    ws.append(header)
    for r in rows:
        ws.append(r)
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="EEF1F6")
    for i, _ in enumerate(header):
        ws.column_dimensions[chr(65 + i)].width = 20
    wb.save(path)


def _pdf(path, title, header, rows):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    for name, file in (("DejaVu", "DejaVuSans.ttf"), ("DejaVu-Bold", "DejaVuSans-Bold.ttf")):
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(FONT_DIR / file)))
    doc = SimpleDocTemplate(str(path), pagesize=landscape(A4), leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
    tb = Table([header] + rows, repeatRows=1)
    tb.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "DejaVu", 8.5), ("FONT", (0, 0), (-1, 0), "DejaVu-Bold", 8.5),
                            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#9aa3b5")),
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef1f6")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    st = ParagraphStyle("t", fontName="DejaVu-Bold", fontSize=13, spaceAfter=4)
    sub = ParagraphStyle("s", fontName="DejaVu", fontSize=9, textColor=colors.HexColor("#64708a"))
    doc.build([Paragraph(title, st), Paragraph("Эксплуатант: Демо-эксплуатант · сформировано tbo-tracker (демо)", sub),
               Spacer(1, 10), tb])


# ------------------------------------------------------------------ эталонный набор журнала движения узлов (модуль B, Х6)
REF_HEADER = ["Дата", "Операция", "Узел", "С борта", "Борт", "Позиция", "Состояние после снятия", "Основание", "Исполнитель"]
REF_XLSX, REF_PDF = "11_etalon_dvizhenie_uzlov.xlsx", "12_etalon_dvizhenie_uzlov.pdf"


def movement_reference(day0: datetime) -> list[dict]:
    """21 строка журнала движения узлов для только что созданной демо-базы: 10 верных операций (снятие в ремонт и
    в резерв, установка из резерва, перестановка между бортами, списание) и 11 ошибочных — у каждой своя причина.

    expect — ожидаемый итог строки (applied — применена, rejected — отклонена), why — фрагмент причины отклонения
    в сообщении импорта. Даты — относительно day0 демо-базы, поэтому набор создаётся при каждом сбросе демо-данных.
    """
    d1 = day0 - timedelta(days=1)

    def at(h, m=0):
        return (d1 + timedelta(hours=h, minutes=m)).strftime("%d.%m.%Y %H:%M")

    e0, e1, e2 = ENGINEERS
    future = (day0 + timedelta(days=400, hours=9)).strftime("%d.%m.%Y %H:%M")
    rows = [
        (at(8), "Снятие", "IMU3-0203", "Борт 03", "", "", "Ремонт", "Дрейф гироскопа", e0, "applied", None),
        (at(8, 30), "Установка", "IMU3-0201", "", "Борт 03", "IMU", "", "Замена из резерва", e0, "applied", None),
        (at(8, 45), "Снятие", "FC7-0107", "Борт 03", "", "", "Резерв", "Ошибка: узел ещё не установлен", e1,
         "rejected", "не установлен ни на один борт"),
        (at(9), "Установка", "FC7-0107", "", "Борт 03", "FC", "", "ПК из резерва", e1, "applied", None),
        (at(9, 15), "Установка", "FC7-0105", "", "Борт 01", "IMU", "", "Ошибка: позиция другого типа", e1,
         "rejected", "предназначена для типа"),
        (at(9, 30), "Снятие", "BVK2-0403", "Борт 03", "", "", "Резерв", "Обмен вычислителями", e2, "applied", None),
        (at(10), "Перестановка", "BVK2-0402", "Борт 02", "Борт 03", "CPU", "", "Обмен вычислителями", e2, "applied", None),
        (at(10, 10), "Перестановка", "ESC40-0302", "Борт 02", "Борт 11", "ESC5", "", "Ошибка: неверный борт-источник", e2,
         "rejected", "числится на борту"),
        (at(10, 30), "Установка", "BVK2-0403", "", "Борт 02", "CPU", "", "Обмен вычислителями", e2, "applied", None),
        (at(10, 40), "Установка", "ESC40-0303", "", "Борт 02", "ESC4", "", "Ошибка: узел на другом борту", e0,
         "rejected", "уже установлен"),
        (at(10, 50), "Установка", "ESC40-0324", "", "Борт 01", "ESC1", "", "Ошибка: позиция занята", e0,
         "rejected", "занята узлом"),
        (at(11), "Снятие", "FC7-0104", "Борт 11", "", "", "Списание", "Повреждение платы", e1, "applied", None),
        (at(11, 10), "Установка", "ESC40-0315", "", "Борт 11", "ESC3", "", "Ошибка: узел в ремонте", e1,
         "rejected", "установка невозможна"),
        (at(11, 30), "Установка", "FC7-0105", "", "Борт 11", "FC", "", "Замена из резерва", e1, "applied", None),
        (at(11, 40), "Установка", "FC7-9998", "", "Борт 11", "FC", "", "Ошибка: узла нет в учёте", e1,
         "rejected", "не зарегистрирован"),
        (at(11, 50), "Установка", "ESC40-0325", "", "Борт 99", "ESC1", "", "Ошибка: борта нет в учёте", e2,
         "rejected", "не найден"),
        (at(12), "Снятие", "ESC40-0319", "Борт 12", "", "", "Резерв", "Проверка разъёма", e2, "applied", None),
        (at(12, 10), "Ремонт", "ESC40-0320", "Борт 12", "", "", "", "Ошибка: нет такой операции", e2,
         "rejected", "неизвестная операция"),
        (at(12, 30), "Установка", "ESC40-0319", "", "Борт 12", "ESC2", "", "После проверки разъёма", e2, "applied", None),
        ("32.13.2026 10:00", "Установка", "ESC40-0324", "", "Борт 03", "ESC2", "", "Ошибка: несуществующая дата", e0,
         "rejected", "не распознана"),
        (future, "Установка", "ESC40-0324", "", "Борт 01", "ESC2", "", "Ошибка: дата в будущем", e0,
         "rejected", "в будущем"),
    ]
    keys = ("date", "op", "serial", "from", "to", "position", "status", "reason", "by", "expect", "why")
    return [dict(zip(keys, r), line=i) for i, r in enumerate(rows, 1)]


def write_movement_reference(folder: Path, day0: datetime, xlsx_name: str = REF_XLSX, pdf_name: str = REF_PDF) -> list[dict]:
    """Записывает эталонный набор в Excel и PDF (одни и те же 21 строка). Возвращает строки с ожидаемым итогом."""
    rows = movement_reference(day0)
    table = [[r["date"], r["op"], r["serial"], r["from"], r["to"], r["position"], r["status"], r["reason"], r["by"]] for r in rows]
    _xlsx(folder / xlsx_name, "Журнал движения узлов", REF_HEADER, table)
    _pdf(folder / pdf_name, "Журнал учёта установки, демонтажа и перестановки узлов (эталонный набор)", REF_HEADER, table)
    return rows


def write_demo_logs(day0: datetime):
    DEMO_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    for old in DEMO_LOGS_DIR.glob("*"):
        old.unlink()
    d1, d2, d3 = day0 - timedelta(days=1), day0 - timedelta(days=2), day0 - timedelta(days=3)
    files = []

    # 1. Расчёт наработки: Борт 01, 8 полётов (3,52 ч, 8 циклов)
    f1 = _flights_at("TBT-M4-001", d1, 6, [24, 31, 27, 19, 33, 26, 29, 22])
    doc1 = {"format": logs.FORMAT_ID, "log_id": f"TBT-M4-001-{d1:%Y%m%d}", "aircraft_serial": "TBT-M4-001",
            "source": "Наземная станция управления (демо), выгрузка за смену", "flights": f1}
    c1 = json.dumps(doc1, ensure_ascii=False, indent=2).encode()
    (DEMO_LOGS_DIR / "01_TBT-M4-001_smena.json").write_bytes(c1)
    files.append({"file": "01_TBT-M4-001_smena.json", "scenario": 1, "title": "Журнал смены «Борт 01»",
                  "description": "8 полётов, 3,52 ч, 8 циклов. Наработка начисляется борту и всем 7 установленным узлам; "
                                 "IMU3-0206 и ESC40-0301 переходят в «просрочено»."})

    # 2. Перестановка: Борт 03 летает после установки ПК с Борта 02 (6 полётов, 4,85 ч)
    f2 = _flights_at("TBT-M4-003", d1, 7, [45, 52, 48, 41, 50, 55])
    for f in f2:
        f["aircraft_serial"] = "TBT-M4-003"
    (DEMO_LOGS_DIR / "02_TBT-M4-003_posle_perestanovki.csv").write_text(_csv(f2), "utf-8")
    files.append({"file": "02_TBT-M4-003_posle_perestanovki.csv", "scenario": 2,
                  "title": "Журнал «Борт 03» после перестановки ПК",
                  "description": "6 полётов, 4,85 ч. Импортировать ПОСЛЕ перестановки FC7-0102 с «Борт 02» на «Борт 03» "
                                 f"с датой операции {d2:%d.%m.%Y} (позавчера)."})

    # 3a. Повтор того же файла (побайтовая копия)
    (DEMO_LOGS_DIR / "03_TBT-M4-001_smena_POVTOR.json").write_bytes(c1)
    files.append({"file": "03_TBT-M4-001_smena_POVTOR.json", "scenario": 3, "title": "Повторная загрузка журнала №1",
                  "description": "Побайтовая копия журнала №1 под другим именем. Ожидается: «Повтор — не начислено»."})

    # 3b. Другая выгрузка тех же полётов (без ID) + 2 новых полёта
    f3 = _flights_at("TBT-M4-001", d1, 6, [24, 31, 27, 19, 33, 26, 29, 22], with_ids=False)
    f3 += [{"flight_id": f"TBT-M4-001-{d1:%Y%m%d}-E{i}", **f}
           for i, f in enumerate(_flights_at("TBT-M4-001", d1, 16, [21, 18], with_ids=False), 1)]
    for f in f3:
        f["aircraft_serial"] = "TBT-M4-001"
    (DEMO_LOGS_DIR / "04_TBT-M4-001_bortovoi_registrator.csv").write_text(_csv(f3), "utf-8")
    files.append({"file": "04_TBT-M4-001_bortovoi_registrator.csv", "scenario": 3,
                  "title": "Выгрузка бортового регистратора «Борт 01» (пересекается с №1)",
                  "description": "Те же 8 полётов без идентификаторов + 2 новых. Ожидается: начислены только 2 новых полёта, "
                                 "8 распознаны как дубликаты по пересечению времени."})

    # 5. Флот VTOL: многобортовой CSV
    f5 = [dict(f, aircraft_serial="TBT-VT2-011") for f in _flights_at("TBT-VT2-011", d2, 6, [62, 71, 55], prefix="F")]
    f5 += [dict(f, aircraft_serial="TBT-VT2-012") for f in _flights_at("TBT-VT2-012", d2, 7, [74, 66], prefix="F")]
    (DEMO_LOGS_DIR / "05_flot_VTOL.csv").write_text(_csv(f5), "utf-8")
    files.append({"file": "05_flot_VTOL.csv", "scenario": 1, "title": "Сводный журнал звена VTOL (2 борта)",
                  "description": "Один CSV на несколько бортов: «Борт 11» — 3 полёта, «Борт 12» — 2 полёта."})

    # 6. Журнал с ошибками
    bad = [
        {"aircraft_serial": "TBT-VT2-011", "flight_id": f"TBT-VT2-011-{d3:%Y%m%d}-01",
         "start": iso(d3 + timedelta(hours=8)), "end": iso(d3 + timedelta(hours=9, minutes=5))},
        {"aircraft_serial": "TBT-X-999", "flight_id": "X-1", "start": iso(d3 + timedelta(hours=8)),
         "end": iso(d3 + timedelta(hours=9))},
        {"aircraft_serial": "TBT-VT2-011", "flight_id": "BAD-2", "start": iso(d3 + timedelta(hours=12)),
         "end": iso(d3 + timedelta(hours=11))},
        {"aircraft_serial": "TBT-VT2-012", "flight_id": "BAD-3", "start": iso(day0 + timedelta(days=5)),
         "end": iso(day0 + timedelta(days=5, hours=1))},
    ]
    (DEMO_LOGS_DIR / "06_s_oshibkami.csv").write_text(_csv(bad), "utf-8")
    files.append({"file": "06_s_oshibkami.csv", "scenario": 0, "title": "Журнал с ошибками (валидация)",
                  "description": "1 корректный полёт и 3 ошибочных: неизвестный борт, посадка раньше взлёта, дата в будущем."})
    # 7. Падение: полёт «Борт 11» с выборкой акселерометра (50 Гц, окно 60 с вокруг события)
    rate, t0, samples = 50, 330, []
    for i in range(60 * rate):
        tt = i / rate
        if 30.0 <= tt < 30.6:
            g = 0.08 + 0.03 * math.sin(i)            # невесомость: свободное падение
        elif 30.6 <= tt < 30.68:
            g = 7.4 - 2.0 * (tt - 30.6) / 0.08        # удар о землю
        elif tt >= 30.68:
            g = 1.0 + 0.01 * math.sin(i * 0.7)        # лежит на земле
        else:
            g = 1.0 + 0.06 * math.sin(i * 0.9) + 0.04 * math.sin(i * 2.3)   # полёт: вибрации
        samples.append(round(g, 3))
    st = d1 + timedelta(hours=10)
    doc7 = {"format": logs.FORMAT_ID, "aircraft_serial": "TBT-VT2-011", "source": "Бортовой регистратор ПК, телеметрия ИНС",
            "flights": [{"flight_id": f"TBT-VT2-011-{d1:%Y%m%d}-CR", "start": iso(st), "end": iso(st + timedelta(seconds=390)),
                         "cycles": 1, "mission": "Мониторинг ЛЭП", "pilot": PILOTS[0],
                         "imu": {"rate_hz": rate, "t0_s": t0, "accel_g": samples}}]}
    (DEMO_LOGS_DIR / "07_TBT-VT2-011_padenie.json").write_text(json.dumps(doc7, ensure_ascii=False), "utf-8")
    files.append({"file": "07_TBT-VT2-011_padenie.json", "scenario": 10, "title": "Падение «Борт 11» (данные акселерометра)",
                  "description": "Полёт 6,5 мин с выборкой |a| 50 Гц: невесомость 0,6 с и удар 7,4 g. Анализ датчиков фиксирует падение; "
                                 "срабатывают «Осмотр после падения» борта и «Калибровка ИНС после удара» узла IMU3-0204."})
    # 8–10. Excel и PDF: журнал движения узлов (установка, снятие, перестановка) и полёты в Excel
    def at(day, h, m=0):
        return day + timedelta(hours=h, minutes=m)
    moves = [
        (at(d2, 9), "Снятие", "ESC40-0302", "", "", "Ремонт", "Повышенный нагрев по телеметрии", ENGINEERS[0]),
        (at(d2, 9, 30), "Установка", "ESC40-0324", "Борт 01", "ESC2", "", "Замена из резерва", ENGINEERS[0]),
        (at(d2, 10), "Снятие", "ESC40-0316", "", "", "Ремонт", "Не проходит самотест", ENGINEERS[2]),
        (at(d2, 10, 30), "Перестановка", "ESC40-0320", "Борт 11", "ESC4", "", "Перестановка с Борт 12 (срочный вылет)", ENGINEERS[2]),
        (at(d2, 11), "Установка", "ESC40-0325", "Борт 12", "ESC3", "", "Установка из резерва взамен переставленного", ENGINEERS[2]),
        (at(d2, 11, 30), "Установка", "FC7-9999", "Борт 03", "FC", "", "Ошибка оператора: такого узла нет", ENGINEERS[1]),
    ]
    header = ["Дата", "Операция", "Узел", "Борт", "Позиция", "Состояние после снятия", "Основание", "Исполнитель"]
    _xlsx(DEMO_LOGS_DIR / "08_dvizhenie_uzlov.xlsx", "Журнал движения узлов", header,
          [[m[0].strftime("%d.%m.%Y %H:%M"), *m[1:]] for m in moves])
    files.append({"file": "08_dvizhenie_uzlov.xlsx", "scenario": 11, "title": "Журнал движения узлов (Excel)",
                  "description": "6 операций: снятие ESC40-0302 и ESC40-0316 в ремонт, установка из резерва, перестановка ESC40-0320 "
                                 "с «Борт 12» на «Борт 11»; одна строка с несуществующим узлом — будет отклонена."})
    _pdf(DEMO_LOGS_DIR / "09_dvizhenie_uzlov.pdf", "Журнал учёта установки, демонтажа и перестановки узлов",
         header, [[m[0].strftime("%d.%m.%Y %H:%M"), *m[1:]] for m in moves[:5]])
    files.append({"file": "09_dvizhenie_uzlov.pdf", "scenario": 11, "title": "Тот же журнал движения в PDF",
                  "description": "5 корректных операций из журнала №8 в виде PDF-документа. Если журнал №8 уже загружен — "
                                 "все строки распознаются как повтор и не применяются второй раз; если нет — применяются."})
    fl = [dict(f, aircraft_serial="TBT-VT2-012") for f in _flights_at("TBT-VT2-012", d3, 7, [58, 64, 61], prefix="X")]
    _xlsx(DEMO_LOGS_DIR / "10_polety_Bort12.xlsx", "Полёты", ["Борт", "ID полёта", "Взлёт", "Посадка", "Циклы", "Задание", "Столкновения"],
          [[f["aircraft_serial"], f["flight_id"], f["start"].replace("T", " ").rstrip("Z"), f["end"].replace("T", " ").rstrip("Z"),
            1, f["mission"], 1 if i == 1 else 0] for i, f in enumerate(fl)])
    files.append({"file": "10_polety_Bort12.xlsx", "scenario": 4, "title": "Полёты «Борт 12» в Excel",
                  "description": "3 полёта (3 ч 3 мин) в таблице Excel; во втором бортовой журнал отметил столкновение — "
                                 "оно учитывается в мониторинге датчиков."})
    # 11–12. Эталонный набор журнала движения узлов (программа испытаний, характеристика Х6)
    write_movement_reference(DEMO_LOGS_DIR, day0)
    ref_note = ("21 строка: 10 верных операций применяются, 11 ошибочных отклоняются с причиной (у каждой своя). "
                "Загружать на только что сброшенных демо-данных; повторная загрузка ничего не применяет. "
                "Ожидаемый итог по строкам — docs/examples/movements-reference.md.")
    files.append({"file": REF_XLSX, "scenario": 11, "title": "Эталонный журнал движения узлов (Excel)", "description": ref_note})
    files.append({"file": REF_PDF, "scenario": 11, "title": "Эталонный журнал движения узлов (PDF)",
                  "description": "Те же 21 строка в PDF. " + ref_note})
    (DEMO_LOGS_DIR / "index.json").write_text(json.dumps(files, ensure_ascii=False, indent=2), "utf-8")


# ------------------------------------------------------------------ точки входа
def seed(now: datetime | None = None):
    with SessionLocal() as s:
        Seeder(s, now).run()


def seed_if_empty():
    with SessionLocal() as s:
        has = s.scalar(select(Aircraft.id).limit(1))
        anchor = s.get(Setting, "demo_anchor")
    if not has:
        seed()
    elif anchor and not (DEMO_LOGS_DIR / "index.json").exists():
        write_demo_logs(datetime.fromisoformat(anchor.value["day0"][:-1]))


# Учётные записи, сессии и журнал аудита сбросом демо-данных не затрагиваются
KEEP_TABLES = {"users", "auth_sessions", "audit_log"}


def reset_and_seed(now: datetime | None = None) -> dict:
    data_tables = [tb for tb in Base.metadata.sorted_tables if tb.name not in KEEP_TABLES]
    Base.metadata.drop_all(engine, tables=data_tables)
    Base.metadata.create_all(engine)
    seed(now)
    return {"message": "Демо-данные пересозданы"}


if __name__ == "__main__":
    reset_and_seed()
    print("Демо-данные созданы. Журналы для импорта:", DEMO_LOGS_DIR)
