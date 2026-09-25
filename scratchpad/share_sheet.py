"""Что лежит в листе «поделиться» у Shorts и Reels — живьём.

Нужно ради одного: как называется пункт «Копировать ссылку». Всё остальное в
цепочке уже проверено селекторами, а этот пункт до сих пор был догадкой.

Ничего не отправляет: лист открывается, содержимое печатается, лист
закрывается кнопкой «назад».
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import adb        # noqa: E402
import device     # noqa: E402
import runlog     # noqa: E402
import session    # noqa: E402
import ui         # noqa: E402
import prefs      # noqa: E402

prefs.apply()

SHARE = {
    "shorts": ("Поделиться видео",),
    "reels": ("Поделиться",),
}


def main(apps):
    w, h = adb.screen_size()
    device.ensure_awake()
    device.keep_awake(True)
    log = runlog.Log(live=True)

    for app in apps:
        print("\n" + "=" * 64)
        print("==", app)
        cfg = session._feed_config(app)
        device.stop_app(cfg["package"])
        time.sleep(1.5)
        session._open_feed(cfg, log)
        session._ensure_feed(cfg, log)
        time.sleep(3.5)

        # Пауза: у Reels без неё дерево не снимается вовсе.
        adb.tap(w // 2, int(h * 0.45))
        time.sleep(1.8)
        tree = ui.dump(retries=2, tolerant=True, timeout=9) or []
        print(f"  дерево ленты: {len(tree)} узлов")

        share = None
        for needle in SHARE[app]:
            for n in tree:
                label = (n.desc or n.text or "")
                if needle.lower() in label.lower() and n.clickable:
                    share = n
                    break
            if share is not None:
                break
        if share is None:
            print("  кнопки «поделиться» не нашёл — пропускаю")
            adb.tap(w // 2, int(h * 0.45))
            continue
        print(f"  кнопка: {(share.desc or share.text)!r} {share.bounds}")

        # Снять паузу перед тапом: у TikTok первый тап с паузы уходит на
        # возобновление, у остальных лишний тап тоже ничего не стоит.
        adb.tap(w // 2, int(h * 0.45))
        time.sleep(1.0)
        ui.tap_node(share)
        time.sleep(2.5)

        sheet = ui.dump(retries=2, tolerant=True, timeout=10) or []
        print(f"  лист: {len(sheet)} узлов")
        for n in sheet:
            label = (n.desc or n.text or "").strip()
            if label:
                print(f"     {label[:46]!r:49} click={str(n.clickable):5} {n.bounds}")

        device.back()
        time.sleep(1.5)

    device.keep_awake(False)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["shorts", "reels"]))
