"""YouTube: теги от модели по описанию и ввод их в приложении (2026-10-19).

Без телефона и без модели: экран сведений YouTube собран так, как он выглядит
живьём (строки без подписей, «Свернуть», «Добавьте теги»), ответы модели
подменены.
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402

tmp = tempfile.mkdtemp(prefix="pa_yttags_")
config.BASE = tmp
config.DB_PATH = os.path.join(tmp, "jobs.db")
config.QUEUE_DIR = os.path.join(tmp, "queue")
os.makedirs(config.QUEUE_DIR)

import jobs       # noqa: E402
import poster     # noqa: E402
import scheduler  # noqa: E402
import yttags     # noqa: E402

jobs._SCHEMA_READY = False
ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


CAPTION = ("7 звуков, которые слышно в каждом втором рилсе: whoosh, pop, UI, counter, bass drop, riser и impact.\n"
           "Весь пак из 200+ эффектов лежит бесплатно в Telegram: motion_order (ссылка в профиле канала).\n"
           "#shorts #монтаж #звуки #sfx")

print("--- ответ модели -> теги ---")
got = yttags.clean("1. звуковые эффекты, #sfx, «монтаж», sfx, \n- sound design, " + "x" * 60)
say(got == ["звуковые эффекты", "sfx", "монтаж", "sound design"],
    f"без номеров, «#», кавычек, повторов и длинных: {got}")
many = yttags.clean(", ".join(f"длинный тег номер {i}" for i in range(40)))
say(sum(len(t) + (2 if " " in t else 0) + 1 for t in many) <= yttags.MAX_TOTAL and len(many) <= 10,
    f"не больше 10 тегов и в пределе YouTube (500 знаков): {len(many)}")
say(yttags.from_hashtags(CAPTION) == ["shorts", "монтаж", "звуки", "sfx"], "без модели — из хэштегов")
import vision  # noqa: E402
vision.ask_text = lambda prompt, max_tokens, temperature, timeout, model=None: \
    "звуковые эффекты, sound effects, sfx, монтаж видео, whoosh"
say(yttags.suggest(CAPTION) == ["звуковые эффекты", "sfx", "монтаж видео", "whoosh"],
    "теги от модели — на языке описания: «sound effects» не из описания выброшен, «sfx» и «whoosh» оттуда")
vision.ask_text = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("нет связи"))
say(yttags.suggest(CAPTION) == ["shorts", "монтаж", "звуки", "sfx"], "модель недоступна — из хэштегов, без падения")

print("\n--- публикация: в YouTube уходят название, описание и теги ---")
calls = []
poster.post = lambda path, text, target, extra=None: calls.append((target, text, extra)) or (True, f"{target}: ok")
video = os.path.join(config.QUEUE_DIR, "v.mp4")
open(video, "wb").write(b"x")
jid = jobs.add(video, CAPTION, "tiktok,shorts", exact=True, captions={"shorts_tags": ["sfx", "монтаж"]})
scheduler.run_post_job(job_id=jid)
yt = next(c for c in calls if c[0] == "shorts")
tt = next(c for c in calls if c[0] == "tiktok")
say(yt[1] == CAPTION.splitlines()[0][:100], f"название — первая строка: {yt[1][:40]}…")
say(yt[2] == {"description": CAPTION, "tags": ["sfx", "монтаж"]},
    "описание — целиком, теги — те, что показали в черновике")
say(tt[2] is None, "в TikTok — без лишнего")
calls.clear()
yttags.suggest = lambda caption, timeout=40: ["подобрано сейчас"]
jid2 = jobs.add(video, CAPTION, "shorts", exact=True)
scheduler.run_post_job(job_id=jid2)
say(calls[0][2]["tags"] == ["подобрано сейчас"], "тегов в задаче нет — модель подбирает при публикации")
config.YT_TAGS = False
calls.clear()
jid3 = jobs.add(video, CAPTION, "shorts", exact=True)
scheduler.run_post_job(job_id=jid3)
say(calls[0][2]["tags"] == [], "YT_TAGS = False — без тегов")
config.YT_TAGS = True

print("\n--- теги на языке описания (2026-10-23: к испанскому описанию — русские теги) ---")
ES = ("🇺🇳 UN VOTO CONTRA 14\n\nEl 20 de noviembre de 2024, el Consejo de Seguridad de la ONU votó "
      "una resolución que exigía un alto el fuego inmediato en Gaza y la liberación de los rehenes.")
RU = "7 трендовых звуков для монтажа 🔊 Какой твой любимый — 01 или 07?\n\n#звукидлямонтажа #sfx"
for cap, want in ((ES, "es"), (RU, "ru"),
                  ("5 shape layer modifiers in After Effects that save you hours of keyframing", "en"),
                  ("O vídeo mais rápido do mundo e a música que ninguém conhece", "pt"), ("🔥🔥", "")):
    say(yttags.language(cap) == want, f"язык «{cap[:28]}…» -> {yttags.language(cap)!r}")
asked_prompts = []
import importlib  # noqa: E402
importlib.reload(yttags)            # выше suggest подменён для публикации — нужен настоящий
_prev_ask = vision.ask_text
vision.ask_text = lambda prompt, **k: asked_prompts.append(prompt) or \
    "ООН, Consejo de Seguridad, перемирие, Gaza, veto Estados Unidos, голосование"
tags = yttags.suggest(ES)
say("на испанском" in asked_prompts[-1] and "смесь" not in asked_prompts[-1].lower(),
    "просим теги ТОЛЬКО на языке описания (испанский), без «смеси русских и английских»")
say(tags == ["Consejo de Seguridad", "Gaza", "veto Estados Unidos"],
    f"модель подмешала кириллицу — выброшена: {tags}")
vision.ask_text = lambda prompt, **k: "звуки для монтажа, sfx, sound effects, монтаж видео, free sounds"
tags = yttags.suggest(RU)
say(tags == ["звуки для монтажа", "sfx", "монтаж видео"],
    f"русское описание: английское «sound effects» — нет, «sfx» из описания — да: {tags}")
vision.ask_text = _prev_ask

print("\n--- черновик в боте показывает теги ---")
import telegram_bot  # noqa: E402
row = jobs.by_id(jid)
text = telegram_bot._draft_text(row)
say("Теги YouTube: sfx, монтаж" in text, "в черновике — строка с тегами (откуда — не записано: без приписки)")
for src, label in (("model", "Теги YouTube (подобрал DeepSeek): sfx"),
                   ("hashtags", "Теги YouTube (из хэштегов описания — модель не ответила): sfx")):
    j = jobs.add(video, CAPTION, "shorts", exact=True,
                 captions={"shorts_tags": ["sfx", "монтаж"], "shorts_tags_from": src})
    say(label in telegram_bot._draft_text(jobs.by_id(j)), f"теги {src}: «{label[:45]}…»")
import vision  # noqa: E402
_ask = vision.ask_text
vision.ask_text = lambda *a, **k: ""
say(yttags.suggest("ролик #sfx #монтаж #звуки") and yttags.LAST_SOURCE == "hashtags",
    "модель молчит — теги из хэштегов, и это записано")
vision.ask_text = _ask


print("\n--- ввод в приложении YouTube (экран как живьём) ---")


class N:
    def __init__(self, text="", b=(0, 0, 0, 0), clickable=True, rid="", cls="android.view.ViewGroup"):
        self.text, self.desc, self.bounds, self.clickable, self.rid = text, "", b, clickable, rid
        self.enabled = self.selected = False
        self.cls = cls

    @property
    def area(self):
        return (self.bounds[2] - self.bounds[0]) * (self.bounds[3] - self.bounds[1])


UPLOAD = N("Загрузить", (33, 2127, 1047, 2177), rid="com.google.android.youtube:id/upload_bottom_button")
TITLE = N(CAPTION.splitlines()[0], (302, 324, 1047, 480), rid="com.google.android.youtube:id/title_edit_text",
          cls="android.widget.EditText")
details = [TITLE, N("Развернуть", (33, 1198, 1047, 1330)), UPLOAD]
expanded = [N("", (0, 879 + i * 154, 1080, 1033 + i * 154)) for i in range(7)] + \
           [N("", (0, 1957, 1080, 2094)), TITLE, UPLOAD]
scrolled = [N("", (0, 939, 1080, 1093)), N("", (0, 1247, 1080, 1451)), N("", (0, 1451, 1080, 1605)),
            N("Свернуть", (33, 1616, 1047, 1748)), TITLE, UPLOAD]
# Живьём 2026-10-22: у экрана ввода описания в дереве — «Назад», одно поле
# ввода без подсказки и чип «Хештеги»; заголовка «Добавьте описание» нет.
desc_editor = [N("Назад", (22, 93, 154, 225)), N("", (44, 244, 1036, 348), cls="android.widget.EditText"),
               N("Хештеги", (66, 1481, 293, 1547))]
location = [N("Назад", (22, 93, 154, 225)), N("", (44, 244, 1036, 348), cls="android.widget.EditText")] + \
    [N(f"место {i}", (0, 700 + i * 150, 1080, 850 + i * 150)) for i in range(5)]
attrs = [N("Назад", (22, 93, 154, 225)), N("Добавьте теги", (0, 379, 1080, 523))]
state = {"screen": details, "typed": [], "chips": [], "taps": []}


def dump(retries=3, tolerant=False, timeout=30):
    if state["screen"] is attrs and state["chips"]:
        return attrs + [N(f"{c}, Нажмите, чтобы выбрать тег.", (143, 484, 613, 572)) for c in state["chips"]]
    return state["screen"]


def tap_node(n):
    state["taps"].append(n.text or f"строка {n.bounds[1]}")
    if n.text == "Развернуть":
        state["screen"] = expanded
    elif n.bounds == (0, 1451, 1080, 1605):
        state["screen"] = attrs
    elif n.bounds == (0, 1187, 1080, 1341):                # третья строка — «Описание»
        state["screen"] = state.get("third", desc_editor)
    elif n.text == "Добавьте теги":
        state["field"] = True


poster.ui.dump = dump
poster.ui.tap_node = tap_node
poster.adb.screen_size = lambda: (1080, 2400)
poster.human.pause = lambda a, b=None: None
poster._snap = lambda *a, **k: None


def adb_tap(x, y):
    state["taps"].append(("точка", x, y))
    if 1187 <= y <= 1341:
        state["screen"] = desc_editor


def adb_shell(cmd, check=True, timeout=30):
    if "swipe" in cmd and state["screen"] is expanded:
        state["screen"] = scrolled
    if "keyevent 66" in cmd and state.get("buf"):
        state["chips"].append(state.pop("buf"))
    return ""


def type_text(t):
    if state["screen"] is attrs:
        state["buf"] = t
    else:
        state["typed"].append(t)


def back():
    if state["screen"] in (desc_editor, location):
        state["screen"] = expanded
    elif state["screen"] is attrs:
        state["screen"] = scrolled


poster.adb.tap = adb_tap
poster.adb.shell = adb_shell
poster.device.type_text = type_text
poster.device.back = back
keyboard = {"up": False, "hidden": 0}
poster.device.keyboard_shown = lambda: keyboard["up"]


def hide_kb():
    if keyboard["up"]:
        keyboard["up"] = False
        keyboard["hidden"] += 1
        return True
    return False


poster.device.hide_keyboard = hide_kb
real_back = back


def back_kb():
    real_back()
    if state["screen"] in (expanded, scrolled):
        keyboard["up"] = True          # живьём: вернулись — курсор в названии, клавиатура открыта


poster.device.back = back_kb
poster._vision_point = lambda prompt: (540, 1264, "Добавьте описание")
step = {"op": "yt_details", "upload": [{"rid": "upload_bottom_button"}]}
ctx = {"caption": CAPTION.splitlines()[0], "description": CAPTION,
       "tags": ["звуковые эффекты", "sfx", "монтаж"]}
note = poster._yt_details(step, ctx, tmp, 8)
say(state["typed"] == [CAPTION], "описание впечатано целиком в редактор описания")
say(state["chips"] == ["звуковые эффекты", "sfx", "монтаж"], f"теги — по одному с Enter: {state['chips']}")
say("теги: 3 из 3" in note and "описание:" in note and "[!]" not in note, f"отчёт: {note}")
say(state["screen"] in (scrolled, expanded) and UPLOAD in state["screen"], "в конце — снова экран с «Загрузить»")
say("строка 1451" in state["taps"] and "строка 1247" not in state["taps"],
    "«Атрибуты» — строка ровно над «Свернуть», а не соседняя (там «Добавить в плейлист»)")
say("строка 1187" in state["taps"] and not any(isinstance(t, tuple) for t in state["taps"]),
    "описание — третья строка под названием, без модели зрения")

print("\n--- на ПК без ключа (модели нет) — описание всё равно вписывается ---")
poster._vision_point = lambda prompt: None
state.update(screen=details, chips=[], typed=[], taps=[])
note = poster._yt_details(step, ctx, tmp, 8)
say(state["typed"] == [CAPTION] and "описание:" in note, f"по порядку строк: {note}")

print("\n--- третьей строкой открылось не то (другая раскладка) — спросить модель ---")
asked = []
poster._vision_point = lambda prompt: asked.append(1) or (540, 1264, "Добавьте описание")
state.update(screen=details, chips=[], typed=[], taps=[], third=location)
note = poster._yt_details(step, ctx, tmp, 8)
say(asked and state["typed"] == [CAPTION] and "описание:" in note,
    "экран со списком мест — не редактор: назад, модель нашла строку, описание вписано")
poster._vision_point = lambda prompt: None
state.update(screen=details, chips=[], typed=[], taps=[], third=location)
note = poster._yt_details(step, ctx, tmp, 8)
say(not state["typed"] and "строку описания не нашёл" in note and "теги: 3 из 3" in note,
    "не узнали экран и модели нет — описание НЕ печатается куда попало, теги ставятся: " + note)
state.pop("third")
poster._vision_point = lambda prompt: (540, 1264, "Добавьте описание")

say(keyboard["hidden"] >= 1, f"клавиатуру убирал перед прокруткой: {keyboard['hidden']} раз")

print("\n--- название испортилось (набор жестом) — не публикуем ---")
TITLE.text = CAPTION.splitlines()[0] + " by"
state.update(screen=details, chips=[], typed=[])
try:
    poster._yt_details(step, ctx, tmp, 8)
    stopped = False
except poster.StepFailed as e:
    stopped = "название на экране изменилось" in str(e)
say(stopped, "в названии лишнее «by» — шаг останавливает публикацию")
TITLE.text = CAPTION.splitlines()[0]

print("\n--- рецепт: шаг перед финальной кнопкой ---")
steps = [s["op"] for s in json.load(open(os.path.join(os.path.dirname(HERE), "recipes.json"),
                                             encoding="utf-8"))["shorts"]["steps"]]
say(steps[-2:] == ["yt_details", "publish"], f"…{steps[-3:]}")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
