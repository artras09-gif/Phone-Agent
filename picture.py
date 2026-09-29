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
