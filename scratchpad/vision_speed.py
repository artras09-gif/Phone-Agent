"""Сколько на самом деле думает модель — на РАЗНЫХ кадрах.

Мерить на одном и том же кадре бессмысленно: срабатывает кэш префикса, и
второй вызов выходит впятеро быстрее первого (наступали на это). Поэтому
берутся разные кадры из настоящих сессий.

Печатает медиану и разброс отдельно по разбору кадра и по судье темы.
"""
import glob
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config    # noqa: E402
import prefs     # noqa: E402
import vision    # noqa: E402

prefs.apply()


def newest_frames(limit=8):
    roots = [os.path.join(config.BASE, "frames"),
             os.path.join(config.BASE, "devices", "*", "frames")]
    found = []
    for root in roots:
        found += glob.glob(os.path.join(root, "*", "*.png"))
    found.sort(key=os.path.getmtime, reverse=True)
    # Разные кадры разных роликов: подряд идущие часто почти одинаковы.
    picked, seen = [], set()
    for path in found:
        folder = os.path.dirname(path)
        if folder in seen and len(picked) < limit:
            continue
        seen.add(folder)
        picked.append(path)
        if len(picked) >= limit:
            break
    if len(picked) < limit:                       # не хватило разных сессий
        picked = found[:limit]
    return picked


def main():
    print("модель:", vision.where())
    frames = newest_frames(8)
    if not frames:
        print("кадров нет — сначала прогони сессию")
        return 1

    vision.warm_up()
    look, judge = [], []
    for path in frames:
        with open(path, "rb") as f:
            png = f.read()
        t0 = time.time()
        info = vision.describe_frame(png)
        look.append(time.time() - t0)
        theme = (info or {}).get("тема") or ""
        t0 = time.time()
        vision.judge_topic(theme, "политика")
        judge.append(time.time() - t0)
        print(f"  {os.path.basename(path):22} кадр {look[-1]:5.2f}с  "
              f"тема {judge[-1]:5.2f}с  «{theme[:38]}»")

    print(f"\n  разбор кадра: медиана {statistics.median(look):.2f}с  "
          f"(от {min(look):.2f} до {max(look):.2f})")
    print(f"  судья темы:   медиана {statistics.median(judge):.2f}с  "
          f"(от {min(judge):.2f} до {max(judge):.2f})")
    print(f"  на ролик выходит ~{statistics.median(look) + statistics.median(judge):.2f}с")
    return 0


if __name__ == "__main__":
    sys.exit(main())
