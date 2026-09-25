"""Что на самом деле лежит в дереве ленты у каждой сети — СЕГОДНЯ.

Вывод «правой колонки кнопок в Shorts и Reels нет» сделан 2026-08-18, с тех
пор приложения обновлялись. От этого зависит всё: есть кнопки в дереве —
ссылки собираются селекторами, как в TikTok; нет — только координатами.

Печатает дерево на паузе и без неё: у TikTok оно снимается только на паузе,
у Shorts и Reels — на ходу.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import adb        # noqa: E402
import device     # noqa: E402
import human      # noqa: E402
import prefs      # noqa: E402
import runlog     # noqa: E402
import session    # noqa: E402
import ui         # noqa: E402

prefs.apply()

# Слова, ради которых всё и затевается.
WANTED = ("лайк", "like", "подел", "share", "коммент", "comment",
          "избранн", "favorit", "нрав", "поиск", "search")


def show(tree, title):
    print(f"\n  --- {title}: {len(tree)} узлов ---")
    if not tree:
        print("      (дерево не снялось)")
        return
    hits = []
    for n in tree:
        label = (n.desc or n.text or "").strip()
        if not label:
            continue
        low = label.lower()
        if any(w in low for w in WANTED):
            hits.append(f"      ★ {label[:60]!r:63} click={n.clickable} {n.bounds}")
    if hits:
        print("\n".join(hits))
    else:
        print("      ничего из нужного; всё, что есть с подписью:")
        shown = 0
        for n in tree:
            label = (n.desc or n.text or "").strip()
            if label and shown < 25:
                shown += 1
                print(f"        {label[:55]!r:58} click={n.clickable} {n.bounds}")


def main(apps):
    w, h = adb.screen_size()
    device.ensure_awake()
    device.keep_awake(True)
    log = runlog.Log(live=True)

    for app in apps:
        print("\n" + "=" * 60)
        print("==", app)
        cfg = session._feed_config(app)
        session._open_feed(cfg, log)
        session._ensure_feed(cfg, log)
        time.sleep(3.0)

        show(ui.dump(retries=1, tolerant=True, timeout=6) or [], "на ходу")

        # На паузе: у TikTok это единственный способ увидеть колонку.
        x, y = human.like_point(w, h)
        adb.tap(int(x), int(y))
        time.sleep(1.5)
        show(ui.dump(retries=2, tolerant=True, timeout=8) or [], "на паузе")
        adb.tap(int(x), int(y))          # вернуть проигрывание
        time.sleep(1.0)

    device.keep_awake(False)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["shorts", "reels"]))
