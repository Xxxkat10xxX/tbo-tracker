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
SCENARIOS = {
    "Пессимистичный": dict(growth=0.00, share=[.005, .015, .025, .035, .045], price=5.00, onprem=[0, 0, 1, 1, 1], free_k=0.5),
    "Базовый":        dict(growth=0.20, share=[.010, .030, .050, .070, .090], price=7.08, onprem=[0, 1, 2, 2, 3], free_k=1.0),
    "Оптимистичный":  dict(growth=0.30, share=[.015, .035, .060, .080, .100], price=8.40, onprem=[1, 2, 3, 4, 4], free_k=1.5),
}
# growth — рост числа коммерческих дронов в год; share — платящая доля на конец года;
# price — подписка, тыс. ₽/борт/год; onprem — новые лицензии «закрытый контур» в год;
# free_k — бесплатных бортов на один платный.

# ---------------------------------------------------------------- затраты и прочие доходы
DEV0 = 1500                   # доработка до коммерческой версии (год 0)
TEAM_PILOT, TEAM = 900, 1800  # год 1: 0,5 ставки; далее 0,5 разработчика + 0,5 внедрения/поддержки клиентов
TEAM_STEP_FLEET = 800         # при среднем парке > 800 бортов команда +1 ставка (1 800)
HOSTING = 150
MARKETING = [200, 400, 300, 300, 300]
COMMISSION = 0.10             # бонус продавцам заказчика с подписок и лицензий
VAR_PAID, VAR_FREE = 0.30, 0.10   # хранение журналов, трафик, обращения, тыс. ₽/борт/год
LICENSE, LICENSE_SUPPORT, IMPLEMENT = 900, 0.20, 150   # лицензия, поддержка в год (доля), внедрение
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
        lic = new_lic * LICENSE
        sup = base_lic * LICENSE * LICENSE_SUPPORT
        free = avg * s["free_k"]
        prt = (avg + free) * PARTS_MARGIN if parts else 0.0
        team = (TEAM_PILOT if t == 0 else TEAM) + (TEAM if avg > TEAM_STEP_FLEET else 0)
        cost = (team + HOSTING + MARKETING[t] + COMMISSION * (saas + lic)
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
    print(f"- Итого: {fmt(total)}; при цене 7 080 ₽ клиент получает {total / 7080:.1f} ₽ на 1 ₽ подписки\n")

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
    print(f"- всё то же, но цена 4 000 ₽: {fmt(npv(run(b, price=4.0)[0]))}")
    print(f"- доработка вдвое дороже (3 000): {fmt(npv(run(b, dev0=3000)[0]))}")
    print(f"- ставка 30 %: {fmt(npv(run(b)[0], 0.30))}\n")

    print("## Чувствительность NPV 5 лет, млн ₽ (строки — цена, тыс. ₽/борт/год; столбцы — доля рынка)\n")
    ks = (0.5, 0.75, 1, 1.25, 1.5)
    print("| Цена | " + " | ".join(f"×{k}" for k in ks) + " |")
    print("|---|" + "---|" * len(ks))
    for p in (4.0, 5.0, 7.08, 8.4):
        print(f"| {p:g} | " + " | ".join(f"{npv(run(b, price=p, share_k=k)[0]) / 1000:+.1f}" for k in ks) + " |")

    fixed = TEAM + HOSTING + 300
    unit = 7.08 * (1 - COMMISSION) - VAR_PAID - b["free_k"] * VAR_FREE + (1 + b["free_k"]) * PARTS_MARGIN
    print(f"\nТочка безубыточности: {fixed / unit:.0f} платящих бортов (вклад {unit:.2f} тыс. ₽/борт), "
          f"без узлов {fixed / (7.08 * 0.9 - 0.3 - 0.1):.0f}")
    cf, _ = run(SCENARIOS["Пессимистичный"])
    print(f"Максимальный убыток при остановке после пилота (пессим.): {fmt(npv(cf[:2]))}")


if __name__ == "__main__":
    main()
