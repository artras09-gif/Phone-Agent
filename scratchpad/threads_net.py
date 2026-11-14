"""Threads (просьба 2026-11-13): публикация и статистика, без «прогрева».

Без телефона: дерево экрана подставлено — то самое, что снято живьём 13.11 на
обоих Redmi (Threads 455 по-английски). Проверяется, что Threads — сеть для
поста и статистики, но не лента; что в галерее выбирается только СВЕЖИЙ ролик
(имени и длины клетка не называет); что чужой недописанный пост не уходит в
новый; и что статистика честно говорит «за 7 дней», а не «за вчера».
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config     # noqa: E402

tmp = tempfile.mkdtemp(prefix="pa_threads_")
config.DB_PATH = os.path.join(tmp, "jobs.db")
config.QUEUE_DIR = os.path.join(tmp, "queue")
config.LOG_DIR = os.path.join(tmp, "logs")
os.makedirs(config.QUEUE_DIR)

import accstats        # noqa: E402
import poster          # noqa: E402
import postspec        # noqa: E402
import scheduler       # noqa: E402
import telegram_bot    # noqa: E402
import ui              # noqa: E402

ok = True


def check(text, good):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


def node(text="", desc="", rid="", bounds=(0, 0, 100, 100), clickable=False, cls="android.view.View"):
    return ui.Node({"text": text, "content-desc": desc, "clickable": "true" if clickable else "false",
                    "resource-id": ("com.instagram.barcelona:id/" + rid) if rid else "",
                    "class": cls, "package": "com.instagram.barcelona", "enabled": "true",
                    "bounds": "[%d,%d][%d,%d]" % bounds})


print("--- Threads — сеть для поста и статистики, но не лента ---")
check("«все сети» включают Threads", postspec.parse("описание: текст")["targets"][-1] == "threads")
check("«в тредс» — только Threads", postspec.parse("куда: тредс\nописание: x")["targets"] == ["threads"])
check("«все кроме тредс»",
      postspec.parse("куда: все кроме тредс\nописание: x")["targets"] == ["tiktok", "shorts", "reels", "facebook"])
check("кнопка в черновике бота", telegram_bot.TARGET_ORDER[-1] == "threads"
      and telegram_bot.BUTTON_TITLES["threads"] == "Threads")
check("в лентах Threads нет: прогревать его не просили",
      "threads" not in config.FEED_APPS and "threads" not in config.DEFAULT_SESSION_APPS)
check("в сетях статистики есть", "threads" in config.STATS_APPS and "threads" in accstats.SHOTS)
check("окно предлагает его для публикации", "threads" in scheduler.normalize_targets("threads,tiktok").split(","))
long_text = "слово " * 200
check("текст укладывается в 500 знаков по целому слову",
      len(postspec.fit(long_text, "threads")) <= 500 and postspec.fit(long_text, "threads").endswith("слово"))

print("\n--- маршрут ---")
recipe = poster.load_recipes()["threads"]
ops = [s["op"] for s in recipe["steps"]]
check("маршрут начинается с главной и кончается финальной кнопкой", ops[0] == "home" and ops[-1] == "publish")
check("перед «Post» обязательно ждём, что ролик прикреплён (иначе ушёл бы пост из одного текста)",
      "wait" in ops[ops.index("pick_video"):] and any(
          "attachment" in str(s.get("any")).lower() for s in recipe["steps"] if s["op"] == "wait"))
check("нет приложения — сеть пропускается, нет доступа к видео — скажет заранее",
      recipe.get("optional_app") and recipe.get("needs_storage"))
check("клавиатуру перед «Post» не убираем: «назад» открыл бы «Save to drafts?»",
      recipe["steps"][-1].get("keep_keyboard") and "hide_keyboard" not in ops)
check("свой недописанный пост маршрут убирает сам", recipe.get("abandon"))

print("\n--- свежесть ролика в галерее ---")
for said, want in (("Video thumbnail, Added on 21 seconds ago", 21),
                   ("Video thumbnail, Added on 2 minutes ago", 120),
                   ("Video thumbnail, Added on a minute ago", 60),
                   ("Video thumbnail, Added on just now", 0),
                   ("Video thumbnail, Added on 3 hours ago", None),
                   ("Video thumbnail, Added on 2 days ago", None),
                   ("Video thumbnail, Added on November 5", None)):
    check(f"{said!r} -> {want}", poster._added_ago(said) == want)

pick = next(s for s in recipe["steps"] if s["op"] == "pick_video")
tapped = []
poster.ui.tap_node = lambda n: tapped.append(n)
poster.human.pause = lambda *a: None
poster.time.sleep = lambda *a: None
poster._snap = lambda *a: None
log_dir = os.path.join(tmp, "log")
os.makedirs(log_dir)
camera = node(rid="gallery_picker_grid_item_container", bounds=(13, 432, 422, 841), clickable=True)
photo = node(desc="Photo thumbnail, Added on 5 seconds ago", rid="gallery_picker_grid_item_container",
             bounds=(436, 432, 844, 841), clickable=True)
ours = node(desc="Video thumbnail, Added on 21 seconds ago", rid="gallery_picker_grid_item_container",
            bounds=(858, 432, 1267, 841), clickable=True)
stale = node(desc="Video thumbnail, Added on 2 days ago", rid="gallery_picker_grid_item_container",
             bounds=(13, 854, 422, 1263), clickable=True)
ctx = {"video_name": "x.mp4", "video_seconds": 9.0}
poster.ui.dump = lambda **kw: [camera, photo, ours, stale]
line = poster._pick_video(dict(pick, timeout=2), ctx, log_dir, 7)
check(f"выбран свежий ролик, а не клетка камеры и не фото: {line}", tapped == [ours])
tapped.clear()
poster.ui.dump = lambda **kw: [camera, stale]
try:
    poster._pick_video(dict(pick, timeout=1), ctx, log_dir, 7)
    refused = False
except poster.StepFailed as e:
    refused = "Чужой не выбираю" in str(e)
check("первое видео добавлено два дня назад — чужое, не выбираем", refused and not tapped)

print("\n--- чужой недописанный пост ---")
guard = next(s for s in recipe["steps"] if s["op"] == "discard_if")
composer = node(rid="new_thread_screen_composer", cls="android.widget.EditText", clickable=True)
poster.ui.dump = lambda **kw: [composer]
check("окно поста пустое — ничего не трогаем",
      "убирать нечего" in poster.run_step(guard, {"package": recipe["package"]}, log_dir, 4) and not tapped)
leftover = [node(text="старый текст", rid="new_thread_screen_composer", cls="android.widget.EditText"),
            node(desc="Remove attachment")]
screens = iter([
    [node(desc="Cancel", rid="navigation_bar_back_button", clickable=True)],
    [node(text="Save", rid="igds_alert_dialog_primary_button", clickable=True),
     node(text="Don't save", rid="igds_alert_dialog_secondary_button", clickable=True)],
    [node(rid="barcelona_tab_create")],
    [composer],
])
poster.ui.dump = lambda **kw: leftover
poster.ui.wait_any = lambda selectors, timeout=20, interval=0.7: (
    lambda nodes: (ui.find_any(nodes, selectors), nodes))(next(screens))
line = poster.run_step(guard, {"package": recipe["package"]}, log_dir, 4)
check(f"остался чужой пост с роликом — сохранён в черновики («Save», не «Don't save»): {line}",
      [n.text or n.desc or n.rid.split("/")[-1] for n in tapped]
      == ["Cancel", "Save", "barcelona_tab_create", "new_thread_screen_composer"])

print("\n--- статистика ---")
INSIGHTS = [
    node(text="Insights", bounds=(195, 185, 1085, 261)),
    node(text="Profile", bounds=(65, 510, 169, 567)),
    node(text="30 days", bounds=(999, 510, 1169, 567)),
    node(text="Views", bounds=(117, 710, 229, 756)),
    node(text="1,234", bounds=(117, 781, 161, 872)),
    node(text="12%", bounds=(117, 872, 171, 918)),
    node(text="Viewers", bounds=(650, 710, 799, 756)),
    node(text="900", bounds=(650, 781, 694, 872)),
    node(text="Posts", bounds=(65, 1666, 151, 1723)),
    node(text="90 days", bounds=(999, 1666, 1169, 1723)),
]
MENU = [node(text=f"{d} days", bounds=(648, 646 + i * 182, 843, 710 + i * 182))
        for i, d in enumerate((7, 14, 30, 90))]
check("выбранный период блока «Profile» — верхняя подпись, а не «90 days» у «Posts»",
      accstats._th_shown(INSIGHTS) == 30)
state = {"screen": INSIGHTS, "taps": []}


def th_tap(n):
    state["taps"].append(n.text)
    if n in INSIGHTS:
        state["screen"] = MENU                       # открылся список периодов
    elif n in MENU:
        days = n.text
        state["screen"] = [node(text=days, bounds=(1026, 510, 1169, 567)) if x.text == "30 days" else x
                           for x in INSIGHTS]


accstats.ui.dump = lambda **kw: state["screen"]
accstats.ui.tap_node = th_tap
accstats.human.pause = lambda *a: None
accstats.time.sleep = lambda *a: None
check("«за вчера» — выбирается самый короткий период, 7 дней", accstats._th_pick(7) == 7
      and state["taps"] == ["30 days", "7 days"])
state["taps"].clear()
check("период уже тот — список не открываем", accstats._th_pick(7) == 7 and not state["taps"])

folder = os.path.join(tmp, "shots")
accstats._snap = lambda folder, name, package=None: os.path.join(folder, name + ".png")
accstats.device.back = lambda: None
profile = [node(text="View insights", bounds=(680, 859, 979, 916))]
state["screen"] = profile


def th_tap2(n):
    if n.text == "View insights":
        state["screen"] = INSIGHTS
    else:
        th_tap(n)


accstats.ui.tap_node = th_tap2
path = accstats._shot_threads(folder, 1280, 2772, days=1)
said = accstats._notes_in(folder)
check(f"просмотры прочитаны из дерева экрана: {said.get('threads_views')!r} за {said.get('threads_when')!r}",
      path and said.get("threads_views") == "1 234" and said.get("threads_when") == "7 дней")
check(f"пояснение не выдаёт 7 дней за вчера: {said.get('threads')!r}",
      "7 дней" in said.get("threads", "") and "отдельного дня Threads не показывает" in said.get("threads", ""))
check(f"в отчёте числом — всегда с периодом: {accstats.numbers_text(folder, ['threads'])!r}",
      accstats.numbers_text(folder, ["threads"]) == "Threads — 1 234 (за 7 дней)")
check("подписчики: «1 follower» в единственном числе тоже число",
      bool(accstats.TH_FOLLOWERS.match("1 follower")) and accstats.parse_count("1 follower") == 1
      and not accstats.TH_FOLLOWERS.match("Edit profile"))
check("снимок статистики — только из самого Threads",
      accstats._snap_app("threads") == "com.instagram.barcelona")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
