"""Галерея TikTok без id (2026-11-13): наш ролик находится по подписи длины.

12.11 пост в TikTok сорвался на обоих телефонах: клетки галереи сменили id
(eve/ioj -> ipj), и программа сочла, что галерея не открылась. Дерево ниже —
первая строка галереи, снятая живьём 13.11. Проверяется, что клетка находится
и по новому id, и без него — по подписи «00:09», и что выбирается первая.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config     # noqa: E402

config.LOG_DIR = tempfile.mkdtemp(prefix="pa_ttgal_")

import poster     # noqa: E402
import ui         # noqa: E402

ok = True


def check(text, good):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


def node(rid, bounds, text="", clickable=False):
    return ui.Node({"text": text, "content-desc": "", "clickable": "true" if clickable else "false",
                    "resource-id": "com.zhiliaoapp.musically:id/" + rid, "class": "android.view.View",
                    "package": "com.zhiliaoapp.musically", "enabled": "true",
                    "bounds": "[%d,%d][%d,%d]" % bounds})


def row(prefix, length_x2, seconds, y=441):
    """Клетка как в живом дереве: картинка, кружок выбора, кнопка в нём, длина."""
    x1 = length_x2 - 418
    return [node(prefix + "img", (x1, y, length_x2, y + 423)),
            node(prefix + "sel", (length_x2 - 143, y, length_x2, y + 143), clickable=True),
            node(prefix + "btn", (length_x2 - 98, y + 20, length_x2 - 20, y + 98), clickable=True),
            node(prefix + "len", (length_x2 - 157, y + 348, length_x2, y + 423), text=seconds)]


# Без камеры поверх: в живом дереве она есть, но ни одной подписи длины в ней нет.
grid = row("ozd_", 425, "00:09") + row("ozd_", 849, "00:14") + row("ozd_", 1273, "00:14") \
    + row("ozd_", 425, "00:12", y=871)
cells = poster._cells_by_duration(grid)
check(f"клетки найдены по длине, без id: {len(cells)}", len(cells) == 4)
check("первая — левая верхняя", cells and cells[0].bounds[:2] == (327, 461))
check("клетка — мелкая кнопка выбора, а не картинка во всю клетку",
      cells and cells[0].rid.endswith("btn"))

recipe = poster.load_recipes()["tiktok"]
step = next(s for s in recipe["steps"] if s["op"] == "pick_video")
check("маршрут ищет клетку по длине, если id опять сменились", step.get("cell_by_duration"))
check("rev поднят — правка дойдёт и до старых установок", recipe["rev"] >= 4)

tapped = []
poster.ui.tap_node = lambda n: tapped.append(n)
poster.human.pause = lambda *a: None
poster.time.sleep = lambda *a: None
poster._snap = lambda *a: None
poster.ui.dump = lambda **kw: grid                    # id в дереве — не те, что в маршруте
line = poster._pick_video(dict(step, timeout=2), {"video_name": "x.mp4", "video_seconds": 9.0},
                          config.LOG_DIR, 6)
check(f"незнакомые id — ролик всё равно выбран: {line}", tapped and tapped[0] is cells[0])
tapped.clear()
try:
    poster._pick_video(dict(step, timeout=1), {"video_name": "x.mp4", "video_seconds": 30.0},
                       config.LOG_DIR, 6)
    refused = False
except poster.StepFailed as e:
    refused = "Чужой не выбираю" in str(e)
check("первым стоит ролик другой длины — не выбираем", refused and not tapped)

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
