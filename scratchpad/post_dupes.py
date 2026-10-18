"""Одно видео — одна публикация (живьём 2026-10-16: видео вышло в Instagram
дважды) и бот без журнала сессий.

Было: то же видео встало в очередь второй раз; публикуемая задача висела
«ждёт» с кнопкой «Убрать» — её удалили посреди поста, и копия вышла вторым
кругом. И бот после каждой сессии присылал весь её журнал.
Без телефона и без Telegram.
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402

LIVE = config.BASE
tmp = tempfile.mkdtemp(prefix="pa_dupes_")
for name in ("webui.html", "recipes.json", "ИНСТРУКЦИЯ.html"):
    shutil.copy(os.path.join(LIVE, name), os.path.join(tmp, name))
config.BASE = tmp
config.DB_PATH = os.path.join(tmp, "jobs.db")
config.PIN_FILE = os.path.join(tmp, "pin.txt")
for attr in ("INTERESTS", "WATCH_DIR", "QUEUE_DIR", "FRAMES_DIR", "LOGS_DIR", "RECIPES"):
    if hasattr(config, attr):
        setattr(config, attr, os.path.join(tmp, os.path.basename(getattr(config, attr))))
for d in (config.WATCH_DIR, config.QUEUE_DIR):
    os.makedirs(d, exist_ok=True)

import jobs       # noqa: E402
import poster     # noqa: E402
import yttags  # noqa: E402
yttags.suggest = lambda caption, timeout=40: ["тег", "ещё тег"]  # без модели
import prefs      # noqa: E402
import scheduler  # noqa: E402
import webui      # noqa: E402

prefs.PATH = os.path.join(tmp, "settings.json")
scheduler.looks_like_video = lambda path: ""
ok = True


def say(cond, text):
    global ok
    ok &= bool(cond)
    print(("  OK  " if cond else "  НЕТ ") + text)


from http.server import ThreadingHTTPServer  # noqa: E402

server = ThreadingHTTPServer(("127.0.0.1", 0), webui.Handler)
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()


def upload(name, data, targets="reels"):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/upload?name={urllib.parse.quote(name)}&targets={targets}",
        data=data, headers={"X-Token": webui.TOKEN})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode("utf-8"))


def count():
    with jobs.connect() as con:
        return con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]


VIDEO = os.urandom(3 << 20)          # 3 МБ «видео»
print("--- то же видео второй раз ---")
res = upload("Video 22222.mp4", VIDEO)
say(res.get("ok") and count() == 1, f"первая загрузка — задача: {res.get('message')}")
res = upload("Video 22222.mp4", VIDEO)
say(res.get("ok") is False and "второй раз не ставлю" in res.get("error", "") and count() == 1,
    f"повтор — не ставлю: {res.get('error')}")
res = upload("другое имя.mp4", VIDEO)
say(res.get("ok") is False and count() == 1, "то же видео под другим именем — тоже повтор")
res = upload("Video 22222.mp4", os.urandom(3 << 20))
say(res.get("ok") and count() == 2, "другое видео с тем же именем — встаёт")
names = sorted(os.listdir(config.QUEUE_DIR))
say(len(names) == 2 and "Video 22222 (2).mp4" in names, f"файлы не затёрли друг друга: {names}")

print("\n--- публикуется: не «ждёт», не удаляется, сторож не берёт ---")
first = jobs.connect().execute("SELECT * FROM jobs ORDER BY id LIMIT 1").fetchone()
seen = {}


def fake_post(path, text, target, extra=None):
    row = jobs.by_id(first["id"])
    seen["status"] = row["status"]
    seen["queue"] = webui._queue()
    seen["due"] = jobs.due()
    return True, f"{target}: ok"


poster.post = fake_post
scheduler._tell_failure = lambda *a: None
scheduler.run_post_job(job_id=first["id"])
say(seen["status"] == "running", f"во время поста статус «{seen['status']}»")
row = [q for q in seen["queue"] if q["id"] == first["id"]][0]
say(row.get("running") and row["when"] == "публикуется сейчас", "в окне — «публикуется сейчас»")
say(seen["due"] is None or seen["due"]["id"] != first["id"], "сторож эту задачу второй раз не берёт")
page = open(os.path.join(LIVE, "webui.html"), encoding="utf-8").read()
say("if(!it.running)" in page, "у публикуемой в окне нет кнопки «Убрать»")
say(jobs.by_id(first["id"])["status"] == "done", "после поста — «готово»")
res = upload("снова.mp4", VIDEO)
say(res.get("ok") is False and "уже опубликовано" in res.get("error", ""),
    f"то же видео в те же сети сразу после поста — повтор: {res.get('error')}")
res = upload("снова.mp4", VIDEO, targets="tiktok")
say(res.get("ok"), "то же видео в ДРУГУЮ сеть — можно")

print("\n--- обрыв посреди поста: не повторяем сами ---")
jid = jobs.add(os.path.join(tmp, "x.mp4"), "x", "reels")
jobs.set_status(jid, "running")
say(jobs.interrupt_running() == 1 and jobs.by_id(jid)["status"] == "failed",
    "«публикуется» после закрытия программы — в «сбой», а не в очередь заново")
say("проверь, вышел ли пост" in jobs.by_id(jid)["result"], "и сказано проверить, вышел ли пост")

print("\n--- бот без журнала сессий ---")
src = open(os.path.join(LIVE, "webui.py"), encoding="utf-8").read()
keeper = src[src.index("class Keeper"):src.index("class Dispatcher")]
say("notify=_tell" not in keeper.split("def _tick")[1] or "run_session" not in keeper.split("notify=_tell")[0][-300:],
    "сторож расписания не шлёт журнал сессии в бот")
say("a, sec, notify=None, keep_open=k" in src, "сессия по расписанию — notify=None")

server.shutdown()
shutil.rmtree(tmp, ignore_errors=True)
print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
