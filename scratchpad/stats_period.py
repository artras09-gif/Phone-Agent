"""Статистика за 1 или 2 дня (2026-10-22: «надо выбор, чтобы он мог показать
стату за один день или за два дня»; «статистику он не собирает с тиктока»).

Без телефона: календари TikTok и Instagram собраны из узлов в точности так,
как их отдал живой Redmi 22.10.2026 (1080x2400); сегодня подменено на 22.10.
"""
import datetime as dt
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402

tmp = tempfile.mkdtemp(prefix="pa_period_")
config.BASE = tmp
config.DB_PATH = os.path.join(tmp, "jobs.db")
config.QUEUE_DIR = os.path.join(tmp, "queue")
config.SERIAL = None

import accstats  # noqa: E402
import adb       # noqa: E402
import device    # noqa: E402
import human     # noqa: E402
import telegram_bot as bot  # noqa: E402
import ui        # noqa: E402

ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


class N:
    def __init__(self, text="", desc="", b=(0, 0, 0, 0), clickable=False, cls="android.view.ViewGroup"):
        self.text, self.desc, self.bounds, self.clickable, self.cls = text, desc, b, clickable, cls
        self.rid, self.enabled, self.selected = "", True, False

    def __repr__(self):
        return f"N({self.text or self.desc}{self.bounds})"


class Today(dt.date):
    @classmethod
    def today(cls):
        return cls(2026, 10, 22)                      # четверг


accstats.dt = types.SimpleNamespace(date=Today, timedelta=dt.timedelta,
                                    datetime=dt.datetime, time=dt.time)
human.pause = lambda *a, **k: None
accstats.time.sleep = lambda s: None
taps = []
ui.tap_node = lambda n: taps.append(n) or accstats._center(n)
adb.tap = lambda x, y: taps.append((x, y))
backs = []
device.back = lambda: backs.append(1)
FRONT = ["com.zhiliaoapp.musically"]
adb.current_app = lambda: (FRONT[0], "")
W, H = 1080, 2400

# ------------------------------------------------------- календарь TikTok
# Снято живьём: конец сентября (13–30) и октябрь целиком, неделя с
# воскресенья; 22-е (сегодня) обведено — клетка 116, остальные 110.
ROWS = {903: 7, 1039: 7, 1176: 4, 1429: 3, 1567: 7, 1704: 7, 1842: 7, 1979: 7}
XS = [22, 176, 331, 485, 639, 794, 948]


def tt_calendar(today_ring=True):
    nodes = [N(b=(0, 903, 1080, 1706), cls="androidx.recyclerview.widget.RecyclerView"),
             N(b=(970, 686, 1036, 752), cls="android.widget.ImageView")]       # крестик
    for y, count in ROWS.items():
        xs = XS[-count:] if y == 1429 else XS[:count]                          # 1 окт. — четверг
        for x in xs:
            nodes.append(N(b=(x - 22, y, x + 132, y + 110)))                   # внешняя клетка
            if y == 1842 and x == 639 and today_ring:
                nodes.append(N(b=(636, 1839, 752, 1955)))                      # 22 окт., кольцо
            else:
                nodes.append(N(b=(x, y, x + 110, y + 110)))
    nodes.append(N(desc="Обновить", b=(44, 2150, 1036, 2177)))
    return nodes


def day_of(node):
    """Какое число у клетки календаря TikTok (по разметке выше)."""
    cells, today = accstats._tt_cells(W)
    i = [c.bounds for c in cells].index(node.bounds)
    return Today.today() - dt.timedelta(days=today - i)


print("--- TikTok: клетки календаря -> даты ---")
ui.dump = lambda *a, **k: tt_calendar()
cells, today = accstats._tt_cells(W)
say(len(cells) == 49 and today == 18 + 21, f"49 клеток, сегодня — №{today} (18 сентябрьских + 21)")
say(day_of(cells[0]) == dt.date(2026, 9, 13) and day_of(cells[-1]) == dt.date(2026, 10, 31),
    "ряд дат сплошной: 13 сентября … 31 октября")
ui.dump = lambda *a, **k: tt_calendar(today_ring=False)
say(accstats._tt_cells(W) == ([], None), "нет обведённой клетки — календарь не трогаем")

print("\n--- TikTok: за 1 и за 2 дня ---")
ui.dump = lambda *a, **k: tt_calendar()
band = []
accstats._wide_band = lambda test, low=0.7: band.append(1) or 2313
taps.clear()
say(accstats._tt_pick_range(1, W), "1 день выбран")
say([day_of(n) for n in taps[:2]] == [dt.date(2026, 10, 21)] * 2 and taps[2] == (540, 2313),
    "дважды 21 окт. (TikTok покажет 20-е — последний полный день), потом «Обновить» по цвету")
taps.clear()
accstats._tt_pick_range(2, W)
say([day_of(n) for n in taps[:2]] == [dt.date(2026, 10, 20), dt.date(2026, 10, 21)],
    "2 дня: 20 и 21 окт.")
taps.clear()
lit = iter([None, 2313])
accstats._wide_band = lambda test, low=0.7: next(lit)
say(accstats._tt_pick_range(1, W) and [day_of(n) for n in taps if not isinstance(n, tuple)]
    == [dt.date(2026, 10, 21)] * 2 + [dt.date(2026, 10, 20)] * 2,
    "вчера не выбирается («Обновить» не загорелась) — берёт позавчера")
taps.clear()
accstats._wide_band = lambda test, low=0.7: None
say(not accstats._tt_pick_range(1, W) and not any(isinstance(t, tuple) for t in taps),
    "кнопка так и не загорелась — «Обновить» не жмёт, сдаётся")
taps.clear()
accstats._wide_band = lambda test, low=0.7: 2313
FRONT[0] = "com.google.android.youtube"
say(not accstats._tt_pick_range(1, W) and not any(isinstance(t, tuple) for t in taps),
    "на экране не TikTok — по цвету не жмёт (красная полоса могла быть чужой)")
FRONT[0] = "com.zhiliaoapp.musically"

print("\n--- «Обновить» ищется по цвету на снимке ---")
importlib = __import__("importlib")
importlib.reload(accstats)
accstats.dt = types.SimpleNamespace(date=Today, timedelta=dt.timedelta,
                                    datetime=dt.datetime, time=dt.time)


def screen(button_rgb, top=2250, bottom=2370, w=120, h=240, scale=10):
    """Экран w×h (в 10 раз меньше настоящего), кнопка во всю ширину внизу."""
    px = bytearray(b"\xff\xff\xff\xff" * w * h)
    for y in range(top // scale, bottom // scale):
        for x in range(w * 4 // 100, w * 96 // 100):
            px[(y * w + x) * 4:(y * w + x) * 4 + 3] = bytes(button_rgb)
    for y in range(1460 // scale, 1540 // scale):                 # выбранный день — красный кружок
        for x in range(30, 42):
            px[(y * w + x) * 4:(y * w + x) * 4 + 3] = bytes((254, 44, 85))
    head = (w).to_bytes(4, "little") + (h).to_bytes(4, "little") + (1).to_bytes(4, "little") \
        + (0).to_bytes(4, "little")
    return head + bytes(px)


adb.exec_out = lambda cmd, timeout=60: screen((254, 44, 85))
y = accstats._wide_band(accstats._tiktok_red)
say(y is not None and 225 <= y <= 237, f"горящая красная кнопка найдена: y={y} (в кадре 240 строк)")
adb.exec_out = lambda cmd, timeout=60: screen((255, 170, 187))
say(accstats._wide_band(accstats._tiktok_red) is None, "бледно-розовая (дата не выбрана) — не кнопка")
adb.exec_out = lambda cmd, timeout=60: b"\x00" * 8
say(accstats._wide_band(accstats._tiktok_red) is None, "снимок не разобрался — None, без падения")

print("\n--- период под «Основные метрики» ---")
say(accstats._tt_range_text([N("Основные метрики14 окт - 20 окт", b=(22, 503, 1058, 2136)),
                             N("19 окт - 19 окт", b=(66, 622, 582, 669))], H) == "19 окт",
    "«19 окт - 19 окт» -> «19 окт»")
say(accstats._tt_range_text([N("Oct 14 - Oct 20", b=(66, 622, 582, 669))], H) == "Oct 14 - Oct 20",
    "по-английски: «Oct 14 - Oct 20»")
say(accstats._tt_range_text([N("2,357+2,357", b=(66, 718, 529, 973))], H) == "", "цифры метрик — не период")

print("\n--- ☰ профиля TikTok: значок, а не «Просмотры профиля» ---")
top = [N(b=(827, 110, 1058, 220), cls="android.widget.LinearLayout"),
       N(b=(827, 110, 937, 220), clickable=True, cls="android.widget.FrameLayout"),
       N(desc="Просмотры профиля", b=(849, 132, 915, 198), cls="android.widget.Button"),
       N("6", b=(900, 159, 931, 198), cls="android.widget.TextView"),
       N(b=(937, 110, 1058, 220), cls="android.widget.ImageView"),
       N("Ease.motion", b=(369, 134, 689, 195), clickable=True)]
icon = accstats._menu_icon(top, W, H)
say(icon is not None and icon.bounds == (937, 110, 1058, 220),
    f"крайний правый значок, хоть и «не нажимаемый»: {icon}")
say(accstats._menu_icon([N(b=(937, 110, 1058, 220), clickable=True)], W, H).clickable,
    "нажимаемый ☰ (как было раньше) — тоже находится")

# ------------------------------------------------------- календарь Instagram
print("\n--- Instagram: календарь с числами ---")


def ig_calendar():
    """Живая разметка: «пн 31» (31 авг. под шапкой), сентябрь, октябрь;
    с 21 октября дни серые — без нажимаемой клетки."""
    nodes = [N("Период времени", b=(87, 300, 474, 359))]
    nodes += [N(d, b=(x, 403, x + 135, 442)) for d, x in
              zip(["пн", "вт", "ср", "чт", "пт", "сб", "вс"], [65, 200, 335, 471, 607, 743, 879])]
    xs = [67, 202, 338, 474, 610, 746, 882]
    nodes += [N(b=(67, 386, 199, 518), clickable=True), N("31", b=(114, 442, 152, 475))]

    def month(header_y, first_col, ndays, top, live_to):
        out = [N("сентября 2026" if header_y < 1000 else "октября 2026", b=(87, header_y, 405, header_y + 59))]
        col, y = first_col, top
        for d in range(1, ndays + 1):
            x = xs[col]
            if d <= live_to:
                out.append(N(b=(x, y, x + 132, y + 121), clickable=True))
            out.append(N(str(d), b=(x + 45, y + 43, x + 85, y + 89)))
            col += 1
            if col == 7:
                col, y = 0, y + 121
        return out

    nodes += month(556, 1, 30, 632, 30)              # 1 сент. — вторник
    nodes += month(1286, 3, 31, 1362, 20)            # 1 окт. — четверг
    nodes += [N("Отмена", b=(588, 1973, 723, 2105), clickable=True),
              N("Применить", b=(789, 1973, 993, 2105), clickable=True)]
    return nodes


def ig_day(node):
    seq = accstats._ig_days(ig_calendar(), W)
    for i, (d, cell) in enumerate(seq):
        if cell is not None and cell.bounds == node.bounds:
            now = max(j for j, (dd, _) in enumerate(seq) if dd == 22)
            return Today.today() - dt.timedelta(days=now - i)
    return None


ui.dump = lambda *a, **k: ig_calendar()
seq = accstats._ig_days(ig_calendar(), W)
say([d for d, _ in seq][:3] == [31, 1, 2] and len(seq) == 62, "ряд: 31 авг., сентябрь, октябрь — 62 числа")
taps.clear()
got = accstats._ig_pick_range(1, W)
say(got == (dt.date(2026, 10, 20), dt.date(2026, 10, 20)) and [ig_day(n) for n in taps] == [dt.date(2026, 10, 20)],
    f"1 день: одно касание по 20 окт. — последнему доступному: {got}")
taps.clear()
got = accstats._ig_pick_range(2, W)
say(got == (dt.date(2026, 10, 19), dt.date(2026, 10, 20))
    and [ig_day(n) for n in taps] == [dt.date(2026, 10, 19), dt.date(2026, 10, 20)],
    "2 дня: 19 и 20 окт.")
say(accstats._span(*got) == "19.10–20.10" and accstats._span(got[1], got[1]) == "20.10", "подписи дат")
broken = [n for n in ig_calendar() if n.text != "20"]
ui.dump = lambda *a, **k: broken
taps.clear()
say(accstats._ig_pick_range(1, W) is None and not taps, "ряд дат с дырой — ничего не нажимает")

print("\n--- Instagram: выпадашка периода ---")
insights = [N("Статистика", b=(198, 141, 467, 200)), N("Весь контент", b=(44, 434, 351, 493)),
            N("Пользовательский диапазон", b=(463, 398, 981, 530), clickable=True)]
menu = [N("Период времени", b=(321, 548, 981, 587))]
for i, label in enumerate(["7 дней", "14 дней", "30 дней", "60 дней", "90 дней",
                           "Пользовательский диапазон"]):
    menu += [N(b=(277, 626 + 154 * i, 1025, 780 + 154 * i), clickable=True),
             N(label, b=(332, 677 + 154 * i, 600, 730 + 154 * i))]
screens = iter([insights, menu])
ui.dump = lambda *a, **k: next(screens)
taps.clear()
say(accstats._ig_period(None, W, H) == "последние 30 дней"
    and [n.bounds for n in taps] == [(463, 398, 981, 530), (277, 934, 1025, 1088)],
    "обычный снимок после «своего диапазона» — возвращает «30 дней» (Instagram помнит выбор)")
screens = iter([[N("Статистика", b=(198, 141, 467, 200)),
                 N("30 дней", b=(839, 398, 981, 530), clickable=True)]])
taps.clear()
say(accstats._ig_period(None, W, H) == "последние 30 дней" and not taps, "уже 30 дней — ничего не трогает")

print("\n--- бот: /stats 1, /stats 2, кнопки ---")
for text, want in [("/stats", None), ("/stats 1", 1), ("/stats 2", 2), ("/stats 7", 0),
                   ("статистика за день", 1), ("статистика за два дня", 2), ("/stats обычно", 0),
                   ("/stats за 2 дня", 2), ("статистика", None)]:
    say(bot.stats_days(text) == want, f"{text!r} -> {bot.stats_days(text)!r}")

print("\n--- подписи альбома с периодом ---")
folder = accstats.shots_dir()
os.makedirs(folder, exist_ok=True)
for name in ("youtube", "instagram", "tiktok"):
    open(os.path.join(folder, name + ".png"), "wb").write(b"x")
accstats._note(folder, "tiktok", "за 20 окт")
accstats._note(folder, "instagram", "за 20.10")
accstats._note(folder, "youtube", "последние 48 часов (короче периода в YouTube Studio нет)")
items, day = accstats.latest_shots()
say([c for _, c in items] == ["YouTube Studio — аналитика, последние 48 часов (короче периода в YouTube Studio нет)",
                             "Instagram — статистика, за 20.10", "TikTok Studio — аналитика, за 20 окт"],
    f"{[c for _, c in items]}")

print("\n--- снимок — только своего приложения (живьём 22.10: в альбом легла переписка) ---")
shots = []
adb.screenshot = lambda path: shots.append(path) or open(path, "wb").write(b"p" * 20000)
folder = accstats.shots_dir()
FRONT[0] = "com.instagram.android"
got = accstats._snap(folder, "youtube")
say(got is None and not shots and "другое приложение" in accstats.notes().get("youtube", ""),
    "YouTube Studio, а на экране Instagram — снимка нет, в подписи — почему: "
    + accstats.notes().get("youtube", "")[:60])
FRONT[0] = "com.google.android.apps.youtube.creator"
say(accstats._snap(folder, "youtube") and len(shots) == 1, "на экране Studio — снят")
FRONT[0] = "com.instagram.android"
say(accstats._snap(folder, "instagram_tiles2") and accstats._snap(folder, "instagram"),
    "плитки и статистика Instagram — его же")
say(accstats._snap(folder, "tiktok_tiles") is None, "плитки TikTok при открытом Instagram — нет")
seq = iter(["com.google.android.apps.youtube.creator", "com.instagram.android"])
adb.current_app = lambda: (next(seq), "")
say(accstats._snap(folder, "youtube") is None, "пока снимали, вылезло другое приложение — снимок выброшен")
adb.current_app = lambda: (FRONT[0], "")

print("\n--- имя аккаунта в подписи (живьём 22.10: Instagram переключился на личный) ---")
ig_head = [N("easyease.studio", b=(330, 282, 632, 328)), N("4", b=(330, 350, 357, 406)),
           N("ontwos.studio", b=(315, 93, 719, 247))]
say(accstats._account_name(ig_head, W, H) == "ontwos.studio",
    "Instagram: логин из шапки, а не отображаемое имя под аватаром")
tt_head = [N("Ease.motion", b=(369, 134, 689, 195)), N("Мысль...", b=(525, 261, 716, 343))]
say(accstats._account_name(tt_head, W, H) == "Ease.motion", "TikTok: заголовок профиля")
accstats._note(folder, "instagram_account", "ontwos.studio")
items, day = accstats.latest_shots()
caps = [c for _, c in items]
say(any(c.startswith("Instagram (ontwos.studio) — статистика") for c in caps)
    and any(c.startswith("Instagram (ontwos.studio) — плитки") for c in caps),
    "в подписях снимков Instagram — его логин: " + next((c for c in caps if "Instagram" in c), ""))

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
