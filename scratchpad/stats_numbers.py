"""Цифры со снимков статистики — текстом в чат (2026-10-29: «нужно ещё сделать,
чтобы бот дублировал цифрами в чате стату, которая на фото», и следом: «стату
проще просто instagram - 100 TikTok - 200 и тд без этого мусора»).

Без телефона: деревья TikTok Studio и Instagram — в точности такие, какими их
отдал Redmi 29.10; ответы DeepSeek по снимкам YouTube Studio — как живьём 30.10.
"""
import datetime as dt
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402

tmp = tempfile.mkdtemp(prefix="pa_numbers_")
config.BASE = tmp
config.DB_PATH = os.path.join(tmp, "jobs.db")
config.QUEUE_DIR = os.path.join(tmp, "queue")

import accstats  # noqa: E402
import vision    # noqa: E402
import telegram_bot as bot  # noqa: E402

ok = True
W, H = 1080, 2400


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


class N:
    def __init__(self, text, b, clickable=False):
        self.text, self.desc, self.bounds, self.clickable = text, text, b, clickable
        self.rid, self.enabled, self.selected, self.cls = "", True, False, "FlattenUIText"


print("--- TikTok Studio: «Просмотры публикаций» (живое дерево 29.10) ---")
TT = [N("Основные метрики", (66, 558, 532, 619)), N("21 окт - 27 окт", (66, 622, 582, 669)),
      N("1,889-468(-19.9%)", (66, 718, 529, 973), True),
      N("Просмотры публика‎…", (100, 744, 487, 802)), N("Просмотры профиля", (585, 744, 956, 802)),
      N("1,889", (100, 802, 495, 889)), N("11", (585, 802, 980, 889)),
      N("-468", (150, 895, 236, 942)), N("(-19.9%)", (241, 895, 379, 942)),
      N("Лайки", (100, 1021, 210, 1079)), N("101", (100, 1079, 495, 1166))]
say(accstats._tt_views(TT, H) == "1 889", f"просмотры публикаций: {accstats._tt_views(TT, H)!r}")
say(accstats._tt_views([N("Лайки", (100, 1021, 210, 1079)), N("101", (100, 1079, 495, 1166))], H) == "",
    "карточки просмотров нет — пусто, лайки за просмотры не выдаёт")

print("\n--- Instagram «Статистика · Обзор» (живое дерево 29.10) ---")
IG = [N("Статистика", (198, 141, 467, 200)), N("Весь контент", (44, 434, 351, 493)),
      N("Пользовательский диапазон", (463, 398, 981, 530), True),
      N("Просмотры", (88, 587, 273, 626)), N("113", (88, 660, 148, 719)),
      N("Чистый прирост подписчиков", (536, 587, 1007, 626)), N("--", (536, 660, 570, 719)),
      N("Просмотры по типу контента", (44, 1610, 630, 1663)),
      N("Зрители", (44, 1722, 1018, 1768)), N("107", (1018, 1718, 1036, 1771))]
say(accstats._ig_views(IG, H) == "113", f"просмотры: {accstats._ig_views(IG, H)!r}")

print("\n--- YouTube Studio: что прочёл DeepSeek (живые ответы 30.10) ---")
got = accstats._yt_views({"подсказка": "28 окт. 2026 г.: 17", "итог": "749"})
say(got == ("17", dt.date(2026, 10, 28)), f"за вчера — число из подсказки и её день: {got}")
got = accstats._yt_views({"подсказка": None, "итог": "256"})
say(got == ("256", None), f"подсказки нет (48 часов, месяц) — итог за период: {got}")
got = accstats._yt_views({"подсказка": None, "итог": 1234})
say(got == ("1234", None), f"итог числом, а не строкой: {got}")
got = accstats._yt_views({"подсказка": None, "итог": "--"})
say(got == ("", None), f"итога нет («--») — пусто: {got}")
got = accstats._yt_views({"подсказка": "сейчас: много", "итог": "около сотни"})
say(got == ("", None), f"ответ не похож на дату и число — пусто, а не выдумка: {got}")
say(accstats._yt_views({}) == ("", None), "прочесть нечем — пусто, без падения")

answers = iter(['```json\n{"подсказка": null, "итог": "129"}\n```', "не знаю", "{оборвано"])
asked = []


def fake_ask(png, prompt, **kw):
    asked.append(kw.get("shrink"))
    return next(answers)


vision.ask = fake_ask
png = os.path.join(tmp, "yt.png")
open(png, "wb").write(b"\x89PNG" + b"0" * 100)
got = accstats._yt_read(png)
say(got == {"подсказка": None, "итог": "129"} and asked == [accstats.YT_SHRINK],
    f"JSON в обёртке ```json — разобран: {got}")
say(accstats._yt_read(png) == {}, "ответ без JSON — {}")
say(accstats._yt_read(png) == {}, "оборванный JSON — {}")


def broken(png, prompt, **kw):
    raise vision.VisionError("не вписан ключ сервиса")


vision.ask = broken
say(accstats._yt_read(png) == {}, "зрения нет — {}, сбор не падает")
say(accstats._yt_read(os.path.join(tmp, "нет.png")) == {}, "снимка нет — {}")

print("\n--- одно сообщение: «Instagram — 113» ---")


def folder_with(views):
    path = tempfile.mkdtemp(dir=tmp)
    for name, (number, when) in views.items():
        accstats._views_note(path, name, number, when)
    return path


day = folder_with({"instagram": ("113", "28.10"), "tiktok": ("286", "27.10"), "youtube": ("60", "28.10")})
text = accstats.numbers_text(day)
say(text == "Instagram — 113\nTikTok — 286 (за 27.10)\nYouTube — 60",
    f"за вчера: период в скобках только у TikTok\n{text}")
week = folder_with({"instagram": ("773", "19.10–25.10")})
say(accstats.numbers_text(week) == "Instagram — 773", "неделя: у YouTube числа нет — строки нет")
usual = folder_with({"instagram": ("950", ""), "tiktok": ("1 889", "21.10–27.10"), "youtube": ("733", "28 дней")})
text = accstats.numbers_text(usual)
say(text == "Instagram — 950\nTikTok — 1 889 (за 21.10–27.10)\nYouTube — 733 (за 28 дней)",
    f"«как обычно»: периоды у всех разные — у каждой сети свой\n{text}")
say(accstats.numbers_text(day, ["tiktok"]) == "TikTok — 286", "TikTok отдельным снимком — без скобок")
# 2026-10-30: «бот почему-то TikTok не присылает стату» — сеть не снялась, а
# бот молчал о ней. Теперь причина — строкой.
holes = folder_with({"instagram": ("4 869", "29.10"), "youtube": ("20", "29.10")})
accstats._note(holes, "tiktok_why", "в меню профиля нет TikTok Studio")
text = accstats.numbers_text(holes)
say(text == "Instagram — 4 869\nYouTube — 20\nTikTok — не снял: в меню профиля нет TikTok Studio",
    f"не снялась сеть — причина строкой, а не тишина\n{text}")
say(accstats.numbers_text(holes, ["reels"]) == "Instagram — 4 869", "чужие сети в отчёт не лезут")

print("\n--- второй телефон 30.10: английская «Аналитика» TikTok, Instagram за 30 дней ---")
ru = [N("7 дн.", (66, 280, 220, 340), True), N("28 дн.", (230, 280, 400, 340), True),
      N("Другое", (800, 280, 1000, 340), True)]
say(accstats._tt_analytics_open(ru, H), "Redmi: «7 дн.» и «Другое» — аналитика открыта")
en = [N("Overview", (40, 250, 260, 320), True), N("Last 7 days", (40, 340, 400, 400), True)]
say(accstats._tt_analytics_open(en, H), "английская: «Last 7 days» — аналитика открыта")
studio_home = [N("TikTok Studio", (300, 120, 780, 220)), N("Analytics", (60, 280, 400, 340)),
               N("Post views", (60, 390, 330, 430)), N("3,846", (60, 440, 330, 500)),
               N("434%", (100, 520, 230, 560)), N("7d", (240, 520, 300, 560))]
say(not accstats._tt_analytics_open(studio_home, H),
    "главная TikTok Studio («Analytics», «7d») за аналитику не принимается")
say(accstats._tt_views(studio_home, H, top=0.08) == "3 846" and accstats._tt_views(studio_home, H) == "",
    "просмотры с главной TikTok Studio читаются только там, где их ждут")
ig30 = folder_with({"instagram": ("79 935", "30 дней, а не вчера: в календаре не выбрались даты"),
                    "youtube": ("20", "29.10"), "tiktok": ("3 846", "7 дней — аналитика не открылась")})
text = accstats.numbers_text(ig30)
say(text == "Instagram — 79 935 (за 30 дней, а не вчера: в календаре не выбрались даты)\n"
            "TikTok — 3 846 (за 7 дней — аналитика не открылась)\nYouTube — 20 (за 29.10)",
    f"чужой период — подписан, а не выдан за вчера\n{text}")
for label, want in (("30 days", "30 дней"), ("Last 30 days", "30 дней"), ("Custom", "свой период"),
                    ("Пользовательский диапазон", "свой период")):
    accstats.ui.dump = lambda **k: [N(label, (500, 400, 1000, 480), True)]
    say(accstats._ig_shown(W, H) == want, f"период Instagram «{label}» -> «{accstats._ig_shown(W, H)}»")

print("\n--- бот шлёт просмотры следом за альбомом ---")
sent = []
for name in ("youtube", "instagram", "tiktok"):
    open(os.path.join(day, name + ".png"), "wb").write(b"\x89PNG" + b"0" * 100)
bot.load_settings = lambda: {"token": "1:" + "A" * 35, "owner": "111"}
bot.destinations = lambda role: []
bot.send_album = lambda pics, chat_id=None, thread=None: sent.append(("album", len(pics), chat_id)) or True
bot.send = lambda text, preview=True, chat_id=None, keyboard=False, thread=None: sent.append(("text", text, chat_id)) or True
bot.send_report(accstats.shots_in(day), "📊 Статистика за вчера, 28.10", accstats.numbers_text(day))
say([s[0] for s in sent] == ["album", "text"]
    and sent[1][1] == "📊 Статистика за вчера, 28.10\nInstagram — 113\nTikTok — 286 (за 27.10)\nYouTube — 60"
    and sent[1][2] == "111", f"отчёт: альбом, потом\n{sent[1][1] if len(sent) > 1 else ''}")
sent.clear()
accstats.latest_shots = lambda: (accstats.shots_in(day), dt.date.today().isoformat())
bot.send_shots(fresh=True)
say([s[0] for s in sent] == ["album", "text"] and sent[1][1].startswith("📊 Просмотры\nInstagram — 113"),
    "/stats: альбом, потом «📊 Просмотры»")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
