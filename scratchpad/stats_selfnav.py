"""Самостоятельный путь к статистике (`statnav.py`) и сверка чисел — без телефона.

Телефон — набор экранов: у каждого кнопки и куда ведёт каждая. Модель —
подставная: на вопрос «куда жать» выбирает из списка кнопку, ведущую к цели,
на вопрос «это статистика?» отвечает по имени экрана.

Проверяется:
  * модель находит дорогу, путь запоминается вместе с эталонами экранов;
  * второй раз путь проходит БЕЗ модели;
  * приложение обновилось, кнопку переименовали — путь обрывается, модель
    находит новый, обрыв сохранён (видно, что поменяли);
  * опасные кнопки (Direct, переключение аккаунта, публикация, переключатели)
    модели даже не показываются;
  * модель не отвечает — сеть не «забывается» на неделю, а не нашла — да;
  * этап 1: версия приложения, «обновился», подозрительный скачок числа.
"""
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402

tmp = tempfile.mkdtemp(prefix="pa_selfnav_")
config.BASE = tmp
config.DB_PATH = os.path.join(tmp, "jobs.db")
config.QUEUE_DIR = os.path.join(tmp, "queue")
config.SERIAL = None
config.STATS_SELF_NAV = True

import abort     # noqa: E402
import accstats  # noqa: E402
import adb       # noqa: E402
import device    # noqa: E402
import escape    # noqa: E402
import human     # noqa: E402
import statnav   # noqa: E402
import ui        # noqa: E402
import vision    # noqa: E402

ok = True
W, H = 1080, 2400
PKG = "com.instagram.android"


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


def node(label, y, cls="android.widget.Button", rid=""):
    return ui.Node({"text": label, "resource-id": rid, "content-desc": "", "class": cls,
                    "package": PKG, "clickable": "true", "enabled": "true", "selected": "false",
                    "bounds": f"[100,{y}][900,{y + 120}]"})


# Экраны Instagram: профиль → ☰ → «Ваша панель» → статистика.
# Опасное лежит рядом с нужным — модель его видеть не должна.
SCREENS = {}
LINKS = {}


def build(panel_label="Ваша панель"):
    SCREENS.clear()
    LINKS.clear()
    SCREENS["профиль"] = [node("Direct", 200), node("my_account ▾ Switch account", 350),
                          node("Изменить профиль", 500), node("Меню", 650),
                          node("Создать", 800)]
    SCREENS["меню"] = [node("Настройки и действия", 300), node("Архив", 450),
                       node("Сохранённое", 600)]
    SCREENS["настройки"] = [node("Уведомления", 300), node(panel_label, 450),
                            node("Режим конфиденциальности", 600, cls="android.widget.Switch"),
                            node("Выйти", 900)]
    SCREENS["панель"] = [node("Просмотры", 400), node("Подписчики", 550)]
    SCREENS["статистика"] = [node("Последние 30 дней", 300)]
    LINKS.update({("профиль", "Меню"): "меню", ("меню", "Настройки и действия"): "настройки",
                  ("настройки", panel_label): "панель", ("панель", "Просмотры"): "статистика"})


WANT = {"профиль": "Меню", "меню": "Настройки и действия", "настройки": None, "панель": "Просмотры"}
phone = {"screen": "профиль", "trail": [], "app": PKG}
asked = {"nav": 0, "look": 0, "down": False}
shown_menus = []


def tap(n):
    label = n.text
    nxt = LINKS.get((phone["screen"], label))
    if nxt:
        phone["trail"].append(phone["screen"])
        phone["screen"] = nxt


def back():
    if phone["trail"]:
        phone["screen"] = phone["trail"].pop()


def fake_ask(png, prompt, **kw):
    if asked["down"]:
        raise vision.VisionError("timed out")
    if "Цель:" in prompt:
        asked["nav"] += 1
        shown_menus.append(prompt)
        if phone["screen"] == "статистика":
            return '{"готово": true, "кнопка": 0}'
        want = WANT.get(phone["screen"]) or ("Insights hub" if any(
            "Insights hub" in line for line in prompt.splitlines()) else "Ваша панель")
        for line in prompt.splitlines():
            m = re.match(r"\[(\d+)\] «(.+?)»", line)
            if m and m.group(2) == want:
                return f'{{"готово": false, "кнопка": {m.group(1)}}}'
        return '{"готово": false, "кнопка": 0, "иначе": "back"}'
    asked["look"] += 1
    if phone["screen"] == "статистика":
        return ('{"статистика": true, "выбор_периода": true, "период": "Nov 18 - Nov 18", '
                '"просмотры": "1,234"}')
    return '{"статистика": false}'


ui.dump = lambda *a, **k: list(SCREENS[phone["screen"]])
ui.tap_node = tap
device.back = back
human.pause = lambda *a, **k: None
human.scroll_feed = lambda *a, **k: None
adb.screen_size = lambda: (W, H)
adb.current_app = lambda: (phone["app"], "")
escape._screencap = lambda: b"\x89PNG" + b"0" * 2000
escape._full_shot = lambda: (b"\x89PNG" + b"0" * 2000, W, H)
escape.hands_ready = lambda iw, ih, say=None: None          # «руки» — в разделе 10
vision.ask = fake_ask
vision.available = lambda: (True, "test")
abort.requested = lambda: False

folder = os.path.join(tmp, "day")
os.makedirs(folder)


def snap():
    path = os.path.join(folder, "instagram.png")
    with open(path, "wb") as f:
        f.write(b"\x89PNG" + phone["screen"].encode("utf-8"))
    return path


def start():
    phone["screen"], phone["trail"] = "профиль", []
    return True


def run(ver="400.0"):
    log = []
    got = statnav.find_stats("reels", PKG, snap, start, ver=ver, log=log)
    return got, log


# ------------------------------------------------------------------
print("1. Пути нет — модель находит дорогу")
build()
got, log = run()
print("   " + "\n   ".join(log))
say(got and got["views"] == "1,234" and got["period"] == "Nov 18 - Nov 18", f"число и период ({got})")
say(got and got["how"] == "путь нашла модель", "помечено, что путь нашла модель")
route = statnav.load_routes().get("reels", {})
say([s.get("label") for s in route.get("steps", [])] ==
    ["Меню", "Настройки и действия", "Ваша панель", "Просмотры"], f"путь запомнен ({route.get('steps')})")
gold = statnav._golden_dir("reels", "400.0")
say(os.path.isfile(os.path.join(gold, "route.json")) and os.path.isfile(os.path.join(gold, "шаг1.json")),
    "эталоны пути сохранены")
menus = "\n".join(shown_menus)
say(not any(bad in menus for bad in ("Direct", "Switch account", "Создать", "Выйти",
                                       "Изменить профиль", "Режим конфиденциальности")),
    "опасные кнопки и переключатели модели не показаны")

print("2. Второй раз — без модели")
asked.update(nav=0, look=0)
got, log = run()
say(got and got["how"] == "по запомненному пути", f"пройдено по памяти ({got and got['how']})")
say(asked["nav"] == 0 and asked["look"] == 1, f"модель спросили только «это статистика?» ({asked})")

print("3. Приложение обновилось — кнопку переименовали")
build(panel_label="Insights hub")
asked.update(nav=0, look=0)
got, log = run(ver="401.0")
print("   " + "\n   ".join(log))
say(got and got["views"] == "1,234", "статистику нашёл заново")
route = statnav.load_routes().get("reels", {})
say("Insights hub" in [s.get("label") for s in route.get("steps", [])] and route.get("ver") == "401.0",
    "запомнен новый путь для новой версии")
say(os.path.isfile(os.path.join(statnav._golden_dir("reels", "401.0"), "обрыв_шаг3.json")),
    "сохранён экран, где старый путь оборвался")

print("4. Модель не отвечает — сеть не забывается")
routes = statnav.load_routes()
routes.pop("reels")
statnav._save_routes(routes)
asked["down"] = True
got, log = run(ver="401.0")
say(got is None and not statnav.load_routes().get("reels", {}).get("failed"),
    "сбой связи не записан как «статистики нет»")
asked["down"] = False

print("5. Статистики в приложении нет — не бродить каждый день")
SCREENS["панель"] = [node("Подписчики", 550)]          # «Просмотры» пропали
got, log = run(ver="401.0")
failed = statnav.load_routes().get("reels", {}).get("failed", {})
say(got is None and failed.get("ver") == "401.0", f"неудача запомнена ({failed})")
asked.update(nav=0)
got, log = run(ver="401.0")
say(asked["nav"] == 0 and any("не ищу до обновления" in line for line in log),
    "на той же версии модель не зовём")
asked.update(nav=0)
build(panel_label="Insights hub")
got, log = run(ver="402.0")
say(got is not None and asked["nav"] > 0, "приложение обновилось — ищем снова")

print("6. Кнопку узнаём по подписи, даже если она переехала")
sig = {"label": "Меню", "rid": "", "x": 0.46, "y": 0.29}
moved = [node("Меню", 1800)]
say(statnav.match(moved, sig, W, H) is moved[0], "переехавшая кнопка найдена по подписи")
say(statnav.match([node("Другое", 700)], sig, W, H) is None, "чужую кнопку за нужную не принимаем")

# ------------------------------------------------------------ этап 1
print("7. Версия приложения и «обновился»")
day = os.path.join(tmp, "queue", "..", "analytics", "2026-11-20")
os.makedirs(day, exist_ok=True)
versions = iter(["47.4.1", "47.5.3"])
accstats._app_version = lambda package: next(versions)
accstats._track_version(day, "tiktok")
log = []
accstats._track_version(day, "tiktok", log)
accstats._views_note(day, "tiktok", "3", "19.11")
text = accstats.numbers_text(day, ["tiktok"])
say("ℹ TikTok обновился: 47.4.1 → 47.5.3" in text, f"в отчёте видно обновление ({text!r})")
say(any("обновилось" in line for line in log), "и в журнале")

print("8. Подозрительный скачок числа")
root = os.path.join(os.path.dirname(config.QUEUE_DIR), "analytics")
for i, v in enumerate([30, 25, 40, 35], start=10):
    d = os.path.join(root, f"2026-11-{i}")
    accstats._views_note(d, "youtube", str(v), f"{i}.11")
today = os.path.join(root, "2026-11-21")
accstats._views_note(today, "youtube", "750 636", "20.11")
text = accstats.numbers_text(today, ["shorts"])
say("⚠" in text and "проверь" in text, f"750 636 при обычных ~30 помечено ({text!r})")
calm = os.path.join(root, "2026-11-22")
accstats._views_note(calm, "youtube", "44", "21.11")
say("⚠" not in accstats.numbers_text(calm, ["shorts"]), "обычное число не помечено")

print("9. Личный аккаунт — модель туда не ходит")
work = os.path.join(root, "2026-11-12")
accstats._note(work, "instagram_account", "studio_work")
accstats._views_note(work, "instagram", "113", "11.11")    # рабочий давал статистику
mine = os.path.join(root, "2026-11-23")
accstats._note(mine, "instagram_account", "personal_one")
accstats._note(mine, "instagram", "в настройках нет «Статистики» — снят профиль")
say(not accstats._nav_allowed("reels", mine), "аккаунт без статистики в прошлом — нельзя")
month = os.path.join(root, "month", "2026-10-01")
accstats._note(month, "instagram_account", "personal_two")
accstats._views_note(month, "instagram", "1 054", "10.2026")      # сумма плиток, не статистика
accstats._note(month, "instagram", "в настройках нет «Статистики» — снят профиль")
os.remove(accstats._stat_accounts_path()) if os.path.isfile(accstats._stat_accounts_path()) else None
probe = os.path.join(root, "2026-11-25")
accstats._note(probe, "instagram_account", "personal_two")
say(not accstats._nav_allowed("reels", probe),
    "число по плиткам профиля («снят профиль») не делает аккаунт рабочим")
say(accstats._route_broke("reels", mine, None) is False, "и маршрут «сломанным» не считается")
other = os.path.join(root, "2026-11-24")
accstats._note(other, "instagram_account", "studio_work")
say(accstats._nav_allowed("reels", other), "рабочий (статистика уже бывала) — можно")
say(accstats._restart_for.__defaults__ == ((),), "старт проверяет аккаунт")

print("10. Кнопки нет в дереве (веб-страница) — модель жмёт по точке")
# Новая TikTok Studio 25.11: «Статистика ›» нарисована, в дереве — только
# вкладки и «Join». Модель называет точку; запретное под точкой — не жмём.
PKG2 = "com.zhiliaoapp.musically"
studio = [node("TikTok Studio", 134), node("Трансляция", 236), node("Join", 1898)]
analytics = [node("Обзор", 300), node("28 дней", 400)]
place = {"screen": "studio", "taps": []}
ui.dump = lambda *a, **k: list(studio if place["screen"] == "studio" else analytics)
adb.current_app = lambda: (PKG2, "")
escape.hands_ready = lambda iw, ih, say=None: "pixels"


def tap_xy(x, y):
    place["taps"].append((x, y))
    if place["screen"] == "studio" and 900 <= x <= 1060 and 400 <= y <= 500:
        place["screen"] = "analytics"


adb.tap = tap_xy
answers = iter(['{"готово": false, "кнопка": 0, "точка": {"x": 520, "y": 1950, "что": "Join"}}',
                '{"готово": false, "кнопка": 0, "точка": {"x": 972, "y": 449, "что": "стрелка Статистика"}}',
                '{"готово": true}'])
seen_prompts = []


def nav_ask(png, prompt, **kw):
    if "Цель:" in prompt:
        seen_prompts.append(prompt)
        return next(answers)
    return ('{"статистика": true, "выбор_периода": true, "период": "23 нояб - 23 нояб", '
            '"просмотры": "13K"}') if place["screen"] == "analytics" else '{"статистика": false}'


vision.ask = nav_ask
statnav._save_routes({})
log = []
steps, why = statnav.explore("tiktok", PKG2, W, H, log)
print("   " + "\n   ".join(log))
say(steps and steps[-1].get("point") == [0.9, 0.187] and place["screen"] == "analytics",
    f"дошёл по точке до аналитики ({steps})")
say(not any(1898 <= y <= 2100 for _, y in place["taps"]), "в «Join» по точке не нажал")
say("пикселях снимка 1080x2400" in seen_prompts[0], "модели сказано, что можно назвать точку")
place.update(screen="studio", taps=[])
statnav.replay(steps, W, H)
say(place["screen"] == "analytics", "запомненная точка повторяется без модели")

print("11. По кругу не ходит")
loop = {"screen": "profile"}
prof = [node("Меню", 300), node("Просмотры профиля", 600)]
views_scr = [node("Вернуться", 200)]
ui.dump = lambda *a, **k: list(prof if loop["screen"] == "profile" else views_scr)


def tap_loop(n):
    if n.text == "Просмотры профиля":
        loop["screen"] = "views"


ui.tap_node = tap_loop
device.back = lambda: loop.__setitem__("screen", "profile")
escape.hands_ready = lambda iw, ih, say=None: None
menus = []


def loop_ask(png, prompt, **kw):
    if "Цель:" not in prompt:
        return '{"статистика": false}'
    menus.append(prompt)
    for line in prompt.splitlines():
        m = re.match(r"\[(\d+)\] «(.+?)»", line)
        if m and m.group(2) == "Просмотры профиля":
            return f'{{"кнопка": {m.group(1)}}}'
    return '{"кнопка": 0, "иначе": "back"}'


vision.ask = loop_ask
statnav.explore("reels", PKG2, W, H, [], max_steps=5)
offers = sum(bool(re.search(r"^\[\d+\] «Просмотры профиля»", p, re.M)) for p in menus if "«Меню»" in p)
say(offers == 1, f"«Просмотры профиля» на том же экране предложена один раз, а не каждый круг ({offers})")

print("12. Дошла до статистики «за 7 дней» — модель сама выбирает вчера")
# Живьём 25.11: модель дошла до статистики TikTok, но принесла «18–24 нояб,
# 104K» — период по умолчанию. Теперь там же выбирает один день.
import datetime as dt  # noqa: E402
yday = dt.date.today() - dt.timedelta(days=1)
MON = ["янв", "февр", "мар", "апр", "мая", "июн", "июл", "авг", "сент", "окт", "нояб", "дек"]
one = f"{yday.day} {MON[yday.month - 1]} - {yday.day} {MON[yday.month - 1]}"
day_scr = {"screen": "stats7"}
DAYS = {"stats7": [node("7 дней", 300), node("Обзор", 150)],
        "chips": [node("Вчера", 500), node("28 дней", 650)],
        "stats1": [node("Вчера", 300), node("Обзор", 150)]}
ui.dump = lambda *a, **k: list(DAYS[day_scr["screen"]])
ui.tap_node = lambda n: day_scr.__setitem__("screen", {("stats7", "7 дней"): "chips",
                                                     ("chips", "Вчера"): "stats1"}.get(
    (day_scr["screen"], n.text), day_scr["screen"]))
escape.hands_ready = lambda iw, ih, say=None: None
asked_day = {"nav": 0}


def day_ask(png, prompt, **kw):
    if "Цель:" in prompt:
        asked_day["nav"] += 1
        if day_scr["screen"] == "stats1":
            return '{"готово": true}'
        want = "7 дней" if day_scr["screen"] == "stats7" else "Вчера"
        for line in prompt.splitlines():
            m = re.match(r"\[(\d+)\] «(.+?)»", line)
            if m and m.group(2) == want:
                return f'{{"кнопка": {m.group(1)}}}'
        return '{"иначе": "back"}'
    period = one if day_scr["screen"] == "stats1" else "18 нояб - 24 нояб"
    return f'{{"статистика": true, "выбор_периода": true, "период": "{period}", "просмотры": "13K"}}'


vision.ask = day_ask
shots = os.path.join(tmp, "dayshots")
os.makedirs(shots, exist_ok=True)


def day_snap():
    path = os.path.join(shots, f"tiktok_day_{day_scr['screen']}.png")
    with open(path, "wb") as f:
        f.write(b"\x89PNG" + day_scr["screen"].encode())
    return path


got = statnav.pick_day("tiktok", PKG2, day_snap, accstats._day_accept, "вчера", ver="36.7.4", log=[])
say(got and got["period"] == one and got["views"] == "13K", f"выбран вчерашний день: {got}")
say(accstats._period_when(got["period"]) == f"{yday:%d.%m}", "в отчёте — дата вчерашнего дня")
day_scr["screen"] = "stats7"
asked_day["nav"] = 0
got = statnav.pick_day("tiktok", PKG2, day_snap, accstats._day_accept, "вчера", ver="36.7.4", log=[])
say(got and asked_day["nav"] == 0 and "запомненному" in got["how"], "второй раз — по памяти, без модели")
say(not accstats._day_accept("18 нояб - 24 нояб") and accstats._day_accept("Yesterday"),
    "«за 7 дней» за день не принимается, «Yesterday» — да")

print("ВСЁ ХОРОШО" if ok else "ЕСТЬ ОШИБКИ")
sys.exit(0 if ok else 1)
