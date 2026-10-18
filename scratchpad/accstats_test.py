"""Аналитика аккаунтов «как в YouTube Studio» из всех трёх сетей (2026-10-17).

Без телефона: экраны профилей собраны из узлов так, как их рисуют TikTok,
Instagram и YouTube; телефон и модель подменены. Живые селекторы — проверить,
когда телефон подключат.
"""
import datetime as dt
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402

tmp = tempfile.mkdtemp(prefix="pa_accstats_")
config.BASE = tmp
config.DB_PATH = os.path.join(tmp, "jobs.db")
config.SERIAL = None

import accstats  # noqa: E402
import device    # noqa: E402
import human     # noqa: E402
import jobs      # noqa: E402
import ui        # noqa: E402

jobs._SCHEMA_READY = False
ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


class N:
    def __init__(self, text="", desc="", b=(0, 0, 0, 0), clickable=False, rid=""):
        self.text, self.desc, self.bounds, self.clickable, self.rid = text, desc, b, clickable, rid
        self.enabled, self.selected = True, False

    def __repr__(self):
        return f"N({self.text or self.desc})"


print("--- TikTok: число над подписью, просмотры на плитках ---")
tiktok = [
    N("120", b=(100, 600, 260, 660)), N("Подписки", b=(100, 670, 260, 710)),
    N("12,5 тыс.", b=(400, 600, 640, 660)), N("Подписчики", b=(400, 670, 640, 710)),
    N("3 400", b=(760, 600, 960, 660)), N("Лайки", b=(760, 670, 960, 710)),
    N("Изменить профиль", b=(100, 760, 980, 840)),
    N("1 234", b=(20, 1500, 200, 1540)), N("980", b=(380, 1500, 560, 1540)),
    N("15.3K", b=(740, 1500, 920, 1540)), N("77", b=(20, 2050, 200, 2090)),
    N("Профиль", desc="Профиль", b=(860, 2300, 1080, 2400), clickable=True),
]
got = accstats.parse_profile(tiktok, 2400)
say(got["followers"] == 12500, f"подписчики 12,5 тыс. -> {got['followers']} (не «Подписки» и не лайки)")
say([v["views"] for v in got["videos"]] == [1234, 980, 15300, 77],
    f"просмотры по порядку плиток: {[v['views'] for v in got['videos']]}")

print("\n--- TikTok живьём 2026-10-17: видео нет, есть фраза и значок «Входящие» ---")
empty = [N("2", b=(159, 803, 412, 864)), N("Подписки", b=(159, 871, 412, 915)),
         N("2", b=(525, 803, 554, 864)), N("Подписчика", b=(413, 871, 666, 915)),
         N("0", b=(667, 803, 920, 864)), N("Лайки", b=(667, 871, 920, 915)),
         N("Изменить профиль", b=(195, 948, 619, 1069)),
         N("За последние 30 дней ваш профиль просмотрел 1 человек. Поделитесь", b=(206, 1594, 874, 1838)),
         N("Загрузить", b=(409, 1871, 670, 1981)),
         N("Входящие", b=(648, 2135, 864, 2177)), N("5", b=(768, 2141, 816, 2177))]
got = accstats.parse_profile(empty, 2400)
say(got["followers"] == 2 and got["videos"] == [],
    f"подписчики 2, видео нет (не «30» из фразы и не «5» со значка): {got}")

print("\n--- YouTube: «7 подписчиков» в шапке, Shorts с названием ---")
youtube = [
    N("@Urugwayy · 7 подписчиков · 3 видео", b=(40, 500, 1040, 560)),
    N(desc="ывпвапавпавп, 2 просмотра", b=(20, 900, 520, 1600)),
    N(desc="ДМИТРИЙ ГОНДОН, 2 просмотра", b=(540, 900, 1060, 1600)),
    N(desc="ЕБЛя В уШи нАХуЙ !!! 666, 1 просмотр", b=(20, 1620, 520, 2300)),
]
got = accstats.parse_profile(youtube, 2400)
say(got["followers"] == 7, f"подписчики из строки шапки: {got['followers']}")
say([(v["title"][:12], v["views"]) for v in got["videos"]]
    == [("ывпвапавпавп", 2), ("ДМИТРИЙ ГОНД", 2), ("ЕБЛя В уШи н", 1)],
    f"Shorts: {[(v['title'][:12], v['views']) for v in got['videos']]}")

print("\n--- YouTube живьём 2026-10-17: название в кавычках, «2 просмотра» ---")
live_yt = [N("Urugwayy", b=(286, 526, 1036, 609)), N("7 подписчиков", b=(286, 675, 518, 719)),
           N("Аналитика", b=(44, 759, 529, 869), clickable=True),
           N(desc='Воспроизвести короткое видео "ывпвапавпавп, 2 просмотра"', b=(44, 1186, 457, 1805), clickable=True)]
got = accstats.parse_profile(live_yt, 2400)
say(got["followers"] == 7 and got["videos"] == [{"views": 2, "title": "ывпвапавпавп"}],
    f"подписчики и Shorts с названием из кавычек: {got}")
say(accstats._is_own(live_yt, accstats.NETS["shorts"]), "свой канал узнан по кнопке «Аналитика»")

print("\n--- Instagram: «▶ 5 678» на плитках Reels ---")
insta = [N("1 234", b=(420, 400, 600, 450)), N("подписчики", b=(420, 460, 600, 500)),
         N("▶ 5 678", b=(20, 1300, 300, 1340)), N("▶ 1,2 тыс.", b=(380, 1300, 660, 1340))]
got = accstats.parse_profile(insta, 2400)
say(got["followers"] == 1234 and [v["views"] for v in got["videos"]] == [5678, 1200],
    f"подписчики {got['followers']}, просмотры {[v['views'] for v in got['videos']]}")

print("\n--- сбор: только вкладки, приложение не закрывается ---")
taps, actions = [], []
screens = {"steps": [tiktok]}
device.open_app = lambda pkg: actions.append(("open", pkg))
device.wait_for_app = lambda pkg, timeout=25: True
device.go_home = lambda: actions.append("home")
device.stop_app = lambda pkg: actions.append(("STOP", pkg))
device.unlock = lambda: True
device.lock = lambda: actions.append("lock")
human.pause = lambda a, b=None: None
ui.wait_any = lambda sel, timeout=10: (ui.find_any(tiktok, sel), tiktok)
ui.find_any = lambda nodes, sel: next((n for n in nodes for s in sel
                                       if (s.get("desc") and s["desc"] == n.desc)
                                       or (s.get("text") and s["text"] == n.text)
                                       or (s.get("contains") and s["contains"] in (n.text + n.desc))), None)
ui.tap_node = lambda n: taps.append(n.text or n.desc)
ui.dump = lambda retries=3, tolerant=False, timeout=30: tiktok
ui.dismiss_popup = lambda nodes=None: False
accstats.adb.screen_size = lambda: (1080, 2400)
accstats.adb.tap = lambda x, y: taps.append((x, y))
got = accstats.collect("tiktok")
say(got["followers"] == 12500 and len(got["videos"]) == 4 and not got["why"], "TikTok снят")
say(taps == ["Профиль"], f"нажата только вкладка профиля: {taps}")
say("home" in actions and not any(a[0] == "STOP" for a in actions if isinstance(a, tuple)),
    "в конце — на рабочий стол, приложение не убито (может догружать видео)")

print("\n--- открылся чужой профиль (кнопка автора «Профиль iramsfoodstory») ---")
foreign = [N(desc="Профиль iramsfoodstory", b=(900, 1000, 1060, 1160), clickable=True),
           N("1,2 млн", b=(456, 640, 624, 701)), N("Подписчика", b=(413, 708, 666, 752)),
           N("Подписаться", b=(148, 785, 467, 906), clickable=True),
           N("Профиль", desc="Профиль", b=(860, 2300, 1080, 2400), clickable=True)]
taps.clear()
ui.dump = lambda retries=3, tolerant=False, timeout=30: foreign
got = accstats.collect("tiktok")
say(taps == ["Профиль"], f"нажата кнопка нижней панели, а не автора ролика: {taps}")
say(got["followers"] is None and "не мой профиль" in got["why"],
    f"чужой профиль — цифры не взяты: {got['why']}")

print("\n--- в дереве цифр нет — спрашиваем модель по снимку ---")
ui.dump = lambda retries=3, tolerant=False, timeout=30: [
    N("Профиль", desc="Профиль", b=(860, 2300, 1080, 2400), clickable=True),
    N("Изменить профиль", b=(100, 760, 980, 840), clickable=True)]
ui.wait_any = lambda sel, timeout=10: (ui.dump()[0], ui.dump())
accstats.adb.screenshot = lambda path: open(path, "wb").write(b"png")
import vision  # noqa: E402
vision.ask = lambda png, prompt, **k: 'Вот: {"подписчики": 321, "просмотры": [50, 40]}'
got = accstats.collect("tiktok")
say(got["followers"] == 321 and [v["views"] for v in got["videos"]] == [50, 40],
    "модель прочитала подписчиков и просмотры со снимка")

print("\n--- отчёт как в YouTube Studio: прибавка за сутки и 28 дней ---")
now = time.time()
accstats.save("tiktok", {"followers": 12000, "videos": [{"views": 900, "title": ""}, {"views": 500, "title": ""}]},
              at=now - 28 * 86400)
accstats.save("tiktok", {"followers": 12400, "videos": [{"views": 1000, "title": ""}, {"views": 600, "title": ""}]},
              at=now - 86400)
accstats.save("tiktok", {"followers": 12500, "videos": [{"views": 1234, "title": ""}, {"views": 980, "title": ""},
                                                       {"views": 15300, "title": ""}]}, at=now)
accstats.save("shorts", {"followers": 7, "videos": [{"views": 2, "title": "ывпвапавпавп"},
                                                   {"views": 2, "title": "ДМИТРИЙ ГОНДОН"}]}, at=now)
text = accstats.report_text(now)
print("    " + text.replace("\n", "\n    "))
say("Подписчики: 12 500 (+100), за 28 дней (+500)" in text, "подписчики TikTok: за сутки и за 28 дней")
say("Самое популярное: 15 300 · 1 234 · 980" in text, "самое популярное — по убыванию")
say("YouTube" in text and "«ывпвапавпавп» — 2 просмотра" in text, "у YouTube — новое видео с названием")
say(accstats.views_gain([5, 1234, 980], [1200, 970]) == (5 + 34 + 10, 1),
    f"новое видео встало первым — сдвиг найден: {accstats.views_gain([5, 1234, 980], [1200, 970])}")
say(accstats.views_gain([15300, 1234, 980], [1200, 970], hint=1) == (15300 + 34 + 10, 1),
    "вирусное новое видео: агент сам его публиковал — сдвиг по подсказке")
say(accstats.views_gain([1250, 990], [1234, 980]) == (26, 0), "без новых — разница по тем же видео")
say(accstats.views_gain([1200, 990], [1234, 980])[0] == 10, "«1,2 тыс.» округлило вниз — не «новое видео»")
say("За сутки во всех сетях:" in text, "заголовок — итог по всем сетям, как в Studio")

print("\n--- снимки экранов статистики с телефона ---")
config.QUEUE_DIR = os.path.join(tmp, "dev", "queue")
folder = accstats.shots_dir()
os.makedirs(folder, exist_ok=True)
for name in ("tiktok", "youtube", "instagram", "facebook"):
    open(os.path.join(folder, name + ".png"), "wb").write(b"png")
items, day = accstats.latest_shots()
say([c for _, c in items] == ["YouTube Studio — аналитика", "Instagram — статистика",
                              "Facebook — статистика (Meta Business Suite)", "TikTok Studio — аналитика"],
    f"альбом: YouTube, Instagram, Facebook, TikTok — {[c.split(' ')[0] for _, c in items]}")
say(day == dt.date.today().isoformat(), "снимки сегодняшние")
accstats._installed = lambda pkg: False
say(accstats._shot_facebook(folder, 1080, 2400) is None, "нет Meta Business Suite — Facebook пропущен")

print("\n--- когда снимать ---")
import telegram_bot  # noqa: E402
late = dt.datetime.combine(dt.date.today(), dt.time(21, 31))
with jobs.connect() as con:
    con.execute("DELETE FROM account_stats")
telegram_bot.load_settings = lambda: {"token": "", "owner": ""}
say(not accstats.due(late), "бот не настроен — не снимаем (показывать некому)")
telegram_bot.load_settings = lambda: {"token": "1:" + "A" * 35, "owner": "1"}
say(not accstats.due(late.replace(hour=20)), "до 21:30 — рано")
say(accstats.due(late), "21:31, сегодня не снимали — пора")
accstats.save("tiktok", {"followers": 1, "videos": []})
say(not accstats.due(late), "уже сняли сегодня — нет")
with jobs.connect() as con:
    con.execute("DELETE FROM account_stats")
jobs.log_event("account_stats", "попытка")
jobs.log_event("account_stats", "попытка")
say(not accstats.due(late), "две неудачные попытки — до завтра не дёргаем телефон")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
