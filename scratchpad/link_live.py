"""Живая проверка сохранения ссылки — целиком, на настоящем телефоне.

Ветки отказа проверены на подставном телефоне (`link_save.py`); здесь
проверяется то, чего подставным не проверишь: настоящие подписи кнопок,
настоящий лист «поделиться», настоящий буфер обмена и возвращение в ленту.

Листает ленту и на каждом ролике зовёт `_save_link` с НАСТОЯЩИМ порогом.
Ролики с малым числом лайков он отсеет сам — как и в сессии, — а на первом
подходящем заберёт адрес и положит в базу.

ГРАБЛИ (ловил 2026-08-18): свои скрипты не будят телефон, а сессия будит.
Погасший экран даёт чёрный кадр, и всё читается как «не играет».
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
import jobs  # noqa: E402
import runlog  # noqa: E402
import session  # noqa: E402
import ui  # noqa: E402


def main(app="tiktok", tries=10):
    cfg = session._feed_config(app)
    w, h = adb.screen_size()

    device.ensure_awake()
    device.keep_awake(True)
    log = runlog.Log(live=True)
    if not session._open_feed(cfg, log):
        print("не удалось открыть ленту")
        return 1

    # Признак ленты у TikTok: дерево НЕ снимается, потому что играет видео.
    for _ in range(5):
        if not ui.dump(retries=1, tolerant=True, timeout=6):
            break
        tab = session._pick(ui.dump(retries=1, tolerant=True, timeout=6) or [],
                            cfg.get("home_tabs") or [])
        if tab is not None:
            ui.tap_node(tab)
        else:
            device.back()
        time.sleep(1.5)
    else:
        print("в ленту попасть не удалось")
        return 1

    before = len(jobs.links(limit=500))
    state = {"same_frames": 0, "screen": (w, h), "package": cfg["package"],
             "id": "живая проверка", "stream": None, "blind": True,
             "shown_at": time.time()}

    print(f"порог: {config.LINK_MIN_LIKES} лайков\n")
    for i in range(1, tries + 1):
        entries = []
        got = session._save_link(state, cfg, w, h, entries, app)
        for line in entries:
            print(f"  {i:2}. {line.strip()}")
        if got:
            print("\nзабрали. Что легло в базу:")
            for row in jobs.links(limit=3):
                print(f"  {row['likes']:>9} лайков  {row['author'] or '—'}  "
                      f"{row['url']}")
                if row["caption"]:
                    print(f"             {row['caption'][:70]}")
            device.keep_awake(False)
            return 0
        # Дальше по ленте — как это делает сессия.
        human.scroll_feed(w, h, "up", quick=True)
        state["shown_at"] = time.time()
        time.sleep(1.5)

    print(f"\nза {tries} роликов подходящего не попалось "
          f"(записей в базе было {before}, стало {len(jobs.links(limit=500))})")
    device.keep_awake(False)
    return 1


if __name__ == "__main__":
    sys.exit(main(*(sys.argv[1:] or ["tiktok"])))
