"""Тревога в Telegram, когда помеха держится долго, — без телефона и без сети.

Просьба 2026-09-30: «если проблема будет неподвластна скрипту долгое время —
пусть отправляет уведомление в бота». Проверяем:
  * не на первую помеху, а когда держится ALERT_AFTER_MIN;
  * повтор не чаще ALERT_REPEAT_MIN;
  * помеха ушла — «выбрался», но только если о ней писали;
  * уходит ВЛАДЕЛЬЦУ, со снимком (multipart собран правильно);
  * сессия, не сумевшая начаться, пишет сразу;
  * бот не настроен — молча, без падений.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config        # noqa: E402
import session       # noqa: E402
import telegram_bot  # noqa: E402

ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


sent = []
settings = {"token": "123:проверка", "owner": "777"}
telegram_bot.load_settings = lambda: dict(settings)
session._alert_shot = lambda: b"\xff\xd8JPEG"
telegram_bot.alert = lambda text, image=None: sent.append((text, image)) or True

clock = [1000.0]
session.time.time = lambda: clock[0]
STATE = {"cfg": {"title": "Instagram Reels"}, "package": "com.instagram.android"}

print("--- когда писать ---")
state = dict(STATE)
session._trouble(state, "лента не двигается")
say(not sent, "первая помеха — молчим, агент ещё пробует")
clock[0] += (config.ALERT_AFTER_MIN - 1) * 60
session._trouble(state, "лента не двигается")
say(not sent, f"через {config.ALERT_AFTER_MIN - 1} мин — всё ещё молчим")
clock[0] += 70
session._trouble(state, "лента не двигается")
say(len(sent) == 1 and "Instagram Reels" in sent[0][0] and sent[0][1],
    f"держится {config.ALERT_AFTER_MIN} мин — написали со снимком: «{sent[0][0][:60]}…»")
clock[0] += 5 * 60
session._trouble(state, "лента не двигается")
say(len(sent) == 1, "через 5 минут — не повторяем")
clock[0] += config.ALERT_REPEAT_MIN * 60
session._trouble(state, "лента не двигается")
say(len(sent) == 2, f"через {config.ALERT_REPEAT_MIN} мин — напоминание")

session._trouble_over(state)
say(len(sent) == 3 and "выбрался" in sent[2][0] and sent[2][1] is None,
    "помеха ушла — «выбрался», без снимка")
session._trouble_over(state)
say(len(sent) == 3, "второй раз «выбрался» не пишем")

state = dict(STATE)
session._trouble(state, "окно")
session._trouble_over(state)
say(len(sent) == 3, "короткая помеха, о которой не писали, — и «выбрался» не пишем")

print("\n--- сессия не началась ---")
del sent[:]
session.vision.tidy_up = lambda: 0          # не чистить настоящую папку проекта
session.device.unlock = lambda: False
report = session.browse("reels", 60)
say(sent and "разблокировать" in sent[0][0] and sent[0][1] is None,
    f"не разблокировался — написали сразу и без снимка: «{sent[0][0][:50]}…»")

print("\n--- отправка: кому и как ---")
import urllib.request  # noqa: E402

calls = []


class Reply:
    def __init__(self, body):
        self.body = body

    def read(self):
        return json.dumps({"ok": True}).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_urlopen(req, data=None, timeout=None):
    url = req.full_url if hasattr(req, "full_url") else req
    body = req.data if hasattr(req, "data") and req.data else data
    calls.append((url, body, getattr(req, "headers", {})))
    return Reply(body)


urllib.request.urlopen = fake_urlopen
import importlib  # noqa: E402

importlib.reload(telegram_bot)
telegram_bot.load_settings = lambda: dict(settings)
telegram_bot.set_reply("999")               # кто-то чужой пишет боту прямо сейчас
good = telegram_bot.alert("⚠️ проверка", b"\xff\xd8JPEGDATA")
url, body, headers = calls[-1]
say(good and url.endswith("/sendPhoto"), "ушло фото")
say(b'name="chat_id"\r\n\r\n777' in body, "владельцу, а не тому, кто пишет боту сейчас")
say(b"JPEGDATA" in body and b"image/jpeg" in body and "multipart/form-data" in
    headers.get("Content-type", headers.get("Content-Type", "")), "multipart собран верно")
say("⚠️ проверка".encode() in body, "подпись на месте")

calls.clear()
telegram_bot.alert("без картинки")
say(calls and calls[-1][0].endswith("/sendMessage"), "без снимка — обычным сообщением")

settings["token"] = ""
calls.clear()
say(telegram_bot.alert("никуда", b"x") is False and not calls, "бот не настроен — молча")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
