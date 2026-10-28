"""Публикация прерывает сессию (просьба 2026-10-27: «если постинг запланирован
и в его время идёт сессия, то сессия прерывается и происходит постинг»).

Раньше сессию прерывал только пост с точным временем (из бота), а задача из
окна ждала её конца. И даже пост ко времени ждал, если раньше него в
очереди стояла обычная задача: сторож смотрел только на первую.
"""
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config     # noqa: E402

tmp = tempfile.mkdtemp(prefix="pa_preempt_")
config.DB_PATH = os.path.join(tmp, "jobs.db")

import jobs       # noqa: E402
import scheduler  # noqa: E402
import webui      # noqa: E402

jobs._SCHEMA_READY = False
scheduler.scan_watch_dir = lambda: None
ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


class Runner:
    def __init__(self, title):
        self.title, self.why, self.started = title, [], []

    def busy(self):
        return bool(self.title)

    def preempt(self, why):
        if not self.title.startswith("сессия"):
            return False
        self.why.append(why)
        return True

    def start(self, title, fn):
        self.started.append(title)


def tick(title):
    runner = Runner(title)
    webui.Dispatcher(runner)._tick()
    return runner


def clear():
    with jobs.connect() as con:
        con.execute("DELETE FROM jobs")


print("--- задача из окна (без точного времени), идёт сессия ---")
clear()
win = jobs.add("v.mp4", "из окна", "tiktok,reels")
r = tick("сессия reels")
say(r.why == [f"пора публиковать задачу #{win}"], f"сессия приостановлена ради публикации: {r.why}")

print("\n--- задача на потом — сессию не трогаем ---")
clear()
jobs.add("v.mp4", "вечером", "tiktok", run_at=time.time() + 3600, exact=True)
r = tick("сессия tiktok")
say(not r.why, "время поста не подошло — сессия идёт дальше")

print("\n--- пост ко времени за обычной задачей ---")
clear()
jobs.add("v.mp4", "обычная", "tiktok", run_at=time.time() - 600)
timed = jobs.add("v.mp4", "ко времени", "reels", run_at=time.time() - 60, exact=True)
r = tick("сессия tiktok")
say(r.why == [f"пора публиковать задачу #{timed}"],
    f"подошли обе — первым пост ко времени: {r.why}")

print("\n--- дневного лимита постов нет: четвёртая за день тоже прерывает сессию ---")
clear()
for _ in range(3):
    jobs.mark(jobs.add("v.mp4", "уже вышла", "tiktok"), "done")
fourth = jobs.add("v.mp4", "четвёртая", "tiktok")
r = tick("сессия tiktok")
say(r.why == [f"пора публиковать задачу #{fourth}"],
    f"три поста сегодня уже вышли, а четвёртую не откладываем: {r.why}")

print("\n--- не сессия (статистика, публикация) — не прерываем ---")
clear()
jobs.add("v.mp4", "из окна", "tiktok")
r = tick("статистика по запросу")
say(not r.why, "съёмку статистики не рвём, публикация дождётся")

print("\n--- телефон свободен — публикуем ---")
r = tick("")
say(r.started == ["публикация из очереди"], f"сразу в работу: {r.started}")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
