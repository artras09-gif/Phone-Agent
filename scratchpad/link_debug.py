"""Почему в сессии лист «поделиться» не открывается, а в разведке открывался.

Прогоняет `_save_link` на живой ленте, но с говорящими обёртками: видно
каждый снимок дерева, каждый тап и каждую проверку «играет ли ролик».
На неудаче кладёт рядом кадр экрана — глазами видно, что там на самом деле.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import adb  # noqa: E402
import config  # noqa: E402
import device  # noqa: E402
import human  # noqa: E402
import runlog  # noqa: E402
import session  # noqa: E402
import ui  # noqa: E402

step = [0]
last = []


def main(app="tiktok", tries=8, threshold=None):
    # Порог проверен отдельно (link_save.py); здесь важна сама цепочка
    # «поделиться → ссылка → буфер», поэтому его можно опустить.
    if threshold is not None:
        config.LINK_MIN_LIKES = int(threshold)
        print(f"порог на время отладки: {config.LINK_MIN_LIKES}")

    cfg = session._feed_config(app)
    w, h = adb.screen_size()
    device.ensure_awake()
    device.keep_awake(True)

    log = runlog.Log(live=True)
    session._open_feed(cfg, log)

    real_dump, real_tap_node = ui.dump, ui.tap_node
    real_playing, real_tapv = session._video_playing, session._tap_video

    def dump(**kw):
        nodes = real_dump(**kw)
        step[0] += 1
        print(f"      [{step[0]:2}] dump -> {len(nodes)} узлов")
        if nodes:
            last.clear()
            last.extend(nodes)
        return nodes

    def tap_node(node):
        print(f"      тап по узлу: {(node.text or node.desc)[:50]!r} "
              f"{node.bounds}")
        return real_tap_node(node)

    def playing(state, wait=0.9):
        got = real_playing(state, wait)
        print(f"      играет? {got}")
        return got

    def tap_video(state, ww, hh):
        print("      тап по видео (пауза/пуск)")
        return real_tapv(state, ww, hh)

    session.ui.dump = dump
    session.ui.tap_node = tap_node
    session._video_playing = playing
    session._tap_video = tap_video

    state = {"same_frames": 0, "screen": (w, h), "package": cfg["package"],
             "id": "отладка", "stream": None, "blind": True,
             "shown_at": time.time()}

    for i in range(1, tries + 1):
        print(f"\n--- ролик {i} ---")
        entries = []
        got = session._save_link(state, cfg, w, h, entries, app)
        for line in entries:
            print("   >", line.strip())
        if got:
            print("ЗАБРАЛИ")
            break
        if any("не открылся" in s for s in entries):
            print("   что было в последнем дереве:")
            for n in last:
                label = n.text or n.desc
                if label:
                    print(f"     {label[:45]!r:48} cls={n.cls.split('.')[-1]:12}"
                          f" click={n.clickable} {n.bounds}")
            png = adb.exec_out("screencap -p", timeout=20)
            path = os.path.join(HERE, f"debug_{i}.png")
            with open(path, "wb") as f:
                f.write(png)
            print(f"   кадр на месте неудачи: {path}")
            break
        human.scroll_feed(w, h, "up", quick=True)
        time.sleep(1.5)

    ui.dump, ui.tap_node = real_dump, real_tap_node
    session._video_playing, session._tap_video = real_playing, real_tapv
    device.keep_awake(False)
    return 0


if __name__ == "__main__":
    sys.exit(main(tries=3, threshold=0))
