"""Постинг 2026-10-11: шаблон подписи, точное время, очередь, расписание.

Просьба: «какая система у поста должна быть, чтобы бот именно всё, что
нужно, запостил с нужным описанием и в нужное время». Без телефона и без
Telegram: публикация и отправка сообщений подменены.
"""
import datetime as dt
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config     # noqa: E402

tmp = tempfile.mkdtemp(prefix="pa_post_")
config.DB_PATH = os.path.join(tmp, "jobs.db")
config.QUEUE_DIR = os.path.join(tmp, "queue")
config.WATCH_DIR = os.path.join(tmp, "watch")
os.makedirs(config.QUEUE_DIR)
os.makedirs(config.WATCH_DIR)

import jobs       # noqa: E402
import postspec   # noqa: E402

jobs._SCHEMA_READY = False
ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


now = dt.datetime(2026, 10, 11, 20, 0)          # воскресенье, вечер

print("--- время ---")
cases = {
    "18:30": dt.datetime(2026, 10, 12, 18, 30),        # прошло — значит завтра
    "21:15": dt.datetime(2026, 10, 11, 21, 15),
    "завтра 9:00": dt.datetime(2026, 10, 12, 9, 0),
    "послезавтра 9:00": dt.datetime(2026, 10, 13, 9, 0),
    "12.10 18:30": dt.datetime(2026, 10, 12, 18, 30),   # дата не путается со временем
    "05.01 10:00": dt.datetime(2027, 1, 5, 10, 0),      # дата в прошлом без года — следующий год
    "пт 20:00": dt.datetime(2026, 10, 16, 20, 0),
    "вс 19:00": dt.datetime(2026, 10, 18, 19, 0),       # сегодня прошло — через неделю
}
for text, want in cases.items():
    got, exact = postspec.parse_when(text, now)
    say(got == want and exact, f"«{text}» -> {got:%d.%m %H:%M}")
say(postspec.parse_when("сейчас", now) == (None, False), "«сейчас» — сразу")
for bad in ("сегодня 19:00", "завтра", "в восемь", "32.10 10:00"):
    try:
        postspec.parse_when(bad, now)
        say(False, f"«{bad}» должно быть ошибкой")
    except postspec.SpecError as e:
        say(True, f"«{bad}» — понятная ошибка: {e}")

print("\n--- шаблон ---")
text = """куда: тикток и рилс
когда: завтра 18:30
описание:
Мой первый тикток про пиццу #еда
вторая строка с ссылкой instagram.com/reel/AbC"""
spec = postspec.parse(text, now)
say(spec["template"] and spec["targets"] == ["tiktok", "reels"], "сети из «куда»")
say(spec["when"] == dt.datetime(2026, 10, 12, 18, 30) and spec["exact"], "время из «когда»")
say(spec["caption"] == "Мой первый тикток про пиццу #еда\nвторая строка с ссылкой "
    "instagram.com/reel/AbC", "описание дословно: слово «тикток» и ссылка не тронуты")
spec2 = postspec.parse("описание: Общий текст\nописание шортс: Короткое название", now)
say(spec2["targets"] == ["tiktok", "shorts", "reels"] and spec2["when"] is None,
    "без «куда» и «когда» — во все сети и сразу")
say(postspec.caption_for(spec2, "shorts") == "Короткое название"
    and postspec.caption_for(spec2, "tiktok") == "Общий текст", "своё описание для шортс")
spec3 = postspec.parse("куда: тикток\nописание шортс: x\nописание: y", now)
say(any("в «куда» этой сети нет" in n for n in spec3["notes"]), "описание для сети не из «куда» — предупреждение")
spec4 = postspec.parse("куда: все\nописание: " + "слово " * 30, now)
say(any("YouTube Shorts" in n for n in spec4["notes"]), "длинное название YouTube — предупреждение")
say(len(postspec.fit(spec4["caption"], "shorts")) <= 100
    and not postspec.fit(spec4["caption"], "shorts").endswith("сло"), "обрезка по целому слову")
try:
    postspec.parse("куда: вк\nописание: x", now)
    say(False, "неизвестная сеть должна быть ошибкой")
except postspec.SpecError as e:
    say("вк" in str(e), f"неизвестная сеть: {e}")
free = postspec.parse("смешной кот #коты тикток в 21:00", now)
say(not free["template"] and free["targets"] == ["tiktok"] and free["caption"] == "смешной кот #коты"
    and free["when"] == dt.datetime(2026, 10, 11, 21, 0), "свободная подпись — как раньше")
shown = postspec.describe(spec, 7, now)
say("Задача #7" in shown and "Куда: TikTok, Instagram Reels" in shown
    and "Когда: завтра в 18:30" in shown and "#еда" in shown, "подтверждение: сети, время, текст")
print("     " + shown.replace("\n", "\n     "))

print("\n--- очередь: своё описание, лимит, точное время ---")
import scheduler  # noqa: E402

posted = []
scheduler.poster.post = lambda path, text, target: (posted.append((target, text)) or (True, f"{target}: ok"))
video = os.path.join(config.QUEUE_DIR, "v.mp4")
open(video, "wb").write(b"x")
job = jobs.add(video, "Общий текст", targets="tiktok,shorts",
               captions={"shorts": "Название"}, exact=True)
scheduler.run_post_job(job_id=job)
say(posted == [("tiktok", "Общий текст"), ("shorts", "Название")], f"в каждую сеть своё: {posted}")

config.MAX_POSTS_PER_DAY = 1
told = []
posted.clear()
late = jobs.add(video, "сверх лимита", targets="tiktok")
msg = scheduler.run_post_job(notify=told.append)
row = jobs.by_id(late)
moved_to = dt.datetime.fromtimestamp(row["run_at"])
say(not posted and row["status"] == "pending" and moved_to.date() == dt.date.today() + dt.timedelta(days=1),
    f"лимит: не публикую, переношу на завтра в {moved_to:%H:%M} — «{msg}»")
say(told and "завтра" in told[0], "и говорю об этом в бот")
say(jobs.due() is None, "до завтра задача не «подошла» — сторож не дёргается каждые 10 с")
exact = jobs.add(video, "ко времени", targets="tiktok", exact=True)
scheduler.run_post_job(job_id=exact)
say(posted == [("tiktok", "ко времени")], "пост с точным временем выходит сверх лимита")

scheduler.poster.post = lambda *a: (_ for _ in ()).throw(RuntimeError("телефон отвалился"))
broken = jobs.add(video, "сломается", targets="tiktok", exact=True)
scheduler.run_post_job(job_id=broken)
say(jobs.by_id(broken)["status"] == "failed", "падение посреди публикации — задача «failed», а не вечно ждёт")

print("\n--- отмена ---")
gone = jobs.add(video, "отменю", targets="tiktok", run_at=time.time() + 3600)
say(jobs.cancel(gone) is not None and jobs.by_id(gone)["status"] == "cancelled", "/cancel убирает задачу")
say(jobs.cancel(gone) is None, "второй раз — «такой нет»")

print("\n--- папка watch: .txt тем же шаблоном ---")
open(os.path.join(config.WATCH_DIR, "clip.mp4"), "wb").write(b"\x00\x00\x00\x18ftypmp42" + b"0" * 4096)
open(os.path.join(config.WATCH_DIR, "clip.txt"), "w", encoding="utf-8").write(
    "куда: рилс\nкогда: завтра 10:00\nописание:\nиз папки")
scheduler.looks_like_video = lambda path: ""
added = scheduler.scan_watch_dir()
row = jobs.by_id(added[0][0]) if added else None
say(row is not None and row["targets"] == "reels" and row["caption"] == "из папки" and row["exact"],
    "видео из watch/ с шаблоном в .txt")

print("\n--- бот: подпись разобрана, подтверждение — ровно то, что уйдёт ---")
import telegram_bot  # noqa: E402

sent = []
telegram_bot.send = lambda text, **kw: sent.append(text)
telegram_bot.load_settings = lambda: {"token": "t", "owner": "1"}
telegram_bot._download = lambda file_id, dest: video
scheduler.looks_like_video = lambda path: ""
telegram_bot._handle({"chat": {"id": 1}, "video": {"file_id": "f", "file_size": 5_000_000},
                      "caption": "куда: шортс\nкогда: 23:59\nописание:\nПицца #еда"})
last = sent[-1]
say("YouTube Shorts" in last and "23:59" in last and "Пицца #еда" in last and "/cancel" in last,
    "подтверждение: сети, время, текст и как отменить")
sent.clear()
telegram_bot._handle({"chat": {"id": 1}, "video": {"file_id": "f", "file_size": 40 * 1024 * 1024},
                      "caption": "кот"})
say(sent and "20 МБ" in sent[-1], "файл 40 МБ — сразу сказано про предел Telegram")
sent.clear()
telegram_bot._handle({"chat": {"id": 1}, "video": {"file_id": "f", "file_size": 1000},
                      "caption": "когда: завтра\nописание: x"})
say(sent and "Не понял подпись" in sent[-1], "кривое время — переспрашиваю, а не публикую")
sent.clear()
telegram_bot._handle({"chat": {"id": 1}, "text": "/queue"})
say(sent and "В очереди" in sent[-1], "/queue показывает очередь")
sent.clear()
telegram_bot._handle({"chat": {"id": 1}, "text": "/template"})
say(sent and "куда:" in sent[-1] and "когда:" in sent[-1], "/template присылает шаблон")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
