"""Финансовая модель tbo-tracker: проверка старого расчёта и новая модель.

Запуск: python scripts/economics_model.py
Все суммы — тыс. ₽ без НДС, если не сказано иное. Допущения собраны в начале файла,
их можно менять и пересчитывать. Текст с пояснениями — docs/ECONOMICS.md.
"""

R = 0.20          # ставка дисконтирования: ключевая 14 % + 6 п. п. риска ранней стадии
TAX = 0.25        # налог на прибыль

# ---------------------------------------------------------------- польза для эксплуатанта, ₽/борт/год
ENGINEER_HOUR = 150_000 / 168          # час инженера с взносами (та же база 150 тыс. ₽/мес, что и для разработчиков)
FAIL_RATE = 0.15                       # внеплановых событий из-за пропущенного/позднего ТО на борт в год
FAIL_REPAIR = 100_000                  # ремонт и узлы за событие, ₽ (= 1,5 % от стоимости борта 1 млн ₽ в год / 0,15)
FAIL_DOWNTIME = 6 * 20_000             # 6 дней простоя × 20 тыс. ₽ упущенной выручки в день
REDUCTION = 0.30                       # сервис снижает число таких событий на 30 %
VALUE = {
    "Ручной учёт: 12 ч/год × час инженера": 12 * ENGINEER_HOUR,
    "Ремонт после пропущенного ТО: 0,15 × 100 тыс. × 30 %": FAIL_RATE * FAIL_REPAIR * REDUCTION,
    "Простой после отказа: 0,15 × 6 дн × 20 тыс. × 30 %": FAIL_RATE * FAIL_DOWNTIME * REDUCTION,
    "Замена узлов по факту, а не «с запасом»: 40 тыс. × 8 %": 40_000 * 0.08,
}

# ---------------------------------------------------------------- сценарии
SAM0 = 8000       # дронов в коммерческих услугах, 2026 (Минпромторг)
# ---------------------------------------------------------------- тарифы (₽ без НДС)
# «Старт»: 2 борта бесплатно 2 недели (пробный период, выручки не даёт).
# «Мини»: до 2 бортов — 1 290 ₽/мес; «Эксплуатант»: до 6 бортов — 2 990 ₽/мес; «Парк»: до 20 бортов — 9 900 ₽/мес;
# «Предприятие»: закрытый контур для парков от 50 бортов, бессрочная лицензия 900 тыс. ₽ (разовый платёж).
TIER_MINI, TIER_MINI_FILL, MINI_SHARE = 1290, 1.5, 0.15       # в среднем 1,5 борта из 2; 15 % платящих бортов
TIER_SMALL, TIER_SMALL_CAP, TIER_SMALL_FILL = 2990, 6, 4.5    # в среднем занято 4,5 места из 6
TIER_MID, TIER_MID_CAP, TIER_MID_FILL = 9900, 20, 12          # в среднем занято 12 мест из 20
SMALL_SHARE = 0.30                                            # доля платящих бортов на тарифе «Эксплуатант»
ARPU = (MINI_SHARE * TIER_MINI * 12 / TIER_MINI_FILL + SMALL_SHARE * TIER_SMALL * 12 / TIER_SMALL_FILL
        + (1 - MINI_SHARE - SMALL_SHARE) * TIER_MID * 12 / TIER_MID_FILL) / 1000
ENTERPRISE_FLEET = 50                                         # бортов у одного клиента «Предприятие» (для маржи на узлах)

SCENARIOS = {
    "Пессимистичный": dict(growth=0.00, share=[.005, .015, .025, .035, .045], price=round(ARPU * 0.8, 2), onprem=[0, 0, 1, 1, 1], free_k=0),
    "Базовый":        dict(growth=0.20, share=[.010, .030, .050, .070, .090], price=round(ARPU, 2),       onprem=[0, 1, 2, 2, 3], free_k=0),
    "Оптимистичный":  dict(growth=0.30, share=[.015, .035, .060, .080, .100], price=round(ARPU, 2),       onprem=[1, 2, 3, 4, 4], free_k=0),
}
# growth — рост числа коммерческих дронов в год; share — платящая доля на конец года;
# price — средняя выручка с платящего борта, тыс. ₽/год (пессимистичный: −20 % на скидки и недозагрузку);
# onprem — новые клиенты «Предприятие» в год; free_k — бесплатных бортов на платный (пробный период — 0).

# ---------------------------------------------------------------- затраты и прочие доходы
DEV0 = 1500                   # доработка до коммерческой версии (год 0)
TEAM_PILOT, TEAM = 900, 1800  # год 1: 0,5 ставки; далее 0,5 разработчика + 0,5 внедрения/поддержки клиентов
TEAM_STEP_FLEET = 800         # при среднем парке > 800 бортов команда +1 ставка (1 800)
HOSTING = 150
MARKETING = [200, 400, 300, 300, 300]
COMMISSION = 0.10             # бонус продавцам заказчика с подписок и лицензий
VAR_PAID, VAR_FREE = 0.30, 0.10   # хранение журналов, трафик, обращения, тыс. ₽/борт/год
LICENSE, IMPLEMENT = 900, 150   # «Предприятие»: бессрочная лицензия 900 тыс. ₽ разово; внедрение у нового клиента
PARTS_MARGIN = 0.70           # маржа заказчика на узлах, заказанных по напоминанию, тыс. ₽/подключённый борт/год
#   = 40 тыс. ₽ трат на узлы × 50 % узлы профиля заказчика × 10 % заказов через сервис × 35 % маржи


def npv(cf, r=R):
    return sum(c / (1 + r) ** t for t, c in enumerate(cf))


def irr(cf):
    lo, hi = -0.99, 10.0
    for _ in range(200):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if npv(cf, mid) > 0 else (lo, mid)
    return mid


def payback(cf):
    cum = 0.0
    for t, c in enumerate(cf):
        if t and cum + c >= 0:
            return t - 1 + (-cum) / c
        cum += c
    return None


def run(s, price=None, share_k=1.0, onprem=True, parts=True, dev0=DEV0):
    price = price or s["price"]
    cf, rows, prev, base_lic = [-dev0], [], 0.0, 0
    for t in range(5):
        sam = SAM0 * (1 + s["growth"]) ** (t + 1)
        end = sam * s["share"][t] * share_k
        avg = (prev + end) / 2
        new_lic = s["onprem"][t] if onprem else 0
        saas = avg * price
        lic = new_lic * LICENSE                     # бессрочная лицензия: платёж один раз в год подключения
        sup = 0.0
        free = avg * s["free_k"]
        prt = (avg + free + (base_lic + new_lic) * ENTERPRISE_FLEET) * PARTS_MARGIN if parts else 0.0
        team = (TEAM_PILOT if t == 0 else TEAM) + (TEAM if avg > TEAM_STEP_FLEET else 0)
        cost = (team + HOSTING + MARKETING[t] + COMMISSION * (saas + new_lic * LICENSE)
                + avg * VAR_PAID + free * VAR_FREE + new_lic * IMPLEMENT)
        rev = saas + lic + sup + prt
        rows.append(dict(sam=sam, end=end, avg=avg, saas=saas, lic=lic, sup=sup, parts=prt, rev=rev, cost=cost, cf=rev - cost))
        cf.append(rev - cost)
        prev, base_lic = end, base_lic + new_lic
    return cf, rows


def old_model():
    """Повтор старого расчёта из презентации (для проверки)."""
    def model(dev, sup, promo, price, fleet):
        cf, prev = [-dev], 0
        for i, f in enumerate(fleet):
            avg = (prev + f) / 2
            prev = f
            cf.append(avg * price - (sup + 120 + promo(i) + avg * 0.3))
        return cf
    full = lambda p, fl: model(1800, 900, lambda i: 300 if i == 0 else 200, p, fl)
    light = lambda p, fl: model(900, 450, lambda i: 100, p, fl)
    sc = {"Пессимистичный": (2.5, [30, 100, 240]), "Базовый": (4, [80, 300, 640]), "Оптимистичный": (6, [150, 600, 1200])}
    print("## Проверка старого расчёта (NPV 3 года, тыс. ₽)\n")
    for name, f in (("Полный", full), ("Облегчённый", light)):
        for k, (p, fl) in sc.items():
            cf = f(p, fl)
            print(f"- {name}, {k}: потоки {[round(x) for x in cf]}, NPV {npv(cf):,.0f}")
    for name, f in (("Полный", full), ("Облегчённый", light)):
        cf = f(4, [80, 300, 640, 900, 1200])
        print(f"- {name}, база, 5 лет: NPV {npv(cf):,.0f}, окупаемость {payback(cf):.1f} г.")
    print()


def fmt(x):
    return f"{x:,.0f}".replace(",", " ")


def main():
    old_model()
    total = sum(VALUE.values())
    print("## Польза для эксплуатанта, ₽/борт/год\n")
    for k, v in VALUE.items():
        print(f"- {k}: {fmt(v)}")
    print(f"- Итого: {fmt(total)}")
    for name, price, n in (("Мини, 2 из 2", TIER_MINI, 2), ("Мини, 1 борт", TIER_MINI, 1), ("Эксплуатант, 6 из 6", TIER_SMALL, 6), ("Эксплуатант, 4,5 из 6", TIER_SMALL, 4.5), ("Эксплуатант, 3 борта", TIER_SMALL, 3), 
                           ("Парк, 20 из 20", TIER_MID, 20), ("Парк, 12 из 20", TIER_MID, 12), ("Парк, 5 бортов", TIER_MID, 5)):
        per = price * 12 / n
        print(f"- {name}: {fmt(per)} ₽/борт/год, польза/цена {total / per:.1f}")
    print(f"- Средняя выручка с платящего борта (база): {ARPU * 1000:,.0f} ₽/год, польза/цена {total / (ARPU * 1000):.1f}\n")

    for name, s in SCENARIOS.items():
        cf, rows = run(s)
        print(f"## {name}\n")
        print("| Год | Рынок, бортов | Платящих на конец | Подписка | Лицензии | Поддержка лиц. | Узлы | Выручка | Затраты | Поток |")
        print("|---|---|---|---|---|---|---|---|---|---|")
        print(f"| 0 | | | | | | | | {fmt(DEV0)} | −{fmt(DEV0)} |")
        for t, r in enumerate(rows, 1):
            print(f"| {t} | {fmt(r['sam'])} | {fmt(r['end'])} | {fmt(r['saas'])} | {fmt(r['lic'])} | {fmt(r['sup'])} | "
                  f"{fmt(r['parts'])} | {fmt(r['rev'])} | {fmt(r['cost'])} | {fmt(r['cf'])} |")
        pb = payback(cf)
        print(f"\nNPV 5 лет {fmt(npv(cf))}, NPV 3 года {fmt(npv(cf[:4]))}, IRR {irr(cf) * 100:.0f} %, "
              f"окупаемость {f'{pb:.1f} г.' if pb else 'нет за 5 лет'}, NPV после налога (у действующей компании) "
              f"{fmt(npv([c * (1 - TAX) for c in cf]))}, выручка за 5 лет {fmt(sum(r['rev'] for r in rows))}\n")

    b = SCENARIOS["Базовый"]
    print("## Откуда берётся стоимость (базовый, NPV 5 лет)\n")
    print(f"- только подписка: {fmt(npv(run(b, onprem=False, parts=False)[0]))}")
    print(f"- + лицензии «закрытый контур»: {fmt(npv(run(b, parts=False)[0]))}")
    print(f"- + маржа на узлах: {fmt(npv(run(b)[0]))}")
    print(f"- выручка с борта −30 %: {fmt(npv(run(b, price=ARPU * 0.7)[0]))}")
    print(f"- доля рынка вдвое ниже: {fmt(npv(run(b, share_k=0.5)[0]))}")
    print(f"- доля вдвое ниже и нет «Предприятий»: {fmt(npv(run(b, share_k=0.5, onprem=False)[0]))}")
    print(f"- доработка вдвое дороже (3 000): {fmt(npv(run(b, dev0=3000)[0]))}")
    print(f"- ставка 30 %: {fmt(npv(run(b)[0], 0.30))}\n")

    print("## Чувствительность NPV 5 лет, млн ₽ (строки — выручка с борта к базе; столбцы — доля рынка)\n")
    ks = (0.5, 0.75, 1, 1.25, 1.5)
    print("| Выручка с борта | " + " | ".join(f"×{k}" for k in ks) + " |")
    print("|---|" + "---|" * len(ks))
    for m in (0.6, 0.8, 1.0, 1.2):
        print(f"| ×{m:g} ({ARPU * m * 1000 / 12:,.0f} ₽/мес) | " + " | ".join(f"{npv(run(b, price=ARPU * m, share_k=k)[0]) / 1000:+.1f}" for k in ks) + " |")

    fixed = TEAM + HOSTING + 300
    unit = ARPU * (1 - COMMISSION) - VAR_PAID + PARTS_MARGIN
    print(f"\nТочка безубыточности: {fixed / unit:.0f} платящих бортов (вклад {unit:.2f} тыс. ₽/борт), "
          f"без узлов {fixed / (ARPU * 0.9 - 0.3):.0f}")
    cf, rows = run(b)
    acq = sum(MARKETING) + COMMISSION * sum(r["saas"] for r in rows) + COMMISSION * sum(b["onprem"]) * LICENSE
    ltv = (ARPU * (1 - COMMISSION) - VAR_PAID) / 0.15
    print(f"LTV {ltv:.1f}, CAC {acq / rows[-1]['end']:.2f}, LTV/CAC {ltv / (acq / rows[-1]['end']):.0f}, "
          f"валовая маржа {(ARPU - VAR_PAID) / ARPU * 100:.0f} %, ARPU {ARPU:.3f}")
    cf, _ = run(SCENARIOS["Пессимистичный"])
    print(f"Максимальный убыток при остановке после пилота (пессим.): {fmt(npv(cf[:2]))}")


if __name__ == "__main__":
    main()

# ---------------------------------------------------------------- для модуля C: ROI, дисконтированная окупаемость
def discounted_payback(cf, r=R):
    cum = 0.0
    for t, c in enumerate(cf):
        d = c / (1 + r) ** t
        if t and cum + d >= 0:
            return t - 1 + (-cum) / d
        cum += d
    return None


def roi(cf):
    """ROI = чистый результат за горизонт / максимальная потребность в инвестициях (накопленный минус)."""
    cum, need = 0.0, 0.0
    for c in cf:
        cum += c
        need = min(need, cum)
    return sum(cf) / -need, -need


# ---------------------------------------------------------------- сравнение вариантов для эксплуатанта (одинаковый объём и период)
RPI_KIT = 55.0        # тыс. ₽: Raspberry Pi 5 8GB ≈ 39,5 (xcom-shop) + корпус, питание, SSD, ИБП ≈ 15,5
ADMIN_H_MONTH = 2     # часов администрирования своего сервера в месяц
SETUP_H = 8           # часов инженера на подключение и обучение


def customer_case(fleet, years=5):
    v = sum(VALUE.values()) / 1000                    # тыс. ₽ пользы на борт в год
    h = ENGINEER_HOUR / 1000
    base = {"Ручной учёт (Excel, бумага)": dict(capex=0, opex=0)}
    if fleet <= 2:   sub = TIER_MINI * 12
    elif fleet <= 6: sub = TIER_SMALL * 12
    else:
        big, rest = divmod(fleet, 20)
        sub = big * TIER_MID * 12 + (0 if rest == 0 else min(TIER_MID, -(-rest // 6) * TIER_SMALL) * 12)
    variants = {"tbo-tracker в облаке (подписка)": dict(capex=SETUP_H * h, opex=sub / 1000),
                "tbo-tracker на своём сервере": dict(capex=LICENSE + RPI_KIT + SETUP_H * h, opex=ADMIN_H_MONTH * 12 * h)}
    out = []
    for name, x in variants.items():
        benefit = fleet * v
        net = benefit - x["opex"]
        total_cost = x["capex"] + x["opex"] * years
        pb = x["capex"] / net if net > 0 else None
        out.append((name, x["capex"], x["opex"], benefit, total_cost, benefit * years - total_cost,
                    (benefit * years - total_cost) / total_cost, pb))
    return out


def module_c():
    b = SCENARIOS["Базовый"]
    cf, rows = run(b)
    r_, need = roi(cf)
    pv_inv = sum(-c / (1 + R) ** t for t, c in enumerate(cf) if c < 0)
    print(f"\n## Модуль C (базовый)\nROI 5 лет {r_ * 100:.0f} % (чистый результат {sum(cf):,.0f} / вложения {need:,.0f}); "
          f"дисконт. ROI {npv(cf) / pv_inv * 100:.0f} % (NPV / PV вложений {pv_inv:,.0f}); "
          f"дисконт. окупаемость {discounted_payback(cf):.1f} г.")
    for fleet in (6, 50):
        print(f"\nЭксплуатант, {fleet} бортов, 5 лет (тыс. ₽): вариант | разово | в год | польза в год | затраты 5 лет | эффект 5 лет | ROI | окупаемость, мес")
        print(f"  Ручной учёт: затраты на учёт {fleet * 12 * ENGINEER_HOUR / 1000:,.0f} в год (12 ч на борт), потери от пропусков ТО {fleet * FAIL_RATE * (FAIL_REPAIR + FAIL_DOWNTIME) / 1000:,.0f} в год")
        for name, capex, opex, ben, tc, eff, ro, pb in customer_case(fleet):
            print(f"  {name}: {capex:,.0f} | {opex:,.1f} | {ben:,.0f} | {tc:,.0f} | {eff:,.0f} | {ro * 100:.0f} % | {pb * 12 if pb is not None else float('nan'):.1f}")


if __name__ == "__main__":
    module_c()
