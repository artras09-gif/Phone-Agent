"""Число лайков читается и у УЖЕ лайкнутого ролика.

Дерево здесь — настоящее, снятое с телефона 2026-08-30 сразу после нашего
двойного тапа (`link_after_like.py`). Ловушка, ради которой тест и написан:
рядом лежат такие же с виду счётчики комментариев («2715») и «Избранного»
(«50,2 тыс.»), и по подписи от лайков они не отличаются ничем.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import session  # noqa: E402
import ui  # noqa: E402

# (подпись, кликабельна, границы) — как пришло с телефона.
AFTER_LIKE = [
    ("Профиль meowtakeover", True, (937, 988, 1058, 1109)),
    ("Подписаться на meowtakeover", True, (915, 1076, 1080, 1170)),
    ("Вам понравилось это видео", True, (898, 1170, 1080, 1332)),
    ("Лайк", False, (935, 1170, 1059, 1294)),
    ("589,4\xa0тыс.", True, (906, 1294, 1071, 1332)),
    ("Прочитать или оставить комментарии. Число комментариев: 2715",
     True, (914, 1332, 1080, 1511)),
    ("2715", True, (960, 1462, 1033, 1511)),
    ("Добавьте это видео в Избранное или удалите его из Избранного",
     True, (914, 1511, 1080, 1684)),
    ("50,2\xa0тыс.", False, (922, 1635, 1070, 1684)),
    ("Поделиться видео. Уже поделились: 368,9\xa0тыс.",
     True, (898, 1684, 1080, 1871)),
    ("368,9\xa0тыс.", True, (906, 1814, 1071, 1841)),
]

BEFORE_LIKE = [
    ("Поставить лайк. Число лайков: 589,4\xa0тыс.", True, (898, 1170, 1080, 1332)),
    ("589,4\xa0тыс.", True, (906, 1294, 1071, 1332)),
]


def tree(rows):
    out = []
    for label, click, (x1, y1, x2, y2) in rows:
        out.append(ui.Node({
            "content-desc": label,
            "text": label,
            "clickable": "true" if click else "false",
            "enabled": "true",
            "bounds": f"[{x1},{y1}][{x2},{y2}]",
        }))
    return out


def main():
    cfg = json.load(open(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "recipes.json"), encoding="utf-8"))["_feed_apps"]["tiktok"]

    ok = True

    def check(name, cond):
        nonlocal ok
        ok = ok and cond
        print(("  OK  " if cond else " ПРОВАЛ ") + name)

    got = session._likes_when_liked(tree(AFTER_LIKE), cfg)
    check(f"после лайка читается 589400 (получено {got})", got == 589400)
    check("не спутал с комментариями (2715)", got != 2715)
    check("не спутал с «Избранным» (50200)", got != 50200)
    check("не спутал с «Поделились» (368900)", got != 368900)

    # До лайка работает прежний путь, и запасной не мешает.
    before = tree(BEFORE_LIKE)
    counter = session._pick(before, cfg["like_count_selectors"])
    check("до лайка находит прежний селектор",
          counter is not None and session._parse_count(counter.desc) == 589400)
    check("до лайка запасной путь молчит (кнопка ещё не переименована)",
          session._likes_when_liked(before, cfg) is None)

    # Лента без такого селектора не должна падать.
    check("лента без liked_selectors не падает",
          session._likes_when_liked(tree(AFTER_LIKE), {}) is None)

    print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПРОВАЛЫ")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
