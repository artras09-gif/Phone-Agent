# -*- coding: utf-8 -*-
"""Честное сравнение трёх способов взять кадр — в одном прогоне. НУЖЕН ТЕЛЕФОН.

Модель в LM Studio гуляет по скорости, поэтому варианты чередуются на одном
и том же экране, а не меряются разными запусками.
"""
import os, struct, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import adb, config, device, human, vision
import stream as stream_mod

device.keep_awake(True); device.ensure_awake()
W, H = adb.screen_size()
T = (W // config.VISION_SHRINK, H // config.VISION_SHRINK)
FF = stream_mod.ffmpeg_exe()


def ff(args, data):
    d = subprocess.run([FF, "-loglevel", "error"] + args, input=data,
                       capture_output=True, timeout=25)
    return d.stdout or None


def scale_png(png):
    return ff(["-f", "image2pipe", "-i", "pipe:0", "-vf", "scale=%d:%d" % T,
               "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1"], png)


def raw_scaled():
    raw = adb.exec_out("screencap", timeout=25)
    if len(raw) < 16:
        return None
    w, h, _ = struct.unpack("<III", raw[:12])
    head = 16 if len(raw) - 16 == w * h * 4 else 12
    if len(raw) - head != w * h * 4:
        return None
    return ff(["-f", "rawvideo", "-pix_fmt", "rgba", "-s", "%dx%d" % (w, h),
               "-i", "pipe:0", "-vf", "scale=%d:%d" % T, "-frames:v", "1",
               "-f", "image2pipe", "-vcodec", "png", "pipe:1"], raw[head:])


vision.warm_up()
res = {"A сейчас": [], "B сырой+ff": [], "C png+ff": []}
print("экран %dx%d -> %dx%d, по 4 круга на вариант\n" % (W, H, T[0], T[1]))

for i in range(4):
    t = time.time(); png = adb.exec_out("screencap -p", timeout=25)
    vision.describe_frame(png, "", "", "", shrink=config.VISION_SHRINK)
    res["A сейчас"].append(time.time() - t)

    t = time.time(); small = raw_scaled()
    if small: vision.describe_frame(small, "", "", "", shrink=1)
    res["B сырой+ff"].append(time.time() - t)

    t = time.time(); png2 = adb.exec_out("screencap -p", timeout=25)
    small2 = scale_png(png2)
    if small2: vision.describe_frame(small2, "", "", "", shrink=1)
    res["C png+ff"].append(time.time() - t)

    human.scroll_feed(W, H, "up", quick=True)
    time.sleep(1.0)

for k, v in res.items():
    print("%-12s %.2fс   (%s)" % (k, sum(v)/len(v), " ".join("%.2f" % x for x in v)))
