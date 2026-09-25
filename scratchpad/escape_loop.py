"""Проверка цикла выхода из тупика (escape.py) без телефона и без модели.

Подменяем всё, что ходит наружу: дерево экрана, ответы модели, тапы. Так же
устроен `adb_multi.py` — два телефона там тоже не нужны.

Что проверяем:
  1. крестик находится и нажимается, после него лента возвращается;
  2. опасные кнопки («Разрешить», «Подписаться») модели даже не показывают;
  3. модель, зациклившуюся на одной кнопке, перебивает «назад»;
  4. живая лента (дерево не снимается) не трогается вообще;
  5. выдуманный номер кнопки не приводит к тапу.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import adb  # noqa: E402
import device  # noqa: E402
import escape  # noqa: E402
import human  # noqa: E402
import ui  # noqa: E402
import vision  # noqa: E402

ok = True


def say(good, text):
    global ok
    ok &= good
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


def node(text="", desc="", rid="", bounds=(0, 0, 100, 100), clickable=True):
    return ui.Node({
        "text": text, "content-desc": desc, "resource-id": rid,
        "class": "android.widget.Button", "package": "com.zhiliaoapp.musically",
        "clickable": "true" if clickable else "false", "enabled": "true",
        "bounds": f"[{bounds[0]},{bounds[1]}][{bounds[2]},{bounds[3]}]",
    })


# Окно «Подпишитесь на друзей»: крестик без подписи, список людей и кнопки,
# которые жать нельзя. Размеры взяты с настоящего кадра (см. `like_current`).
POPUP = [
    node(desc="Закрыть", rid="com.zhiliaoapp.musically:id/close", bounds=(950, 520, 1030, 600)),
    node(text="Подпишитесь на друзей", clickable=False, bounds=(60, 620, 1020, 700)),
    node(text="Подписаться", bounds=(700, 800, 1000, 900)),
    node(text="Разрешить", bounds=(60, 1700, 1020, 1800)),
    node(text="Не сейчас", bounds=(60, 1850, 1020, 1950)),
]

PERMISSION = [
    node(text="Разрешить приложению доступ к контактам?", clickable=False,
         bounds=(60, 900, 1020, 1100)),
    node(text="Разрешить", bounds=(600, 1200, 1000, 1300)),
    node(text="Не разрешать", bounds=(100, 1200, 550, 1300)),
]

taps = []
backs = []
answers = []
screens = []


def fake_dump(*a, **kw):
    return screens.pop(0) if screens else []


def fake_ask(png, prompt, **kw):
    fake_ask.prompts.append(prompt)
    return answers.pop(0) if answers else "{}"


fake_ask.prompts = []

ui.dump = fake_dump
ui.tap_node = lambda n: taps.append(escape._label_of(n))
vision.ask = fake_ask
vision.frame_signature = lambda *a, **kw: [0]
vision.frames_differ = lambda a, b: 0.0
device.back = lambda: backs.append(1)
adb.screen_size = lambda: (1080, 2400)
adb.exec_out = lambda *a, **kw: b"x" * 900_000
vision.looks_blank = lambda png, **kw: False
human.pause = lambda lo, hi: None
human.scroll_feed = lambda *a, **kw: None


def run(goal="лента не листается", steps=5):
    del taps[:], backs[:], fake_ask.prompts[:]
    return escape.escape(goal=goal, max_steps=steps)


# --- 1. крестик закрывает окно ---------------------------------------
print("--- окно «Подпишитесь на друзей» ---")
screens[:] = [POPUP, []]          # второй дамп пуст = вернулись в ленту
answers[:] = ['{"кнопка": 1}']
rep = run()
say(rep["ok"], f"выбрались: {rep['почему']}")
say(taps == ["Закрыть"], f"нажали именно крестик: {taps}")

# Список кнопок и надписи экрана — разные вещи: надписи нужны, чтобы
# модель поняла, что за экран, а нажать можно только то, что в списке.
menu_text = fake_ask.prompts[0]
say("[1] «Закрыть»" in menu_text, "крестик стоит первым в списке")

# --- 2. опасные кнопки не показываем ---------------------------------
print("\n--- запреты ---")
say("Подписаться" not in menu_text, "«Подписаться» не предложена")
say("Разрешить»" not in menu_text, "«Разрешить» не предложена")
say("Не сейчас" in menu_text, "«Не сейчас» осталась")

screens[:] = [PERMISSION, []]
answers[:] = ['{"кнопка": 1}']
rep = run()
say(taps == ["Не разрешать"], f"отказ от прав разрешён: {taps}")

# --- 3. зацикливание перебивается «назад» ----------------------------
print("\n--- модель зациклилась ---")
same = '{"кнопка": 1}'
screens[:] = [POPUP, POPUP, POPUP, POPUP, POPUP, POPUP, POPUP, POPUP]
answers[:] = [same] * 5
rep = run(steps=3)
say(len(taps) == 1, f"тапнули один раз, дальше не повторяли: {taps}")
say(bool(backs), "вместо повтора нажали «назад»")

# --- 4. живая лента не трогается -------------------------------------
print("\n--- ложная тревога: живая лента ---")
screens[:] = [[]]
answers[:] = [same]
rep = run()
say(not taps and not backs, "на живой ленте не нажали ничего")
say("лента" in rep["почему"], f"причина названа: {rep['почему']}")

# --- 5. выдуманный номер кнопки --------------------------------------
print("\n--- модель придумала кнопку ---")
screens[:] = [POPUP, POPUP]
answers[:] = ['{"кнопка": 9}']
rep = run()
say(not taps, "по несуществующему номеру не тапнули")
say("списке нет" in rep["почему"], f"причина названа: {rep['почему']}")

# --- 6. мусор вместо JSON --------------------------------------------
print("\n--- модель ответила мусором ---")
screens[:] = [POPUP, POPUP]
answers[:] = ["конечно! вот что я вижу на экране..."]
rep = run()
say(not taps and not rep["ok"], "мусорный ответ ничего не нажал")

# --- 7. встроено ли это в сессию ------------------------------------
# Самый обидный вид поломки — когда модуль работает, а звать его некому.
print("\n--- лесенка _unstick зовёт выход ---")
import runlog  # noqa: E402
import session  # noqa: E402

session.ui.dismiss_popup = lambda nodes=None: False
session._ensure_feed = lambda cfg, log, timeout=4: True
called = {}


def fake_escape(**kw):
    called.update(kw)
    return {"ok": True, "steps": ["tap «Закрыть»"], "почему": "помогло", "экран": ""}


session.escape.escape = fake_escape
state = {"popups": 1, "blind": False, "rescues": 0, "id": "test",
         "cfg": {"package": "com.zhiliaoapp.musically", "feed_markers": []}}
log = runlog.Log(live=False)
what = session._unstick(state, "com.zhiliaoapp.musically", log)
say(what == "выбрался сам", f"первая ступень пускает модель: {what!r}")
say(called.get("package") == "com.zhiliaoapp.musically",
    "пакет передан — есть защита от ухода из приложения")
say(called.get("allow_done") is False, "у TikTok ответ done запрещён")
say(callable(called.get("grab")), "кадры снимает сама сессия")

session.config.ESCAPE_ENABLED = False
state["popups"] = 1
say(session._unstick(state, "com.zhiliaoapp.musically", log).startswith("кнопки не нашлось"),
    "выключатель возвращает старое поведение")
session.config.ESCAPE_ENABLED = True

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
