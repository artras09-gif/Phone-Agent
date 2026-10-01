"""Категории роликов: у понятого ролика — своя полка, а не «другое».

Жалоба 2026-10-02: «в сводке 38% — категория «другое»». Разбор 2987 кадров
показал, что это не непонятые ролики, а понятые без своей категории:
модель сама отвечала «природа» (121 раз), «жизнь» (62), «туризм»… — и всё
это выбрасывалось. Плюс кадры-сбои (чёрный экран, окно поверх ленты)
раздували «другое», хотя роликами не являются.
"""
import os
import sqlite3
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402
import vision  # noqa: E402

ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


cat = vision.categorize
print("--- слово модели ---")
say(cat("юмор", "кот падает") == "юмор", "слово из списка — как есть")
say(cat("природа", "пейзаж") == "природа", "«природа» теперь своя категория")
say(cat("жизнь", "женщина рассказывает о себе") == "блог", "«жизнь» -> блог")
say(cat("туризм", "люди на улице") == "путешествия", "«туризм» -> путешествия")
say(cat("художество", "девушка рисует") == "творчество", "«художество» -> творчество")
say(cat("домашние дела", "мытьё посуды") == "дом", "«домашние дела» -> дом")
say(cat("путешествие", "вид на город") == "путешествия", "похожее слово — по началу")

print("\n--- «другое» по описанию ролика ---")
for theme, want in [("пейзаж с рекой и зелёным полем", "природа"),
                    ("люди гуляют на Таймс-сквер в Нью-Йорке", "путешествия"),
                    ("женщина рассказывает о своём опыте", "блог"),
                    ("стоматолог показывает зубы", "здоровье"),
                    ("девушка рисует картину на ткани", "творчество"),
                    ("исторический сюжет о Наполеоне", "история"),
                    ("мультфильм про летающие тарелки", "кино"),
                    ("женщина готовит чай дома", "еда")]:
    got = cat("другое", theme)
    say(got == want, f"«{theme}» -> {got}")

print("\n--- без ложных попаданий ---")
for theme in ("человек, который стоит у стены", "небольшой предмет на столе",
              "полезный совет без слов", "семь человек стоят в ряд"):
    got = cat("другое", theme)
    say(got in ("другое", "обучение") and got not in ("животные", "природа", "отношения"),
        f"«{theme}» -> {got} (не кот, не небо, не поле, не семья)")

print("\n--- кадры-сбои — отдельно ---")
for theme in ("не разобрал кадр", "?????????", "", "чёрный экран, видео не воспроизводится",
              "пользователи подписываются на друзей"):
    say(cat("другое", theme) == vision.NOT_PARSED, f"«{theme[:30]}» -> {vision.NOT_PARSED}")
say(cat("интерфейс", "экран чата") == vision.NOT_PARSED, "старая категория «интерфейс» — тоже сбой")

print("\n--- список в вопросе модели ---")
prompts = []
vision.ask = lambda png, prompt, **kw: prompts.append(prompt) or \
    '{"тема": "закат над морем", "категория": "природа", "реклама": false, "язык": "ru"}'
vision.provider = lambda: vision.LOCAL
data = vision.describe_frame(b"png")
say(all(c in prompts[-1] for c in ("природа", "путешествия", "блог", "здоровье", "кино")),
    "модели предложены новые категории")
say(data["категория"] == "природа" and "сырой" not in data, "ответ «природа» больше не «вне списка»")

print("\n--- история переложена при первом запуске ---")
tmp = tempfile.mkdtemp(prefix="pa_cats_")
config.DB_PATH = os.path.join(tmp, "jobs.db")
con = sqlite3.connect(config.DB_PATH)
con.executescript(__import__("jobs").SCHEMA)
for theme, category, raw in [("закат на море", "другое", "категория вне списка: природа"),
                             ("женщина рассказывает историю", "другое", "категория вне списка: жизнь"),
                             ("кот спит", "животные", ""),
                             ("не разобрал кадр", "другое", "модель повторила пример из промпта"),
                             ("человек стоит у стены", "другое", "")]:
    con.execute("INSERT INTO content (session, app, frame, tema, category, raw, at) "
                "VALUES ('s', 'tiktok', ?, ?, ?, ?, ?)", (theme, theme, category, raw, time.time()))
con.commit()
con.close()
import jobs  # noqa: E402

jobs._SCHEMA_READY = False
jobs.connect().close()
con = sqlite3.connect(config.DB_PATH)
after = dict(con.execute("SELECT tema, category FROM content"))
version = con.execute("PRAGMA user_version").fetchone()[0]
con.close()
say(after["закат на море"] == "природа" and after["женщина рассказывает историю"] == "блог",
    "старые «вне списка» переложены по слову модели")
say(after["не разобрал кадр"] == vision.NOT_PARSED, "старый сбой — в «не разобрано»")
say(after["кот спит"] == "животные" and after["человек стоит у стены"] == "другое",
    "правильные и честно непонятные — не тронуты")
say(version == jobs.CATEGORIES_VERSION, "отметка версии поставлена — второй раз не перекладываем")

print("\n--- сводка ---")
import stats  # noqa: E402

lines = stats.report(30)
text = "\n".join(lines) if not isinstance(lines, str) else lines
say("не разобрано (сбой кадра" in text and "из 4 разобранных роликов" in text,
    "сбои — отдельной строкой, доли — от настоящих роликов")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
