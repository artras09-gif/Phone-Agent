"""Подпись как у пользователя: первая строка — название YouTube, дальше
описание; по умолчанию — во все сети, «без ютуба» исключает (2026-10-14).

Без Telegram и без телефона: API бота и публикация подменены.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config     # noqa: E402

tmp = tempfile.mkdtemp(prefix="pa_exclude_")
config.DB_PATH = os.path.join(tmp, "jobs.db")
config.QUEUE_DIR = os.path.join(tmp, "queue")
config.BASE = tmp
os.makedirs(config.QUEUE_DIR)

import jobs          # noqa: E402
import postspec      # noqa: E402
import poster        # noqa: E402
import scheduler     # noqa: E402
import telegram_bot as bot  # noqa: E402

jobs._SCHEMA_READY = False
ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


calls, sent = [], []
bot._call = lambda method, params=None, timeout=70: (calls.append((method, dict(params or {})))
                                                      or {"ok": True, "result": {}})
bot.send = lambda text, **kw: sent.append(text) or True
bot.load_settings = lambda: {"token": "t", "owner": "1"}
video = os.path.join(config.QUEUE_DIR, "v.mp4")
# Каждое «видео» — своё содержимое: одинаковые байты программа теперь
# справедливо считает повтором того же видео (post_dupes).
bot._download = lambda file_id, dest, *more: (open(video, "wb").write(os.urandom(64)) and video)
scheduler.looks_like_video = lambda path: ""
bot._run = lambda title, work: work()

TITLE = "🇺🇸 CERO PREOCUPACIONES"
BODY = ("Donald Trump volvió a sorprender con su actitud despreocupada: «La verdad es "
        "que no me preocupa nada. Pase lo que pase... ¿Saben cuál es mi actitud? Me da igual».\n\n"
        "❓ ¿Es una muestra de confianza absoluta o simplemente indiferencia?")
POST = TITLE + "\n\n" + BODY

posted = {}
poster.post = lambda path, text, target: (posted.__setitem__(target, text) or (True, f"{target}: ok"))


def send_video(caption):
    calls.clear()
    bot._handle({"chat": {"id": 1}, "video": {"file_id": "f", "file_size": 1000},
                 "caption": caption})
    return jobs.by_id(max(r["id"] for r in jobs.connect().execute("SELECT id FROM jobs")))


print("--- подпись пользователя без указаний — во все три ---")
job = send_video(POST)
say(job["targets"] == "tiktok,reels,shorts", f"сети: {job['targets']}")
say(job["caption"] == POST, "описание сохранено как есть, с эмодзи и испанскими знаками")
draft = [c for c in calls if c[0] == "sendMessage"][-1][1]["text"]
say(f"Название на YouTube: {TITLE}" in draft, "в черновике видно, какое будет название на YouTube")
bot._handle_callback({"id": "cb", "data": f"w:{job['id']}:now",
                      "message": {"chat": {"id": 1}, "message_id": 5}})
say(posted.get("shorts") == TITLE, f"YouTube: название = первая строка: {posted.get('shorts')!r}")
say(posted.get("tiktok") == POST and posted.get("reels") == POST,
    "TikTok и Instagram: весь текст, название + описание")

print("\n--- «без ютуба» — всё, кроме YouTube, строка в пост не попадает ---")
posted.clear()
job = send_video(POST + "\nбез ютуба")
say(job["targets"] == "tiktok,reels", f"сети: {job['targets']}")
say(job["caption"] == POST, "строка «без ютуба» вырезана из описания")
draft = [c for c in calls if c[0] == "sendMessage"][-1][1]["text"]
say("Не публикую в: YouTube Shorts" in draft, "в черновике сказано, куда не публикую")

for line, want in (("кроме инсты и тиктока", "shorts"), ("- yt", "tiktok,reels"),
                   ("не в рилс", "tiktok,shorts"), ("❌ тикток", "reels,shorts"),
                   ("минус инстаграм", "tiktok,shorts")):
    job = send_video(POST + "\n" + line)
    say(job["targets"] == want and job["caption"] == POST, f"«{line}» -> {job['targets']}")

print("\n--- обычный текст со словами «без» и «не» — не исключение ---")
for line in ("без слов", "Не знаю, что сказать", "без комментариев #trump",
             "не пропусти новый выпуск в тикток"):
    job = send_video(POST + "\n" + line)
    say(job["targets"] == "tiktok,reels,shorts" and job["caption"].endswith(line),
        f"«{line}» — остался в описании, сети все")

sent.clear()
before = jobs.connect().execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
bot._handle({"chat": {"id": 1}, "video": {"file_id": "f", "file_size": 1000},
             "caption": POST + "\nбез тиктока, инсты и ютуба"})
after = jobs.connect().execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
say(after == before and sent and "некуда" in sent[-1], "исключены все — пост не создан, сказано почему")

print("\n--- шаблон и папка watch понимают то же ---")
spec = postspec.parse("куда: все кроме ютуба\nописание:\n" + POST)
say(spec["targets"] == ["tiktok", "reels"] and spec["caption"] == POST, "«куда: все кроме ютуба»")
spec = postspec.parse("куда: без инсты\nописание: текст")
say(spec["targets"] == ["tiktok", "shorts"], "«куда: без инсты»")
spec = postspec.parse(POST + "\nбез ютуба")
say(spec["targets"] == ["tiktok", "reels"] and "без ютуба" not in spec["caption"],
    ".txt в watch: «без ютуба» тоже работает")
say(postspec.shorts_title(POST) == TITLE, "название YouTube — первая непустая строка")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
