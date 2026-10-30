"""Публикация с главной и повтор с неё (2026-10-30: «хочу, чтобы постинг он
начинал с главной домашней страницы каждой соцсети, и если возникают какие-то
проблемы с постингом, он бы возвращался на главную и пробовал ещё раз запостить
с неё»).

Без телефона. Экраны — в точности такие, какими их отдал Redmi 30.10:
главные TikTok / YouTube / Instagram, галереи всех трёх.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import adb     # noqa: E402
import config  # noqa: E402
import device  # noqa: E402
import human   # noqa: E402
import poster  # noqa: E402
import ui      # noqa: E402

config.LOG_DIR = tempfile.mkdtemp(prefix="pa_post_home_")
human.pause = lambda *a, **k: None
poster.time.sleep = lambda s: None
poster._snap = lambda *a: None
poster._vision_note = lambda *a: ""
ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


def node(text="", desc="", rid="", bounds=(0, 0, 10, 10), clickable=False, selected=False,
         pkg="app"):
    return ui.Node({"text": text, "content-desc": desc, "resource-id": rid, "package": pkg,
                    "clickable": "true" if clickable else "false", "enabled": "true",
                    "selected": "true" if selected else "false",
                    "bounds": "[%d,%d][%d,%d]" % bounds})


tapped = []
ui.tap_node = lambda n: tapped.append(n.desc or n.text or n.rid)
adb.tap = lambda x, y: tapped.append((x, y))
adb.screen_size = lambda: (1080, 2400)
adb.current_app = lambda: ("app", "")
log = tempfile.mkdtemp(prefix="pa_post_home_log_")

print("--- длина ролика по подписи ---")
say([poster._seconds_of(t) for t in ("0:23", "00:39", "1:02:03", "23", "Далее", "")]
    == [23, 39, 3723, None, None, None], "«0:23», «00:39», «1:02:03» — да; прочее — нет")

print("\n--- TikTok: галерея (живое дерево 30.10) ---")
TT = [node("Видео", rid="qti", bounds=(474, 261, 606, 313), selected=True),
      node(rid="eve", bounds=(237, 355, 358, 476), clickable=True),
      node("00:23", rid="dp3", bounds=(227, 657, 358, 712)),
      node(rid="eve", bounds=(595, 355, 716, 476), clickable=True),
      node("00:39", rid="dp3", bounds=(586, 657, 716, 712)),
      node(rid="eve", bounds=(237, 718, 358, 839), clickable=True),
      node("00:44", rid="dp3", bounds=(228, 1020, 358, 1075)),
      node("Далее", rid="ojk", bounds=(551, 2105, 1047, 2177))]
step = {"op": "pick_video", "items": [{"rid": "eve"}], "duration": [{"rid": "dp3"}]}
lab = poster._label_for(TT[1], [n for n in TT if n.rid == "dp3"])
say(lab is not None and lab.text == "00:23", f"длина первой клетки — её «00:23», не соседней: {lab and lab.text}")
ui.dump = lambda **k: TT
tapped.clear()
line = poster._pick_video(step, {"video_name": "x.mp4", "video_seconds": 22.6}, log, 6)
say(tapped == ["eve"] and "00:23" in line, f"наш ролик 22,6 с — выбран первый: {line}")
try:
    poster._pick_video(dict(step, timeout=0.01), {"video_name": "x.mp4", "video_seconds": 44.0},
                       log, 6)
    say(False, "чужой ролик первым — должна быть ошибка")
except poster.StepFailed as e:
    say("Чужой не выбираю" in str(e) and "00:23" in str(e), f"первым чужой ролик — ошибка: {e}")
NO_ID = [node(rid="zz1", bounds=n.bounds, clickable=True) if n.rid == "eve" else
         node(n.text, rid="", bounds=n.bounds) for n in TT]
ui.dump = lambda **k: NO_ID
tapped.clear()
line = poster._pick_video({"op": "pick_video", "items": [{"rid": "zz1"}]},
                          {"video_seconds": 22.6}, log, 6)
say(tapped == ["zz1"], f"у подписи длины сменился id — нашлась по виду «00:23»: {line}")

print("\n--- YouTube: галерея с именами файлов ---")
YT = [node(desc="2026-10-27-235430464.mp4", rid="thumb_image_view", bounds=(3, 408, 357, 1037)),
      node("0:39", rid="duration_text_view", bounds=(241, 958, 340, 1020)),
      node(desc="SFX рилс — ASCII город v6.mp4", rid="thumb_image_view", bounds=(363, 408, 717, 1037)),
      node("0:22", rid="duration_text_view", bounds=(601, 958, 700, 1020))]
ui.dump = lambda **k: YT
ystep = {"op": "pick_video", "by_name": True, "items": [{"rid": "thumb_image_view"}],
         "duration": [{"rid": "duration_text_view"}]}
tapped.clear()
line = poster._pick_video(ystep, {"video_name": "SFX рилс — ASCII город v6.mp4",
                                  "video_seconds": 22.6}, log, 5)
say(tapped == ["SFX рилс — ASCII город v6.mp4"], f"по имени, даже не первым: {line}")
try:
    poster._pick_video(dict(ystep, timeout=0.01), {"video_name": "другой.mp4",
                                                   "video_seconds": 22.6}, log, 5)
    say(False, "нашего имени нет — должна быть ошибка")
except poster.StepFailed as e:
    say("2026-10-27-235430464.mp4" in str(e), f"нашего имени нет — ошибка с тем, что первым: {e}")

print("\n--- Instagram: галерея Reels ---")
IG = [node(desc="Открыть камеру", bounds=(5, 541, 358, 1168), clickable=True),
      node(desc="Не выбрано Миниатюра видео создано 30 октября 2026 г. 10:32",
           rid="gallery_grid_item_thumbnail", bounds=(364, 541, 716, 1166), clickable=True),
      node("0:23", rid="gallery_grid_item_label", bounds=(617, 1097, 694, 1149)),
      node(desc="Не выбрано Миниатюра видео создано 27 октября 2026 г. 11:55",
           rid="gallery_grid_item_thumbnail", bounds=(722, 541, 1075, 1168), clickable=True),
      node("0:39", rid="gallery_grid_item_label", bounds=(976, 1097, 1053, 1149))]
ui.dump = lambda **k: IG
tapped.clear()
line = poster._pick_video({"op": "pick_video", "items": [{"rid": "gallery_grid_item_thumbnail"}],
                           "duration": [{"rid": "gallery_grid_item_label"}]},
                          {"video_seconds": 22.6}, log, 4)
say(len(tapped) == 1 and "30 октября" in tapped[0], f"плитка камеры пропущена, наш ролик: {line}")

print("\n--- главная: закрыть, открыть, дождаться ---")
calls = []
device.stop_app = lambda p: calls.append(("stop", p))
device.open_app = lambda p: calls.append(("open", p))
device.wait_for_app = lambda p, timeout=25: True
TT_HOME = [node(desc="Главная", rid="ipy", bounds=(0, 2135, 216, 2177), clickable=True, selected=True),
           node(desc="Создать", rid="ipv", bounds=(432, 2135, 648, 2177), clickable=True)]
screens = iter([[], TT_HOME])                    # лента играет: дерева нет, потом пауза
ui.dump = lambda **k: next(screens, TT_HOME)
tapped.clear()
hstep = {"op": "home", "desc": "главная TikTok", "pause_xy": [0.5, 0.42],
         "home": [{"desc": "Главная", "selected": True, "clickable": True}],
         "tab": [{"desc": "Главная", "clickable": True}]}
ctx = {"package": "app"}
line = poster._to_home(hstep, ctx, log, 1)
say(calls == [("stop", "app"), ("open", "app")] and tapped == [(540, 1008)] and ctx.get("at_home"),
    f"закрыл и открыл, касанием поставил ленту на паузу, главная: {line}")
OTHER_TAB = [node(desc="Главная", bounds=(0, 2135, 216, 2177), clickable=True),
             node(desc="Профиль", bounds=(864, 2135, 1080, 2177), clickable=True, selected=True)]
screens = iter([OTHER_TAB, TT_HOME])
tapped.clear()
line = poster._to_home(dict(hstep, pause_xy=None), {"package": "app"}, log, 1)
say(tapped == ["Главная"], f"открылось на «Профиле» — нажата «Главная»: {line}")
ui.dump = lambda **k: OTHER_TAB[1:]
try:
    poster._to_home(dict(hstep, timeout=0.01), {"package": "app"}, log, 1)
    say(False, "главной нет — должна быть ошибка")
except poster.StepFailed as e:
    say("не дождался главной" in str(e), f"главной нет — ошибка: {str(e)[:60]}")

print("\n--- сбой — заново с главной; после финальной кнопки — никогда ---")
for name in ("unlock", "keep_awake", "close_overlays", "cleanup_pushed", "remove_remote",
             "forget_pushed", "remember_pushed"):
    setattr(device, name, lambda *a, **k: True)
device.push_video = lambda path: "/sdcard/Movies/x.mp4"
device.tx_bytes = lambda: 0
refreshed = []
device.refresh_media = lambda r: refreshed.append(r)
poster.wait_upload = lambda *a, **k: True
config.DRY_RUN = False
RECIPE = {"demo": {"package": "app", "title": "demo",
                   "steps": [{"op": "home"}, {"op": "pick_video"}, {"op": "publish"}]}}
real_run = poster.run_step


def scripted(fails):
    """run_step, который падает на заданных (попытка, шаг)."""
    state = {"attempt": 0, "log": []}

    def run(step, ctx, log_path, i):
        if step["op"] == "home":
            state["attempt"] += 1
        state["log"].append((state["attempt"], step["op"]))
        if (state["attempt"], step["op"]) in fails:
            exc = fails[(state["attempt"], step["op"])]
            if step["op"] == "publish":
                poster.FINAL["tapped"] = True
            raise exc
        if step["op"] == "publish":
            poster.FINAL["tapped"] = True
        return step["op"]
    return run, state


calls.clear()
poster.run_step, st = scripted({(1, "pick_video"): poster.StepFailed("не нашёл ролик")})
good, rep = poster.post(__file__, "x", "demo", recipes=RECIPE)
say(good and st["attempt"] == 2 and "попытка 2 из 3" in rep and ("stop", "app") in calls
    and refreshed == ["/sdcard/Movies/x.mp4"],
    "сбой на галерее — закрыл, обновил медиатеку, со второго захода с главной вышло")

refreshed.clear()
poster.run_step, st = scripted({(a, "pick_video"): poster.StepFailed("нет") for a in (1, 2, 3)})
good, rep = poster.post(__file__, "x", "demo", recipes=RECIPE)
say(not good and st["attempt"] == 3 and "попытка 3 из 3" in rep and "ОШИБКА: нет" in rep,
    "три сбоя подряд — три захода с главной и честная ошибка")

poster.run_step, st = scripted({(1, "publish"): poster.StepFailed("кнопка не ушла")})
good, rep = poster.post(__file__, "x", "demo", recipes=RECIPE)
say(not good and st["attempt"] == 1 and "проверь" in rep.lower(),
    "финальная кнопка нажата и сбой — НЕ повторяю (ролик мог уйти)")

poster.run_step, st = scripted({(1, "pick_video"): ui.NeedsPermission("нет доступа к файлам")})
good, rep = poster.post(__file__, "x", "demo", recipes=RECIPE)
say(not good and st["attempt"] == 1, "нужен человек (доступ) — не повторяю")

config.POST_ATTEMPTS = 1
poster.run_step, st = scripted({(1, "pick_video"): poster.StepFailed("нет")})
good, rep = poster.post(__file__, "x", "demo", recipes=RECIPE)
say(not good and st["attempt"] == 1, "POST_ATTEMPTS = 1 — без повторов")
config.POST_ATTEMPTS = 3
poster.run_step = real_run

print("\n--- маршруты в recipes.json ---")
recipes = poster.load_recipes()
for net in ("tiktok", "shorts", "reels"):
    steps = recipes[net]["steps"]
    ops = [s["op"] for s in steps]
    bad = [s for s in steps for sel in s.get("any", []) + s.get("home", []) + s.get("tab", [])
           + s.get("items", []) + s.get("duration", []) + s.get("xy_if", [])
           if set(sel) - {"text", "contains", "rid", "desc", "cls", "clickable", "selected"}]
    say(ops[0] == "home" and "pick_video" in ops and ops.index("pick_video") < ops.index("publish")
        and not bad, f"{net}: с главной, наш ролик сверяется до публикации ({len(ops)} шагов)")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
