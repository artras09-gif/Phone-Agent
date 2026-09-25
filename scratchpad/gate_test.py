"""Проверка пропускника НА НАСТОЯЩИХ ПРОЦЕССАХ.

Потоками такое не проверяется: замок берётся средствами ОС и в одном
процессе ведёт себя иначе. Поэтому дети запускаются через subprocess.

Что проверяем:
  1. при одном слоте работы НЕ пересекаются по времени;
  2. при двух слотах пересекаются, но не больше чем вдвоём;
  3. убитый насмерть держатель слот НЕ уносит с собой;
  4. перевзятие внутри себя не приводит к самозапиранию.
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import gate  # noqa: E402

GATES = os.path.join(HERE, ".gates_test")

CHILD = r'''
import os, sys, time
sys.path.insert(0, {root!r})
import gate
g = gate.Gate("t", {slots}, {gates!r})
g.acquire()
print("IN %.3f" % time.time(), flush=True)
time.sleep({hold})
print("OUT %.3f" % time.time(), flush=True)
g.release()
'''


def run(n, slots, hold=0.4):
    kids = []
    for _ in range(n):
        code = CHILD.format(root=ROOT, slots=slots, gates=GATES, hold=hold)
        kids.append(subprocess.Popen([sys.executable, "-c", code],
                                     stdout=subprocess.PIPE, text=True))
    spans = []
    for k in kids:
        out, _ = k.communicate(timeout=60)
        lines = out.split()
        spans.append((float(lines[1]), float(lines[3])))
    return sorted(spans)


def overlap_max(spans):
    """Наибольшее число одновременно работавших."""
    edges = []
    for a, b in spans:
        edges.append((a, 1))
        edges.append((b, -1))
    edges.sort()
    cur = best = 0
    for _, d in edges:
        cur += d
        best = max(best, cur)
    return best


def clean():
    if os.path.isdir(GATES):
        for f in os.listdir(GATES):
            try:
                os.remove(os.path.join(GATES, f))
            except OSError:
                pass


ok = True

# --- 1. один слот: строго по очереди ---------------------------------
clean()
spans = run(3, slots=1)
n = overlap_max(spans)
print(f"1 слот, 3 процесса: одновременно максимум {n}")
if n != 1:
    print("  ПРОВАЛ: работы пересеклись, хотя слот один")
    ok = False

# --- 2. два слота: пересекаются, но не втроём -------------------------
clean()
spans = run(4, slots=2)
n = overlap_max(spans)
print(f"2 слота, 4 процесса: одновременно максимум {n}")
if n != 2:
    print(f"  ПРОВАЛ: ожидали ровно 2, получили {n}")
    ok = False

# --- 3. убитый процесс не уносит слот ---------------------------------
clean()
hog = subprocess.Popen(
    [sys.executable, "-c", CHILD.format(root=ROOT, slots=1, gates=GATES, hold=30)],
    stdout=subprocess.PIPE, text=True)
time.sleep(1.5)                     # дать ему занять слот
hog.kill()
hog.wait(timeout=10)
t0 = time.time()
g = gate.Gate("t", 1, GATES)
try:
    g.acquire(timeout=10)
    print(f"после убийства держателя слот взят за {time.time() - t0:.2f} с")
    g.release()
except gate.Timeout:
    print("  ПРОВАЛ: слот остался занят навсегда")
    ok = False

# --- 4. перевзятие внутри себя ----------------------------------------
clean()
g = gate.Gate("t", 1, GATES)
try:
    g.acquire(timeout=5)
    g.acquire(timeout=5)            # вложенный вызов — не должен запереть себя
    g.release()
    g.release()
    print("перевзятие внутри себя: не заперлось")
except gate.Timeout:
    print("  ПРОВАЛ: процесс встал в очередь сам за собой")
    ok = False

# --- 5. slots=0 не ограничивает ---------------------------------------
clean()
spans = run(3, slots=0, hold=0.4)
n = overlap_max(spans)
print(f"без ограничения, 3 процесса: одновременно максимум {n}")
if n != 3:
    print(f"  ПРОВАЛ: ожидали 3, получили {n}")
    ok = False

clean()
print("\nИТОГ:", "всё сошлось" if ok else "ЕСТЬ ПРОВАЛЫ")
sys.exit(0 if ok else 1)
