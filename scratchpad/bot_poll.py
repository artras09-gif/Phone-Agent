"""Приёмник Telegram-бота говорит, почему молчит, и не умирает — без сети.

Жалоба 2026-10-01: «бот в тг перестал работать». Сообщения лежали
непрочитанными, а программа молчала: при любой беде опрос тихо повторялся
раз в 5 с. Проверяем:
  * вторая копия программы на том же токене (409) — названа словами;
  * токен отвергнут (401) — названо;
  * связь вернулась — беда снята;
  * сбой разбора сообщения не убивает опрос;
  * окно показывает причину во вкладке «Бот».
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import telegram_bot as tb  # noqa: E402

ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


replies = []
tb.load_settings = lambda: {"token": "123:x", "owner": "777"}
tb.send = lambda text, **kw: replies.append(text) or True

print("--- почему молчит ---")
say(tb._why_failed({"ok": False, "error": "HTTP Error 409: Conflict"}) == tb.CONFLICT,
    "409 — «другая копия программы»")
say(tb._why_failed({"ok": False, "error_code": 409, "description": "Conflict: terminated by "
                    "other getUpdates request"}) == tb.CONFLICT, "409 с описанием Telegram — то же")
say(tb._why_failed({"ok": False, "error": "HTTP Error 401: Unauthorized"}) == tb.REJECTED,
    "401 — «токен не принят»")
say("нет связи" in tb._why_failed({"ok": False, "error": "urlopen error timed out"}),
    "обрыв — «нет связи»")

print("\n--- один круг опроса ---")
answers = []
tb._call = lambda method, params=None, timeout=70: answers.pop(0)
answers[:] = [{"ok": False, "error": "HTTP Error 409: Conflict"}]
try:
    tb._poll_once(0)
    say(False, "409 не замечен")
except tb._PollFailed as e:
    say(str(e) == tb.CONFLICT, "409 поднимает понятную ошибку")

handled = []


def handle(msg):
    handled.append(msg["text"])
    if msg["text"] == "плохое":
        raise ValueError("не разобрал")


tb._handle = handle
tb._TROUBLE["text"] = tb.CONFLICT
answers[:] = [{"ok": True, "result": [
    {"update_id": 10, "message": {"text": "Привет", "chat": {"id": 777}}},
    {"update_id": 11, "message": {"text": "плохое", "chat": {"id": 777}}},
    {"update_id": 12, "message": {"text": "третье", "chat": {"id": 777}}}]}]
offset = tb._poll_once(0)
say(offset == 13, f"offset сдвинут за последнее сообщение: {offset}")
say(handled == ["Привет", "плохое", "третье"], "сбой на втором не помешал третьему")
say(replies and "ошибка обработки" in replies[-1], "о сбое сказано в чат")
say(tb.trouble() == "", "связь вернулась — беда снята")

print("\n--- 409 в первую минуту — не тревога (наш же прежний запрос) ---")
real_time, real_sleep = tb.time.time, tb.time.sleep
saved = (tb._poll_once, tb.check, tb._drop_stale_drafts, tb.setup_server, tb.load_settings)
clock = [1000.0]
tb.time.time = lambda: clock[0]
tb.time.sleep = lambda s: clock.__setitem__(0, clock[0] + s)
tb.check = lambda: (True, "@test")
tb._drop_stale_drafts = lambda: None
tb.setup_server = lambda: ""
tb.load_settings = lambda: {"token": "1:x", "owner": "1"}
seen = []


def run(polls):
    """Прогнать опрос по списку ответов: что было в «беде» после каждого."""
    seen.clear()
    tb._TROUBLE["text"] = ""
    queue = list(polls)

    def one(offset, generation=None):
        if not queue:
            tb._GENERATION["n"] += 1         # конец сценария — поток выходит
            raise tb._PollFailed("стоп")
        kind = queue.pop(0)
        if kind == "409":
            out = tb._PollFailed(tb.CONFLICT)
            seen.append(None)
            raise out
        seen.append(None)
        return offset

    tb._poll_once = one
    gen = tb._GENERATION["n"]
    tb.poll_forever(gen)
    return tb._TROUBLE["text"]


left = run(["409", "409", "ok"])
say(left == "", "два 409 подряд (10 с), потом связь — тревоги не было")
tb._GENERATION["n"] += 0
left = run(["409"] * 20)
say(left == tb.CONFLICT, "409 дольше минуты — сказано «другая копия»")
tb.time.time, tb.time.sleep = real_time, real_sleep
tb._poll_once, tb.check, tb._drop_stale_drafts, tb.setup_server, tb.load_settings = saved
tb._TROUBLE["text"] = ""

print("\n--- смена токена: с какого сообщения продолжать ---")
tb._OFFSET["7000000001"] = 50
say(tb._offset_for("7000000001:" + "B" * 35) == 50,
    "перевыпущенный токен того же бота — с того же места (без повторных ответов)")
say(tb._offset_for("7000000002:" + "C" * 35) == 0, "другой бот — с нуля")
tb._OFFSET.clear()

print("\n--- поток не умирает ---")
import threading  # noqa: E402
import time  # noqa: E402

calls = {"n": 0}


def flaky(method, params=None, timeout=70):
    calls["n"] += 1
    if method == "getMe":
        return {"ok": True, "result": {"username": "testbot"}}
    if calls["n"] == 2:
        raise RuntimeError("что-то совсем неожиданное")
    time.sleep(0.05)
    return {"ok": True, "result": []}


tb._call = flaky
tb.setup_server = lambda: ""                # поиск сервера Bot API здесь не проверяем
tb.RETRY_SEC = 0.01
t = threading.Thread(target=tb.poll_forever, args=(tb._GENERATION["n"],), daemon=True)
t.start()
time.sleep(0.5)
say(t.is_alive() and calls["n"] > 3, f"неожиданное исключение пережито, опрос идёт (кругов: {calls['n']})")

print("\n--- окно показывает причину ---")
src = open(os.path.join(os.path.dirname(HERE), "webui.html"), encoding="utf-8").read()
say("b.trouble ? b.trouble" in src, "вкладка «Бот» пишет причину словами")
import webui  # noqa: E402

tb._TROUBLE["text"] = tb.CONFLICT
say(webui._bot_state()["trouble"] == tb.CONFLICT, "состояние окна отдаёт причину")
tb._TROUBLE["text"] = ""

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
