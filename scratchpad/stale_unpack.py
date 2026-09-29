"""Чистка забытых распаковок exe не трогает распаковку ЖИВОЙ программы.

Проверено 2026-09-29 на собранном exe: прежняя проверка («папку с
загруженной DLL Windows не переименует») не сработала — второй запуск
выпотрошил папку работающего экземпляра, и тот не смог выйти сам.

Здесь без сборки: во временной папке делаем три поддельные распаковки —
  * чей процесс жив (PID этого теста в имени),
  * чей процесс умер, а DLL из неё загружена (страховка второй проверкой),
  * просто забытую (процесс умер, DLL никто не держит),
и смотрим, что снесена только последняя. Плюс чужие _MEI — не наши.
"""
import ctypes
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import bundled  # noqa: E402

ok = True


def say(good, text):
    global ok
    ok &= good
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


def fake_unpack(temp, name, ours=True, dll=None):
    folder = os.path.join(temp, name)
    os.makedirs(os.path.join(folder, "tools"))
    with open(os.path.join(folder, "tools", "adb.exe"), "wb") as f:
        f.write(b"x" * 1024)
    if ours:
        for marker in ("webui.html", "build.txt"):
            with open(os.path.join(folder, marker), "w", encoding="utf-8") as f:
                f.write("x")
    if dll:
        shutil.copy2(dll, os.path.join(folder, os.path.basename(dll)))
    return folder


def dead_pid(live):
    """Номер, который НИ ОДИН живой PID не начинает (иначе тест врёт)."""
    for n in range(90000000, 90001000, 4):
        if not any(str(n).startswith(str(p)) for p in live):
            return n
    raise SystemExit("не нашёл свободный номер")


temp = tempfile.mkdtemp(prefix="pa_stale_")
os.environ["TEMP"] = temp
live = bundled.pids()
say(bool(live) and os.getpid() in live, f"tasklist видит процессы ({len(live or [])}), и этот тоже")

dll = os.path.join(sys.base_prefix, "python3.dll")
gone1, gone2 = dead_pid(live), dead_pid(live + [dead_pid(live)])

alive = fake_unpack(temp, f"_MEI{os.getpid()}2")
held = fake_unpack(temp, f"_MEI{gone1}2", dll=dll)
stale = fake_unpack(temp, f"_MEI{gone2}3")
foreign = fake_unpack(temp, f"_MEI{gone2}4", ours=False)
mine = fake_unpack(temp, f"_MEI{gone2}5")          # как будто своя распаковка
leftover = os.path.join(temp, f"_MEI{gone2}6.phoneagent-old")
os.makedirs(leftover)

handle = ctypes.WinDLL(os.path.join(held, "python3.dll"))   # «загружена программой»

bundled.sweep_stale(keep=mine)

say(os.path.exists(os.path.join(alive, "webui.html"))
    and os.path.exists(os.path.join(alive, "tools", "adb.exe")),
    "распаковка живого процесса цела до последнего файла")
say(os.path.exists(os.path.join(held, "python3.dll"))
    and os.path.exists(os.path.join(held, "webui.html")),
    "папку с загруженной DLL не тронули, хотя PID в имени мёртв")
say(not os.path.exists(stale), "забытая распаковка удалена")
say(not os.path.exists(stale + ".phoneagent-old"), "…и без хвоста .phoneagent-old")
say(os.path.exists(foreign), "чужая _MEI (не PhoneAgent) не тронута")
say(os.path.exists(mine), "своя текущая распаковка не тронута")
say(not os.path.exists(leftover), "недоудалённое в прошлый раз добрано")

# Процесс «умер» — DLL отпущена: следующая чистка забирает и эту папку.
ctypes.windll.kernel32.FreeLibrary(ctypes.c_void_p(handle._handle))
bundled.sweep_stale(keep=mine)
say(not os.path.exists(held), "DLL отпустили — папку сняли в следующий раз")

# Не видим процессов — не трогаем ничего.
real = bundled.pids
bundled.pids = lambda image=None: None
again = fake_unpack(temp, f"_MEI{gone2}7")
bundled.sweep_stale(keep=mine)
say(os.path.exists(again), "tasklist не ответил — ничего не удалено")
bundled.pids = lambda image=None: []
bundled.sweep_stale(keep=mine)
say(os.path.exists(again), "tasklist вернул пусто — тоже ничего")
bundled.pids = real

shutil.rmtree(temp, ignore_errors=True)
print("\nВСЁ СОШЛОСЬ" if ok else "\nЕСТЬ ОШИБКИ")
sys.exit(0 if ok else 1)
