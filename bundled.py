# -*- coding: utf-8 -*-
"""Всё встроенное в exe: распаковать туда, откуда программа это возьмёт.

Собранный PhoneAgent.exe — один файл, как у нормальных программ. Внутри у
него лежат adb, scrcpy и настройки по умолчанию, а работать программе нужно
с обычными файлами на диске. Этот модуль при запуске раскладывает их в
рабочую папку (`config.BASE`, в сборке это `%LOCALAPPDATA%\\PhoneAgent`).

Почему adb НЕЛЬЗЯ запускать прямо из распаковки PyInstaller: она лежит во
временной папке и стирается при выходе из программы, а сервер adb живёт
дольше неё. Запущенный оттуда сервер держит свой exe, временная папка не
удаляется, а следующий запуск распаковывает новую рядом — и так копятся
десятки мегабайт мусора. Из постоянной папки сервер работает как обычно.

Здесь НЕЛЬЗЯ импортировать config: модуль зовётся из самого config, до того
как тот посчитает путь к adb.
"""
import os
import shutil
import subprocess
import sys

# Настройки, которые человек может править. Кладутся, только если их ещё
# нет: правку человека новая версия программы затирать не должна.
DEFAULTS = ("recipes.json", "interests.json", "plan.json", "ADBKeyboard.apk")

# А это часть программы, не настройка: должно совпадать со сборкой. Иначе
# после обновления exe окно открывало бы инструкцию от прошлой версии.
FOLLOW_BUILD = ("ИНСТРУКЦИЯ.html",)


def _inside():
    return getattr(sys, "_MEIPASS", "")


def _same_build(tools, stamp):
    """Инструменты в рабочей папке — от этой же сборки?"""
    try:
        with open(os.path.join(tools, ".build"), encoding="utf-8") as f:
            return f.read().strip() == stamp
    except OSError:
        return False


def _stop_old_adb(tools):
    """Погасить сервер adb прежней версии, иначе его exe не перезаписать."""
    adb = os.path.join(tools, "platform-tools", "adb.exe")
    if os.path.exists(adb):
        try:
            subprocess.run([adb, "kill-server"], capture_output=True, timeout=15,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            pass


MARK = "install.json"     # что было на машине до первого запуска


def remember_first_run(base):
    """При ПЕРВОМ запуске записать, что на машине уже было до программы.

    Нужно для удаления без следа. adb держит ключ авторизации телефона в
    `%USERPROFILE%\\.android`, а журнал — в `%TEMP%\\adb.log`, и перенести их
    нельзя: проверено, ни ANDROID_USER_HOME, ни ANDROID_SDK_HOME, ни подмена
    USERPROFILE этот adb не слушает. Стирать их при удалении можно, только
    если их создала сама программа: у кого стоит Android Studio или свой adb,
    тот иначе потерял бы доступ к телефону во всех остальных программах.
    """
    path = os.path.join(base, MARK)
    if os.path.exists(path):
        return
    import json
    import time

    home = os.path.expanduser("~")
    temp = os.environ.get("TEMP") or ""
    info = {
        "first_run": time.strftime("%Y-%m-%d %H:%M:%S"),
        "android_dir_existed": os.path.isdir(os.path.join(home, ".android")),
        "adb_log_existed": bool(temp) and os.path.exists(os.path.join(temp, "adb.log")),
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def is_ours(folder):
    """Это распаковка PhoneAgent? Узнаём по своим файлам, а не по имени:
    `_MEI…` создаёт любая программа, собранная PyInstaller."""
    return (os.path.exists(os.path.join(folder, "webui.html"))
            and os.path.exists(os.path.join(folder, "build.txt")))


def pids(image=None):
    """PID-ы запущенных процессов; с `image` — только с таким именем exe.

    Вывод tasklist читаем байтами: имена чужих процессов бывают в любой
    кодировке, а нам из строки нужен только номер."""
    cmd = ["tasklist", "/FO", "CSV", "/NH"]
    if image:
        cmd += ["/FI", f"IMAGENAME eq {image}"]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=20,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return None                     # не знаем — пусть вызывающий осторожничает
    found = []
    for line in out.decode("latin-1").splitlines():
        parts = line.split('","')
        if len(parts) > 1 and parts[1].strip('"').isdigit():
            found.append(int(parts[1].strip('"')))
    return found


def _owner_alive(name, live):
    """Жив ли процесс, создавший папку `_MEI<pid><n>`?

    Загрузчик PyInstaller называет распаковку по своему PID с добавочной
    цифрой, а сам живёт, пока работает программа. Номер — начало имени, но
    где он кончается, не видно, поэтому сверяем все живые PID как начало."""
    digits = name[4:]
    return any(digits.startswith(str(p)) for p in live)


def _dll_loaded(folder):
    """Держит ли кто-то DLL Python из этой папки. Удалить загруженную DLL
    Windows не даёт; а если удалилась — папка всё равно шла под снос.
    Первой пробуем основную (python314.dll), а не короткую python3.dll: та
    может и не быть загружена, и её удаление ничего бы не доказало."""
    dlls = [e for e in os.listdir(folder)
            if e.lower().startswith("python3") and e.lower().endswith(".dll")]
    for entry in sorted(dlls, key=len, reverse=True):
        try:
            os.remove(os.path.join(folder, entry))
        except FileNotFoundError:
            pass
        except OSError:
            return True
    return False


def sweep_stale(keep=""):
    """Убрать распаковки прошлых запусков, оставшиеся от аварийных закрытий.

    Onefile-exe при выходе стирает свою временную папку сам, но если процесс
    убили (диспетчер задач, выключение ПК), она остаётся — 70 МБ за раз.

    ГЛАВНОЕ — не тронуть распаковку ЖИВОГО экземпляра. `rmtree` с
    `ignore_errors` удалил бы из неё всё незанятое — например webui.html, — и
    работающая программа сломалась бы. Проверено 2026-09-29: признак «папку
    с загруженной DLL не переименовать» НЕ работает — Windows переименовала
    папку живого экземпляра, снос выпотрошил её (осталось 19 файлов из 140),
    и тот не смог завершиться сам. Поэтому две проверки: жив ли процесс, чей
    PID в имени папки, и удаляется ли её DLL Python.
    """
    temp = os.environ.get("TEMP") or ""
    if not temp or not os.path.isdir(temp):
        return
    live = pids()
    if not live:
        return                          # не видим процессы — не рискуем
    for name in os.listdir(temp):
        folder = os.path.join(temp, name)
        if name.startswith("_MEI") and name.endswith(".phoneagent-old"):
            shutil.rmtree(folder, ignore_errors=True)   # недоудалённое в прошлый раз
            continue
        if (not name.startswith("_MEI") or not name[4:].isdigit()
                or os.path.normcase(folder) == os.path.normcase(keep)
                or not is_ours(folder) or _owner_alive(name, live)):
            continue
        try:
            if _dll_loaded(folder):
                continue
            doomed = folder + ".phoneagent-old"
            os.rename(folder, doomed)
        except OSError:
            continue
        shutil.rmtree(doomed, ignore_errors=True)


# Модули на своих DLL из распаковки. Грузятся ДО сброса пути поиска DLL
# (`quiet_children`): так им гарантированно найдётся всё своё.
NATIVE = ("_ctypes", "_socket", "select", "_ssl", "_hashlib", "_sqlite3",
          "_tkinter", "_lzma", "_bz2", "pyexpat", "_decimal", "_queue",
          "_overlapped", "_asyncio", "unicodedata", "_elementtree")


def quiet_children():
    """Дочерние программы exe — без окон и без НАШИХ DLL.

    1. Окна. У exe нет консоли (`--windowed`), и Windows на КАЖДЫЙ запуск
       консольной программы — adb, lms, ffmpeg, tasklist — открывает новое
       окно консоли поверх всех. adb зовётся десятки раз в минуту, и работать
       за компьютером становится невозможно (жалоба с чужого ПК 2026-09-29:
       «похоже на вирус»). Проверено: за 40 с работы — три окна. Поэтому
       всякий запуск без явных флагов получает CREATE_NO_WINDOW. Окна
       обычных программ (Chrome, scrcpy) этот флаг не прячет.
    2. DLL. Загрузчик PyInstaller зовёт SetDllDirectory(распаковка), и это
       НАСЛЕДУЮТ дочерние процессы (проверено на Python: ребёнок видит тот же
       путь). Сервер adb, Chrome, lms искали DLL сначала в нашей временной
       папке; переживший программу держал её VCRUNTIME140.dll, и на выходе
       загрузчик показывал «Failed to remove temporary directory», а папка
       оставалась мусором (чужой ПК, 2026-09-29).
    """
    if os.name != "nt":
        return
    import ctypes

    for name in NATIVE:
        try:
            __import__(name)
        except Exception:
            pass
    try:
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    except (AttributeError, OSError):
        pass

    if getattr(subprocess.Popen, "_quiet", False):
        return
    loud = (getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10)
            | getattr(subprocess, "DETACHED_PROCESS", 0x8))
    hide = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

    class QuietPopen(subprocess.Popen):
        _quiet = True

        def __init__(self, *args, **kwargs):
            flags = kwargs.get("creationflags") or 0
            if not flags & loud:            # свою консоль просили явно — не мешаем
                kwargs["creationflags"] = flags | hide
            super().__init__(*args, **kwargs)

    # `subprocess.run` и прочие берут Popen из модуля — подмена ловит всех.
    subprocess.Popen = QuietPopen


def unpack(base):
    """Разложить встроенное в `base`. Без сборки (из исходников) — ничего."""
    src = _inside()
    if not src:
        return
    quiet_children()                # до первого же запуска чего-либо
    os.makedirs(base, exist_ok=True)
    remember_first_run(base)
    sweep_stale(keep=src)

    for name in DEFAULTS:
        packed = os.path.join(src, "defaults", name)
        target = os.path.join(base, name)
        if os.path.exists(packed) and not os.path.exists(target):
            try:
                shutil.copy2(packed, target)
            except OSError:
                pass
    for name in FOLLOW_BUILD:
        packed = os.path.join(src, "defaults", name)
        target = os.path.join(base, name)
        try:
            with open(packed, "rb") as f:
                fresh = f.read()
            old = b""
            if os.path.exists(target):
                with open(target, "rb") as f:
                    old = f.read()
            if old != fresh:
                with open(target, "wb") as f:
                    f.write(fresh)
        except OSError:
            pass

    packed_tools = os.path.join(src, "tools")
    if not os.path.isdir(packed_tools):
        return
    try:
        with open(os.path.join(src, "build.txt"), encoding="utf-8") as f:
            stamp = f.read().strip()
    except OSError:
        stamp = "?"

    tools = os.path.join(base, "tools")
    if _same_build(tools, stamp):
        return                      # уже разложено этой версией — не трогаем

    _stop_old_adb(tools)
    for entry in os.listdir(packed_tools):
        target = os.path.join(tools, entry)
        try:
            if os.path.isdir(target):
                shutil.rmtree(target)
            shutil.copytree(os.path.join(packed_tools, entry), target)
        except OSError:
            # Не вышло (антивирус держит файл) — старая копия, скорее всего,
            # рабочая. Отметку не ставим: в следующий раз попробуем ещё.
            return
    with open(os.path.join(tools, ".build"), "w", encoding="utf-8") as f:
        f.write(stamp)
