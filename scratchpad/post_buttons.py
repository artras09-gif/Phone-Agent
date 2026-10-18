"""Пост кнопками 2026-10-11: подпись = описание, сети и время — кнопками.

Жалоба на шаблон «куда/когда/описание»: «какая-то странная хуйня, как-то
проще можно?». Без Telegram: все вызовы API подменены.
"""
import datetime as dt
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config     # noqa: E402

tmp = tempfile.mkdtemp(prefix="pa_buttons_")
config.DB_PATH = os.path.join(tmp, "jobs.db")
config.QUEUE_DIR = os.path.join(tmp, "queue")
config.BASE = tmp
os.makedirs(config.QUEUE_DIR)

import jobs          # noqa: E402
import scheduler     # noqa: E402
import yttags  # noqa: E402
yttags.suggest = lambda caption, timeout=40: ["тег", "ещё тег"]  # без модели
import telegram_bot as bot  # noqa: E402

jobs._SCHEMA_READY = False
ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


calls, sent, started = [], [], []
bot._call = lambda method, params=None, timeout=70: (calls.append((method, dict(params or {})))
                                                      or {"ok": True, "result": {}})
bot.send = lambda text, **kw: sent.append(text) or True
bot.load_settings = lambda: {"token": "t", "owner": "1"}
video = os.path.join(config.QUEUE_DIR, "v.mp4")
# Каждое «видео» — своё содержимое: одинаковые байты программа теперь
# справедливо считает повтором того же видео (post_dupes).
bot._download = lambda file_id, dest, *more: (open(video, "wb").write(os.urandom(64)) and video)
scheduler.looks_like_video = lambda path: ""
bot._run = lambda title, work: started.append(title)


def markup_of(call):
    return json.loads(call[1]["reply_markup"])["inline_keyboard"]


def press(data, msg_id=77):
    calls.clear()
    bot._handle_callback({"id": "cb", "data": data,
                          "message": {"chat": {"id": 1}, "message_id": msg_id}})


print("--- видео с обычной подписью — черновик с кнопками ---")
caption = "Мой первый тикток про пиццу #еда\nвторая строка"
bot._handle({"chat": {"id": 1}, "video": {"file_id": "f", "file_size": 1000}, "caption": caption})
draft = [c for c in calls if c[0] == "sendMessage"]
job = jobs.by_id(1)
say(job["status"] == "draft" and job["caption"] == caption,
    "подпись целиком стала описанием (слово «тикток» не вырезано), задача — черновик")
say(jobs.due() is None and not jobs.pending(), "черновик не публикуется и не в очереди")
rows = markup_of(draft[-1])
say([b["text"] for b in rows[0]] == ["✅ TikTok", "✅ Reels", "✅ Shorts"], "сети — все отмечены")
say(any("Сейчас" in b["text"] for r in rows for b in r) and any("Своё время" in b["text"] for r in rows for b in r),
    "кнопки времени: «Сейчас» … «Своё время»")

print("\n--- снять сеть, выбрать время ---")
press("t:1:shorts")
say(jobs.by_id(1)["targets"] == "tiktok,reels", "Shorts снят")
edited = [c for c in calls if c[0] == "editMessageText"]
say(edited and "⬜ Shorts" in json.dumps(markup_of(edited[-1]), ensure_ascii=False),
    "сообщение перерисовано: ⬜ Shorts")
press("w:1:tom12")
job = jobs.by_id(1)
when = dt.datetime.fromtimestamp(job["run_at"])
say(job["status"] == "pending" and job["exact"] and when.hour == 12
    and when.date() == dt.date.today() + dt.timedelta(days=1), "«Завтра 12:00» — в очереди, точно ко времени")
text = [c for c in calls if c[0] == "editMessageText"][-1][1]["text"]
say("TikTok" in text and "Reels" in text and "Shorts" not in text and "завтра в 12:00" in text
    and "пиццу" in text, "подтверждение: сети, время, описание")
press("t:1:tiktok")
say(jobs.by_id(1)["targets"] == "tiktok,reels", "после выбора времени сети уже не меняются")
say(not os.path.exists(os.path.join(tmp, "bot_post.json")),
    "снятая галочка НЕ запоминается (2026-10-14: по умолчанию — все сети)")

print("\n--- следующее видео: снова все сети, «Своё время» ---")
bot._handle({"chat": {"id": 1}, "video": {"file_id": "f", "file_size": 1000}, "caption": "кот"})
rows = markup_of([c for c in calls if c[0] == "sendMessage"][-1])
say([b["text"] for b in rows[0]] == ["✅ TikTok", "✅ Reels", "✅ Shorts"], "снова отмечены все")
press("w:2:ask")
say(sent and "Напиши время" in sent[-1], "«Своё время» — просит написать")
sent.clear()
bot._handle({"chat": {"id": 1}, "text": "в восемь"})
say(sent and "Не понял" in sent[-1] and jobs.by_id(2)["status"] == "draft", "непонятное время — переспрос")
calls.clear()
bot._handle({"chat": {"id": 1}, "text": "завтра 9:30"})
job = jobs.by_id(2)
say(job["status"] == "pending" and dt.datetime.fromtimestamp(job["run_at"]).strftime("%H:%M") == "09:30",
    "«завтра 9:30» — поставлено")

print("\n--- «Сейчас» и отмена ---")
bot._handle({"chat": {"id": 1}, "video": {"file_id": "f", "file_size": 1000}, "caption": ""})
press("w:3:now")
say(started == ["публикация #3"] and not jobs.by_id(3)["exact"], "«Сейчас» — публикация пошла сразу")
bot._handle({"chat": {"id": 1}, "video": {"file_id": "f", "file_size": 1000}, "caption": "x"})
press("x:4")
say(jobs.by_id(4)["status"] == "cancelled", "«Отмена» в черновике — отменено")
press("x:2")
say(jobs.by_id(2)["status"] == "cancelled", "«Отменить пост» после выбора времени — отменено")
press("t:2:tiktok")
answers = [c for c in calls if c[0] == "answerCallbackQuery"]
say(answers and answers[-1][1]["text"] == "этот пост отменён", f"кнопка отменённого — ответ: «{answers[-1][1]['text']}»")
bot._handle({"chat": {"id": 1}, "video": {"file_id": "f", "file_size": 1000}, "caption": "y"})
press("t:5:tiktok")
press("t:5:reels")
press("t:5:shorts")
press("w:5:now")
answers = [c for c in calls if c[0] == "answerCallbackQuery"]
say(jobs.by_id(5)["status"] == "draft" and "хотя бы одну" in answers[-1][1]["text"],
    "ни одной сети — не публикую, прошу отметить")

print("\n--- YouTube: название — первая строка описания ---")
import postspec   # noqa: E402

say(postspec.shorts_title("Пицца за 5 минут\n#еда #рецепт\nподробности") == "Пицца за 5 минут",
    "первая строка")
posted = []
scheduler.poster.post = lambda path, text, target, extra=None: (posted.append((target, text)) or (True, "ok"))
j = jobs.add(video, "Пицца за 5 минут\n#еда #рецепт", targets="tiktok,shorts", exact=True)
scheduler.run_post_job(job_id=j)
say(posted == [("tiktok", "Пицца за 5 минут\n#еда #рецепт"), ("shorts", "Пицца за 5 минут")],
    "в TikTok — всё описание, в Shorts — первая строка")

print("\n--- забытые черновики убираются ---")
old = jobs.add(video, "забыл", targets="tiktok", status="draft")
with jobs.connect() as con:
    con.execute("UPDATE jobs SET created_at=? WHERE id=?", (0, old))
bot._drop_stale_drafts()
say(jobs.by_id(old)["status"] == "cancelled", "черновик старше двух дней отменён")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
