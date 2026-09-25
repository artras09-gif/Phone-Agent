"""Сбор ссылок во всех трёх сетях — на НАСТОЯЩИХ деревьях, снятых 2026-08-30.

Проверяет то, что раньше было догадкой: читается ли число лайков, находится
ли кнопка «поделиться», берётся ли пункт «копировать ссылку» — включая
YouTube, где у этого пункта в дереве нет подписи вовсе.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import session  # noqa: E402
import ui  # noqa: E402

W = 1080

RECIPES = json.load(open(os.path.join(os.path.dirname(HERE), "recipes.json"),
                         encoding="utf-8"))["_feed_apps"]


def node(label, click, box, cls="android.view.ViewGroup"):
    x1, y1, x2, y2 = box
    return ui.Node({"content-desc": label, "text": label, "class": cls,
                    "clickable": "true" if click else "false",
                    "enabled": "true", "bounds": f"[{x1},{y1}][{x2},{y2}]"})


# --- лента Shorts (панель видна) ---
SHORTS_FEED = [
    node('поставить отметку "Нравится" (это видео понравилось 135 тысяч '
         'пользователям)', True, (904, 1301, 1080, 1466)),
    node('Посмотреть 1\xa0403 комментария', True, (904, 1466, 1080, 1631)),
    node('Поделиться видео', True, (904, 1631, 1080, 1796)),
    node('Перейти на канал "@vodolazlebedev"', False, (44, 1834, 132, 1922)),
    node('Поиск', True, (849, 126, 915, 192)),
    node('Shorts', True, (216, 2138, 432, 2177)),
]

# --- лист «поделиться» YouTube: подписаны ТОЛЬКО значки приложений ---
SHORTS_SHEET = [
    node('', True, (0, 0, 1080, 2177)),                    # touch_outside
    node('Telegram', True, (33, 1585, 209, 1783)),
    node('X', True, (253, 1585, 429, 1783)),
    node('ВКонтакте', True, (473, 1585, 649, 1783)),
    node('WhatsApp', True, (693, 1585, 869, 1783)),
    node('Gmail', True, (913, 1585, 1058, 1783)),
    node('', True, (55, 1918, 1058, 2050)),                # «Коп. ссылку»
    node('', True, (55, 2094, 1058, 2177)),                # «Быстрая отправка»
]

# --- лента Reels на паузе ---
REELS_FEED = [
    node('Нравится', True, (927, 915, 1048, 1036)),
    node('Нравится: 24718. Посмотреть', True, (917, 1036, 1058, 1100)),
    node('Комментарии: 1137. Посмотреть', True, (927, 1221, 1048, 1285)),
    node('Репост', True, (927, 1285, 1048, 1406)),
    node('334 репостов', True, (927, 1406, 1048, 1470)),
    node('Поделиться', True, (927, 1470, 1048, 1591)),
    node('Число репостов: 19742', True, (920, 1591, 1055, 1655)),
    node('Число сохранений: 2336', True, (927, 1776, 1048, 1840)),
    node('Фото профиля goldengeorgiii', True, (55, 1895, 154, 1994)),
    node('Поиск и интересное', True, (648, 2138, 864, 2177)),
    node('Reels', True, (216, 2138, 432, 2177)),
]

REELS_SHEET = [
    node('Поделиться', True, (52, 1980, 195, 2123)),
    node('Копировать ссылку', True, (256, 1980, 399, 2123)),
    node('Скачать', True, (460, 1980, 603, 2123)),
    node('Добавить в историю', True, (664, 1980, 807, 2123)),
    node('Эльмар Чат не выбран', True, (0, 1049, 360, 1412)),
]

ok = True


def check(name, cond, got=""):
    global ok
    ok = ok and bool(cond)
    print(("  OK  " if cond else " ПРОВАЛ ") + name + (f"  [{got}]" if got else ""))


def main():
    print("== Shorts ==")
    cfg = RECIPES["shorts"]
    counter = session._pick(SHORTS_FEED, cfg["like_count_selectors"])
    likes = session._parse_count(counter.desc) if counter else None
    check("число лайков 135000", likes == 135000, str(likes))

    # YouTube склоняет подпись по числу, и после НАШЕГО лайка она меняется
    # целиком: было «поставить отметку…», стало «457 отметок "Нравится"».
    # Каждая форма — отдельный промах, если селектор держится за окончание.
    for text, want in (('24 793 отметки "Нравится"', 24793),
                       ('457 отметок "Нравится"', 457),
                       ('1 отметка "Нравится"', 1)):
        got = session._pick([node(text, True, (904, 1301, 1080, 1466))],
                            cfg["like_count_selectors"])
        value = session._parse_count(got.desc) if got else None
        check(f"после лайка: {text[:24]}", value == want, str(value))
    share = session._pick(SHORTS_FEED, cfg["share_selectors"])
    check("кнопка «поделиться»", share is not None and share.bounds[1] == 1631)
    author = session._pick(SHORTS_FEED, cfg["author_selectors"])
    check("автор найден", author is not None)
    check("поиск найден", session._pick(SHORTS_FEED, cfg["discover_tabs"]) is not None)
    check("вкладка ленты найдена", session._pick(SHORTS_FEED, cfg["home_tabs"]) is not None)

    link = session._pick(SHORTS_SHEET, cfg["link_selectors"])
    check("селектор по подписи НЕ находит (подписи нет)", link is None)
    check("запасной путь «по месту» разрешён только YouTube",
          RECIPES["shorts"].get("link_row_fallback") is True
          and not RECIPES["tiktok"].get("link_row_fallback")
          and not RECIPES["reels"].get("link_row_fallback"))
    row = session._link_row(SHORTS_SHEET, W)
    check("безымянная строка ссылки взята по месту",
          row is not None and row.bounds == (55, 1918, 1058, 2050),
          str(row.bounds) if row else "нет")
    check("«Быстрая отправка» НЕ выбрана",
          row is not None and row.bounds[1] != 2094)
    check("растянутый touch_outside НЕ выбран",
          row is not None and row.bounds[1] != 0)

    print("\n== Reels ==")
    cfg = RECIPES["reels"]
    counter = session._pick(REELS_FEED, cfg["like_count_selectors"])
    likes = session._parse_count(counter.desc) if counter else None
    check("число лайков 24718", likes == 24718, str(likes))
    check("не спутал с репостами 19742", likes != 19742)
    check("не спутал с сохранениями 2336", likes != 2336)
    share = session._pick(REELS_FEED, cfg["share_selectors"])
    check("кнопка «поделиться» — самолётик, а не «Репост»",
          share is not None and share.bounds[1] == 1470,
          str(share.bounds) if share else "нет")
    check("автор найден", session._pick(REELS_FEED, cfg["author_selectors"]) is not None)
    link = session._pick(REELS_SHEET, cfg["link_selectors"])
    check("«Копировать ссылку» найдено подписью",
          link is not None and "опировать" in (link.desc or ""))

    print("\n== TikTok (не сломали) ==")
    cfg = RECIPES["tiktok"]
    tt = [node('Поставить лайк. Число лайков: 589,4\xa0тыс.', True,
               (898, 1170, 1080, 1332)),
          node('Поделиться видео. Уже поделились: 368,9\xa0тыс.', True,
               (898, 1684, 1080, 1871))]
    counter = session._pick(tt, cfg["like_count_selectors"])
    check("счётчик TikTok читается",
          counter is not None and session._parse_count(counter.desc) == 589400)
    check("TikTok помечен как «тап по играющему»",
          cfg.get("share_needs_playing") is True)

    print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПРОВАЛЫ")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
