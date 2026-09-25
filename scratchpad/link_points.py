"""Ссылки в лентах БЕЗ кнопок в дереве (Shorts, Reels) — без телефона.

TikTok отдаёт в дереве всё: и счётчик лайков, и «поделиться», и вкладки.
У Shorts и Reels правой колонки в дереве нет вовсе, поэтому путь другой:
число лайков читает зрение, а по кнопке агент бьёт координатой из
`recipes.json`. Слепой тап — вещь опасная, и проверять надо не «дошло до
конца», а что КАЖДЫЙ промах замечен и откачен:

* зрение не прочитало лайков — лист даже не открываем;
* лайков меньше порога — разворот до листа;
* точка «поделиться» промахнулась (лист не открылся) — «назад» и в ленту;
* до строки поиска не добрались — возвращаемся, ничего не записав;
* обычный ход — адрес в базе, поле поиска вычищено, лента играет.

Отдельно проверяется, что в этих лентах агент НЕ ставит ролик на паузу:
их дерево снимается на ходу, а лишний тап по видео открывает что попало.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402
import session  # noqa: E402

ok = True
URL = "https://www.youtube.com/shorts/abcDEF12345"


def say(good, text):
    global ok
    ok &= good
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


class Node:
    def __init__(self, desc="", text="", rid="", cls="android.view.View",
                 clickable=True):
        self.desc, self.text, self.rid, self.cls = desc, text, rid, cls
        self.clickable = clickable
        self.pkg = "com.google.android.youtube"
        self.bounds = (100, 100, 200, 200)

    @property
    def area(self):
        return 10_000

    @property
    def center(self):
        return 150, 150

    def __repr__(self):
        return f"<Node {self.text or self.desc!r}>"


# Ровно то, что лежит в recipes.json у shorts.
CFG = {
    "likes_by_vision": True,
    "share_point": [0.93, 0.805],
    "search_points": [[0.1, 0.965], [0.87, 0.05]],
    "home_point": [0.3, 0.965],
    "search_boxes": [{"contains": "Поиск"}],
    "link_selectors": [{"contains": "Копировать ссылку", "clickable": True}],
    "feed_markers": [{"desc": "Приостановить видео"}],
}

W, H = 1080, 2400

# Слой доступности Shorts: две кнопки плеера и больше ничего.
FEED = [Node(desc="Приостановить видео"), Node(desc="Следующее видео")]
SHEET = [Node(text="Копировать ссылку"), Node(text="Отправить в WhatsApp")]
HOME = [Node(desc="Поиск"), Node(desc="Главная")]


class Phone:
    def __init__(self, sheet_opens=True, search_works=True, likes="128,4 тыс.",
                 paste_works=True):
        self.sheet_opens, self.search_works = sheet_opens, search_works
        self.likes, self.paste_works = likes, paste_works

        self.screen = "feed"
        self.playing = True
        self.clipboard = self.field = ""
        self.cleared = False
        self.video_taps = 0
        self.points = []
        self.backs = 0

    # --- то, что зовёт сессия ---------------------------------------
    def video_playing(self, state, wait=0.9):
        return self.playing if self.screen == "feed" else False

    def tap_video(self, state, w, h):
        self.video_taps += 1
        self.playing = not self.playing

    def dump(self, **kw):
        return {"feed": FEED, "sheet": SHEET if self.sheet_opens else [],
                "home": HOME if self.search_works else [Node(desc="Главная")],
                "search": [Node(cls="android.widget.EditText", text=self.field)],
                }.get(self.screen, [])

    def near(self, x, y, point):
        return (abs(x - point[0] * W) < 30 and abs(y - point[1] * H) < 30)

    def tap(self, x, y):
        self.points.append((x, y))
        if self.screen == "feed" and self.near(x, y, CFG["share_point"]):
            self.screen = "sheet" if self.sheet_opens else "feed"
        elif self.screen == "feed" and self.near(x, y, CFG["search_points"][0]):
            self.screen = "home"
        elif self.screen == "home" and self.near(x, y, CFG["search_points"][1]):
            self.screen = "search" if self.search_works else "home"
            self.field = ""
        elif self.near(x, y, CFG["home_point"]):
            self.screen = "feed"
            self.playing = True

    def tap_node(self, node):
        label = node.text or node.desc
        if "Копировать ссылку" in label:
            self.clipboard = URL
            self.screen = "feed"
        elif "Поиск" in label:
            self.screen = "search"

    def keyevent(self, name):
        if name == "KEYCODE_PASTE" and self.screen == "search" and self.paste_works:
            self.field = self.clipboard

    def shell(self, cmd, **kw):
        if "KEYCODE_DEL" in cmd:
            self.cleared = True
            self.field = ""
        return ""

    def back(self):
        self.backs += 1
        self.screen = {"search": "home", "home": "feed",
                       "sheet": "feed"}.get(self.screen, "feed")
        if self.screen == "feed":
            self.playing = True

    def screencap(self, cmd, timeout=20):
        return b"PNG" * 100


saved = []


def run(phone, cfg=CFG, app="shorts"):
    session._video_playing = phone.video_playing
    session._tap_video = phone.tap_video
    session.ui.dump = phone.dump
    session.ui.tap_node = phone.tap_node
    session.adb.tap = phone.tap
    session.adb.keyevent = phone.keyevent
    session.adb.shell = phone.shell
    session.adb.exec_out = phone.screencap
    session.device.back = phone.back
    session.human.pause = lambda a, b: 0
    session._ensure_feed = lambda cfg, log, timeout=4: True

    # Зрение: отвечает так же, как настоящая модель — строкой с экрана.
    session.vision.available = lambda: (True, "подставная модель")
    session.vision.looks_blank = lambda png, limit_kb=None: False
    session.vision.looks_degenerate = lambda answer: False
    session.vision.ask = lambda png, prompt, **kw: phone.likes

    saved.clear()
    session.jobs.add_link = lambda url, app, likes, **kw: (
        saved.append((url, app, likes, kw)) or True)

    state = {"same_frames": 0, "screen": (W, H), "id": "проверка",
             "package": "com.google.android.youtube"}
    log = []
    got = session._save_link(state, cfg, W, H, log, app)
    return got, phone, log


print("--- обычный ход ---")
got, ph, log = run(Phone())
say(got is True, "ссылка забрана")
say(bool(saved) and saved[0][0] == URL,
    f"в базу ушёл адрес: {saved[0][0] if saved else '—'}")
say(bool(saved) and saved[0][2] == 128400,
    f"лайки от зрения разобраны: {saved[0][2] if saved else '—'}")
say(ph.video_taps == 0, f"по видео не тапали ни разу (было {ph.video_taps})")
say(ph.screen == "feed" and ph.playing, "вернулись в ленту, ролик играет")
say(ph.cleared, "поле поиска вычищено за собой")

print("\n--- зрение не прочитало лайков ---")
got, ph, log = run(Phone(likes="нет"))
say(got is False, "ссылку не забрали")
say(ph.screen == "feed", "остались в ленте")
say(not ph.points, "ни одного слепого тапа не сделали")
say(any("не прочиталось" in s for s in log), f"сказано почему: {log}")

print("\n--- лайков меньше порога ---")
got, ph, log = run(Phone(likes="3,2 тыс."))
say(got is False, "ссылку не забрали")
say(not ph.points, "до листа «поделиться» дело не дошло")
say(any("не беру" in s for s in log), f"сказано почему: {log}")

print("\n--- точка «поделиться» промахнулась ---")
got, ph, log = run(Phone(sheet_opens=False))
say(got is False, "ссылку не забрали")
say(ph.screen == "feed" and ph.playing, "вернулись в играющую ленту")
say(not saved, "в базу ничего не ушло")
say(any("не открылся" in s for s in log), f"сказано почему: {log}")

print("\n--- до строки поиска не добрались ---")
got, ph, log = run(Phone(search_works=False))
say(got is False, "ссылку не забрали")
say(not saved, "в базу ничего не ушло")
say(ph.screen == "feed", "всё равно вернулись в ленту")

print("\n--- вставилось пусто ---")
got, ph, log = run(Phone(paste_works=False))
say(got is False, "ссылку не забрали")
say(not saved, "в базу ничего не ушло")

print("\n--- лента без точек (TikTok) этим путём не идёт ---")
tiktok_cfg = {"share_selectors": [{"desc": "Поделиться видео"}]}
ph = Phone()
got, ph, log = run(ph, cfg=tiktok_cfg, app="tiktok")
say(ph.video_taps > 0, "там пауза как раньше ставится")

print("\n--- порог берётся из config ---")
config.LINK_MIN_LIKES = 200_000
got, ph, log = run(Phone(likes="128,4 тыс."))
say(got is False, "128 тысяч при пороге 200 тысяч — мимо")
config.LINK_MIN_LIKES = 50_000

print("\nВСЁ ХОРОШО" if ok else "\nЕСТЬ ОШИБКИ")
sys.exit(0 if ok else 1)
