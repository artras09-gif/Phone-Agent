"""Модель в маршруте публикации (просьба 2026-11-17): расчистить экран, но не публиковать.

16.11 Threads не опубликовал пост: подпись кончалась «#egipto», открылся список
тем и закрыл «Post». Модель видела это, но в публикации её совет не исполнялся.
Теперь шаг, не нашедший кнопку, зовёт модель: она может нажать безопасную
кнопку, «назад», убрать клавиатуру или подождать. Дерево ниже — снятое живьём
17.11 на телефоне T. Без телефона и без модели: ответы модели подставлены.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config     # noqa: E402

config.LOG_DIR = tempfile.mkdtemp(prefix="pa_helper_")

import escape     # noqa: E402
import poster     # noqa: E402
import postspec   # noqa: E402
import ui         # noqa: E402

ok = True
W, H = 1280, 2772


def check(text, good):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


def node(label="", rid="", bounds=(0, 0, 100, 100), clickable=True, cls="android.view.View"):
    return ui.Node({"text": "", "content-desc": label, "clickable": "true" if clickable else "false",
                    "resource-id": ("com.instagram.barcelona:id/" + rid) if rid else "",
                    "class": cls, "package": "com.instagram.barcelona", "enabled": "true",
                    "bounds": "[%d,%d][%d,%d]" % bounds})


TOPICS = [
    node("Cancel", "navigation_bar_back_button", (0, 145, 195, 301)),
    node("Click to view all your saved drafts.", "", (961, 145, 1114, 282)),
    node("More options", "", (1114, 145, 1270, 282)),
    node("подпись поста #egipto", "", (195, 315, 1228, 1098), clickable=False, cls="android.widget.EditText"),
    node("egipto", "ig_text", (91, 1170, 231, 1227), clickable=False),
    node("Egipto", "ig_text", (91, 1425, 232, 1482), clickable=False),
    node("Dismiss typeahead", "", (1205, 1134, 1238, 1167), clickable=False),
]
POST = node("", "new_thread_screen_post_button", (1034, 1618, 1241, 1748))

print("--- что модели можно нажимать ---")
menu = escape.post_menu(TOPICS, W, H)
check(f"на экране списка тем — только крестик списка: {[escape._label_of(n) for n in menu]}",
      [escape._label_of(n) for n in menu] == ["Dismiss typeahead"])
risky = [node("Post", "new_thread_screen_post_button", (1034, 1618, 1241, 1748)),
         node("Share now", "", (260, 2538, 1228, 2681)),
         node("Save", "igds_alert_dialog_primary_button", (100, 1500, 1180, 1600)),
         node("Don't save", "igds_alert_dialog_secondary_button", (100, 1620, 1180, 1720)),
         node("Remove attachment", "", (917, 723, 956, 762)),
         node("Add photos and videos from your camera roll", "", (147, 518, 278, 650)),
         node("Delete", "", (100, 1800, 600, 1900)),
         node("Allow", "", (100, 2000, 600, 2100)),
         node("Not now", "", (100, 2200, 600, 2300))]
names = [escape._label_of(n) for n in escape.post_menu(risky, W, H)]
check(f"публикация, черновики, удаление, медиа, права — не в списке; «Not now» можно: {names}",
      names == ["Not now"])

print("\n--- разбор ответа модели ---")
check("номер кнопки", escape.parse_post('{"кнопка": 1}', menu) == {"действие": "tap", "кнопка": 1})
check("кнопки нет в списке — ничего", escape.parse_post('{"кнопка": 7}', menu)["действие"] == "none")
check("«убрать клавиатуру» по-русски", escape.parse_post('{"иначе": "клавиатура"}', menu)["действие"] == "keyboard")
check("что-то своё («tap по точке») — ничего",
      escape.parse_post('{"иначе": "point"}', menu)["действие"] == "none")

print("\n--- маршрут: шаг не нашёл «Post» -> модель закрыла список -> шаг нашёл ---")
screen = {"nodes": TOPICS}
tapped, asked = [], []
config.VISION_ENABLED = True
config.POST_HELPER = True
escape.adb.screen_size = poster.adb.screen_size = lambda: (W, H)
escape.adb.current_app = lambda: ("com.instagram.barcelona", "")
escape._full_shot = lambda: (b"png", W, H)
escape.vision.ask = lambda png, prompt, **kw: asked.append(prompt) or '{"кнопка": 1}'
escape.human.pause = poster.human.pause = lambda *a: None
poster.time.sleep = lambda *a: None
poster._snap = lambda *a, **k: None
poster._vision_note = lambda *a: ""


def tap(n):
    tapped.append(escape._label_of(n) or n.rid)
    if escape._label_of(n) == "Dismiss typeahead":
        screen["nodes"] = [x for x in TOPICS if x.rid != "ig_text" and "typeahead" not in x.desc] + [POST]


ui.tap_node = escape.ui.tap_node = poster.ui.tap_node = tap
ui.dump = lambda **kw: screen["nodes"]


def wait_any(selectors, timeout=20, interval=0.7):
    return ui.find_any(screen["nodes"], selectors), screen["nodes"]


ui.wait_any = poster.ui.wait_any = wait_any
poster.ui.dismiss_popup = lambda nodes: False
config.DRY_RUN = False
poster.FINAL["tapped"] = False
poster._publish_went = lambda step, sel, n, label, log_path, index: "экран ушёл"
step = poster.load_recipes()["threads"]["steps"][-1]
ctx = {"package": "com.instagram.barcelona"}
line = poster.run_step(step, ctx, config.LOG_DIR, 13)
check(f"модель закрыла список, маршрут сам нажал «Post»: {tapped}",
      tapped == ["Dismiss typeahead", "com.instagram.barcelona:id/new_thread_screen_post_button"])
check("в вопросе модели — что ищем и что можно", asked and "new thread screen post button" in asked[0]
      and "Dismiss typeahead" in asked[0])
check(f"что сделала модель — в отчёт: {ctx.get('helper')}",
      any("Dismiss typeahead" in s for s in ctx.get("helper", [])))

print("\n--- модель не помогла — честная ошибка, как раньше ---")
screen["nodes"], tapped[:], asked[:] = TOPICS, [], []
escape.vision.ask = lambda png, prompt, **kw: '{"иначе": "none"}'
poster.FINAL["tapped"] = False
try:
    poster.run_step(step, {"package": "com.instagram.barcelona"}, config.LOG_DIR, 13)
    failed = False
except poster.StepFailed as e:
    failed = "не нашёл элемент" in str(e)
check("кнопка так и не нашлась — шаг падает, финальная кнопка не нажата",
      failed and not tapped and not poster.FINAL["tapped"])

escape.vision.ask = lambda png, prompt, **kw: '{"иначе": "back"}'
backs = []
escape.device.back = lambda: backs.append(1)
try:
    poster.run_step(step, {"package": "com.instagram.barcelona"}, config.LOG_DIR, 13)
except poster.StepFailed:
    pass
check(f"одно и то же действие подряд модели не дают: «назад» нажато {len(backs)} раз", len(backs) == 1)

config.POST_HELPER = False
escape.vision.ask = lambda png, prompt, **kw: asked.append(prompt) or '{"кнопка": 1}'
asked.clear()
try:
    poster.run_step(step, {"package": "com.instagram.barcelona"}, config.LOG_DIR, 13)
except poster.StepFailed:
    pass
check("выключатель POST_HELPER — модель не зовут", not asked)

print("\n--- длина подписи Threads: эмодзи — два знака ---")
text = "palabra 🔥 " * 80 + "#egipto"
fitted = postspec.fit(text, "threads")
check(f"обрезано по счёту Threads: {postspec.length(fitted, 'threads')} <= 500 (в символах Python {len(fitted)})",
      postspec.length(fitted, "threads") <= 500 < postspec.length(fitted, "threads") + 12)
check("у остальных сетей счёт обычный", postspec.length("🔥", "tiktok") == 1
      and postspec.length("🔥", "threads") == 2)

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
