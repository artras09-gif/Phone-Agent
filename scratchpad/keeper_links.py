"""Сторож расписания в окне: подборка ссылок и итог дня (поймано 2026-10-16).

Пункт «подборка ссылок» доходил до разбора сессии (`app, seconds = arg` при
arg=None) и падал TypeError — в окне вечерняя подборка не приходила вовсе.
Теперь подборка и итог дня идут мимо очереди действий: телефона они не
касаются и не ждут конца сессии.
"""
import datetime as dt
import os
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402

tmp = tempfile.mkdtemp(prefix="pa_keeper_links_")
config.BASE = tmp
config.DB_PATH = os.path.join(tmp, "jobs.db")

import jobs           # noqa: E402
import scheduler      # noqa: E402
import telegram_bot   # noqa: E402
import webui          # noqa: E402

jobs._SCHEMA_READY = False
ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


digests, stats, started = [], [], []
telegram_bot.digest = lambda force=False: digests.append(1) or "подборка ссылок отправлена: 3"
scheduler.maybe_day_stats = lambda now=None: stats.append(now) or []


class Runner(webui.Runner):
    def start(self, title, fn):
        started.append(title)


runner = Runner()
keeper = webui.Keeper(runner)
now = dt.datetime.now()
keeper.day = now.date()
keeper.schedule = [(now - dt.timedelta(minutes=1), "links", None),
                   (now + dt.timedelta(hours=1), "session", ("tiktok", 600))]
keeper.done = set()

print("--- подборка ссылок по расписанию ---")
try:
    keeper._tick()
    crashed = ""
except Exception as e:
    crashed = f"{type(e).__name__}: {e}"
say(not crashed, f"сторож не падает на пункте «подборка» {crashed}")
time.sleep(0.3)
say(digests == [1], "подборка отправлена")
say(0 in keeper.done and not started, "пункт отмечен, телефон не занимали")
keeper._tick()
time.sleep(0.3)
say(digests == [1], "второй круг — второй раз не шлёт")
say(stats, "итог дня проверяется на каждом круге")

print("\n--- посреди сессии ---")
busy = threading.Event()
real = webui.Runner()
real.start("сессия tiktok", lambda: busy.wait(5))
keeper2 = webui.Keeper(real)
keeper2.day = now.date()
keeper2.schedule = [(now - dt.timedelta(minutes=1), "links", None)]
keeper2.done = set()
keeper2._tick()
time.sleep(0.3)
say(digests == [1, 1], "идёт сессия — подборка всё равно уходит вовремя")
busy.set()

print("\n--- аналитика аккаунтов не вперёд расписания ---")
import accstats  # noqa: E402
accstats.due = lambda now=None: True
started.clear()
keeper3 = webui.Keeper(runner)
keeper3.day = now.date()
keeper3.schedule = [(now - dt.timedelta(minutes=1), "session", ("tiktok", 600))]
keeper3.done = set()
keeper3._tick()
say(started and started[0].startswith("сессия"),
    f"сессия, чьё время подошло, — первой (не ждёт аналитику): {started}")
started.clear()
keeper3._tick()
say(started == ["аналитика аккаунтов"], f"по плану ничего не ждёт — аналитика: {started}")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
