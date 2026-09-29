"""Снимки не копятся: разобранный кадр удаляется, а всё нужное из него — в базе.

Ни телефона, ни модели: всё во временной папке.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402

tmp = tempfile.mkdtemp(prefix="pa_frames_")
config.DB_PATH = os.path.join(tmp, "jobs.db")
config.FRAMES_DIR = os.path.join(tmp, "frames")
config.LOG_DIR = os.path.join(tmp, "logs")
config.BASE = tmp
config.KEEP_ANALYZED_FRAMES = False

import jobs    # noqa: E402
import poster  # noqa: E402
import vision  # noqa: E402

ok = True


def say(good, text):
    global ok
    ok &= good
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


def frame(session, index, kb):
    path = vision.save_frame(b"x" * kb * 1024, session, index)
    return path


# --- 1. разобранный кадр уходит, размер остаётся в базе ------------------
print("--- кадр после разбора ---")
path = frame("s1", 1, 700)
jobs.add_content("s1", "tiktok", path, {"тема": "котик", "категория": "животные"})
vision.drop_frame(path)
say(not os.path.exists(path), "файл кадра удалён")
say(not os.path.exists(os.path.dirname(path)), "опустевшая папка сессии удалена")
with jobs.connect() as con:
    kb = con.execute("SELECT frame_kb FROM content WHERE frame=?", (path,)).fetchone()[0]
say(kb == 700, f"размер кадра сохранён в базе: {kb} КБ")

# --- 2. неразобранный остаётся для analyze -------------------------------
print("\n--- неразобранный и старый запас ---")
keep = frame("s2", 1, 300)                       # модель не успела
old = frame("s2", 2, 300)                        # разобран, но лежит с прошлых времён
jobs.add_content("s2", "tiktok", old, {"тема": "еда"})
vision.tidy_up()
say(os.path.exists(keep), "неразобранный кадр остался — его подберёт analyze")
say(not os.path.exists(old), "разобранный запас прошлых времён убран при уборке")

# --- 3. хранить, если так сказано ----------------------------------------
config.KEEP_ANALYZED_FRAMES = True
kept = frame("s3", 1, 10)
vision.drop_frame(kept)
say(os.path.exists(kept), "KEEP_ANALYZED_FRAMES=True — кадры не трогаются")
config.KEEP_ANALYZED_FRAMES = False

# --- 4. stats считает пустые кадры по базе, без файлов --------------------
print("\n--- счётчик пустых кадров ---")
import stats  # noqa: E402

blank = frame("s4", 1, 3)                        # погашенный экран весит ~15 КБ
jobs.add_content("s4", "tiktok", blank, {"тема": "чёрный экран"})
vision.drop_frame(blank)
report = stats.report(days=1)
say("ПУСТЫЕ КАДРЫ" in str(report), "пустой кадр посчитан, хотя файла уже нет")

# --- 5. снимки удачной публикации убираются, отчёт остаётся ---------------
print("\n--- снимки шагов публикации ---")
log = os.path.join(tmp, "logs", "demo")
os.makedirs(log)
for name in ("01_a.png", "02_b.png", "report.txt"):
    open(os.path.join(log, name), "w").close()
poster._drop_snaps(log)
left = sorted(os.listdir(log))
say(left == ["report.txt"], f"после удачного маршрута остался только отчёт: {left}")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
