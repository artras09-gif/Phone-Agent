"""Смешивание лент в одном окне расписания — plan._mix и scheduler.chained.

Просьба 2026-09-30: «если в расписании в одном диапазоне стоят сессии в
разных соцсетях — комбинировать выбранные соцсети случайно». Проверяем на
тысяче розыгрышей: время каждой ленты — из её правила, куски вперемешку,
блок в окне, порядок каждый день свой; одиночные правила — как раньше.
"""
import datetime as dt
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config     # noqa: E402
import plan       # noqa: E402
import scheduler  # noqa: E402

ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


DAY = dt.date(2026, 10, 1)
MIX = [
    {"окно": "21:00-23:00", "минут": [30, 40], "дни": "каждый день", "что": "tiktok", "вкл": True},
    {"окно": "21:30-23:00", "минут": [20, 30], "дни": "каждый день", "что": "reels", "вкл": True},
]
LEAST = config.PLAN_MIX_MIN_CHUNK_MIN * 60

print("--- две ленты в одном окне ---")
orders, bad_total, bad_adjacent, bad_short, bad_start, bad_gap = Counter(), 0, 0, 0, 0, 0
for _ in range(1000):
    items = plan.for_date(DAY, MIX)
    per = Counter()
    for _w, app, sec in items:
        per[app] += sec
    if not (30 * 60 - 1 <= per["tiktok"] <= 40 * 60 + 1 and 20 * 60 - 1 <= per["reels"] <= 30 * 60 + 1):
        bad_total += 1
    apps = [a for _, a, _ in items]
    if any(a == b for a, b in zip(apps, apps[1:])):
        bad_adjacent += 1
    if any(sec < LEAST - 1 for _, _, sec in items):
        bad_short += 1
    start = items[0][0]
    if not (dt.datetime(2026, 10, 1, 21, 0) <= start <= dt.datetime(2026, 10, 1, 23, 0)):
        bad_start += 1
    for (w1, _, s1), (w2, _, _) in zip(items, items[1:]):
        gap = (w2 - w1).total_seconds() - s1
        if not (config.PLAN_MIX_GAP_SEC[0] - 1 <= gap <= config.PLAN_MIX_GAP_SEC[1] + 1):
            bad_gap += 1
    orders["→".join(apps)] += 1

say(bad_total == 0, "каждая лента получает время из своего правила (1000/1000)")
say(bad_adjacent == 0, "одна лента дважды подряд не идёт")
say(bad_short == 0, f"кусков короче {config.PLAN_MIX_MIN_CHUNK_MIN} мин нет")
say(bad_start == 0, "блок начинается внутри окна")
say(bad_gap == 0, f"между кусками пауза {config.PLAN_MIX_GAP_SEC[0]}-{config.PLAN_MIX_GAP_SEC[1]} с")
say(len(orders) >= 6, f"порядков за 1000 дней: {len(orders)}; чаще всего: "
    + ", ".join(f"{k} ({v})" for k, v in orders.most_common(3)))
starts_tt = sum(v for k, v in orders.items() if k.startswith("tiktok"))
say(200 < starts_tt < 800, f"начинает то TikTok ({starts_tt}), то Reels ({1000 - starts_tt})")

print("\n--- что остаётся как раньше ---")
SAME = [dict(MIX[0]), dict(MIX[0], окно="21:30-22:30")]
say(len(plan.for_date(DAY, SAME)) == 2, "одна и та же лента в двух правилах — не режется")
APART = [dict(MIX[0], окно="09:00-10:00"), dict(MIX[1], окно="21:00-22:00")]
items = plan.for_date(DAY, APART)
say(len(items) == 2 and items[0][0].hour == 9, "окна не пересекаются — две обычные сессии")
config.PLAN_MIX = False
say(len(plan.for_date(DAY, MIX)) == 2, "PLAN_MIX = False — по-старому")
config.PLAN_MIX = True
THREE = MIX + [{"окно": "22:00-23:30", "минут": 20, "дни": "каждый день", "что": "shorts", "вкл": True}]
apps = {a for _, a, _ in plan.for_date(DAY, THREE)}
say(apps == {"tiktok", "reels", "shorts"}, "три ленты в пересекающихся окнах — все три в блоке")

print("\n--- телефон между кусками не блокируется ---")
items = plan.for_date(DAY, MIX)
sched = [(w, "session", (a, s)) for w, a, s in items]
flags = [scheduler.chained(sched, i) for i in range(len(sched))]
say(all(flags[:-1]) and not flags[-1], f"все куски кроме последнего — без блокировки: {flags}")
late = sched[0][0] + dt.timedelta(minutes=40)
say(scheduler.chained(sched, 0, now=late), "первый кусок начался с опозданием — всё равно цепочка")
lone = [(dt.datetime(2026, 10, 1, 9), "session", ("tiktok", 1800)),
        (dt.datetime(2026, 10, 1, 21), "session", ("reels", 1800))]
say(not scheduler.chained(lone, 0), "до следующей сессии полдня — блокируем как обычно")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
