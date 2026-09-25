# -*- coding: utf-8 -*-
"""Насколько кадр из потока отстаёт от живого экрана. НУЖЕН ТЕЛЕФОН.

Эталон — `screencap`: он медленный, но показывает экран прямо сейчас.
После свайпа замеряем, сколько проходит, прежде чем кадр из потока сойдётся
с эталоном. Это и есть та задержка, из-за которой вердикт достаётся не тому
ролику. Только TikTok и только свайпы: ни лайков, ни тапов.
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import adb, config, human, vision
import stream as stream_mod

LIMIT = config.FEED_SAME_LIMIT
SWIPES = 4
PATIENCE = 8.0


import subprocess

SIZE = "360x800"


def same_size(png):
    """Привести кадр к общему размеру: отпечаток зависит от размера картинки,
    а поток 720x1600 и screencap 1080x2400 иначе не сравнить (выйдет 255)."""
    done = subprocess.run([stream_mod.ffmpeg_exe(), "-loglevel", "error",
                           "-f", "image2pipe", "-i", "pipe:0",
                           "-vf", "scale=" + SIZE.replace("x", ":"),
                           "-f", "image2pipe", "-vcodec", "png", "pipe:1"],
                          input=png, capture_output=True, timeout=20)
    return done.stdout or None


def live():
    """Эталонный снимок экрана прямо сейчас."""
    return adb.exec_out("screencap -p", timeout=20)


w, h = adb.screen_size()
print("экран %dx%d, порог совпадения %.1f" % (w, h, LIMIT))
feed = stream_mod.Stream().start()
print("поток поднят: %s, кусок %dс" % (config.STREAM_SIZE, config.STREAM_CHUNK_SEC))
time.sleep(2.0)

lags = []
for n in range(1, SWIPES + 1):
    human.scroll_feed(w, h, "up", quick=True)
    t0 = time.time()
    caught, probes = None, []
    while time.time() - t0 < PATIENCE:
        png = feed.frame(timeout=1.5)
        truth = live()
        if png is None or truth is None:
            continue
        a, b = same_size(png), same_size(truth)
        if not a or not b:
            continue
        d = vision.frames_differ(vision.frame_signature(a),
                                 vision.frame_signature(b))
        probes.append((time.time() - t0, d))
        if d < LIMIT:
            caught = time.time() - t0
            break
    trail = "  ".join("%.1fс:%.0f" % p for p in probes[:6])
    if caught is None:
        print("свайп %d: НЕ СОШЁЛСЯ за %.0fс   [%s]" % (n, PATIENCE, trail))
    else:
        lags.append(caught)
        print("свайп %d: поток догнал экран за %.2fс   [%s]" % (n, caught, trail))
    time.sleep(1.0)

feed.stop() if hasattr(feed, "stop") else None
if lags:
    print("\nотставание: минимум %.2fс, максимум %.2fс, среднее %.2fс"
          % (min(lags), max(lags), sum(lags) / len(lags)))
    print("сейчас ждём в _fresh_frame: %.1fс" % config.FRESH_FRAME_WAIT_SEC)
