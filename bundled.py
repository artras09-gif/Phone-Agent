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
DEFAULTS = ("recipes.json", "interests.json", "plan.json",
            "ADBKeyboard.apk", "ИНСТРУКЦИЯ.html")


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


def unpack(base):
    """Разложить встроенное в `base`. Без сборки (из исходников) — ничего."""
    src = _inside()
    if not src:
        return
    os.makedirs(base, exist_ok=True)

    for name in DEFAULTS:
        packed = os.path.join(src, "defaults", name)
        target = os.path.join(base, name)
        if os.path.exists(packed) and not os.path.exists(target):
            try:
                shutil.copy2(packed, target)
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
