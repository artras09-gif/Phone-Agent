"""Проверка починки «more than one device/emulator».

Двух настоящих телефонов нет, поэтому подменяем сам adb: поддельный отвечает
ровно так же, как настоящий — ошибкой, если устройств несколько, а `-s` не
передан, и успехом, если передан.

Воспроизводится именно тот случай с рабочего ПК: процесс начал работу, когда
телефона не было (или он был один), закрепления не случилось, а ко времени
сессии подключился второй аппарат.
"""
import os
import subprocess
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import adb  # noqa: E402
import config  # noqa: E402

USB, NET = "74eed241", "10.40.223.172:5555"

ok = True
calls = []


def say(good, text):
    global ok
    ok &= good
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


def fake_run(cmd, **kw):
    """Поддельный adb: ведёт себя как настоящий при нескольких устройствах."""
    calls.append(cmd)
    args = cmd[1:]
    serial = None
    if len(args) >= 2 and args[0] == "-s":
        serial, args = args[1], args[2:]

    if args and args[0] == "devices":
        body = "List of devices attached\n"
        for s in ALIVE:
            body += f"{s}\tdevice\n"
        return types.SimpleNamespace(returncode=0, stdout=body.encode(), stderr=b"")

    if serial is None and len(ALIVE) > 1:
        return types.SimpleNamespace(
            returncode=1, stdout=b"",
            stderr=b"adb.exe: more than one device/emulator")

    return types.SimpleNamespace(returncode=0, stdout=b"OK\n", stderr=b"")


adb.subprocess = types.SimpleNamespace(
    run=fake_run, TimeoutExpired=subprocess.TimeoutExpired)

# --- 1. закрепление, когда телефон ОДИН -------------------------------
print("--- телефон один: закрепляем всё равно ---")
ALIVE = [USB]
config.SERIAL = None
adb.prefer(None)
say(adb.pin() is True, "pin() отработал")
say(adb._PREFERRED == USB, f"телефон закреплён сразу: {adb._PREFERRED}")
say(adb._base()[-2:] == ["-s", USB], "команды пойдут с -s")

# --- 2. тот самый случай: закрепления не было, появился второй --------
print("\n--- закрепления не было, подключился второй ---")
ALIVE = [USB, NET]
config.SERIAL = None
adb.prefer(None)                    # процесс стартовал без телефона
calls.clear()
try:
    out = adb.shell("input swipe 743 1957 698 1426 203")
    say(True, "команда прошла (а раньше была бы ошибка)")
except adb.AdbError as e:
    say(False, f"упало: {e}")

say(adb._PREFERRED == USB, f"закрепился кабель, а не сеть: {adb._PREFERRED}")
first = [c for c in calls if "swipe" in " ".join(c)]
say(len(first) == 2, f"повтор был ровно один (вызовов swipe: {len(first)})")
say("-s" in first[-1], "повтор ушёл уже с -s")

# --- 3. дальше всё идёт без осечек ------------------------------------
print("\n--- следующие команды ---")
calls.clear()
adb.shell("dumpsys window")
adb.keyevent("KEYCODE_BACK")
retried = [c for c in calls if c.count("-s") == 0 and "devices" not in " ".join(c)]
say(not retried, "больше ни одна команда не идёт без -s")

# --- 4. закреплённый пропал — выбираем заново -------------------------
print("\n--- закреплённый телефон отключили ---")
ALIVE = [NET]
adb.pin()
say(adb._PREFERRED == NET, f"перезакрепился на живой: {adb._PREFERRED}")

# --- 5. явный выбор важнее догадок ------------------------------------
print("\n--- телефон выбран явно ---")
ALIVE = [USB, NET]
config.SERIAL = NET
adb.prefer(USB)
adb.pin()
say(adb._base()[-2:] == ["-s", NET], "config.SERIAL перебивает закрепление")

# --- 6. телефонов нет вообще ------------------------------------------
print("\n--- ни одного телефона ---")
ALIVE = []
config.SERIAL = None
adb.prefer(None)
say(adb.pin() is False, "pin() честно говорит «не за что закрепляться»")

print("\nИТОГ:", "починка работает" if ok else "ЕСТЬ ПРОВАЛЫ")
sys.exit(0 if ok else 1)
