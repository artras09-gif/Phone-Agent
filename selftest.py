"""Проверка логики, которая работает без телефона."""
import os
import statistics
import tempfile

import config

# Тест не должен писать в боевую очередь: фиктивные задачи попадают в
# счётчик дневного лимита, и агент решает, что уже всё опубликовал.
config.DB_PATH = os.path.join(tempfile.gettempdir(), "phoneagent_selftest.db")
if os.path.exists(config.DB_PATH):
    os.remove(config.DB_PATH)

import human
import jobs
import scheduler
import ui

print(f"--- очередь (тестовая БД: {config.DB_PATH}) ---")
jid = jobs.add(r"C:\test\video.mp4", "тестовое описание #тег", "tiktok")
print("добавлена задача:", jid, "| в очереди:", len(jobs.pending()))
jobs.mark(jid, "done", "ок")
print("опубликовано сегодня:", jobs.count_today("done"))

print("\n--- расписание (разное при каждом запуске) ---")
for t, kind, arg in scheduler._plan_for_today():
    print(f"  {t:%H:%M}  {kind} {arg or ''}")

print("\n--- человекоподобность ---")
d = [human.dwell() for _ in range(5000)]
print(f"время на видео: медиана {statistics.median(d):.1f}с, "
      f"мин {min(d):.1f}с, макс {max(d):.1f}с")
long_tail = sum(1 for x in d if x > 20) / len(d) * 100
print(f"досмотрел до конца (>20с): {long_tail:.1f}% видео")

print("\n--- поиск элементов ---")
node = ui.Node({
    "text": "Опубликовать", "resource-id": "com.zhiliaoapp.musically:id/btn_post",
    "bounds": "[297,1798][783,1902]", "clickable": "true", "enabled": "true",
    "class": "android.widget.Button", "content-desc": "",
})
other = ui.Node({
    "text": "", "resource-id": "com.zhiliaoapp.musically:id/caption_edit",
    "bounds": "[40,300][1040,420]", "clickable": "true", "enabled": "true",
    "class": "android.widget.EditText", "content-desc": "Опишите видео",
})
tree = [node, other]

print("узел:", node, "центр:", node.center)
checks = [
    ("по хвосту id",      ui.find_one(tree, rid="btn_post")),
    ("по полному id",     ui.find_one(tree, rid="com.zhiliaoapp.musically:id/btn_post")),
    ("по тексту",         ui.find_one(tree, text="опубликовать")),
    ("по подстроке",      ui.find_one(tree, contains="публик")),
    ("по content-desc",   ui.find_one(tree, contains="Опишите")),
    ("по классу",         ui.find_one(tree, cls="EditText")),
    ("несуществующий",    ui.find_one(tree, text="Нет такого")),
]
for name, res in checks:
    print(f"  {name:18} -> {res}")

print("\n--- любой из вариантов (мультиязычность) ---")
print(" ", ui.find_any(tree, [{"text": "Post"}, {"text": "Опубликовать"}]))

print("\n--- точки тапа: не центр, каждый раз разные ---")
print(" ", [human.tap_point(node) for _ in range(4)])

print("\n--- зрение: разбор ответа модели ---")
import vision

samples = [
    ('{"тема":"кот","категория":"животные"}',              "чистый JSON"),
    ('```json\n{"тема":"кот","категория":"животные"}\n```', "в markdown-заборе"),
    ('Конечно! {"тема":"кот","категория":"животные"} Готово.', "с болтовнёй вокруг"),
    # Ответ обрывается на лимите токенов — поля надо спасать по одному,
    # иначе весь сырой текст уезжает в «тему» (ловилось на живой сессии).
    ('{\n"тема": "два ребёнка в бане", "кат',                "оборван на лимите"),
    ('вообще не json',                                      "мусор"),
]
for raw, label in samples:
    got = vision._json_from(raw)
    print(f"  {label:20} -> {got}")

print("\n--- зрение: страховка от выдуманных координат ---")
_real_ask = vision.ask
try:
    cases = [
        ('{"действие":"tap","x":500,"y":1200,"экран":"диалог"}', "координаты в экране"),
        ('{"действие":"tap","x":9999,"y":9999,"экран":"диалог"}', "координаты за экраном"),
        ('{"действие":"нажать всё","экран":"диалог"}',            "неизвестное действие"),
    ]
    for answer, label in cases:
        vision.ask = lambda *a, **kw: answer
        res = vision.rescue(b"", "тест", (1080, 2400))
        print(f"  {label:22} -> действие={res['действие']}")
finally:
    vision.ask = _real_ask

print("\n--- подпись под роликом из дерева ленты ---")
feed = [
    ui.Node({"text": "Подписаться", "bounds": "[900,1200][1040,1260]",
             "resource-id": "com.zhiliaoapp.musically:id/follow_btn"}),
    ui.Node({"text": "@kotopes", "bounds": "[40,1900][300,1950]",
             "resource-id": "com.zhiliaoapp.musically:id/author_name"}),
    ui.Node({"text": "как мой кот ворует еду со стола #юмор #коты",
             "bounds": "[40,1960][900,2050]",
             "resource-id": "com.zhiliaoapp.musically:id/desc"}),
    ui.Node({"text": "оригинальный звук - kotopes", "bounds": "[40,2060][700,2100]",
             "resource-id": "com.zhiliaoapp.musically:id/music_title"}),
    ui.Node({"text": "12.3K", "bounds": "[980,1500][1050,1540]",
             "resource-id": "com.zhiliaoapp.musically:id/like_count"}),
]
PKG = "com.zhiliaoapp.musically"
for n in feed:
    n.pkg = PKG
# Чужое окно поверх ленты: шторка уведомлений попадает в тот же дамп.
feed.append(ui.Node({"text": "Встречали Веронику? Кажется, вы в общей компании",
                     "bounds": "[0,1980][1080,2060]", "package": "com.vk.im"}))

caption, author, music = ui.feed_caption(feed, screen=(1080, 2400), package=PKG)
print(f"  описание: {caption}")
print(f"  автор:    {author}")
print(f"  музыка:   {music}")
print(f"  чужое уведомление отсеяно: {'Веронику' not in caption}")
print(f"  пустое дерево -> {ui.feed_caption([], screen=(1080, 2400))}")

print("\n--- три ступени: тема и язык ---")
import interests

taste = {"тема": "новости", "язык": "ru",
         "поведение": {"секунд_на_взгляд": [1.5, 3.0],
                       "секунд_на_тему_без_языка": [5.0, 7.0],
                       "секунд_на_язык_без_темы": [4.0, 6.0],
                       "секунд_на_неинтересное": [0.0, 0.0], "лайк": 0.25}}
# Модель темы подменена заглушкой: судим по слову «новост» в описании,
# чтобы тест не зависел от запущенного LM Studio.
judge = lambda theme, topic: "новост" in theme or "событи" in theme

cases = [
    ({"тема": "ведущий рассказывает о событиях дня", "язык": "ru"}, "интересно"),
    ({"тема": "новости дня", "язык": "en"},                         "нейтрально"),
    ({"тема": "кошка спит на стуле", "язык": "ru"},                 "нейтрально"),
    ({"тема": "cat sleeps on a chair", "язык": "en"},               "мимо"),
    # Язык модель называет не всегда — за молчание ролик не наказываем.
    ({"тема": "сводка новостей", "язык": ""},                       "интересно"),
]
FULL = 8.0        # сколько смотрели бы интересное
for frame, want in cases:
    verdict, why, matched = interests.classify(frame, taste, judge=judge)
    secs = interests.watch_seconds(verdict, matched, FULL, taste)
    mark = "OK" if verdict == want else "!!"
    print(f"  {mark} {str(frame['тема'])[:30]:32} {frame['язык'] or '—':3} "
          f"-> {verdict:10} {secs:4.1f}с ({why})")

print("\n  лайк:", {v: interests.like_probability(v, taste)
                    for v in ("интересно", "нейтрально", "мимо")})

# Два сорта нейтрального: тема без языка ценнее языка без темы.
topic_only = [interests.watch_seconds("нейтрально", {"тема": True, "язык": False},
                                      FULL, taste) for _ in range(1000)]
lang_only = [interests.watch_seconds("нейтрально", {"тема": False, "язык": True},
                                     FULL, taste) for _ in range(1000)]
print(f"  тема без языка: {min(topic_only):.1f}-{max(topic_only):.1f}с "
      f"{'OK' if 5.0 <= min(topic_only) and max(topic_only) <= 7.0 else '!!'}")
print(f"  язык без темы:  {min(lang_only):.1f}-{max(lang_only):.1f}с "
      f"{'OK' if 4.0 <= min(lang_only) and max(lang_only) <= 6.0 else '!!'}")
print(f"  мимо: {interests.watch_seconds('мимо', {}, FULL, taste):.1f}с")

# Ступени не должны переворачиваться: короткий dwell не может сделать
# интересный ролик короче нейтрального.
import human

good = [interests.watch_seconds("интересно", {}, human.dwell(), taste)
        for _ in range(2000)]
worst_neutral = max(max(topic_only), max(lang_only))
print(f"  интересно: {min(good):.1f}-{max(good):.1f}с "
      f"{'OK' if min(good) >= worst_neutral else '!! короче нейтрального'}")

# Одно заданное условие: средней ступени быть не должно.
only_topic = dict(taste, язык="")
got = {interests.classify(f, only_topic, judge=judge)[0] for f, _ in cases}
print(f"  без языка ступени: {sorted(got)} "
      f"{'OK' if 'нейтрально' not in got else '!! появилась лишняя'}")

print(f"  склонения: «коты» в «мем с котом» -> "
      f"{interests.matches('коты', 'мем с котом на весах')}")
print(f"  «кот» в «который час» -> {interests.matches('кот', 'который час')}")

print("\n--- планировщик сессий ---")
import datetime as _dt

import plan as plan_mod

for text, want in (("пн-пт", {0, 1, 2, 3, 4}), ("пн,ср,сб", {0, 2, 5}),
                   ("каждый день", set(range(7))), ("выходные", {5, 6}),
                   ("mon-fri", {0, 1, 2, 3, 4}),
                   ("сб-вт", {5, 6, 0, 1})):          # диапазон через воскресенье
    got = plan_mod.parse_days(text)
    print(f"  {'OK' if got == want else '!!'} дни {text!r:16} -> "
          f"{plan_mod.days_text(got)}")

for bad in ("пн-хз", "25:00-26:00", ""):
    try:
        plan_mod.parse_days(bad) if ":" not in bad else plan_mod.parse_window(bad)
        print(f"  !! мусор {bad!r} принят молча")
    except ValueError as e:
        print(f"  OK мусор {bad!r:14} отвергнут: {e}")

# Окно должно давать разное время каждый день и укладываться в границы.
rules = [{"окно": "21:00-22:00", "минут": 30, "дни": "каждый день",
          "что": "tiktok", "вкл": True}]
starts = []
for i in range(200):
    day = _dt.date(2026, 8, 1) + _dt.timedelta(days=i % 30)
    (when, app, secs), = plan_mod.for_date(day, rules)
    starts.append(when.hour * 60 + when.minute)
inside = all(21 * 60 <= s <= 22 * 60 for s in starts)
print(f"  начало внутри окна: {inside}, разных значений {len(set(starts))} из 200")
print(f"  длительность: {secs / 60:.0f} мин")

night, = plan_mod.for_date(_dt.date(2026, 8, 1),
                           [{"окно": "23:00-01:00", "минут": 15,
                             "дни": "каждый день", "вкл": True}])
print(f"  окно через полночь -> {night[0]:%H:%M} (те же сутки: "
      f"{night[0].date() == _dt.date(2026, 8, 1)})")

off = plan_mod.for_date(_dt.date(2026, 8, 1),
                        [dict(rules[0], вкл=False)])
print(f"  выключенное правило не срабатывает: {off == []}")

print("\n--- закрытие окон: что именно нажмёт агент ---")
# Реальное окно из прогона: TikTok просит доступ к контактам.
permission = [
    ui.Node({"text": "Чтобы связаться в TikTok со своими знакомыми, разрешите "
                     "приложению доступ к контактам в настройках устройства.",
             "bounds": "[144,840][936,1236]", "enabled": "true"}),
    ui.Node({"text": "Открыть настройки", "bounds": "[144,1260][936,1380]",
             "clickable": "true", "enabled": "true"}),
    ui.Node({"text": "Не разрешать", "bounds": "[144,1392][936,1512]",
             "clickable": "true", "enabled": "true"}),
]
pressed = []
_real_tap = ui.tap_node
try:
    ui.tap_node = lambda node: pressed.append(node.text)
    ui.dismiss_popup(permission)
finally:
    ui.tap_node = _real_tap
print(f"  запрос доступа к контактам -> нажал {pressed!r}")
print(f"  «Разрешить» в списке закрытий: "
      f"{'ЕСТЬ, это опасно' if 'Разрешить' in ui.DISMISS_LABELS else 'нет'}")

print("\n--- застревание: лента стоит на месте ---")
import session

# Реальные описания из прогона, где поверх ленты висело окно «Подпишитесь
# на друзей»: агент свайпал внутри него и разбирал одно и то же 12 раз.
stuck_run = ["набор друзей в социальной сети",
             "пользователи подписываются на канал",
             "пользователи подписываются на друзей",
             "пользователи подписываются на другую страницу"]
state = {}
caught = [session._looks_stuck(state, t) for t in stuck_run]
print(f"  окно поверх ленты -> поймано на {caught.index(True) + 1}-м кадре"
      if any(caught) else "  окно поверх ленты -> НЕ ПОЙМАНО")

normal_run = ["маленькая ящерица на песке", "парень и девушка в кухне",
              "женщина готовит чай дома", "два кота спят на кровати"]
state = {}
false_alarm = any(session._looks_stuck(state, t) for t in normal_run)
print(f"  обычная лента -> ложных срабатываний: {'ЕСТЬ' if false_alarm else 'нет'}")

print("\n--- стоп-кран ---")
config.set_stop(True)
print("  взведён:", config.stop_requested())
config.set_stop(False)
print("  снят:   ", config.stop_requested())

print("\nвсё работает")
