# -*- coding: utf-8 -*-
"""Из чего складывается время решения и что можно срезать. НУЖЕН ТЕЛЕФОН."""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import adb, config, human, vision

ok, why = vision.available()
if not ok:
    print("модель недоступна:", why); sys.exit(2)
print("модель:", why)
w, h = adb.screen_size()
vision.warm_up()

def avg(xs): return sum(xs) / len(xs)

# --- 1. снимок экрана: PNG против сырого кадра
png_t, raw_t, sizes = [], [], []
for _ in range(4):
    t = time.time(); png = adb.exec_out("screencap -p", timeout=20); png_t.append(time.time()-t)
    sizes.append(len(png))
    t = time.time(); raw = adb.exec_out("screencap", timeout=20); raw_t.append(time.time()-t)
    human.scroll_feed(w, h, "up", quick=True); time.sleep(1.2)
print("\nснимок PNG : %.2fс (%.1f МБ)" % (avg(png_t), avg(sizes)/1e6))
print("снимок сырой: %.2fс" % avg(raw_t))

# --- 2. модель при разном ужатии
shot = adb.exec_out("screencap -p", timeout=20)
print("\nкадр экрана %dx%d" % (w, h))
for sh in (2, 3, 4, 5):
    times, temas = [], []
    for _ in range(2):
        t = time.time()
        d = vision.describe_frame(shot, "", "", "", shrink=sh)
        times.append(time.time() - t)
        temas.append(str(d.get("тема", ""))[:44])
    print("  shrink=%d -> %4dx%-4d  %.2fс   %s"
          % (sh, w // sh, h // sh, avg(times), temas[-1]))
