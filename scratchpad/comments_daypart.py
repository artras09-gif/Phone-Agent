"""Пункты 4 и 5: заход в комментарии и время суток.

Комментарии — единственное место, где агент тапает не по лайку, и цена
ошибки высока: тапы в незнакомый интерфейс уже уводили сессию на чужой
профиль. Поэтому проверяем не «вызвалось без ошибки», а КАЖДУЮ развилку
отказа: не попали в паузу, не нашли кнопку, открылось не то. Телефон
подменяем целиком.
"""
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402
import human  # noqa: E402
import session  # noqa: E402

ok = True


def say(good, text):
    global ok
    ok &= good
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


class Node:
    def __init__(self, desc="", text="", area=10000, clickable=True):
        self.desc, self.text = desc, text
        self.area, self.clickable = area, clickable
        self.bounds = (100, 100, 200, 200)


CFG = {
    "comment_selectors": [{"desc": "Комментарии", "clickable": True}],
    "comment_markers": [{"desc": "Добавить комментарий"}],
    "feed_markers": [],
}

FEED = [Node(desc="Комментарии")]
PANEL = [Node(desc="Добавить комментарий"), Node(text="Комментарии")]


class Phone:
    """Подставной телефон: помнит, что с ним делали."""

    def __init__(self, pause_works=True, button=True, opens=True):
        self.pause_works, self.button, self.opens = pause_works, button, opens
        self.playing = True
        self.screen = "feed"
        self.taps, self.backs, self.swipes = 0, 0, 0
        self.log = []

    # --- то, что зовёт сессия ---
    def video_playing(self, state, wait=0.9):
        return self.playing

    def tap_video(self, state, w, h):
        self.taps += 1
        if self.pause_works:
            self.playing = not self.playing
        self.log.append("тап по видео")

    def dump(self, **kw):
        if self.screen == "feed" and self.playing:
            return []               # играет — дерево не снимается
        if self.screen == "comments":
            return PANEL
        return FEED if self.button else []

    def tap_node(self, node):
        self.log.append("тап по кнопке")
        self.screen = "comments" if self.opens else "profile"

    def back(self):
        self.backs += 1
        self.screen = "feed"
        self.log.append("назад")

    def scroll_comments(self, w, h):
        self.swipes += 1
        return 0.2, 200

    def frame_size(self, state):
        # Панель комментариев — крупная светлая плашка: кадр резко легчает.
        # Числа взяты с живого замера (2248 -> 903 КБ).
        return 903_000 if self.screen == "comments" else 2_248_000


def run(phone):
    """Прогнать _read_comments на подставном телефоне."""
    session._video_playing = phone.video_playing
    session._tap_video = phone.tap_video
    session.ui.dump = phone.dump
    session.ui.tap_node = phone.tap_node
    session.device.back = phone.back
    session.human.scroll_comments = phone.scroll_comments
    session._frame_size = phone.frame_size
    session.human.pause = lambda a, b: 0
    session._ensure_feed = lambda cfg, log, timeout=4: True
    state = {"started_at": 0, "same_frames": 0}
    log = []
    got = session._read_comments(state, CFG, 1080, 2400, log)
    return got, phone, log


real_dump, real_tap = session.ui.dump, session.ui.tap_node
real_back, real_scroll = session.device.back, session.human.scroll_comments
real_pause, real_ensure = session.human.pause, session._ensure_feed
real_playing, real_tapv = session._video_playing, session._tap_video
real_fsize = session._frame_size

print("--- 4. заход в комментарии ---")

got, ph, log = run(Phone())
say(got is True, "обычный ход: сходили и вернулись")
say(ph.screen == "feed", "вернулись в ленту")
say(ph.backs == 1, "закрыли ровно одним «назад»")
say(ph.swipes >= 1, f"почитали ({ph.swipes} протяжек)")
say(ph.playing, "ролик снова играет, не остался на паузе")

got, ph, log = run(Phone(pause_works=False))
say(got is False, "пауза не сработала → не полезли дальше")
say(ph.screen == "feed", "остались в ленте")
say(ph.backs == 0, "и никуда не нажимали")
say(any("пауза не сработала" in s for s in log), "причина названа вслух")

got, ph, log = run(Phone(button=False))
say(got is False, "кнопки нет → отказ")
say(ph.playing, "ролик возвращён в проигрывание")
say(any("не нашёл" in s for s in log), "и об этом сказано")

got, ph, log = run(Phone(opens=True))
ph2 = Phone(opens=False)
got, ph2, log = run(ph2)
say(got is False, "открылось НЕ ТО (чужой профиль) → отказ по весу кадра")
say(ph2.backs == 1, "откатились назад")
say(ph2.screen == "feed", "и оказались в ленте")
say(ph2.playing, "ролик играет")
say(any("откатываюсь" in s for s in log), "откат назван вслух")

# кадр не сменился — под пальцем может быть окно, а не видео
session._video_playing = lambda *a, **k: True
state = {"started_at": 0, "same_frames": 3}
say(session._read_comments(state, CFG, 1080, 2400, []) is False,
    "кадр не сменился → не тапаем вовсе (та же оговорка, что у лайка)")

# нет селекторов в рецепте — молча ничего не делаем
say(session._read_comments({"started_at": 0}, {}, 1080, 2400, []) is False,
    "нет селекторов в рецепте → не пытаемся")

session.ui.dump, session.ui.tap_node = real_dump, real_tap
session.device.back, session.human.scroll_comments = real_back, real_scroll
session.human.pause, session._ensure_feed = real_pause, real_ensure
session._video_playing, session._tap_video = real_playing, real_tapv
session._frame_size = real_fsize

print("\n--- выдержка между заходами ---")
st = {"started_at": time.time()}
say(session._may_read_comments(st) is False, "сразу после начала — нельзя")
st["started_at"] = time.time() - config.COMMENTS_EVERY_SEC - 1
say(session._may_read_comments(st) is True,
    f"через {config.COMMENTS_EVERY_SEC} с — можно")

print("\n--- 5. время суток ---")
by_hour = {}
for h in range(24):
    t = time.struct_time((2026, 8, 18, h, 0, 0, 0, 230, 0))
    by_hour[h] = human.daypart(t)

print("    " + "  ".join(f"{h:02d}:{by_hour[h]:.2f}" for h in range(0, 24, 3)))
say(max(by_hour.values()) / min(by_hour.values()) > 1.4,
    f"вечер и утро различаются: {min(by_hour.values()):.2f}..{max(by_hour.values()):.2f}")
peak = max(by_hour, key=by_hour.get)
low = min(by_hour, key=by_hour.get)
say(19 <= peak <= 23 or peak == 0, f"пик вечером: {peak}:00")
say(8 <= low <= 12, f"спад к позднему утру: {low}:00")

steps = [abs(by_hour[(h + 1) % 24] - by_hour[h]) for h in range(24)]
say(max(steps) < 0.06,
    f"переход плавный, без ступеньки на границе часа: макс шаг {max(steps):.3f}")

say(abs(statistics.fmean(by_hour.values()) - 1.0) < 0.01,
    f"в среднем по суткам единица: {statistics.fmean(by_hour.values()):.3f}")

print("\n--- залипание чаще вечером ---")
night = [human.deep_watch_gap(scale=by_hour[23]) for _ in range(4000)]
morning = [human.deep_watch_gap(scale=by_hour[9]) for _ in range(4000)]
say(statistics.fmean(night) < statistics.fmean(morning),
    f"вечером через {statistics.fmean(night):.1f} роликов, "
    f"утром через {statistics.fmean(morning):.1f}")
say(min(night) >= 2, "но не чаще чем раз в два ролика")

print("\n--- комментарии заведены во ВСЕХ трёх лентах (2026-08-30) ---")
import json  # noqa: E402
import os as _os  # noqa: E402

_recipes = json.load(open(
    _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                  "recipes.json"), encoding="utf-8"))["_feed_apps"]
for _app in ("tiktok", "shorts", "reels"):
    _cfg = _recipes[_app]
    say(bool(_cfg.get("comment_selectors")),
        f"{_app}: кнопка комментариев задана селектором")
    say("comments_need_playing" in _cfg,
        f"{_app}: сказано, тапать по играющему или с паузы")

# Различие не косметическое: у TikTok первый тап с паузы уходит на
# возобновление и панель не открывается, а у Shorts и Reels наоборот —
# отпустив паузу, теряем дерево. Проверено живьём на всех трёх.
say(_recipes["tiktok"]["comments_need_playing"] is True,
    "TikTok — по играющему ролику")
say(_recipes["shorts"]["comments_need_playing"] is False
    and _recipes["reels"]["comments_need_playing"] is False,
    "Shorts и Reels — не снимая паузы")

print("\nИТОГ:", "оба пункта работают" if ok else "ЕСТЬ ПРОВАЛЫ")
sys.exit(0 if ok else 1)
