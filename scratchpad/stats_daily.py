"""Статистика за вчера — каждый день сама (2026-10-28: «мне каждый день нужно,
чтобы он отправлял стату за полный прошлый день, но в инсте сейчас только так
выбрать стату, как быть?»).

Без телефона: сегодня подменено на 28.10.2026, съёмка сетей — заглушкой,
которая кладёт снимки и подписи так же, как настоящая. Узлы YouTube Studio —
из живого дерева Redmi 28.10 (страница «Просмотры»).
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

tmp = tempfile.mkdtemp(prefix="pa_daily_")
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


NOW = [dt.datetime(2026, 10, 28, 12, 0)]


class Today(dt.date):
    @classmethod
    def today(cls):
        return cls(2026, 10, 28)


class Clock(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW[0]


accstats.dt = types.SimpleNamespace(date=Today, timedelta=dt.timedelta, datetime=Clock, time=dt.time)
bot.dt = types.SimpleNamespace(date=Today, timedelta=dt.timedelta, datetime=Clock, time=dt.time)
human.pause = lambda *a, **k: None
accstats.time.sleep = lambda s: None


def log_event(kind, payload=""):
    with jobs.connect() as con:
        con.execute("INSERT INTO events (kind,payload,at,device) VALUES (?,?,?,?)",
                    (kind, payload, time.mktime(NOW[0].timetuple()), jobs.DEVICE))


jobs.log_event = log_event
W, H = 1080, 2400

print("--- дата из подписи периода ---")
for text, want in [("27 окт", dt.date(2026, 10, 27)), ("27 окт - 27 окт", dt.date(2026, 10, 27)),
                   ("Oct 27", dt.date(2026, 10, 27)), ("27 oct.", dt.date(2026, 10, 27)),
                   ("5 мая", dt.date(2026, 5, 5)), ("31 dic", dt.date(2025, 12, 31)),
                   ("2,357", None), ("", None), ("Основные метрики", None)]:
    say(accstats._day_of(text) == want, f"{text!r} -> {accstats._day_of(text)}")

print("\n--- подпись «за вчера» и отметка дня ---")
folder = accstats.daily_dir()
say(accstats._day_note(folder, "tiktok", dt.date(2026, 10, 27), "27 окт", "отстаёт") == "за вчера, 27.10"
    and accstats._notes_in(folder).get("tiktok_day") == "2026-10-27", "вчера — «за вчера, 27.10», день отмечен")
note = accstats._day_note(folder, "tiktok", dt.date(2026, 10, 26), "26 окт", "TikTok отстаёт")
say(note == "за 26.10 — TikTok отстаёт", f"позавчера — честно: {note}")
say(accstats._day_note(folder, "tiktok", None, "Oct 20 - Oct 27", "отстаёт") == "за Oct 20 - Oct 27",
    "не дата одного дня — как показано")

print("\n--- когда снимать ---")
bot.load_settings = lambda: {"token": "1:" + "A" * 35, "owner": "111"}
saved = {}
bot._saved = lambda: dict(saved)
bot._remember = lambda **f: saved.update(f)
say(bot.daily_stats_time() == "12:00", "по умолчанию — каждый день в 12:00")
say(not accstats.daily_due(dt.datetime(2026, 10, 28, 11, 59)), "11:59 — рано")
say(accstats.daily_due(dt.datetime(2026, 10, 28, 12, 0)), "12:00 — пора")
say(accstats.due(dt.datetime(2026, 10, 28, 12, 0)), "сторож окна (due) тоже видит, что пора")
bot.load_settings = lambda: {"token": "", "owner": ""}
say(not accstats.daily_due(dt.datetime(2026, 10, 28, 12, 0)), "бот не настроен — слать некому")
bot.load_settings = lambda: {"token": "1:" + "A" * 35, "owner": "111"}
saved["daily_stats"] = ""
say(not accstats.daily_due(dt.datetime(2026, 10, 28, 15, 0)), "выключено командой — не снимает")
saved["daily_stats"] = "09:30"
say(accstats.daily_due(dt.datetime(2026, 10, 28, 9, 30)), "своё время 09:30 — снимает в 09:30")
saved.pop("daily_stats")

print("\n--- попытки: Instagram ещё не открыл вчера; TikTok, как всегда, на два дня позади ---")
shot_days = {"tiktok": [dt.date(2026, 10, 26)], "reels": [dt.date(2026, 10, 26), dt.date(2026, 10, 27)]}
LATE = {"tiktok": "TikTok отстаёт", "reels": "Instagram ещё не открыл"}
calls = []


def fake_collect(nets=None, log=None, days=None, folder=None, fresh=True, full=True):
    calls.append((list(nets), fresh, full, days))
    os.makedirs(folder, exist_ok=True)
    for net in nets:
        name = accstats.SHOT_FILES[net]
        open(os.path.join(folder, name + ".png"), "wb").write(b"png")
        if net == "shorts":
            accstats._note(folder, "youtube", "за вчера: просмотры за последний посчитанный день")
            continue
        day = shot_days[net].pop(0) if len(shot_days[net]) > 1 else shot_days[net][0]
        accstats._note(folder, name, accstats._day_note(folder, name, day, f"{day:%d.%m}", LATE[net]))
    return {}


accstats._collect_all = fake_collect
albums = []
bot.send_album = lambda pics, chat_id=None, thread=None: albums.append((pics, chat_id, thread)) or True
bot.destinations = lambda role: []
config.FEED_APPS = ["tiktok", "reels", "shorts"]

NOW[0] = dt.datetime(2026, 10, 28, 12, 0)
log = []
accstats.collect_all(log=log)
say(calls[-1] == (["tiktok", "reels", "shorts"], True, False, 1),
    f"12:00 — все три сети за вчера, без плиток: {calls[-1]}")
say(not albums and log[-1].endswith("Instagram — пересниму в 14:00"),
    f"альбом не ушёл, TikTok в ожидание не попал: {log[-1]}")
say(not accstats.daily_due(dt.datetime(2026, 10, 28, 13, 59)), "через час — ещё ждём")
NOW[0] = dt.datetime(2026, 10, 28, 14, 0)
say(accstats.daily_due(NOW[0]), "через два часа — снова пора")
accstats.collect_all(log=log)
say(calls[-1] == (["reels"], False, False, 1), f"14:00 — переснят только Instagram, остальное на месте: {calls[-1]}")
say(len(albums) == 1, "Instagram за вчера есть — альбом ушёл один раз")
pics, chat, _ = albums[0]
caps = [c for _, c in pics]
say(chat == "111" and caps[0].startswith("📊 Статистика за вчера, 27.10\n\nYouTube Studio"),
    f"владельцу (темы статистики нет), заголовок на первом снимке: {caps[0][:50]!r}")
say(any("за вчера, 27.10" in c and c.startswith("Instagram") for c in caps)
    and any("за 26.10 — TikTok отстаёт" in c and c.startswith("TikTok") for c in caps), f"подписи: {caps}")
say(not accstats.daily_due(dt.datetime(2026, 10, 28, 20, 0)), "ушёл — до завтра больше не снимает")
say(accstats.daily_due(dt.datetime(2026, 10, 29, 12, 0)), "завтра в 12:00 — снова")

print("\n--- последняя попытка: альбом уходит с тем, что есть ---")
with jobs.connect() as con:
    con.execute("DELETE FROM events")
albums.clear()
shot_days["reels"] = [dt.date(2026, 10, 26)]
NOW[0] = dt.datetime(2026, 10, 28, 19, 30)
bot.destinations = lambda role: [("-100", 7)]
accstats.collect_all(log=log)
say(len(albums) == 1 and albums[0][1:] == ("-100", 7), "19:30 + 2 ч позже 21:00 — шлёт сразу, в тему статистики")
say(any(c.startswith("Instagram") and "за 26.10 — Instagram ещё не открыл" in c for _, c in albums[0][0]),
    "в подписи Instagram — за какой день на самом деле")

print("\n--- телефон не ответил: текстом, а не тишиной ---")
with jobs.connect() as con:
    con.execute("DELETE FROM events")
texts = []
bot.send = lambda text, preview=True, chat_id=None, keyboard=False, thread=None: texts.append(text) or True
say(bot.send_daily_stats([], dt.date(2026, 10, 27)) and "снимки не получились" in texts[-1], texts[-1][:60])

print("\n--- YouTube: график «Просмотров» и касание последней точки ---")
CHIPS = [N(desc=d, b=(x, 269, x + 150, 357), clickable=True)
         for d, x in (("7 ДН", 44), ("28 ДН", 221), ("90 ДН", 420), ("365 ДН", 619), ("Окт.", 865))]
LOADED = CHIPS + [N(b=(0, 390, 1080, 2135)), N(b=(44, 437, 1036, 987)), N(b=(0, 1377, 1080, 1575), clickable=True)]
LOADING = CHIPS + [N(b=(0, 390, 1080, 2135)), N(b=(0, 528, 1080, 595)), N(b=(0, 595, 1080, 793), clickable=True)]
say(accstats._yt_chart(LOADED, W, H).bounds == (44, 437, 1036, 987), "график найден (живое дерево 28.10)")
say(accstats._yt_chart(LOADING, W, H) is None, "страница грузится, на месте графика строки видео — графика нет")
taps, backs = [], []
adb.tap = lambda x, y: taps.append((x, y))
ui.tap_node = lambda n: taps.append(n)
device.back = lambda: backs.append(1)
accstats._yt_views_card = lambda w, h: True
screens = iter([LOADED, LOADING, LOADED, LOADED, LOADED])
ui.dump = lambda *a, **k: next(screens)
say(accstats._yt_day(W, H), "подсказка вызвана")
point = taps[-1]
say(taps[0].desc == "28 ДН" and 437 < point[1] < 987 and 1030 <= point[0] < 1036,
    f"сначала «28 ДН» (за 7 график на Redmi не рисуется), потом касание у правого края: {point}")
taps.clear()
# Чип «28 ДН», график дважды на месте, а после касания — страница видео.
screens = iter([LOADED] * 3 + [[N("С момента публикации", b=(44, 269, 535, 357))]])
say(not accstats._yt_day(W, H) and backs, "касание увело на страницу видео — назад и «не вышло»")
taps.clear()
ui.dump = lambda *a, **k: LOADING
say(not accstats._yt_day(W, H) and all(not isinstance(t, tuple) for t in taps),
    "график так и не появился — по странице не касается")

print("\n--- бот: «/stats каждый день …» ---")
texts.clear()
saved.clear()
bot._cmd_daily_stats("/stats каждый день 9:30")
say(saved.get("daily_stats") == "09:30" and "09:30" in texts[-1], texts[-1][:70])
bot._cmd_daily_stats("/stats каждый день выкл")
say(saved.get("daily_stats") == "" and bot.daily_stats_time() == "", "выключено")
bot._cmd_daily_stats("/stats каждый день")
say("не присылаю" in texts[-1], f"без времени — что сейчас: {texts[-1][:50]}")
for text, daily in [("/stats каждый день 10:00", True), ("/stats ежедневно", True), ("/stats 1", False),
                    ("/stats за день", False), ("статистика за вчера", False)]:
    say(bool(bot.DAILY_WORDS.search(text.lower())) == daily, f"{text!r}: {'время' if daily else 'снимок'}")
say(bot.stats_days("/stats вчера") == 1 and bot.STATS_PERIODS[1] == "за вчера", "«/stats вчера» — за вчера")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
