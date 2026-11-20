"""Альбом статистики, когда связь с Telegram мигает — без сети.

21.11 в 00:51: снимки сняты все, а бот написал «Снимки статистики не
получились… Телефон подключён?» — альбом оборвался на первой попытке, через
две минуты ушёл бы. Теперь: несколько попыток, а не ушёл — правда про
Telegram, а не вопрос про телефон. И ⚠ «в N раз больше» — только по
истории ТОГО ЖЕ аккаунта.
"""
import os
import sys
import tempfile
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402

tmp = tempfile.mkdtemp(prefix="pa_album_")
config.BASE = tmp
config.DB_PATH = os.path.join(tmp, "jobs.db")
config.QUEUE_DIR = os.path.join(tmp, "queue")

import accstats      # noqa: E402
import telegram_bot  # noqa: E402

ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


sleeps, calls, sent_texts = [], {"n": 0, "fail": 0}, []
telegram_bot.time.sleep = lambda s: sleeps.append(s)
telegram_bot.load_settings = lambda: {"token": "T", "owner": 1}
telegram_bot.current_chat = lambda: None
telegram_bot._thread_for = lambda target, thread: None
telegram_bot.send = lambda text, **k: sent_texts.append(text)


class Resp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b'{"ok": true}'


def fake_open(req, timeout=None):
    calls["n"] += 1
    if calls["fail"]:
        calls["fail"] -= 1
        raise urllib.error.URLError("[SSL: UNEXPECTED_EOF_WHILE_READING] EOF")
    return Resp()


telegram_bot.urllib.request.urlopen = fake_open
pics = [(b"\x89PNG" + b"1" * 50, "a"), (b"\x89PNG" + b"2" * 50, "b")]

print("Связь оборвалась один раз")
calls.update(n=0, fail=1)
sleeps.clear()
say(telegram_bot.send_album(pics) is True, "альбом ушёл со второй попытки")
say(calls["n"] == 2 and sleeps == [20], f"две попытки, пауза 20 с ({calls['n']}, {sleeps})")

print("Связи нет совсем")
calls.update(n=0, fail=99)
sleeps.clear()
say(telegram_bot.send_album(pics) is False and calls["n"] == 3, "три попытки и сдался")

print("Что пишет бот")
day = os.path.join(os.path.dirname(config.QUEUE_DIR), "analytics", __import__("datetime").date.today().isoformat())
os.makedirs(day)
with open(os.path.join(day, "tiktok.png"), "wb") as f:
    f.write(b"\x89PNG" + b"0" * 100)
with open(os.path.join(day, "youtube.png"), "wb") as f:
    f.write(b"\x89PNG" + b"0" * 100)
calls.update(fail=99)
say(telegram_bot.send_shots(fresh=True) is False, "снимки есть, Telegram не принял")
text = telegram_bot.shots_failed_text()
say("сняты" in text and "Telegram" in text and "телефон" not in text,
    f"правда про Telegram, без вопроса про телефон ({text})")
calls.update(fail=0)
say(telegram_bot.send_shots(fresh=True) is True, "связь вернулась — ушло")
for name in ("tiktok.png", "youtube.png"):
    os.remove(os.path.join(day, name))
telegram_bot.send_shots(fresh=True)
say("телефон" in telegram_bot.shots_failed_text(), "снимков нет — тогда вопрос про телефон")

print("⚠ только по своему аккаунту")
root = os.path.join(os.path.dirname(config.QUEUE_DIR), "analytics")
for i, v in enumerate([30, 25, 40, 35], start=10):
    d = os.path.join(root, f"2026-10-{i}")
    accstats._note(d, "youtube_account", "Другой канал")
    accstats._views_note(d, "youtube", str(v), f"{i}.10")
now = os.path.join(root, "2026-10-20")
accstats._note(now, "youtube_account", "Канал А")
accstats._views_note(now, "youtube", "4 424", "19.10")
say("⚠" not in accstats.numbers_text(now, ["shorts"]), "чужая история — не повод для ⚠")
for i, v in enumerate([30, 25, 40, 35], start=21):
    d = os.path.join(root, f"2026-10-{i}")
    accstats._note(d, "youtube_account", "Канал А")
    accstats._views_note(d, "youtube", str(v), f"{i}.10")
later = os.path.join(root, "2026-10-30")
accstats._note(later, "youtube_account", "Канал А")
accstats._views_note(later, "youtube", "750 636", "29.10")
say("⚠" in accstats.numbers_text(later, ["shorts"]), "скачок на фоне СВОЕЙ истории — ⚠ есть")

print("ВСЁ ХОРОШО" if ok else "ЕСТЬ ОШИБКИ")
sys.exit(0 if ok else 1)
