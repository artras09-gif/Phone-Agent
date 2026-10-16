"""Бот в групповом чате — тот же функционал, что в личке (просьба 2026-10-16).

Без Telegram: все вызовы API подменены. Проверяется и безопасность: бот
работает только в чатах владельца, чужой чат — молчит.
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config     # noqa: E402

tmp = tempfile.mkdtemp(prefix="pa_groups_")
config.DB_PATH = os.path.join(tmp, "jobs.db")
config.QUEUE_DIR = os.path.join(tmp, "queue")
config.BASE = tmp
os.makedirs(config.QUEUE_DIR)

import jobs          # noqa: E402
import scheduler     # noqa: E402
import telegram_bot as bot  # noqa: E402

jobs._SCHEMA_READY = False
ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


OWNER, MEMBER, OTHER, STRANGER = "100", "300", "400", "200"
GROUP, ALIEN = "-500", "-600"
calls = []


def fake_call(method, params=None, timeout=70):
    calls.append((method, dict(params or {})))
    if method == "getMe":
        return {"ok": True, "result": {"username": "Videopost23bot",
                                       "can_read_all_group_messages": False}}
    return {"ok": True, "result": {}}


bot._call = fake_call
bot.save_settings("1234567:" + "A" * 35, OWNER)
video = os.path.join(config.QUEUE_DIR, "v.mp4")
bot._download = lambda file_id, dest: (open(video, "wb").write(os.urandom(64)) and video)
scheduler.looks_like_video = lambda path: ""
bot._run = lambda title, work: None


def sent(chat=None):
    return [p for m, p in calls if m == "sendMessage" and (chat is None or str(p["chat_id"]) == chat)]


def message(chat, sender, text="", video=False, kind="supergroup", **extra):
    msg = {"chat": {"id": int(chat), "type": kind, "title": "Команда"},
           "from": {"id": int(sender)}, **extra}
    if video:
        msg["video"] = {"file_id": "f", "file_size": 1000}
        msg["caption"] = text
    else:
        msg["text"] = text
    return msg


def handle(msg):
    try:
        bot._handle(msg)
    finally:
        bot.set_reply(None)


def member_update(chat, by, status, was="left", kind="supergroup"):
    bot._membership({"chat": {"id": int(chat), "type": kind, "title": "Команда"},
                     "from": {"id": int(by)}, "new_chat_member": {"status": status},
                     "old_chat_member": {"status": was}})
    bot.set_reply(None)


print("--- владелец добавил бота в чат ---")
member_update(GROUP, OWNER, "member")
say(GROUP in bot.allowed_groups(), "чат владельца — в списке разрешённых")
hello = sent(GROUP)
say(hello and "как в личке" in hello[-1]["text"] and "/setprivacy" in hello[-1]["text"],
    "в чате — приветствие и как дать видеть видео (privacy)")

calls.clear()
member_update(GROUP, OWNER, "administrator", was="member")
say(len(sent(GROUP)) == 1 and "админ" in sent(GROUP)[0]["text"]
    and "как в личке" not in sent(GROUP)[0]["text"],
    "сделали админом — без второго приветствия, коротко")
calls.clear()
member_update("-800", OWNER, "administrator", kind="channel")
say(not sent() and "-800" not in bot.allowed_groups(), "канал — не трогаем, в канал не пишем")

print("\n--- чужой добавил бота в свой чат ---")
calls.clear()
member_update(ALIEN, STRANGER, "member")
say(ALIEN not in bot.allowed_groups(), "чужой чат — не разрешён")
say(sent(OWNER) and "не ты" in sent(OWNER)[-1]["text"] and not sent(ALIEN),
    "владельцу сказано лично, в чужой чат — ни слова")
calls.clear()
handle(message(ALIEN, STRANGER, "смотри видео", video=True))
say(not sent() and len(jobs.connect().execute("SELECT * FROM jobs").fetchall()) == 0,
    "видео в чужом чате — молча игнор, задачи нет")

print("\n--- участник чата присылает видео ---")
calls.clear()
handle(message(GROUP, MEMBER, "@Videopost23bot 🇺🇸 Título\nTexto del post", video=True))
job = jobs.connect().execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 1").fetchone()
say(job and job["status"] == "draft", "черновик создан")
say(job and "Videopost23bot" not in job["caption"] and job["caption"].startswith("🇺🇸"),
    f"упоминание бота вырезано из описания: {job['caption'][:30]!r}")
draft = sent(GROUP)
say(draft and "reply_markup" in draft[-1] and "inline_keyboard" in draft[-1]["reply_markup"],
    "черновик с кнопками — в этот же чат")
say(draft and "Название на YouTube: 🇺🇸 Título" in draft[-1]["text"],
    "первая строка — название на YouTube, как в личке")

print("\n--- «Своё время» — ждём ответа от того, кто нажал ---")
calls.clear()
bot._handle_callback({"id": "cb", "data": f"w:{job['id']}:ask", "from": {"id": int(MEMBER)},
                      "message": {"chat": {"id": int(GROUP), "type": "supergroup"},
                                  "message_id": 9, "is_topic_message": True,
                                  "message_thread_id": 7}})
bot.set_reply(None)
ask = sent(GROUP)
say(ask and "Напиши время" in ask[-1]["text"] and ask[-1].get("message_thread_id") == 7,
    "«Напиши время» — в ту же тему, где кнопка")
handle(message(GROUP, OTHER, "18:30"))
say(jobs.by_id(job["id"])["status"] == "draft", "«18:30» от другого участника — не принято")
handle(message(GROUP, MEMBER, "18:30"))
say(jobs.by_id(job["id"])["status"] == "pending", "«18:30» от нажавшего — поставлено")

print("\n--- болтовня и команды ---")
calls.clear()
handle(message(GROUP, OTHER, "привет всем, как дела"))
say(not sent(), "обычное сообщение в чате — бот молчит")
handle(message(GROUP, OTHER, "/status@Videopost23bot"))
say(sent(GROUP) and "В очереди" in sent(GROUP)[-1]["text"], "/status@бот — ответ в чат")
calls.clear()
handle(message(GROUP, OTHER, "/status@other_bot"))
say(not sent(), "команда другому боту — не наша")
handle(message(GROUP, OTHER, "@Videopost23bot ты тут?"))
say(sent(GROUP) and "Пришли видео" in sent(GROUP)[-1]["text"], "обратились к боту — ответил")
say(all("reply_markup" not in p for p in sent(GROUP)), "клавиатура внизу в группе не вешается")

print("\n--- ссылка в чате ---")
calls.clear()
before = len(jobs.connect().execute("SELECT * FROM links").fetchall())
handle(message(GROUP, OTHER, "глянь https://www.tiktok.com/@x/video/7500000000000000001"))
after = len(jobs.connect().execute("SELECT * FROM links").fetchall())
say(after == before + 1 and sent(GROUP), "ссылка из чата сохранена, ответ — в чат")

print("\n--- темы в группе ---")
calls.clear()
handle(message(GROUP, OTHER, "/status", is_topic_message=True, message_thread_id=7))
say(sent(GROUP) and sent(GROUP)[-1].get("message_thread_id") == 7, "ответ — в ту же тему")

print("\n--- пост по времени — отчёт в тот чат, откуда заказали ---")
import threading  # noqa: E402
import poster     # noqa: E402

say(jobs.by_id(job["id"])["chat"] == GROUP, "задача помнит чат, откуда её заказали")
poster.post = lambda path, text, target: (True, f"{target}: опубликовано")


def tell(text):                       # как webui._tell: без адреса
    bot.send(str(text))


def dispatcher(job_id):               # сторож окна — свой поток, без «кому отвечать»
    t = threading.Thread(target=scheduler.run_post_job, args=(tell, job_id))
    t.start()
    t.join()


calls.clear()
dispatcher(job["id"])
say(sent(GROUP) and "опубликовано" in sent(GROUP)[-1]["text"] and not sent(OWNER),
    "сторож опубликовал по времени — отчёт в группу, не в личку")
calls.clear()
handle(message(GROUP, MEMBER, "в тему", video=True, is_topic_message=True, message_thread_id=7))
topic_job = jobs.connect().execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 1").fetchone()
say(topic_job["chat"] == f"{GROUP}:7", f"из темы — помнит и тему: {topic_job['chat']}")
jobs.schedule(topic_job["id"], None, False)
calls.clear()
dispatcher(topic_job["id"])
say(sent(GROUP) and sent(GROUP)[-1].get("message_thread_id") == 7, "отчёт — в ту же тему")
plain = jobs.add(video, "из окна", "tiktok", exact=True)
calls.clear()
dispatcher(plain)
say(sent(OWNER) and not sent(GROUP), "задача из окна — отчёт владельцу, как раньше")

print("\n--- владелец пишет в новом чате сам ---")
calls.clear()
handle(message("-700", OWNER, "/start"))
say("-700" in bot.allowed_groups() and sent("-700"), "/start владельца — чат разрешён, приветствие")

print("\n--- группа стала супергруппой: новый номер чата ---")
calls.clear()
handle(message("-700", OWNER, kind="group", migrate_to_chat_id=-100700))
handle(message("-100700", OWNER, migrate_from_chat_id=-700))
say("-100700" in bot.allowed_groups() and "-700" not in bot.allowed_groups() and not sent(),
    "разрешение переехало на новый номер, молча")
handle(message("-100700", OTHER, "/status"))
say(sent("-100700") and "В очереди" in sent("-100700")[-1]["text"], "в новом номере бот работает")

print("\n--- бота убрали из чата ---")
member_update(GROUP, OWNER, "left")
say(GROUP not in bot.allowed_groups(), "убрали — чат из списка удалён")
calls.clear()
job2 = jobs.add(video, "x", "tiktok", status="draft")
bot._handle_callback({"id": "cb2", "data": f"x:{job2}", "from": {"id": int(OTHER)},
                      "message": {"chat": {"id": int(GROUP), "type": "supergroup"},
                                  "message_id": 10}})
bot.set_reply(None)
answer = [p for m, p in calls if m == "answerCallbackQuery"]
say(jobs.by_id(job2)["status"] == "draft" and answer and "не подключён" in answer[-1]["text"],
    "кнопка под старым сообщением в убранном чате — не срабатывает")
late = jobs.add(video, "после ухода", "tiktok", exact=True, chat=GROUP)
calls.clear()
dispatcher(late)
say(sent(OWNER) and not sent(GROUP), "бота убрали, а пост ждал — отчёт владельцу")

print("\n--- личка — как раньше ---")
calls.clear()
handle(message(OWNER, OWNER, "/status", kind="private"))
say(sent(OWNER) and "В очереди" in sent(OWNER)[-1]["text"], "в личке /status работает")
handle(message(OWNER, OWNER, "привет", kind="private"))
say("reply_markup" in sent(OWNER)[-1], "в личке клавиатура внизу — на месте")
say(json.load(open(bot.settings_path(), encoding="utf-8"))["owner"] == OWNER,
    "владелец — человек, а не группа")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
