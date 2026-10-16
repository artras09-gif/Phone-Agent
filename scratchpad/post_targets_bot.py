"""Пост из окна уходит туда, что отмечено; бот подхватывает новый токен.

Жалобы 2026-10-14:
  * «постить с компа стал — он только в TikTok постил, неважно какие соцсети
    я выберу»: загрузка перетаскиванием вообще не передавала выбор, а
    `scan_watch_dir` ставил «tiktok» намертво; выбрать можно было одну сеть;
  * «вставил новый токен — он на него не отреагировал»: опрос продолжал со
    старым номером сообщения (`offset`), а у каждого бота нумерация своя, и
    новый бот отвечал «ничего нового». Плюс повторный захват своего же замка
    кончался ложным «бота ведёт служба телефона».

Без сети и без телефона: сервер окна — на временной папке, Telegram — заглушка.
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402

LIVE_BASE = config.BASE
ok = True


def say(cond, text):
    global ok
    ok &= bool(cond)
    print(("  OK  " if cond else "  НЕТ ") + text)


tmp = tempfile.mkdtemp(prefix="post_targets_")
for name in ("webui.html", "recipes.json", "ИНСТРУКЦИЯ.html"):
    shutil.copy(os.path.join(LIVE_BASE, name), os.path.join(tmp, name))
config.BASE = tmp
config.DB_PATH = os.path.join(tmp, "jobs.db")
config.PIN_FILE = os.path.join(tmp, "pin.txt")
for attr in ("INTERESTS", "WATCH_DIR", "QUEUE_DIR", "FRAMES_DIR", "LOGS_DIR", "RECIPES"):
    if hasattr(config, attr):
        setattr(config, attr, os.path.join(tmp, os.path.basename(getattr(config, attr))))
for d in (config.WATCH_DIR, config.QUEUE_DIR):
    os.makedirs(d, exist_ok=True)

import jobs       # noqa: E402
import prefs      # noqa: E402
import scheduler  # noqa: E402
import telegram_bot  # noqa: E402
import webui      # noqa: E402

prefs.PATH = os.path.join(tmp, "settings.json")
# Настоящей проверки на «видео ли это» здесь не нужно — файл-заглушка.
scheduler.looks_like_video = lambda path: ""

print("--- соцсети из окна ---")
say(scheduler.normalize_targets("reels, TikTok,xx") == "tiktok,reels",
    "список чистится и упорядочивается, мусор отброшен")
say(scheduler.normalize_targets("") == "", "пусто — пусто (задача не создаётся)")

from http.server import ThreadingHTTPServer  # noqa: E402

server = ThreadingHTTPServer(("127.0.0.1", 0), webui.Handler)
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()


def post(path, data, ctype="application/json"):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data,
                                 headers={"X-Token": webui.TOKEN, "Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode("utf-8"))


def last_job():
    with jobs.connect() as con:
        return con.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 1").fetchone()


res = post("/api/upload?name=clip1.mp4&targets=" + urllib.parse.quote("tiktok,reels,shorts"),
           os.urandom(2048), "application/octet-stream")
row = last_job()
say(res.get("ok") and row and set(row["targets"].split(",")) == {"tiktok", "reels", "shorts"},
    f"перетащенное видео — во все три отмеченные: {row['targets'] if row else res}")

res = post("/api/upload?name=clip2.mp4&targets=reels", os.urandom(2048), "application/octet-stream")
row = last_job()
say(row and row["targets"] == "reels", f"только Reels — только Reels: {row['targets'] if row else res}")

before = last_job()["id"]
res = post("/api/upload?name=clip3.mp4&targets=", os.urandom(2048), "application/octet-stream")
say(res.get("ok") is False and last_job()["id"] == before,
    f"ничего не отмечено — файл не принят и задачи нет: {res.get('error')}")

video = os.path.join(tmp, "ready.mp4")
open(video, "wb").write(os.urandom(2048))
res = post("/api/job-add", json.dumps({"path": video, "caption": "x",
                                       "targets": "shorts,tiktok"}).encode())
row = last_job()
say(res.get("ok") and row["targets"] == "tiktok,shorts",
    f"файл с диска — в отмеченные: {row['targets']}")
res = post("/api/job-add", json.dumps({"path": video, "target": "reels"}).encode())
say(res.get("ok") is False and "второй раз не ставлю" in res.get("error", ""),
    "тот же файл, пока прежняя задача ждёт, — повтор, не ставится")
video2 = os.path.join(tmp, "ready2.mp4")
open(video2, "wb").write(os.urandom(2048))
res = post("/api/job-add", json.dumps({"path": video2, "target": "reels"}).encode())
say(res.get("ok") and last_job()["targets"] == "reels", "старое поле target тоже понимается")

page = open(os.path.join(LIVE_BASE, "webui.html"), encoding="utf-8").read()
say('id="q-target"' not in page and "q-targets" in page and "&targets=" in page,
    "в окне галочки вместо одной строки, и загрузка передаёт выбор")

print("\n--- бот: новый токен ---")
calls = []
OLD, NEW = "1111111:" + "A" * 35, "2222222:" + "B" * 35
updates = {OLD: [{"update_id": 900, "message": {"text": "старый", "chat": {"id": 1}}}],
           NEW: [{"update_id": 5, "message": {"text": "новый", "chat": {"id": 1}}}]}
handled = []


def fake_call(method, params=None, timeout=70):
    token = telegram_bot.load_settings()["token"]
    calls.append((token, method, dict(params or {})))
    if method == "getUpdates":
        time.sleep(0.05)
        off = (params or {}).get("offset", 0)
        return {"ok": True, "result": [u for u in updates.get(token, [])
                                        if u["update_id"] >= off]}
    return {"ok": True, "result": {"username": "bot_" + token.lower()}}


telegram_bot._call = fake_call
telegram_bot.setup_server = lambda: ""      # поиск сервера Bot API здесь не проверяем
telegram_bot.check = lambda: (True, "@bot_" + telegram_bot.load_settings()["token"].lower())
telegram_bot._handle = lambda msg: handled.append(msg["text"])
telegram_bot.send = lambda *a, **k: None
telegram_bot.RETRY_SEC = 0.05
telegram_bot.save_settings(OLD, "1")
webui._start_bot()
time.sleep(0.4)
say("старый" in handled, "старый бот принимает сообщения")
say(not webui._BOT_ELSEWHERE["yes"], "замок бота взят этим окном")

res = post("/api/telegram", json.dumps({"token": " \"bot" + NEW + "\" "}).encode())
deadline = time.time() + 3
while "новый" not in handled and time.time() < deadline:
    time.sleep(0.05)
say("новый" in handled, f"новый токен подхвачен сразу, сообщение принято: {handled}")
say(not webui._BOT_ELSEWHERE["yes"],
    "повторное сохранение не пишет «бота ведёт служба телефона»")
new_offsets = [c[2].get("offset", 0) for c in calls if c[0] == NEW and c[1] == "getUpdates"]
say(new_offsets and new_offsets[0] == 0,
    f"новый бот опрашивается с нуля, а не со старого номера (первый offset {new_offsets[:1]})")
time.sleep(0.3)
say(handled.count("старый") == 1, "старый поток больше не принимает сообщений")
say(telegram_bot.running(), "бот работает")

print("\n--- токен, вставленный с мусором (2026-10-14: «HTTP Error 404») ---")
say(telegram_bot.load_settings()["token"] == NEW,
    "вставили « \"bot<токен>\" » — сохранён чистый токен")
say(telegram_bot.clean_token(NEW[:12] + "​" + NEW[12:]) == NEW, "невидимый символ внутри убран")
res = post("/api/telegram", json.dumps({"token": "привет, вот токен"}).encode())
say(res.get("ok") is False and "не похоже на токен" in res.get("error", "")
    and telegram_bot.load_settings()["token"] == NEW,
    "не токен вовсе — понятная ошибка, рабочий токен не затёрт")
why = telegram_bot._why_failed({"ok": False, "error": "HTTP Error 404: Not Found"})
say(why == telegram_bot.MALFORMED and "нет связи" not in why,
    "404 — «токен вставлен с ошибкой», а не «нет связи»")

server.shutdown()
shutil.rmtree(tmp, ignore_errors=True)
print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
