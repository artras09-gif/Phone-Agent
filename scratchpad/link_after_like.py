"""Почему в СЕССИИ число лайков не читается, а в `link-probe` читается.

Разница между ними ровно одна: в сессии `_save_link` зовётся сразу после
НАШЕГО двойного тапа-лайка. Здесь этот порядок и воспроизводится, а на
неудаче печатается всё дерево целиком — видно, чем оно отличается.

Лайк ставится настоящий: иначе состояния «уже лайкнуто» не получить.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import adb          # noqa: E402
import config       # noqa: E402
import device       # noqa: E402
import human        # noqa: E402
import prefs        # noqa: E402
import runlog       # noqa: E402
import session      # noqa: E402
import ui           # noqa: E402

prefs.apply()
last = []


def main(app="tiktok", tries=3):
    cfg = session._feed_config(app)
    w, h = adb.screen_size()
    device.ensure_awake()
    device.keep_awake(True)

    log = runlog.Log(live=True)
    session._open_feed(cfg, log)
    session._ensure_feed(cfg, log)

    real_dump = ui.dump

    def dump(**kw):
        nodes = real_dump(**kw)
        print(f"      dump -> {len(nodes)} узлов")
        if nodes:
            last.clear()
            last.extend(nodes)
        return nodes

    session.ui.dump = dump

    state = {"same_frames": 0, "screen": (w, h), "package": cfg["package"],
             "id": "после-лайка", "stream": None, "blind": True,
             "shown_at": time.time()}

    for i in range(1, tries + 1):
        print(f"\n--- ролик {i} ---")
        time.sleep(2.0)

        # 1. Как в сессии: сначала настоящий лайк двойным тапом.
        x, y = human.like_point(w, h)
        human.double_tap(x, y)
        print(f"   лайк двойным тапом в ({x}, {y})")
        time.sleep(1.0)

        # 2. И сразу за ним — попытка забрать ссылку.
        entries = []
        got = session._save_link(state, cfg, w, h, entries, app)
        for line in entries:
            print("   >", line.strip())
        if got:
            print("   ЗАБРАЛИ ССЫЛКУ")
            break

        print("   дерево на месте неудачи:")
        shown = 0
        for n in last:
            label = n.text or n.desc
            if label and shown < 40:
                shown += 1
                print(f"     {label[:52]!r:55} click={n.clickable} {n.bounds}")
        if not last:
            print("     (пусто)")

        human.scroll_feed(w, h, "up", quick=True)
        time.sleep(1.5)

    ui.dump = real_dump
    device.keep_awake(False)
    return 0


if __name__ == "__main__":
    sys.exit(main(tries=int(sys.argv[1]) if len(sys.argv) > 1 else 2))
