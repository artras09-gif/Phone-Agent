"""Есть ли правая колонка кнопок в дереве — включая БЕЗЫМЯННЫЕ узлы.

Прошлый вывод «колонки нет» делался по подписям. Но у TikTok крестик листа
«поделиться» тоже без подписи, и нашёлся он по месту и кликабельности
(см. escape.ID_WORDS). Здесь печатается ВСЁ, что лежит в правой трети экрана,
с классом и resource-id, — чтобы решать по фактам, а не по наличию текста.
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


def report(tree, w, h, title):
    print(f"\n  --- {title}: {len(tree)} узлов ---")
    if not tree:
        print("      (дерево не снялось)")
        return
    right = []
    for n in tree:
        x1, y1, x2, y2 = n.bounds
        if x2 <= x1:
            continue
        # Правая треть, выше панели вкладок.
        if x1 >= w * 0.62 and y2 <= h * 0.95:
            right.append(n)
    right.sort(key=lambda n: n.bounds[1])
    if not right:
        print("      в правой трети нет ни одного узла с границами")
        return
    for n in right:
        label = (n.desc or n.text or "").strip()
        print(f"      {label[:34]!r:37} cls={n.cls.split('.')[-1]:14}"
              f" id={n.rid.split('/')[-1][:22]:24} click={str(n.clickable):5}"
              f" {n.bounds}")


def main(apps):
    w, h = adb.screen_size()
    device.ensure_awake()
    device.keep_awake(True)
    log = runlog.Log(live=True)

    for app in apps:
        print("\n" + "=" * 70)
        print("==", app, f"(экран {w}x{h})")
        cfg = session._feed_config(app)
        session._open_feed(cfg, log)
        session._ensure_feed(cfg, log)
        time.sleep(3.0)
        report(ui.dump(retries=1, tolerant=True, timeout=6) or [], w, h, "на ходу")

        x, y = human.like_point(w, h)
        adb.tap(int(x), int(y))
        time.sleep(1.5)
        report(ui.dump(retries=2, tolerant=True, timeout=8) or [], w, h, "на паузе")
        adb.tap(int(x), int(y))
        time.sleep(1.0)

    device.keep_awake(False)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["shorts", "reels"]))
