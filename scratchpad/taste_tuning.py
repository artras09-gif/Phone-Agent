"""Вкусы 2026-10-11: несколько языков, «сначала язык, потом тема», быстрые
свайпы очередью, TikTok «Главная» — без телефона.

Жалобы:
  * «новый телефон быстро настраивается на язык, но с темой сложнее —
    контент на испанском, но он про мамочек каких-то»;
  * «возможность добавлять несколько языков»;
  * «добавить 2-3 скролла моментальных без проверок, рандомно, но не часто»;
  * «в тиктоке путает home: думает, что это не лента».
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config     # noqa: E402

tmp = tempfile.mkdtemp(prefix="pa_taste_")
config.INTERESTS = os.path.join(tmp, "interests.json")
config.DB_PATH = os.path.join(tmp, "jobs.db")

import interests  # noqa: E402
import jobs       # noqa: E402

jobs._SCHEMA_READY = False
ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


print("--- несколько языков ---")
say(interests.languages({"язык": "ru, en es"}) == ["ru", "en", "es"], "«ru, en es» — три языка")
say(interests.languages({"язык": "ru,ru, -"}) == ["ru"], "повторы и прочерк убраны")
taste = interests.set_choice(topic="кулинария", lang="es, en, de")
say(interests.load()["язык"] == "es, en, de", "три языка сохраняются и читаются")
ok_lang, note = interests._lang_matches({"язык": "de"}, "es, en, de")
say(ok_lang, f"de подходит при es/en/de: {note}")
ok_lang, note = interests._lang_matches({"язык": "ru"}, "es, en, de")
say(not ok_lang and "es или en или de" in note, f"ru — нет, и в причине все языки: {note}")

print("\n--- старый файл вкусов получает новые умолчания ---")
with open(config.INTERESTS, "w", encoding="utf-8") as f:
    json.dump({"тема": "коты", "язык": "ru", "поведение": {
        "секунд_на_тему_без_языка": [10.0, 15.0], "секунд_на_язык_без_темы": [1.5, 10.0],
        "лайк": 0.4}}, f, ensure_ascii=False)
old = interests.load()
say(old["поведение"]["секунд_на_тему_без_языка"] == [15.0, 25.0],
    "старое умолчание «тема без языка» заменено новым")
say(old["поведение"]["лайк"] == 0.4, "своё значение («лайк» 0.4) сохранено")
say(old["поведение"]["язык_настроен_доля"] == 0.6, "новые ключи градации на месте")

print("\n--- градация «сначала язык, потом тема» ---")
t = {"тема": "кулинария", "язык": "es", "поведение": dict(interests.DEFAULTS["поведение"])}
judge = lambda theme, topic: "пицц" in theme or "торт" in theme
new_phone = [("en", None)] * 18 + [("es", False)] * 4
tuned = interests.tuning("tiktok", t, rows=new_phone)
say(tuned["этап"] == "язык", f"новый телефон, испанского 18% — учим язык: {tuned['текст']}")
spanish = [("es", False)] * 37 + [("es", True)] * 3
tuned2 = interests.tuning("tiktok", t, rows=spanish)
say(tuned2["этап"] == "тема" and tuned2["усиление"] == 2.0,
    f"испанского 100%, темы 7% — упор на тему, лайк x2: {tuned2['текст']}")
few = interests.tuning("tiktok", t, rows=[("es", False)] * 5)
say(few["этап"] == "язык" and few["язык"] is None, "данных мало — пока учим язык")
no_lang = interests.tuning("tiktok", {"тема": "кулинария", "язык": "",
                                      "поведение": t["поведение"]}, rows=new_phone)
say(no_lang["этап"] == "тема", "язык не задан — сразу упор на тему")

mom = {"тема": "мамочка рассказывает про детский сад", "язык": "es"}
v1, why1, m1 = interests.classify(mom, t, judge=judge, tuned=tuned)
s1 = interests.watch_seconds(v1, m1, 8.0, t)
say(v1 == "нейтрально" and 3.0 <= s1 <= 7.0 and interests.like_probability(v1, t, m1) == 0,
    f"учим язык: «мамочка, es» — вполглаза {s1:.1f}с, без лайка")
v2, why2, m2 = interests.classify(mom, t, judge=judge, tuned=tuned2)
say(v2 == "мимо" and "язык уже настроен" in why2,
    f"язык настроен: «мамочка, es» — листаю сразу ({why2})")
pizza = {"тема": "пицца из печи", "язык": "es"}
v3, _, m3 = interests.classify(pizza, t, judge=judge, tuned=tuned2)
like_rare = interests.like_probability(v3, t, m3)
v4, _, m4 = interests.classify(pizza, t, judge=judge,
                               tuned=interests.tuning("tiktok", t, rows=[("es", True)] * 40))
like_common = interests.like_probability(v4, t, m4)
say(v3 == "интересно" and like_rare == 0.5 and like_common == 0.25,
    f"редкая тема — лайк {like_rare}, частая — {like_common}")
say(interests.like_probability("интересно", dict(t, поведение=dict(t["поведение"], лайк=0.5)),
                                {"усиление": 2.0}) == 0.6, "потолок лайка 0.6")
foreign = {"тема": "торт шоколадный", "язык": "en"}
v5, _, m5 = interests.classify(foreign, t, judge=judge, tuned=tuned2)
s5 = interests.watch_seconds(v5, m5, 8.0, t)
say(v5 == "нейтрально" and 15 <= s5 <= 25 and interests.like_probability(v5, t, m5) > 0,
    f"тема на чужом языке — {s5:.0f}с и лайк иногда")

print("\n--- «по теме» по кадру подтверждает судья (жалоба 2026-10-12) ---")
craft = {"тема": "монтаж, моушендизайн", "язык": "ru", "не_смотреть": "",
         "поведение": dict(interests.DEFAULTS["поведение"])}
mama = {"тема": "мама обнимает спящего младенца в постели", "язык": "ru", "по_теме": True}
strict_judge = lambda theme, topic: "таймлайн" in theme or "анимац" in theme
v, why, m = interests.classify(mama, craft, judge=strict_judge, tuned={"этап": "тема"})
say(v == "мимо" and "описание не про неё" in why, f"нейроролик «мама с младенцем» при «монтаже» — {v} ({why})")
real = {"тема": "парень режет клипы на таймлайне premiere", "язык": "ru", "по_теме": True}
v, why, m = interests.classify(real, craft, judge=strict_judge, tuned={"этап": "тема"})
say(v == "интересно" and "монтаж" in why, f"настоящий урок монтажа — {v} ({why})")
broken = lambda theme, topic: (_ for _ in ()).throw(RuntimeError("сеть"))
v, why, m = interests.classify(mama, craft, judge=broken, tuned={"этап": "тема"})
say(v == "интересно", "судья недоступен — верим кадру, а не теряем решение")

print("\n--- «Не смотреть» ---")
banned = dict(craft, не_смотреть="нейросети, мамы с детьми")
v, why, m = interests.classify(dict(real, запрет=True), banned, judge=strict_judge)
say(v == "мимо" and why.startswith("не смотреть"), f"запрет по кадру — мимо даже при теме ({why})")
v, why, m = interests.classify(dict(real, запрет=False), banned, judge=strict_judge)
say(v == "интересно", "запрет=false — решаем как обычно")
text_judge = lambda theme, topic: topic == "мамы с детьми" and "мама" in theme
hit, what = interests.banned_hit({"тема": "мама кормит малыша"}, banned, text_judge)
say(hit and what == "мамы с детьми", "своя модель без поля «запрет» — судья по описанию")
interests.set_choice(banned="Нейросети,  пранки")
say(interests.load()["не_смотреть"] == "нейросети, пранки", "«не смотреть» сохраняется")
import vision as _v  # noqa: E402
asked = []
real_fq, real_ask = _v.frame_questions, _v.ask
_v.frame_questions = lambda: True
_v.ask = lambda png, prompt, **kw: asked.append(prompt) or (
    '{"тема": "x", "категория": "юмор", "реклама": false, "язык": "ru", '
    '"запрет": true, "лента": true}')
d = _v.describe_frame(b"png", topics="монтаж", banned="нейросети")
say(d.get("запрет") is True and "запрет: true" in asked[-1] and "сгенерированные нейросетью" in asked[-1],
    "вопрос «запрет» в том же запросе, с подсказкой про нейроролики")
_v.describe_frame(b"png", topics="монтаж", banned="пранки")
say("сгенерированные нейросетью" not in asked[-1], "без нейросетей в запрете подсказки нет")
d = _v.describe_frame(b"png", topics="монтаж", banned="")
say("запрет" not in d and "запрет:" not in asked[-1], "запрет пуст — поле не спрашивается и не берётся")
_v.frame_questions, _v.ask = real_fq, real_ask

print("\n--- доли считаются по базе этой соцсети ---")
for i in range(12):
    path = os.path.join(tmp, f"f{i}.png")
    jobs.add_content("s1", "tiktok", path, {"тема": "x", "язык": "es", "категория": "еда"})
    jobs.set_verdict(path, "мимо", True, i < 3, "кулинария")
jobs.add_content("s1", "reels", os.path.join(tmp, "r.png"), {"тема": "y", "язык": "ru"})
rows = jobs.recent_feed("tiktok")
say(len(rows) == 12 and rows.count(("es", True)) == 3, f"tiktok: 12 роликов, 3 по теме")
live = interests.tuning("tiktok", t)
say(live["этап"] == "тема" and abs(live["тема"] - 0.25) < 0.01, f"из базы: {live['текст']}")
other = interests.tuning("tiktok", dict(t, тема="политика"))
say(other["тема"] is None, "сменили тему — старые отметки «по теме» не считаются")

print("\n--- быстрые свайпы очередью ---")
import abort    # noqa: E402
import human    # noqa: E402
import runlog   # noqa: E402
import session  # noqa: E402

swipes = []
human.scroll_feed = lambda w, h, d="up", quick=False: swipes.append(quick)
abort.sleep = lambda s: False
st = {"daypart": 1.0}
session._flick_rearm(st, 0)
low, high = config.FLICK_EVERY
say(low <= st["flick_at"] <= high, f"первая очередь через {st['flick_at']} роликов")
log = runlog.Log(live=False)
bursts = []
for watched in range(1, 400):
    before = len(swipes)
    session._next_video(st, 1080, 2400, watched, log)
    if len(swipes) - before > 1:
        bursts.append(len(swipes) - before)
gaps_ok = all(2 <= n <= 3 for n in bursts)
say(gaps_ok and 400 / high - 2 <= len(bursts) <= 400 / low + 2,
    f"за 400 роликов {len(bursts)} очередей по {sorted(set(bursts))} свайпа")
say(st.get("flicked") == sum(n - 1 for n in bursts), "лишние свайпы посчитаны в итог сессии")

print("\n--- TikTok: «Главная» на паузе — это лента ---")
import ui       # noqa: E402

recipes = json.load(open(os.path.join(os.path.dirname(HERE), "recipes.json"), encoding="utf-8"))
cfg = recipes["_feed_apps"]["tiktok"]
nodes = [ui.Node({"content-desc": "Рекомендации", "selected": "true", "bounds": "[628,93][937,253]"}),
         ui.Node({"content-desc": "Подписки", "selected": "false", "bounds": "[423,93][628,252]"}),
         ui.Node({"content-desc": "Главная", "selected": "true", "clickable": "true",
                  "bounds": "[0,2135][216,2177]"})]
ui.dump = lambda *a, **kw: nodes
say(session._off_feed(cfg) == "", "выбраны «Рекомендации» — в ленте")
nodes[0] = ui.Node({"content-desc": "Рекомендации", "selected": "false", "bounds": "[628,93][937,253]"})
nodes[1] = ui.Node({"content-desc": "Подписки", "selected": "true", "bounds": "[423,93][628,252]"})
say(session._off_feed(cfg) == "Главная", "выбраны «Подписки» — не та лента")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
