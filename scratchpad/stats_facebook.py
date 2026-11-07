"""Статистика Facebook без телефона (экраны сняты живьём 2026-11-04, второй
телефон, Facebook 585 по-английски, личный профиль без профессионального режима).

Проверяет: плитки «Reel number 1, 246 plays» — одно видео с 246 просмотрами
(не номер рилса и не повтор числа под плиткой), свой профиль и имя в шапке,
просмотры за период по снимкам (нет снимка — честно «всё время»), путь к
«Post details» свежего рилса, строку «Facebook — …» в цифрах и в альбоме,
пропуск телефона без Facebook.
"""
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402

tmp = tempfile.mkdtemp(prefix="pa_fbstats_")
config.BASE = tmp
config.DB_PATH = os.path.join(tmp, "jobs.db")
config.QUEUE_DIR = os.path.join(tmp, "queue")
config.SERIAL = None

import accstats  # noqa: E402
import jobs      # noqa: E402
import ui        # noqa: E402

jobs._SCHEMA_READY = False
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


def bar(selected=5):
    names = ["Home, tab 1 of 6", "Reels, tab 2 of 6, 1 new", "Friends, tab 3 of 6",
             "Marketplace, tab 4 of 6", "Notifications, tab 5 of 6, 4 new", "Profile, tab 6 of 6"]
    return [node((i * 214, 2538, (i + 1) * 214, 2720), desc=d, cls="android.app.ActionBar$Tab",
                 clickable=True, selected=i == selected) for i, d in enumerate(names)]


H, W = 2772, 1280
PROFILE = [
    node((1123, 770, 1266, 913), desc="Edit cover photo", cls="android.widget.Button", clickable=True),
    node((443, 1131, 849, 1219), desc="Dan Morris", text="Dan Morris"),
    node((443, 1131, 830, 1219), text="Dan Morris", cls="android.widget.Button", clickable=True),
    node((1136, 1123, 1241, 1227), desc="Open profile switcher", cls="android.widget.Button", clickable=True),
    node((575, 1221, 706, 1303), desc="1 post", cls="android.widget.Button"),
    node((39, 1355, 624, 1472), desc="Add to story", cls="android.widget.Button", clickable=True),
    node((650, 1355, 1241, 1472), desc="Edit profile", cls="android.widget.Button", clickable=True),
    node((81, 2351, 506, 2468), desc="Add friend", cls="android.widget.Button", clickable=True),
    node((39, 2576, 173, 2693), desc="All, 1 of 3", cls="android.widget.TabWidget", clickable=True, selected=True),
    node((458, 2576, 656, 2693), desc="Reels, 3 of 3", cls="android.widget.TabWidget", clickable=True),
] + bar()
REELS = [
    node((39, 1453, 173, 1570), desc="All, 1 of 3", cls="android.widget.TabWidget", clickable=True),
    node((458, 1453, 656, 1570), desc="Reels, 3 of 3", cls="android.widget.TabWidget", clickable=True, selected=True),
    node((0, 1778, 425, 2534), desc="Create reel", cls="android.widget.Button"),
    node((95, 2225, 330, 2286), desc="Create reel", text="Create reel"),
    node((427, 1778, 853, 2537), desc="Reel number 1, 246 plays", cls="android.widget.Button", clickable=True),
    node((428, 1778, 852, 2533), desc="Reel number 1, 246 plays", cls="android.widget.ImageView"),
    node((519, 2450, 591, 2499), desc="246", text="246"),
    node((853, 1778, 1279, 2537), desc="Reel number 2, 1.2K plays", cls="android.widget.Button", clickable=True),
] + bar()

cfg = accstats.NETS["facebook"]
print("Facebook: профиль и плитки")
check("свой профиль узнан", accstats._own_state(PROFILE, cfg, H) is True)
check("чужой профиль (Add friend + Message в шапке) — не свой",
      accstats._own_state([node((100, 900, 600, 1000), desc="Add friend", clickable=True),
                           node((650, 900, 1200, 1000), desc="Message", clickable=True)], cfg, H) is False)
check(f"имя в шапке: {accstats._fb_name(PROFILE)!r}", accstats._fb_name(PROFILE) == "Dan Morris")
tab = accstats._pick(PROFILE, cfg["steps"][0]["find"], H, bottom=True)
check("«Профиль» — шестая вкладка нижней панели", tab is not None and tab.desc.startswith("Profile, tab"))
check("вкладка Reels профиля под нижней панелью — сперва не берётся",
      accstats._pick(PROFILE, cfg["grid_tab"], H, top_only=True) is None)
check("после подтяжки — берётся вкладка профиля, а не «Reels» нижней панели",
      (accstats._pick(REELS, cfg["grid_tab"], H, top_only=True) or node((0, 0, 1, 1))).desc == "Reels, 3 of 3")
got = accstats.parse_profile(REELS, H, cfg["tile_re"])
check(f"просмотры рилсов: {[v['views'] for v in got['videos']]}",
      [v["views"] for v in got["videos"]] == [246, 1200])
check("без образца было бы неверно (номер рилса за просмотры) — образец нужен",
      [v["views"] for v in accstats.parse_profile(REELS, H)["videos"]] != [246, 1200])
tiles = accstats._fb_tiles(REELS, H)
check("свежий рилс для статистики — первая плитка", tiles and tiles[0].desc.startswith("Reel number 1"))

print("\nпросмотры за период — по своим снимкам")
now = time.time()
vids = [{"views": 246, "title": ""}]
views, when = accstats._fb_period(vids, 1, now)
check(f"снимков ещё нет — сумма за всё время, так и сказано: {views} / {when[:40]}…",
      views == "246" and when.startswith("всё время"))
accstats.save("facebook", {"followers": None, "videos": [{"views": 200, "title": ""}]}, at=now - 24 * 3600)
views, when = accstats._fb_period(vids, 1, now)
check(f"за сутки: 246 − 200 = {views} ({when})", views == "46" and "–" in when)
views, _ = accstats._fb_period([{"views": 30, "title": ""}] + vids, 1, now)
check(f"вышел новый рилс — он целиком в прибавку: {views}", views == "76")
views, when = accstats._fb_period(vids, None, now)
check("без периода — всё время", views == "246" and "всё время" in when)
cut = accstats._fb_cutoff("week", now)
first, _ = accstats.report_span("week")
check("неделя считается от снимка в её первый день",
      abs(cut - time.mktime(first.timetuple())) < 2 * 86400)

print("\nснимок «Post details»")
folder = os.path.join(tmp, "shots")
viewer = [node((39, 2386, 373, 2480), desc="View insights", cls="android.widget.Button", clickable=True),
          node((117, 2411, 347, 2457), desc="View insights", text="View insights")]
details = [node((493, 173, 787, 237), desc="Post details", text="Post details"),
           node((39, 1581, 627, 1787), desc="Views, 246"),
           node((653, 1581, 1241, 1787), desc="Interactions, 3")]
screens = {"now": REELS}
taps, backs = [], []


def tap_node(n):
    taps.append(n.desc)
    screens["now"] = viewer if n.desc.startswith("Reel number") else details


accstats.ui.dump = lambda **k: screens["now"]
accstats.ui.tap_node = tap_node
accstats.device.back = lambda: backs.append(1)
accstats.human.pause = lambda *a: None
accstats.time.sleep = lambda s: None
accstats.adb.current_app = lambda: (PKG, "")
accstats._wait_clean = lambda **k: None


def fake_shot(path):
    with open(path, "wb") as f:
        f.write(b"x" * 20_000)


accstats.adb.screenshot = fake_shot
swipes = []
accstats.adb.shell = lambda cmd, **k: swipes.append(cmd) or ""      # к телефону — никогда
path = accstats._shot_facebook(folder, W, H, 1, vids)
said = accstats._notes_in(folder)
check(f"рилс → View insights → снимок: {taps}", path and taps == ["Reel number 1, 246 plays", "View insights"])
check(f"подпись снимка: {said.get('facebook')!r}", "246 просмотров за всё время" in said.get("facebook", ""))
check("назад дважды — к профилю", len(backs) == 2)
text = accstats.numbers_text(folder)
check(f"цифры текстом: {text!r}", text.startswith("Facebook — 46"))
items = accstats.shots_in(folder)
check("в альбоме — «Facebook — статистика»",
      any(t.startswith("Facebook — статистика") for _, t in items))
check("снимок только из Facebook", accstats._snap_app("facebook") == PKG
      and accstats._snap_app("facebook_tiles") == PKG)

screens["now"] = [node((0, 1778, 425, 2534), desc="Create reel", cls="android.widget.Button")] + bar()
check("рилсов нет — снимка нет, без касаний", accstats._shot_facebook(os.path.join(tmp, "e"), W, H, 1, []) is None)

print("\nпрофессиональный режим: Creator Dashboard → Analytics")
import datetime as dt  # noqa: E402
yest = dt.date.today() - dt.timedelta(days=1)
mon = yest.strftime("%b") + f" {yest.day}"
PRO = [node((39, 1494, 535, 1611), desc="Dashboard", cls="android.widget.Button", clickable=True)] + REELS
WELCOME = [node((653, 2525, 1241, 2642), desc="Explore on your own", cls="android.widget.Button", clickable=True)]
DASH = [node((39, 301, 316, 431), desc="Analytics, 1 of 4", cls="android.widget.Button", clickable=True)]
ANALYTICS = [
    node((1144, 145, 1261, 262), desc="Select date range", cls="android.widget.Button", clickable=True),
    node((78, 441, 507, 502), desc="Views", text="Views", selected=True),
    node((78, 528, 102, 584), desc="57", text="57", selected=True),
    node((600, 836, 701, 957), desc=f"{mon}, 12"),
]
first7 = yest - dt.timedelta(days=6)
RANGES = [node((0, 1955, 1280, 2130), clickable=True, cls="android.widget.RadioButton",
               desc=f"Last 7 days, {first7:%b} {first7.day} - {mon}"),
          node((0, 2305, 1280, 2480), clickable=True, cls="android.widget.RadioButton",
               desc="Last 28 days, Oct 7 - Nov 3")]
flow = {"Dashboard": WELCOME, "Analytics, 1 of 4": ANALYTICS, "Select date range": RANGES}
taps.clear()
backs.clear()


def tap_pro(n):
    taps.append(n.desc)
    screens["now"] = flow.get(n.desc, ANALYTICS)


def back_pro():
    backs.append(1)
    if screens["now"] is WELCOME:
        screens["now"] = DASH


accstats.ui.tap_node = tap_pro
accstats.device.back = back_pro
screens["now"] = PRO
f2 = os.path.join(tmp, "pro")
path = accstats._shot_facebook(f2, W, H, 1, vids)
said = accstats._notes_in(f2)
check(f"панель → (шторка «назад») → Analytics → 7 дней: {taps}",
      path and taps[:3] == ["Dashboard", "Analytics, 1 of 4", "Select date range"]
      and taps[3].startswith("Last 7 days"))
check("в шторке приветствия ничего не нажато", "Explore on your own" not in taps)
check(f"за вчера — подпись точки графика: {said.get('facebook_views')} за {said.get('facebook_when')}",
      said.get("facebook_views") == "12" and said.get("facebook_when") == f"{yest:%d.%m}")
check("сказано, что считает с включения режима", "профессионального режима" in said.get("facebook", ""))
screens["now"] = PRO
taps.clear()
f3 = os.path.join(tmp, "pro2")
accstats._shot_facebook(f3, W, H, 2, vids)
check("за 2 дня 7-дневное число не выдаётся за 2 дня",
      accstats._notes_in(f3).get("facebook_views") != "57")
screens["now"] = PRO
f4 = os.path.join(tmp, "pro3")
accstats._shot_facebook(f4, W, H, "month", vids)
check("месяц: у Facebook только «последние 28 дней» — число не подменяется",
      accstats._notes_in(f4).get("facebook_views") != "57")

print("\nтелефон без Facebook")
answers = {}
accstats.adb.shell = lambda cmd, **k: answers.get(cmd.split()[-1], "")
check("Facebook нет — сеть пропускается", accstats._has_app("facebook") is False)
answers[PKG] = "package:/data/app/base.apk"
check("Facebook есть — снимается", accstats._has_app("facebook") is True)
check("TikTok проверкой не пропускается никогда", accstats._has_app("tiktok") is True)
check("Facebook в списке сетей статистики", "facebook" in accstats.SHOTS and "facebook" in config.FEED_APPS)

print("\nВСЁ СОШЛОСЬ" if ok else "\nЕСТЬ ОШИБКИ")
sys.exit(0 if ok else 1)
