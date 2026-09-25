# -*- coding: utf-8 -*-
"""Стенд: решение принимается по ЖИВОМУ кадру, а не по устаревшему из потока.

Поломка, которую ловил пользователь 2026-09-01 дважды: модель описывала
предыдущий ролик («Путин на церемонии», когда на экране парень режет пиццу),
и агент смотрел и лайкал не то видео. Причина — поток отстаёт от экрана на
3-4 с (замер `stream_lag.py`), а свайп ждёт 0.05-0.15 с.

Телефон не нужен: и поток, и снимок экрана подменены.
"""
import os, random, struct, sys, zlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import adb, config, session, vision

W, H, BLOCK = 360, 800, 64


def make_png(seed):
    """Кадр с крупными цветными блоками и мелким шумом.

    Блоки — чтобы отпечаток (ужатие в 32 раза) видел разницу, шум — чтобы PNG
    не ужался ниже `BLANK_FRAME_KB` и не сошёл за погашенный экран.
    """
    rnd = random.Random(seed)
    colors, rows = {}, []
    for y in range(H):
        row = bytearray(b"\x00")
        for x in range(W):
            key = (x // BLOCK, y // BLOCK)
            if key not in colors:
                colors[key] = tuple(rnd.randrange(256) for _ in range(3))
            for c in colors[key]:
                row.append(max(0, min(255, c + rnd.randrange(-24, 25))))
        rows.append(bytes(row))
    raw = zlib.compress(b"".join(rows), 6)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


STALE = make_png(1)                 # что отдаёт поток: предыдущий ролик
LIVE  = make_png(2) + b"\x00" * 999  # что на экране сейчас (вес заведомо иной)


class FakeStream:
    def __init__(self):
        self.calls = 0

    def frame(self, timeout=6.0, max_age=1.0):
        self.calls += 1
        return STALE


# Снимок экрана подменяем на уровне adb — так же, как его зовёт _grab.
adb.exec_out = lambda cmd, timeout=20: LIVE

ok = True
say = lambda good, text: (print(("  [ок ] " if good else "  [ПРОВАЛ] ") + text), good)[1]

print("режим: DECIDE_FROM_SCREENCAP = %s" % config.DECIDE_FROM_SCREENCAP)
ok &= say(not vision.looks_blank(STALE) and not vision.looks_blank(LIVE),
          "оба кадра не считаются погашенным экраном")

# --- 1. решение по живому кадру, хотя поток подключён и отдаёт старое
st = FakeStream()
state = {"stream": st, "id": "test-decide", "frame_size": len(STALE)}
path, png = session._grab(state, 1, live=True)
ok &= say(png == LIVE, "живой запрос обошёл поток и взял снимок экрана")
ok &= say(st.calls == 0, "к потоку при этом не обращались вовсе")

# --- 2. вес кадра не испорчен: зацикливание ловится сравнением потока с потоком
ok &= say(state["frame_size"] == len(STALE),
          "точка отсчёта для зацикливания осталась потоковой (%d Б)"
          % state["frame_size"])

# --- 3. обычный запрос по-прежнему берёт кадр из потока
st2 = FakeStream()
state2 = {"stream": st2, "id": "test-decide"}
path, png = session._grab(state2, 2, live=False)
ok &= say(png == STALE and st2.calls == 1, "обычный запрос идёт в поток, как и раньше")
ok &= say(state2.get("frame_size") == len(STALE),
          "и он же выставляет вес кадра")

# --- 4. без потока живой запрос работает так же
state3 = {"stream": None, "id": "test-decide"}
path, png = session._grab(state3, 3, live=True)
ok &= say(png == LIVE and state3.get("frame_size") == len(LIVE),
          "без потока: снимок экрана и вес от него же")

print("\nИТОГ:", "всё сходится" if ok else "ЕСТЬ ПРОВАЛЫ")
sys.exit(0 if ok else 1)
