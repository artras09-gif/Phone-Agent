"""Проверка надзирателя: запуск, журнал, кооперативная остановка, перезапуск.

Настоящую службу тут запускать НЕЛЬЗЯ: она поведёт живой телефон и может
опубликовать то, что лежит в очереди (DRY_RUN снят). Поэтому подменяем
команду запуска на подставную службу, которая ведёт себя так же: печатает
строки, ждёт файл-просьбу и выходит сама.

Проверяется именно механика надзирателя, а не поведение агента.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import fleet  # noqa: E402

STUB = os.path.join(HERE, "stub_serve.py")

# Подставная служба: печатает по строке в секунду, слушает просьбу выйти
# ровно тем же способом, что настоящая (fleet.install_child_stopper).
with open(STUB, "w", encoding="utf-8", newline="\n") as f:
    f.write(f'''import os, sys, time
sys.path.insert(0, {ROOT!r})
import fleet
serial = os.environ.get("ANDROID_SERIAL", "?")
should_stop = fleet.install_child_stopper(serial)
print(f"подставная служба поднялась для {{serial}}", flush=True)
n = 0
while not should_stop():
    n += 1
    print(f"круг {{n}} по-русски: проверка кодировки", flush=True)
    time.sleep(0.4)
print("выхожу по просьбе", flush=True)
''')

SERIAL_A = "СТЕНД-A"
SERIAL_B = "СТЕНД-B"

fleet.child_command = lambda serial: [sys.executable, STUB]

ok = True


def say(good, text):
    global ok
    ok &= good
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


# --- запуск двух служб одновременно ----------------------------------
print("--- две службы разом ---")
say(fleet.FLEET.start(SERIAL_A), "первая запустилась")
say(fleet.FLEET.start(SERIAL_B), "вторая запустилась")
time.sleep(2.0)

state = {s["serial"]: s for s in fleet.FLEET.state()}
say(state[SERIAL_A]["running"] and state[SERIAL_B]["running"],
    "обе работают одновременно")
say(state[SERIAL_A]["pid"] != state[SERIAL_B]["pid"], "процессы разные")

# --- журнал ----------------------------------------------------------
print("\n--- журнал ---")
total_a, lines_a = fleet.FLEET.service(SERIAL_A).tail(0)
say(total_a > 1, f"строки собираются ({total_a} шт.)")
say(any("по-русски" in ln for ln in lines_a), "кириллица не побилась")
say(any(SERIAL_A in ln for ln in lines_a), "служба видит свой серийник")

log_file = fleet.log_path(SERIAL_A)
say(os.path.exists(log_file), f"файл журнала пишется ({os.path.basename(log_file)})")

# --- замок на телефон ------------------------------------------------
print("\n--- замок на телефон ---")
say(fleet.busy_elsewhere(SERIAL_A) is False,
    "подставная службу замок не берёт (его берёт настоящий serve)")

# --- кооперативная остановка -----------------------------------------
print("\n--- остановка ---")
t0 = time.time()
graceful = fleet.FLEET.stop(SERIAL_A, timeout=15)
took = time.time() - t0
say(graceful, f"вышла сама, без убийства (за {took:.1f} с)")
say(took < 5, "уложилась быстро")

_, lines_a = fleet.FLEET.service(SERIAL_A).tail(0)
say(any("выхожу по просьбе" in ln for ln in lines_a),
    "успела попрощаться — значит выход был штатным")
say(not os.path.exists(fleet.stop_path(SERIAL_A)),
    "файл-просьба убран за собой")

state = {s["serial"]: s for s in fleet.FLEET.state()}
say(not state[SERIAL_A]["running"], "первая остановлена")
say(state[SERIAL_B]["running"], "вторая при этом ЖИВА (остановка не задела соседа)")

# --- остановка всех --------------------------------------------------
print("\n--- остановить все ---")
t0 = time.time()
fleet.FLEET.stop_all(timeout=15)
say(not fleet.FLEET.running(), f"все остановлены (за {time.time() - t0:.1f} с)")

try:
    os.remove(STUB)
except OSError:
    pass

print("\nИТОГ:", "надзиратель работает" if ok else "ЕСТЬ ПРОВАЛЫ")
sys.exit(0 if ok else 1)
