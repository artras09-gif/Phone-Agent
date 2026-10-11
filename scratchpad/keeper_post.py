"""Сторож расписания 2026-10-11: пост ко времени, опоздания, полночь.

  * пост с точным временем приостанавливает идущую сессию, а её остаток
    досматривается следом — без отмены остатка блока (это делает только
    кнопка «Остановить»);
  * пока подошла публикация, сессии не стартуют;
  * кусок, начатый с опозданием, укорачивается, чтобы кончить в окне;
  * хвост блока через полночь не теряется при смене суток;
  * план закладывает накладные расходы куска и кончается в окне.
"""
import datetime as dt
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config     # noqa: E402

tmp = tempfile.mkdtemp(prefix="pa_keeper_")
config.DB_PATH = os.path.join(tmp, "jobs.db")

import abort      # noqa: E402
import jobs       # noqa: E402
import plan       # noqa: E402
import scheduler  # noqa: E402
import webui      # noqa: E402

jobs._SCHEMA_READY = False
scheduler._publish = lambda *a, **kw: None
ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


print("--- опоздание куска ---")
say(scheduler.trimmed(600, 30) == 600, "опоздал на 30 с — не трогаю")
say(scheduler.trimmed(600, 200) == 400, "на 200 с — укорачиваю до 400")
say(scheduler.trimmed(600, 550) is None, "осталось меньше двух минут — пропускаю")


class FakeRunner:
    def __init__(self):
        self.on_done, self.on_cancel, self.lines = [], [], []
        self.preempted, self.title, self.started = False, "", []
        self._busy = False

    def log(self, line):
        self.lines.append(line)

    def busy(self):
        return self._busy

    def start(self, title, fn):
        self.title, self.preempted, self._busy = title, False, True
        self.started.append(title)


print("\n--- пост ко времени приостанавливает сессию ---")
real = webui.Runner()
real._thread = type("T", (), {"is_alive": lambda self: True})()
real.title = "сессия tiktok"
abort.clear()
say(real.preempt("пора публиковать #5") and abort.requested() and real.preempted,
    "сессия приостановлена, флаг «ради поста» стоит")
say(not real.preempt("ещё раз"), "повторно не дёргаю")
real.title = "публикация из очереди"
real.preempted = False
say(not real.preempt("пост"), "публикацию ради публикации не прерываю")
abort.clear()

runner = FakeRunner()
keeper = webui.Keeper(runner)
now = dt.datetime.now().replace(microsecond=0)
keeper.day = now.date()
keeper.schedule = [(now - dt.timedelta(minutes=10), "session", ("tiktok", 1200)),
                   (now + dt.timedelta(minutes=11), "session", ("reels", 600))]
keeper.done = {0}
keeper.running, keeper.running_since, keeper.running_seconds = 0, webui.time.time() - 600, 1200
runner.preempted = True
keeper._finished()
say(len(keeper.schedule) == 3 and keeper.schedule[1][2][0] == "tiktok"
    and 590 <= keeper.schedule[1][2][1] <= 600 and keeper.done == {0},
    f"остаток tiktok {keeper.schedule[1][2][1] / 60:.0f} мин вставлен сразу следом, reels на месте")

print("\n--- пока ждёт публикация, сессии не стартуют ---")
runner2 = FakeRunner()
k2 = webui.Keeper(runner2)
k2.day = now.date()
k2.schedule = [(now - dt.timedelta(seconds=5), "session", ("tiktok", 600))]
k2.done = set()
job = jobs.add("v.mp4", "x", targets="tiktok", exact=True)
k2._tick()
say(not runner2.started, "подошёл пост — сессия ждёт")
jobs.cancel(job)
k2._tick()
say(runner2.started == ["сессия tiktok"], "пост ушёл — сессия стартовала")

print("\n--- опоздавший кусок укорачивается или пропускается ---")
runner3 = FakeRunner()
k3 = webui.Keeper(runner3)
k3.day = now.date()
k3.schedule = [(now - dt.timedelta(minutes=9), "session", ("reels", 600)),
               (now - dt.timedelta(minutes=5), "session", ("tiktok", 900))]
k3.done = set()
k3._tick()
say(runner3.started == ["сессия tiktok"] and 0 in k3.done
    and any("пропускаю сессию reels" in x for x in runner3.lines),
    "reels опоздал на 9 из 10 мин — пропущен; tiktok стартовал")
say(any("укорачиваю до 10 мин" in x for x in runner3.lines) and 590 <= k3.running_seconds <= 600,
    "tiktok опоздал на 5 мин — укорочен до 10")

print("\n--- полночь: хвост вчерашнего блока не теряется ---")
runner4 = FakeRunner()
k4 = webui.Keeper(runner4)
yesterday = now.date() - dt.timedelta(days=1)
midnight = dt.datetime.combine(now.date(), dt.time())
k4.day = yesterday
k4.schedule = [(midnight - dt.timedelta(minutes=30), "session", ("tiktok", 1200)),
               (midnight - dt.timedelta(minutes=9), "session", ("reels", 900)),
               (midnight + dt.timedelta(minutes=7), "session", ("tiktok", 900))]
k4.done = {0, 1}
k4.running = 1
runner4._busy = True
scheduler._plan_for_today = lambda day=None: []
k4._tick()
tail = [s for s in k4.schedule if s[0] >= midnight]
say(k4.day == now.date() and len(tail) == 1 and tail[0][2][0] == "tiktok",
    "после полуночи кусок 00:07 остался в плане")
say(k4.running is not None and k4.schedule[k4.running][2][0] == "reels"
    and k4.running in k4.done, "идущий кусок узнаётся — «Остановить» отменит и хвост")
left = scheduler.carry_over(k4.schedule, k4.done, k4.running, midnight)
say(any(not r[1] for r in left), "невыполненный хвост помечен как ещё не сделанный")

print("\n--- план закладывает время на открытие приложения ---")
rules = [{"окно": "21:00-22:00", "минут": 30, "что": a, "дни": "каждый день"}
         for a in ("tiktok", "reels", "shorts")]
end = dt.datetime.combine(dt.date(2026, 10, 12), dt.time(22, 0))
worst = dt.timedelta(0)
for _ in range(200):
    items = plan.for_date(dt.date(2026, 10, 12), rules)
    finish = max(w + dt.timedelta(seconds=s + config.PLAN_PIECE_OVERHEAD_SEC) for w, _, s in items)
    worst = max(worst, finish - end)
say(worst <= dt.timedelta(seconds=1), f"блок с запасом на открытие кончается в окне (худшее {worst})")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
