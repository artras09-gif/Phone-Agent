"""Цифры со снимков статистики — текстом в чат (2026-10-29: «нужно ещё сделать,
чтобы бот дублировал цифрами в чате стату, которая на фото»).

Без телефона: деревья TikTok Studio и Instagram и строки распознавания
YouTube Studio — в точности такие, какими их отдали Redmi и Windows 29.10.
"""
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


print("--- TikTok Studio: карточки «Основных метрик» (живое дерево 29.10) ---")
TT = [N("Основные метрики", (66, 558, 532, 619)), N("21 окт - 27 окт", (66, 622, 582, 669)),
      N("Выбор подписчиков", (0, 614, 364, 664)),
      N("1,889-468(-19.9%)", (66, 718, 529, 973), True),
      N("Просмотры публика‎…", (100, 744, 487, 802)), N("Просмотры профиля", (585, 744, 956, 802)),
      N("1,889", (100, 802, 495, 889)), N("11", (585, 802, 980, 889)),
      N("-468", (150, 895, 236, 942)), N("(-19.9%)", (241, 895, 379, 942)),
      N("+5", (635, 895, 681, 942)), N("(+83.3%)", (686, 895, 851, 942)),
      N("Лайки", (100, 1021, 210, 1079)), N("Комментарии", (585, 1021, 831, 1079)),
      N("101", (100, 1079, 495, 1166)), N("3", (585, 1079, 980, 1166)),
      N("+6", (150, 1171, 196, 1218)), N("(+6.3%)", (201, 1171, 341, 1218)), N("+3", (635, 1171, 682, 1218)),
      N("Репосты", (100, 1297, 250, 1355)), N("Ожид. награды", (585, 1297, 855, 1355)),
      N("6", (100, 1355, 495, 1442)), N("$0.00", (585, 1355, 980, 1442)),
      N("+2", (150, 1448, 196, 1495)), N("(+50%)", (201, 1448, 332, 1495))]
got = accstats._tt_numbers(TT, H)
want = ("просмотры публикаций 1 889 (−468, −19.9%), просмотры профиля 11 (+5, +83.3%), "
        "лайки 101 (+6, +6.3%), комментарии 3 (+3), репосты 6 (+2, +50%), ожидаемые награды $0.00")
say(got == want, f"\n      {got}")

print("\n--- Instagram «Статистика · Обзор» (живое дерево 29.10, за 28.10) ---")
IG = [N("Статистика", (198, 141, 467, 200)), N("Обзор", (125, 285, 235, 331)),
      N("Весь контент", (44, 434, 351, 493)), N("Пользовательский диапазон", (463, 398, 981, 530), True),
      N("Просмотры", (88, 587, 273, 626)), N("113", (88, 660, 148, 719)),
      N("Чистый прирост подписчиков", (536, 587, 1007, 626)), N("--", (536, 660, 570, 719)),
      N("0.0% подписчики", (44, 796, 366, 842)), N("100.0% неподписчики", (44, 842, 448, 888)),
      N("2", (56, 1018, 75, 1057)), N("1", (61, 1188, 75, 1227)), N("0", (55, 1358, 75, 1397)),
      N("28 окт.", (108, 1400, 1025, 1439)), N("Просмотры по типу контента", (44, 1610, 630, 1663)),
      N("Зрители", (44, 1722, 1018, 1768)), N("107", (1018, 1718, 1036, 1771)),
      N("Подписчики", (78, 1851, 269, 1890)), N("Неподписчики", (369, 1851, 597, 1890)),
      N("Reels", (44, 1934, 129, 1944)), N("113", (893, 1990, 1025, 2029)),
      N("Публикации", (44, 2073, 234, 2112)), N("0", (893, 2129, 1025, 2168)),
      N("Истории", (0, 0, 0, 0)), N("0", (0, 0, 0, 0))]
got = accstats._ig_numbers(IG, W, H)
want = ("просмотры 113, чистый прирост подписчиков --, 0.0% подписчики, 100.0% неподписчики, "
        "зрители 107, Reels 113, публикации 0")
say(got == want, f"\n      {got}")

print("\n--- YouTube Studio: распознанный снимок «Просмотров» (Windows OCR 29.10) ---")
YT = [("Просмотры", (157, 141, 457, 191)), ("90 дн", (454, 300, 561, 335)), ("Окт.", (900, 300, 974, 329)),
      ("150", (46, 453, 99, 478)), ("27 окт. 2026 г.: 60", (762, 664, 1037, 689)), ("50", (46, 755, 80, 780)),
      ("30 сент.", (135, 952, 256, 977)), ("27 окт.", (929, 952, 1033, 977)),
      ("Просмотры", (101, 1090, 341, 1131)), ("733", (962, 1090, 1034, 1123)),
      ("Самый популярный контент", (46, 1325, 711, 1374)), ("бе...", (854, 1456, 920, 1487)),
      ("7 трендовых звуков для монтажа", (166, 1458, 782, 1495)), ("167", (973, 1458, 1035, 1487)),
      ("Топ-4 выражения для моушена — магия...", (166, 1623, 922, 1660)), ("109", (973, 1623, 1034, 1652)),
      ("Жидкие капли (Liquid BIob) в After Effect...", (165, 1784, 927, 1825)), ("97", (993, 1788, 1035, 1817)),
      ("Главная", (58, 2225, 161, 2246)), ("Монетиза...", (901, 2225, 1045, 2246))]
got = accstats._yt_numbers(YT, W, H, "за 28 дней")
say(got.startswith("27.10 — 60 просмотров; за 28 дней — 733; лучшие: «7 трендовых звуков для монтажа» 167 · ")
    and "«Топ-4 выражения для моушена — магия» 109" in got and "Главная" not in got, f"\n      {got}")
say(accstats._yt_numbers([("Просмотры", (157, 141, 457, 191)), ("Самый популярный контент", (46, 300, 711, 349)),
                          ("Delay & Offset — создаем Эффект домин...", (166, 1350, 900, 1390)),
                          ("49", (993, 1352, 1035, 1385))], W, H, "за 7 дней")
    == "лучшие: «Delay & Offset — создаем Эффект домин» 49", "7 дней: графика нет, остаётся список")
say(accstats._yt_numbers(None, W, H) == "", "распознать нечем — пусто, без падения")

print("\n--- одно сообщение на все сети ---")
folder = os.path.join(tmp, "f")
for key, value in {"youtube_numbers": "27.10 — 60 просмотров", "youtube_account": "Ease.motion",
                   "instagram_numbers": "за 28.10: просмотры 113", "instagram_account": "ontwos.studio",
                   "tiktok_numbers": "за 27.10: лайки 9", "tiktok_tiles": "видно 3 видео, просмотров на них 2 614"
                   }.items():
    accstats._note(folder, key, value)
text = accstats.numbers_text(folder)
say(text == "YouTube (Ease.motion): 27.10 — 60 просмотров\nInstagram (ontwos.studio): за 28.10: просмотры 113\n"
            "TikTok: за 27.10: лайки 9; плитки профиля: видно 3 видео, просмотров на них 2 614", f"\n{text}")
say(accstats.numbers_text(folder, ["tiktok"]).startswith("TikTok"), "только TikTok — для его отдельного снимка")

print("\n--- бот шлёт цифры следом за альбомом ---")
sent = []
for name in ("youtube", "instagram", "tiktok"):
    open(os.path.join(folder, name + ".png"), "wb").write(b"\x89PNG" + b"0" * 100)
bot.load_settings = lambda: {"token": "1:" + "A" * 35, "owner": "111"}
bot.destinations = lambda role: []
bot.send_album = lambda pics, chat_id=None, thread=None: sent.append(("album", len(pics), chat_id)) or True
bot.send = lambda text, preview=True, chat_id=None, keyboard=False, thread=None: sent.append(("text", text, chat_id)) or True
bot.send_report(accstats.shots_in(folder), "📊 Статистика за вчера, 28.10", accstats.numbers_text(folder))
say([s[0] for s in sent] == ["album", "text"] and sent[1][1].startswith("📊 Статистика за вчера, 28.10 — цифрами\n\nYouTube")
    and sent[1][2] == "111", f"отчёт: альбом, потом цифры — {sent[1][1][:50]!r}")
sent.clear()
accstats.latest_shots = lambda: (accstats.shots_in(folder), __import__("datetime").date.today().isoformat())
bot.send_shots(fresh=True)
say([s[0] for s in sent] == ["album", "text"] and sent[1][1].startswith("📊 Цифрами\n\nYouTube"),
    "/stats: альбом, потом цифры")

print("\n--- распознавание Windows на этом ПК ---")
png = os.path.join(tmp, "white.png")
import struct  # noqa: E402
import zlib    # noqa: E402

raw = b"".join(b"\x00" + b"\xff\xff\xff" * 64 for _ in range(32))
chunk = lambda kind, data: struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
open(png, "wb").write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 32, 8, 2, 0, 0, 0))
                      + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
got = ocr.lines(png)
say(got == [] if os.name == "nt" else got is None, f"пустая картинка — пустой список, без сбоя: {got!r}")
say(ocr.lines(os.path.join(tmp, "нет.png")) is None, "файла нет — None")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
