"""Дочерние программы exe не открывают окон и не берут DLL из распаковки.

Жалобы с чужого ПК 2026-09-29: «постоянно вызывает cmd поверх всех окон —
похоже на вирус» и окно «Failed to remove temporary directory _MEI…» с
оставшимся VCRUNTIME140.dll. Здесь — как ведёт себя `bundled.quiet_children`:

  * ребёнок без явных флагов запускается без окна консоли;
  * путь поиска DLL, который ставит загрузчик PyInstaller, сброшен — и дети
    его больше не наследуют;
  * своё после сброса работает: https, база, Tk;
  * adb зовётся без окна и сам по себе, не только в exe.
"""
import ctypes
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


CHILD = ("import ctypes;k=ctypes.windll.kernel32;b=ctypes.create_unicode_buffer(600);"
         "k.GetDllDirectoryW(600,b);print(k.GetConsoleWindow(), repr(b.value))")

# Как у exe: загрузчик PyInstaller ставит путь поиска DLL в свою распаковку.
fake_mei = tempfile.mkdtemp(prefix="_MEI_fake_")
ctypes.windll.kernel32.SetDllDirectoryW(fake_mei)

print("--- до: так вёл себя exe ---")
before = subprocess.run([sys.executable, "-c", CHILD], capture_output=True, text=True).stdout.split()
# Окно консоли у ребёнка видно, только если у самого теста есть консоль
# (запуск из cmd — есть, из фоновой среды — нет), поэтому это не проверка,
# а справка. Сами окна exe считает `who_holds.ps1` по-настоящему.
print(f"  (справка) у ребёнка окно консоли: {before[:1]}")
say(fake_mei.replace("\\", "\\\\") in " ".join(before), "ребёнок унаследовал путь DLL в распаковку")

import bundled  # noqa: E402

bundled.quiet_children()
print("\n--- после quiet_children ---")
after = subprocess.run([sys.executable, "-c", CHILD], capture_output=True, text=True).stdout.split()
say(after and after[0] == "0", f"ребёнок без окна консоли (GetConsoleWindow = {after[:1]})")
say(after and after[-1] == "''", "путь DLL ребёнку не достался")
buf = ctypes.create_unicode_buffer(600)
ctypes.windll.kernel32.GetDllDirectoryW(600, buf)
say(buf.value == "", "и у самой программы сброшен")

out = subprocess.check_output([sys.executable, "-c", "print('вывод на месте')"],
                              text=True, encoding="utf-8")
say("вывод на месте" in out, "вывод ребёнка по-прежнему перехватывается")
bundled.quiet_children()
say(subprocess.Popen.__mro__[1].__name__ == "Popen", "повторный вызов не наматывает обёртки")

print("\n--- своё после сброса работает ---")
import sqlite3  # noqa: E402
import ssl      # noqa: E402
import tkinter  # noqa: E402

say(ssl.create_default_context() is not None, "https (ssl) поднимается")
con = sqlite3.connect(":memory:")
say(con.execute("select 1").fetchone() == (1,), "база (sqlite) работает")
say(tkinter.Tcl().eval("expr {2+2}") == "4", "Tk/Tcl работает")

print("\n--- adb без окна и из исходников ---")
import adb  # noqa: E402

seen = {}
real_run = subprocess.run


def spy(*args, **kwargs):
    seen.update(kwargs)
    return real_run([sys.executable, "-c", "print('List of devices attached')"],
                    capture_output=True)


subprocess.run = spy
try:
    adb.devices()
finally:
    subprocess.run = real_run
say(seen.get("creationflags", 0) & subprocess.CREATE_NO_WINDOW, "adb.raw просит запуск без окна")

os.rmdir(fake_mei)
print("\nВСЁ СОШЛОСЬ" if ok else "\nЕСТЬ ОШИБКИ")
sys.exit(0 if ok else 1)
