"""YouTube на живом телефоне, 2026-10-16: «Загрузить» серая.

* У YouTube был отозван доступ к файлам (отказ «навсегда») — видео по
  file:// он не читал: превью-заглушка, кнопка неактивна. Отказ, похоже,
  нажимал общий «закрыватель окон» («Не разрешать»).
* Новый канал: без ответа «для детей?» кнопка тоже серая.
* Отчёт говорил бы «готово» — нажатие серой кнопки ничего не делает.

Без телефона: экраны — деревья, снятые с телефона.
"""
import json
import io
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import adb      # noqa: E402
import config   # noqa: E402
import device   # noqa: E402
import human    # noqa: E402
import poster   # noqa: E402
import ui       # noqa: E402

ok = True


def say(cond, text):
    global ok
    ok &= bool(cond)
    print(("  OK  " if cond else "  НЕТ ") + text)


human.pause = lambda lo, hi: None
poster.time.sleep = lambda s: None
poster._snap = lambda *a: None
poster._vision_note = lambda *a: ""
YT = "com.google.android.youtube"
PC = "com.google.android.permissioncontroller"


def node(text="", desc="", rid="", pkg=YT, bounds=(0, 0, 10, 10), clickable=True, enabled=True):
    return ui.Node({"text": text, "content-desc": desc, "resource-id": rid, "package": pkg,
                    "clickable": "true" if clickable else "false",
                    "enabled": "true" if enabled else "false", "bounds": "[%d,%d][%d,%d]" % bounds})


print("--- окно доступа к файлам общим «Не разрешать» не закрывается ---")
taps = []
ui.tap_node = lambda n: taps.append(n.text or n.desc)
media = [node("Разрешить приложению YouTube доступ к фото и видео на устройстве?", pkg=PC,
              clickable=False),
         node("Разрешить", pkg=PC), node("Не разрешать", pkg=PC)]
say(ui.dismiss_popup(media) is False and not taps, "доступ к фото и видео — не трогаю ничего")
mic = [node("Разрешить приложению YouTube записывать аудио?", pkg=PC, clickable=False),
       node("Не разрешать", pkg=PC)]
say(ui.dismiss_popup(mic) is True and taps == ["Не разрешать"], "микрофон — по-прежнему отказ")

print("\n--- доступ к файлам по dumpsys ---")
DENIED = ("    runtime permissions:\n"
          "      android.permission.READ_EXTERNAL_STORAGE: granted=false, flags=[ USER_SET|USER_FIXED]\n"
          "      android.permission.WRITE_EXTERNAL_STORAGE: granted=false, flags=[ USER_SET]\n")
GRANTED = DENIED.replace("READ_EXTERNAL_STORAGE: granted=false", "READ_EXTERNAL_STORAGE: granted=true")
T13 = "      android.permission.READ_MEDIA_VIDEO: granted=true, flags=[ USER_SET]\n"
for text, want, name in ((DENIED, False, "отказано"), (GRANTED, True, "разрешено"),
                         (T13, True, "Android 13+: READ_MEDIA_VIDEO"), ("ничего", None, "не узнать")):
    adb.shell = lambda cmd, check=True, timeout=20, t=text: t
    say(device.has_storage_access(YT) is want, f"{name} -> {want}")

print("\n--- без доступа к файлам публикация не начинается ---")
recipes = json.load(io.open(os.path.join(os.path.dirname(HERE), "recipes.json"), encoding="utf-8"))
say(recipes["shorts"].get("needs_storage") is True, "у маршрута YouTube стоит needs_storage")
pushed = []
device.unlock = lambda: True
device.keep_awake = lambda on=True: None
device.close_overlays = lambda *a, **k: None
device.push_video = lambda path: pushed.append(path) or "/sdcard/Movies/x.mp4"
device.refresh_media = lambda remote: None      # телефон в этом тесте не трогаем
device.remove_remote = lambda remote: None
device.has_storage_access = lambda pkg: False
okk, report = poster.post(__file__, "x", "shorts")
say(not okk and "доступа к файлам" in report and not pushed,
    "сразу понятная ошибка, видео даже не заливается")

print("\n--- серая финальная кнопка: честная ошибка, а не «готово» ---")
up_off = node("Загрузить", rid="upload_bottom_button", enabled=False)
ui.wait_any = lambda sels, timeout=20, interval=0.7: (up_off, [up_off])
ui.dump = lambda retries=1, tolerant=True: [up_off]
clock = [0.0]
real = poster.time.time
poster.time.time = lambda: clock.__setitem__(0, clock[0] + 2) or clock[0]
step = [s for s in recipes["shorts"]["steps"] if s["op"] == "publish"][0]
for dry in (True, False):
    config.DRY_RUN = dry
    taps.clear()
    try:
        poster.run_step(step, {}, tempfile.mkdtemp(), 9)
        say(False, f"серая кнопка должна давать ошибку (сухой={dry})")
    except poster.StepFailed as e:
        say(not taps and "неактивна" in str(e), f"сухой прогон={dry}: «{str(e)[:60]}…», ничего не нажато")
poster.time.time = real
config.DRY_RUN = False

print("\n--- аудитория «не для детей» по строкам ---")
W = 1080
adb.screen_size = lambda: (W, 2400)
state = {"screen": "info", "kids": None}


def screen():
    if state["screen"] == "info":
        return [node("", desc="Перейти вверх", bounds=(0, 93, 154, 225)),
                node("", desc="Укажите название", bounds=(44, 1084, 1036, 1292)),
                node("", bounds=(0, 1350, W, 1504)),                 # Доступ
                node("", bounds=(0, 1504, W, 1658)),                 # Аудитория
                node("", desc="Развернуть", bounds=(33, 1669, 1047, 1801)),
                node("Загрузить", rid="upload_bottom_button", bounds=(33, 2127, 1047, 2177),
                     enabled=state["kids"] is not None)]
    return [node("", desc="Назад", bounds=(22, 93, 154, 225)),
            node("", bounds=(0, 696, W, 894)),                       # для детей
            node("", bounds=(0, 894, W, 1048)),                      # не для детей
            node("", bounds=(0, 1095, W, 1249))]                     # возрастные


def tap(n):
    b = n.bounds
    if state["screen"] == "info" and b == (0, 1504, W, 1658):
        state["screen"] = "aud"
    elif state["screen"] == "aud" and b == (0, 696, W, 894):
        state["kids"] = True
    elif state["screen"] == "aud" and b == (0, 894, W, 1048):
        state["kids"] = False
    elif state["screen"] == "aud" and n.desc == "Назад":
        state["screen"] = "info"
    elif n.desc == "Перейти вверх":
        raise AssertionError("вышли из загрузки!")


ui.dump = lambda retries=1, tolerant=True: screen()
ui.tap_node = tap
aud = [s for s in recipes["shorts"]["steps"] if s["op"] == "yt_audience"][0]
say(recipes["shorts"]["steps"].index(aud) < recipes["shorts"]["steps"].index(step),
    "аудитория выбирается до финальной кнопки")
res = poster.run_step(aud, {}, tempfile.mkdtemp(), 8)
say(state["kids"] is False and state["screen"] == "info", f"выбрано «не для детей», вернулись: {res}")
state["kids"] = None
state["screen"] = "info"
real_screen = screen


def screen_upload_alive():
    nodes = real_screen()
    for n in nodes:
        if n.rid.endswith("upload_bottom_button"):
            n.enabled = True                    # новая раскладка: живая и без аудитории
    return nodes


ui.dump = lambda retries=1, tolerant=True: screen_upload_alive()
res = poster.run_step(aud, {}, tempfile.mkdtemp(), 8)
say(state["kids"] is False, f"«Загрузить» живая, а аудитория не выбрана — всё равно выбираю: {res}")

print("\n--- экран не читается, пока играет видео: нажатие на видео (пауза) ---")
adv = [s for s in recipes["shorts"]["steps"] if s["op"] == "advance_until"][0]
say(adv.get("pause_xy") and adv.get("vision_after", 0) > adv.get("pause_after", 0),
    "в маршруте: сначала пауза, по снимку — позже")
yt = {"screen": "trim", "playing": True}
taps_xy = []


def yt_dump(retries=1, tolerant=True):
    if yt["playing"]:
        return []                               # дерево под играющим видео не снимается
    if yt["screen"] == "trim":
        return [node("Далее", bounds=(800, 2100, 1060, 2240))]
    return [node("", desc="Добавьте название", bounds=(302, 284, 1047, 388))]


def yt_tap_node(n):
    if n.text == "Далее":
        yt["screen"], yt["playing"] = "details", False


def yt_tap(x, y):
    taps_xy.append((x, y))
    yt["playing"] = False                       # нажатие на видео — пауза


ui.dump, ui.tap_node, adb.tap = yt_dump, yt_tap_node, yt_tap
poster._vision_point = lambda prompt: (_ for _ in ()).throw(AssertionError("по снимку не нужно"))
clock[0] = 0.0
poster.time.time = lambda: clock.__setitem__(0, clock[0] + 1.0) or clock[0]
res = poster._advance_until(dict(adv, timeout=120), {}, tempfile.mkdtemp(), 2)
poster.time.time = real
say(yt["screen"] == "details" and len(taps_xy) == 1 and "пауза" in res,
    f"одно нажатие на видео — дерево прочиталось, «Далее» по дереву: {res[:90]}")
cx, cy = taps_xy[0]
say(abs(cx - W * adv["pause_xy"][0]) < 2 and abs(cy - 2400 * adv["pause_xy"][1]) < 2,
    f"нажатие в центр видео ({cx},{cy}), не по кнопкам")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
