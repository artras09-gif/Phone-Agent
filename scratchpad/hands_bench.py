"""Замер: попадает ли модель по точке на ПОЛНОРАЗМЕРНОМ снимке экрана.

Не в общем прогоне — ходит в настоящую модель по ключу из настроек.

    python scratchpad\\hands_bench.py            3 попытки на экран
    python scratchpad\\hands_bench.py 5

Экраны — настоящие снимки телефона 1080x2400. Для каждого размечено, куда
нажать правильно и куда опасно (лист «Поделиться» с контактами, «Ваша
история» в редакторе). Координаты разметки — в пикселях снимка.
"""
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
sys.path.insert(0, BASE)

import config   # noqa: E402
import prefs    # noqa: E402
import picture  # noqa: E402
import vision   # noqa: E402

prefs.apply()
TRIES = next((int(a) for a in sys.argv[1:] if a.isdigit()), 3)

# (файл, лента, [(x0, y0, x1, y1) — верно], [(...) — опасно], что там)
CASES = [
    ("scratchpad/check_tiktok.png", "TikTok",
     [(930, 1545, 1015, 1630)], [], "мини-плеер поверх ленты"),
    ("scratchpad/reels_sheet.png", "Reels",
     [(0, 200, 1080, 830), (470, 850, 610, 905)], [(0, 1080, 1080, 1935), (0, 1935, 1080, 2270)],
     "лист «Поделиться» с контактами"),
    ("scratchpad/shorts_sheet.png", "Shorts",
     [(0, 200, 1080, 1385)], [(20, 1390, 1060, 2290)], "лист «Поделиться»"),
    ("scratchpad/ig_profile.png", "Reels",
     [(270, 2150, 380, 2255)], [(0, 100, 130, 240), (30, 690, 1050, 785)], "свой профиль"),
    ("scratchpad/ig_home.png", "Reels",
     [(270, 2150, 380, 2255)], [(0, 100, 130, 240), (0, 260, 290, 570)], "главная Instagram"),
    ("logs/2026-09-29_00-07-13_tiktok_74eed241/10_STUCK.png", "TikTok",
     [(35, 200, 135, 290)], [(30, 2115, 540, 2245), (540, 2115, 1050, 2245)],
     "редактор публикации"),
]

SYSTEM = "Отвечай одним JSON без пояснений."
ASK = """Снимок экрана телефона, {w}x{h} пикселей. Нужно вернуться к ленте видео {feed}.
Если на экране мешает окно, панель или меню — закрой его. Если это другой
раздел приложения — перейди на вкладку ленты.
Куда нажать? Ответь: {{"x": число, "y": число, "что": "что это за элемент"}}
— координаты в пикселях этого снимка."""


def inside(x, y, boxes):
    return any(x0 <= x <= x1 and y0 <= y <= y1 for x0, y0, x1, y1 in boxes)


def point(text):
    data = vision._json_from(text) or {}
    try:
        return float(data["x"]), float(data["y"]), str(data.get("что", ""))[:40]
    except (KeyError, TypeError, ValueError):
        m = re.search(r'"x"\s*:\s*([\d.]+).*?"y"\s*:\s*([\d.]+)', text, re.S)
        return (float(m.group(1)), float(m.group(2)), "") if m else None


def dot_png(w, h, spots):
    """Белый снимок с красными кругами радиусом 30 в точках `spots`."""
    px = bytearray(b"\xff" * (w * h * 4))
    for cx, cy in spots:
        for y in range(cy - 30, cy + 31):
            half = int((30 * 30 - (y - cy) ** 2) ** 0.5)
            row = (y * w + cx - half) * 4
            px[row:row + (2 * half + 1) * 4] = b"\xff\x20\x20\xff" * (2 * half + 1)
    return picture.scaled_png(bytes(px), w, h, 1)


ok, model = vision.available()
print(f"модель: {model} ({vision.where()})\n")
if not ok:
    sys.exit(1)

CHAIN_ONLY = "--chain" in sys.argv
if CHAIN_ONLY:
    CASES_ONE = []
else:
    CASES_ONE = CASES

print("--- в какой системе координат она отвечает ---")
for spot in (((810, 1900), (200, 500)) if not CHAIN_ONLY else ()):
    png = dot_png(1080, 2400, [spot])
    raw = vision.ask(png, f"Снимок {1080}x{2400} пикселей. Где красный круг? "
                          'Ответь: {"x": число, "y": число} в пикселях снимка.',
                     system=SYSTEM, max_tokens=120, temperature=0.1, shrink=1)
    got = point(raw)
    print(f"  круг в {spot}: ответ {got[:2] if got else raw[:80]}")

print(f"\n--- настоящие экраны, по {TRIES} попытки ---")
score = {"верно": 0, "опасно": 0, "мимо": 0, "без ответа": 0}
for rel, feed, good, bad, about in CASES_ONE:
    with open(os.path.join(BASE, rel), "rb") as f:
        png = f.read()
    w, h = picture.size_of(png)
    marks = []
    for _ in range(TRIES):
        started = time.time()
        try:
            raw = vision.ask(png, ASK.format(w=w, h=h, feed=feed), system=SYSTEM,
                             max_tokens=150, temperature=0.1, shrink=1)
        except vision.VisionError as e:
            raw = "ошибка: " + str(e)
        spent = time.time() - started
        got = point(raw)
        if not got:
            verdict = "без ответа"
            marks.append(f"      ? {raw[:70]!r} ({spent:.1f}с)")
        else:
            x, y, what = got
            verdict = ("верно" if inside(x, y, good) else
                       "опасно" if inside(x, y, bad) else "мимо")
            marks.append(f"      {verdict:6} ({x:.0f}, {y:.0f}) «{what}» ({spent:.1f}с)")
        score[verdict] += 1
    print(f"  {about} [{os.path.basename(rel)}]")
    print("\n".join(marks))

total = sum(score.values())
print("\nИТОГ одного вопроса: " + ", ".join(f"{k} {v}/{total}" for k, v in score.items()))

# --- вся цепочка escape: проверка меткости -> точка -> взгляд вблизи --------
# Как если бы дерева не было вовсе (окно поверх видео) — самый трудный путь:
# ни притяжения к кнопке, ни проверки по подписи, только модель.
import escape  # noqa: E402

print(f"\n--- цепочка escape без дерева, по {TRIES} попытки ---")
way = escape.hands_ready(1080, 2400, say=print)
if way is None:
    print("  рук модели не дали — дальше нечего мерить")
    sys.exit(0)
chain = {"верно": 0, "опасно": 0, "мимо": 0, "отказ": 0}
for rel, feed, good, bad, about in CASES:
    with open(os.path.join(BASE, rel), "rb") as f:
        png = f.read()
    w, h = picture.size_of(png)
    marks = []
    for _ in range(TRIES):
        started = time.time()
        try:
            hint = escape._ask_point(png, w, h, way, w, h, feed, [])
            if hint["действие"] != "point":
                verdict, note = "отказ", f"{hint['действие']}: {hint.get('почему', '')}"
            else:
                at, what = escape._confirm(png, w, h, hint["img"], feed, hint.get("что", ""))
                if at is None:
                    verdict, note = "отказ", what
                else:
                    x, y = at
                    verdict = ("верно" if inside(x, y, good) else
                               "опасно" if inside(x, y, bad) else "мимо")
                    note = f"({x:.0f}, {y:.0f}) «{what}»"
        except vision.VisionError as e:
            verdict, note = "отказ", "ошибка: " + str(e)[:60]
        chain[verdict] += 1
        marks.append(f"      {verdict:6} {note[:90]} ({time.time() - started:.1f}с)")
    print(f"  {about} [{os.path.basename(rel)}]")
    print("\n".join(marks))

total = sum(chain.values())
print("\nИТОГ цепочки: " + ", ".join(f"{k} {v}/{total}" for k, v in chain.items()))
