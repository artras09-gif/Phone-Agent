"""«Слепой» сервер adb (2026-11-13): телефоны воткнуты, а `adb devices` пуст.

Так бывает, если сервер подняли раньше, чем прежний отпустил USB: после
обновления программа не видела ни одного телефона, и службы не поднимались.
Без телефона: команды adb подменены. Проверяется, что на время, пока USB
отпускается, порт сервера занят нами (соседние вызовы сервер не поднимут),
и что перезапуск — не чаще раза в минуту.
"""
import os
import socket
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import adb  # noqa: E402

ok = True


def check(text, good):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


probe = socket.socket()
probe.bind(("127.0.0.1", 0))
port = probe.getsockname()[1]
probe.close()

answers = []


def knock():
    """Соседний вызов adb: стучится в порт сервера посреди лечения."""
    time.sleep(0.5)
    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect(("127.0.0.1", port))
        answers.append(s.recv(4))                  # сразу закрыто — b""
    except OSError as e:
        answers.append(e)
    finally:
        s.close()


threading.Thread(target=knock, daemon=True).start()
started = time.time()
held = adb._hold_port(port, settle=1.5)
spent = time.time() - started
check(f"порт занят на время паузы: {spent:.1f} с", held and 1.4 < spent < 3.5)
check(f"соседний вызов получил отказ, а не сервер: {answers!r}", answers and answers[0] in (b"",)
      or (answers and isinstance(answers[0], OSError)))
s = socket.socket()
try:
    s.bind(("127.0.0.1", port))
    freed = True
except OSError:
    freed = False
s.close()
check("после паузы порт свободен — свой сервер поднимется", freed)

busy = socket.socket()
busy.bind(("127.0.0.1", port))
busy.listen(1)
check("порт уже занят чужим сервером — не держим, пробуем заново",
      adb._hold_port(port, settle=1.0, grab=0.5) is False)
busy.close()

calls = []
adb.raw = lambda *a, **k: calls.append(a[0]) or ""
adb._hold_port = lambda port, settle, grab=5.0: True
adb.time.sleep = lambda s: None
adb._HEALED["at"] = 0
check("телефонов не видно — сервер перезапущен", adb.heal_if_blind() is True
      and calls == ["kill-server", "start-server"])
calls.clear()
check("через секунду снова — не трогаем (раз в минуту)", adb.heal_if_blind() is False and not calls)

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
