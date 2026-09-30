"""Остановка, бесконечный круг в тупике и скорость решения — без телефона.

Жалобы 2026-10-01:
  * «когда пытается выйти из непредвиденного положения, не может
    остановиться по кнопке» — выход и лесенка не слушали «Остановить», а
    сторож расписания сразу запускал следующий кусок смешанного блока;
  * магазин TikTok: кадр «витрина с окном» разбирался как реклама, счётчик
    помех обнулялся, и лесенка вечно стояла на первой ступени;
  * «мимо» решалось 6-7 с: модель думала, темы судились по очереди.
"""
import datetime as dt
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import abort      # noqa: E402
import adb        # noqa: E402
import config     # noqa: E402
import device     # noqa: E402
import human      # noqa: E402
import interests  # noqa: E402
import runlog     # noqa: E402
import scheduler  # noqa: E402
import session    # noqa: E402
import ui         # noqa: E402
import vision     # noqa: E402

ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


human.pause = lambda lo, hi: None

# --- 1. остановка отменяет остаток смешанного блока ---------------------
print("--- «Остановить» посреди смешанного блока ---")
import webui  # noqa: E402

t0 = dt.datetime(2026, 10, 1, 21, 0)
sched = [(t0, "session", ("tiktok", 600)),
         (t0 + dt.timedelta(seconds=630), "session", ("reels", 600)),
         (t0 + dt.timedelta(seconds=1260), "session", ("tiktok", 600)),
         (t0 + dt.timedelta(hours=3), "session", ("shorts", 600))]


class FakeRunner:
    def __init__(self):
        self.on_done, self.on_cancel, self.lines = [], [], []

    def log(self, line):
        self.lines.append(line)


runner = FakeRunner()
keeper = webui.Keeper(runner)
keeper.runner.on_cancel.append(keeper._cancelled)
keeper.day, keeper.schedule, keeper.done, keeper.running = t0.date(), sched, {0}, 0
keeper._cancelled()
say(keeper.done == {0, 1, 2}, f"остаток блока отменён, отдельная сессия позже — нет: {sorted(keeper.done)}")
say(any("отменён" in x for x in runner.lines), "и это сказано в журнале")
keeper.running = None
keeper.done = {0}
keeper._cancelled()
say(keeper.done == {0}, "остановка ручного действия план не трогает")

# --- 2. выход и лесенка слышат «Остановить» -----------------------------
print("\n--- лесенка в тупике слышит «Остановить» ---")
ui.dump = lambda *a, **kw: []
abort.request()
state = {"popups": 3, "blind": False, "rescues": 0, "cfg": {"package": "p"}}
did = session._unstick(state, "p", runlog.Log(live=False))
abort.clear()
say(did == "остановлено", f"лесенка вышла сразу: {did!r}")

# --- 3. счётчик помех обнуляется, только если лента сдвинулась ----------
print("\n--- магазин TikTok: круг на первой ступени ---")
sigs = iter([[10] * 16, [11] * 16, [80] * 16])
vision.frame_signature = lambda png, *a, **kw: next(sigs)
st = {}
session._feed_frozen(st, b"1")
say(st["moved"] is False, "первый кадр после расклинивания — сдвиг не известен")
session._feed_frozen(st, b"2")
say(st["moved"] is False, "почти тот же кадр (витрина с окном) — не сдвинулась")
session._feed_frozen(st, b"3")
say(st["moved"] is True, "новый ролик — сдвинулась")

src = open(os.path.join(os.path.dirname(HERE), "session.py"), encoding="utf-8").read()
say('if state.get("moved"):\n                        state["popups"] = 0' in src,
    "в цикле счётчик обнуляется только при сдвиге ленты")

print("\n--- чужой экран приложения — «назад» сразу ---")
backs = []
device.back = lambda: backs.append(1)
adb.current_app = lambda: ("com.zhiliaoapp.musically", "com.x.ShopActivity")
session._escape_now = lambda *a, **kw: False
session._ensure_feed = lambda cfg, log, timeout=4: True
ui.dismiss_popup = lambda nodes=None: False
state = {"popups": 1, "blind": False, "rescues": 0, "feed_activity": "com.x.SplashActivity",
         "cfg": {"package": "com.zhiliaoapp.musically"}}
did = session._unstick(state, "com.zhiliaoapp.musically", runlog.Log(live=False))
say(backs and "назад" in did, f"первая ступень не ждёт: {did!r}")
backs.clear()
adb.current_app = lambda: ("com.zhiliaoapp.musically", "com.x.SplashActivity")
did = session._unstick(state, "com.zhiliaoapp.musically", runlog.Log(live=False))
say(not backs and "жду" in did, f"тот же экран, что у ленты — как раньше ждём: {did!r}")

# --- 4. скорость решения ------------------------------------------------
print("\n--- темы судятся одновременно ---")


def slow_judge(theme, topic):
    time.sleep(0.5)
    return False


t = time.time()
matched, note = interests._topic_matches({"тема": "комбайн убирает траву"}, "политика, история",
                                         expansions={}, judge=slow_judge)
spent = time.time() - t
say(not matched and spent < 0.8, f"две темы по 0.5 с — за {spent:.2f} с, а не за 1.0")

calls = []


def judge_by_topic(theme, topic):
    calls.append(topic)
    return topic == "история"


matched, note = interests._topic_matches({"тема": "битва при Бородино"}, "политика, история",
                                         expansions={}, judge=judge_by_topic)
say(matched and "история" in note, f"совпадение по второй теме найдено: {note}")

print("\n--- размышление модели ---")
sent = []


def fake_post(path, payload, timeout, kind):
    sent.append(dict(payload))
    if "thinking" in payload and fake_post.reject:
        raise vision.VisionError("unknown parameter: thinking")
    return {"choices": [{"message": {"content": "нет"}}]}


fake_post.reject = False
vision._post = fake_post
vision._completion("m1", [], 10, 0, 5, vision.API)
say(sent[-1].get("thinking") == {"type": "disabled"}, "по умолчанию размышление выключено")
vision._completion("m1", [], 10, 0, 5, vision.API, think=True)
say("thinking" not in sent[-1], "руки в тупике размышление оставляют")
fake_post.reject = True
sent.clear()
vision._completion("m2", [], 10, 0, 5, vision.API)
say(len(sent) == 2 and "thinking" not in sent[1], "сервис не знает параметра — повтор без него")
sent.clear()
vision._completion("m2", [], 10, 0, 5, vision.API)
say(len(sent) == 1 and "thinking" not in sent[0], "и второй раз его этому сервису не шлём")
vision._completion("m3", [], 10, 0, 5, vision.LOCAL)
say("thinking" not in sent[-1], "своей модели в LM Studio параметр не шлётся")

print("\n--- съёмка кадра: сырой или сжатый на телефоне ---")
import gzip  # noqa: E402

RAW = b"\x00" * 1000
delays = {"screencap": 0.12, "screencap | gzip -1": 0.03}


def fake_exec(cmd, timeout=60):
    time.sleep(delays[cmd])
    return gzip.compress(RAW) if "gzip" in cmd else RAW


adb.exec_out = fake_exec
session._CAPTURE.update({"raw": [], "gzip": [], "pick": None})
config.SHOT_COMPRESS = "auto"
for _ in range(6):
    assert session._screencap_raw() == RAW
say(session._CAPTURE["pick"] == "gzip", "на «медленном кабеле» выбран сжатый")
delays.update({"screencap": 0.02, "screencap | gzip -1": 0.1})
session._CAPTURE.update({"raw": [], "gzip": [], "pick": None})
for _ in range(6):
    session._screencap_raw()
say(session._CAPTURE["pick"] == "raw", "на быстром — сырой")


def no_gzip(cmd, timeout=60):
    if "gzip" in cmd:
        raise adb.AdbError("gzip: not found")
    return RAW


adb.exec_out = no_gzip
session._CAPTURE.update({"raw": [0.1], "gzip": [], "pick": None})
say(session._screencap_raw() == RAW and session._CAPTURE["pick"] == "raw",
    "gzip на телефоне нет — молча сырой")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
