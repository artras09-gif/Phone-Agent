"""Настройки бота из окна (просьба 2026-11-18): где какое дело и как часто статистика.

«Все параметры ботов — в каком чате какие действия он выполняет и как часто
отсылает стату — перенести в приложение и чтобы там можно было настраивать».
Без Telegram и телефона: файл бота — во временной папке. Проверяется, что
окно видит то же, что настроено командами /here, что сохранение переносит
дело (оно одно на бота), что запасы нельзя посадить в тему другого дела, что
снятая руками полка не возвращается по названию темы, и что выключенный в
окне отчёт (неделя, месяц) больше не снимается сам.
"""
import datetime as dt
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config     # noqa: E402

tmp = tempfile.mkdtemp(prefix="pa_botsetup_")
config.DB_PATH = os.path.join(tmp, "jobs.db")
config.BASE = tmp
config.SERIAL = None

import accstats            # noqa: E402
import jobs                # noqa: E402
import telegram_bot as bot  # noqa: E402

jobs._SCHEMA_READY = False
ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


GROUP, OTHER = "-1003904397006", "-1002"
with open(bot.shared_path(), "w", encoding="utf-8") as f:
    json.dump({"token": "123456:" + "A" * 35, "owner": "335219222", "groups": [
        {"id": GROUP, "title": "Tras el Velo",
         "shelves": {"stock": {"topic": 12, "name": "Запасы"}, "archive": {"topic": 8, "name": "Запощено"}},
         "roles": {"posts": {"topic": 6, "name": "Постинг для бота"}}},
        {"id": OTHER, "title": "Второй чат"}]}, f, ensure_ascii=False)

print("--- окно видит то, что настроено командами ---")
s = bot.setup()
say(s["jobs"]["posts"] == {"title": "Видео на постинг", "unbound": "в любом чате и теме",
                           "chat": GROUP, "topic": "6"}, f"постинг: {s['jobs']['posts']}")
say(s["jobs"]["stock"]["topic"] == "12" and s["jobs"]["archive"]["topic"] == "8", "запасы и архив")
say(s["jobs"]["stats"]["chat"] == "" and s["jobs"]["stats"]["unbound"] == "владельцу в личку",
    "статистика не привязана — владельцу в личку")
names = {t["id"]: t["name"] for t in s["groups"][0]["topics"]}
say(names == {"general": "Общая", "6": "Постинг для бота", "8": "Запощено", "12": "Запасы"},
    f"темы группы для выбора: {names}")
say(s["stats_time"] == config.DAILY_STATS_AT and s["stats_kinds"] == ["day", "week", "month"],
    f"статистика по умолчанию: {s['stats_time']}, {s['stats_kinds']}")

print("\n--- бот запоминает темы, где видел сообщения ---")
bot._remember_topic(GROUP, 40, {"message_thread_id": 40})
bot._remember_topic(GROUP, 41, {"reply_to_message": {"forum_topic_created": {"name": "Статистика"}}})
names = {t["id"]: t["name"] for t in bot.setup()["groups"][0]["topics"]}
say(names.get("40") == "тема №40" and names.get("41") == "Статистика",
    f"тема без названия — по номеру, с названием — по имени: {names.get('40')}, {names.get('41')}")
bot._remember_topic(OTHER, None, {})
say(any(t["id"] == "general" for t in bot.setup()["groups"][1]["topics"]), "«Общая» второго чата")

print("\n--- сохранение из окна ---")
said = bot.apply_setup({"jobs": {"stats": {"chat": GROUP, "topic": "41"},
                                 "posts": {"chat": OTHER, "topic": "general"},
                                 "links": {"chat": "", "topic": ""}},
                        "stats_time": "10:30", "stats_kinds": ["day", "month"]})
s = bot.setup()
say(said and s["jobs"]["stats"]["topic"] == "41" and bot.role_topic(GROUP, "stats") == 41,
    "статистика — в тему «Статистика»")
say(bot.role_home("posts") == (OTHER, "general") and bot.role_topic(GROUP, "posts") is None,
    "постинг переехал во второй чат и из первого снят (дело одно на бота)")
say(bot.destinations("stats") == [(GROUP, 41)], "отчёты статистики уходят в выбранную тему")
say(s["stats_time"] == "10:30" and s["stats_kinds"] == ["day", "month"],
    f"время и виды отчётов: {s['stats_time']}, {s['stats_kinds']}")

print("\n--- чего нельзя ---")
try:
    bot.apply_setup({"jobs": {"stock": {"chat": GROUP, "topic": "41"}}})
    refused = ""
except ValueError as e:
    refused = str(e)
say("в одной теме нельзя" in refused, f"запасы в теме статистики — отказ: {refused[:70]}")
try:
    bot.apply_setup({"stats_time": "25:00"})
    refused = ""
except ValueError as e:
    refused = str(e)
say("12:00" in refused, "время «25:00» — отказ")
try:
    bot.apply_setup({"jobs": {"links": {"chat": "-999", "topic": "1"}}})
    refused = ""
except ValueError as e:
    refused = str(e)
say("уже нет" in refused, "чат, где бота нет, — отказ")
say(bot.role_topic(GROUP, "stats") == 41 and bot._shelves(GROUP)["stock"]["topic"] == 12,
    "после отказа ничего не поменялось")

print("\n--- полку сняли в окне — по названию темы не возвращается ---")
bot.apply_setup({"jobs": {"archive": {"chat": "", "topic": ""}}})
say(bot.shelf_home("archive") is None, "архив снят")
sent = []
bot.send = lambda text, **kw: sent.append(text)
bot._auto_shelf({"message_thread_id": 8, "reply_to_message": {"forum_topic_created": {"name": "Архив"}}},
                GROUP, 8)
say(bot.shelf_home("archive") is None and not sent, "сообщение в теме «Архив» полку не назначило")
bot.apply_setup({"jobs": {"archive": {"chat": GROUP, "topic": "8"}}})
say(bot.shelf_home("archive") == (GROUP, 8, "Запощено"), "назначили снова — архив на месте, имя темы прежнее")

print("\n--- статистика: выключенные отчёты сами не снимаются ---")
bot.apply_setup({"stats_time": "12:00", "stats_kinds": ["day"]})
monday = dt.datetime(2026, 11, 2, 13, 0)          # понедельник, 2-е — неделя есть, месяц догоняется
due = accstats.reports_due(monday)
say(due and all(kind == "day" for kind, _ in due), f"включён только день — снимается только он: {due}")
bot.apply_setup({"stats_kinds": ["day", "week", "month"]})
kinds = {kind for kind, _ in accstats.reports_due(monday)}
say({"day", "week", "month"} <= kinds, f"включены все — все: {sorted(kinds)}")
bot.apply_setup({"stats_time": ""})
say(accstats.reports_due(monday) == [] and bot.setup()["stats_time"] == "", "время пустое — сама не шлёт")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
