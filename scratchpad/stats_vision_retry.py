"""Модель зрения не ответила при съёмке статистики — без телефона.

19.11 в 22:15 (второй телефон) разом пропали TikTok, YouTube и Threads —
ровно те сети, чьё число читает модель по снимку, — а Instagram и Facebook
(число из дерева экрана) пришли. В чате: «числа нет, снимок в альбоме: за
вчера», Threads — «1 418 (за 7 дней)» вместо числа за день. Настоящая
причина была только в журнале того компьютера.

Теперь: после съёмки числа дочитываются с сохранённых снимков с паузами,
а не вышло — в чате причина, а не «числа нет».
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402

tmp = tempfile.mkdtemp(prefix="pa_vretry_")
config.BASE = tmp
config.DB_PATH = os.path.join(tmp, "jobs.db")
config.QUEUE_DIR = os.path.join(tmp, "queue")
config.SERIAL = None

import accstats  # noqa: E402
import vision    # noqa: E402

ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


sleeps = []
accstats.time.sleep = lambda s: sleeps.append(s)
vision.available = lambda: (True, "test-model")

ANSWERS = {  # что «модель» отвечает на вопрос каждой сети
    accstats.TT_ASK: '{"период": "Nov 18 - Nov 18", "просмотры": "78"}',
    accstats.YT_ASK: '{"подсказка": "Nov 18, 2026: 3", "итог": "2,574"}',
    accstats.TH_ASK: '{"день": 18, "месяц": 11, "просмотры": "2"}',
}
state = {"fail": 0, "calls": 0}


def fake_ask(png, prompt, **kw):
    state["calls"] += 1
    if state["fail"]:
        state["fail"] -= 1
        raise vision.VisionError("HTTP 429: Too Many Requests")
    return ANSWERS[prompt]


vision.ask = fake_ask
PNG = b"\x89PNG" + b"0" * 20_000


def fresh_folder(name):
    """Папка дня, как её оставила съёмка при упавшей модели."""
    folder = os.path.join(tmp, name)
    os.makedirs(folder)
    for f in ("tiktok.png", "youtube.png", "threads.png", accstats.TH_TIP_FILE,
              "instagram.png", "facebook.png"):
        with open(os.path.join(folder, f), "wb") as fh:
            fh.write(PNG)
    accstats._note(folder, "tiktok", "за вчера")
    accstats._note(folder, "tiktok_miss", "за вчера")
    accstats._note(folder, "youtube", "за вчера: просмотры за последний посчитанный день")
    accstats._note(folder, "youtube_period", "28 дней")
    accstats._note(folder, "youtube_miss", "за вчера: просмотры за последний посчитанный день")
    accstats._views_note(folder, "threads", "1 418", "7 дней")
    accstats._views_note(folder, "instagram", "5", "18.11")
    accstats._views_note(folder, "facebook", "7", "18.11")
    return folder


NETS = ["reels", "tiktok", "shorts", "facebook", "threads"]

print("Модель ожила со второй попытки")
folder = fresh_folder("a")
state.update(fail=3, calls=0)          # первая попытка: все три сети падают
accstats.VISION_MISS.clear()
log = []
accstats._reread(folder, NETS, log)
text = accstats.numbers_text(folder)
print("   " + text.replace("\n", "\n   "))
say("TikTok — 78" in text, "TikTok дочитан")
say("YouTube — 3" in text, "YouTube дочитан (из подсказки графика)")
say("Threads — 2 (за 18.11)" in text, "Threads — число за день, а не за 7 дней")
say("числа нет" not in text and "не прочитал" not in text, "ни одной строки «числа нет»")
say(not os.path.exists(os.path.join(folder, accstats.TH_TIP_FILE)),
    "снимок с подсказкой Threads ушёл в альбом вместо снимка за 7 дней")
say(sleeps == [0, 45], f"ждал 0 и 45 с, дальше не ждал ({sleeps})")
say(any("дочитал" in line for line in log), "в журнале — что дочитал")

print("Модель так и не ответила")
folder = fresh_folder("b")
state.update(fail=10**6, calls=0)
sleeps.clear()
accstats.VISION_MISS.clear()
log = []
accstats._reread(folder, NETS, log)
text = accstats.numbers_text(folder)
print("   " + text.replace("\n", "\n   "))
say("TikTok — числа нет, снимок в альбоме: модель зрения не ответила: HTTP 429" in text,
    "TikTok: причина, а не «за вчера»")
say("YouTube — числа нет, снимок в альбоме: модель зрения не ответила: HTTP 429" in text,
    "YouTube: причина")
say("Threads — 1 418 (за 7 дней) — за день не прочитал: модель зрения не ответила: HTTP 429" in text,
    "Threads: видно, что 7 дней — это запасное, и почему")
say("Instagram — 5" in text and "Facebook — 7" in text, "Instagram и Facebook не тронуты")
say(sleeps == list(accstats.REREAD_PAUSES), f"три попытки с паузами ({sleeps})")

print("Зрение не настроено — не ждём попусту")
folder = fresh_folder("c")
sleeps.clear()
vision.available = lambda: (False, "выключено")
accstats._reread(folder, NETS, [])
say(sleeps == [0], f"одна попытка без пауз ({sleeps})")
vision.available = lambda: (True, "test-model")

print("Модель ответила, но на снимке прочерки — причину «модель не ответила» не придумываем")
folder = fresh_folder("d")
ANSWERS[accstats.TT_ASK] = '{"период": "Nov 19 - Nov 19", "просмотры": "-"}'
state.update(fail=0)
accstats.VISION_MISS.clear()
accstats._reread(folder, ["tiktok"], [])
text = accstats.numbers_text(folder, ["tiktok"])
say("TikTok — числа нет, снимок в альбоме: за вчера" in text, f"прежняя пометка осталась ({text})")

print("Всё уже прочитано — ничего не делаем")
folder = os.path.join(tmp, "e")
os.makedirs(folder)
accstats._views_note(folder, "tiktok", "3", "17.11")
state.update(calls=0)
sleeps.clear()
accstats._reread(folder, NETS, [])
say(state["calls"] == 0 and not sleeps, "модель не спрашивали, не ждали")

print("ВСЁ ХОРОШО" if ok else "ЕСТЬ ОШИБКИ")
sys.exit(0 if ok else 1)
