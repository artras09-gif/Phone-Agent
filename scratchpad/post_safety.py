"""Публикация не должна оставлять за собой открытый экран с живой кнопкой.

Две находки живого прогона 2026-09-29, обе без телефона:

  1. `hide_keyboard` жал «назад» дважды, если `dumpsys` отставал, — и второе
     нажатие выбрасывало с формы описания в редактор;
  2. после сухого прогона (и после сбоя посреди маршрута) приложение так и
     стояло на экране публикации с кнопкой «Поделиться».
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import tempfile  # noqa: E402

import config  # noqa: E402
import device  # noqa: E402
import poster  # noqa: E402

# Журналы шагов — во временную папку, не в боевые logs/.
config.LOG_DIR = tempfile.mkdtemp(prefix="pa_post_")

ok = True


def say(good, text):
    global ok
    ok &= good
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


# --- 1. «назад» ровно один раз -----------------------------------------
print("--- клавиатура прячется одним «назад» ---")
saved = device.keyboard_shown, device.back
назад = []
ответы = iter([True, True, True, False] + [False] * 20)   # dumpsys отстаёт
device.keyboard_shown = lambda: next(ответы)
device.back = lambda: назад.append(1)
try:
    device.hide_keyboard()
    say(len(назад) == 1, f"при отстающем dumpsys нажато «назад»: {len(назад)} раз")

    назад.clear()
    device.keyboard_shown = lambda: False
    device.hide_keyboard()
    say(not назад, "клавиатуры нет — «назад» не жмётся вовсе")
finally:
    device.keyboard_shown, device.back = saved


# --- 2. после публикации без нажатия — приложение закрыто --------------
print("\n--- экран публикации не остаётся открытым ---")
закрыто = []
РЕЦЕПТ = {"demo": {"package": "com.example.app", "title": "demo",
                   "steps": [{"op": "sleep", "min": 0, "max": 0}]}}

saved_dev = {n: getattr(device, n) for n in (
    "unlock", "keep_awake", "close_overlays", "push_video", "share_video",
    "wait_for_app", "stop_app", "remove_remote")}
device.unlock = lambda: True
device.keep_awake = lambda on=True: None
device.close_overlays = lambda *a, **k: None
device.push_video = lambda path: "/sdcard/Movies/x.mp4"
device.refresh_media = lambda remote: None      # телефон в этом тесте не трогаем
device.share_video = lambda *a, **k: None
device.wait_for_app = lambda pkg, timeout=25: True
device.stop_app = lambda pkg: закрыто.append(pkg)
device.remove_remote = lambda path: None
poster.wait_upload = lambda *a, **k: True      # телефон в этом тесте не трогаем
saved_step, saved_dry = poster.run_step, config.DRY_RUN
try:
    poster.run_step = lambda step, ctx, log_path, i: "шаг"

    config.DRY_RUN = True
    poster.post("x.mp4", "", "demo", recipes=РЕЦЕПТ)
    say(закрыто == ["com.example.app"], "сухой прогон — приложение закрыто")

    закрыто.clear()
    config.DRY_RUN = False
    poster.run_step = lambda *a: (_ for _ in ()).throw(poster.StepFailed("сбой"))
    poster.post("x.mp4", "", "demo", recipes=РЕЦЕПТ)
    # С 30.10 сбой — ещё и повтор с главной: закрыто после каждой попытки.
    say(закрыто and set(закрыто) == {"com.example.app"},
        "настоящая публикация сорвалась посреди маршрута — тоже закрыто")

    закрыто.clear()
    poster.run_step = lambda step, ctx, log_path, i: "шаг"
    poster.post("x.mp4", "", "demo", recipes=РЕЦЕПТ)
    say(not закрыто, "настоящая публикация прошла — приложение не трогаем")
finally:
    for n, f in saved_dev.items():
        setattr(device, n, f)
    poster.run_step, config.DRY_RUN = saved_step, saved_dry

print("\n--- Instagram: после «Share» лента с видео (дерева нет) — пост ушёл ---")
import ui  # noqa: E402
saved_ui = ui.dump, ui.tap_node
caption_screen = [type("N", (), {"text": "", "desc": "", "rid": "com.instagram.android:id/caption_input_text_view",
                                 "bounds": (52, 1323, 1228, 1479), "clickable": True, "enabled": True,
                                 "selected": False, "area": 1000, "pkg": "", "cls": ""})()]
dumps = iter([caption_screen, [], [], [], []])
ui.dump = lambda retries=3, tolerant=False, timeout=30: next(dumps, [])
poster.human.pause = lambda a, b=None: None
config.DRY_RUN = False
step = {"op": "publish_more", "timeout": 30, "any": [{"text": "Share"}],
        "unless": [{"rid": "caption_input_text_view"}], "first_rid": "share_button"}
try:
    res = poster.run_step(step, {}, tempfile.mkdtemp() if "tempfile" in globals() else ".", 12)
except poster.StepFailed as e:
    res = "ОШИБКА " + str(e)
say("опубликовано" in res, f"три пустых дерева подряд — ушли с экрана подписи: {res[:60]}")

print("\n--- TikTok: в редакторе играет видео (дерева нет) — пауза касанием ---")
taps = []
poster.adb.screen_size = lambda: (1280, 2772)
poster.adb.tap = lambda x, y: taps.append((x, y))
nxt = type("N", (), {"text": "Next", "desc": "", "rid": "", "bounds": (985, 2571, 1240, 2720),
                     "clickable": True, "enabled": True, "selected": False, "area": 1000, "pkg": "", "cls": ""})()
calls = iter([(None, []), (nxt, [nxt])])
ui.wait_any = lambda selectors, timeout=20, interval=0.7: next(calls)
tapped = []
ui.tap_node = lambda n: tapped.append(n.text)
TT = "com.zhiliaoapp.musically"
front = {"pkg": TT}
screen = {"state": "unlocked"}
unlocked = []
poster.adb.current_app = lambda: (front["pkg"], "x")
poster.device.screen_state = lambda: screen["state"]
poster.device.unlock = lambda: unlocked.append(1) or screen.update(state="unlocked") or True
res = poster.run_step({"op": "tap", "timeout": 60, "pause_xy": [0.5, 0.42], "any": [{"text": "Next"}]},
                      {"package": TT}, ".", 3)
say(taps == [(640, 1164)] and tapped == ["Next"], f"касание по видео ({taps}), потом «Next»: {res[:40]}")

print("\n--- приложение ушло с экрана — вслепую не касаемся (живьём 2026-10-21) ---")
taps.clear(); tapped.clear()
front["pkg"] = "com.miui.home"
calls = iter([(None, []), (nxt, [nxt])])
try:
    poster.run_step({"op": "tap", "timeout": 60, "pause_xy": [0.5, 0.42], "any": [{"text": "Next"}],
                     "desc": "из редактора"}, {"package": TT}, ".", 3)
    stopped = ""
except poster.StepFailed as e:
    stopped = str(e)
say(not taps and "ушло с экрана" in stopped and "com.miui.home" in stopped,
    f"на экране рабочий стол — касания нет, честная остановка: {stopped[:70]}")

print("\n--- экран погас, пока TikTok открывал большой файл — будим и ждём дальше ---")
taps.clear(); tapped.clear(); unlocked.clear()
front["pkg"] = TT
screen["state"] = "off"
calls = iter([(None, []), (nxt, [nxt])])
res = poster.run_step({"op": "tap", "timeout": 60, "pause_xy": [0.5, 0.42], "any": [{"text": "Next"}]},
                      {"package": TT}, ".", 3)
say(unlocked == [1] and tapped == ["Next"] and not taps,
    f"разбудил и разблокировал, «Next» нашёлся без касаний вслепую: {res[:30]}")
print("\n--- Instagram: реклама Edits поверх редактора (в дереве её нет) ---")
editor_next = type("N", (), {"text": "Далее", "desc": "", "rid": "com.instagram.android:id/clips_right_action_button",
                             "bounds": (807, 2144, 1047, 2177), "clickable": True, "enabled": True,
                             "selected": False, "area": 1000, "pkg": "", "cls": ""})()
caption = type("N", (), {"text": "Добавьте подпись", "desc": "", "rid": "com.instagram.android:id/caption_input_text_view",
                         "bounds": (44, 1101, 1036, 1233), "clickable": True, "enabled": True,
                         "selected": False, "area": 1000, "pkg": "", "cls": ""})()
screens = {"now": "editor"}
backs, tapped2 = [], []


def wait_any2(selectors, timeout=20, interval=0.7):
    if screens["now"] == "caption":
        return caption, [caption]
    return None, [editor_next]


ui.wait_any = wait_any2
ui.dump = lambda retries=3, tolerant=False, timeout=30: [editor_next] if screens["now"] != "caption" else [caption]
ui.tap_node = lambda n: tapped2.append(n.text) or screens.update(now="caption" if screens["now"] == "editor_clear" else screens["now"])
poster.device.back = lambda: backs.append(1) or screens.update(now="editor_clear")
poster._overlay_on_screen = lambda: screens["now"] == "editor"
poster._snap = lambda *a, **k: None
step = {"op": "clear_overlay", "timeout": 3, "tries": 2,
        "until": [{"rid": "caption_input_text_view"}], "still": [{"rid": "clips_right_action_button"}]}
res = poster.run_step(step, {}, ".", 5)
say(backs == [1] and tapped2 == ["Далее"] and "закрыл всплывающее окно" in res and "открылся" in res,
    f"окно закрыто «назад», не его кнопкой; «Далее» ещё раз — экран подписи: {res}")
screens["now"] = "editor"
backs.clear(); tapped2.clear()
poster._overlay_on_screen = lambda: False
res = poster.run_step(step, {}, ".", 5)
say(not backs and not tapped2 and "окна поверх нет" in res,
    "окна на снимке нет — ничего не жмём вслепую (раньше «Далее» жалось сквозь рекламу)")
ui.dump, ui.tap_node = saved_ui
config.DRY_RUN = saved_dry

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
