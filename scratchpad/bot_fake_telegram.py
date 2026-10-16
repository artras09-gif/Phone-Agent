"""Бот целиком — против поддельного Telegram на этом ПК (просьба 2026-10-16:
«протести ещё работу бота, чтобы с ним всё было идеально»).

Не подмена функций, а настоящий HTTP: бот сам опрашивает getUpdates, качает
файлы по /file/bot…, жмёт кнопки, шлёт снимок multipart'ом, переживает 409,
401, webhook, 429, обрыв связи и смену токена. Публикация идёт через ту же
очередь действий, что в окне (webui.RUNNER + сторож очереди), только сам
телефон подменён: poster.post — заглушка. Живой токен и второй ПК не трогаются.
"""
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import urllib.parse
import http.server

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config     # noqa: E402

TMP = tempfile.mkdtemp(prefix="pa_fake_tg_")
config.BASE = TMP
config.DB_PATH = os.path.join(TMP, "jobs.db")
config.QUEUE_DIR = os.path.join(TMP, "queue")
config.WATCH_DIR = os.path.join(TMP, "watch")
config.MAX_POSTS_PER_DAY = 100
for d in (config.QUEUE_DIR, config.WATCH_DIR):
    os.makedirs(d, exist_ok=True)

import abort          # noqa: E402
import jobs           # noqa: E402
import poster         # noqa: E402
import scheduler      # noqa: E402
import telegram_bot as bot  # noqa: E402
import tgserver       # noqa: E402
import webui          # noqa: E402

jobs._SCHEMA_READY = False
ok = True
FAILS = []


def say(good, text):
    global ok
    ok &= bool(good)
    if not good:
        FAILS.append(text)
    print(f"  {'OK ' if good else 'НЕТ'} {text}", flush=True)


TOKEN = "7000000001:" + "A" * 35
TOKEN2 = "7000000001:" + "B" * 35      # тот же бот, токен перевыпущен в @BotFather
OWNER, FRIEND, STRANGER = 111, 222, 333
GROUP, ALIEN_GROUP = -1001, -1002
BOTNAME = "Videopost23bot"


# ------------------------------------------------------------ поддельный Telegram

class Fake:
    def __init__(self):
        self.token = TOKEN
        self.lock = threading.Condition()
        self.updates = []
        self.next_update = 1
        self.next_msg = 1000
        self.sent = []                  # (метод, параметры) — всё, что прислал бот
        self.files = {}                 # file_id -> bytes
        self.mode = set()               # conflict, webhook, down, privacy_off
        self.rate_limit = 0             # сколько ближайших sendMessage отбить 429
        self.slow_file = 0.0            # задержка отдачи файла
        self.local_dir = ""             # свой сервер: getFile отдаёт путь на диске
        self.allowed = None
        self.logged_out = 0

    # --- со стороны «людей» ---
    def push(self, update):
        with self.lock:
            update["update_id"] = self.next_update
            self.next_update += 1
            self.updates.append(update)
            self.lock.notify_all()

    def calls(self, method=None, since=0):
        with self.lock:
            return [(m, p) for m, p in self.sent[since:] if method is None or m == method]

    def mark(self):
        with self.lock:
            return len(self.sent)

    # --- со стороны бота ---
    def handle(self, method, params, token):
        if "down" in self.mode:
            return None                                   # обрыв: соединение рвём
        if not re.fullmatch(r"\d+:[A-Za-z0-9_-]{30,50}", token):
            return 404, {"ok": False, "error_code": 404, "description": "Not Found"}
        if token != self.token:
            return 401, {"ok": False, "error_code": 401, "description": "Unauthorized"}
        if method == "getMe":
            return 200, {"ok": True, "result": {
                "id": 7000000001, "is_bot": True, "username": BOTNAME,
                "can_read_all_group_messages": "privacy_off" in self.mode}}
        if method == "getUpdates":
            if "webhook" in self.mode:
                return 409, {"ok": False, "error_code": 409, "description":
                             "Conflict: can't use getUpdates method while webhook is active; "
                             "use deleteWebhook to delete the webhook first"}
            if "conflict" in self.mode:
                return 409, {"ok": False, "error_code": 409, "description":
                             "Conflict: terminated by other getUpdates request; make sure "
                             "that only one bot instance is running"}
            self.allowed = params.get("allowed_updates")
            offset = int(params.get("offset") or 0)
            deadline = time.time() + min(float(params.get("timeout") or 0), 0.4)
            with self.lock:
                self.updates = [u for u in self.updates if u["update_id"] >= offset]
                while not self.updates and time.time() < deadline:
                    self.lock.wait(deadline - time.time())
                return 200, {"ok": True, "result": list(self.updates)}
        if method in ("sendMessage", "sendPhoto") and self.rate_limit > 0:
            self.rate_limit -= 1
            return 429, {"ok": False, "error_code": 429, "description":
                         "Too Many Requests: retry after 1", "parameters": {"retry_after": 1}}
        with self.lock:
            entry = dict(params)
            if method in ("sendMessage", "sendPhoto"):
                self.next_msg += 1
                entry["_id"] = self.next_msg
            self.sent.append((method, entry))
        if method in ("sendMessage", "sendPhoto"):
            return 200, {"ok": True, "result": {"message_id": entry["_id"],
                                                "chat": {"id": int(params["chat_id"])}}}
        if method == "getFile":
            fid = params["file_id"]
            if fid not in self.files:
                return 400, {"ok": False, "error_code": 400,
                             "description": "Bad Request: invalid file_id"}
            name = f"videos/{fid}.mp4"
            if self.local_dir:
                time.sleep(self.slow_file)            # свой сервер сам качает у Telegram
                path = os.path.join(self.local_dir, fid + ".mp4")
                with open(path, "wb") as f:
                    f.write(self.files[fid])
                name = path
            return 200, {"ok": True, "result": {"file_id": fid, "file_path": name,
                                                "file_size": len(self.files[fid])}}
        if method == "logOut":
            self.logged_out += 1
        return 200, {"ok": True, "result": True}

    def file(self, token, path):
        if token != self.token:
            return None
        fid = os.path.splitext(os.path.basename(path))[0]
        if self.slow_file:
            time.sleep(self.slow_file)
        return self.files.get(fid)


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

    def _reply(self, code, payload, ctype="application/json"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        m = re.match(r"/bot([^/]*)/(\w+)$", self.path)
        params = parse_body(self)
        if not m:
            return self._reply(404, {"ok": False, "error_code": 404, "description": "Not Found"})
        res = FAKE.handle(m.group(2), params, m.group(1))
        if res is None:
            self.close_connection = True
            self.connection.close()
            return
        self._reply(*res)

    def do_GET(self):
        m = re.match(r"/file/bot([^/]*)/(.+)$", self.path)
        data = FAKE.file(m.group(1), m.group(2)) if m else None
        if data is None:
            return self._reply(404, {"ok": False, "error_code": 404, "description": "Not Found"})
        self._reply(200, data, "video/mp4")


SERVER = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
SERVER.daemon_threads = True
threading.Thread(target=SERVER.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{SERVER.server_address[1]}"


# ------------------------------------------------------------ телефон — заглушка

POSTED = []
SCREEN_PNG = b"\x89PNG\r\n\x1a\n" + b"screen" * 50


def fake_post(path, text, target):
    POSTED.append((os.path.basename(path), text, target))
    time.sleep(0.05)
    if "СЛОМАЙ" in text:
        poster.LAST_FAIL["dir"] = TMP
        return False, f"=== {target} ===\nОШИБКА: кнопка «Опубликовать» не нашлась"
    return True, f"{target}: опубликовано"


poster.post = fake_post
poster.failure_files = lambda d: (SCREEN_PNG, "кнопки: Далее, Отмена")

bot.API = URL
tgserver.url = lambda: ""
bot.RETRY_SEC = 0.2
bot.CONFLICT_GRACE_SEC = 1.0
webui.Dispatcher.EVERY = 0.3
bot.set_submit(webui.RUNNER.start)
webui.DISPATCHER.start()


# ------------------------------------------------------------ помощники

CHAT_TYPE = {OWNER: "private", FRIEND: "private", STRANGER: "private",
             GROUP: "supergroup", ALIEN_GROUP: "supergroup"}
_msg_id = [1]
_file_n = [0]


def mp4(size=80 * 1024, seed=None):
    return b"\x00\x00\x00\x18ftypmp42" + (os.urandom(size) if seed is None
                                          else (seed * (size // len(seed) + 1))[:size])


def video(data=None, size_field=None, name=None, mime="video/mp4", kind="video"):
    _file_n[0] += 1
    fid = f"F{_file_n[0]}"
    FAKE.files[fid] = data if data is not None else mp4()
    obj = {"file_id": fid, "file_unique_id": "U" + fid,
           "file_size": size_field if size_field is not None else len(FAKE.files[fid]),
           "mime_type": mime}
    if name:
        obj["file_name"] = name
    if kind == "video":
        obj.update(duration=12, width=720, height=1280)
    return kind, obj


def say_to(chat, sender, text=None, media=None, thread=None, reply_to=None, topic_name=None,
           **extra):
    _msg_id[0] += 1
    msg = {"message_id": _msg_id[0], "date": int(time.time()),
           "chat": {"id": chat, "type": CHAT_TYPE[chat],
                    **({"title": "Команда", "is_forum": True} if chat < 0 else {})},
           "from": {"id": sender, "is_bot": False, "first_name": "U"}, **extra}
    if media:
        msg[media[0]] = media[1]
        if text is not None:
            msg["caption"] = text
    elif text is not None:
        msg["text"] = text
        if text.startswith("/"):
            msg["entities"] = [{"type": "bot_command", "offset": 0,
                                "length": len(text.split()[0])}]
    if thread:
        msg.update(is_topic_message=True, message_thread_id=thread)
    if topic_name:
        msg["reply_to_message"] = {"message_id": thread, "forum_topic_created": {"name": topic_name}}
    if reply_to:
        msg["reply_to_message"] = {"message_id": reply_to, "from": {"id": 7000000001,
                                                                     "is_bot": True,
                                                                     "username": BOTNAME}}
    FAKE.push({"message": msg})


def press(sent_entry, data, sender, chat=None):
    chat = chat if chat is not None else int(sent_entry["chat_id"])
    message = {"message_id": sent_entry["_id"], "chat": {"id": chat, "type": CHAT_TYPE[chat]}}
    if sent_entry.get("message_thread_id"):
        message.update(is_topic_message=True,
                       message_thread_id=int(sent_entry["message_thread_id"]))
    FAKE.push({"callback_query": {"id": f"cb{time.time()}", "from": {"id": sender},
                                  "message": message, "data": data}})


def wait(pred, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        got = pred()
        if got:
            return got
        time.sleep(0.05)
    return pred()


def texts(chat=None, since=0, method="sendMessage"):
    return [p for m, p in FAKE.calls(method, since)
            if chat is None or str(p.get("chat_id")) == str(chat)]


def last_text(chat, since=0, contains="", method="sendMessage"):
    def find():
        for p in reversed(texts(chat, since, method)):
            if contains in str(p.get("text") or p.get("caption") or ""):
                return p
        return None
    return wait(find)


def draft_of(chat, since):
    return last_text(chat, since, "получил")


def job_by_caption(part):
    rows = jobs.connect().execute("SELECT * FROM jobs ORDER BY id DESC").fetchall()
    return next((r for r in rows if part in (r["caption"] or "")), None)


def idle(timeout=10.0):
    """Очередь действий свободна и сторож всё разобрал."""
    return wait(lambda: not webui.RUNNER.busy() and not jobs.running(), timeout)


# ============================================================ сценарии

bot.save_settings(TOKEN, "")
print("--- запуск ---")
bot.start_background()
say(wait(lambda: FAKE.allowed is not None), "бот опрашивает getUpdates")
allowed = json.loads(FAKE.allowed or "[]")
say({"message", "callback_query", "my_chat_member"} <= set(allowed),
    f"просит у Telegram сообщения, кнопки и вступления в чаты: {allowed}")

print("\n--- личка: команды ---")
m = FAKE.mark()
say_to(OWNER, OWNER, "/start")
hello = last_text(OWNER, m, "Работаю")
say(hello and "reply_markup" in hello, "/start — справка и клавиатура внизу")
say(bot.load_settings()["owner"] == str(OWNER), "первый написавший — владелец")
say_to(OWNER, OWNER, "/help")
say(last_text(OWNER, m + 1, "Работаю"), "/help — та же справка")
say_to(OWNER, OWNER, "/status")
say(last_text(OWNER, m, "В очереди"), "/status")
say_to(OWNER, OWNER, "привет")
say(last_text(OWNER, m, "Пришли видео"), "непонятный текст — подсказка")
say_to(OWNER, OWNER, bot.BTN_LINKS)
say(last_text(OWNER, m, "Ссылок пока нет"), "кнопка «Ссылки» — пусто")
say_to(OWNER, OWNER, bot.BTN_POST)
say(last_text(OWNER, m, "В очереди пусто"), "кнопка «Постинг» — очередь")

print("\n--- ссылки ---")
m = FAKE.mark()
say_to(OWNER, OWNER, "смешное https://www.tiktok.com/@a/video/7500000000000000001")
say(last_text(OWNER, m, "Сохранил 1"), "ссылка сохранена")
say_to(OWNER, OWNER, "https://www.tiktok.com/@a/video/7500000000000000001")
say(last_text(OWNER, m, "Уже было: 1"), "повтор — «уже было»")
m = FAKE.mark()
say_to(OWNER, OWNER, "/links")
digest = last_text(OWNER, m, "Понравилось")
say(digest and "7500000000000000001" in digest["text"], "/links — подборка пришла")

print("\n--- видео → черновик → кнопки → публикация ---")
m = FAKE.mark()
caption = "🇺🇸 CERO PREOCUPACIONES\nTexto del post #fyp #viral"
say_to(OWNER, OWNER, caption, media=video())
draft = draft_of(OWNER, m)
say(draft and "inline_keyboard" in draft.get("reply_markup", ""), "черновик с кнопками")
say(draft and "Название на YouTube: 🇺🇸 CERO PREOCUPACIONES" in draft["text"],
    "первая строка — название YouTube")
job = job_by_caption("CERO")
say(job and job["status"] == "draft" and os.path.isfile(job["video_path"]),
    "задача-черновик, видео скачано в очередь")
say(job and job["caption"] == caption, "описание — слово в слово, с эмодзи и переносом")
m2 = FAKE.mark()
press(draft, f"t:{job['id']}:shorts", OWNER)
edited = wait(lambda: [p for _, p in FAKE.calls("editMessageText", m2)])
say(edited and "Не публикую в" in edited[-1]["text"], "сняли YouTube — сообщение поправилось")
say(wait(lambda: FAKE.calls("answerCallbackQuery", m2)), "кнопка отвечена (не крутится)")
m2 = FAKE.mark()
press(draft, f"w:{job['id']}:now", OWNER)
report = last_text(OWNER, m2, "опубликовано")
say(report, "«Сейчас» — отчёт о публикации пришёл в чат")
say(wait(lambda: jobs.by_id(job["id"])["status"] == "done"), "задача — опубликована")
nets = [t for name, _, t in POSTED if job["video_path"].endswith(name)]
say(nets == ["tiktok", "reels"], f"опубликовано в TikTok и Reels, без YouTube: {nets}")
say([p for _, p in FAKE.calls("editMessageText", m2) if "✅ Пост" in p["text"]],
    "черновик превратился в «✅ Пост … — сейчас»")
idle()

print("\n--- подпись, похожая на команду, — всё равно описание ---")
for cap in ("Ссылки на рецепт в профиле", "Отмена встречи — разбор", "/status это описание",
            "очередь в магазине"):
    m = FAKE.mark()
    say_to(OWNER, OWNER, cap, media=video())
    d = draft_of(OWNER, m)
    say(d and job_by_caption(cap.split()[0]) is not None,
        f"видео с подписью «{cap}» — черновик, а не команда")

print("\n--- исключения, документы, фото, размер ---")
m = FAKE.mark()
say_to(OWNER, OWNER, "без ютуба\nОписание без ютуба", media=video())
d = draft_of(OWNER, m)
j = job_by_caption("Описание без")
say(j and j["targets"] == "tiktok,reels" and "без ютуба\n" not in j["caption"],
    f"«без ютуба» — YouTube снят, строка не в описании: {j and j['targets']}")
m = FAKE.mark()
files_before = len(FAKE.calls("getFile"))
say_to(OWNER, OWNER, "договор", media=video(b"%PDF-1.4" + b"x" * 1000, name="dogovor.pdf",
                                             mime="application/pdf", kind="document"))
say(last_text(OWNER, m, "Это не видео"), "PDF — «это не видео»")
say(len(FAKE.calls("getFile")) == files_before, "PDF даже не скачивали")
m = FAKE.mark()
say_to(OWNER, OWNER, "файлом", media=video(name="clip.mp4", kind="document"))
say(draft_of(OWNER, m), "видео файлом (документом) — черновик")
m = FAKE.mark()
say_to(OWNER, OWNER, None, media=("photo", [{"file_id": "P1", "width": 90, "height": 90}]))
say(last_text(OWNER, m, "Пришли видео"), "фото — подсказка прислать видео")
m = FAKE.mark()
files_before = len(FAKE.calls("getFile"))
say_to(OWNER, OWNER, "большое", media=video(size_field=73 * 1024 * 1024))
say(last_text(OWNER, m, "до 20 МБ"), "73 МБ через облако — объяснение и выход")
say(len(FAKE.calls("getFile")) == files_before, "большое через облако не пытались качать")

print("\n--- одно видео дважды ---")
same = mp4(seed=b"same-video")
m = FAKE.mark()
say_to(OWNER, OWNER, "двойник", media=video(same))
say_to(OWNER, OWNER, "двойник", media=video(same))
say(last_text(OWNER, m, "Второй раз не ставлю"), "второе такое же — не ставим")
twins = jobs.connect().execute("SELECT COUNT(*) FROM jobs WHERE caption='двойник' "
                               "AND status!='cancelled'").fetchone()[0]
say(twins == 1, f"в базе одна задача: {twins}")

print("\n--- «Своё время» не съедает ссылку ---")
m = FAKE.mark()
say_to(OWNER, OWNER, "ко времени", media=video())
d = draft_of(OWNER, m)
j = job_by_caption("ко времени")
press(d, f"w:{j['id']}:ask", OWNER)
say(last_text(OWNER, m, "Напиши время"), "просит написать время")
m2 = FAKE.mark()
say_to(OWNER, OWNER, "https://youtube.com/shorts/abcDEF12345")
say(last_text(OWNER, m2, "Сохранил"), "ссылка, присланная пока ждём время, — в подборку")
say_to(OWNER, OWNER, "завтра 9:30")
say(wait(lambda: jobs.by_id(j["id"])["status"] == "pending"), "«завтра 9:30» — поставлено")
fixed = wait(lambda: [p for _, p in FAKE.calls("editMessageText", m2) if "завтра в 09:30" in p["text"]])
say(fixed, "сообщение-черновик: «завтра в 09:30»")
m2 = FAKE.mark()
say_to(OWNER, OWNER, "/queue")
q = last_text(OWNER, m2, "В очереди")
say(q and f"#{j['id']}" in q["text"], "/queue показывает поставленное")
say_to(OWNER, OWNER, f"/cancel {j['id']}")
say(last_text(OWNER, m2, "отменена"), "/cancel — отменено")
say(jobs.by_id(j["id"])["status"] == "cancelled" and not os.path.exists(j["video_path"]),
    "задача отменена, файл удалён")

print("\n--- сбой публикации ---")
m = FAKE.mark()
say_to(OWNER, OWNER, "СЛОМАЙ меня", media=video())
d = draft_of(OWNER, m)
j = job_by_caption("СЛОМАЙ")
press(d, f"t:{j['id']}:reels", OWNER)
press(d, f"t:{j['id']}:shorts", OWNER)
time.sleep(0.5)
press(d, f"w:{j['id']}:now", OWNER)
photo = last_text(OWNER, m, "❌", method="sendPhoto")
say(photo and photo.get("photo") == SCREEN_PNG, "владельцу — снимок экрана (multipart дошёл целым)")
say(last_text(OWNER, m, "Что было на экране"), "и что было на экране — текстом")
say(wait(lambda: jobs.by_id(j["id"])["status"] == "failed"), "задача — «не удалось»")
idle()

print("\n--- телефон занят сессией ---")
release = threading.Event()
webui.RUNNER.start("сессия tiktok 10 мин", lambda: release.wait(20))
m = FAKE.mark()
say_to(OWNER, OWNER, "пока идёт сессия", media=video())
d = draft_of(OWNER, m)
j = job_by_caption("пока идёт сессия")
press(d, f"w:{j['id']}:now", OWNER)
busy = last_text(OWNER, m, "занят")
say(busy and "сессия tiktok" in busy["text"], "«телефон занят (сессия …)» — сказано, чем")
release.set()
say(last_text(OWNER, m, "опубликовано"), "сессия кончилась — сторож опубликовал, отчёт в чат")
say(wait(lambda: jobs.by_id(j["id"])["status"] == "done"), "задача опубликована")
idle()

print("\n--- кнопки работают и после перезапуска программы ---")
m = FAKE.mark()
say_to(OWNER, OWNER, "до перезапуска", media=video())
d = draft_of(OWNER, m)
j = job_by_caption("до перезапуска")
bot.restart()
time.sleep(0.8)
m2 = FAKE.mark()
press(d, f"w:{j['id']}:tom12", OWNER)
say(wait(lambda: jobs.by_id(j["id"])["status"] == "pending"), "старая кнопка «Завтра 12:00» сработала")
say(wait(lambda: [p for _, p in FAKE.calls("editMessageText", m2) if "завтра в 12:00" in p["text"]]),
    "сообщение поправилось: «завтра в 12:00»")
m2 = FAKE.mark()
press(d, f"x:{j['id']}", OWNER)
say(wait(lambda: jobs.by_id(j["id"])["status"] == "cancelled"), "«Отменить пост» — отменён")
press(d, f"w:{j['id']}:now", OWNER)
note = wait(lambda: [p for _, p in FAKE.calls("answerCallbackQuery", m2) if p.get("text")])
say(note and "отменён" in note[-1]["text"], f"кнопка отменённого — «этот пост отменён»")
say(len(texts(OWNER, m2)) == 0, "и в чат лишнего не написал")

print("\n--- старый шаблон подписи ---")
m = FAKE.mark()
say_to(OWNER, OWNER, "куда: тикток\nкогда: завтра 18:30\nописание:\nПо шаблону #тест",
       media=video())
desc = last_text(OWNER, m, "/cancel")
tj = job_by_caption("По шаблону")
say(desc and tj and tj["status"] == "pending" and tj["targets"] == "tiktok" and tj["exact"],
    f"шаблон: только TikTok, завтра 18:30, точно: {tj and (tj['targets'], tj['status'])}")
jobs.cancel(tj["id"])

print("\n--- альбом из трёх видео ---")
m = FAKE.mark()
say_to(OWNER, OWNER, "Альбом — подпись к первому", media=video(), media_group_id="G1")
say_to(OWNER, OWNER, None, media=video(), media_group_id="G1")
say_to(OWNER, OWNER, None, media=video(), media_group_id="G1")
say(wait(lambda: len([p for p in texts(OWNER, m) if "получил" in p["text"]]) == 3),
    "три видео альбомом — три черновика")

print("\n--- бот не замирает, пока качается большое ---")
FAKE.slow_file = 2.5
m = FAKE.mark()
say_to(OWNER, OWNER, "медленное", media=video())
time.sleep(0.3)
say_to(OWNER, OWNER, "/status")
status = last_text(OWNER, m, "В очереди", )
d = draft_of(OWNER, m)
say(status and d and status["_id"] < d["_id"], "/status ответил раньше, чем докачалось видео")
FAKE.slow_file = 0.0

print("\n--- поток сообщений и лимит Telegram ---")
m = FAKE.mark()
for n in range(25):
    say_to(OWNER, OWNER, "/status")
got = wait(lambda: len([p for p in texts(OWNER, m) if "В очереди" in p["text"]]) >= 25, 15)
count = len([p for p in texts(OWNER, m) if "В очереди" in p["text"]])
say(count == 25, f"25 команд подряд — 25 ответов, без потерь и повторов: {count}")
FAKE.rate_limit = 1
m = FAKE.mark()
say_to(OWNER, OWNER, "/status")
say(last_text(OWNER, m, "В очереди"), "429 «слишком часто» — подождал и отправил")

print("\n--- чужой человек в личке (бот открыт всем — config.BOT_OPEN) ---")
m = FAKE.mark()
say_to(STRANGER, STRANGER, "/status")
say(last_text(STRANGER, m, "В очереди") and not texts(OWNER, m),
    "ответ — ему, не владельцу")

print("\n--- группа ---")
m = FAKE.mark()
FAKE.push({"my_chat_member": {"chat": {"id": GROUP, "type": "supergroup", "title": "Команда"},
                              "from": {"id": OWNER}, "date": int(time.time()),
                              "old_chat_member": {"status": "left"},
                              "new_chat_member": {"status": "member"}}})
hello = last_text(GROUP, m, "как в личке")
say(hello and "ОТВЕТОМ" in hello["text"], "добавил владелец — приветствие и как дать видеть видео")
say(str(GROUP) in bot.allowed_groups(), "чат в списке разрешённых")
m = FAKE.mark()
say_to(GROUP, FRIEND, "в пост", media=video(), reply_to=hello["_id"])
gd = draft_of(GROUP, m)
say(gd and "inline_keyboard" in gd["reply_markup"], "видео ответом на бота — черновик в группе")
for chatter in ("ссылки скинь", "отмена встречи", "очередь в кассу", "шаблон"):
    say_to(GROUP, FRIEND, chatter)
say_to(GROUP, FRIEND, None, media=video(kind="animation", name="lol.mp4"))
time.sleep(1.0)
say(not [p for p in texts(GROUP, m) if p["_id"] > gd["_id"]],
    "болтовня («ссылки скинь», «отмена…», «очередь…», гифка) — бот молчит")
say(jobs.links_waiting() > 0, "подборка не «съедена» чужим «ссылки скинь»")
m = FAKE.mark()
say_to(GROUP, FRIEND, f"/status@{BOTNAME}")
say(last_text(GROUP, m, "В очереди"), "/status@бот — ответ в группу")
say_to(GROUP, FRIEND, f"@{BOTNAME} очередь")
say(last_text(GROUP, m, "Чтобы опубликовать"), "«@бот очередь» — очередь")
say(all("reply_markup" not in p for p in texts(GROUP, m)), "клавиатура внизу в группе не вешается")
m = FAKE.mark()
gj = job_by_caption("в пост")
press(gd, f"w:{gj['id']}:now", FRIEND)
say(last_text(GROUP, m, "опубликовано") and not last_text(OWNER, m, "опубликовано"),
    "участник нажал «Сейчас» — отчёт в группу")
idle()
m = FAKE.mark()
say_to(GROUP, FRIEND, f"/start@{BOTNAME}", thread=42)
topic = last_text(GROUP, m, "как в личке")
say(topic and str(topic.get("message_thread_id")) == "42", "тема в группе — ответ в ту же тему")
m = FAKE.mark()
say_to(GROUP, FRIEND, "СЛОМАЙ в группе", media=video(), reply_to=hello["_id"])
gd2 = draft_of(GROUP, m)
gj2 = job_by_caption("СЛОМАЙ в группе")
press(gd2, f"w:{gj2['id']}:now", FRIEND)
say(last_text(OWNER, m, "❌", method="sendPhoto") and not texts(GROUP, m, "sendPhoto"),
    "сбой в группе: снимок — только владельцу лично")
say(last_text(GROUP, m, "ОШИБКА"), "а в группу — отчёт словами")
idle()

m = FAKE.mark()
say_to(GROUP, FRIEND, "в группе ко времени", media=video(), reply_to=hello["_id"])
gd3 = draft_of(GROUP, m)
gj3 = job_by_caption("в группе ко времени")
press(gd3, f"w:{gj3['id']}:ask", FRIEND)
ask = last_text(GROUP, m, "Напиши время")
say(ask and "ответом на это сообщение" in ask["text"], "в группе «Напиши время — ответом на это сообщение»")
say_to(GROUP, FRIEND, "21:45", reply_to=ask["_id"])
say(wait(lambda: jobs.by_id(gj3["id"])["status"] == "pending"), "время ответом — поставлено")
say(jobs.by_id(gj3["id"])["chat"] == str(GROUP), "задача помнит группу")
jobs.cancel(gj3["id"])

print("\n--- бот только в одной теме группы ---")
m = FAKE.mark()
say_to(GROUP, FRIEND, "черновик до темы", media=video(), reply_to=hello["_id"])
early = draft_of(GROUP, m)
early_job = job_by_caption("черновик до темы")
m = FAKE.mark()
say_to(GROUP, FRIEND, f"/here@{BOTNAME}", thread=42, topic_name="Постинг")
say(last_text(GROUP, m, "может только его владелец") and bot.group_topic(GROUP) is None,
    "участник не может выбрать тему — только владелец")
m = FAKE.mark()
say_to(GROUP, OWNER, f"/here@{BOTNAME}", thread=42, topic_name="Постинг")
bound = last_text(GROUP, m, "только в теме «Постинг»")
say(bound and str(bound.get("message_thread_id")) == "42", "/here в теме «Постинг» — привязан, ответ туда")
say(bot.group_topic(GROUP) == 42, "привязка записана")
m = FAKE.mark()
say_to(GROUP, FRIEND, "/status", thread=7)
say_to(GROUP, FRIEND, f"/status@{BOTNAME}")
say_to(GROUP, FRIEND, "в другую тему", media=video(), thread=7)
say_to(GROUP, FRIEND, "/here", thread=7)
time.sleep(1.2)
say(not texts(GROUP, m), "в других темах и в «Общей» — ни слова (и на чужой /here тоже)")
say(job_by_caption("в другую тему") is None and bot.group_topic(GROUP) == 42,
    "видео из другой темы не взято, привязка не сдвинута")
press(early, f"w:{early_job['id']}:now", FRIEND)
alert = wait(lambda: [p for _, p in FAKE.calls("answerCallbackQuery", m) if p.get("text")])
say(alert and "другой теме" in alert[-1]["text"] and not texts(GROUP, m)
    and jobs.by_id(early_job["id"])["status"] == "draft",
    "кнопка старого черновика из «Общей» — всплывашка, в чат ни слова")
m = FAKE.mark()
say_to(GROUP, FRIEND, "/status", thread=42)
st = last_text(GROUP, m, "В очереди")
say(st and str(st.get("message_thread_id")) == "42", "в своей теме — отвечает туда")
say_to(GROUP, FRIEND, "в нужную тему", media=video(), thread=42)
td = draft_of(GROUP, m)
say(td and str(td.get("message_thread_id")) == "42", "видео в своей теме — черновик там же")
tj = job_by_caption("в нужную тему")
press(td, f"w:{tj['id']}:now", FRIEND)
rep_ = last_text(GROUP, m, "опубликовано")
say(rep_ and str(rep_.get("message_thread_id")) == "42", "отчёт о публикации — в тему «Постинг»")
idle()
m = FAKE.mark()
old_order = jobs.add(job_by_caption("в нужную тему")["video_path"], "заказ из Общей",
                     "tiktok", exact=True, chat=str(GROUP), fingerprint="old-order")
rep2 = last_text(GROUP, m, "опубликовано", )
say(rep2 and str(rep2.get("message_thread_id")) == "42",
    "пост, заказанный ещё в «Общей», отчитался в «Постинг»")
idle()
say("только тема «Постинг»" in " ".join(webui._bot_state()["groups"]),
    f"окно: {webui._bot_state()['groups']}")
m = FAKE.mark()
say_to(GROUP, OWNER, "/everywhere", thread=42)
say(last_text(GROUP, m, "во всех темах") and bot.group_topic(GROUP) is None,
    "/everywhere — снова во всех темах")
say_to(GROUP, FRIEND, f"/status@{BOTNAME}")
gen = last_text(GROUP, m, "В очереди")
say(gen and not gen.get("message_thread_id"), "и в «Общей» снова отвечает")
jobs.cancel(early_job["id"])

print("\n--- три темы: видео, ссылки, статистика ---")
POSTS, LINKS, STATS, OTHER = 10, 20, 30, 40
m = FAKE.mark()
say_to(GROUP, OWNER, "/here видео", thread=POSTS, topic_name="Постинг")
say_to(GROUP, OWNER, "/here ссылки", thread=LINKS, topic_name="Ссылки")
say_to(GROUP, OWNER, f"/here@{BOTNAME} статистика", thread=STATS, topic_name="Статистика")
last = last_text(GROUP, m, "тема «Статистика» — статистика")
say(last and str(last.get("message_thread_id")) == str(STATS)
    and "видео на постинг → «Постинг»" in last["text"] and "ссылки → «Ссылки»" in last["text"],
    "три команды /here … — темы привязаны, бот сказал, что куда")
say(bot.role_topic(GROUP, "posts") == POSTS and bot.role_topic(GROUP, "links") == LINKS
    and bot.role_topic(GROUP, "stats") == STATS, "в настройках: видео 10, ссылки 20, статистика 30")
say("видео на постинг → «Постинг»" in " ".join(webui._bot_state()["groups"]),
    f"окно: {webui._bot_state()['groups']}")

m = FAKE.mark()
say_to(GROUP, FRIEND, "видео не туда", media=video(), thread=LINKS)
say_to(GROUP, FRIEND, "https://www.tiktok.com/@x/video/7500000000000000077", thread=POSTS)
say_to(GROUP, FRIEND, f"/stats@{BOTNAME}", thread=POSTS)
say_to(GROUP, FRIEND, f"/status@{BOTNAME}", thread=OTHER)
say_to(GROUP, FRIEND, f"/links@{BOTNAME}", thread=STATS)
time.sleep(1.2)
say(not texts(GROUP, m), "видео в теме ссылок, ссылка в теме видео, /stats не в той теме, "
    "чужая тема — ни слова")
say(job_by_caption("видео не туда") is None, "видео из темы ссылок не взято")

m = FAKE.mark()
say_to(GROUP, FRIEND, "в постинг", media=video(), thread=POSTS)
pd = draft_of(GROUP, m)
say(pd and str(pd.get("message_thread_id")) == str(POSTS), "видео в «Постинг» — черновик там")
pj = job_by_caption("в постинг")
press(pd, f"w:{pj['id']}:now", FRIEND)
pr = last_text(GROUP, m, "опубликовано")
say(pr and str(pr.get("message_thread_id")) == str(POSTS), "отчёт о публикации — в «Постинг»")
idle()

m = FAKE.mark()
say_to(GROUP, FRIEND, "глянь https://www.tiktok.com/@x/video/7500000000000000078", thread=LINKS)
lk = last_text(GROUP, m, "Сохранил")
say(lk and str(lk.get("message_thread_id")) == str(LINKS), "ссылка в «Ссылки» — сохранена, ответ там")
m = FAKE.mark()
say(bot.digest(force=True), "подборка по расписанию ушла")
dg = last_text(GROUP, m, "Понравилось")
say(dg and str(dg.get("message_thread_id")) == str(LINKS) and not texts(OWNER, m),
    "вечерняя подборка — в «Ссылки», не в личку")

m = FAKE.mark()
scheduler.session.browse = lambda app, seconds=None, keep_open=False: (
    "сессия в tiktok, план 1.0 мин\n  заглянул в комментарии (протяжек: 2)\n"
    "итог: 30 видео, 4 лайков, 1 ссылок сохранено")
scheduler.run_session("tiktok", 60, manual=True)
ss = last_text(GROUP, m, "📊 Сессия TikTok")
say(ss and str(ss.get("message_thread_id")) == str(STATS) and "роликов 30" in ss["text"]
    and "лайков 4" in ss["text"], "после сессии — цифры в «Статистика»")
say(not texts(OWNER, m) and len(texts(GROUP, m)) == 1, "больше никуда — ни в личку, ни в другие темы")
m = FAKE.mark()
import datetime as _dt  # noqa: E402
late = _dt.datetime.combine(_dt.date.today(), _dt.time(22, 1))
said = scheduler.maybe_day_stats(late)
day = last_text(GROUP, m, "Итог дня")
say(said and day and str(day.get("message_thread_id")) == str(STATS), "в 22:00 — итог дня в «Статистика»")
say(day and "Сессий: 1" in day["text"] and "Роликов: 30" in day["text"] and "Постов:" in day["text"],
    "в итоге дня — сессии, ролики, посты: " + repr(day and day["text"][:120]))
say(not scheduler.maybe_day_stats(late), "второй раз за день — не шлёт")
say(not scheduler.maybe_day_stats(late.replace(hour=21)), "до 22:00 — не шлёт")
m = FAKE.mark()
say_to(GROUP, FRIEND, "/stats", thread=STATS)
say(last_text(GROUP, m, "Итог дня"), "/stats в «Статистика» — итог за сегодня")
say_to(GROUP, FRIEND, "/status", thread=LINKS)
st = last_text(GROUP, m, "В очереди")
say(st and str(st.get("message_thread_id")) == str(LINKS), "/status в любой из тем бота — ответ там же")

m = FAKE.mark()
say_to(GROUP, OWNER, "/everywhere ссылки", thread=LINKS)
say(last_text(GROUP, m, "снова в любой теме") and bot.role_topic(GROUP, "links") is None
    and bot.role_topic(GROUP, "posts") == POSTS, "/everywhere ссылки — отвязаны только ссылки")
say_to(GROUP, FRIEND, "https://www.tiktok.com/@x/video/7500000000000000079", thread=OTHER)
lo = last_text(GROUP, m, "Сохранил")
say(lo and str(lo.get("message_thread_id")) == str(OTHER), "ссылки снова принимаются в любой теме")
say_to(GROUP, OWNER, "/everywhere")
say(wait(lambda: not bot._roles(GROUP)), "/everywhere — всё отвязано")

print("\n--- чужая группа ---")
m = FAKE.mark()
FAKE.push({"my_chat_member": {"chat": {"id": ALIEN_GROUP, "type": "supergroup", "title": "Левые"},
                              "from": {"id": STRANGER}, "date": int(time.time()),
                              "old_chat_member": {"status": "left"},
                              "new_chat_member": {"status": "member"}}})
say(last_text(OWNER, m, "не ты"), "владельцу лично: добавили в чужой чат")
say_to(ALIEN_GROUP, STRANGER, f"/status@{BOTNAME}")
say_to(ALIEN_GROUP, STRANGER, "пост", media=video())
time.sleep(1.0)
say(not texts(ALIEN_GROUP, m), "в чужой группе — ни слова")

print("\n--- беды связи ---")
FAKE.mode.add("conflict")
say(wait(lambda: bot.trouble() == bot.CONFLICT, 6), "409 дольше минуты — «другая копия»")
m = FAKE.mark()
say_to(OWNER, OWNER, "/status")
FAKE.mode.discard("conflict")
say(wait(lambda: bot.trouble() == "", 6) and last_text(OWNER, m, "В очереди"),
    "копия ушла — беда снята, пропущенное обработано")
FAKE.mode.add("webhook")
say(wait(lambda: bot.trouble() == bot.WEBHOOK, 6), "включён webhook — сказано именно это")
FAKE.mode.discard("webhook")
say(wait(lambda: bot.trouble() == "", 6), "webhook сняли — работает")
FAKE.mode.add("down")
say(wait(lambda: "нет связи" in bot.trouble(), 8), "обрыв связи — «нет связи»")
m = FAKE.mark()
say_to(OWNER, OWNER, "/status")
FAKE.mode.discard("down")
say(wait(lambda: bot.trouble() == "", 8) and last_text(OWNER, m, "В очереди"),
    "связь вернулась — пришедшее за время обрыва обработано")
state = webui._bot_state()
say(state.get("groups") and "Команда" in state["groups"][0], f"окно: «работает в чатах»: {state.get('groups')}")

print("\n--- смена токена (перевыпуск в @BotFather) ---")
FAKE.token = TOKEN2
say(wait(lambda: bot.trouble() == bot.REJECTED, 6), "старый токен отозван — «токен не принят»")
bot.save_settings(TOKEN2, bot.load_settings()["owner"])
bot.restart()
m = FAKE.mark()
say_to(OWNER, OWNER, "/status")
say(wait(lambda: bot.trouble() == "", 6) and last_text(OWNER, m, "В очереди"),
    "новый токен — сразу работает")
time.sleep(1.0)
alive = [t for t in threading.enumerate() if t.name.startswith("Thread") and t.is_alive()]
say(len(texts(OWNER, m)) == 1, "на одно сообщение — один ответ (старый поток ушёл): "
    + repr([p["text"][:40] for p in texts(OWNER, m)]))

print("\n--- свой сервер: видео до 2 ГБ ---")
local = tempfile.mkdtemp(prefix="pa_local_")
FAKE.local_dir = local
FAKE.slow_file = 1.0
bot.save_settings(TOKEN2, bot.load_settings()["owner"], api_id="12345", api_hash="0123abcd" * 4)
tgserver.start = lambda api_id, api_hash, token="", wait=20.0: URL
tgserver.url = lambda: URL
bot.restart()
say(wait(lambda: FAKE.logged_out == 1, 6), "выписал бота из облака один раз")
m = FAKE.mark()
big = mp4(200 * 1024)
kind, obj = video(big)
obj["file_size"] = 73 * 1024 * 1024
say_to(OWNER, OWNER, "большое через свой сервер", media=(kind, obj))
say(last_text(OWNER, m, "Качаю 73"), "«Качаю 73 МБ — это займёт время»")
d = draft_of(OWNER, m)
j = job_by_caption("большое через свой")
say(d and j and os.path.isfile(j["video_path"]) and not os.listdir(local),
    "файл со своего сервера перенесён в очередь, на сервере не остался")
bot.restart()
time.sleep(1.5)
say(FAKE.logged_out == 1, "второй запуск — из облака не выписывает снова")

print("\n--- /stop ---")
m = FAKE.mark()
abort.clear()
say_to(OWNER, OWNER, "/stop")
say(last_text(OWNER, m, "Останавливаю") and abort.requested(), "/stop — останавливает")
abort.clear()

print("\n--- итог по всей переписке ---")
broken = [p["text"] for _, p in FAKE.calls("sendMessage")
          if p["text"].startswith(("ошибка обработки", "ошибка кнопки"))]
say(not broken, f"ни одного «ошибка обработки» за весь прогон: {broken[:2]}")
say(bot.running(), "поток бота жив")

SERVER.shutdown()
print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
if FAILS:
    print("\n".join("  - " + f for f in FAILS))
sys.stdout.flush()
os._exit(0 if ok else 1)
