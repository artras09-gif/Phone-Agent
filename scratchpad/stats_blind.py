"""«Аналитика» TikTok без подписей и Instagram без дерева (2026-10-31, второй
телефон: TikTok 47.5, Android 15, 1280x2772).

В дереве «Аналитики» и её календаря нет ни одной подписи, поэтому экран
узнаётся по устройству, «Custom» и числа календаря — по пикселям снимка,
период и просмотры — моделью зрения. Instagram открывается на ленте с
видео, дерево не снимается — кнопка профиля находится по снимку панели.

Без телефона: узлы — как в живом дереве, снимки экрана нарисованы по тем же
местам, что на живых снимках.
"""
import datetime as dt
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402

tmp = tempfile.mkdtemp(prefix="pa_blind_")
config.BASE = tmp
config.DB_PATH = os.path.join(tmp, "jobs.db")
config.QUEUE_DIR = os.path.join(tmp, "queue")
config.SERIAL = None

import accstats  # noqa: E402
import adb       # noqa: E402
import device    # noqa: E402
import human     # noqa: E402
import ui        # noqa: E402

ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


class N:
    def __init__(self, text="", desc="", b=(0, 0, 0, 0), clickable=False, cls="android.view.ViewGroup"):
        self.text, self.desc, self.bounds, self.clickable, self.cls = text, desc, b, clickable, cls
        self.rid, self.enabled, self.selected, self.pkg = "", True, False, ""


class Today(dt.date):
    @classmethod
    def today(cls):
        return cls(2026, 10, 31)                      # суббота; у TikTok «сегодня» — 30-е


accstats.dt = types.SimpleNamespace(date=Today, timedelta=dt.timedelta,
                                    datetime=dt.datetime, time=dt.time)
human.pause = lambda *a, **k: None
taps, swipes, backs = [], [], []
adb.tap = lambda x, y: taps.append((int(x), int(y)))
adb.shell = lambda cmd, *a, **k: swipes.append(cmd) or ""
adb.current_app = lambda: ("com.zhiliaoapp.musically", "")
device.back = lambda: backs.append(1)
W, H = 1280, 2772
HSV = "android.widget.HorizontalScrollView"

# ------------------------------------------------------ устройство экрана
print("\n--- «Аналитика» без подписей узнаётся по устройству ---")
ANALYTICS = [N(b=(0, 0, 1280, 2772), cls="android.widget.FrameLayout"),
             N(b=(0, 301, 1280, 431), cls=HSV),
             N(b=(0, 301, 1280, 431), cls="android.widget.LinearLayout")]
ANALYTICS += [N(b=(x1, 301, x2, 431)) for x1, x2 in ((52, 357), (357, 638), (638, 888),
                                                         (888, 1133), (1133, 1280))]
ANALYTICS += [N(b=(357, 424, 638, 431)),                  # черта под «Overview»
              N(b=(0, 432, 1280, 433)),
              N(b=(26, 460, 1254, 590), cls=HSV),
              N(b=(26, 616, 1254, 2548))]
say(accstats._tt_blind_row(ANALYTICS, W, H) == (26, 460, 1254, 590), "ряд периодов — второй горизонтальный ряд")
say(accstats._tt_tab_mark(ANALYTICS, W, H) == (357, 638), "выбранная вкладка — по черте под ней")
say(not accstats._tt_analytics_open(ANALYTICS, H), "по подписям «Аналитику» не узнать — их нет")
home = [N(b=(461, 181, 819, 253), text="TikTok Studio"), N(b=(0, 1900, 1280, 2100), cls=HSV),
        N(b=(0, 2300, 1280, 2450), cls=HSV)]
say(accstats._tt_blind_row(home, W, H) is None, "главная TikTok Studio (ряды внизу) за «Аналитику» не принимается")
say(accstats._tt_update([N(desc=" disabled,Update", b=(52, 2476, 1228, 2632))])[1] is False
    and accstats._tt_update([N(desc=" Update", b=(52, 2590, 1228, 2746))])[1] is True,
    "«Update» в дереве: погашена / горит")

# ------------------------------------------------------ рисованный экран
WHITE, PAGE, DIM = (255, 255, 255), (244, 244, 244), (118, 118, 118)


class Screen:
    def __init__(self, w=W, h=H, fill=PAGE):
        self.w, self.h = w, h
        self.px = bytearray(bytes((*fill, 255)) * (w * h))

    def box(self, x1, y1, x2, y2, rgb):
        line = bytes((*rgb, 255)) * (x2 - x1)
        for y in range(y1, y2):
            i = (y * self.w + x1) * 4
            self.px[i:i + len(line)] = line

    def raw(self):
        return self.w, self.h, memoryview(self.px)


COL = W / 7


def calendar(selected=None, label=False):
    """Календарь «Custom», как на живом снимке 31.10: шторка с 748, дни
    недели, «October 2026», октябрь с 1-го (четверг); 30-е — сегодня (серое
    в кольце), 31-е серое; selected — красный круг."""
    s = Screen()
    s.box(0, 0, W, 748, DIM)                             # затемнённая «Аналитика»
    s.box(0, 748, W, H, WHITE)
    s.box(540, 813, 740, 855, (0, 0, 0))                 # «Custom»
    s.box(1160, 815, 1215, 852, (0, 0, 0))               # крестик
    for c in range(7):                                   # Sun … Sat
        s.box(int((c + 0.5) * COL) - 30, 968, int((c + 0.5) * COL) + 30, 998, (150, 150, 150))
    s.box(0, 1048, W, 1050, (225, 225, 225))             # черта под днями недели
    s.box(470, 1099, 810, 1137, (0, 0, 0))               # «October 2026»
    col, y = 4, 1249
    for d in range(1, 32):
        cx = int((col + 0.5) * COL)
        if d == selected:
            s.box(cx - 60, y - 40, cx + 60, y + 78, (254, 44, 85))
        else:
            s.box(cx - 20, y, cx + 20, y + 38, (170, 170, 170) if d >= 30 else (0, 0, 0))
        col += 1
        if col == 7:
            col, y = 0, y + 162
    if label:
        s.box(0, 2420, W, 2422, (225, 225, 225))         # черта над подписью
        s.box(580, 2480, 700, 2520, (0, 0, 0))           # «Oct 29» — по центру столбца
    s.box(52, 2590, 1228, 2746, (254, 44, 85))           # «Update»
    return s


UPDATE = [N(desc=" Update", b=(52, 2590, 1228, 2746))]
ui.dump = lambda *a, **k: UPDATE
SCREEN = {"s": calendar()}
accstats._raw_screen = lambda: SCREEN["s"].raw()

print("\n--- календарь по пикселям ---")
cells, frame = accstats._tt_blind_cells(W, H)
say(len(cells) == 31 and cells[0]["head"] and cells[0]["col"] == 4,
    f"31 число октября, 1-е — четверг, сразу после заголовка: {len(cells)}")
kinds = "".join(c["kind"] for c in cells)
say(kinds == "d" * 29 + "gg", "29 доступных, 30-е и 31-е серые")
dated = accstats._tt_blind_dates(cells)
say(dated and dated[0][0] == dt.date(2026, 10, 1) and dated[-1][0] == dt.date(2026, 10, 31),
    "даты сошлись по дням недели")
say(accstats._tt_blind_today(dated) == dt.date(2026, 10, 30),
    "сегодня у TikTok — 30-е (его часовой пояс на день позади ПК)")
SCREEN["s"] = calendar(selected=29, label=True)
cells, _ = accstats._tt_blind_cells(W, H)
say(len(cells) == 31 and cells[28]["kind"] == "r",
    "выбранное 29-е — красное; подпись «Oct 29» под чертой числом не считается")
broken = [c for i, c in enumerate(cells) if i != 10]
say(accstats._tt_blind_dates(broken) == [], "пропало число — даты не сходятся, ничего не выбираем")
only_red = [dict(c, kind="d" if c["kind"] == "g" else c["kind"]) for c in cells]
for c in only_red:
    c["head"] = False
say(accstats._tt_blind_dates(only_red, known=dt.date(2026, 10, 29))[28][0] == dt.date(2026, 10, 29),
    "без серых и заголовка — опора на выбранный день")
ui.dump = lambda *a, **k: []
say(accstats._tt_blind_cells(W, H) == ([], None), "нет «Update» в дереве — календаря нет, ничего не разбираем")
ui.dump = lambda *a, **k: UPDATE

print("\n--- выбор дня: прочерки — на день раньше ---")
SCREEN["s"] = calendar()
picked, seen = [], iter([{"период": "Oct 29 - Oct 29", "просмотры": ""},
                         {"период": "Oct 28 - Oct 28", "просмотры": "909"}])
real_pick, real_seen, real_cal = accstats._tt_blind_pick, accstats._tt_blind_seen, accstats._tt_blind_calendar
accstats._tt_blind_pick = lambda a, b, w, h: picked.append((a, b)) or True
accstats._tt_blind_seen = lambda waits=2: next(seen)
accstats._tt_blind_calendar = lambda w, h: True
say(accstats._tt_blind_range(1, W, H) and picked == [(dt.date(2026, 10, 29),) * 2, (dt.date(2026, 10, 28),) * 2],
    f"29-е без данных («-») — выбрано 28-е: {picked}")
picked.clear()
seen = iter([{}])
accstats._tt_blind_range(1, W, H)
say(picked == [(dt.date(2026, 10, 29),) * 2], "модели зрения нет — судить не по чему, остаётся 29-е")
accstats._tt_blind_pick, accstats._tt_blind_seen, accstats._tt_blind_calendar = real_pick, real_seen, real_cal

taps.clear()
accstats._wide_band = lambda test, low=0.7: 2668
accstats._tt_blind_pick(dt.date(2026, 10, 29), dt.date(2026, 10, 29), W, H)
say(len(taps) == 3 and taps[0] == taps[1] and taps[0][0] == int(4.5 * COL)
    and abs(taps[0][1] - (1249 + 4 * 162 + 19)) <= 4 and taps[-1] == (W // 2, 2668),
    f"29-е — дважды по месту клетки, потом «Update»: {taps}")

print("\n--- период и просмотры со снимка — моделью ---")
import vision  # noqa: E402
png = os.path.join(tmp, "a.png")
open(png, "wb").write(b"png")
vision.ask = lambda *a, **k: '```json\n{"период": "Oct 28 - Oct 28", "просмотры": "3,846"}\n```'
say(accstats._tt_read(png) == {"период": "Oct 28 - Oct 28", "просмотры": "3 846"}, "прочитано и приведено")
vision.ask = lambda *a, **k: '{"период": "Key metrics", "просмотры": "-"}'
say(accstats._tt_read(png) == {"период": "", "просмотры": ""}, "не дата и прочерк — пусто, не выдумка")

print("\n--- Instagram: нижняя панель по снимку ---")
s = Screen(fill=WHITE)
s.box(0, 600, W, 2550, (40, 30, 20))                 # лента с видео
for k in range(4):
    cx = int((k + 0.5) * W / 5)
    s.box(cx - 30, 2617, cx + 30, 2667, (0, 0, 0))    # значки
s.box(390, 2742, 890, 2748, (60, 60, 60))             # полоска жестов
SCREEN["s"] = s
y = accstats._tab_row(W, H, 5)
say(y is not None and abs(y - 2642) <= 4, f"панель найдена на y={y} (живьём 2642)")
s2 = Screen(fill=WHITE)
s2.box(0, 600, W, 2700, (40, 30, 20))
SCREEN["s"] = s2
say(accstats._tab_row(W, H, 5) is None, "панели нет — None, вслепую не жмём")

print("\n--- TikTok: своё окно «доступ к камере» публикацию не стопорит ---")
overlay = [N("Allow TikTok to access your camera and microphone"), N("Open settings", clickable=True),
           N("CAMERA"), N("CREATE")]
say(ui.handle_permission(overlay, strict=True, app="TikTok", access=False) is False,
    "access=False — не повод останавливаться")
try:
    ui.handle_permission(overlay, strict=True, app="TikTok")
    say(False, "строго — остановка с текстом")
except ui.NeedsPermission as e:
    say("TikTok" in str(e) and "Instagram" not in str(e), f"текст про TikTok, а не про Instagram: {str(e)[:70]}…")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ОШИБКИ")
sys.exit(0 if ok else 1)
