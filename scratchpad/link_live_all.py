"""Сквозной живой прогон сбора ссылок по всем трём сетям.

Порядок как в бою: настоящий лайк двойным тапом, сразу за ним `_save_link`.
Порог снижается НА ВРЕМЯ ПРОГОНА, иначе цепочку до конца не увидеть — за
пять роликов подряд ролика на 50 тысяч может не попасться.

База подменяется на временную: боевая подборка должна остаться чистой.
"""
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import adb        # noqa: E402
import config     # noqa: E402
import device     # noqa: E402
import human      # noqa: E402
import prefs      # noqa: E402
import runlog     # noqa: E402
import session    # noqa: E402

prefs.apply()


def main(apps, tries=3, threshold=100):
    live_db = config.DB_PATH
    tmp = tempfile.mkdtemp(prefix="link_all_")
    config.DB_PATH = os.path.join(tmp, "jobs.db")
    assert config.DB_PATH != live_db
    config.LINK_MIN_LIKES = threshold
    print(f"порог на время прогона: {threshold}, база: временная\n")

    import jobs

    w, h = adb.screen_size()
    device.ensure_awake()
    device.keep_awake(True)
    log = runlog.Log(live=True)
    result = {}

    for app in apps:
        print("\n" + "=" * 62)
        print("==", app)
        cfg = session._feed_config(app)
        device.stop_app(cfg["package"])
        time.sleep(1.5)
        session._open_feed(cfg, log)
        session._ensure_feed(cfg, log)

        state = {"same_frames": 0, "screen": (w, h), "package": cfg["package"],
                 "id": f"проверка-{app}", "stream": None, "blind": True,
                 "shown_at": time.time(), "popups": 0, "rescues": 0,
                 "cfg": cfg}

        got = False
        for i in range(1, tries + 1):
            print(f"\n  --- ролик {i} ---")
            time.sleep(2.5)
            x, y = human.like_point(w, h)
            human.double_tap(x, y)
            print("     лайк двойным тапом")
            time.sleep(1.2)

            entries = []
            got = session._save_link(state, cfg, w, h, entries, app)
            for line in entries:
                print("   >", line.strip())
            if got:
                break
            human.scroll_feed(w, h, "up", quick=True)
            time.sleep(1.5)

        result[app] = got

    print("\n" + "=" * 62)
    for app, got in result.items():
        print(f"  {app:8} {'ССЫЛКА ЗАБРАНА' if got else 'не получилось'}")
    print("\nчто легло во временную базу:")
    for r in jobs.links(limit=50):
        print(f"   ♥ {r['likes']:>8} | {r['app']:7} | {(r['author'] or '?')[:20]:20} "
              f"| {r['url']}")

    device.keep_awake(False)
    print("\nбоевая база не тронута:", live_db)
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.isdigit()]
    nums = [int(a) for a in sys.argv[1:] if a.isdigit()]
    sys.exit(main(args or ["shorts", "reels"], *(nums or [3])))
