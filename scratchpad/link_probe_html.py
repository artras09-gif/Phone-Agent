"""Разметка калибровки (`link-probe shorts`) — без телефона.

Точки для Shorts и Reels проверяются глазами по HTML со снимком экрана, и
если эта разметка соберётся криво, промах кнопки будет уже не на чем
заметить. Проверяем то, от чего зависит вывод: крестики стоят ровно на
заданных долях, снимок лежит внутри файла (его же уносят на другой ПК),
подсказка про `recipes.json` на месте, браузер позван.

Телефон подменён: отдаёт однопиксельный PNG.
"""
import os
import sys
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import adb  # noqa: E402
import config  # noqa: E402
import main as m  # noqa: E402
import session  # noqa: E402

# Однопиксельный PNG — меньше нельзя, а больше и не нужно.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6300010000050001"
)

ok = True


def check(name, good):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {name}")


def probe(app, likes_answer=None):
    """Прогнать разметку для одной ленты. `likes_answer` — что ответит зрение."""
    cfg = session._feed_config(app)
    adb.exec_out = lambda cmd, timeout=20: PNG

    opened = []
    webbrowser.open = lambda url: opened.append(url)

    if likes_answer is None:
        session.vision.available = lambda: (False, "выключено")
    else:
        session.vision.available = lambda: (True, "подставная модель")
        session.vision.looks_blank = lambda png, limit_kb=None: False
        session.vision.looks_degenerate = lambda answer: False
        session.vision.ask = lambda png, prompt, **kw: likes_answer

    rc = m._probe_points(app, cfg, 1080, 2400, [])
    path = os.path.join(config.BASE, "frames", f"probe-{app}.html")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    return rc, html, opened, cfg


print("--- shorts: остались только точки пути к поиску ---")
rc, html, opened, cfg = probe("shorts")
# С 2026-08-30 «поделиться» берётся СЕЛЕКТОРОМ: YouTube отдаёт кнопку в
# дерево, если панель на экране (её прятали наши же тапы). Координата убрана
# совсем — крестика для неё быть не должно.
check("share_point убран", cfg.get("share_point") is None)
check("кнопка ищется селектором", bool(cfg.get("share_selectors")))
check("html собрался", html.startswith("<!doctype html>"))
check("крестика «поделиться» нет", "share_point" not in html)
# Каждая точка названа дважды: подписью у крестика и строкой в списке справа.
check("оба шага пути к поиску размечены",
      html.count("search_points[0]") == 2 and html.count("search_points[1]") == 2)
check("возврат в ленту размечен", "home_point" in html)
check("снимок внутри файла", "data:image/png;base64," in html)
check("сказано, где править", "recipes.json" in html)
check("браузер позван", bool(opened))
check("без зрения код возврата 1", rc == 1)

print("\n--- reels: одна точка, остальное селекторами ---")
rc, html, opened, cfg = probe("reels", likes_answer="128,4 тыс.")
check("share_point убран и здесь", cfg.get("share_point") is None)
check("кнопка ищется селектором", bool(cfg.get("share_selectors")))
check("лишних точек не нарисовано", "search_points[" not in html)
check("со зрением код возврата 0", rc == 0)

print("\n--- лента без точек размечать нечего ---")
check("у tiktok точек нет", not session._feed_config("tiktok").get("share_point"))

print("\nВСЁ ХОРОШО" if ok else "\nЕСТЬ ОШИБКИ")
sys.exit(0 if ok else 1)
