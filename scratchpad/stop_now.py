"""«Остановить» обрывает действие сразу — без телефона.

Жалоба 2026-11-25: «действия типа листания ленты не прекращаются сразу, как
я нажму стоп». Цикл ленты спрашивал остановку только в начале ролика, и
после кнопки круг доигрывался: лайк, ссылка, комментарии, свайп дальше.
Теперь поток ленты огражден (`abort.guarded`): после кнопки следующее
обращение к телефону не выполняется, сразу уборка — домой и блокировка.

Проверяется:
  * ограждение действует только на свой поток и только после кнопки;
  * `Stopped` не глотается обычным `except Exception`;
  * вопрос модели (`abort.call`) не держит кнопку и идёт в одном постоянном
    потоке (соединение с облаком не рвётся на каждом ролике);
  * живой цикл ленты на подставном adb: после кнопки — ни свайпа, ни касания;
    для сравнения то же без ограждения — свайп был;
  * сбор статистики: после кнопки касаний нет, приложение закрыто.
"""
import os
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402

tmp = tempfile.mkdtemp(prefix="pa_stop_")
config.BASE = tmp
config.DB_PATH = os.path.join(tmp, "jobs.db")
config.FRAMES_DIR = os.path.join(tmp, "frames")
config.SERIAL = "TEST"

import abort    # noqa: E402
import adb      # noqa: E402

ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


# ---------------------------------------------------------- подставной adb
CALLS = []          # (время, команда)
REAL_RUN = subprocess.run


def fake_run(cmd, *a, **k):
    if cmd and str(cmd[0]) == str(config.ADB):
        line = " ".join(str(x) for x in cmd[1:])
        CALLS.append((time.time(), line))
        out = b""
        if "wm size" in line:
            out = b"Physical size: 1080x2400"
        return subprocess.CompletedProcess(cmd, 0, out, b"")
    return REAL_RUN(cmd, *a, **k)


adb.subprocess.run = fake_run

print("1. Ограждение")
abort.clear()
adb.shell("input tap 1 1")
say(CALLS and "input tap" in CALLS[-1][1], "без кнопки — касание идёт")
abort.request()
adb.shell("input tap 2 2")
say("2 2" in CALLS[-1][1], "кнопка нажата, но поток не огражден — касание идёт (уборка, бот)")
with abort.guarded():
    try:
        adb.shell("input tap 3 3")
        say(False, "в огражденном потоке после кнопки касание не должно идти")
    except abort.Stopped:
        say(not any("3 3" in c for _, c in CALLS), "в огражденном потоке после кнопки — Stopped, касания нет")
    other = {}

    def neighbour():
        try:
            adb.shell("input tap 4 4")
            other["ok"] = True
        except abort.Stopped:
            other["ok"] = False

    t = threading.Thread(target=neighbour)
    t.start()
    t.join()
    say(other.get("ok"), "соседний поток (монтаж, бот) ограждения не замечает")
adb.shell("input tap 5 5")
say("5 5" in CALLS[-1][1], "после `with` ограждение снято")

try:
    with abort.guarded():
        try:
            adb.shell("input tap 6 6")
        except Exception:
            say(False, "Stopped проглочен `except Exception`")
except abort.Stopped:
    say(True, "Stopped не глотается обычным `except Exception`")
abort.clear()

print("2. Вопрос модели не держит кнопку")
threads = []


def slow(x):
    threads.append(threading.get_ident())
    time.sleep(0.3 if x < 3 else 5)
    return x * 2


with abort.guarded():
    say(abort.call(slow, 1) == 2 and abort.call(slow, 2) == 4, "ответ модели доходит как обычно")
    say(len(set(threads)) == 1, "оба вопроса — в одном постоянном потоке (соединение живёт)")
    threading.Timer(0.4, abort.request).start()
    started = time.time()
    try:
        abort.call(slow, 3)
        say(False, "кнопка во время вопроса должна оборвать ожидание")
    except abort.Stopped:
        spent = time.time() - started
        say(spent < 1.0, f"кнопка во время 5-секундного вопроса — вышли через {spent:.1f} с")
abort.clear()
say(abort.call(lambda: 7) == 7, "вне ограждения — обычный вызов")

print("3. Лента на подставном телефоне")
import device   # noqa: E402
import human    # noqa: E402
import session  # noqa: E402

device.unlock = lambda: True
device.ensure_awake = lambda *a, **k: True
session.vision.tidy_up = lambda: 0
session._open_feed = lambda cfg, log: True
session._ensure_feed = lambda cfg, log, timeout=4: True
session.ui.dismiss_popup = lambda *a, **k: False
session.ui.dump = lambda *a, **k: []
session.interests.load = lambda: {"тема": "", "язык": ""}
session.interests.ensure_expansion = lambda *a, **k: []
config.FRAME_SOURCE = "screencap"
config.VISION_CAPTURE = False
config.VISION_DECIDE = False
config.LINK_SAVE = False
human.dwell = lambda: 4.0
session._deep_watch_due = lambda state, watched: False
session._leave_due = lambda state, watched: False
session._may_distract = lambda state: False
session._may_read_comments = lambda state: False
session.human.chance = lambda p: False


def run_feed(press_after):
    del CALLS[:]
    abort.clear()
    pressed = {}

    def press():
        pressed["at"] = time.time()
        abort.request()

    threading.Timer(press_after, press).start()
    report = session.browse("reels", 60)
    after = [c for t, c in CALLS if t >= pressed.get("at", 1e18)]
    gestures = [c for c in after if "input swipe" in c or "input tap" in c or "motionevent" in c]
    return report, after, gestures


report, after, gestures = run_feed(1.0)
say("остановлено" in report, "сессия написала «остановлено»")
say(not gestures, f"после кнопки — ни свайпа, ни касания ({len(after)} команд после неё)")
say(any("KEYCODE_HOME" in c for c in after), "уборка прошла: домой")

real_check = abort.check
abort.check = lambda: None                  # как было до правки
report, after, gestures = run_feed(1.0)
abort.check = real_check
say(gestures, f"для сравнения, без ограждения после кнопки было касаний/свайпов: {len(gestures)}")

print("4. Статистика")
import accstats  # noqa: E402

del CALLS[:]
abort.clear()


def read_profile(net, cfg, result):
    adb.shell("input tap 10 10")
    abort.request()                          # нажали посреди съёмки
    adb.shell("input tap 11 11")             # раньше — шаг вслепую
    result["followers"] = 1


accstats._read_profile = read_profile
closed = []
accstats.device.finish_app = lambda package, log=None, **k: closed.append(package)
res = accstats.collect("reels", log=None, shots=False)
say(res["why"] == "остановлено" and not any("11 11" in c for _, c in CALLS),
    "после кнопки следующий шаг не сделан, причина «остановлено»")
say(closed and any("KEYCODE_HOME" in c for _, c in CALLS), "приложение закрыто, телефон на рабочем столе")
abort.clear()
adb.shell("input tap 12 12")
say("12 12" in CALLS[-1][1], "после сбора ограждение снято")

abort.clear()
print("ВСЁ ХОРОШО" if ok else "ЕСТЬ ОШИБКИ")
sys.exit(0 if ok else 1)
