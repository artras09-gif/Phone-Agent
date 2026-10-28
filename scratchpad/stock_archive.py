"""Запасы и архив (просьба 2026-10-28): опубликованное — в архив, из запасов — прочь.

«Всё, что запостил, сначала пересылает в отдельный чат, а потом удаляет из
другого чата, в котором будут лежать запасы». Запасы, постинг и архив — темы
одной группы; видео из запасов человек ПЕРЕСЫЛАЕТ в тему постинга.

Без Telegram и без телефона: API и публикация подменены. Проверяется порядок
(сначала архив, потом удаление), что вышедшее не везде остаётся в запасах и
что ни при одной ошибке видео не пропадает из запасов, не попав в архив.
"""
import itertools
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config     # noqa: E402

tmp = tempfile.mkdtemp(prefix="pa_stock_")
config.DB_PATH = os.path.join(tmp, "jobs.db")
config.QUEUE_DIR = os.path.join(tmp, "queue")
config.BASE = tmp
os.makedirs(config.QUEUE_DIR)

import jobs          # noqa: E402
import scheduler     # noqa: E402
import yttags        # noqa: E402
yttags.suggest = lambda caption, timeout=40: ["тег"]          # без модели
import telegram_bot as bot  # noqa: E402

jobs._SCHEMA_READY = False
ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


OWNER, MEMBER = "100", "300"
GROUP = "-1005"
POSTS, STOCK, ARCHIVE = 10, 20, 30
NAMES = {POSTS: "Постинг для бота", STOCK: "Запасы", ARCHIVE: "запощено"}

calls = []
state = {"admin": True, "can_delete": True, "forward_fail": set(), "delete_fail": ""}


def fake_call(method, params=None, timeout=70):
    params = dict(params or {})
    calls.append((method, params))
    if method == "getMe":
        return {"ok": True, "result": {"id": 999, "username": "Videopost23bot",
                                       "can_read_all_group_messages": True}}
    if method == "getChatMember":
        member = {"status": "administrator" if state["admin"] else "member"}
        if state["admin"]:
            member["can_delete_messages"] = state["can_delete"]
        return {"ok": True, "result": member}
    if method == "forwardMessage":
        if (str(params["from_chat_id"]), int(params["message_id"])) in state["forward_fail"]:
            return {"ok": False, "error_code": 400,
                    "description": "Bad Request: message to forward not found"}
        return {"ok": True, "result": {"message_id": 5000}}
    if method == "deleteMessage" and state["delete_fail"]:
        return {"ok": False, "error_code": 400, "description": state["delete_fail"]}
    return {"ok": True, "result": {"message_id": 4000}}


bot._call = fake_call
bot._ME.clear()
bot.save_settings("1234567:" + "A" * 35, OWNER)
bot.allow_group(GROUP, "11111")
counter = itertools.count(1)


def fake_download(file_id, dest, *more):
    path = os.path.join(dest, f"v{next(counter)}.mp4")
    with open(path, "wb") as f:
        f.write(os.urandom(64))
    return path


bot._download = fake_download
scheduler.looks_like_video = lambda path: ""
bot._run = lambda title, work: None                # «Сейчас» гоняем руками
failing = set()
scheduler.poster.post = lambda path, text, target, extra=None: (
    (False, f"{target}: ОШИБКА не нашёл кнопку") if target in failing else (True, f"{target}: ok"))
scheduler._tell_failure = lambda target, report: None
msg_ids = itertools.count(100)


def message(thread, sender=MEMBER, text="", file=None, forwarded=False):
    msg = {"chat": {"id": int(GROUP), "type": "supergroup", "title": "11111", "is_forum": True},
           "from": {"id": int(sender)}, "message_id": next(msg_ids),
           "message_thread_id": thread, "is_topic_message": True, "date": 1761650000,
           "reply_to_message": {"message_id": thread,
                                "forum_topic_created": {"name": NAMES[thread]}}}
    if file:
        msg["video"] = {"file_id": "id-" + file, "file_unique_id": file, "file_size": 1000}
        msg["caption"] = text
    else:
        msg["text"] = text
    if forwarded:
        msg["forward_origin"] = {"type": "user", "sender_user": {"id": int(MEMBER)},
                                 "date": 1761640000}
    return msg


def handle(msg):
    try:
        bot._handle(msg)
    finally:
        bot.set_reply(None)
    return msg


def said():
    return "\n".join(p["text"] for m, p in calls if m == "sendMessage")


def api(method):
    return [p for m, p in calls if m == method]


def last_job():
    return jobs.connect().execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 1").fetchone()


def post(job_id):
    jobs.schedule(job_id, None, True)
    return scheduler.run_post_job(job_id=job_id)


def from_stock(file, caption="UN VOTO CONTRA 14"):
    """Видео лежит в запасах, человек пересылает его в тему постинга."""
    original = handle(message(STOCK, file=file, text=caption))
    handle(message(POSTS, file=file, text=caption, forwarded=True))
    return original, last_job()


print("--- темы назначаются командами ---")
handle(message(POSTS, OWNER, "/here видео"))
state["admin"] = False
calls.clear()
handle(message(STOCK, OWNER, "/here запасы"))
say(bot.shelf_home("stock") == (GROUP, STOCK, "Запасы"), f"запасы: {bot.shelf_home('stock')}")
say("нет права удалять" in said() and "Архив пока не назначен" in said(),
    "бот не админ — предупреждает; без архива — подсказывает /here архив")
state["admin"] = True
calls.clear()
handle(message(ARCHIVE, OWNER, "/here архив"))
say(bot.shelf_home("archive") == (GROUP, ARCHIVE, "запощено"), f"архив: {bot.shelf_home('archive')}")
say("пересылаю сюда и удаляю из «Запасы»" in said(), "архив назначен — сказал, откуда удаляю")
say(any("запасы → «Запасы»" in g and "архив → «запощено»" in g for g in bot.groups_text()),
    f"окно видит обе темы: {bot.groups_text()}")

print("\n--- то, что запутало бы ---")
calls.clear()
handle(message(POSTS, OWNER, "/here запасы"))
say(bot.shelf_home("stock")[1] == STOCK and "тема постинга" in said(),
    "тема постинга не может стать запасами (удалял бы видео оттуда же)")
calls.clear()
handle(message(STOCK, OWNER, "/here архив"))
say(bot.shelf_home("archive")[1] == ARCHIVE and "уже запасы" in said(), "запасы ≠ архив")
calls.clear()
handle(message(STOCK, OWNER, "/here видео"))
say(bot.role_home("posts") == (GROUP, POSTS) and "Эта тема — запасы" in said(),
    "постинг не переезжает в запасы")
calls.clear()
handle(message(STOCK, MEMBER, "/here архив"))
say(bot.shelf_home("archive")[1] == ARCHIVE, "чужой участник темы не переназначает")

print("\n--- в запасах бот только запоминает ---")
calls.clear()
original = handle(message(STOCK, file="AAA", text="первое видео"))
handle(message(STOCK, text="обсуждаем, что выложить"))
handle(message(ARCHIVE, file="ZZZ", text="руками переслал в архив"))
rows = jobs.stock_find("AAA")
say(len(rows) == 1 and rows[0]["msg"] == original["message_id"] and rows[0]["chat"] == GROUP,
    "видео в запасах запомнено: где лежит")
say(not said() and last_job() is None, "в запасах и в архиве — ни слова и ни одного черновика")
say(not jobs.stock_find("ZZZ"), "видео в архиве не считается запасом")

print("\n--- переслал на постинг, вышло во все сети ---")
calls.clear()
handle(message(POSTS, file="AAA", text="первое видео", forwarded=True))
job = last_job()
src = jobs.tg_source(job)
say(job["status"] == "draft" and src.get("file") == "AAA" and src.get("fwd"),
    f"черновик помнит, откуда видео: {src}")
posting_msg = src["msg"]
calls.clear()
report = post(job["id"])
fwd, dele = api("forwardMessage"), api("deleteMessage")
order = [m for m, _ in calls if m in ("forwardMessage", "deleteMessage")]
say(fwd and fwd[0]["chat_id"] == GROUP and fwd[0]["message_thread_id"] == ARCHIVE
    and int(fwd[0]["message_id"]) == posting_msg,
    "переслано в «запощено» (из темы постинга)")
say(dele and int(dele[0]["message_id"]) == original["message_id"], "удалено из запасов — то самое сообщение")
say(order == ["forwardMessage", "deleteMessage"], f"сначала архив, потом удаление: {order}")
say("Переслал в «запощено», из «Запасы» удалил" in report, f"в отчёте: {report.splitlines()[-1]}")
say(not jobs.stock_find("AAA"), "запись о запасе снята")

print("\n--- вышло не во все сети ---")
failing.add("shorts")
_, job = from_stock("BBB")
jobs.set_targets(job["id"], "tiktok,shorts")
calls.clear()
report = post(job["id"])
say(not api("forwardMessage") and not api("deleteMessage"), "ни пересылки, ни удаления")
say("в запасах видео оставил" in report and jobs.stock_find("BBB"), "сказано, что осталось в запасах")
failing.clear()

print("\n--- в архив переслать не вышло ---")
_, job = from_stock("CCC")
src = jobs.tg_source(job)
stock_row = jobs.stock_find("CCC")[0]
state["forward_fail"] = {(GROUP, src["msg"]), (GROUP, stock_row["msg"])}
calls.clear()
report = post(job["id"])
say(not api("deleteMessage") and jobs.stock_find("CCC"),
    "из запасов НЕ удалено — иначе видео пропало бы совсем")
say("переслать не смог" in report and "не удалял" in report, f"сказано почему: {report.splitlines()[-1][:80]}")

print("\n--- сообщение в теме постинга уже удалили ---")
_, job = from_stock("DDD")
src = jobs.tg_source(job)
stock_row = jobs.stock_find("DDD")[0]
state["forward_fail"] = {(GROUP, src["msg"])}
calls.clear()
report = post(job["id"])
fwd = api("forwardMessage")
say(len(fwd) == 2 and int(fwd[1]["message_id"]) == stock_row["msg"],
    "переслано прямо из запасов")
say(api("deleteMessage") and "удалил" in report, "и потом удалено из запасов")
state["forward_fail"] = set()

print("\n--- нет права удалять ---")
_, job = from_stock("EEE")
state["delete_fail"] = "Bad Request: message can't be deleted"
calls.clear()
report = post(job["id"])
say(api("forwardMessage") and jobs.stock_find("EEE"), "в архив ушло, запись о запасе осталась")
say("удалить не смог" in report and "Удалением сообщений" in report and "вручную" in report,
    f"сказано, что делать: {report.splitlines()[-1][:90]}")
state["delete_fail"] = "Bad Request: message to delete not found"
_, job = from_stock("FFF")
report = post(job["id"])
say("удалил" in report and not jobs.stock_find("FFF"), "из запасов уже удалили руками — это не ошибка")
state["delete_fail"] = ""

print("\n--- видео лежало в запасах до обновления ---")
handle(message(POSTS, file="OLD", text="старое", forwarded=True))
job = last_job()
calls.clear()
report = post(job["id"])
say(api("forwardMessage") and not api("deleteMessage"), "в архив переслано, удалять нечего")
say("не нашёл" in report and "вручную" in report, "попросил удалить из запасов руками")

print("\n--- видео прислали файлом, не из запасов ---")
handle(message(POSTS, file="NEW", text="свежее"))
calls.clear()
report = post(last_job()["id"])
say(api("forwardMessage") and report.splitlines()[-1] == "📦 Переслал в «запощено».",
    f"всё запощенное — в архив, без лишних слов про запасы: {report.splitlines()[-1]}")

print("\n--- одно и то же видео в запасах дважды ---")
handle(message(STOCK, file="TWO", text="копия 1"))
_, job = from_stock("TWO")
calls.clear()
report = post(job["id"])
say(len(api("deleteMessage")) == 2 and "2 копии" in report, "удалены обе копии")

print("\n--- архив сняли ---")
calls.clear()
handle(message(ARCHIVE, OWNER, "/everywhere архив"))
say(bot.shelf_home("archive") is None and "Архив снят" in said(), "/everywhere архив — снят")
_, job = from_stock("GGG")
calls.clear()
report = post(job["id"])
say(not api("forwardMessage") and not api("deleteMessage") and jobs.stock_find("GGG"),
    "без архива из запасов не удаляем")
say("Архив не назначен" in report, "и подсказка, как назначить")

print("\n--- запасы сняли ---")
handle(message(STOCK, OWNER, "/everywhere запасы"))
calls.clear()
handle(message(STOCK, MEMBER, file="HHH", text="теперь это обычная тема"))
say(bot.shelf_home("stock") is None and not jobs.stock_find("HHH"), "запасы сняты — больше не запоминаем")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
