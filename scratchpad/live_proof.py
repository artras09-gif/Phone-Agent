# -*- coding: utf-8 -*-
"""Живая сверка: что видит модель в кадре ПОТОКА и в кадре ЭКРАНА. НУЖЕН ТЕЛЕФОН.

В один и тот же момент после свайпа берём оба кадра и оба показываем модели.
Если поток отстаёт, его описание будет про ПРЕДЫДУЩИЙ ролик, а описание
снимка — про тот, что на экране. Только TikTok, только свайпы: ни лайков,
ни тапов.
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import adb, config, human, vision
import stream as stream_mod

ok, why = vision.available()
if not ok:
    print("модель недоступна:", why); sys.exit(2)
print("модель:", why, "|", vision.where())

w, h = adb.screen_size()
feed = stream_mod.Stream().start()
time.sleep(2.5)
vision.warm_up()

def tema(png):
    if png is None:
        return "(кадра нет)"
    d = vision.describe_frame(png, "", "", "", shrink=2)
    return "%s — %s" % (d.get("категория", "?"), str(d.get("тема", ""))[:70])

prev_screen = None
for n in range(1, 4):
    human.scroll_feed(w, h, "up", quick=True)     # пауза как в бою, 0.05-0.15с

    from_stream = feed.frame()
    from_screen = adb.exec_out("screencap -p", timeout=20)

    s_stream, s_screen = tema(from_stream), tema(from_screen)
    print("\n--- свайп %d ---" % n)
    print("  поток : %s" % s_stream)
    print("  экран : %s" % s_screen)
    if prev_screen:
        print("  (на прошлом шаге на ЭКРАНЕ было: %s)" % prev_screen)
    prev_screen = s_screen
    time.sleep(2.0)

print("\nЕсли строка «поток» повторяет то, что было на экране ШАГОМ РАНЬШЕ —")
print("это и есть поломка, из-за которой вердикт доставался не тому ролику.")
