"""Финальная кнопка под клавиатурой (живьём 2026-10-23, телефон пользователя:
YouTube «Add details», курсор в названии, клавиатура поверх «Upload» —
касание ушло в клавиатуру, а отчёт сказал «нажал, жду загрузку»).

Без телефона: экран, клавиатура и касания подменены. Касание по кнопке
срабатывает, только если клавиатура спрятана.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402

config.LOG_DIR = tempfile.mkdtemp(prefix="pa_pubkb_")

import device  # noqa: E402
import human   # noqa: E402
import poster  # noqa: E402
import ui      # noqa: E402

ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


class N:
    def __init__(self, text="", rid="", b=(0, 0, 0, 0)):
        self.text, self.desc, self.rid, self.bounds = text, "", rid, b
        self.clickable = self.enabled = True
        self.selected = False
        self.cls = "android.widget.Button"

    @property
    def area(self):
        return (self.bounds[2] - self.bounds[0]) * (self.bounds[3] - self.bounds[1])


UPLOAD = N("Upload", "com.google.android.youtube:id/upload_bottom_button", (33, 2400, 1247, 2520))
DETAILS = [N("Add details", b=(200, 120, 800, 200)), UPLOAD]
HOME = [N("Home", b=(0, 2600, 256, 2700)), N("Uploading 1 of 1", b=(0, 300, 1280, 400))]

state = {}


def reset(keyboard=True, hide_works=True, tap_works=True):
    state.update(screen=DETAILS, keyboard=keyboard, hide_works=hide_works,
                 tap_works=tap_works, taps=[], hides=0)


def hide_keyboard():
    if not state["keyboard"]:
        return False
    state["hides"] += 1
    if state["hide_works"]:
        state["keyboard"] = False
    else:
        state["hide_works"] = True           # со второго раза
    return True


def tap_node(n):
    state["taps"].append((n.text, state["keyboard"]))
    if state["keyboard"]:
        return                               # касание ушло в клавиатуру
    if state["tap_works"] and n is UPLOAD:
        state["screen"] = HOME


device.keyboard_shown = lambda: state["keyboard"]
device.hide_keyboard = hide_keyboard
ui.dump = lambda *a, **k: state["screen"]
ui.tap_node = tap_node
human.pause = lambda *a, **k: None
poster.time.sleep = lambda s: None
poster._snap = lambda *a, **k: None
config.DRY_RUN = False

step = {"op": "publish", "timeout": 3, "desc": "ФИНАЛЬНАЯ КНОПКА", "verify_gone": True,
        "gone_timeout": 0.5,
        "any": [{"rid": "upload_bottom_button"}, {"text": "Upload"}]}
log = tempfile.mkdtemp()

print("--- клавиатура поверх «Upload» — сперва убрать, потом нажать ---")
reset(keyboard=True)
line = poster.run_step(step, {"package": "com.google.android.youtube"}, log, 9)
say(state["hides"] == 1 and state["taps"] == [("Upload", False)] and state["screen"] is HOME,
    f"клавиатура убрана ДО касания, касание прошло: {line}")

print("\n--- клавиатура не ушла с первого раза — касание пропало, повтор ---")
reset(keyboard=True, hide_works=False)
line = poster.run_step(step, {}, log, 9)
say(state["taps"] == [("Upload", True), ("Upload", False)] and "со второго раза" in line,
    f"первое касание в клавиатуру, второе после неё: {line}")

print("\n--- кнопка не нажимается совсем — ошибка, а не «жду загрузку» ---")
reset(keyboard=False, tap_works=False)
try:
    poster.run_step(step, {}, log, 9)
    failed = ""
except poster.StepFailed as e:
    failed = str(e)
say("НЕ загружено" in failed and len(state["taps"]) == 2, f"честная остановка: {failed[:70]}")

print("\n--- без verify_gone (TikTok, Instagram) — как раньше ---")
reset(keyboard=False, tap_works=False)
plain = dict(step)
plain.pop("verify_gone")
line = poster.run_step(plain, {}, log, 9)
say(line.startswith("тап «ФИНАЛЬНАЯ КНОПКА»") and len(state["taps"]) == 1,
    "одно касание, без ожидания (там свои проверки: publish_more, ожидание загрузки)")

print("\n--- сухой прогон: клавиатуру убрал, но не нажал ---")
config.DRY_RUN = True
reset(keyboard=True)
line = poster.run_step(step, {}, log, 9)
say("СУХОЙ ПРОГОН" in line and not state["taps"] and state["hides"] == 1,
    "кнопку видно (клавиатура убрана), нажатия нет")
config.DRY_RUN = False

print("\n--- маршрут YouTube в recipes.json проверяет, что экран ушёл ---")
import json  # noqa: E402
recipes = json.load(open(os.path.join(os.path.dirname(HERE), "recipes.json"), encoding="utf-8"))
last = recipes["shorts"]["steps"][-1]
say(last["op"] == "publish" and last.get("verify_gone") and recipes["shorts"]["rev"] >= 7,
    f"verify_gone у «Загрузить», rev {recipes['shorts']['rev']} — на других ПК маршрут обновится сам")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
