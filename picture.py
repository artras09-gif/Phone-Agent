# -*- coding: utf-8 -*-
"""Ужать кадр и перегнать его в JPEG средствами самой Windows (GDI+).

Зачем. Решение по ролику ждёт кадр. Быстрый путь — сырой снимок экрана
(`screencap` без `-p`: телефон отдаёт пиксели как есть) и ужатие на ПК.
Раньше ужимал ffmpeg, а без ffmpeg программа молча уходила на `screencap -p`:
PNG полного размера кодирует сам телефон, и под играющим роликом это 3-5 с
на кадр. Поймано на чужом ПК 2026-09-29: «кадр 5.16, модель 2.02». На машине
разработки этого не было видно — ffmpeg лежал в папке, прописанной в коде.

GDI+ есть в любой Windows и зовётся через ctypes прямо в процессе: ничего не
надо ставить, и не запускается ни одна программа (значит, и окон консоли
нет). Масштабирует бикубически — не хуже ffmpeg.

При любой осечке функции возвращают None: вызывающий идёт запасным путём.
"""
import ctypes
import struct
import threading
import uuid
from ctypes import POINTER, byref, c_int, c_int64, c_uint, c_uint32, c_uint64, c_ulong, c_void_p

_LOCK = threading.Lock()
_GDI = None             # None — ещё не пробовали, False — не вышло

PIXEL_32RGB = 0x00022009    # B, G, R, X — байт прозрачности не читается
PIXEL_24RGB = 0x00021808
HIGH_QUALITY_BICUBIC = 7
PIXEL_OFFSET_HALF = 4
COMPOSITE_COPY = 1

PNG_CODEC = "557CF406-1A04-11D3-9A73-0000F81EF32E"
JPEG_CODEC = "557CF401-1A04-11D3-9A73-0000F81EF32E"
QUALITY = "1D5BE4B5-FA4A-452D-9CDD-5DB35105E7EB"

# Качество JPEG для облака. 85 — подписи на кадре читаются так же, как в
# PNG, а весит кадр 360x800 около 50 КБ вместо 300-400.
JPEG_QUALITY = 85


class GUID(ctypes.Structure):
    _fields_ = [("Data1", c_uint32), ("Data2", ctypes.c_uint16),
                ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]


class _StartupInput(ctypes.Structure):
    _fields_ = [("GdiplusVersion", c_uint32), ("DebugEventCallback", c_void_p),
                ("SuppressBackgroundThread", c_int), ("SuppressExternalCodecs", c_int)]


class _EncoderParameter(ctypes.Structure):
    _fields_ = [("Guid", GUID), ("NumberOfValues", c_ulong),
                ("Type", c_ulong), ("Value", c_void_p)]


class _EncoderParameters(ctypes.Structure):
    _fields_ = [("Count", c_uint), ("Parameter", _EncoderParameter * 1)]


def _guid(text):
    return GUID.from_buffer_copy(uuid.UUID(text).bytes_le)


def _gdi():
    """Библиотека GDI+, запущенная один раз на процесс. None — её нет."""
    global _GDI
    if _GDI is None:
        _GDI = False
        try:
            gdi = ctypes.windll.gdiplus
            shl = ctypes.windll.shlwapi
            vp = c_void_p
            for name, args in (
                ("GdiplusStartup", [POINTER(ctypes.c_size_t), POINTER(_StartupInput), vp]),
                ("GdipCreateBitmapFromScan0", [c_int, c_int, c_int, c_int, vp, POINTER(vp)]),
                ("GdipCreateBitmapFromStream", [vp, POINTER(vp)]),
                ("GdipGetImageGraphicsContext", [vp, POINTER(vp)]),
                ("GdipSetInterpolationMode", [vp, c_int]),
                ("GdipSetPixelOffsetMode", [vp, c_int]),
                ("GdipSetCompositingMode", [vp, c_int]),
                ("GdipDrawImageRectI", [vp, vp, c_int, c_int, c_int, c_int]),
                ("GdipDrawImageRectRectI", [vp, vp, c_int, c_int, c_int, c_int,
                                            c_int, c_int, c_int, c_int, c_int, vp, vp, vp]),
                ("GdipCreatePen1", [c_uint32, ctypes.c_float, c_int, POINTER(vp)]),
                ("GdipDrawEllipseI", [vp, vp, c_int, c_int, c_int, c_int]),
                ("GdipDrawLineI", [vp, vp, c_int, c_int, c_int, c_int]),
                ("GdipDeletePen", [vp]),
                ("GdipDeleteGraphics", [vp]),
                ("GdipSaveImageToStream", [vp, vp, POINTER(GUID), vp]),
                ("GdipGetImageWidth", [vp, POINTER(c_uint)]),
                ("GdipGetImageHeight", [vp, POINTER(c_uint)]),
                ("GdipDisposeImage", [vp]),
            ):
                fn = getattr(gdi, name)
                fn.argtypes = args
                fn.restype = c_int
            shl.SHCreateMemStream.argtypes = [vp, c_uint]
            shl.SHCreateMemStream.restype = vp

            token = ctypes.c_size_t()
            start = _StartupInput(1, None, 0, 0)
            if gdi.GdiplusStartup(byref(token), byref(start), None) == 0:
                _GDI = (gdi, shl)
        except (AttributeError, OSError):
            pass
    return _GDI or None


def available():
    with _LOCK:
        return _gdi() is not None


# --- IStream: память вместо временного файла ------------------------------
# Файл на диске каждый кадр — это ещё и проверка антивирусом на каждый кадр.

def _method(obj, index, restype, *argtypes):
    table = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
    return ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)(table[index])


def _release(stream):
    if stream:
        _method(stream, 2, c_ulong)(stream)


def _stream_bytes(stream):
    seek = _method(stream, 5, ctypes.HRESULT, c_int64, c_ulong, POINTER(c_uint64))
    read = _method(stream, 3, ctypes.HRESULT, c_void_p, c_ulong, POINTER(c_ulong))
    size = c_uint64()
    seek(stream, 0, 2, byref(size))           # в конец — узнать размер
    seek(stream, 0, 0, None)                  # и обратно в начало
    buf = ctypes.create_string_buffer(size.value)
    got = c_ulong()
    read(stream, buf, size.value, byref(got))
    return buf.raw[:got.value]


def _save(gdi, shl, image, codec, quality=None):
    """Изображение GDI+ -> байты PNG или JPEG."""
    stream = shl.SHCreateMemStream(None, 0)
    if not stream:
        return None
    try:
        params = None
        if quality is not None:
            value = c_ulong(int(quality))
            params = _EncoderParameters(1)
            params.Parameter[0] = _EncoderParameter(
                _guid(QUALITY), 1, 4, ctypes.cast(byref(value), c_void_p))
        clsid = _guid(codec)
        if gdi.GdipSaveImageToStream(image, stream, byref(clsid),
                                     byref(params) if params else None) != 0:
            return None
        return _stream_bytes(stream)
    finally:
        _release(stream)


def scaled_png(pixels, width, height, factor, order="rgba"):
    """Сырые пиксели экрана -> PNG, уменьшенный в `factor` раз. None — не вышло.

    `pixels` — ровно width*height*4 байта, как их отдаёт `screencap`:
    `order="rgba"` (RGBA_8888 / RGBX_8888) или `"bgra"`. GDI+ ждёт B, G, R —
    каналы переставляем срезами, это целиком на стороне C.
    """
    if len(pixels) != width * height * 4 or factor < 1:
        return None
    with _LOCK:
        lib = _gdi()
        if not lib:
            return None
        gdi, shl = lib
        if order == "rgba":
            buf = bytearray(len(pixels))
            buf[0::4] = pixels[2::4]
            buf[1::4] = pixels[1::4]
            buf[2::4] = pixels[0::4]
        else:
            buf = bytearray(pixels)
        # GDI+ не копирует пиксели, а читает их из этого буфера — он должен
        # жить, пока жив исходный рисунок.
        scan0 = (ctypes.c_ubyte * len(buf)).from_buffer(buf)
        src = c_void_p()
        dst = c_void_p()
        graphics = c_void_p()
        out_w, out_h = max(1, width // factor), max(1, height // factor)
        try:
            if gdi.GdipCreateBitmapFromScan0(width, height, width * 4, PIXEL_32RGB,
                                             scan0, byref(src)) != 0:
                return None
            if factor == 1:
                return _save(gdi, shl, src, PNG_CODEC)
            if gdi.GdipCreateBitmapFromScan0(out_w, out_h, 0, PIXEL_24RGB,
                                             None, byref(dst)) != 0:
                return None
            if gdi.GdipGetImageGraphicsContext(dst, byref(graphics)) != 0:
                return None
            gdi.GdipSetCompositingMode(graphics, COMPOSITE_COPY)
            gdi.GdipSetInterpolationMode(graphics, HIGH_QUALITY_BICUBIC)
            gdi.GdipSetPixelOffsetMode(graphics, PIXEL_OFFSET_HALF)
            if gdi.GdipDrawImageRectI(graphics, src, 0, 0, out_w, out_h) != 0:
                return None
            gdi.GdipDeleteGraphics(graphics)
            graphics = c_void_p()
            return _save(gdi, shl, dst, PNG_CODEC)
        except OSError:
            return None
        finally:
            if graphics:
                gdi.GdipDeleteGraphics(graphics)
            for image in (dst, src):
                if image:
                    gdi.GdipDisposeImage(image)
            del scan0


def from_screencap(raw, factor=1):
    """Ответ `adb exec-out screencap` (без -p) -> PNG, ужатый в factor раз.

    Заголовок: ширина, высота, формат и — с Android 9 — ещё и цветовое
    пространство; какой именно, видно по остатку (пиксели — ровно
    ширина*высота*4). Форматы 1, 2 — RGBA/RGBX_8888, 5 — BGRA_8888.
    None — не разобрал.
    """
    if not raw or len(raw) < 16:
        return None
    w, h, fmt = struct.unpack("<III", bytes(raw[:12]))
    head = 16 if len(raw) - 16 == w * h * 4 else 12
    order = {1: "rgba", 2: "rgba", 5: "bgra"}.get(fmt)
    if not order or len(raw) - head != w * h * 4:
        return None
    return scaled_png(memoryview(raw)[head:], w, h, factor, order)


def to_jpeg(png, quality=None):
    """PNG -> JPEG для облака. None — не вышло (тогда уходит PNG)."""
    with _LOCK:
        lib = _gdi()
        if not lib or not png:
            return None
        gdi, shl = lib
        data = ctypes.create_string_buffer(bytes(png), len(png))
        stream = shl.SHCreateMemStream(data, len(png))
        if not stream:
            return None
        image = c_void_p()
        try:
            if gdi.GdipCreateBitmapFromStream(stream, byref(image)) != 0:
                return None
            return _save(gdi, shl, image, JPEG_CODEC, quality or JPEG_QUALITY)
        except OSError:
            return None
        finally:
            if image:
                gdi.GdipDisposeImage(image)
            _release(stream)


RING_COLOR = 0xFFFF1E1E      # ARGB, красный
UNIT_PIXEL = 2


def crop_png(png, box, ring=None):
    """Вырезать из картинки прямоугольник `box` (x0, y0, x1, y1) в пикселях.

    `ring` — точка ВНУТРИ вырезки, вокруг которой рисуется красное кольцо:
    так модели показывают, куда собираются нажать, и спрашивают второй раз,
    уже вблизи (`escape._confirm`). Масштаб 1:1 — вблизи модель должна видеть
    мелкий крестик таким, какой он есть.

    Только кольцо, БЕЗ перекрестья: перекрестье внутри кольца модель читала
    как кнопку «+» — «создание публикации, опасно» — и запрещала почти любое
    нажатие (замер 2026-09-30: 11 отказов из 18, в 9 из них — «кнопка +»).
    """
    x0, y0, x1, y1 = (int(v) for v in box)
    cw, ch = x1 - x0, y1 - y0
    if cw <= 0 or ch <= 0:
        return None
    with _LOCK:
        lib = _gdi()
        if not lib or not png:
            return None
        gdi, shl = lib
        data = ctypes.create_string_buffer(bytes(png), len(png))
        stream = shl.SHCreateMemStream(data, len(png))
        if not stream:
            return None
        src, dst, graphics, pen = c_void_p(), c_void_p(), c_void_p(), c_void_p()
        try:
            if gdi.GdipCreateBitmapFromStream(stream, byref(src)) != 0:
                return None
            if gdi.GdipCreateBitmapFromScan0(cw, ch, 0, PIXEL_24RGB, None, byref(dst)) != 0:
                return None
            if gdi.GdipGetImageGraphicsContext(dst, byref(graphics)) != 0:
                return None
            gdi.GdipSetCompositingMode(graphics, COMPOSITE_COPY)
            if gdi.GdipDrawImageRectRectI(graphics, src, 0, 0, cw, ch, x0, y0, cw, ch,
                                          UNIT_PIXEL, None, None, None) != 0:
                return None
            if ring is not None:
                rx, ry = (int(v) for v in ring)
                if gdi.GdipCreatePen1(RING_COLOR, 3.0, UNIT_PIXEL, byref(pen)) == 0:
                    gdi.GdipDrawEllipseI(graphics, pen, rx - 34, ry - 34, 68, 68)
            gdi.GdipDeleteGraphics(graphics)
            graphics = c_void_p()
            return _save(gdi, shl, dst, PNG_CODEC)
        except OSError:
            return None
        finally:
            if pen:
                gdi.GdipDeletePen(pen)
            if graphics:
                gdi.GdipDeleteGraphics(graphics)
            for image in (dst, src):
                if image:
                    gdi.GdipDisposeImage(image)
            _release(stream)


def dots_png(width, height, spots, radius=30):
    """Белая картинка с красными кругами в точках `spots`.

    Проверочная: по ней узнают, в какой системе координат модель называет
    точки и насколько точно (`escape.hands_ready`).
    """
    px = bytearray(b"\xff" * (width * height * 4))
    for cx, cy in spots:
        for y in range(max(0, cy - radius), min(height, cy + radius + 1)):
            half = int((radius * radius - (y - cy) ** 2) ** 0.5)
            left, right = max(0, cx - half), min(width - 1, cx + half)
            row = (y * width + left) * 4
            px[row:row + (right - left + 1) * 4] = b"\xff\x20\x20\xff" * (right - left + 1)
    return scaled_png(bytes(px), width, height, 1)


def size_of(data):
    """(ширина, высота) картинки PNG/JPEG. None — не разобрал. Для проверок."""
    with _LOCK:
        lib = _gdi()
        if not lib or not data:
            return None
        gdi, shl = lib
        buf = ctypes.create_string_buffer(bytes(data), len(data))
        stream = shl.SHCreateMemStream(buf, len(data))
        if not stream:
            return None
        image = c_void_p()
        try:
            if gdi.GdipCreateBitmapFromStream(stream, byref(image)) != 0:
                return None
            w, h = c_uint(), c_uint()
            gdi.GdipGetImageWidth(image, byref(w))
            gdi.GdipGetImageHeight(image, byref(h))
            return w.value, h.value
        finally:
            if image:
                gdi.GdipDisposeImage(image)
            _release(stream)
