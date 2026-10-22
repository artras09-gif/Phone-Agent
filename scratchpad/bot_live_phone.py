"""Бот целиком против поддельного Telegram, но с НАСТОЯЩИМ телефоном (2026-10-22:
«возьми воспоминания о боте и прям оттестируй постинг и сбор статы на нём»).

Как bot_fake_telegram.py — настоящий HTTP, очередь действий и сторож те же, что
в окне, — только poster.post и accstats.collect_all не подменены: работают с
воткнутым телефоном. Публикация — СУХИМ ПРОГОНОМ (config.DRY_RUN в памяти):
маршрут доходит до активной последней кнопки и её не жмёт. Статистика —
по-настоящему: альбом, который бот отправил бы человеку, сохраняется на диск.

    python scratchpad\\bot_live_phone.py <видео.mp4> [подпись-файлом.txt] [--no-post] [--no-stats]

Место этому тесту — на столе с кабелем, в all_tests.py его нет.
"""
import json
import os
import re
import sys
import tempfile
import threading
import time
import http.server
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
args = [a for a in sys.argv[1:] if not a.startswith("--")]
VIDEO = args[0]
CAPTION = open(args[1], encoding="utf-8").read().strip() if len(args) > 1 else "Проверка бота"
DO_POST, DO_STATS = "--no-post" not in sys.argv, "--no-stats" not in sys.argv

import config     # noqa: E402

TMP = tempfile.mkdtemp(prefix="pa_live_bot_")
OUT = os.path.join(TMP, "album")
config.BASE = TMP
config.DB_PATH = os.path.join(TMP, "jobs.db")
config.QUEUE_DIR = os.path.join(TMP, "queue")
config.WATCH_DIR = os.path.join(TMP, "watch")
config.DRY_RUN = True                     # без последней кнопки: ролик уже опубликован
config.MAX_POSTS_PER_DAY = 100
for d in (config.QUEUE_DIR, config.WATCH_DIR, OUT):
    os.makedirs(d, exist_ok=True)

import jobs           # noqa: E402
import telegram_bot as bot  # noqa: E402
import tgserver       # noqa: E402
import webui          # noqa: E402

TOKEN = "7000000001:" + "L" * 35
OWNER = 111
LOG = []


def note(text):
    line = f"[{time.strftime('%H:%M:%S')}] {text}"
    LOG.append(line)
    print(line, flush=True)


class Fake:
    def __init__(self):
        self.lock = threading.Condition()
        self.updates, self.sent, self.files = [], [], {}
        self.next_update, self.next_msg = 1, 1000

    def push(self, update):
        with self.lock:
            update["update_id"] = self.next_update
            self.next_update += 1
            self.updates.append(update)
            self.lock.notify_all()

    def handle(self, method, params):
        if method == "getMe":
            return {"ok": True, "result": {"id": 7000000001, "is_bot": True,
                                           "username": "Videopost23bot"}}
        if method == "getUpdates":
            offset = int(params.get("offset") or 0)
            deadline = time.time() + 0.5
            with self.lock:
                self.updates = [u for u in self.updates if u["update_id"] >= offset]
                while not self.updates and time.time() < deadline:
                    self.lock.wait(deadline - time.time())
                return {"ok": True, "result": list(self.updates)}
        if method == "getFile":
            fid = params["file_id"]
            return {"ok": True, "result": {"file_id": fid, "file_path": f"videos/{fid}.mp4",
                                           "file_size": len(self.files[fid])}}
        with self.lock:
            self.next_msg += 1
            entry = {k: v for k, v in params.items() if not isinstance(v, bytes)}
            entry["_id"] = self.next_msg
            self.sent.append((method, entry))
        if method == "sendMediaGroup":
            media = json.loads(params["media"])
            for i, item in enumerate(media):
                data = params.get(f"p{i}")
                path = os.path.join(OUT, f"{i:02d}.png")
                with open(path, "wb") as f:
                    f.write(data if isinstance(data, bytes) else b"")
                note(f"АЛЬБОМ {i + 1}/{len(media)}: {item.get('caption')} -> {path}")
        elif method in ("sendMessage", "editMessageText", "sendPhoto"):
            note(f"БОТ ({method}): {str(params.get('text') or params.get('caption') or '')[:700]}")
        return {"ok": True, "result": {"message_id": self.next_msg, "chat": {"id": OWNER}}}


FAKE = Fake()


def parse_body(handler):
    size = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(size) if size else b""
    kind = handler.headers.get("Content-Type", "")
    if kind.startswith("multipart/form-data"):
        boundary = kind.split("boundary=")[1].encode()
        out = {}
        for part in raw.split(b"--" + boundary):
            if b"\r\n\r\n" not in part:
                continue
            head, body = part.split(b"\r\n\r\n", 1)
            name = re.search(rb'name="([^"]+)"', head)
            if name:
                body = body[:-2] if body.endswith(b"\r\n") else body
                out[name.group(1).decode()] = body if b"filename=" in head else body.decode()
        return out
    return {k: v[0] for k, v in urllib.parse.parse_qs(raw.decode()).items()}


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _reply(self, payload, ctype="application/json"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        m = re.match(r"/bot([^/]*)/(\w+)$", self.path)
        self._reply(FAKE.handle(m.group(2), parse_body(self)))

    def do_GET(self):
        fid = os.path.splitext(os.path.basename(self.path))[0]
        self._reply(FAKE.files.get(fid, b""), "video/mp4")


SERVER = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
SERVER.daemon_threads = True
threading.Thread(target=SERVER.serve_forever, daemon=True).start()
bot.API = f"http://127.0.0.1:{SERVER.server_address[1]}"
tgserver.url = lambda: ""
bot.set_submit(webui.RUNNER.start)
webui.DISPATCHER.start()
bot.save_settings(TOKEN, "")
bot.start_background()

_n = [1]


def say_to(text=None, media=None):
    _n[0] += 1
    msg = {"message_id": _n[0], "date": int(time.time()),
           "chat": {"id": OWNER, "type": "private"}, "from": {"id": OWNER, "is_bot": False}}
    if media:
        msg["video"] = media
        msg["caption"] = text or ""
    else:
        msg["text"] = text
        if text.startswith("/"):
            msg["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
    FAKE.push({"message": msg})


def press(entry, data):
    FAKE.push({"callback_query": {"id": f"cb{time.time()}", "from": {"id": OWNER},
                                  "message": {"message_id": entry["_id"],
                                              "chat": {"id": OWNER, "type": "private"}},
                                  "data": data}})


def wait_for(method, contains="", since=0, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        with FAKE.lock:
            for m, p in FAKE.sent[since:]:
                if m == method and contains in str(p.get("text") or p.get("caption") or p.get("media") or ""):
                    return p
        time.sleep(0.3)
    return None


def mark():
    with FAKE.lock:
        return len(FAKE.sent)


time.sleep(1.5)
say_to("/start")
wait_for("sendMessage", "Работаю", timeout=20)

if DO_POST:
    note("=== ПУБЛИКАЦИЯ через бота (сухой прогон) ===")
    with open(VIDEO, "rb") as f:
        FAKE.files["V1"] = f.read()
    m = mark()
    say_to(CAPTION, media={"file_id": "V1", "file_unique_id": "UV1", "file_size": len(FAKE.files["V1"]),
                           "mime_type": "video/mp4", "duration": 17, "width": 1080, "height": 1920})
    draft = wait_for("sendMessage", "получил", m, timeout=120)
    if draft is None:
        note("черновика нет — бот видео не принял")
    else:
        job = jobs.connect().execute("SELECT * FROM jobs ORDER BY id DESC").fetchone()
        note(f"черновик: задача #{job['id']}, сети {job['targets']}, кнопки: "
             + ", ".join(b["callback_data"] for row in json.loads(draft["reply_markup"])["inline_keyboard"] for b in row))
        m = mark()
        press(draft, f"w:{job['id']}:now")
        end = time.time() + 30 * 60
        while time.time() < end and jobs.by_id(job["id"])["status"] not in ("done", "failed"):
            time.sleep(2)
        note(f"задача #{job['id']}: {jobs.by_id(job['id'])['status']}")
        time.sleep(3)

if DO_STATS:
    note("=== СТАТИСТИКА через бота: /stats → «За всё время» ===")
    m = mark()
    say_to("/stats")
    ask = wait_for("sendMessage", "за какой период", m, timeout=30)
    if ask is None:
        note("бот не спросил период")
    else:
        m = mark()
        press(ask, "s:-1")
        album = wait_for("sendMediaGroup", "", m, timeout=25 * 60) or wait_for("sendPhoto", "", m, timeout=5)
        note("альбом пришёл" if album else "альбома нет")

time.sleep(2)
bot.stop_background() if hasattr(bot, "stop_background") else None
with open(os.path.join(TMP, "log.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(LOG))
note(f"всё в {TMP}")
os._exit(0)
