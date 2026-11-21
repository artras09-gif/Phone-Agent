"""Публикация чинит себя сама — без телефона.

17–19.11 Instagram падал семь раз из семи на одном месте: только что
залитый ролик стоял в галерее тёмным квадратом без превью, касание ничего
не открывало, а маршрут считал «выбрал» и падал шагом позже — и так три
попытки подряд, секунда в секунду. 21.11 YouTube показал после «+» другой
экран, и модель, которая умела только «расчистить», ушла не туда.

Проверяется:
  * клетка без превью — ждём картинку, потом жмём (п. 1);
  * нажали, а экран не сменился — ждём и жмём ещё раз, ОДИН раз (п. 2);
  * повтор маршрута терпеливее: 2-я попытка ждёт в 1,5 раза дольше (п. 2);
  * шаг не нашёл кнопку — модель ведёт к ней, путь запоминается, второй
    раз без модели (п. 5);
  * модели не показываются публикация, черновики, сообщения, смена аккаунта.
"""
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402

tmp = tempfile.mkdtemp(prefix="pa_postheal_")
config.BASE = tmp
config.DB_PATH = os.path.join(tmp, "jobs.db")
config.QUEUE_DIR = os.path.join(tmp, "queue")
config.POST_NAV = True

import abort     # noqa: E402
import accstats  # noqa: E402
import adb       # noqa: E402
import device    # noqa: E402
import escape    # noqa: E402
import human     # noqa: E402
import postnav   # noqa: E402
import poster    # noqa: E402
import ui        # noqa: E402
import vision    # noqa: E402

ok = True
W, H = 1080, 2400
PKG = "com.instagram.android"


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


def node(label, y, rid="", x1=100, x2=900, cls="android.widget.Button", desc=""):
    return ui.Node({"text": label, "resource-id": rid, "content-desc": desc, "class": cls,
                    "package": PKG, "clickable": "true", "enabled": "true", "selected": "false",
                    "bounds": f"[{x1},{y}][{x2},{y + 300}]"})


phone = {"screen": "", "taps": []}
SCREENS, LINKS = {}, {}
human.pause = lambda *a, **k: None
poster.time.sleep = lambda s: None
adb.screen_size = lambda: (W, H)
adb.current_app = lambda: (PKG, "")
abort.requested = lambda: False
ui.dump = lambda *a, **k: list(SCREENS.get(phone["screen"], []))
ui.wait_any = lambda sel, timeout=20, interval=0.7: (ui.find_any(ui.dump(), sel), ui.dump())
device.refresh_media = lambda remote: phone["taps"].append("rescan")
log_path = os.path.join(tmp, "log")
os.makedirs(log_path)


def tap(n):
    phone["taps"].append(n.text or n.rid)
    nxt = LINKS.get((phone["screen"], n.text or n.rid))
    if callable(nxt):
        nxt = nxt()
    if nxt:
        phone["screen"] = nxt


ui.tap_node = tap
device.back = lambda: phone["taps"].append("back")
# Описание экрана при ошибке шага снимает НАСТОЯЩИЙ экран — с подключённым
# телефоном это лишний вопрос модели (и чужой кадр). Здесь не нужно.
poster._vision_note = lambda *a, **k: ""

# --------------------------------------------------- п.1: превью ролика
print("1. Клетка свежего ролика без превью")
cell = node("", 600, rid="gallery_grid_item_thumbnail", x1=300, x2=600)
SCREENS["галерея"] = [cell, node("1:32", 860, rid="gallery_grid_item_label", x1=480, x2=590)]
SCREENS["редактор"] = [node("Далее", 2000, rid="clips_right_action_button")]
state = {"dark": 3, "ready_taps": 0}


def raw_screen():
    """Тёмная заливка, пока state["dark"] > 0, потом «картинка»."""
    import random
    px = bytearray(W * H * 4)
    if state["dark"] > 0:
        state["dark"] -= 1
        px[:] = bytes([22, 22, 24, 255]) * (W * H)
    else:
        rnd = random.Random(1)
        px[:] = bytes(rnd.randrange(256) for _ in range(W * H * 4))
    return W, H, px


accstats._raw_screen = raw_screen


def editor_if_ready():
    state["ready_taps"] += 1
    return "редактор" if state["dark"] <= 0 else None


LINKS[("галерея", "gallery_grid_item_thumbnail")] = editor_if_ready
step = {"op": "pick_video", "desc": "наш ролик", "timeout": 20,
        "items": [{"rid": "gallery_grid_item_thumbnail"}],
        "duration": [{"rid": "gallery_grid_item_label"}], "thumb_ready": True,
        "expect": [{"rid": "clips_right_action_button"}], "expect_timeout": 8}
ctx = {"video_seconds": 92.2, "video_name": "v.mp4", "remote": "/sdcard/Movies/v.mp4",
       "patience": 1.0, "package": PKG}
phone.update(screen="галерея", taps=[])
real_time = poster.time.time
clock = {"t": 1000.0}
poster.time.time = lambda: clock.__setitem__("t", clock["t"] + 2.5) or clock["t"]
line = poster.run_step(step, ctx, log_path, 4)
print("   ", line)
say(phone["screen"] == "редактор", "редактор открылся")
say("превью ждал" in line, "в отчёте — сколько ждал превью")
say(phone["taps"].count("gallery_grid_item_thumbnail") == 1, f"по тёмной клетке не жал ({phone['taps']})")
say("rescan" in phone["taps"], "медиатеку пересканировал, пока ждал")

# --------------------------------------------------- п.2: экран не сменился
print("2. Нажал — экран не сменился")
state.update(dark=0, ready_taps=0)
first_tap = {"n": 0}


def editor_second_time():
    first_tap["n"] += 1
    return "редактор" if first_tap["n"] >= 2 else None


LINKS[("галерея", "gallery_grid_item_thumbnail")] = editor_second_time
phone.update(screen="галерея", taps=[])
line = poster.run_step(step, ctx, log_path, 4)
print("   ", line)
say(phone["screen"] == "редактор" and "со второго нажатия" in line, "нажал второй раз — открылось")
say("превью ждал" not in line, "превью было сразу — про ожидание не пишет")
LINKS[("галерея", "gallery_grid_item_thumbnail")] = lambda: None
phone.update(screen="галерея", taps=[])
try:
    poster.run_step(step, ctx, log_path, 4)
    say(False, "не открылось и после второго — должна быть ошибка")
except poster.StepFailed as e:
    say("дважды" in str(e), f"честная ошибка: {str(e)[:60]}")
    say(phone["taps"].count("gallery_grid_item_thumbnail") == 2, "третий раз не жмёт")

print("3. Повтор терпеливее")
seen = []
real_wait = ui.wait_any
ui.wait_any = lambda sel, timeout=20, interval=0.7: (seen.append(timeout), real_wait(sel))[1]
SCREENS["пусто"] = [node("Что-то", 500)]
phone["screen"] = "пусто"
config.POST_NAV = False
config.POST_HELPER = False
for patience in (1.0, 1.5, 2.0):
    try:
        poster.run_step({"op": "optional_tap", "any": [{"text": "Нет такой"}], "timeout": 10},
                        {"patience": patience}, log_path, 2)
    except poster.StepFailed:
        pass
say(seen[0] == 10 and seen[-1] == 20 and 15 in seen, f"ожидание 10 → 15 → 20 с ({seen})")
ui.wait_any = real_wait
config.POST_NAV = True

poster.time.time = real_time       # дальше — настоящие часы (даты пути)

# --------------------------------------------------- п.5: модель ведёт к цели
print("4. Шаг не нашёл кнопку — модель ведёт к ней")
SCREENS.clear()
LINKS.clear()
SCREENS["создание"] = [node("Открыть редактор", 300), node("Другие инструменты", 700),
                       node("Далее", 150, rid="share_button"),
                       node("Опубликовать", 1100), node("Черновики", 1400),
                       node("Сообщения", 1700), node("Создать короткое видео", 2000)]
SCREENS["shorts"] = [node("Добавить из галереи", 1600)]
LINKS[("создание", "Создать короткое видео")] = "shorts"
asked = {"n": 0, "prompts": []}


def fake_ask(png, prompt, **kw):
    asked["n"] += 1
    asked["prompts"].append(prompt)
    for line in prompt.splitlines():
        m = re.match(r"\[(\d+)\] «(.+?)»", line)
        if m and "Создать короткое" in m.group(2):
            return f'{{"кнопка": {m.group(1)}}}'
    return '{"кнопка": 0, "иначе": "back"}'


vision.ask = fake_ask
vision.available = lambda: (True, "test")
escape._screencap = lambda: b"\x89PNG" + b"0" * 100
accstats._app_version = lambda package: "20.25"
gstep = {"op": "tap", "any": [{"text": "Добавить из галереи"}], "desc": "«Добавить из галереи»",
         "timeout": 10}
gctx = {"package": PKG, "title": "YouTube", "target": "shorts", "nav_until": 9, "patience": 1.0,
        "final": [{"rid": "share_button"}, {"text": "Поделиться"}]}
phone.update(screen="создание", taps=[])
line = poster.run_step(gstep, gctx, log_path, 4)
for said in gctx.pop("helper", []):
    print("   ", said)
say(phone["taps"][-1] == "Добавить из галереи", f"дошёл и нажал нужную кнопку ({phone['taps']})")
say("Опубликовать" not in phone["taps"] and "Открыть редактор" not in phone["taps"],
    "лишнего не нажимал")
shown = "\n".join(asked["prompts"])
say(not any(b in shown for b in ("«Опубликовать»", "«Черновики»", "«Сообщения»", "«Далее»")),
    "публикация, черновики, сообщения и финальная «Далее» (share_button) модели не показаны")
route = postnav.load_routes().get("shorts|«Добавить из галереи»", {})
say([s.get("label") for s in route.get("steps", [])] == ["Создать короткое видео"]
    and route.get("ver") == "20.25", f"путь запомнен ({route})")
say(os.path.isfile(os.path.join(postnav.golden_dir("shorts", "20.25"), "route.json")), "эталон сохранён")

print("5. Второй раз — без модели")
asked["n"] = 0
phone.update(screen="создание", taps=[])
poster.run_step(gstep, gctx, log_path, 4)
said = gctx.pop("helper", [])
say(asked["n"] == 0 and any("без модели" in s for s in said), f"по памяти ({said})")

print("6. На экране поста (после ввода текста) модель к цели не ведёт")
asked["n"] = 0
phone.update(screen="создание", taps=[])
config.POST_HELPER = False
try:
    poster.run_step(gstep, dict(gctx, nav_until=3), log_path, 7)
except poster.StepFailed:
    pass
say(asked["n"] == 0, "шаг 7 при nav_until=3 — без модели-проводника")

poster.time.time = real_time
print("ВСЁ ХОРОШО" if ok else "ЕСТЬ ОШИБКИ")
sys.exit(0 if ok else 1)
