"""Цифры со снимков статистики — текстом в чат (2026-10-29: «нужно ещё сделать,
чтобы бот дублировал цифрами в чате стату, которая на фото», и следом: «стату
проще просто instagram - 100 TikTok - 200 и тд без этого мусора»).

Без телефона: деревья TikTok Studio и Instagram и строки распознавания
YouTube Studio — в точности такие, какими их отдали Redmi и Windows 29.10.
"""
import datetime as dt
import os
import struct
import sys
import tempfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402

tmp = tempfile.mkdtemp(prefix="pa_numbers_")
config.BASE = tmp
config.DB_PATH = os.path.join(tmp, "jobs.db")
config.QUEUE_DIR = os.path.join(tmp, "queue")

import accstats  # noqa: E402
import ocr       # noqa: E402
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

print("\n--- YouTube Studio: распознанный снимок «Просмотров» (Windows OCR 29.10) ---")
YT = [("Просмотры", (157, 141, 457, 191)), ("150", (46, 453, 99, 478)),
      ("27 окт. 2026 г.: 60", (762, 664, 1037, 689)), ("27 окт.", (929, 952, 1033, 977)),
      ("Просмотры", (101, 1090, 341, 1131)), ("733", (962, 1090, 1034, 1123)),
      ("Самый популярный контент", (46, 1325, 711, 1374)),
      ("7 трендовых звуков для монтажа", (166, 1458, 782, 1495)), ("167", (973, 1458, 1035, 1487))]
got = accstats._yt_views(YT, W, H)
say(got == ("60", dt.date(2026, 10, 27)), f"за вчера — число из подсказки и её день: {got}")
got = accstats._yt_views([line for line in YT if ":" not in line[0]], W, H)
say(got == ("733", None), f"подсказки нет (месяц) — итог за период: {got}")
got = accstats._yt_views([("Просмотры", (157, 141, 457, 191)), ("Самый популярный контент", (46, 300, 711, 349)),
                          ("Delay & Offset", (166, 1350, 900, 1390)), ("49", (993, 1352, 1035, 1385))], W, H)
say(got == ("", None), "7 дней: ни подсказки, ни итога — пусто, а не число из списка видео")
say(accstats._yt_views(None, W, H) == ("", None), "распознать нечем — пусто, без падения")

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

print("\n--- распознавание Windows на этом ПК ---")
png = os.path.join(tmp, "white.png")
raw = b"".join(b"\x00" + b"\xff\xff\xff" * 64 for _ in range(32))


def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


open(png, "wb").write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 32, 8, 2, 0, 0, 0))
                      + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
got = ocr.lines(png)
say(got == [] if os.name == "nt" else got is None, f"пустая картинка — пустой список, без сбоя: {got!r}")
say(ocr.lines(os.path.join(tmp, "нет.png")) is None, "файла нет — None")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
