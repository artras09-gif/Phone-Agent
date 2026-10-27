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

print("\n--- YouTube ушёл на страницу канала, где у «+» подпись «Загрузить» (живьём 2026-10-24) ---")
PLUS = N("", b=(400, 2600, 600, 2700))
PLUS.desc = "Загрузить"
CHANNEL = [N("Ease.motion", b=(300, 400, 900, 480)), N("Загрузка · 51%", b=(360, 900, 700, 1000)), PLUS]
yt_step = dict(step, any=[{"rid": "upload_bottom_button"}, {"text": "Загрузить"},
                          {"text": "Upload"}, {"desc": "Загрузить"}, {"desc": "Upload"}])
UPLOAD.text = "Загрузить"
reset(keyboard=False)


def tap_to_channel(n):
    state["taps"].append((n.text or n.desc, state["keyboard"]))
    if n is UPLOAD:
        state["screen"] = CHANNEL


ui.tap_node = tap_to_channel
line = poster.run_step(yt_step, {}, log, 9)
say("экран ушёл" in line and state["taps"] == [("Загрузить", False)],
    f"по id кнопки — ушёл; «+» с той же подписью второй раз НЕ нажат: {state['taps']}")
ui.tap_node = tap_node
UPLOAD.text = "Upload"

print("\n--- после «Загрузить» дерево пустое (страница канала с превью), живьём 2026-10-28 ---")
UPLOAD.text = "Загрузить"
reset(keyboard=False)
dumps = iter([[], [], []])


def tap_to_blank(n):
    state["taps"].append((n.text or n.desc, state["keyboard"]))
    if n is UPLOAD:
        state["screen"] = None


ui.tap_node = tap_to_blank
ui.dump = lambda *a, **k: next(dumps, CHANNEL) if state["screen"] is None else state["screen"]
line = poster.run_step(yt_step, {}, log, 9)
say("ушёл" in line and len(state["taps"]) == 1,
    f"пустые снимки — не «кнопка на месте»: одно касание, {line[-30:]!r}")
reset(keyboard=False)
ui.dump = lambda *a, **k: [] if state["screen"] is None else state["screen"]
line = poster.run_step(yt_step, {}, log, 9)
say("ушёл" in line and len(state["taps"]) == 1,
    "дерево пустое до конца ожидания — кнопки не видно ни разу, значит ушла")
ui.dump = lambda *a, **k: state["screen"]
ui.tap_node = tap_node
UPLOAD.text = "Upload"

print("\n--- TikTok/Instagram: «клавиатура убрана» только что, dumpsys ещё врёт «показана» ---")
backs = []
plain = dict(step)
plain.pop("verify_gone")
reset(keyboard=True)
device.hide_keyboard = lambda: backs.append(1) or True
state["screen"] = DETAILS
line = poster.run_step(plain, {}, log, 10)
say(not backs, "второго «назад» нет — с экрана описания в редактор не выбрасывает (живьём 2026-10-24)")
device.hide_keyboard = hide_keyboard

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

print("\n--- сбой ПОСЛЕ финальной кнопки: приложение не закрывать, файл не удалять ---")
stopped, removed, homes = [], [], []
for name, fn in {"unlock": lambda: True, "keep_awake": lambda on=True: None,
                 "close_overlays": lambda *a, **k: None, "has_storage_access": lambda p: True,
                 "push_video": lambda path: "/sdcard/Movies/v.mp4",
                 "remember_pushed": lambda p: None, "forget_pushed": lambda p: None,
                 "cleanup_pushed": lambda keep_sec=None: 0, "tx_bytes": lambda: 0,
                 "share_video": lambda *a, **k: None, "wait_for_app": lambda pkg, timeout=25: True,
                 "stop_app": lambda pkg: stopped.append(pkg), "go_home": lambda: homes.append(1),
                 "remove_remote": lambda path: removed.append(path)}.items():
    setattr(device, name, fn)
video = os.path.join(tempfile.mkdtemp(), "v.mp4")
open(video, "wb").write(b"x")
recipe = {"yt": {"package": "com.google.android.youtube", "title": "yt", "steps": [step]}}
real_run_step = poster.run_step


def failing_after_tap(st, ctx, lp, i):
    poster.FINAL["tapped"] = True
    raise poster.StepFailed("экран не ушёл")


poster.run_step = failing_after_tap
good, rep = poster.post(video, "x", "yt", recipes=recipe)
say(not good and not stopped and homes and not removed and "не закрываю" in rep,
    "после нажатой «Загрузить» — на рабочий стол, YouTube не убит, файл на месте (загрузка могла пойти)")


def failing_before_tap(st, ctx, lp, i):
    raise poster.StepFailed("кнопки нет")


stopped.clear(); removed.clear()
poster.run_step = failing_before_tap
poster.post(video, "x", "yt", recipes=recipe)
say(stopped == ["com.google.android.youtube"] and removed,
    "сбой ДО финальной кнопки — как раньше: приложение закрыто, файл удалён")
poster.run_step = real_run_step

print("\n--- маршрут YouTube в recipes.json проверяет, что экран ушёл ---")
import json  # noqa: E402
recipes = json.load(open(os.path.join(os.path.dirname(HERE), "recipes.json"), encoding="utf-8"))
last = recipes["shorts"]["steps"][-1]
say(last["op"] == "publish" and last.get("verify_gone") and recipes["shorts"]["rev"] >= 7,
    f"verify_gone у «Загрузить», rev {recipes['shorts']['rev']} — на других ПК маршрут обновится сам")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
