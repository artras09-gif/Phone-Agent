"""Отчёты статистики сами: за вчера каждый день, за прошлую неделю по
понедельникам, за прошлый месяц 1-го (2026-10-28: «мне каждый день нужно,
чтобы он отправлял стату за полный прошлый день»; 2026-10-29: «каждую неделю
за прошедшую неделю и каждый месяц за прошедший месяц»).

Без телефона: «сегодня» подменяется, съёмка сетей — заглушкой, которая
кладёт снимки и подписи так же, как настоящая. Календарь TikTok собран по
живой разметке Redmi 28–29.10 (внешние клетки 154x110, шаг строк 137,
заголовок месяца — промежуток 253), узлы YouTube Studio — из живого дерева.
"""
import datetime as dt
import os
import sys
import tempfile
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402

tmp = tempfile.mkdtemp(prefix="pa_reports_")
config.BASE = tmp
config.DB_PATH = os.path.join(tmp, "jobs.db")
config.QUEUE_DIR = os.path.join(tmp, "queue")
config.SERIAL = None

import accstats  # noqa: E402
import adb       # noqa: E402
import device    # noqa: E402
import human     # noqa: E402
import jobs      # noqa: E402
import telegram_bot as bot  # noqa: E402
import ui        # noqa: E402

ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


class N:
    def __init__(self, text="", desc="", b=(0, 0, 0, 0), clickable=False):
        self.text, self.desc, self.bounds, self.clickable = text, desc, b, clickable
        self.rid, self.enabled, self.selected, self.cls = "", True, False, "android.view.ViewGroup"


NOW = [dt.datetime(2026, 10, 29, 12, 0)]


class Today(dt.date):
    @classmethod
    def today(cls):
        return cls(NOW[0].year, NOW[0].month, NOW[0].day)


class Clock(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW[0]


accstats.dt = types.SimpleNamespace(date=Today, timedelta=dt.timedelta, datetime=Clock, time=dt.time)
human.pause = lambda *a, **k: None
accstats.time.sleep = lambda s: None


def log_event(kind, payload=""):
    with jobs.connect() as con:
        con.execute("INSERT INTO events (kind,payload,at,device) VALUES (?,?,?,?)",
                    (kind, payload, time.mktime(NOW[0].timetuple()), jobs.DEVICE))


jobs.log_event = log_event
W, H = 1080, 2400
D = dt.date

print("--- даты из подписей периода ---")
for text, want in [("27 окт", D(2026, 10, 27)), ("Oct 27", D(2026, 10, 27)), ("27 oct.", D(2026, 10, 27)),
                   ("5 мая", D(2026, 5, 5)), ("31 dic", D(2025, 12, 31)), ("2,357", None), ("", None)]:
    say(accstats._day_of(text) == want, f"{text!r} -> {accstats._day_of(text)}")
say(accstats._range_of("19 окт - 25 окт") == (D(2026, 10, 19), D(2026, 10, 25))
    and accstats._range_of("26 окт") == (D(2026, 10, 26),) * 2 and accstats._range_of("Основные") is None,
    "«19 окт - 25 окт» и «26 окт» — диапазоны")

print("\n--- периоды ---")
for now, kind, want in [(D(2026, 10, 29), "day", (D(2026, 10, 28),) * 2),
                        (D(2026, 10, 29), "week", (D(2026, 10, 19), D(2026, 10, 25))),
                        (D(2026, 11, 2), "week", (D(2026, 10, 26), D(2026, 11, 1))),
                        (D(2026, 11, 1), "month", (D(2026, 10, 1), D(2026, 10, 31))),
                        (D(2027, 1, 1), "month", (D(2026, 12, 1), D(2026, 12, 31)))]:
    say(accstats.report_span(kind, now) == want, f"{now} {kind}: {accstats.report_span(kind, now)}")
say(accstats.report_title("month", D(2026, 10, 1), D(2026, 10, 31)) == "📊 Статистика за октябрь 2026"
    and accstats.report_title("week", D(2026, 10, 26), D(2026, 11, 1), "tiktok")
    == "📊 TikTok за прошлую неделю, 26.10–01.11", "заголовки отчётов")

print("\n--- подписи «за вчера» и «за неделю» ---")
folder = accstats.report_dir("day", D(2026, 10, 28))
say(accstats._day_note(folder, "tiktok", D(2026, 10, 28), "28 окт", "отстаёт") == "за вчера, 28.10"
    and accstats._notes_in(folder).get("tiktok_end") == "2026-10-28", "вчера — «за вчера, 28.10», конец отмечен")
say(accstats._day_note(folder, "tiktok", D(2026, 10, 27), "27 окт", "TikTok отстаёт") == "за 27.10 — TikTok отстаёт",
    "позавчера — честно")
week = (D(2026, 10, 19), D(2026, 10, 25))
say(accstats._range_note(folder, "tiktok", week, week, "-") == "за 19.10–25.10", "неделя целиком")
say(accstats._range_note(folder, "tiktok", week, (D(2026, 10, 19), D(2026, 10, 24)), "воскресенья нет")
    == "за 19.10–24.10 — воскресенья нет" and accstats._notes_in(folder)["tiktok_end"] == "2026-10-24",
    "неделя без последнего дня — честно, конец отмечен 24-м")

print("\n--- когда что снимать ---")
bot.load_settings = lambda: {"token": "1:" + "A" * 35, "owner": "111"}
saved = {}
bot._saved = lambda: dict(saved)
bot._remember = lambda **f: saved.update(f)


def due(y, m, d, hh=12, mm=0):
    return accstats.reports_due(dt.datetime(y, m, d, hh, mm))


say(bot.daily_stats_time() == "12:00", "по умолчанию — в 12:00")
say(due(2026, 10, 29, 11, 59) == [], "11:59 — рано")
say(due(2026, 10, 29) == [("day", "main")], "четверг 12:00 — только за вчера")
say(due(2026, 10, 26) == [("day", "main"), ("week", "main")], "понедельник — вчера и прошлая неделя")
say(due(2026, 11, 2) == [("day", "main"), ("week", "main"), ("month", "main")],
    "понедельник 2-го — ещё и месяц (догоняет: 1-го программа была закрыта)")
say(due(2026, 11, 1) == [("day", "main"), ("month", "main")], "1-е число — вчера и прошлый месяц")
say(due(2026, 11, 4) == [("day", "main"), ("week", "main")], "в среду неделю ещё догоняет (программа была закрыта)")
say(due(2026, 11, 5) == [("day", "main")], "в четверг — уже нет: не присылать прошлое посреди недели")
say(accstats.due(dt.datetime(2026, 10, 29, 12, 0)), "сторож окна (due) видит, что пора")
saved["daily_stats"] = ""
say(due(2026, 10, 26, 15) == [], "выключено командой — ничего")
saved["daily_stats"] = "09:30"
say(due(2026, 10, 26, 9, 30) == [("day", "main"), ("week", "main")], "своё время 09:30")
saved.pop("daily_stats")
bot.load_settings = lambda: {"token": "", "owner": ""}
say(due(2026, 10, 26) == [], "бот не настроен — слать некому")
bot.load_settings = lambda: {"token": "1:" + "A" * 35, "owner": "111"}

print("\n--- попытки: за вчера, Instagram ещё не открыл вчера ---")
ENDS = {}            # сеть -> очередь последних дней, которые она «покажет»
calls = []


def fake_collect(nets=None, log=None, days=None, folder=None, fresh=True, full=True):
    calls.append((days, list(nets), fresh, full))
    os.makedirs(folder, exist_ok=True)
    for old in os.listdir(folder) if fresh else []:      # как настоящая: только свежее
        os.remove(os.path.join(folder, old))
    kind = "day" if days == 1 else days
    first, last = accstats.report_span(kind)
    for net in nets:
        name = accstats.SHOT_FILES[net]
        open(os.path.join(folder, name + ".png"), "wb").write(b"png")
        if net == "shorts":
            accstats._note(folder, name, "YouTube за период")
            continue
        queue = ENDS.get(net) or [last]
        end = queue.pop(0) if len(queue) > 1 else queue[0]
        if kind == "day":
            note = accstats._day_note(folder, name, end, f"{end:%d.%m}", f"{net} отстаёт")
        else:
            note = accstats._range_note(folder, name, (first, last), (first, end), f"{net} отстаёт")
        accstats._note(folder, name, note)
    return {}


accstats._collect_all = fake_collect
albums = []
bot.send_album = lambda pics, chat_id=None, thread=None: albums.append((pics, chat_id, thread)) or True
bot.destinations = lambda role: []
config.FEED_APPS = ["tiktok", "reels", "shorts"]


def run_due():
    log = []
    accstats.collect_all(log=log)
    return log


NOW[0] = dt.datetime(2026, 10, 29, 12, 0)
ENDS.update(reels=[D(2026, 10, 27), D(2026, 10, 28)], tiktok=[D(2026, 10, 27)])
log = run_due()
say(calls[-1] == (1, ["tiktok", "reels", "shorts"], True, False), f"12:00 — три сети за вчера, без плиток: {calls[-1]}")
say(not albums and log[-1].endswith("Instagram — пересниму в 14:00"), f"альбом ждёт Instagram: {log[-1]}")
NOW[0] = dt.datetime(2026, 10, 29, 14, 0)
run_due()
say(calls[-1] == (1, ["reels"], False, False), f"14:00 — переснят только Instagram: {calls[-1]}")
caps = [c for _, c in albums[-1][0]] if albums else []
say(len(albums) == 1 and albums[-1][1] == "111" and caps[0].startswith("📊 Статистика за вчера, 28.10\n\nYouTube"),
    f"альбом ушёл владельцу, заголовок на первом снимке: {caps[:1]}")
say(any(c.startswith("TikTok") and "за 27.10 — tiktok отстаёт" in c for c in caps),
    "TikTok за вчера — последний посчитанный им день, и это не задержало альбом")
say(due(2026, 10, 29, 20) == [], "ушёл — до завтра ничего")

print("\n--- понедельник: неделя без TikTok, TikTok назавтра ---")
albums.clear()
NOW[0] = dt.datetime(2026, 10, 26, 12, 0)
ENDS.clear()
run_due()                                            # за вчера и неделя — одним заходом
say(calls[-2][0] == 1 and calls[-1] == ("week", ["reels", "shorts"], True, False),
    f"сначала за вчера, потом неделя — Instagram и YouTube: {calls[-2:]}")
head = albums[-1][0][0][1] if albums else ""
say(head.startswith("📊 Статистика за прошлую неделю, 19.10–25.10\nTikTok пришлю завтра"),
    f"в заголовке — когда будет TikTok: {head.splitlines()[:2]}")
say(not any(c.startswith("TikTok") for _, c in albums[-1][0]), "TikTok в альбоме недели нет")
say(due(2026, 10, 26, 18) == [], "в понедельник больше ничего")
NOW[0] = dt.datetime(2026, 10, 27, 12, 0)
say(due(2026, 10, 27) == [("day", "main"), ("week", "tiktok")], "во вторник — вчера и TikTok за неделю")
run_due()
say(calls[-1] == ("week", ["tiktok"], False, False), f"TikTok за неделю — один, старые снимки на месте: {calls[-1]}")
pics = albums[-1][0]
say(len(pics) == 1 and pics[0][1].startswith("📊 TikTok за прошлую неделю, 19.10–25.10\n\nTikTok")
    and "за 19.10–25.10" in pics[0][1], f"отдельным снимком, неделя целиком: {pics[0][1][:80]!r}")
say(due(2026, 10, 27, 18) == [], "и всё")

print("\n--- TikTok и назавтра не досчитал — ждёт до вечера, потом честно ---")
with jobs.connect() as con:
    con.execute("DELETE FROM events")
albums.clear()
NOW[0] = dt.datetime(2026, 10, 26, 12, 0)
run_due()
NOW[0] = dt.datetime(2026, 10, 27, 12, 0)
ENDS["tiktok"] = [D(2026, 10, 24)]
log = run_due()
say("TikTok — пересниму в 14:00" in log[-1] and len(albums) == 3, f"TikTok без воскресенья — ждёт: {log[-1]}")
NOW[0] = dt.datetime(2026, 10, 27, 19, 30)
run_due()
say(len(albums) == 4 and "за 19.10–24.10 — tiktok отстаёт" in albums[-1][0][0][1],
    "последняя попытка — шлёт с тем, что есть, и пишет, по какой день")

print("\n--- 1-е число: месяц ---")
with jobs.connect() as con:
    con.execute("DELETE FROM events")
albums.clear()
ENDS.clear()
NOW[0] = dt.datetime(2026, 11, 1, 12, 0)
run_due()
say(calls[-1] == ("month", ["reels", "shorts"], True, False)
    and albums[-1][0][0][1].startswith("📊 Статистика за октябрь 2026\nTikTok пришлю завтра"),
    f"октябрь без TikTok, TikTok — 2-го: {albums[-1][0][0][1].splitlines()[:2]}")
say(due(2026, 11, 2) == [("day", "main"), ("week", "main"), ("month", "tiktok")],
    "2-го: вчера, неделя (понедельник) и TikTok за октябрь")

print("\n--- нет снимков — текстом, а не тишиной ---")
texts = []
bot.send = lambda text, preview=True, chat_id=None, keyboard=False, thread=None: texts.append(text) or True
say(bot.send_report([], "📊 Статистика за вчера, 28.10") and "снимки не получились" in texts[-1], texts[-1][:60])

# --------------------------------------------------------- календарь TikTok
print("\n--- TikTok: даты клеток календаря ---")
NOW[0] = dt.datetime(2026, 10, 29, 12, 0)
XS = [0, 154, 309, 463, 617, 771, 926]


def tt_view(days, top, ring=None, selected=()):
    """Внешние клетки 154x110 для дат days (по порядку), строки через 137,
    перед 1-м числом (кроме первого) — промежуток заголовка 253."""
    nodes, y, prev = [], top, None
    for d in days:
        col = (d.weekday() + 1) % 7
        if prev is not None:
            if d.day == 1:
                y += 253
            elif col == 0:
                y += 137
        x = XS[col]
        nodes.append(N(b=(x, y, x + 154, y + 110)))
        size = 116 if d == ring else 120 if d in selected else 110
        nodes.append(N(b=(x + 22, y, x + 22 + size, y + size)))
        prev = d
    nodes.append(N(desc="Обновить", b=(44, 2150, 1036, 2177)))
    return nodes


def span(a, b):
    return [a + dt.timedelta(days=i) for i in range((b - a).days + 1)]


OCT = span(D(2026, 10, 1), D(2026, 10, 31))
got = accstats._tt_map(W) if not (setattr(ui, "dump", lambda *a, **k: tt_view(OCT, 1032, ring=D(2026, 10, 29)))) else None
say(got and got[0][0] == D(2026, 10, 1) and got[-1][0] == D(2026, 10, 31), "октябрь с кольцом сегодня — 1…31")
SCROLLED = span(D(2026, 9, 6), D(2026, 10, 3))
ui.dump = lambda *a, **k: tt_view(SCROLLED, 903)
got = accstats._tt_map(W)
say(got and got[0][0] == D(2026, 9, 6) and got[-1][0] == D(2026, 10, 3),
    "пролистано вверх (живьём 29.10: 6–30 сент. + 1–3 окт.), кольца нет — по началу месяца")
ui.dump = lambda *a, **k: tt_view(span(D(2026, 9, 6), D(2026, 9, 26)), 903)
say(accstats._tt_map(W) == [], "ни кольца, ни начала месяца — не угадывает")
ui.dump = lambda *a, **k: tt_view(SCROLLED, 903, selected=(D(2026, 9, 7),))
got = accstats._tt_map(W)
say(got and got[0][0] == D(2026, 9, 6), "выбранная дата (другой кружок) не сбивает")
ui.dump = lambda *a, **k: tt_view(OCT, 1032, ring=D(2026, 10, 29), selected=(D(2026, 10, 19),))
got = accstats._tt_map(W)
say(got and got[0][0] == D(2026, 10, 1) and got[-1][0] == D(2026, 10, 31),
    "кольцо и выбранная дата — обе «другие» (живьём 29.10): по 1-му числу первой клетки")

print("\n--- TikTok: выбрать прошлый месяц, листая к нему ---")
views = {"oct": tt_view(OCT, 1032, ring=D(2026, 10, 29)), "sep": tt_view(span(D(2026, 8, 30), D(2026, 10, 3)), 903)}
state = {"view": "oct"}
taps, swipes = [], []
ui.dump = lambda *a, **k: views[state["view"]]


def shell(cmd, check=True):
    swipes.append(cmd)
    if "1000" in cmd.split()[3]:                          # палец сверху вниз — к прошлому
        state["view"] = "sep"
    return ""


adb.shell = shell
ui.tap_node = lambda n: taps.append(n)
adb.tap = lambda x, y: taps.append((x, y))
accstats._wide_band = lambda test, low=0.7: 2313
adb.current_app = lambda: ("com.zhiliaoapp.musically", "")
say(accstats._tt_pick_dates(D(2026, 9, 1), D(2026, 9, 30), W), "выбрано")
sep_map = dict((c.bounds, d) for d, c in accstats._tt_map(W))
picked = [sep_map.get(t.bounds) for t in taps if not isinstance(t, tuple)]
say(len(swipes) == 1 and picked == [D(2026, 9, 1), D(2026, 9, 30)] and taps[-1] == (540, 2313),
    f"одно листание вверх, касания 1 и 30 сент., потом «Обновить»: {picked}")
state["view"], taps[:], swipes[:] = "oct", [], []
say(accstats._tt_pick_dates(D(2026, 10, 19), D(2026, 10, 25), W) and not swipes,
    "неделя в этом месяце — без листания")

print("\n--- TikTok: календарь сдвинут на день (живьём 29.10: выбрал 19–25 — показал 18–24) ---")
RU = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
real_pick = accstats._tt_pick_dates
for shift_on_phone, want_picks in [(1, [(D(2026, 10, 20), D(2026, 10, 26))]),
                                   (0, [(D(2026, 10, 20), D(2026, 10, 26)), (D(2026, 10, 19), D(2026, 10, 25))])]:
    picks, label = [], [""]

    def fake_pick(a, b, w, s=shift_on_phone):
        picks.append((a, b))
        a, b = a - dt.timedelta(days=s), b - dt.timedelta(days=s)
        label[0] = f"{a.day} {RU[a.month - 1]} - {b.day} {RU[b.month - 1]}"
        return True

    accstats._tt_pick_dates = fake_pick
    ui.dump = lambda *a, **k: [N("Другое", b=(628, 320, 845, 390), clickable=True),
                               N(label[0], b=(66, 622, 582, 669))]
    text = accstats._tt_span("week", W, H)
    say(picks == want_picks and accstats._range_of(text) == (D(2026, 10, 19), D(2026, 10, 25)),
        f"{'сдвиг есть' if shift_on_phone else 'сдвига нет'}: выбрано {[(f'{a:%d}', f'{b:%d}') for a, b in picks]}, "
        f"показано «{text}»")
accstats._tt_pick_dates = real_pick
NOW[0] = dt.datetime(2026, 10, 26, 12, 0)                # понедельник: 26-е — сегодня, его не выбрать
picks = []
device.back = lambda: None
accstats._tt_pick_dates = lambda a, b, w: picks.append((a, b)) or False
accstats._tt_span("week", W, H)
say(picks[:1] == [(D(2026, 10, 20), D(2026, 10, 25))], f"в понедельник конец — вчера: {picks[:1]}")
accstats._tt_pick_dates = real_pick
NOW[0] = dt.datetime(2026, 10, 29, 12, 0)

# --------------------------------------------------------- Instagram
print("\n--- Instagram: неделя и месяц в календаре ---")


def ig_calendar(live_to):
    """Как живой 28.10: сентябрь и октябрь числами, с live_to октября — серые."""
    xs = [67, 202, 338, 474, 610, 746, 882]
    nodes = []

    def month(first_col, ndays, top, live):
        out, col, y = [], first_col, top
        for d in range(1, ndays + 1):
            x = xs[col]
            if d <= live:
                out.append(N(b=(x, y, x + 132, y + 121), clickable=True))
            out.append(N(str(d), b=(x + 45, y + 43, x + 85, y + 89)))
            col += 1
            if col == 7:
                col, y = 0, y + 121
        return out
    nodes += month(1, 30, 632, 30)                       # 1 сент. — вторник (неделя с пн)
    nodes += month(3, 31, 1362, live_to)                 # 1 окт. — четверг
    return nodes + [N("Применить", b=(789, 1973, 993, 2105), clickable=True)]


def ig_date(node, live_to):
    seq = accstats._ig_days(ig_calendar(live_to), W)
    now = max(i for i, (d, _) in enumerate(seq) if d == 29)
    for i, (_, cell) in enumerate(seq):
        if cell is not None and cell.bounds == node.bounds:
            return D(2026, 10, 29) - dt.timedelta(days=now - i)


ui.dump = lambda *a, **k: ig_calendar(28)
taps.clear()
got = accstats._ig_pick_dates(D(2026, 10, 19), D(2026, 10, 25), W)
say(got == (D(2026, 10, 19), D(2026, 10, 25)) and [ig_date(t, 28) for t in taps] == list(got),
    f"неделя: касания 19 и 25 окт.: {got}")
taps.clear()
got = accstats._ig_pick_dates(D(2026, 9, 1), D(2026, 9, 30), W)
say(got == (D(2026, 9, 1), D(2026, 9, 30)) and [ig_date(t, 28) for t in taps] == list(got), "месяц: 1 и 30 сент.")
ui.dump = lambda *a, **k: ig_calendar(24)
taps.clear()
got = accstats._ig_pick_dates(D(2026, 10, 19), D(2026, 10, 25), W)
say(got == (D(2026, 10, 19), D(2026, 10, 24)), f"25-е ещё серое — по последний открытый: {got}")
taps.clear()
say(accstats._ig_pick_dates(D(2026, 8, 1), D(2026, 8, 31), W) is None and not taps, "августа на экране нет — не трогает")

# --------------------------------------------------------- YouTube
print("\n--- YouTube: график, «28 ДН», чип месяца ---")
CHIPS = [N(desc=d, b=(x, 269, x + 150, 357), clickable=True)
         for d, x in (("7 ДН", 44), ("28 ДН", 221), ("90 ДН", 420), ("365 ДН", 619), ("Окт.", 865),
                      ("Сент.", 1032))]
LOADED = CHIPS + [N(b=(0, 390, 1080, 2135)), N(b=(44, 437, 1036, 987)), N(b=(0, 1377, 1080, 1575), clickable=True)]
LOADING = CHIPS + [N(b=(0, 390, 1080, 2135)), N(b=(0, 528, 1080, 595)), N(b=(0, 595, 1080, 793), clickable=True)]
say(accstats._yt_chart(LOADED, W, H).bounds == (44, 437, 1036, 987), "график найден (живое дерево 28.10)")
say(accstats._yt_chart(LOADING, W, H) is None, "страница грузится — графика нет")
taps.clear()
backs = []
device.back = lambda: backs.append(1)
accstats._yt_views_card = lambda w, h: True
screens = iter([LOADED, LOADING, LOADED, LOADED, LOADED])
ui.dump = lambda *a, **k: next(screens)
say(accstats._yt_day(W, H), "за вчера: подсказка вызвана")
point = taps[-1]
say(taps[0].desc == "28 ДН" and 437 < point[1] < 987 and 1030 <= point[0] < 1036,
    f"«28 ДН», касание у самого края графика: {point}")
taps.clear()
screens = iter([LOADED] * 3 + [[N("С момента публикации", b=(44, 269, 535, 357))]])
say(not accstats._yt_day(W, H) and backs, "касание увело на страницу видео — назад и «не вышло»")
taps.clear()
ui.dump = lambda *a, **k: LOADED
first, _ = accstats.report_span("month", D(2026, 10, 29))
say(accstats._yt_chip(W, H, lambda s: s.lower().startswith(accstats.YT_MONTHS[first.month]))
    and taps[0].desc == "Сент.", "прошлый месяц — чип «Сент.»")
taps.clear()
say(accstats._yt_chip(W, H, accstats.YT_WEEK.match) and taps[0].desc == "7 ДН", "неделя — «7 ДН»")

# --------------------------------------------------------- бот
print("\n--- бот ---")
texts.clear()
saved.clear()
bot._cmd_daily_stats("/stats каждый день 9:30")
say(saved.get("daily_stats") == "09:30" and "по понедельникам" in texts[-1] and "1-го" in texts[-1], texts[-1][:90])
bot._cmd_daily_stats("/stats каждый день выкл")
say(saved.get("daily_stats") == "" and bot.daily_stats_time() == "", "выключено")
for text, want in [("/stats неделя", "week"), ("/stats за прошлую неделю", "week"), ("/stats месяц", "month"),
                   ("статистика за месяц", "month"), ("/stats вчера", 1), ("/stats 2", 2), ("/stats все", -1)]:
    say(bot.stats_days(text) == want, f"{text!r} -> {bot.stats_days(text)!r}")
for text, daily in [("/stats каждый день 10:00", True), ("/stats ежедневно", True), ("/stats неделя", False)]:
    say(bool(bot.DAILY_WORDS.search(text.lower())) == daily, f"{text!r}: {'время' if daily else 'снимок'}")
marks = [b["callback_data"] for row in __import__("json").loads(bot._stats_markup())["inline_keyboard"] for b in row]
say("s:week" in marks and "s:month" in marks and bot.STATS_PERIODS["week"] == "за прошлую неделю",
    f"кнопки «За прошлую неделю», «За прошлый месяц»: {marks}")
snapped = []
bot._snap_stats = lambda days=None, say=None: snapped.append(days)
bot._call = lambda *a, **k: {"ok": True}
bot._stats_button({"id": "1"}, "s:week", "111", 5, False, None)
bot._stats_button({"id": "1"}, "s:1", "111", 5, False, None)
say(snapped == ["week", 1], f"кнопки доходят до съёмки: {snapped}")

print("\n--- разблокировка сразу после блокировки (живьём 29.10: второй сбор снимал чёрный экран) ---")
import importlib  # noqa: E402

device = importlib.reload(device)
human.pause = lambda *a, **k: None
phone = {"nfc": "mScreenState=ON_UNLOCKED", "power": "mWakefulness=Dozing"}
keys = []
adb.shell = lambda cmd, *a, **k: phone["nfc"] if "nfc" in cmd else phone["power"] if "power" in cmd else ""
adb.keyevent = lambda key: keys.append(key)
device.wake()
say(keys == ["KEYCODE_WAKEUP"], "MIUI говорит «unlocked», а телефон засыпает (Dozing) — будит")
keys.clear()
phone["power"] = "mWakefulness=Awake"
device.wake()
say(keys == [], "проснулся по-настоящему — не трогает")
states = iter(["mScreenState=ON_UNLOCKED", "mScreenState=ON_UNLOCKED", "mScreenState=OFF"])
adb.shell = lambda cmd, *a, **k: next(states, "mScreenState=OFF") if "nfc" in cmd else ""
device.lock()
say(keys == ["KEYCODE_POWER"] and next(states, None) is None, "блокировка ждёт, пока экран погаснет")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
