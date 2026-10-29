"""Статистика «за всё время» и плитки профилей (2026-10-22: «собери стату за
все время»; «надо чтобы мог собирать плитки вот этих экранов где просмотры
видны» — снимок вкладки Reels профиля с просмотрами под каждой плиткой).

Без телефона: разбор команды, кнопка периода, состав альбома и подписи.
"""
import datetime as dt
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402

tmp = tempfile.mkdtemp(prefix="pa_alltime_")
config.BASE = tmp
config.DB_PATH = os.path.join(tmp, "jobs.db")
config.QUEUE_DIR = os.path.join(tmp, "queue")
config.SERIAL = None

import accstats  # noqa: E402
import adb       # noqa: E402
import human     # noqa: E402
import telegram_bot as bot  # noqa: E402
import ui        # noqa: E402

ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


print("команда /stats:")
for text, want in [("/stats все", -1), ("/stats всё время", -1), ("/stats за все время", -1),
                   ("/stats all", -1), ("статистика за всё время", -1), ("/stats 1", 1),
                   ("/stats 2", 2), ("/stats", None), ("/stats 7", 0), ("/stats за день", 1)]:
    got = bot.stats_days(text)
    say(got == want, f"«{text}» -> {got} (ждали {want})")

print("кнопки периода:")
rows = json.loads(bot._stats_markup())["inline_keyboard"]
datas = [b["callback_data"] for row in rows for b in row]
say("s:-1" in datas, f"есть «За всё время»: {datas}")
say(bot.STATS_PERIODS.get(-1) == "за всё время", "подпись периода")
say(accstats.PERIOD_WORDS.get(accstats.ALL_TIME) == "всё время", "слово периода в accstats")

print("кнопка s:-1 доходит до съёмки с days=-1:")
got = []
bot._snap_stats = lambda days=None, say=None: got.append(days)
bot._call = lambda *a, **k: {"ok": True}
bot._edit = lambda *a, **k: None
bot._stats_button({"id": "1"}, "s:-1", 5, 7, False, None)
say(got == [-1], f"days={got}")

print("альбом: статистика сети, следом её плитки:")
folder = accstats.shots_dir()
os.makedirs(folder, exist_ok=True)
for name in ("youtube", "youtube_tiles", "instagram", "instagram_tiles", "instagram_tiles2",
             "tiktok", "tiktok_tiles"):
    with open(os.path.join(folder, name + ".png"), "wb") as f:
        f.write(b"\x89PNG" + b"0" * 20000)
accstats._note(folder, "tiktok_tiles", "3 видео, просмотров на плитках всего 2 531")
accstats._note(folder, "tiktok", "за 365 дней")
items, day = accstats.latest_shots()
names = [os.path.basename(p) for p, _ in items]
say(names == ["youtube.png", "youtube_tiles.png", "instagram.png", "instagram_tiles.png",
              "instagram_tiles2.png", "tiktok.png", "tiktok_tiles.png"], f"порядок: {names}")
caps = dict((os.path.basename(p), c) for p, c in items)
say("2 531" in caps["tiktok_tiles.png"] and "плитки" in caps["tiktok_tiles.png"], caps["tiktok_tiles.png"])
say("(дальше)" in caps["instagram_tiles2.png"], caps["instagram_tiles2.png"])
say(len(items) <= 10, f"в альбоме {len(items)} из 10 возможных")

print("плитки: один снимок, первый экран (2026-10-29: «просто первый экран с плитками один раз»):")
shots, swipes = [], []
accstats._snap = lambda folder, name: shots.append(name) or os.path.join(folder, name + ".png")
human.pause = lambda *a, **k: None
adb.shell = lambda cmd, **k: swipes.append(cmd) or ""
screens = []


def dump(**k):
    return screens.pop(0) if screens else []


ui.dump = dump
parsed = []
accstats.parse_profile = lambda nodes, h=2400: parsed.pop(0) if parsed else {"videos": []}
same = {"videos": [{"title": "a", "views": 10}, {"title": "b", "views": 20}]}
screens[:] = [[], [], []]
parsed[:] = [same, {"videos": same["videos"] + [{"title": "c", "views": 5}]}]
accstats._shot_tiles("tiktok", folder, 1080, 2400, same["videos"])
say(shots == ["tiktok_tiles"] and not any("input swipe" in s for s in swipes),
    f"видео больше, чем влезло, — всё равно один снимок и без листания: {shots}")
say("2 видео, просмотров на них 30" in accstats._notes_in(folder).get("tiktok_tiles", ""),
    "в подписи — то, что видно на снимке: " + accstats._notes_in(folder).get("tiktok_tiles", ""))

print("\nИТОГ:", "всё сошлось" if ok else "ЕСТЬ ОШИБКИ")
sys.exit(0 if ok else 1)
