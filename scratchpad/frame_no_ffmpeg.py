"""Кадр без ffmpeg: сырой снимок экрана ужимается и уходит в JPEG через GDI+.

Поймано 2026-09-29 на чужом ПК: без ffmpeg кадр шёл через `screencap -p`, и
съёмка занимала 3-5 с («кадр 5.16, модель 2.02»). Здесь без телефона: берём
настоящие снимки экрана из scratchpad, разворачиваем их в сырые пиксели —
ровно то, что отдаёт `screencap` без `-p`, — и гоняем новый путь.

Проверяем, что остальная программа этот кадр понимает: размер, отпечаток
для «лента застряла» (читает Tk), порог «пустой кадр» и JPEG для облака.
"""
import ctypes
import glob
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config   # noqa: E402
import picture  # noqa: E402
import vision   # noqa: E402

ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


def raw_of(path):
    """PNG -> то, что отдаёт `screencap`: заголовок 16 байт + RGBA."""
    gdi = ctypes.windll.gdiplus
    img = ctypes.c_void_p()
    gdi.GdipCreateBitmapFromFile.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p)]
    assert gdi.GdipCreateBitmapFromFile(path, ctypes.byref(img)) == 0
    w, h = ctypes.c_uint(), ctypes.c_uint()
    gdi.GdipGetImageWidth(img, ctypes.byref(w))
    gdi.GdipGetImageHeight(img, ctypes.byref(h))
    w, h = w.value, h.value

    class Rect(ctypes.Structure):
        _fields_ = [("X", ctypes.c_int), ("Y", ctypes.c_int), ("W", ctypes.c_int), ("H", ctypes.c_int)]

    class Data(ctypes.Structure):
        _fields_ = [("Width", ctypes.c_uint), ("Height", ctypes.c_uint), ("Stride", ctypes.c_int),
                    ("PixelFormat", ctypes.c_int), ("Scan0", ctypes.c_void_p), ("Reserved", ctypes.c_void_p)]

    data = Data()
    rect = Rect(0, 0, w, h)
    gdi.GdipBitmapLockBits.argtypes = [ctypes.c_void_p, ctypes.POINTER(Rect), ctypes.c_uint,
                                       ctypes.c_int, ctypes.POINTER(Data)]
    assert gdi.GdipBitmapLockBits(img, ctypes.byref(rect), 1, 0x0026200A, ctypes.byref(data)) == 0
    bgra = ctypes.string_at(data.Scan0, w * h * 4)
    gdi.GdipBitmapUnlockBits(img, ctypes.byref(data))
    gdi.GdipDisposeImage(img)
    rgba = bytearray(len(bgra))
    rgba[0::4], rgba[1::4], rgba[2::4], rgba[3::4] = bgra[2::4], bgra[1::4], bgra[0::4], bgra[3::4]
    return struct.pack("<IIII", w, h, 1, 0) + bytes(rgba)


print("--- GDI+ есть ---")
say(picture.available(), "GDI+ поднялась")

shots = sorted(glob.glob(os.path.join(HERE, "*.png")), key=os.path.getsize, reverse=True)[:6]
say(len(shots) >= 3, f"настоящих снимков экрана для проверки: {len(shots)}")

print("\n--- настоящие кадры ---")
factor = config.VISION_SHRINK
worst = 0.0
for path in shots:
    raw = raw_of(path)
    w, h = struct.unpack("<II", raw[:8])
    started = time.perf_counter()
    png = picture.scaled_png(memoryview(raw)[16:], w, h, factor)
    spent = time.perf_counter() - started
    worst = max(worst, spent)
    size = picture.size_of(png)
    jpg_at = time.perf_counter()
    jpg = picture.to_jpeg(png)
    jpg_spent = time.perf_counter() - jpg_at
    sig = vision.frame_signature(png)
    good = (png and size == (w // factor, h // factor) and png[:8] == b"\x89PNG\r\n\x1a\n"
            and jpg and jpg[:2] == b"\xff\xd8" and sig
            and not vision.looks_blank(png, config.BLANK_FRAME_SMALL_KB))
    say(good, f"{os.path.basename(path):18} {w}x{h} -> {size}  ужатие {spent*1000:4.0f} мс, "
              f"PNG {len(png)//1024} КБ, JPEG {len(jpg)//1024} КБ за {jpg_spent*1000:.0f} мс")
# Порог с запасом: первый кадр после запуска GDI+ и общий прогон под нагрузкой
# бывают медленнее (один сбой из 16 кругов при пороге 0.5 с). Главное — не 3-5 с.
say(worst < 1.5, f"самое долгое ужатие {worst*1000:.0f} мс (было: телефон кодирует PNG 3-5 с)")

print("\n--- пустой (погашенный) экран ---")
w, h = 1080, 2400
black = bytes(w * h * 4)
png = picture.scaled_png(black, w, h, factor)
say(png and vision.looks_blank(png, config.BLANK_FRAME_SMALL_KB),
    f"чёрный кадр {len(png)} байт — ниже порога {config.BLANK_FRAME_SMALL_KB} КБ")

print("\n--- кривой ввод не роняет ---")
say(picture.scaled_png(b"123", 10, 10, 3) is None, "обрезанные пиксели -> None")
say(picture.to_jpeg(b"not a png") is None, "не картинка -> None")

print("\n--- сессия берёт этот путь сама, без ffmpeg ---")
import adb      # noqa: E402
import session  # noqa: E402
import stream   # noqa: E402

stream.ffmpeg_exe = lambda: None                # как на чужом ПК
sample = raw_of(shots[0])
adb.exec_out = lambda cmd, timeout=60: sample   # телефон «отдал» сырой кадр
got = session._raw_scaled({})
say(got and picture.size_of(got) == (1080 // factor, 2400 // factor)
    if struct.unpack("<II", sample[:8]) == (1080, 2400) else got,
    "_raw_scaled без ffmpeg вернул ужатый кадр")
vision._FFMPEG = "?"
jpg = vision.to_jpeg(got)
say(jpg and jpg[:2] == b"\xff\xd8", "vision.to_jpeg без ffmpeg отдал JPEG")
url = vision._image_url(got, 1, vision.API)
say(url.startswith("data:image/jpeg;base64,"), "в облако уходит JPEG, а не PNG")

print("\nВСЁ СОШЛОСЬ" if ok else "\nЕСТЬ ОШИБКИ")
sys.exit(0 if ok else 1)
