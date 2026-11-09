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
# «Мини»: до 2 бортов — 1 290 ₽/мес; «Эксплуатант»: до 4 бортов — 2 290 ₽/мес; «Парк»: до 20 бортов — 9 900 ₽/мес;
# «Предприятие»: закрытый контур для парков от 50 бортов, 600 тыс. ₽ в год (оплата вперёд за год).
TIER_MINI, TIER_MINI_FILL, MINI_SHARE = 1290, 1.5, 0.15       # в среднем 1,5 борта из 2; 15 % платящих бортов
TIER_SMALL, TIER_SMALL_CAP, TIER_SMALL_FILL = 2290, 4, 3      # в среднем занято 3 места из 4
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
LICENSE, IMPLEMENT = 600, 150   # «Предприятие»: 600 тыс. ₽ в год с каждого клиента; внедрение у нового клиента
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
        lic = (base_lic + new_lic) * LICENSE        # годовая оплата вперёд, новые клиенты платят в год подключения
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
    for name, price, n in (("Мини, 2 из 2", TIER_MINI, 2), ("Мини, 1 борт", TIER_MINI, 1), ("Эксплуатант, 4 из 4", TIER_SMALL, 4), ("Эксплуатант, 3 из 4", TIER_SMALL, 3), ("Эксплуатант, 1 борт", TIER_SMALL, 1),
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
