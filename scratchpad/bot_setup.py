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

print("\n--- названия тем не портятся при сохранении (живьём 18.11) ---")
# Как было у пользователя: тем ещё не помнили, дела привязаны командами с
# настоящими названиями. Сохранение из окна превращало их в «тема №6».
data = json.load(open(bot.shared_path(), encoding="utf-8"))
g = data["groups"][0]
g.pop("topics", None)
g["roles"] = {"posts": {"topic": 6, "name": "Постинг для бота"}}
g["shelves"] = {"stock": {"topic": 12, "name": "Запасы"}, "archive": {"topic": 8, "name": "Запощено"}}
g.pop("no_auto", None)
data["groups"][1].pop("roles", None)
json.dump(data, open(bot.shared_path(), "w", encoding="utf-8"), ensure_ascii=False)
bot.apply_setup({"jobs": {"posts": {"chat": GROUP, "topic": "6"}, "stock": {"chat": GROUP, "topic": "12"},
                          "archive": {"chat": GROUP, "topic": "8"}}})
names = (bot._roles(GROUP)["posts"]["name"], bot._shelves(GROUP)["stock"]["name"],
         bot._shelves(GROUP)["archive"]["name"])
say(names == ("Постинг для бота", "Запасы", "Запощено"), f"после сохранения названия те же: {names}")
# Уже испорченное — чинится само, если настоящее название где-то известно.
data = json.load(open(bot.shared_path(), encoding="utf-8"))
data["groups"][0]["shelves"]["archive"]["name"] = "тема №8"
json.dump(data, open(bot.shared_path(), "w", encoding="utf-8"), ensure_ascii=False)
bot.setup()
say(bot._shelves(GROUP)["archive"]["name"] == "Запощено", "«тема №8» вернулась в «Запощено»")

print("\n--- тема по ссылке: бот в ней ещё ничего не видел ---")
said = bot.add_topic("https://t.me/c/3904397006/15", "Ссылки")
topics = {t["id"]: t["name"] for t in bot.setup()["groups"][0]["topics"]}
say(topics.get("15") == "Ссылки" and "добавлена" in said, f"тема 15 «Ссылки» в списке: {said}")
bot.add_topic("t.me/c/3904397006/16/120")                      # ссылка на сообщение в теме
topics = {t["id"]: t["name"] for t in bot.setup()["groups"][0]["topics"]}
say(topics.get("16") == "тема №16", "ссылка на сообщение внутри темы — тема 16, без названия — по номеру")
for bad, why in (("https://t.me/somebody", "ссылка на тему вида"), ("https://t.me/c/999/3", "группы у бота нет")):
    try:
        bot.add_topic(bad)
        refused = ""
    except ValueError as e:
        refused = str(e)
    say(why in refused, f"{bad} — отказ: {refused[:60]}")

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

print("\n--- шаблон монтажа: выпадающий список (26.11) ---")
import montage  # noqa: E402

s = bot.setup()
say(s["montage_template"] == "rusorama"
    and {"id": "rusorama", "name": "Rusorama"} in [{"id": t["id"], "name": t["name"]}
                                                  for t in s["montage_templates"]],
    f"по умолчанию — Rusorama: {s['montage_templates']}")
mine = os.path.join(tmp, "templates", "second")
os.makedirs(mine)
with open(os.path.join(mine, "template.json"), "w", encoding="utf-8") as f:
    json.dump({"name": "Второй канал", "title": {"font": "x.ttf"}}, f, ensure_ascii=False)
broken = os.path.join(tmp, "templates", "broken")
os.makedirs(broken)
with open(os.path.join(broken, "template.json"), "w", encoding="utf-8") as f:
    f.write("{не json")
ids = [t["id"] for t in bot.setup()["montage_templates"]]
say("second" in ids and "broken" not in ids, f"новая папка с template.json — сама в списке, битая — нет: {ids}")
bot.apply_setup({"montage_template": "second", "stats_time": "11:00"})
s = bot.setup()
say(s["montage_template"] == "second" and bot._saved()["montage_template"] == "second"
    and montage.load_template(bot._saved()["montage_template"])["name"] == "Второй канал",
    "выбор сохранён, монтаж возьмёт именно его")
try:
    bot.apply_setup({"montage_template": "нет-такого", "stats_time": "13:00"})
    refused = ""
except ValueError as e:
    refused = str(e)
say("нет шаблона" in refused and bot.setup()["stats_time"] == "11:00" and bot._saved()["montage_template"] == "second",
    f"неизвестный шаблон — отказ до любых правок: {refused}")
bot.apply_setup({"stats_time": "12:00"})
say(bot._saved()["montage_template"] == "second", "сохранение без поля шаблона выбор не сбрасывает")

print("\n--- память шаблона в окне (27.11): о чём канал, счётчик, очистка ---")
import webui  # noqa: E402
montage.remember("rusorama", "accepted", "Uno | dos", "Texto aceptado")
montage.remember("rusorama", "rejected", "Tres", "Texto rechazado")
items = {t["id"]: t for t in bot.setup()["montage_templates"]}
say(items["rusorama"]["accepted"] == 1 and items["rusorama"]["rejected"] == 1
    and items["rusorama"]["about"].startswith("канал о политике")
    and items["second"]["accepted"] == 0 and items["second"]["about"] == "",
    "у каждого шаблона своя память; «О чём канал» по умолчанию — из шаблона")
bot.apply_setup({"montage_template": "second", "montage_about": "Канал про кино"})
items = {t["id"]: t for t in bot.setup()["montage_templates"]}
say(items["second"]["about"] == "Канал про кино" and items["rusorama"]["about"].startswith("канал о политике"),
    "«О чём канал» сохраняется выбранному шаблону и не трогает другой")
bot.apply_setup({"stats_time": "12:00"})
say(montage.memory("second")["about"] == "Канал про кино", "сохранение без поля «О чём канал» его не стирает")
forget = webui.COMMANDS["montage-forget"]
res = forget({"template": "rusorama"})
mem = montage.memory("rusorama")
say(res.get("ok") and not mem["accepted"] and not mem["rejected"],
    f"«Очистить память» из окна: {res.get('message')}")
say(forget({"template": "нет-такого"}).get("ok") is False, "чужое имя шаблона — отказ")
say(("montage-forget" in webui.HUB_COMMANDS) == ("bot-setup" in webui.HUB_COMMANDS),
    "очистка идёт туда же, куда сохранение настроек бота (к службе телефона)")
page = open(os.path.join(os.path.dirname(HERE), "webui.html"), encoding="utf-8").read()
say(all(s in page for s in ('id="bs-about"', 'id="b-bs-forget"', "montage_about:", '"montage-forget"')),
    "в окне — поле «О чём канал», кнопка очистки, оба уходят в программу")
import shutil  # noqa: E402
shutil.rmtree(mine)
say(bot.setup()["montage_template"] == "rusorama", "выбранный шаблон удалили — в окне снова Rusorama")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
