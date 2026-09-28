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
device.share_video = lambda *a, **k: None
device.wait_for_app = lambda pkg, timeout=25: True
device.stop_app = lambda pkg: закрыто.append(pkg)
device.remove_remote = lambda path: None
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
    say(закрыто == ["com.example.app"],
        "настоящая публикация сорвалась посреди маршрута — тоже закрыто")

    закрыто.clear()
    poster.run_step = lambda step, ctx, log_path, i: "шаг"
    poster.post("x.mp4", "", "demo", recipes=РЕЦЕПТ)
    say(not закрыто, "настоящая публикация прошла — приложение не трогаем")
finally:
    for n, f in saved_dev.items():
        setattr(device, n, f)
    poster.run_step, config.DRY_RUN = saved_step, saved_dry

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
