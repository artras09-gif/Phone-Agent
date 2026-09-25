# -*- coding: utf-8 -*-
"""Сырой кадр + ffmpeg против `screencap -p`. НУЖЕН ТЕЛЕФОН."""
import os, struct, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import adb, config, device, vision
import stream as stream_mod

device.keep_awake(True); device.ensure_awake()
W, H = adb.screen_size()
TARGET = (W // config.VISION_SHRINK, H // config.VISION_SHRINK)


def raw_to_png(raw, size):
    """Заголовок screencap: ширина, высота, формат и (с Android 9) цвет."""
    if len(raw) < 16:
        return None
    w, h, _fmt = struct.unpack("<III", raw[:12])
    head = 16 if len(raw) - 16 == w * h * 4 else 12
    if len(raw) - head != w * h * 4:
        return None
    done = subprocess.run(
        [stream_mod.ffmpeg_exe(), "-loglevel", "error",
         "-f", "rawvideo", "-pix_fmt", "rgba", "-s", "%dx%d" % (w, h),
         "-i", "pipe:0", "-vf", "scale=%d:%d" % size,
         "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1"],
        input=raw[head:], capture_output=True, timeout=25)
    return done.stdout or None


print("экран %dx%d -> цель %dx%d" % (W, H, TARGET[0], TARGET[1]))
old_t, new_t, old_model, new_model = [], [], [], []
vision.warm_up()

for i in range(4):
    t = time.time(); png = adb.exec_out("screencap -p", timeout=25); old_t.append(time.time()-t)
    t = time.time()
    vision.describe_frame(png, "", "", "", shrink=config.VISION_SHRINK)
    old_model.append(time.time()-t)

    t = time.time()
    raw = adb.exec_out("screencap", timeout=25)
    small = raw_to_png(raw, TARGET)
    new_t.append(time.time()-t)
    if small is None:
        print("  сырой кадр не разобрался"); break
    t = time.time()
    d = vision.describe_frame(small, "", "", "", shrink=1)
    new_model.append(time.time()-t)
    if i == 0:
        print("  PNG телефона %.0f КБ | ужатый на ПК %.0f КБ | пусто=%s"
              % (len(png)/1024, len(small)/1024, vision.looks_blank(small)))
        print("  модель на ужатом сказала:", str(d.get("тема",""))[:60])
    time.sleep(0.6)

f = lambda xs: sum(xs)/len(xs) if xs else 0
print("\n            кадр    модель   ИТОГО")
print("сейчас   %6.2f  %6.2f  %6.2f" % (f(old_t), f(old_model), f(old_t)+f(old_model)))
print("сырой+ff %6.2f  %6.2f  %6.2f" % (f(new_t), f(new_model), f(new_t)+f(new_model)))
