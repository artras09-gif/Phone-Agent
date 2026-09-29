"""Руки в выходе из тупика: модель нажимает точку на полном снимке.

Без телефона и без модели: ответы модели, дерево и нажатия подменены. Живой
замер меткости — `hands_bench.py` (ходит в настоящую модель).

Что проверяем:
  1. проверка меткости: пиксели / тысячные / промах -> рук не дают;
  2. притяжение точки к кнопке дерева (промах модели 35-60 пикс.);
  3. предохранители: запрещённая подпись, поле ввода, точка вне экрана,
     «опасно» при взгляде вблизи, молчание вблизи;
  4. на листе «Поделиться» рук нет — только список и «назад»;
  5. окно поверх играющего видео (дерева нет, лента стоит): раньше агент
     уходил ни с чем, теперь нажимает — и проверяет, что лента пошла;
  6. ложная тревога (лента листается) — не нажимает ничего.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import adb      # noqa: E402
import config   # noqa: E402
import device   # noqa: E402
import escape   # noqa: E402
import human    # noqa: E402
import picture  # noqa: E402
import ui       # noqa: E402
import vision   # noqa: E402

ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


def node(text="", desc="", rid="", bounds=(0, 0, 100, 100), clickable=True,
         cls="android.widget.Button"):
    return ui.Node({
        "text": text, "content-desc": desc, "resource-id": rid, "class": cls,
        "package": "com.instagram.android",
        "clickable": "true" if clickable else "false", "enabled": "true",
        "bounds": f"[{bounds[0]},{bounds[1]}][{bounds[2]},{bounds[3]}]",
    })


with open(os.path.join(HERE, "check_tiktok.png"), "rb") as f:
    SHOT = f.read()                  # настоящий снимок 1080x2400

answers, prompts, taps, points, backs, screens, moves = [], [], [], [], [], [], []


def fake_ask(png, prompt, **kw):
    prompts.append(prompt)
    return answers.pop(0) if answers else "{}"


vision.ask = fake_ask
vision.available = lambda: (True, "проверочная-модель")
vision.frame_signature = lambda *a, **kw: [0]
vision.frames_differ = lambda a, b: 0.0
vision.looks_blank = lambda png, *a, **kw: False
ui.dump = lambda *a, **kw: screens.pop(0) if screens else []
ui.tap_node = lambda n: taps.append(escape._label_of(n) or "значок")
adb.tap = lambda x, y: points.append((x, y))
adb.screen_size = lambda: (1080, 2400)
device.back = lambda: backs.append(1)
human.pause = lambda lo, hi: None
human.scroll_feed = lambda *a, **kw: None
escape._full_shot = lambda: (SHOT, 1080, 2400)
escape._feed_moves = lambda w, h: moves.pop(0) if moves else False


def reset():
    for bag in (answers, prompts, taps, points, backs, screens, moves):
        del bag[:]
    escape._HANDS.clear()


# --- 1. проверка меткости ---------------------------------------------
print("--- проверка меткости модели ---")
# Проверочные точки: (810, 1896), (199, 504), (928, 792).
PIXELS = ['{"x": 867, "y": 1878}', '{"x": 165, "y": 495}', '{"x": 905, "y": 800}']
reset()
answers[:] = list(PIXELS)                 # первые два — настоящие ответы deepseek-flash
way = escape.hands_ready(1080, 2400)
say(way == "pixels", f"ответы в пикселях узнаны: {way}")
say(len(prompts) == 3 and "красный круг" in prompts[0], "спросили про три проверочные точки")
answers[:] = []
say(escape.hands_ready(1080, 2400) == "pixels" and not prompts[3:],
    "второй раз не спрашивают — запомнено")

reset()
answers[:] = ['{"x": 750, "y": 790}', '{"x": 184, "y": 210}', '{"x": 859, "y": 330}']
say(escape.hands_ready(1080, 2400) == "permille", "тысячные доли узнаны")
x, y = escape._to_image(750, 790, "permille", 1080, 2400)
say(abs(x - 810) < 3 and abs(y - 1896) < 3, f"и переводятся в пиксели: ({x:.0f}, {y:.0f})")

reset()
answers[:] = ['{"x": 300, "y": 400}', '{"x": 900, "y": 2000}', '{"x": 100, "y": 100}']
say(escape.hands_ready(1080, 2400) is None, "промахнулась — рук не дают")

reset()
# Модель ужимает снимок у себя до 540x1200 и называет точки на ужатом.
answers[:] = ['{"x": 405, "y": 948}', '{"x": 100, "y": 252}', '{"x": 464, "y": 396}']
way = escape.hands_ready(1080, 2400)
say(isinstance(way, tuple) and abs(way[1] - 2) < 0.05, f"своя шкала ×2 узнана: {way}")

reset()
# Урок живого замера: оборванный ответ на одну точку дал бы ложную шкалу.
answers[:] = ['{"x": 867, "y": 1878}', '{"x": 198, "', '{"x": 905, "y": 800}']
say(escape.hands_ready(1080, 2400) is None and not escape._HANDS,
    "ответила не на все точки — рук нет, но и не запомнено")

reset()
answers[:] = ['{"x": 1000, "y": 2300}', '{"x": 100, "y": 300}', '{"x": 600, "y": 1300}']
say(escape.hands_ready(1080, 2400) is None,
    "разброс множителя по точкам — своей шкале не верим")

reset()
real_ask = vision.ask


def broken(*a, **kw):
    raise vision.VisionError("нет связи")


vision.ask = broken
say(escape.hands_ready(1080, 2400) is None and not escape._HANDS,
    "нет связи — не приговор: результат не запомнен")
vision.ask = real_ask

config.ESCAPE_HANDS = False
reset()
say(escape.hands_ready(1080, 2400) is None and not prompts, "выключатель ESCAPE_HANDS работает")
config.ESCAPE_HANDS = True

# --- 2. притяжение к кнопке -------------------------------------------
print("\n--- притяжение точки к кнопке ---")
REELS_TAB = node(desc="Reels", bounds=(270, 2150, 380, 2255))
POST = node(desc="Фото публикации", bounds=(0, 730, 1080, 2140))
TABS = [node(desc="Главная", bounds=(50, 2150, 160, 2255)), REELS_TAB, POST]
got, small = escape._node_at(TABS, 270, 2098, 1080, 2400)       # настоящий промах
say(got is REELS_TAB and small, "точка на 50 пикс. выше вкладки -> вкладка, а не пост")
got, small = escape._node_at(TABS, 320, 2200, 1080, 2400)
say(got is REELS_TAB, "точка на вкладке -> вкладка")
got, small = escape._node_at(TABS, 540, 1200, 1080, 2400)
say(got is POST and not small, "посреди поста -> крупный контейнер, жмётся сама точка")
got, small = escape._node_at([], 540, 1200, 1080, 2400)
say(got is None, "дерева нет -> кнопки нет")

# --- 3. предохранители ------------------------------------------------
print("\n--- предохранители ---")


def hint(x, y, what="крестик"):
    return {"действие": "point", "x": x, "y": y, "img": (x, y), "что": what}


reset()
FOLLOW = [node(text="Подписаться", bounds=(700, 800, 1000, 900))]
did, why = escape._hand_tap(hint(850, 850), FOLLOW, 1080, 2400, SHOT, 1080, 2400, "Reels")
say(did is None and not taps and not points, f"«Подписаться» под пальцем — не нажато ({why})")

reset()
FIELD = [node(text="", bounds=(40, 700, 700, 780), cls="android.widget.EditText")]
did, why = escape._hand_tap(hint(300, 740), FIELD, 1080, 2400, SHOT, 1080, 2400, "Reels")
say(did is None and not points, f"поле ввода — не нажато ({why})")

reset()
did, why = escape._hand_tap(hint(1500, 300), [], 1080, 2400, SHOT, 1080, 2400, "Reels")
say(did is None and not points and not prompts, f"точка вне экрана — не нажато ({why})")

reset()
did, why = escape._hand_tap(hint(270, 2098), TABS, 1080, 2400, SHOT, 1080, 2400, "Reels")
say(taps == ["Reels"] and not prompts, f"вкладка с подписью жмётся без второго вопроса: {did}")

reset()
answers[:] = ['{"что": "кнопка Поделиться", "опасно": true}']
did, why = escape._hand_tap(hint(960, 1590), [], 1080, 2400, SHOT, 1080, 2400, "TikTok")
say(did is None and not points, f"вблизи «опасно» — не нажато ({why})")
say(prompts and "Красное кольцо" in prompts[0], "вблизи модель видит кольцо на месте нажатия")

reset()
answers[:] = ['{"что": "крестик"}']
did, why = escape._hand_tap(hint(960, 1590), [], 1080, 2400, SHOT, 1080, 2400, "TikTok")
say(did is None and not points, f"вблизи промолчала про опасность — не нажато ({why})")

reset()
answers[:] = ['{"что": "подписаться на автора", "опасно": false}']
did, why = escape._hand_tap(hint(960, 1590), [], 1080, 2400, SHOT, 1080, 2400, "TikTok")
say(did is None and not points, f"«опасно: false», но это подписка — не нажато ({why})")

reset()
# Настоящий ответ живого замера: «+» в углу Reels ведёт в камеру.
answers[:] = ['{"что": "кнопка + (создать новый Reels)", "опасно": false}']
did, why = escape._hand_tap(hint(653, 299), [], 1080, 2400, SHOT, 1080, 2400, "Reels")
say(did is None and not points, f"«создать новый Reels» — не нажато ({why})")

reset()
answers[:] = ['{"что": "затемнённое видео над меню «Поделиться»", "опасно": false}']
did, why = escape._hand_tap(hint(540, 400), [], 1080, 2400, SHOT, 1080, 2400, "Reels")
say(points, f"«над меню «Поделиться»» — не кнопка «Поделиться», нажато ({did or why})")

reset()
# Настоящий случай замера: целилась в крестик, а вблизи — творог в миске.
answers[:] = ['{"что": "кучки творога в миске", "кнопка": false, "опасно": false}']
did, why = escape._hand_tap(hint(831, 1040, "крестик закрытия мини-плеера"), [],
                            1080, 2400, SHOT, 1080, 2400, "TikTok")
say(did is None and not points, f"целилась в крестик, а там видео — не нажато ({why})")

reset()
answers[:] = ['{"что": "лицо человека в видео", "кнопка": false, "опасно": false}']
did, why = escape._hand_tap(hint(540, 400, "область видео над панелью"), [],
                            1080, 2400, SHOT, 1080, 2400, "Reels")
say(points, f"целилась в видео над листом, вблизи видео — нажато ({did or why})")

reset()
answers[:] = ['Под кольцом крестик. {"что": "крестик мини-плеера", "опасно": fal']
did, why = escape._hand_tap(hint(960, 1590), [], 1080, 2400, SHOT, 1080, 2400, "TikTok")
say(did is None and not points, f"оборванный ответ без вердикта — не нажато ({why})")

reset()
answers[:] = ['{"что": "крестик мини-плеера", "x": 200, "y": 190, "опасно": false}']
did, why = escape._hand_tap(hint(960, 1590), [], 1080, 2400, SHOT, 1080, 2400, "TikTok")
say(points and abs(points[0][0] - 960) <= 5 and abs(points[0][1] - 1590) <= 5,
    f"вблизи точку НЕ двигают, даже если модель предлагает: {points} ({did})")

# --- 4. лист «Поделиться»: рук нет -----------------------------------
print("\n--- лист «Поделиться»: рук нет ---")
SHEET = [
    node(text="Поделиться", clickable=False, bounds=(40, 1440, 600, 1520)),
    node(text="Никита", bounds=(780, 1100, 1020, 1350)),
    node(text="Telegram", bounds=(40, 1580, 200, 1780)),
]
reset()
escape._HANDS[("проверочная-модель", 1080, 2400)] = "pixels"
screens[:] = [SHEET, SHEET]
# Список на таком листе урезан до пустого (контакты и «Telegram» — не
# закрывающие кнопки), поэтому модели сразу задаётся второй вопрос.
answers[:] = ['{"иначе": "back"}']
rep = escape.escape(goal="лента не листается", max_steps=1, stuck=True)
say(not any("Куда нажать" in p for p in prompts), "точку не спрашивали вовсе")
say(not taps and not points and backs, f"ничего не нажато, ушли «назад» ({rep['steps']})")

# --- 5. окно поверх играющего видео ----------------------------------
print("\n--- окно поверх видео (дерева нет, лента стоит) ---")
reset()
screens[:] = [[]]
moves[:] = [False, True]           # до нажатия лента стоит, после — пошла
answers[:] = PIXELS + [                                              # проверка
              '{"x": 960, "y": 1590, "что": "крестик мини-плеера"}',    # точка
              '{"что": "крестик", "опасно": false}']                    # вблизи
rep = escape.escape(goal="лента не листается", max_steps=3, stuck=True,
                    package=None, feed="TikTok")
say(rep["ok"], f"выбрались: {rep['почему']}")
say(len(points) == 1, f"нажата одна точка: {points}")
say(any("TikTok" in p for p in prompts if "Куда нажать" in p), "модели сказано, какая лента нужна")

reset()
screens[:] = [[]]
rep = escape.escape(goal="лента не листается", max_steps=3, stuck=False)
say(not points and not taps and "это лента" in rep["почему"],
    "без признака «лента встала» пустое дерево — это лента, как раньше")

# --- 6. ложная тревога -----------------------------------------------
print("\n--- ложная тревога ---")
reset()
escape._HANDS[("проверочная-модель", 1080, 2400)] = "pixels"
screens[:] = [[]]
moves[:] = [True]
rep = escape.escape(goal="лента не листается", max_steps=3, stuck=True)
say(rep["ok"] and not points and not prompts, f"лента листается — ничего не нажато ({rep['почему']})")

reset()
escape._HANDS[("проверочная-модель", 1080, 2400)] = None
screens[:] = [[]]
rep = escape.escape(goal="лента не листается", max_steps=3, stuck=True)
say(not points and "это лента" in rep["почему"], "модели без меткости рук не дали — как раньше")

# --- 7. фрагмент и снимок ---------------------------------------------
print("\n--- картинки ---")
crop = picture.crop_png(SHOT, (660, 1380, 1080, 1800), ring=(300, 210))
say(picture.size_of(crop) == (420, 420), "фрагмент вырезается 1:1")
raw = bytes(16) + bytes(4)
import struct  # noqa: E402

raw = struct.pack("<IIII", 2, 2, 1, 0) + b"\xff\x00\x00\xff" * 4
say(picture.size_of(picture.from_screencap(raw, 1)) == (2, 2), "сырой screencap разбирается")
say(picture.from_screencap(b"\x00" * 10) is None, "обрезанный — None")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
