"""Иконка приложения и аватарка бота — рисуются кодом.

PIL в проекте нет и заводить его ради картинки незачем: PNG собирается
вручную (zlib + CRC), ICO — это контейнер из тех же PNG. Рисуем с
четырёхкратным запасом и усредняем — так получаются мягкие края.

Что изображено: скруглённый квадрат с розово-фиолетовым переходом (те же
цвета, что у значка в окне) и белый треугольник «плей» со смещённой бирюзовой
тенью — это отсылка к раздвоенному цвету логотипа TikTok. На 16 пикселях
остаётся читаемой кнопка воспроизведения, ничего не сливается.

Запуск:  python make_icon.py
На выходе: icon.ico (для exe) и bot-avatar.png (для @BotFather).
"""
import os
import struct
import zlib

BASE = os.path.dirname(os.path.abspath(__file__))

PINK = (254, 44, 85)
VIOLET = (123, 47, 247)
CYAN = (37, 244, 238)
WHITE = (255, 255, 255)

SS = 4                      # во столько раз рисуем крупнее для сглаживания
ICO_SIZES = (16, 32, 48, 64, 128, 256)


# ----------------------------------------------------------------- рисование

def _rounded_alpha(x, y, size, radius):
    """1.0 внутри скруглённого квадрата, 0.0 снаружи."""
    left, top = radius, radius
    right, bottom = size - radius, size - radius
    dx = left - x if x < left else (x - right if x > right else 0)
    dy = top - y if y < top else (y - bottom if y > bottom else 0)
    if dx == 0 and dy == 0:
        return 1.0
    return 1.0 if dx * dx + dy * dy <= radius * radius else 0.0


def _in_triangle(x, y, pts):
    (ax, ay), (bx, by), (cx, cy) = pts

    def side(px, py, qx, qy):
        return (x - px) * (qy - py) - (y - py) * (qx - px)

    d1, d2, d3 = side(ax, ay, bx, by), side(bx, by, cx, cy), side(cx, cy, ax, ay)
    neg = d1 < 0 or d2 < 0 or d3 < 0
    pos = d1 > 0 or d2 > 0 or d3 > 0
    return not (neg and pos)


def _blend(under, over, alpha):
    return tuple(round(u + (o - u) * alpha) for u, o in zip(under, over))


def render(size):
    """RGBA-буфер иконки указанного размера."""
    big = size * SS
    radius = big * 0.235

    # «Плей»: равносторонний треугольник, слегка правее центра — так он
    # выглядит уравновешенным, хотя геометрически смещён.
    half = big * 0.205
    cx, cy = big * 0.545, big * 0.5
    tri = ((cx - half * 0.86, cy - half),
           (cx - half * 0.86, cy + half),
           (cx + half, cy))
    shift = big * 0.035
    tri_shadow = tuple((px - shift, py - shift * 0.55) for px, py in tri)

    rows = []
    for y in range(big):
        row = []
        for x in range(big):
            inside = _rounded_alpha(x + 0.5, y + 0.5, big, radius)
            if not inside:
                row.append((0, 0, 0, 0))
                continue
            # Диагональный переход розовый -> фиолетовый.
            t = (x / big * 0.65) + (y / big * 0.35)
            color = _blend(PINK, VIOLET, t)
            if _in_triangle(x + 0.5, y + 0.5, tri_shadow):
                color = CYAN
            if _in_triangle(x + 0.5, y + 0.5, tri):
                color = WHITE
            row.append(color + (255,))
        rows.append(row)

    # Усреднение SSxSS -> итоговый пиксель.
    out = bytearray()
    for y in range(size):
        out.append(0)                       # тип фильтра строки PNG
        for x in range(size):
            r = g = b = a = 0
            for dy in range(SS):
                for dx in range(SS):
                    pr, pg, pb, pa = rows[y * SS + dy][x * SS + dx]
                    r += pr * pa; g += pg * pa; b += pb * pa; a += pa
            n = SS * SS
            if a:
                out += bytes((r // a, g // a, b // a, a // n))
            else:
                out += b"\0\0\0\0"
    return bytes(out)


# --------------------------------------------------------------------- PNG

def _chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def png(size):
    raw = render(size)
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw, 9))
            + _chunk(b"IEND", b""))


def ico(sizes):
    """Контейнер ICO из PNG-кадров. Windows понимает PNG внутри ICO."""
    images = [(s, png(s)) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, blobs = b"", b""
    for size, data in images:
        entries += struct.pack("<BBBBHHII",
                               0 if size >= 256 else size,
                               0 if size >= 256 else size,
                               0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    return header + entries + blobs


if __name__ == "__main__":
    path = os.path.join(BASE, "icon.ico")
    with open(path, "wb") as f:
        f.write(ico(ICO_SIZES))
    print(f"иконка приложения: {path} ({os.path.getsize(path) // 1024} КБ)")

    avatar = os.path.join(BASE, "bot-avatar.png")
    with open(avatar, "wb") as f:
        f.write(png(512))
    print(f"аватарка бота:     {avatar} ({os.path.getsize(avatar) // 1024} КБ)")
