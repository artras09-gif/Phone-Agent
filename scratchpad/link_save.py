"""Сохранение ссылки на понравившееся: проверка веток без телефона.

Заход дорогой и опасный — семь тапов, лист «поделиться», уход в
«Интересное» и обратно. Поэтому, как и с комментариями, проверяем не
«вызвалось без ошибки», а КАЖДУЮ развилку отказа: не встали на паузу, не
прочиталось число лайков, лайков мало, лист не открылся, в поле поиска
не оказалось адреса. Телефон подменён целиком.

Отдельно проверяется дешёвый выход по порогу: если лайков мало, агент
обязан развернуться СРАЗУ — до листа «поделиться», иначе порог не
экономит ничего.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import session  # noqa: E402
import ui  # noqa: E402

ok = True
URL = "https://www.tiktok.com/t/ZTDfdsRYo/"


def say(good, text):
    global ok
    ok &= good
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


class Node:
    """Узел дерева с теми же полями, что у настоящего ui.Node."""

    def __init__(self, desc="", text="", rid="", cls="android.view.View",
                 clickable=True):
        self.desc, self.text, self.rid, self.cls = desc, text, rid, cls
        self.clickable = clickable
        self.pkg = "com.zhiliaoapp.musically"
        self.bounds = (100, 100, 200, 200)

    @property
    def area(self):
        return 10_000

    @property
    def center(self):
        return 150, 150

    def __repr__(self):
        return f"<Node {self.text or self.desc!r}>"


CFG = {
    "like_count_selectors": [{"desc": "Число лайков", "clickable": True}],
    "share_selectors": [{"desc": "Поделиться видео", "clickable": True}],
    "link_selectors": [{"text": "Ссылка", "clickable": True}],
    "discover_tabs": [{"contains": "Интересное", "clickable": True}],
    "search_boxes": [{"contains": "Поиск"}],
    "home_tabs": [{"contains": "Главная", "clickable": True}],
    "author_selectors": [{"desc": "Профиль ", "clickable": True}],
}

SHEET = [Node(text="Репост"), Node(text="Ссылка"), Node(text="Сохранить видео")]
DISCOVER = [Node(text="Поиск", clickable=False), Node(desc="Главная"),
            Node(desc="Интересное")]


def feed_tree(likes="1578"):
    return [Node(desc=f"Поставить лайк. Число лайков: {likes}"),
            Node(desc="Поделиться видео. Уже поделились: 951"),
            Node(desc="Профиль Amber recipes"),
            Node(desc="Главная"), Node(desc="Интересное")]


class Phone:
    """Подставной телефон: помнит, что с ним делали."""

    def __init__(self, pause_works=True, likes="1578", sheet_opens=True,
                 has_link=True, search_works=True, paste_works=True,
                 tree_reads=True):
        self.pause_works, self.likes = pause_works, likes
        self.sheet_opens, self.has_link = sheet_opens, has_link
        self.search_works, self.paste_works = search_works, paste_works
        self.tree_reads = tree_reads

        self.playing = True
        self.screen = "feed"
        self.clipboard = ""
        self.field = ""
        self.cleared = False
        self.taps = self.backs = self.node_taps = 0
        self.log = []

    # --- то, что зовёт сессия ---------------------------------------
    def video_playing(self, state, wait=0.9):
        return self.playing if self.screen == "feed" else False

    def tap_video(self, state, w, h):
        self.taps += 1
        if self.pause_works:
            self.playing = not self.playing

    def dump(self, **kw):
        if self.screen == "feed":
            if self.playing or not self.tree_reads:
                return []           # играет — дерево не снимается
            return feed_tree(self.likes)
        if self.screen == "sheet":
            return SHEET if self.has_link else [Node(text="Репост")]
        if self.screen == "discover":
            return DISCOVER if self.search_works else [Node(desc="Главная")]
        if self.screen == "search":
            return [Node(cls="android.widget.EditText", text=self.field),
                    Node(desc="Главная")]
        return []

    def tap_node(self, node):
        self.node_taps += 1
        label = node.text or node.desc
        self.log.append(f"тап: {label}")
        if "Поделиться" in label:
            self.screen = "sheet" if self.sheet_opens else "profile"
        elif "Интересное" in label:
            # Раньше сюда шёл тап по координате, теперь — по узлу: узел в
            # дереве есть, а `ui.tap_node` бьёт внутрь кнопки со случайным
            # разбросом, чего ровная координата не даёт.
            self.screen = "discover"
        elif label == "Ссылка":
            self.clipboard = URL          # адрес ушёл в буфер
            self.screen = "feed"
        elif "Поиск" in label:
            self.screen = "search"
            self.field = ""
        elif "Главная" in label:
            self.screen = "feed"
            self.playing = True
        elif node.cls.endswith("EditText"):
            pass                          # фокус в поле

    def tap(self, x, y):
        # В TikTok сюда попадать больше нечему: и «поделиться», и вкладки
        # берутся из дерева и жмутся через `ui.tap_node`. Оставлено нарочно —
        # если кто-то снова начнёт тапать по голым координатам, стенд уедет
        # на «Интересное» не вовремя, и это будет видно.
        self.taps += 1
        if self.screen == "feed":
            self.screen = "discover"

    def keyevent(self, name):
        if name == "KEYCODE_PASTE" and self.screen == "search":
            if self.paste_works:
                self.field = self.clipboard

    def shell(self, cmd, **kw):
        if "KEYCODE_DEL" in cmd:
            self.cleared = True
            self.field = ""
        return ""

    def back(self):
        self.backs += 1
        self.screen = {"search": "discover", "discover": "feed",
                       "sheet": "feed", "profile": "feed"}.get(self.screen,
                                                               "feed")
        if self.screen == "feed":
            self.playing = True


def run(phone, cfg=CFG):
    session._video_playing = phone.video_playing
    session._tap_video = phone.tap_video
    session.ui.dump = phone.dump
    session.ui.tap_node = phone.tap_node
    session.adb.tap = phone.tap
    session.adb.keyevent = phone.keyevent
    session.adb.shell = phone.shell
    session.device.back = phone.back
    session.human.pause = lambda a, b: 0
    session._ensure_feed = lambda cfg, log, timeout=4: True
    session.ui.feed_caption = lambda nodes, screen=None, package=None: (
        "Куриные энчилады с сыром", "", "")

    saved.clear()
    session.jobs.add_link = lambda url, app, likes, **kw: (
        saved.append((url, app, likes, kw)) or True)

    state = {"same_frames": 0, "screen": (1080, 2400), "id": "проверка",
             "package": "com.zhiliaoapp.musically"}
    log = []
    got = session._save_link(state, cfg, 1080, 2400, log, "tiktok")
    return got, phone, log


saved = []
real = {name: getattr(session, name) for name in
        ("_video_playing", "_tap_video", "_ensure_feed")}
real_ui = (ui.dump, ui.tap_node, ui.feed_caption)

print("--- обычный ход ---")
got, ph, log = run(Phone(likes="128400"))
say(got is True, "ссылка забрана")
say(saved and saved[0][0] == URL, f"в базу ушёл адрес: {saved[0][0] if saved else '—'}")
say(saved and saved[0][2] == 128400, f"и число лайков: {saved[0][2] if saved else '—'}")
say(ph.screen == "feed" and ph.playing, "вернулись в ленту, ролик играет")
say(ph.cleared, "поле поиска вычищено за собой")
say(any("сохранена" in s for s in log), "в журнале сказано, что сохранили")

print("\n--- лайков мало: дешёвый выход ---")
got, ph, log = run(Phone(likes="1578"))
say(got is False, "ссылку не берём")
say(ph.node_taps == 0, "и до листа «поделиться» дело НЕ дошло")
say(ph.playing, "ролик возвращён в проигрывание")
say(any("не беру" in s for s in log), "причина названа вслух")
say(not saved, "в базу ничего не ушло")

print("\n--- лента без селекторов (shorts, reels) ---")
got, ph, log = run(Phone(), cfg={"share_selectors": []})
say(got is False, "даже не пытаемся")
say(ph.taps == 0 and ph.node_taps == 0, "телефон не тронут вовсе")

print("\n--- пауза не сработала ---")
got, ph, log = run(Phone(pause_works=False, likes="128400"))
say(got is False, "отказ")
say(ph.node_taps == 0, "ничего не нажимали")
say(any("пауза не сработала" in s for s in log), "причина названа")

print("\n--- дерево не снялось ---")
got, ph, log = run(Phone(tree_reads=False, likes="128400"))
say(got is False, "отказ")
say(ph.node_taps == 0, "ничего не нажимали")

print("\n--- число лайков не прочиталось ---")
got, ph, log = run(Phone(likes="нет числа"))
say(got is False, "отказ")
say(any("не прочиталось" in s for s in log), "причина названа")
say(ph.node_taps == 0, "до листа не дошли")

print("\n--- лист «поделиться» не открылся ---")
got, ph, log = run(Phone(likes="128400", sheet_opens=False))
say(got is False, "отказ")
say(ph.backs >= 1, "откатились «назад»")
say(ph.screen == "feed", "вернулись в ленту")
say(not saved, "в базу ничего не ушло")

print("\n--- в листе нет пункта «Ссылка» ---")
got, ph, log = run(Phone(likes="128400", has_link=False))
say(got is False, "отказ")
say(ph.screen == "feed", "вернулись в ленту")

print("\n--- строки поиска не нашлось ---")
got, ph, log = run(Phone(likes="128400", search_works=False))
say(got is False, "отказ")
say(ph.screen == "feed", "всё равно вернулись в ленту")
say(any("поиска" in s for s in log), "причина названа")

print("\n--- вставка не сработала (буфер пуст) ---")
got, ph, log = run(Phone(likes="128400", paste_works=False))
say(got is False, "отказ")
say(not saved, "в базу ничего не ушло")
say(ph.screen == "feed", "вернулись в ленту")

print("\n--- разбор числа лайков ---")
for text, want in [("Поставить лайк. Число лайков: 1578", 1578),
                   ("Поставить лайк. Число лайков: 128 400", 128400),
                   ("Like. Number of likes: 1,500", 1500),
                   ("Число лайков: 1,5 млн", 1_500_000),
                   ("Число лайков: 12.3K", 12_300),
                   ("Нравится", None)]:
    got = session._parse_count(text)
    say(got == want, f"{text!r} -> {got}")

for name, fn in real.items():
    setattr(session, name, fn)
ui.dump, ui.tap_node, ui.feed_caption = real_ui

print("\nИТОГ:", "всё сходится" if ok else "ЕСТЬ ПРОВАЛЫ")
sys.exit(0 if ok else 1)
