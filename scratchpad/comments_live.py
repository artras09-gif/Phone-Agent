"""Заход в комментарии живьём — по всем трём лентам.

Гоняет НАСТОЯЩИЙ `session._read_comments`, а не его пересказ: важны те же
проверки, что и в бою (панель опознаётся по весу кадра, каждая неудача
откатывается). Печатает вес кадра до и после — по нему видно, годится ли
общий порог `COMMENTS_PANEL_RATIO` для этой ленты.

Выдержки обходятся: в бою заход бывает раз в четыре минуты, а здесь нужно
несколько подряд.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import adb        # noqa: E402
import config     # noqa: E402
import device     # noqa: E402
import human      # noqa: E402
import prefs      # noqa: E402
import runlog     # noqa: E402
import session    # noqa: E402

prefs.apply()


def main(apps, tries=2):
    w, h = adb.screen_size()
    device.ensure_awake()
    device.keep_awake(True)
    log = runlog.Log(live=True)
    result = {}

    real_size = session._frame_size
    sizes = []

    def spy(state):
        got = real_size(state)
        sizes.append(got)
        return got

    session._frame_size = spy

    for app in apps:
        print("\n" + "=" * 62)
        print("==", app)
        cfg = session._feed_config(app)
        device.stop_app(cfg["package"])
        time.sleep(1.5)
        session._open_feed(cfg, log)
        session._ensure_feed(cfg, log)
        time.sleep(3.0)

        state = {"same_frames": 0, "screen": (w, h), "package": cfg["package"],
                 "id": f"комменты-{app}", "stream": None, "blind": True,
                 "shown_at": time.time(), "popups": 0, "rescues": 0,
                 "cfg": cfg}

        got = False
        for i in range(1, tries + 1):
            print(f"\n  --- ролик {i} ---")
            sizes.clear()
            entries = []
            got = session._read_comments(state, cfg, w, h, entries)
            for line in entries:
                print("   >", line.strip())
            if len(sizes) >= 2 and sizes[0] and sizes[1]:
                before, after = sizes[0], sizes[1]
                print(f"     кадр: {before // 1024} КБ -> {after // 1024} КБ  "
                      f"отношение {after / before:.2f} "
                      f"(порог {config.COMMENTS_PANEL_RATIO})")
            if got:
                print("     ЗАШЁЛ И ВЕРНУЛСЯ")
                break
            human.scroll_feed(w, h, "up", quick=True)
            time.sleep(2.0)

        result[app] = got

    session._frame_size = real_size
    device.keep_awake(False)

    print("\n" + "=" * 62)
    for app, got in result.items():
        print(f"  {app:8} {'комментарии работают' if got else 'НЕ получилось'}")
    return 0 if all(result.values()) else 1


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.isdigit()]
    nums = [int(a) for a in sys.argv[1:] if a.isdigit()]
    sys.exit(main(args or ["shorts", "reels"], *(nums or [2])))
