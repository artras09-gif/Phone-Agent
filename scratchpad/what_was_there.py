"""Куда била СТАРАЯ координата «поделиться» в Reels.

До 2026-08-30 у Reels в рецепте стояло `share_point: [0.945, 0.755]`, и по
этой точке агент открывал лист «поделиться». Если она попадала не в
самолётик, а в соседнюю кнопку, дальше открывался другой список — и запасной
выбор строки «по месту» мог нажать в нём что угодно.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import adb      # noqa: E402
import device   # noqa: E402
import prefs    # noqa: E402
import runlog   # noqa: E402
import session  # noqa: E402
import ui       # noqa: E402

prefs.apply()

OLD_POINT = (0.945, 0.755)


def main():
    w, h = adb.screen_size()
    device.ensure_awake()
    log = runlog.Log(live=True)
    cfg = session._feed_config("reels")
    session._open_feed(cfg, log)
    time.sleep(3.0)
    adb.tap(w // 2, int(h * 0.45))          # пауза, чтобы снялось дерево
    time.sleep(1.8)

    x, y = int(OLD_POINT[0] * w), int(OLD_POINT[1] * h)
    print(f"старая точка: доли {OLD_POINT} = пиксели ({x}, {y})\n")

    tree = ui.dump(retries=2, tolerant=True, timeout=9) or []
    print(f"узлов: {len(tree)}")
    hit = []
    for n in tree:
        x1, y1, x2, y2 = n.bounds
        label = (n.desc or n.text or "").strip()
        if x1 <= x <= x2 and y1 <= y <= y2 and label:
            hit.append((n.area, label, n.bounds, n.clickable))
    if not hit:
        print("  в эту точку не попадает НИ ОДИН подписанный узел —")
        print("  то есть тап уходил в пустоту между кнопками")
    for area, label, bounds, click in sorted(hit):
        print(f"  {label[:40]!r:43} click={click} {bounds}")

    print("\nсоседи по правой колонке (для сравнения):")
    for n in sorted(tree, key=lambda n: n.bounds[1]):
        label = (n.desc or n.text or "").strip()
        if label and n.bounds[0] > w * 0.8 and n.clickable:
            print(f"  {label[:40]!r:43} {n.bounds}")

    adb.tap(w // 2, int(h * 0.45))          # вернуть проигрывание
    return 0


if __name__ == "__main__":
    sys.exit(main())
