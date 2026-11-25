"""Прогрев: ушли с ленты — заметить моделью и вернуться, запомнив как.

2026-11-25: «во время прогрева проваливается вот в такие странички и
застревает там, и думает, что это лента» — сетка «Интересного» с играющей
верхней плиткой, профиль рекламодателя с «Visit Website», открытый рекламный
пост с «Visit Instagram profile». Свайп листает страницу, кадры меняются, а
проверка по дереву («дерево не снимается — значит лента») ошибалась на
«Интересном» так же, как в ленте. Без телефона и без сети.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402

tmp = tempfile.mkdtemp(prefix="pa_feed_")
config.BASE = tmp
config.DB_PATH = os.path.join(tmp, "jobs.db")
config.QUEUE_DIR = os.path.join(tmp, "queue")
config.FRAMES_DIR = os.path.join(tmp, "frames")
config.SERIAL = "TEST"

import abort    # noqa: E402
import adb      # noqa: E402
import session  # noqa: E402
import vision   # noqa: E402

ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


print("1. Вопрос модели")
for raw, want in (("сетка", "сетка"), ("Поиск", "сетка"), ("Профиль.", "профиль"),
                  ("реклама", "пост"), ("лента", "лента"), ("что-то", "другое"), ("", "другое")):
    say(vision.screen_kind(raw) == want, f"экран «{raw}» -> {want}")
say("Visit Instagram profile" in vision.FEED_FIELD and "сетка из многих плиток" in vision.FEED_FIELD,
    "в вопросе «лента?» названы ловушки со снимков: сетка, «Visit…»")
answers = []
vision.ask = lambda png, prompt, **k: answers.pop(0)
config.FRAME_QUESTIONS = True
vision.provider = lambda: vision.API
answers.append('{"тема": "сетка роликов", "категория": "другое", "реклама": false, '
               '"язык": "en", "лента": false, "экран": "поиск"}')
data = vision.describe_frame(b"png", "")
say(data.get("лента") is False and data.get("экран") == "сетка",
    f"разбор кадра: «лента: false», экран «сетка» ({data.get('экран')})")
answers.append('{"лента": true, "экран": "лента"}')
say(vision.feed_check(b"png") == (True, "лента"), "короткая проверка «теперь лента?»")
answers.append("не знаю")
say(vision.feed_check(b"png") == (None, "другое"), "непонятный ответ — None, а не «лента»")

print("2. Решение по ролику: подозрение, потом возврат")
frames = []
session._grab = lambda state, index, live=False: (os.path.join(tmp, f"f{index}.png"), b"\x89PNG" + b"0" * 50)
session._feed_frozen = lambda state, png: False
session._read_caption = lambda state: ("", "", "")
session.vision.drop_frame = lambda path: None
added = []
session.jobs.add_content = lambda *a, **k: added.append(a)
replies = []
session.vision.describe_frame = lambda *a, **k: replies.pop(0)
state = {"blind": False, "id": "s", "mood": 1.0, "daypart": 1.0}
log = []


class Log(list):
    def append(self, line):
        super().append(line)


log = Log()
replies.append({"тема": "сетка", "категория": "другое", "лента": False, "экран": "сетка"})
v, skip, watch = session._look_and_decide(state, "reels", 1, {}, log)
say(v == "не лента?" and not added and any("без касаний" in x for x in log),
    f"первый кадр «не лента» — подозрение, без касаний, в базу не кладём ({v})")
replies.append({"тема": "сетка", "категория": "другое", "лента": False, "экран": "сетка"})
v, _, _ = session._look_and_decide(state, "reels", 2, {}, log)
say(v == "не лента" and state["screen_kind"] == "сетка", "второй подряд — возвращаться")
state["not_feed_streak"] = 1
log.clear()
plan = session._plan_like(state, "интересно", {}, 10, {}, 1080, 2400, log)
session.interests.like_probability = lambda *a: 1.0
plan = session._plan_like(state, "интересно", {}, 10, {}, 1080, 2400, log)
say(plan["do"] is None and any("сомневается" in x for x in log),
    "пока модель сомневается — лайк не ставится (тап по сетке или «Visit…» уводит глубже)")

print("3. Возврат: лесенка и память")
actions = []
session.device.back = lambda: actions.append("назад")
session.device.open_uri = lambda uri, package=None: actions.append("ссылка")
session.device.stop_app = lambda package: actions.append("стоп")
session._open_feed = lambda cfg, log: actions.append("открыть") or True
session._escape_now = lambda state, goal, log, stuck=False: actions.append("модель") or False
session.human.pause = lambda lo, hi: None
session._trouble_over = lambda state, log=None: None
checks = []
session._in_feed_now = lambda state, cfg: checks.pop(0)
cfg = {"package": "com.instagram.android", "title": "Instagram Reels",
       "open_uri": "instagram://reels_home"}
state = {"blind": False, "screen_kind": "профиль", "not_feed_streak": 2}
log = Log()
checks[:] = [False, False, True]
say(session._recover_feed(state, cfg, log) and actions == ["назад", "назад", "назад", "ссылка"],
    f"«назад», «назад×2» не помогли — помогла ссылка: {actions}")
say(any("вернулся: ссылка (запомнил)" in x for x in log) and state["not_feed_streak"] == 0,
    "в журнале — чем вернулся, счётчик сброшен")
fixes = json.load(open(session._feed_fixes_path(), encoding="utf-8"))
say(fixes.get("Instagram Reels:профиль", {}).get("step") == "ссылка", f"запомнено: {fixes}")
actions.clear()
checks[:] = [True]
state["screen_kind"] = "профиль"
log = Log()
say(session._recover_feed(state, cfg, log) and actions == ["ссылка"]
    and any("как в прошлый раз" in x for x in log),
    "второй раз на профиле — сразу ссылкой, без лишних «назад»")
actions.clear()
checks[:] = [True]
state["screen_kind"] = "сетка"
session._recover_feed(state, cfg, Log())
say(actions == ["назад"], "на другом экране (сетка) — своя память, начинаем с «назад»")
actions.clear()
checks[:] = [False] * 5
say(not session._recover_feed(dict(state, screen_kind="сайт"), cfg, Log())
    and actions == ["назад", "назад", "назад", "ссылка", "стоп", "открыть", "модель"],
    f"ничего не помогло — честно «не вернулся», испробовано всё: {actions}")
actions.clear()
checks[:] = [True]
no_uri = dict(cfg, open_uri="")
session._recover_feed(dict(state, screen_kind="окно"), no_uri, Log())
say("ссылка" not in actions, "без прямой ссылки шаг «ссылка» пропускается")

print("4. Проверка «снова лента» — моделью, не деревом")
importlib_session = session
calls = {"tree": 0}
real_in_feed = None
import importlib  # noqa: E402
session = importlib.reload(importlib_session)
session._grab = lambda state, index, live=False: (None, b"\x89PNG" + b"0" * 50)
session.abort.sleep = lambda s: False
session._off_feed = lambda cfg, timeout=4: calls.__setitem__("tree", calls["tree"] + 1) or ""
session.vision.feed_check = lambda png, shrink=None: (False, "сетка")
st = {"blind": False}
say(session._in_feed_now(st, cfg) is False and calls["tree"] == 0 and st["screen_kind"] == "сетка",
    "модель сказала «сетка» — не лента, хотя дерево сказало бы «лента»")


def broken(png, shrink=None):
    raise session.vision.VisionError("нет связи")


session.vision.feed_check = broken
say(session._in_feed_now(st, cfg) is True and calls["tree"] == 1, "модель не ответила — запасной путь по дереву")
session.abort.sleep = abort.sleep

print("5. Цикл ленты на подставном телефоне")
CALLS = []
REAL_RUN = subprocess.run


def fake_run(cmd, *a, **k):
    if cmd and str(cmd[0]) == str(config.ADB):
        line = " ".join(str(x) for x in cmd[1:])
        CALLS.append(line)
        out = b"Physical size: 1080x2400" if "wm size" in line else b""
        return subprocess.CompletedProcess(cmd, 0, out, b"")
    return REAL_RUN(cmd, *a, **k)


adb.subprocess.run = fake_run
session = importlib.reload(session)
session.device.unlock = lambda: True
session.device.ensure_awake = lambda *a, **k: True
session.vision.tidy_up = lambda: 0
session._open_feed = lambda cfg, log: True
session._ensure_feed = lambda cfg, log, timeout=4: True
session.ui.dismiss_popup = lambda *a, **k: False
session.ui.dump = lambda *a, **k: []
session.interests.ensure_expansion = lambda *a, **k: []
session._retune = lambda *a, **k: None
session.vision.available = lambda: (True, "модель")
session.vision.warm_up = lambda: True
session.vision.where = lambda: "облако"
config.FRAME_SOURCE = "screencap"
config.VISION_CAPTURE = True
config.VISION_DECIDE = True
config.VISION_REALTIME = True
config.LINK_SAVE = False
session._deep_watch_due = lambda state, watched: False
session._leave_due = lambda state, watched: False
session._may_distract = lambda state: False
session._may_read_comments = lambda state: False
session.human.chance = lambda p: False
script = ["не лента?", "не лента", "ok", "stop"]
recovered = []


def fake_decide(state, app, index, taste, log):
    step = script.pop(0)
    if step == "stop":
        abort.request()
        return None, True, None
    if step == "ok":
        state["not_feed_streak"] = 0
        return "нейтрально", False, 0.3
    state["not_feed_streak"] = state.get("not_feed_streak", 0) + 1
    state["screen_kind"] = "сетка"
    return step, None, None


session._look_and_decide = fake_decide
session._recover_feed = lambda state, cfg, log: recovered.append(state.get("screen_kind")) or True
abort.clear()
report = session.browse("reels", 60)
abort.clear()
swipes = [c for c in CALLS if "input swipe" in c or "motionevent" in c]
say(recovered == ["сетка"], f"возврат позван один раз, на втором «не лента» ({recovered})")
summary = next((line for line in report.splitlines() if line.startswith("итог")), "")
say(summary.startswith("итог: 2 видео"),
    f"два кадра «не ленты» роликами не считаются — из четырёх кругов два ролика ({summary})")
say(not any("input tap" in c for c in CALLS), "ни одного касания по экрану, пока ушли с ленты")

print("ВСЁ ХОРОШО" if ok else "ЕСТЬ ОШИБКИ")
sys.exit(0 if ok else 1)
