"""Несколько телефонов — несколько ботов (2026-10-16: «для нескольких телефонов
нужно будет несколько ботов, продумай эту механику»).

Главное, что проверяется: видео из бота телефона попадает в очередь ЭТОГО
телефона и ни в какую другую; один бот не достаётся двум телефонам; общий бот
прежних версий продолжает работать у одного телефона, как было.
"""
import json
import os
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402

tmp = tempfile.mkdtemp(prefix="pa_phones_")
config.BASE = tmp
config.DB_PATH = os.path.join(tmp, "jobs.db")
config.SERIAL = None

import devices        # noqa: E402

devices.ROOT = os.path.join(tmp, "devices")
devices.NAMES = os.path.join(devices.ROOT, "names.json")

import fleet          # noqa: E402
import jobs           # noqa: E402
import scheduler      # noqa: E402
import telegram_bot as bot  # noqa: E402

jobs._SCHEMA_READY = False
ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


A, B, C = "phoneA", "phoneB", "phoneC"
TOKEN_A = "7000000001:" + "A" * 35
TOKEN_B = "7000000002:" + "B" * 35
devices.remember(A, "Redmi")
devices.remember(B, "Pixel")
devices.remember(C, "Samsung")


def on(serial):
    devices.use(serial)


print("--- прежняя установка: один общий бот ---")
on(A)
bot.save_settings(TOKEN_A, "555")
shared = json.load(open(bot.shared_path(), encoding="utf-8"))
say(shared["token"] == TOKEN_A and shared.get("phone") == A,
    "один телефон — бот в общем файле, как раньше, и он за этим телефоном")
say(bot.load_settings()["token"] == TOKEN_A, "телефон A видит своего бота")

print("\n--- второй телефон без своего бота ---")
on(B)
say(bot.load_settings()["token"] == "", "у B бота нет — общий бот уже у A, B его НЕ берёт")
say(bot.taken_by(TOKEN_A) == A, "бот A при попытке вставить его на B — «уже у телефона A»")

print("\n--- свой бот у второго телефона ---")
bot.save_settings(TOKEN_B, "")
mine = json.load(open(bot.settings_path(), encoding="utf-8"))
say(bot.settings_path().endswith(os.path.join(devices.slug(B), "telegram.json"))
    and mine["token"] == TOKEN_B and mine["phone"] == B, "бот B — в папке телефона B")
say(mine["owner"] == "555", "владелец тот же человек — не ждём первого написавшего")
say(json.load(open(bot.shared_path(), encoding="utf-8"))["token"] == TOKEN_A,
    "общий файл (бот A) не тронут")
on(A)
say(bot.load_settings()["token"] == TOKEN_A, "A по-прежнему со своим ботом")
names = sorted((b["serial"], bot._bot_of(b["token"])) for b in bot.bots())
say(names == [(A, "7000000001"), (B, "7000000002")], f"список ботов программы: {names}")

print("\n--- группы — у каждого бота свои ---")
on(B)
bot.allow_group("-100", "Команда Pixel")
on(A)
say(bot.allowed_groups() == {}, "группа бота B не видна боту A")
on(B)
say("-100" in bot.allowed_groups(), "у бота B — своя")

print("\n--- видео ложится в очередь своего телефона ---")
on(A)
queue_a = config.QUEUE_DIR
video = os.path.join(tmp, "v.mp4")
open(video, "wb").write(b"\x00\x00\x00\x18ftypmp42" + os.urandom(70 * 1024))
moved = threading.Event()


def slow_download(file_id, dest_dir, size_mb=0):
    on(B)                                  # пока качалось, окно переключили на B
    moved.set()
    dest = os.path.join(dest_dir, "got.mp4")
    open(dest, "wb").write(open(video, "rb").read())
    return dest


bot._download = slow_download
bot.send = lambda *a, **k: True
bot._send_draft = lambda job_id: None
where = (config.QUEUE_DIR, jobs.DEVICE)
bot._take_video({"file_id": "f"}, "пост с A", None, 1, where)
row = jobs.connect().execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 1").fetchone()
say(moved.is_set() and row["device"] == A and row["video_path"].startswith(queue_a),
    f"переключили телефон посреди скачивания — задача всё равно у A ({row['device']})")
on(A)
say(any(j["id"] == row["id"] for j in jobs.connect().execute(
    "SELECT id FROM jobs WHERE status='draft'" + jobs._mine()[0], jobs._mine()[1]).fetchall()),
    "очередь A её видит")
on(B)
seen_b = [j["id"] for j in jobs.connect().execute(
    "SELECT id FROM jobs WHERE status='draft' AND device=?", (B,)).fetchall()]
say(row["id"] not in seen_b, "у B её нет")

print("\n--- замок — у каждого бота свой ---")
ga = fleet.telegram_gate(bot._bot_of(TOKEN_A))
ga.acquire(timeout=0.5)
say(fleet.bot_busy(bot._bot_of(TOKEN_A)), "бот A занят этим процессом")
say(not fleet.bot_busy(bot._bot_of(TOKEN_B)), "бот B свободен — его возьмёт служба телефона B")
ga.release()

print("\n--- замок бота отпускается из любого потока ---")
held = fleet.telegram_gate("7000000009")
threading.Thread(target=lambda: held.acquire(timeout=0.5)).start()
time.sleep(0.3)
say(fleet.bot_busy("7000000009"), "взяли в одном потоке — занят")
t = threading.Thread(target=held.release)
t.start(); t.join()
say(not fleet.bot_busy("7000000009"), "отпустили из ДРУГОГО потока — свободен (раньше оставался занят)")
again = fleet.telegram_gate("7000000009")
again.acquire(timeout=0.5)
say(again.held(), "снова взяли — без ложного «бота ведёт другой процесс»")
again.release()

print("\n--- служба телефона: «Сейчас» публикует цикл службы, не поток бота ---")
said = []
bot.send = lambda text, **k: said.append(text) or True
bot.set_submit(bot.leave_to_scheduler)
started = bot._run("публикация #1", lambda: said.append("ОПУБЛИКОВАЛ В ПОТОКЕ БОТА"))
say(started is False and "ОПУБЛИКОВАЛ В ПОТОКЕ БОТА" not in said
    and any("служба телефона опубликует" in s for s in said),
    "в службе пост ждёт цикла службы (иначе он пошёл бы посреди сессии)")
bot.set_submit(None)

print("\n--- старый общий бот без отметки телефона ---")
data = json.load(open(bot.shared_path(), encoding="utf-8"))
data.pop("phone")
json.dump(data, open(bot.shared_path(), "w", encoding="utf-8"))
on(C)
say(bot.load_settings()["token"] == TOKEN_A, "ничей общий бот — доступен телефону, что его запустит")
bot.bind_shared()
say(json.load(open(bot.shared_path(), encoding="utf-8")).get("phone") == C,
    "запустил C — бот теперь за C")
on(A)
say(bot.load_settings()["token"] == "", "и A его больше не берёт — двое одного бота не делят")

print("\n--- окно: сменили телефон — сменился бот ---")
import webui  # noqa: E402

polls = []
bot.start_background = lambda: polls.append(bot._bot_of(bot.load_settings()["token"])) or bot.send
bot.stop = lambda: polls.append("stop")
on(B)
webui._start_bot()
say(webui._BOT_GATE["bot"] == "7000000002" and polls[-1] == "7000000002", "окно на B — читает бота B")
on(C)
webui._switch_bot()
say(webui._BOT_GATE["bot"] == "7000000001" and "stop" in polls and polls[-1] == "7000000001",
    "переключили на C — бот B брошен, взят бот C (общий)")
say(not fleet.bot_busy("7000000002"), "замок бота B отпущен — его может взять служба B")
on(A)
webui._switch_bot()
say(webui._BOT_GATE["gate"] is None, "у A своего бота нет — окно ничего не читает")
rows = webui._bots_list()
say(len(rows) == 2 and all(r["where"] for r in rows), f"окну — список ботов: {[(r['phone'], r['where'][:22]) for r in rows]}")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.stdout.flush()
os._exit(0 if ok else 1)
