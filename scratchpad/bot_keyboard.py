# -*- coding: utf-8 -*-
"""Стенд: кнопки в Telegram-боте. Телефон и сеть не нужны.

Добавлены 2026-09-02 по просьбе «только ссылки и постинг». Проверяем ровно
то, что новое: маршрутизацию нажатий и то, что клавиатура прикладывается к
последнему куску сообщения, а не к первому.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config, telegram_bot as tb

ok = True
say = lambda good, text: (print(("  [ок ] " if good else "  [ПРОВАЛ] ") + text), good)[1]

# Ни сети, ни файла настроек: подменяем всё, что ходит наружу.
tb.load_settings = lambda: {"token": "TEST", "owner": "1"}
tb.save_settings = lambda *a: None
config.BOT_OPEN = True

sent = []
tb._call = lambda method, params=None, timeout=70: (sent.append(params), {"ok": True})[1]

def press(text):
    sent.clear()
    tb._handle({"chat": {"id": 1}, "text": text})
    return sent

# --- 1. клавиатура уходит только на последнем куске длинного сообщения
sent.clear()
tb.send("x" * 8000, keyboard=True)
marks = [("reply_markup" in p) for p in sent]
ok &= say(len(sent) == 3 and marks == [False, False, True],
          "длинное сообщение: %d куска, клавиатура на последнем" % len(sent))

sent.clear()
tb.send("коротко", keyboard=False)
ok &= say(sent and "reply_markup" not in sent[0],
          "без клавиатуры разметка не прикладывается")

# --- 2. кнопка «Ссылки» зовёт разбор именно как голую команду
calls = []
real_links = tb._cmd_links
tb._cmd_links = lambda text: calls.append(text)
press(tb.BTN_LINKS)
ok &= say(calls == ["/links"], "кнопка Ссылки -> _cmd_links(%r)" % (calls[0] if calls else None))

# Обычный текст «ссылки» должен работать как раньше — не сломали ли
calls.clear(); press("ссылки")
ok &= say(calls == ["ссылки"], "текст «ссылки» по-прежнему уходит как есть")
calls.clear(); press("/links 20")
ok &= say(calls == ["/links 20"], "«/links 20» не задет")
tb._cmd_links = real_links

# --- 3. кнопка «Постинг»: состояние очереди и клавиатура обратно
out = press(tb.BTN_POST)
body = out[0]["text"] if out else ""
ok &= say(bool(out) and "reply_markup" in out[0], "кнопка Постинг отвечает с клавиатурой")
ok &= say("В очереди" in body and "ВИДЕО файлом" in body,
          "в ответе есть очередь и как публиковать")
ok &= say(body.count(chr(10)) >= 3, "ответ разбит на строки, а не слеплен")

# --- 4. в самой разметке ровно две кнопки, те самые
import json
kb = json.loads(tb.KEYBOARD)
rows = kb["keyboard"]
ok &= say(len(rows) == 1 and len(rows[0]) == 2, "кнопок ровно две, в один ряд")
ok &= say([b["text"] for b in rows[0]] == [tb.BTN_LINKS, tb.BTN_POST],
          "подписи: %s" % " | ".join(b["text"] for b in rows[0]))
ok &= say(kb.get("is_persistent") and kb.get("resize_keyboard"),
          "клавиатура закреплённая и по размеру экрана")

print(chr(10) + "ИТОГ:", "всё сходится" if ok else "ЕСТЬ ПРОВАЛЫ")
sys.exit(0 if ok else 1)
