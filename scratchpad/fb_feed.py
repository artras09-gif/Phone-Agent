"""Лента Facebook Reels: рецепт против живого дерева (снято 2026-11-04,
второй телефон, Facebook 585, английский интерфейс).

Проверяет: ленту по выбранной вкладке, «Главную» — не лентой, описание и
автора с полноэкранного узла, счётчик реакций, кнопки лайка, комментариев и
«поделиться», докрутку ряда значков до «Copy link» и запрет «Share now».
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402

config.BASE = tempfile.mkdtemp(prefix="pa_fbfeed_")
import session  # noqa: E402
import ui  # noqa: E402

PKG = "com.facebook.katana"
ok = True


def check(what, cond):
    global ok
    print(("  ок  " if cond else "  ОШИБКА  ") + what)
    ok = ok and bool(cond)


def node(b, desc="", text="", cls="android.view.ViewGroup", clickable=False, selected=False):
    return ui.Node({"bounds": "[%d,%d][%d,%d]" % b, "content-desc": desc, "text": text,
                    "class": cls, "package": PKG, "clickable": str(clickable).lower(),
                    "selected": str(selected).lower(), "enabled": "true"})


def tabs(selected):
    names = ["Home, tab 1 of 6", "Reels, tab 2 of 6", "Friends, tab 3 of 6",
             "Marketplace, tab 4 of 6", "Notifications, tab 5 of 6", "Profile, tab 6 of 6"]
    out = []
    for i, name in enumerate(names):
        if i == 1 and selected != 1:
            name += ", 1 new"
        out.append(node((i * 214, 2538, (i + 1) * 214, 2720), desc=name,
                        cls="android.app.ActionBar$Tab", clickable=True, selected=i == selected))
    return out


CAPTION = "How Banana Slugs Glide Across Razor Blades 😮"
FEED = [
    node((0, 0, 1280, 2772), cls="android.widget.FrameLayout"),
    node((0, 133, 1280, 2538), desc=CAPTION, clickable=True),
    node((0, 133, 1280, 2538), desc="Reels tab details", cls="android.widget.Button", clickable=True),
    node((0, 133, 1280, 2538), desc="Tap to show video controls",
         cls="android.widget.FrameLayout", clickable=True),
    node((39, 2298, 156, 2415), desc="View Zack D. Films's reels", cls="android.widget.Button",
         clickable=True),
    node((183, 2297, 734, 2354), desc="Zack D. Films, Privacy setting Public",
         text="Zack D. Films, Privacy setting Public", cls="android.widget.Button", clickable=True),
    node((779, 2316, 980, 2397), desc="Follow Zack D. Films", cls="android.widget.Button",
         clickable=True),
    node((39, 2445, 1072, 2500), desc=CAPTION, cls="android.widget.Button", clickable=True),
    node((1124, 1616, 1280, 1746), desc="Like button. Double tap and hold to react.",
         cls="android.widget.Button", clickable=True),
    node((1124, 1737, 1280, 1815), desc="235K reactions", cls="android.widget.Button",
         clickable=True),
    node((1124, 1815, 1280, 2014), desc="716 comments", cls="android.widget.Button",
         clickable=True),
    node((1124, 1936, 1280, 2014), desc="716 comments", text="716 comments"),
    node((1124, 2014, 1280, 2213), desc="Share, 1.4K shares", cls="android.widget.Button",
         clickable=True),
    node((1124, 2213, 1280, 2412), desc="Save", cls="android.widget.Button", clickable=True),
    node((1124, 2412, 1280, 2500), desc="More", cls="android.widget.Button", clickable=True),
    node((20, 184, 163, 301), desc="Menu", cls="android.widget.Button", clickable=True),
    node((1000, 184, 1117, 301), desc="Search", cls="android.widget.Button", clickable=True),
    node((1143, 184, 1260, 301), desc="Navigate to your Reels profile",
         cls="android.widget.Button", clickable=True),
] + tabs(1)

HOME = [
    node((195, 1131, 1098, 1241), desc="What's on your mind?", text="What's on your mind?",
         cls="android.widget.Button", clickable=True),
    node((0, 2418, 1280, 2720), desc="Reel", cls="android.widget.Button", clickable=True),
] + tabs(0)

cfg = session._feed_config("facebook")
print("Facebook Reels: рецепт против живого дерева")

check("лента опознаётся по выбранной вкладке Reels",
      session._pick(FEED, cfg["feed_selected"]) is not None)
check("на «Главной» вкладка Reels не выбрана — это не лента",
      session._pick(HOME, cfg["feed_selected"]) is None
      and session._pick(HOME, cfg["feed_markers"]) is None)
tab = session._pick(HOME, cfg["feed_tabs"])
check("с «Главной» жмётся именно вкладка Reels", tab is not None and tab.desc.startswith("Reels, tab"))
check("маркер ленты на месте", session._pick(FEED, cfg["feed_markers"]) is not None)

caption, author = session._recipe_caption(FEED, cfg, PKG)
check(f"описание с полноэкранного узла: {caption!r}", caption == CAPTION)
check(f"автор без обёртки «View … 's reels»: {author!r}", author == "Zack D. Films")
check("автор у Shorts по-прежнему чистится",
      session._author_name('Перейти на канал "@motion"') == "@motion")

counter = session._pick(FEED, cfg["like_count_selectors"])
check("счётчик реакций прочитан: 235 000",
      counter is not None and session._parse_count(counter.desc) == 235_000)
like = session._pick(FEED, cfg["like_selectors"])
check("кнопка лайка найдена", like is not None and like.bounds[1] == 1616)
check("до лайка «уже лайкнут» не видно", session._pick(FEED, cfg["liked_selectors"]) is None)
liked = [n for n in FEED if n.desc != like.desc] + [
    node((1124, 1616, 1280, 1746), desc="Like button, pressed. Double tap and hold to change reaction.",
         cls="android.widget.Button", clickable=True, selected=True)]
check("после лайка кнопка «pressed» находится", session._pick(liked, cfg["liked_selectors"]) is not None)
com = session._pick(FEED, cfg["comment_selectors"])
check("комментарии — кнопка в колонке", com is not None and com.clickable and com.bounds[0] == 1124)
share = session._pick(FEED, cfg["share_selectors"])
check("«поделиться» — кнопка в колонке", share is not None and share.bounds[1] == 2014)
search = session._pick(FEED, cfg["discover_tabs"])
check("поиск для чтения буфера — значок в шапке", search is not None and search.bounds[1] == 184)

print("\n«Главная» вместо Reels (жалоба 2026-11-04)")
real_dump, real_swipe, real_tap = session.ui.dump, session.adb.swipe, session.adb.tap
real_pause, real_size = session.human.pause, session.adb.screen_size
session.human.pause = lambda *a: None
session.abort.sleep = lambda s: False
session.adb.screen_size = lambda: (1280, 2772)
acts = []
session.adb.swipe = lambda *a: acts.append(("swipe",) + a[:4])
session.adb.tap = lambda x, y: acts.append(("tap", x, y))
session.ui.dump = lambda **k: []
check("пустое дерево у Facebook — НЕ лента (на «Главной» играет видео)",
      session._off_feed(cfg) != "")
check("у Instagram пустое дерево по-прежнему лента",
      session._off_feed(session._feed_config("reels")) == "")

# Дерева нет, пока не нажата вкладка Reels по месту; после — лента Reels.
state = {"in_reels": False}
real_tap_fn = session.adb.tap


def tap(x, y):
    acts.append(("tap", x, y))
    state["in_reels"] = True


session.adb.tap = tap
session.ui.dump = lambda **k: FEED if state["in_reels"] else []
log = []
went = session._ensure_feed(cfg, log)
check("сначала протяжка вниз (вернуть панель), потом вкладка по месту",
      [a[0] for a in acts[-3:]] == ["swipe", "swipe", "tap"] or
      [a[0] for a in acts[-2:]] == ["swipe", "tap"])
check("вкладка нажата в нижней панели, 2-я из 6",
      acts[-1][0] == "tap" and 214 <= acts[-1][1] <= 428 and acts[-1][2] > 2538)
check(f"и это засчитано как лента: {log}", went)

# Панель спрятана, но дерево есть (картинка на «Главной»): после протяжки
# вкладка находится в дереве и жмётся узлом.
acts.clear()
state["swiped"] = False
HIDDEN = [n for n in HOME if "tab" not in n.desc]


def swipe(*a):
    acts.append(("swipe",) + a[:4])
    state["swiped"] = True


session.adb.swipe = swipe
state["in_reels"] = False
taps = []
session.ui.tap_node = lambda n: (taps.append(n.desc), state.update(in_reels=True))
session.ui.dump = lambda **k: (FEED if state["in_reels"] else HOME if state["swiped"] else HIDDEN)
went = session._ensure_feed(cfg, [])
check("панель спрятана — протяжка, и вкладка Reels нажата узлом",
      went and acts and acts[0][0] == "swipe" and taps and taps[0].startswith("Reels, tab"))
session.ui.dump, session.adb.swipe, session.adb.tap = real_dump, real_swipe, real_tap
session.human.pause, session.adb.screen_size = real_pause, real_size

SHEET1 = [
    node((0, 1084, 1280, 1123), desc="Close", cls="android.widget.Button", clickable=True),
    node((881, 1775, 1188, 1892), desc="Share now", cls="android.widget.Button", clickable=True),
    node((0, 1984, 267, 2326), desc="Send to Dan", cls="android.widget.Button", clickable=True),
    node((267, 1984, 534, 2326), desc="Select more friends on Messenger to send the link to",
         cls="android.widget.Button", clickable=True),
] + [node((i * 267, 2352, (i + 1) * 267, 2694), desc=d, cls="android.widget.Button", clickable=True)
     for i, d in enumerate(["Your story", "Messenger", "WhatsApp", "WhatsApp Status"])]
SHEET2 = SHEET1[:4] + [
    node((x1, 2352, x2, 2694), desc=d, cls="android.widget.Button", clickable=True)
    for x1, x2, d in [(0, 141, "Text"), (141, 408, "Group"), (408, 675, "Copy link"),
                      (675, 942, "Instagram"), (942, 1209, "Threads")]]

check("на первом экране листа «Copy link» нет", session._pick(SHEET1, cfg["link_selectors"]) is None)
swipes = []
session.adb.swipe = lambda *a: swipes.append(a)
session.human.pause = lambda *a: None
session.ui.dump = lambda **k: SHEET2
log = []
fresh = session._scroll_link_row(SHEET1, 1280, log)
check("ряд значков докручен свайпом по его середине",
      len(swipes) == 1 and swipes[0][1] == swipes[0][3] == 2523 and swipes[0][0] > swipes[0][2])
link = session._pick(fresh or [], cfg["link_selectors"])
check("после докрутки «Copy link» находится", link is not None and link.desc == "Copy link")
check("«Copy link» — не кнопка отправки", not session._is_sending(link))
check("«Share now» и «Your story» — кнопки отправки",
      session._is_sending(SHEET1[1]) and session._is_sending(SHEET1[4]))

print("\nВСЁ СОШЛОСЬ" if ok else "\nЕСТЬ ОШИБКИ")
sys.exit(0 if ok else 1)
