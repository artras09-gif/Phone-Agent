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
ops = [s["op"] for s in reels]
say(ops.index("permissions") < [i for i, s in enumerate(reels) if "галереи" in s.get("desc", "")][0],
    "в маршруте Instagram окна разрешений закрываются ДО тапа по галерее")
text = json.dumps(reels, ensure_ascii=False)
say(all(w in text for w in ("New reel", "ideo", "Next", "Write a caption", "Share")),
    "английские подписи Instagram в маршруте")

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
