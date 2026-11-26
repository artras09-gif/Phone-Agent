"""Утренняя пробная публикация (`routecheck.py`) — без телефона.

Проверяется: время и «раз в день»; финальная кнопка не нажимается (DRY_RUN
на время проверки и обратно после); в бот — только когда есть о чём сказать
(маршрут сломался и модель нашла путь, или не прошёл); всё как обычно —
молчим.
"""
import datetime as dt
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402

tmp = tempfile.mkdtemp(prefix="pa_routecheck_")
config.BASE = tmp
config.DB_PATH = os.path.join(tmp, "jobs.db")
config.QUEUE_DIR = os.path.join(tmp, "queue")

import device         # noqa: E402
import poster         # noqa: E402
import routecheck     # noqa: E402
import telegram_bot   # noqa: E402

ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


telegram_bot.load_settings = lambda: {"token": "T", "owner": 1}
device.go_home = lambda: None
device.lock = lambda: None
routecheck.probe_clip = lambda: os.path.join(tmp, "clip.mp4")
routecheck.targets = lambda: ["tiktok", "reels", "shorts"]
poster.load_recipes = lambda: {"tiktok": {"title": "TikTok"}, "reels": {"title": "Instagram Reels"},
                               "shorts": {"title": "YouTube Shorts"}}

print("1. Когда")
day = dt.date(2026, 11, 27)
say(not routecheck.due(dt.datetime.combine(day, dt.time(4, 59))), "до 05:30 — рано")
say(routecheck.due(dt.datetime.combine(day, dt.time(6, 10))), "в 06:10 — пора")
say(not routecheck.due(dt.datetime.combine(day, dt.time(9, 30))), "после 09:00 — день пропущен, не лезем")

print("2. Всё как обычно — молчим, финальную не жмём")
dry_seen, sent = [], []


def all_fine(video, caption, target):
    dry_seen.append(config.DRY_RUN)
    return True, f"=== {target}\n  18. СУХОЙ ПРОГОН: не нажимаю «ФИНАЛЬНАЯ КНОПКА»\nготово"


poster.post = all_fine
config.DRY_RUN = False
text = routecheck.run(log=[], send=sent.append)
say(dry_seen == [True, True, True] and config.DRY_RUN is False,
    "во время проверки — сухой прогон, после — как было")
say(not sent and "✓ TikTok" in text, "всё прошло — в бот не пишем")
say(not routecheck.due(dt.datetime.now().replace(hour=7, minute=0)), "сегодня уже пробовали — второй раз не идём")

print("3. Что-то поменялось — пишем")


def mixed(video, caption, target):
    if target == "reels":
        return True, "  путь: «из редактора дальше» нашёлся за 1 нажат. — запомнил\nготово"
    if target == "shorts":
        return False, ("ОШИБКА: не нашёл элемент для «Добавить из галереи». Дерево экрана "
                       "сохранено в C:\\x")
    return True, "готово"


poster.post = mixed
text = routecheck.run(log=[], send=sent.append)
say(sent and "путь нашла модель" in sent[-1] and "✗ YouTube Shorts: не нашёл элемент" in sent[-1]
    and "Дерево экрана" not in sent[-1], f"в бот ушло:\n{sent[-1] if sent else ''}")

print("ВСЁ ХОРОШО" if ok else "ЕСТЬ ОШИБКИ")
sys.exit(0 if ok else 1)
