# -*- coding: utf-8 -*-
"""Живой ли `screencap` сразу после свайпа и сколько он стоит. НУЖЕН ТЕЛЕФОН.

Поток отстаёт на 3-4с (см. stream_lag.py). Вопрос: показывает ли screencap
уже НОВЫЙ ролик через доли секунды после свайпа. Если да — решение надо
принимать по нему, а не по потоку. Только TikTok, только свайпы.
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import adb, config, human, vision

w, h = adb.screen_size()
costs, verdicts = [], []
print("экран %dx%d, порог «тот же кадр» %.1f\n" % (w, h, config.FEED_SAME_LIMIT))

for n in range(1, 5):
    t = time.time(); before = adb.exec_out("screencap -p", timeout=20)
    costs.append(time.time() - t)
    sig_before = vision.frame_signature(before)

    human.scroll_feed(w, h, "up", quick=True)      # пауза 0.05-0.15с, как в бою

    t = time.time(); after = adb.exec_out("screencap -p", timeout=20)
    cost_after = time.time() - t
    costs.append(cost_after)
    d_new = vision.frames_differ(sig_before, vision.frame_signature(after))

    # Через 3с ролик точно новый: сравним с ним — если снимок сразу после
    # свайпа уже показывал его, разойдутся они только движением картинки.
    time.sleep(3.0)
    settled = adb.exec_out("screencap -p", timeout=20)
    d_same = vision.frames_differ(vision.frame_signature(after),
                                  vision.frame_signature(settled))

    fresh = d_new >= config.FEED_SAME_LIMIT
    verdicts.append(fresh)
    print("свайп %d: снимок за %.2fс | против ПРЕДЫДУЩЕГО %.0f (%s) "
          "| против устоявшегося %.0f"
          % (n, cost_after, d_new, "новый ролик" if fresh else "СТАРЫЙ", d_same))

print("\nснимок экрана стоит %.2f-%.2fс (среднее %.2f)"
      % (min(costs), max(costs), sum(costs)/len(costs)))
print("показывал новый ролик сразу: %d из %d" % (sum(verdicts), len(verdicts)))
