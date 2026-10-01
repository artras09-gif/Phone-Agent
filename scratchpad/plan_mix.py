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

print("\n--- всё внутри окна (жалоба 2026-10-02) ---")
W0, W1 = dt.datetime(2026, 10, 1, 21, 0), dt.datetime(2026, 10, 1, 22, 0)
CROWD = [{"окно": "21:00-22:00", "минут": [40, 60], "дни": "каждый день", "что": app, "вкл": True}
         for app in ("tiktok", "reels", "shorts")]
late, shares, squeezed = 0, [], 0
for _ in range(300):
    plan.LAST_SQUEEZE.clear()
    items = plan.for_date(DAY, CROWD)
    end = max(w + dt.timedelta(seconds=s) for w, _, s in items)
    late += end > W1 + dt.timedelta(seconds=1) or items[0][0] < W0
    per = Counter()
    for _w, app, s in items:
        per[app] += s
    shares.append(max(per.values()) / sum(per.values()))
    squeezed += plan.LAST_SQUEEZE.get(21 * 60, 1) < 0.99
say(late == 0, "три сессии по 40-60 мин в окне 21-22: блок ВЕСЬ внутри окна (300 из 300)")
say(squeezed == 300, "заказано больше окна — время ужато, а не вынесено за окно")
say(max(shares) < 0.45, f"доли лент сохранены (самая большая — {max(shares):.0%})")
real_load = plan.load
plan.load = lambda: CROWD
text = plan.describe(DAY)
plan.load = real_load
say("ужато" in text, "в плане сказано, что время ужато под окно")

SAME3 = [dict(c, что="tiktok") for c in CROWD]
items = plan.for_date(DAY, SAME3)
end = max(w + dt.timedelta(seconds=s) for w, _, s in items)
say(len(items) == 1 and end <= W1 + dt.timedelta(seconds=1),
    f"три правила одной ленты на одно время — одна сессия в окне: {items[0][0]:%H:%M}, "
    f"{items[0][2] / 60:.0f} мин")

ONE = [{"окно": "21:00-22:00", "минут": 30, "дни": "каждый день", "что": "tiktok", "вкл": True}]
ends = [plan.for_date(DAY, ONE)[0] for _ in range(300)]
say(all(w + dt.timedelta(seconds=s) <= W1 + dt.timedelta(seconds=1) for w, _, s in ends),
    "одно правило на 30 мин в окне 21-22 кончается до 22:00 (300 из 300)")
LONG = [dict(ONE[0], минут=[60, 90])]
starts = {plan.for_date(DAY, LONG)[0][0] for _ in range(50)}
say(starts == {W0}, "сессия длиннее окна начинается в начале окна")
POINT = [dict(c, окно="21:00", минут=20) for c in CROWD]
items = plan.for_date(DAY, POINT)
say(items[0][0] == W0 and abs(sum(s for *_, s in items) - 3600) < 5,
    "окно-точка «21:00»: блок ровно с 21:00, без ужатия")

print("\n--- что остаётся как раньше ---")
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
