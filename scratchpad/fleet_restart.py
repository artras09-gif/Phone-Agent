"""Проверка присмотра: упавшая служба поднимается, соседей это не задевает.

Проверяется именно то, ради чего цикл присмотра переписан со «сна на месте»
на отметку времени: пауза перед перезапуском растёт до пяти минут, и если
спать прямо в цикле, одна упавшая служба задержит перезапуск остальных.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import fleet  # noqa: E402

DIER = os.path.join(HERE, "stub_dier.py")     # падает сразу
LIVER = os.path.join(HERE, "stub_liver.py")   # живёт и слушает просьбу

with open(DIER, "w", encoding="utf-8", newline="\n") as f:
    f.write('import sys\nprint("падаю", flush=True)\nsys.exit(3)\n')

with open(LIVER, "w", encoding="utf-8", newline="\n") as f:
    f.write(f'''import os, sys, time
sys.path.insert(0, {ROOT!r})
import fleet
should_stop = fleet.install_child_stopper(os.environ.get("ANDROID_SERIAL", "?"))
print("живу", flush=True)
while not should_stop():
    time.sleep(0.2)
''')

BAD, GOOD = "СТЕНД-ПАДУН", "СТЕНД-ЖИВОЙ"
fleet.child_command = lambda serial: [
    sys.executable, DIER if serial == BAD else LIVER]

ok = True


def say(good, text):
    global ok
    ok &= good
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


fleet.FLEET.start(BAD)
fleet.FLEET.start(GOOD)
print(f"запущены обе; жду перезапуск (пауза {fleet.RESTART_DELAYS[0]} с, "
      f"опрос {fleet.Fleet.WATCH_EVERY} с)\n")

bad = fleet.FLEET.service(BAD)
good = fleet.FLEET.service(GOOD)

deadline = time.time() + 25
while time.time() < deadline and bad.restarts < 1:
    time.sleep(0.5)

took = 25 - (deadline - time.time())
say(bad.restarts >= 1, f"упавшая перезапущена (через {took:.0f} с)")
say(good.alive(), "живая соседка при этом не пострадала")

lines = bad.tail(0)[1]
say(any("завершилась" in ln for ln in lines), "падение отмечено в журнале")
say(any("код 3" in ln for ln in lines), "код выхода записан верно")

print("\n--- остановка снимает и присмотр ---")
bad.want_running = False
n = bad.restarts
time.sleep(fleet.Fleet.WATCH_EVERY + fleet.RESTART_DELAYS[0] + 3)
say(bad.restarts == n, "остановленную больше не поднимают")

fleet.FLEET.stop_all(timeout=15)
say(not fleet.FLEET.running(), "все остановлены")

for p in (DIER, LIVER):
    try:
        os.remove(p)
    except OSError:
        pass

print("\nИТОГ:", "присмотр работает" if ok else "ЕСТЬ ПРОВАЛЫ")
sys.exit(0 if ok else 1)
