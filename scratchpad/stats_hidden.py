"""Невидимые узлы и обрезанные кнопки на экранах статистики (2026-10-30).

TikTok после обновления отдаёт в дереве «Аналитики» и невидимые копии
подписей с рамкой [0,0][0,0], а «Другое» («Custom» на английском телефоне)
стоит последней в ряду периодов и обрезана правым краем. Программа брала
невидимую копию, жала в угол экрана, календарь не открывался, «назад»
уводил на главную TikTok Studio — и снималась она, без цифры.

Без телефона: узлы — как в живом дереве Redmi 30.10 (1080x2400).
"""
import datetime as dt
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402

tmp = tempfile.mkdtemp(prefix="pa_hidden_")
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
        self.rid, self.enabled, self.selected = "", True, False

    def __repr__(self):
        return f"N({self.text or self.desc}{self.bounds})"


class Today(dt.date):
    @classmethod
    def today(cls):
        return cls(2026, 10, 30)                      # пятница


accstats.dt = types.SimpleNamespace(date=Today, timedelta=dt.timedelta,
                                    datetime=dt.datetime, time=dt.time)
human.pause = lambda *a, **k: None
accstats.time.sleep = lambda s: None
taps, backs, swipes = [], [], []
ui.tap_node = lambda n: taps.append(n) or accstats._center(n)
adb.tap = lambda x, y: taps.append((x, y))
adb.shell = lambda cmd, *a, **k: swipes.append(cmd) or ""
adb.exec_out = lambda *a, **k: b""
adb.current_app = lambda: ("com.zhiliaoapp.musically", "")
device.back = lambda: backs.append(1)
W, H = 1080, 2400


def analytics(words, swiped=False):
    """Верх «Аналитики»: заголовок, вкладки, ряд периодов, подпись дат,
    «Просмотры публикаций». words — подписи периодов, последняя — «Другое».
    Плюс невидимые копии подписей — как в живом дереве."""
    xs = [(6, 226), (248, 467), (489, 732), (754, 1014)] if swiped else \
        [(44, 235), (257, 477), (499, 718), (740, 983), (1005, 1080)]
    shown = words[1:] if swiped else words
    nodes = [N(desc=w, clickable=True) for w in words]           # невидимые копии
    nodes += [N("Аналитика,heading", b=(240, 134, 840, 195)),
              N("Обзор", b=(369, 265, 553, 317))]
    for w, (x1, x2) in zip(shown, xs):
        nodes += [N(w, b=(x1, 382, x2, 470), clickable=True, cls="com.lynx.tasm.behavior.ui.view.UIView"),
                  N(w, b=(x1 + 33, 401, min(x2, 1080), 451))]
    nodes += [N("22 окт - 28 окт", b=(66, 622, 582, 669)),
              N("Просмотры публика‎…", b=(100, 744, 487, 802)), N("1,224", b=(100, 802, 495, 889)),
              N("31", b=(0, 0, 0, 0)), N("30", b=(0, 0, 0, 0))]
    return nodes


RU = ["7 дней", "28 дней", "60 дней", "365 дней", "Другое"]
EN = ["7 days", "28 days", "60 days", "365 days", "Custom"]

print("\n--- TikTok: невидимые копии и обрезанная «Другое» ---")
nodes = analytics(RU)
say(accstats._tt_other(nodes, W, H).bounds == (1005, 382, 1080, 470),
    "из двух «Другое» берётся видимая, а не копия с рамкой [0,0][0,0]")
say(accstats._tt_analytics_open([N("Другое"), N("7 дней")], H) is False,
    "одни невидимые подписи — это ещё не «Аналитика»")
say(accstats._tt_range_text([N("1 окт - 1 окт"), N("22 окт - 28 окт", b=(66, 622, 582, 669))], H)
    == "22 окт - 28 окт", "подпись дат — видимая")

for words, lang in ((RU, "русский"), (EN, "английский")):
    screens = iter([analytics(words), analytics(words, swiped=True)])
    ui.dump = lambda *a, **k: next(screens)
    swipes.clear()
    other, _ = accstats._tt_find_other(W, H)
    say(other is not None and other.bounds == (754, 382, 1014, 470) and len(swipes) == 1
        and "swipe 864 426 270 426" in swipes[0],
        f"{lang}: обрезанную краем «{words[-1]}» сначала выдвигает, листая ряд влево")

# Календарь не открылся — «назад» не жмём: он увёл бы с «Аналитики».
after = analytics(EN, swiped=True)
screens = iter([analytics(EN), after])
ui.dump = lambda *a, **k: next(screens, after)
taps.clear(), backs.clear(), swipes.clear()
accstats.TT_CAL["why"] = ""
got = accstats._tt_period(1, W, H)
say(got is None and taps and taps[0].bounds == (754, 382, 1014, 470),
    "нажата видимая «Custom», не угол экрана")
say(not backs, "календарь не открылся — «назад» не нажат, остаёмся на «Аналитике»")
say("клеток" in accstats.TT_CAL["why"] and "чисел 0" in accstats.TT_CAL["why"],
    f"причина без невидимых чисел: {accstats.TT_CAL['why']}")

# Календарь открыт (ряда периодов в дереве нет) — «назад» его закрывает.
ui.dump = lambda *a, **k: [N(b=(22, 900, 176, 1010)) for _ in range(5)]
backs.clear()
accstats._tt_close(H)
say(backs == [1], "открытый календарь закрывается «назад»")

# Ряд из нескольких готовых периодов — не выпадающий список: «7 days» не жмём.
row = [n for n in analytics(EN) if accstats._label(n) not in accstats.TT_OTHER]
ui.dump = lambda *a, **k: row
taps.clear(), backs.clear()
say(accstats._tt_period(1, W, H) is None and not backs
    and not any(getattr(t, "bounds", None) == (44, 382, 235, 470) for t in taps),
    "нет «Custom» в ряду — готовый период не нажат, «назад» не нажат")
say("нет «Custom»" in accstats.TT_CAL["why"], f"причина: {accstats.TT_CAL['why']}")
drop = [N("Last 7 days", b=(700, 382, 1036, 470), clickable=True), N("Overview", b=(369, 265, 553, 317))]
menu = [N("Last 7 days", b=(100, 1500, 980, 1600), clickable=True),
        N("Custom", b=(100, 1900, 980, 2000), clickable=True)]
screens = iter([drop, drop, menu])
ui.dump = lambda *a, **k: next(screens, [])
taps.clear()
accstats._tt_period(1, W, H)
say(len(taps) >= 2 and taps[1].bounds == (100, 1900, 980, 2000),
    "один «Last 7 days ▾» — выпадающий список, «Custom» берётся из него")

print("\n--- строка «Просмотры»: снимок есть, числа нет ---")
folder = tempfile.mkdtemp(prefix="pa_miss_")
accstats._note(folder, "tiktok", "главная TikTok Studio, 7 дней (раздел «Аналитика» не открылся)")
accstats._note(folder, "tiktok_miss", "главная TikTok Studio, 7 дней (раздел «Аналитика» не открылся)")
accstats._note(folder, "instagram_views", "113")
accstats._note(folder, "instagram_miss", "лишнее")
text = accstats.numbers_text(folder)
print("   " + text.replace("\n", "\n   "))
say("TikTok — числа нет, снимок в альбоме: главная TikTok Studio" in text,
    "TikTok не пропадает молча — сказано, почему нет числа")
say("Instagram — 113" in text and "лишнее" not in text, "есть число — старая пометка не мешает")

print("\n--- Instagram: сегодняшний день ниже края календаря ---")


def ig_calendar(months):
    """Календарь Instagram: months — [(подпись, первый столбец, дней, до какого
    числа нажимается)] сверху вниз, по 121 px на неделю."""
    nodes, y = [N("Период времени", b=(87, 300, 474, 359))], 400
    xs = [67, 202, 338, 474, 610, 746, 882]
    for head, col, ndays, live_to in months:
        nodes.append(N(head, b=(87, y, 405, y + 59)))
        y += 76
        for d in range(1, ndays + 1):
            x = xs[col]
            if d <= live_to:
                nodes.append(N(b=(x, y, x + 132, y + 121), clickable=True))
            nodes.append(N(str(d), b=(x + 45, y + 43, x + 85, y + 89)))
            col += 1
            if col == 7:
                col, y = 0, y + 121
        y += 121 if col else 0
    return nodes + [N("Apply", b=(789, 1973, 993, 2105), clickable=True)]


SEP = ("September 2026", 1, 30, 30)
OCT = ("October 2026", 3, 31, 28)
top = ig_calendar([("August 2026", 5, 31, 31), SEP])            # октябрь ещё ниже
seq, now = accstats._ig_today(top, W)
say(now is None, "30 сентября под заголовком «September» не принято за сегодня (30 октября)")
screens = iter([top, ig_calendar([SEP, OCT])])
ui.dump = lambda *a, **k: next(screens)
taps.clear(), swipes.clear()
accstats.IG_PICKED.clear()
got = accstats._ig_pick_range(1, W, H)
say(got == (dt.date(2026, 10, 28), dt.date(2026, 10, 28)) and len(swipes) == 1,
    f"листнул календарь вниз и выбрал 28 окт. — последний доступный: {got}")
ui.dump = lambda *a, **k: top
accstats.IG_PICKED.clear()
say(accstats._ig_pick_range(1, W, H) is None and "30 — только в прошлом месяце" in accstats.IG_PICKED["cal"],
    f"октября так и не видно — причина: {accstats.IG_PICKED.get('cal')}")
ui.dump = lambda *a, **k: ig_calendar([("September 2026", 1, 30, 0), ("October 2026", 3, 31, 0)])
accstats.IG_PICKED.clear()
say(accstats._ig_pick_range(1, W, H) is None and "нет ни одного нажимаемого" in accstats.IG_PICKED["cal"],
    f"нажимаемых дней нет — причина: {accstats.IG_PICKED.get('cal')}")
ui.dump = lambda *a, **k: [N("Apply", b=(789, 1973, 993, 2105), clickable=True)]
accstats.IG_PICKED.clear()
say(accstats._ig_pick_range(1, W, H) is None and accstats.IG_PICKED["cal"] == "чисел в календаре не видно",
    "чисел нет — так и сказано")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ОШИБКИ")
sys.exit(0 if ok else 1)
