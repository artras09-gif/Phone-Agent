"""Статистика на «свежем» телефоне (живьём 2026-11-12, первый телефон: новые
аккаунты, Android 15, 1280x2772) — без телефона.

На нём сбор не работал ни в одной сети, и каждая ломалась по-своему:
  * TikTok — у «Аналитики» нет ряда периодов (плашка «Data is processing»),
    программа считала её «не открылась» и снимала главную Studio за 7 дней;
  * YouTube Studio — первый запуск: окно «Accounts», потом «Get started»;
    в альбом уходил снимок окна с почтой;
  * Instagram — касание по «Custom» терялось, список оставался открытым, и
    числа календаря искались в нём («чисел в календаре не видно»).
Узлы — как в живых деревьях.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402

tmp = tempfile.mkdtemp(prefix="pa_first_")
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
W, H = 1280, 2772


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


class N:
    def __init__(self, text="", desc="", b=(0, 0, 0, 0), clickable=False,
                 cls="android.view.ViewGroup", rid=""):
        self.text, self.desc, self.bounds, self.clickable, self.cls = text, desc, b, clickable, cls
        self.rid, self.enabled, self.selected, self.pkg = rid, True, False, ""


human.pause = lambda *a, **k: None
accstats.time.sleep = lambda s: None
backs, taps = [], []
device.back = lambda: backs.append(1)
adb.shell = lambda cmd, *a, **k: ""

# ------------------------------------------------------------------ TikTok
print("TikTok: «Аналитика» без ряда периодов")
SCROLL = "android.widget.HorizontalScrollView"
TABS = [N(b=(0, 301, 1280, 431), cls=SCROLL),
        N(b=(52, 301, 357, 431)), N(b=(357, 301, 638, 431)), N(b=(357, 424, 638, 431)),
        N(b=(638, 301, 888, 431)), N(b=(888, 301, 1133, 431)), N(b=(1133, 301, 1280, 431))]
BARE = TABS + [N(b=(26, 627, 1254, 2559)), N(b=(78, 1902, 1202, 2494), cls="android.view.View")]
FULL = TABS + [N(b=(0, 474, 1280, 578), cls=SCROLL)]
# Главная TikTok Studio второго телефона: тоже один ряд вкладок — Posts · LIVE.
HOME = [N("TikTok Studio", b=(461, 181, 819, 253)), N(b=(0, 301, 1280, 431), cls=SCROLL),
        N(b=(52, 301, 652, 431)), N("Posts", b=(290, 331, 418, 401)), N(b=(52, 424, 652, 431)),
        N(b=(652, 301, 1228, 431)), N("LIVE", b=(890, 331, 990, 401)),
        N(b=(52, 301, 640, 431)), N(b=(660, 301, 1228, 431))]
say(accstats._tt_bare(BARE, W, H), "вкладки без ряда периодов — это «Аналитика», а не «не открылась»")
say(not accstats._tt_bare(FULL, W, H) and accstats._tt_blind_row(FULL, W, H) == (0, 474, 1280, 578),
    "с рядом периодов — обычная «Аналитика» без подписей, как раньше")
say(len(accstats._tt_rows(HOME, W, H)) == 1 and not accstats._tt_bare(HOME, W, H),
    "главная Studio с вкладками Posts · LIVE за «Аналитику» не принята (заголовок «TikTok Studio»)")
say(not accstats._tt_bare([], W, H), "пустое дерево — не «Аналитика»")

folder = os.path.join(tmp, "tt")
accstats._snap = lambda f, name, package=None: os.path.join(f, name + ".png")
accstats._tt_read = lambda path: {"период": "Nov 5 - Nov 11", "просмотры": ""}
backs.clear()
path = accstats._tt_shot_bare(folder, 1)
said = accstats._notes_in(folder)
say(path and "ещё не посчитал" in said.get("tiktok", "") and "05.11–11.11" in said.get("tiktok", ""),
    f"прочерки названы прямо: {said.get('tiktok')!r}")
say(not said.get("tiktok_views"), "числа нет — и не выдумано")
say(len(backs) == 3, "назад трижды — к профилю")
folder = os.path.join(tmp, "tt2")
accstats._tt_read = lambda path: {"период": "Nov 5 - Nov 11", "просмотры": "12"}
accstats._tt_shot_bare(folder, 1)
said = accstats._notes_in(folder)
say(said.get("tiktok_views") == "12" and "а не вчера" in said.get("tiktok_when", ""),
    f"число есть, а периода не выбрать — не выдаётся за вчера: {said.get('tiktok_when')!r}")

# ----------------------------------------------------------------- YouTube
print("\nYouTube Studio: первый запуск")
ID = "com.google.android.apps.youtube.creator:id/"


def picker(*names):
    out = [N("Accounts", b=(250, 1251, 932, 1330), rid=ID + "title"),
           N(desc="Add account", b=(932, 1213, 1088, 1369), clickable=True, rid=ID + "add_account"),
           N("someone@example.com", b=(172, 1369, 1108, 1471), rid=ID + "title")]
    for i, name in enumerate(names):
        top = 1471 + i * 167
        out += [N(b=(172, top, 1108, top + 167), clickable=True, cls="android.widget.RelativeLayout"),
                N(name, b=(414, top + 26, 644, top + 88), rid=ID + "name"),
                N("No subscribers", b=(414, top + 88, 1030, top + 141), rid=ID + "byline")]
    return out


START = [N(desc="Previous", b=(0, 2538, 188, 2655)),
         N("Get started", b=(188, 2538, 1092, 2655), clickable=True),
         N(desc="Next", b=(1092, 2538, 1280, 2655), clickable=True)]
STUDIO = [N("Dashboard", b=(47, 2655, 208, 2699), rid=ID + "text"),
          N("Analytics", b=(572, 2655, 707, 2699), rid=ID + "text")]
screen = {"now": None}


def tap(n):
    taps.append(n)
    if screen["now"] is START:
        screen["now"] = STUDIO
    elif any((x.rid or "").endswith("/add_account") for x in screen["now"]):
        screen["now"] = START


ui.dump = lambda **k: screen["now"]
ui.tap_node = tap

screen["now"] = one = picker("Tras el velo")
taps.clear()
say(accstats._yt_enter("Tras el velo") == "" and screen["now"] is STUDIO,
    "окно «Accounts» → канал → «Get started» → Studio")
say(len(taps) == 2 and taps[0].bounds == (172, 1471, 1108, 1638) and taps[1].text == "Get started",
    "нажаты строка канала и «Get started», а не «Add account»")
screen["now"] = picker("Tras el velo")
taps.clear()
say(accstats._yt_enter("") == "" and len(taps) == 2, "имя канала неизвестно, но он один — его и берём")
screen["now"] = two = picker("Чужой канал", "Tras el velo")
taps.clear()
say(accstats._yt_enter("tras el velo") == "" and taps[0].bounds == (172, 1638, 1108, 1805),
    "каналов два — выбран тот, что открыт в YouTube")
screen["now"] = picker("Первый", "Второй")
taps.clear()
backs.clear()
trouble = accstats._yt_enter("Tras el velo")
say(bool(trouble) and not taps and backs, f"нашего канала в списке нет — не гадаем: {trouble[:60]!r}")
screen["now"] = STUDIO
taps.clear()
say(accstats._yt_enter("Tras el velo") == "" and not taps, "Studio уже вошёл — ничего не нажато")

# --------------------------------------------------------------- Instagram
print("\nInstagram: касание по списку периодов потерялось")
LIST = [N("Time period", b=(851, 668, 1163, 714))] + \
       [N(label, b=(864, 820 + i * 182, 1050, 883 + i * 182))
        for i, label in enumerate(("7 days", "14 days", "30 days", "60 days", "90 days", "Custom"))]
CAL = [N(str(d), b=(100 + (d % 7) * 160, 546 + (d // 7) * 143, 150 + (d % 7) * 160, 598 + (d // 7) * 143))
       for d in range(1, 31)] + [N("Apply", b=(1036, 2369, 1192, 2525), clickable=True)]
PAGE = [N("All content", b=(52, 540, 320, 600)), N("30 days", b=(996, 491, 1163, 647)),
        N("Views", b=(104, 691, 216, 737)), N("1", b=(104, 760, 130, 830))]
say(len(accstats._ig_period_list(LIST)) == 6 and not accstats._ig_period_list(PAGE),
    "открытый список отличается от страницы с выбором периода")
say(accstats._ig_calendar_open(CAL, W) and not accstats._ig_calendar_open(LIST, W)
    and not accstats._ig_calendar_open(PAGE, W), "календарь узнаётся; список и страница — не календарь")


def tap_custom(n):
    taps.append(n.text)
    if n.text == "Custom":
        screen["now"] = CAL


# Первое касание по «Custom» (его делает `_ig_period`) пропало: на экране
# по-прежнему список.
ui.tap_node = tap_custom
screen["now"] = LIST
taps.clear()
say(accstats._ig_open_calendar(W) and taps == ["Custom"],
    "список остался открытым — «Custom» нажат ещё раз, календарь открылся")
screen["now"] = CAL
taps.clear()
say(accstats._ig_open_calendar(W) and not taps, "календарь открылся с первого раза — лишних касаний нет")
screen["now"] = PAGE
taps.clear()
say(not accstats._ig_open_calendar(W) and not taps, "ни списка, ни календаря — не жмём вслепую")

print("\nИТОГ: всё зелёное" if ok else "\nЕСТЬ ОШИБКИ")
sys.exit(0 if ok else 1)
