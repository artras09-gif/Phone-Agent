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
import time

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
POSTS, STOCK, ARCHIVE, PLAIN = 10, 20, 30, 40
NAMES = {POSTS: "Постинг для бота", STOCK: "Запасы", ARCHIVE: "запощено", PLAIN: "Готовое"}

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


def message(thread, sender=MEMBER, text="", file=None, forwarded=False, date=None, reply=None):
    msg = {"chat": {"id": int(GROUP), "type": "supergroup", "title": "11111", "is_forum": True},
           "from": {"id": int(sender)}, "message_id": next(msg_ids),
           "message_thread_id": thread, "is_topic_message": True,
           # По умолчанию — только что: удалять старше 48 часов Telegram не даёт.
           "date": date or int(time.time()) - 60,
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
    if reply:
        msg["reply_to_message"] = reply
    return msg


def press(data, thread=POSTS, sender=OWNER):
    """Нажатие кнопки под сообщением бота в теме."""
    try:
        bot._handle_callback({"id": "cb", "data": data, "from": {"id": int(sender)},
                              "message": {"chat": {"id": int(GROUP), "type": "supergroup"},
                                          "message_id": 4000, "message_thread_id": thread,
                                          "is_topic_message": True}})
    finally:
        bot.set_reply(None)


def buttons():
    """Данные кнопок всех сообщений, отправленных с последней очистки."""
    import json as _json
    out = []
    for m, p in calls:
        if m == "sendMessage" and p.get("reply_markup"):
            for row in _json.loads(p["reply_markup"]).get("inline_keyboard", []):
                out += [b.get("callback_data", "") for b in row]
    return out


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


print("--- тема называется «Запасы» — она и есть запасы, без команды ---")
handle(message(POSTS, OWNER, "/here видео"))
calls.clear()
first = handle(message(STOCK, file="AUTO", text="положил в запасы"))
say(bot.shelf_home("stock") == (GROUP, STOCK, "Запасы"), f"запасы назначены сами: {bot.shelf_home('stock')}")
say("называется как запасы" in said() and "/everywhere запасы" in said(),
    "бот сказал об этом и как отменить")
say(last_job() is None and jobs.stock_find("AUTO"),
    "видео НЕ стало черновиком на постинг, а запомнено как запас (живой случай 12.11)")
calls.clear()
handle(message(ARCHIVE, MEMBER, "сюда складываем"))
say(bot.shelf_home("archive") == (GROUP, ARCHIVE, "запощено") and "называется как архив" in said(),
    "тема «запощено» стала архивом при первом сообщении в ней")
calls.clear()
handle(message(PLAIN, MEMBER, "просто болтаем"))
say("называется" not in said(), "тема с обычным названием полкой не становится")
handle(message(STOCK, OWNER, "/everywhere запасы"))
handle(message(ARCHIVE, OWNER, "/everywhere архив"))
calls.clear()
handle(message(STOCK, file="AUTO2", text="ещё одно"))
say(bot.shelf_home("stock") is None and "называется" not in said(),
    "сняли руками — по названию обратно не назначается")
for stale in jobs.connect().execute("SELECT id FROM jobs").fetchall():
    jobs.cancel(stale["id"])                       # черновик от AUTO2 — убрать
with jobs.connect() as con:
    con.execute("DELETE FROM jobs")
    con.execute("DELETE FROM stock")

print("\n--- темы назначаются командами ---")
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
original, job = from_stock("BBB")
jobs.set_targets(job["id"], "tiktok,shorts")
calls.clear()
report = post(job["id"])
say(not api("forwardMessage") and not api("deleteMessage"), "ни пересылки, ни удаления")
say("в запасах видео оставил" in report and jobs.stock_find("BBB"), "сказано, что осталось в запасах")
job = jobs.by_id(job["id"])
say(jobs.went_of(job) == ["tiktok"] and jobs.left_of(job) == ["shorts"],
    f"запомнено, куда вышло и куда нет: {job['went']!r}")
say(f"r:{job['id']}" in buttons() and "Не вышел: YouTube Shorts" in said(),
    "кнопка «Повторить» и список сетей, куда не вышло")

print("\n--- «Повторить»: только туда, куда не вышло, и после этого — в архив ---")
failing.clear()
calls.clear()
press(f"r:{job['id']}")
again = last_job()
say(again["id"] != job["id"] and again["targets"] == "shorts"
    and jobs.tg_source(again) == jobs.tg_source(job),
    f"новая задача — только YouTube, с тем же исходным сообщением: {again['targets']}")
calls.clear()
report = post(again["id"])
say(api("forwardMessage") and api("deleteMessage")
    and int(api("deleteMessage")[0]["message_id"]) == original["message_id"]
    and not jobs.stock_find("BBB"),
    "повтор вышел — видео ушло в архив и удалено из запасов")
calls.clear()
press(f"r:{job['id']}")
say(last_job()["id"] == again["id"]
    and any("уже повторял" in p.get("text", "") for p in api("answerCallbackQuery")),
    "вторая «Повторить» под тем же сообщением ничего не ставит")

print("\n--- после сбоя видео прислали заново — сети, где оно уже есть, не отмечены ---")
failing.add("reels")
handle(message(POSTS, file="RE1", text="упадёт в инсте"))
job = last_job()
jobs.set_targets(job["id"], "tiktok,reels,shorts")
post(job["id"])
failing.clear()
same = job["video_path"]
bot._download = lambda file_id, dest, *more: same          # тот же файл — тот же отпечаток
calls.clear()
handle(message(POSTS, file="RE1", text="упадёт в инсте"))
bot._download = fake_download
redo = last_job()
say(redo["id"] != job["id"] and "tiktok" not in redo["targets"].split(",")
    and "shorts" not in redo["targets"].split(",") and "reels" in redo["targets"].split(","),
    f"в черновике отмечено только то, куда не вышло: {redo['targets']}")
say("уже вышло в: TikTok, YouTube Shorts" in said(), "и сказано почему")
jobs.cancel(redo["id"])

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
state["can_delete"] = False
calls.clear()
report = post(job["id"])
say(api("forwardMessage") and jobs.stock_find("EEE"), "в архив ушло, запись о запасе осталась")
say("удалить не смог" in report and "Удалением сообщений" in report and "вручную" in report,
    f"сказано, что делать: {report.splitlines()[-1][:90]}")
say(not api("setMessageReaction"), "реакцией не помечаем: дело в правах, а не в возрасте")
state["can_delete"] = True

print("\n--- видео лежит в запасах дольше 48 часов: Telegram ботам такое удалять не даёт ---")
old = handle(message(STOCK, file="AGED", text="давнее", date=int(time.time()) - 5 * 86400))
handle(message(POSTS, file="AGED", text="давнее", forwarded=True))
calls.clear()
report = post(last_job()["id"])
mark = api("setMessageReaction")
say(api("forwardMessage") and "48 часов" in report and "вручную" in report
    and "Удалением сообщений" not in report,
    f"в архив ушло, причина названа честно: {report.splitlines()[-1][:110]}")
say(mark and int(mark[0]["message_id"]) == old["message_id"] and bot.REACTION_DONE in str(__import__("json").loads(mark[0]["reaction"])),
    "видео в запасах помечено реакцией — видно, что оно вышло")
say(not jobs.stock_find("AGED"), "и больше не числится в запасах")
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

print("\n--- видео лежало в запасах до бота: «/post» ответом на него ---")
ancient = {"message_id": 77, "from": {"id": int(MEMBER)}, "date": int(time.time()) - 3600,
           "caption": "лежало тут до бота",
           "video": {"file_id": "id-ANC", "file_unique_id": "ANC", "file_size": 1000}}
calls.clear()
handle(message(STOCK, MEMBER, "/post", reply=ancient))
job = last_job()
src = jobs.tg_source(job)
say(job["status"] == "draft" and job["caption"] == "лежало тут до бота"
    and src.get("msg") == 77 and not src.get("fwd"),
    f"черновик из самого сообщения запасов: {src}")
drafts = [p for p in api("sendMessage") if p.get("reply_markup")]
say(drafts and drafts[0].get("message_thread_id") == POSTS and job["chat"] == f"{GROUP}:{POSTS}",
    "черновик и отчёты — в теме постинга, в запасах бот молчит")
calls.clear()
report = post(job["id"])
say(api("forwardMessage") and int(api("forwardMessage")[0]["message_id"]) == 77
    and api("deleteMessage") and int(api("deleteMessage")[0]["message_id"]) == 77
    and "удалил" in report,
    "после выхода — в архив и из запасов прочь, хотя бот этого видео раньше не видел")
calls.clear()
handle(message(STOCK, MEMBER, "/post"))
say("ответь командой /post на само видео" in said() and last_job()["id"] == job["id"],
    "«/post» без видео — подсказка, а не черновик")

print("\n--- то же видео числится в запасах ДРУГОГО бота (база у телефонов общая) ---")
jobs.stock_add("SHARED", "-100999", "5", 555)
_, job = from_stock("SHARED")
calls.clear()
report = post(job["id"])
say([str(p["chat_id"]) for p in api("deleteMessage")] == [GROUP]
    and [r["chat"] for r in jobs.stock_find("SHARED")] == ["-100999"],
    "удалено только из своих запасов, чужая запись не тронута")

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

print("\n--- полок нет вовсе, а видео переслано — подсказка один раз, а не молчание ---")
handle(message(POSTS, file="HINT1", text="переслал", forwarded=True))
report = post(last_job()["id"])
say("назови темы «Запасы» и «Архив»" in report, "бот объяснил, как включить архив и запасы")
handle(message(POSTS, file="HINT2", text="переслал ещё", forwarded=True))
report = post(last_job()["id"])
say("назови темы" not in report, "во второй раз не повторяет")

print("\n--- видео прислали в тему ДО того, как её назначили запасами ---")
handle(message(POSTS, OWNER, "/everywhere"))                # бот снова берёт видео из любой темы
early = handle(message(PLAIN, file="EARLY", text="положил заранее"))
say(last_job()["status"] == "draft" and jobs.tg_source(last_job()).get("msg") == early["message_id"],
    "тема обычная — бот взял видео на постинг")
calls.clear()
handle(message(PLAIN, OWNER, "/here запасы"))
rows = jobs.stock_find("EARLY")
say(rows and rows[0]["msg"] == early["message_id"] and "тоже запомнил" in said(),
    "назначили запасами — уже взятое отсюда видео запомнено как запас")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
