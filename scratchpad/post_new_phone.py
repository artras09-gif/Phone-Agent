"""Публикация на новом телефоне (английский интерфейс), сбои 2026-10-14.

* YouTube открывал не экран названия, а обрезку («Drag to adjust video»,
  кнопка длины «15s», «Done»), потом редактор («Next»). Маршрут ждал название
  и вставал. Плюс «Done» на «15s» обрезал бы ролик до 15 секунд.
* Instagram при открытии камеры спросил микрофон («Allow Instagram to record
  audio?»). Кнопка «Don’t allow» — с типографским апострофом, и окно не
  закрывалось; публикация вставала на «галерее».

Без телефона: экраны — деревья, собранные по снимкам пользователя.
"""
import io
import json
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import bundled  # noqa: E402
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
taps = []


def node(text="", desc="", rid="", pkg="com.google.android.youtube", bounds=(0, 0, 10, 10),
         clickable=True):
    b = "[%d,%d][%d,%d]" % bounds
    return ui.Node({"text": text, "content-desc": desc, "resource-id": rid, "package": pkg,
                    "clickable": "true" if clickable else "false", "enabled": "true",
                    "bounds": b})


# --------------------------------------------------------- длина mp4
def fake_mp4(seconds, scale=1000, version=0):
    if version == 0:
        body = bytes([0, 0, 0, 0]) + b"\0" * 8 + struct.pack(">II", scale, int(seconds * scale))
    else:
        body = bytes([1, 0, 0, 0]) + b"\0" * 16 + struct.pack(">IQ", scale, int(seconds * scale))
    atom = struct.pack(">I", 8 + len(body)) + b"mvhd" + body
    path = os.path.join(tempfile.mkdtemp(), "v.mp4")
    open(path, "wb").write(b"\0\0\0\x18ftypisom" + b"\0" * 12 + b"\0\0\0\x08moov" + atom)
    return path


print("--- длина видео по заголовку ---")
say(abs(poster.mp4_seconds(fake_mp4(42.5)) - 42.5) < 0.01, "mvhd v0: 42.5 с")
say(abs(poster.mp4_seconds(fake_mp4(95, version=1)) - 95) < 0.01, "mvhd v1: 95 с")
say(poster._length_of("15s") == 15 and poster._length_of("3m") == 180
    and poster._length_of("60 с") == 60 and poster._length_of("6.1s") is None,
    "кнопка длины: 15s, 3m, 60 с; «6.1s» с полосы — не кнопка")


# --------------------------------------------------------- YouTube: экраны подряд
class Phone:
    """YouTube нового образца: обрезка -> редактор -> название."""

    def __init__(self, video_len):
        self.screen, self.length = "trim", 15
        self.options = [15, 60, 180]
        self.video_len = video_len
        self.done_length = None

    def dump(self, retries=1, tolerant=True):
        H = 2712
        if self.screen == "trim":
            return [node("Drag to adjust video", bounds=(40, 2500, 600, 2560), clickable=False),
                    node(f"{self.length}s", rid="duration_chip", bounds=(1100, 120, 1200, 200)),
                    node("6.1s", bounds=(600, 2300, 700, 2350), clickable=False),
                    node("Done", rid="done_button", bounds=(1000, 2480, 1220, 2580)),
                    node("", bounds=(0, 0, 1260, H), clickable=False)]
        if self.screen == "editor":
            return [node("Next", rid="shorts_edit_next", bounds=(1000, 2500, 1220, 2600)),
                    node("", bounds=(0, 0, 1260, H), clickable=False)]
        return [node("", rid="title_edit_text", desc="Caption your Short",
                     bounds=(40, 400, 1220, 500)),
                node("Upload Short", rid="upload_bottom_button", bounds=(40, 2500, 1220, 2600)),
                node("", bounds=(0, 0, 1260, H), clickable=False)]

    def tap(self, n):
        taps.append(n.text or n.desc or n.rid)
        if n.rid == "duration_chip":
            i = self.options.index(self.length)
            self.length = self.options[(i + 1) % len(self.options)]
        elif n.text == "Done":
            self.done_length, self.screen = self.length, "editor"
        elif n.text == "Next":
            self.screen = "details"
        elif "upload" in (n.text + n.rid).lower():
            raise AssertionError("тест нажал публикацию!")


recipes = json.load(io.open(os.path.join(os.path.dirname(HERE), "recipes.json"), encoding="utf-8"))
step = recipes["shorts"]["steps"][1]
say(step["op"] == "advance_until" and recipes["shorts"]["rev"] >= 2, "в маршруте YouTube — проход экранов")

for video_len, want_len in ((14, 15), (42, 60), (100, 180)):
    phone = Phone(video_len)
    ui.dump, ui.tap_node = phone.dump, phone.tap
    taps.clear()
    res = poster._advance_until(dict(step, timeout=30), {"video": fake_mp4(video_len)}, "", 2)
    say(phone.screen == "details" and phone.done_length == want_len,
        f"видео {video_len} с: длина выставлена {phone.done_length} с, дошли до названия")
say("Upload Short" not in taps, "кнопку публикации проход экранов не нажимал")

phone = Phone(400)
ui.dump, ui.tap_node = phone.dump, phone.tap
res = poster._advance_until(dict(step, timeout=30), {"video": fake_mp4(400)}, "", 2)
say(phone.done_length == 180 and "обрежется" in res,
    f"видео длиннее максимума — максимум (3 мин) и предупреждение в отчёте: {phone.done_length}")

print("\n--- старый YouTube: название сразу ---")
phone = Phone(10)
phone.screen = "details"
ui.dump, ui.tap_node = phone.dump, phone.tap
taps.clear()
poster._advance_until(dict(step, timeout=30), {"video": fake_mp4(10)}, "", 2)
say(not taps, "экран названия сразу — ничего не нажато")

print("\n--- Instagram: окно разрешения микрофона ---")
PC = "com.google.android.permissioncontroller"
dialog = [node("Allow Instagram to record audio?", pkg=PC, clickable=False, bounds=(100, 1400, 1100, 1500)),
          node("While using the app", pkg=PC, bounds=(100, 1600, 1100, 1700)),
          node("Only this time", pkg=PC, bounds=(100, 1750, 1100, 1850)),
          node("Don’t allow", pkg=PC, bounds=(100, 1900, 1100, 2000))]
taps.clear()
ui.tap_node = lambda n: taps.append(n.text)
say(ui.handle_permission(dialog) and taps == ["Don't allow"],
    f"«Don’t allow» с типографским апострофом нажат: {taps}")
taps.clear()
say(ui.dismiss_popup(dialog) and taps == ["Don't allow"], "и через общий «закрыть окно» тоже")
say("While using the app" not in taps and "Only this time" not in taps, "разрешение не выдано")

media = [node("Allow Instagram to access photos and videos on this device?", pkg=PC,
              clickable=False),
         node("Allow", pkg=PC), node("Don’t allow", pkg=PC)]
taps.clear()
try:
    ui.handle_permission(media, strict=True)
    say(False, "доступ к фото и видео — должна быть понятная остановка")
except ui.NeedsPermission as e:
    say(not taps and "фото и видео" in str(e), f"фото и видео: не жму ничего, прошу человека — {e}")
say(ui.handle_permission(media) is False and not taps, "в ленте (не строго) — просто не трогаю")

reels = recipes["reels"]["steps"]
say(reels[0]["op"] == "reel_gallery" and recipes["reels"]["rev"] >= 4,
    "маршрут Instagram начинается с пути к галерее Reels (без камеры, если можно)")
text = json.dumps(reels, ensure_ascii=False)
say(all(w in text for w in ("New reel", "ideo", "Next", "Write a caption", "Share")),
    "английские подписи Instagram в маршруте")

print("\n--- Instagram: свой экран «доступ к камере и микрофону» ---")
IG = "com.instagram.android"
own = [node("Allow access to camera and microphone", pkg=IG, clickable=False),
       node("This lets you share photos and record videos.", pkg=IG, clickable=False),
       node("Allow access", pkg=IG)]
taps.clear()
try:
    ui.handle_permission(own, strict=True)
    say(False, "свой экран доступа — должна быть понятная остановка")
except ui.NeedsPermission as e:
    say(not taps and "While using the app" in str(e), f"кнопку доступа не жму, говорю что нажать: {str(e)[:90]}…")
say(ui.handle_permission(own) is False and not taps, "в ленте (не строго) — не трогаю")
camera = [node("REEL", pkg=IG), node("STORY", pkg=IG), node("Add audio", pkg=IG)]
say(ui.access_screen(camera) is None, "обычная камера Instagram за экран доступа не принимается")
print("\n--- Instagram: к галерее Reels ---")
import device   # noqa: E402

W, H = 1280, 2772
poster.adb.screen_size = lambda: (W, H)
device.stop_app = lambda pkg: None
device.open_uri = lambda uri, pkg=None: ig.go("reels_feed")
device.open_app = lambda pkg: ig.go("home")


class Insta:
    """Instagram нового образца: «+» на главной -> «New post» -> REEL -> галерея."""

    def __init__(self, create=True, reel_opens_camera=False):
        self.screen, self.create, self.cam = "off", create, reel_opens_camera
        self.log = []

    def go(self, screen):
        self.screen = screen
        self.log.append(screen)

    def dump(self, retries=1, tolerant=True):
        s = self.screen
        if s == "home":
            out = [node("", desc="Home", pkg=IG, bounds=(0, 2650, 256, 2772)),
                   node("", desc="Your story", pkg=IG, bounds=(20, 300, 200, 480)),
                   node("", desc="Create story", pkg=IG, bounds=(200, 300, 380, 480))]
            # «+» в дереве главной нет (живьём 2026-10-15) — только по месту.
            return out
        if s == "new_post":
            return [node("New post", rid="gallery_title_text", pkg=IG, clickable=False),
                    node("POST", pkg=IG, bounds=(300, 2600, 450, 2700)),
                    node("REEL", pkg=IG, bounds=(546, 2567, 733, 2678))]
        if s == "camera":
            return [node("STORY", pkg=IG, bounds=(350, 2567, 530, 2678)),
                    node("REEL", pkg=IG, bounds=(546, 2567, 733, 2678)),
                    node("", desc="Close", pkg=IG, bounds=(20, 120, 140, 230))]
        if s == "reel_gallery":
            return [node("New reel", rid="gallery_title_text", pkg=IG, clickable=False),
                    node("", rid="gallery_grid_item_thumbnail", desc="Video thumbnail", pkg=IG)]
        return []                                   # играющая лента — дерева нет

    def tap_node(self, n):
        label = n.text or n.desc
        self.log.append("tap " + label)
        if label == "Create":
            self.go("new_post")
        elif label == "REEL" and self.screen == "new_post":
            self.go("camera" if self.cam else "reel_gallery")
        elif label in ("Your story", "Create story"):
            raise AssertionError("тест ушёл в историю!")

    def tap_xy(self, x, y):
        self.log.append(f"xy {x},{y}")
        if self.screen == "home" and y < 300 and self.create:
            self.go("new_post")
        elif self.screen == "reels_feed" and y < 300:
            self.go("camera")
        elif self.screen == "camera" and x < 200 and 2567 <= y <= 2678:
            self.go("reel_gallery")                    # миниатюра в ряду REEL


step_ig = reels[0]
poster._snap = lambda *a: None
poster._vision_note = lambda *a: ""
fast = [0.0]
real_clock = poster.time.time
poster.time.time = lambda: fast.__setitem__(0, fast[0] + 0.5) or fast[0]
for name, kw, want_camera in (("«+» на главной (по месту)", {}, False),
                              ("«+», но REEL открыл камеру", {"reel_opens_camera": True}, True),
                              ("на главной нет «+» — камера", {"create": False}, True)):
    ig = Insta(**kw)
    ui.dump, ui.tap_node, poster.adb.tap = ig.dump, ig.tap_node, ig.tap_xy
    try:
        res = poster._reel_gallery(step_ig, {}, tempfile.mkdtemp(), 1)
        good = ig.screen == "reel_gallery" and (("camera" in ig.log) == want_camera)
    except poster.StepFailed as e:
        res, good = str(e), False
    say(good, f"{name}: {res}")
poster.time.time = real_clock
say("tap Create story" not in ig.log and "tap Your story" not in ig.log,
    "кнопки историй не нажимались («Create» — только точным совпадением)")

print("\n--- YouTube: экран обрезки без дерева — кнопка по снимку ---")


class Blind(Phone):
    """Под играющим видео дерево не снимается: на обрезке и в редакторе пусто."""

    def dump(self, retries=1, tolerant=True):
        return [] if self.screen in ("trim", "editor") else Phone.dump(self)


yt = Blind(42)
ui.dump = yt.dump
seen_prompts = []


def fake_sight(prompt):
    seen_prompts.append(prompt)
    if "длительности" in prompt:
        return (1150, 160, f"{yt.length}s") if yt.screen == "trim" else None
    if yt.screen == "trim":
        return (1110, 2530, "Done")
    if yt.screen == "editor":
        return (1110, 2550, "Next")
    return None


def blind_tap(x, y):
    if yt.screen == "trim" and y < 300:
        yt.tap(node(f"{yt.length}s", rid="duration_chip"))
    elif yt.screen == "trim":
        yt.tap(node("Done"))
    elif yt.screen == "editor":
        yt.tap(node("Next"))


poster._vision_point = fake_sight
poster.adb.tap = blind_tap
real_time = poster.time.time
clock = [0.0]
poster.time.time = lambda: clock.__setitem__(0, clock[0] + 1.0) or clock[0]
try:
    res = poster._advance_until(dict(step, timeout=200, pause_xy=None), {"video": fake_mp4(42)}, tempfile.mkdtemp(), 2)
    good = yt.screen == "details" and yt.done_length == 60 and "по снимку" in res
except poster.StepFailed as e:
    res, good = str(e), False
poster.time.time = real_time
say(good, f"дерева нет — «Done» и «Next» найдены по снимку, длина 60 с: {res[:120]}")
say(any("длительности" in p for p in seen_prompts), "длину по снимку проверяли")

yt = Blind(42)
ui.dump = yt.dump
poster._vision_point = lambda prompt: (1110, 2530, "Upload Short")
clock[0] = 0.0
poster.time.time = lambda: clock.__setitem__(0, clock[0] + 1.0) or clock[0]
try:
    poster._advance_until(dict(step, timeout=60, pause_xy=None), {"video": fake_mp4(42)}, tempfile.mkdtemp(), 2)
    say(False, "модель назвала «Upload» — должна быть остановка")
except poster.StepFailed as e:
    say(yt.screen == "trim" and "не жму" in str(e), "модель назвала «Upload Short» — не жму, честная остановка")
poster.time.time = real_time

print("\n--- Instagram: второй экран «Поделиться» и защита от двойного поста ---")
import config   # noqa: E402

more = [s for s in recipes["reels"]["steps"] if s["op"] == "publish_more"][0]
say(recipes["reels"]["steps"][-1] is more, "проверка второго экрана — последним шагом маршрута")
pressed = []
ui.tap_node = lambda n: pressed.append(n.text or n.rid)
caption_screen = [node("", rid="caption_input_text_view", pkg=IG),
                  node("Далее", rid="share_button", pkg=IG)]
second_screen = [node("Поделиться", rid="share_button", pkg=IG)]
assert config.DRY_RUN is False
fast2 = [0.0]
real_clock2 = poster.time.time
poster.time.time = lambda: fast2.__setitem__(0, fast2[0] + 1.0) or fast2[0]
# Живьём 2026-10-16: окно «О Reels» лежит ПОВЕРХ экрана подписи — в дереве
# и поле подписи, и share_button «Далее», и «Поделиться» окна.
sheet_over = caption_screen + [node("Поделиться", rid="igds_button", pkg=IG),
                               node("Отмена", pkg=IG)]
state_ig = {"screen": sheet_over}
ui.dump = lambda retries=1, tolerant=True: state_ig["screen"]


def tap_sheet(n):
    pressed.append(n.text or n.rid)
    if n.text == "Поделиться" and not n.rid.endswith("share_button"):
        state_ig["screen"] = [node("", desc="Дом", pkg=IG)]          # ушли в ленту — пост пошёл


ui.tap_node = tap_sheet
res = poster.run_step(more, {}, tempfile.mkdtemp(), 12)
say(pressed == ["Поделиться"] and "опубликовано" in res,
    f"окно «О Reels» поверх подписи — «Поделиться» нажат, экран ушёл: {res}")

pressed.clear()
state_ig["screen"] = [node("", desc="Дом", pkg=IG)]
res = poster.run_step(more, {}, tempfile.mkdtemp(), 12)
say(not pressed and "первой кнопкой" in res, f"окна нет и экран ушёл — опубликовано первой: {res}")

pressed.clear()
old_ig = [node("", rid="caption_input_text_view", pkg=IG),
          node("Поделиться", rid="share_button", pkg=IG)]          # старый Instagram: та же кнопка
state_ig["screen"] = old_ig
try:
    poster.run_step(more, {}, tempfile.mkdtemp(), 12)
    say(False, "экран подписи остался — должна быть ошибка, а не «готово»")
except poster.StepFailed as e:
    say(not pressed and "не ушёл" in str(e),
        f"та же share_button «Поделиться» повторно НЕ жмётся, честная ошибка: {str(e)[:70]}…")
poster.time.time = real_clock2
config.DRY_RUN = True
state_ig["screen"] = sheet_over
res = poster.run_step(more, {}, tempfile.mkdtemp(), 12)
say(not pressed and "СУХОЙ ПРОГОН" in res, "в сухом прогоне второй экран не трогается")
config.DRY_RUN = False

print("\n--- сбой публикации: снимок и экран — в бот ---")
import scheduler      # noqa: E402
import telegram_bot   # noqa: E402

fail_dir = tempfile.mkdtemp()
open(os.path.join(fail_dir, "06_STUCK.png"), "wb").write(b"\x89PNG old")
open(os.path.join(fail_dir, "99_FAIL.png"), "wb").write(b"\x89PNG last")
open(os.path.join(fail_dir, "06_screen.txt"), "w", encoding="utf-8").write("'Allow access' id=x clickable")
poster.LAST_FAIL.update(dir=fail_dir, target="reels")
got = {}
telegram_bot.load_settings = lambda: {"token": "t", "owner": "42"}
telegram_bot.alert = lambda text, image=None: got.update(alert=text, image=image) or True
telegram_bot.send = lambda text, **kw: got.update(text=text, chat=kw.get("chat_id")) or True
scheduler._tell_failure("reels", "=== Instagram\nОШИБКА: не нашёл элемент для «галерея»")
say(got.get("image") == b"\x89PNG last", "в бот ушёл последний снимок экрана")
say("Instagram Reels" in got.get("alert", "") and "галерея" in got["alert"], f"подпись: {got.get('alert')}")
say("Allow access" in got.get("text", "") and got.get("chat") == "42", "и список кнопок экрана — владельцу")
got.clear()
scheduler._tell_failure("tiktok", "=== TikTok : 2.mp4\nне смог разблокировать телефон")
say("разблокировать" in got.get("alert", ""),
    f"нет строки «ОШИБКА» — в бот уходит настоящая причина, а не «не опубликовал»: {got.get('alert')}")

print("\n--- новый маршрут доходит до старой установки ---")
tmp = tempfile.mkdtemp()
mine = {"shorts": {"package": "x", "steps": [{"op": "old"}], "мой_ключ": 1},
        "reels": {"package": "y", "rev": 5, "steps": [{"op": "моё"}]}}
packed = {"shorts": {"package": "x", "rev": 2, "steps": [{"op": "new"}]},
          "reels": {"package": "y", "rev": 2, "steps": [{"op": "встроенное"}]}}
pm, pp = os.path.join(tmp, "mine.json"), os.path.join(tmp, "packed.json")
json.dump(mine, open(pm, "w", encoding="utf-8"), ensure_ascii=False)
json.dump(packed, open(pp, "w", encoding="utf-8"), ensure_ascii=False)
bundled.merge_new_keys(pp, pm)
got = json.load(open(pm, encoding="utf-8"))
say(got["shorts"]["steps"] == [{"op": "new"}] and got["shorts"]["мой_ключ"] == 1,
    "шаги YouTube заменены (rev новее), свои ключи человека целы")
say(got["reels"]["steps"] == [{"op": "моё"}], "маршрут человека с rev новее встроенного не тронут")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
