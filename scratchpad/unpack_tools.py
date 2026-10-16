"""Раскладка инструментов из exe — надёжно (живьём 2026-10-16: нового
сервера Telegram не оказалось в tools, хотя он был в exe).

Раньше первая же неудача копирования (антивирус на секунду держит только что
распакованный файл) бросала всю раскладку, и следующие папки не доходили.
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import bundled  # noqa: E402

ok = True


def say(cond, text):
    global ok
    ok &= bool(cond)
    print(("  OK  " if cond else "  НЕТ ") + text)


bundled.time.sleep = lambda s: None
bundled.quiet_children = lambda: None
bundled.remember_first_run = lambda base: None
bundled.sweep_stale = lambda keep="": None
bundled._stop_old_adb = lambda tools: None


def fresh(stamp="NEW", have=("platform-tools",), old_stamp="NEW"):
    src = tempfile.mkdtemp(prefix="pa_mei_")
    base = tempfile.mkdtemp(prefix="pa_base_")
    for name in ("platform-tools", "scrcpy", "telegram-bot-api"):
        os.makedirs(os.path.join(src, "tools", name))
        open(os.path.join(src, "tools", name, "x.exe"), "w").write(stamp)
    open(os.path.join(src, "build.txt"), "w").write(stamp)
    os.makedirs(os.path.join(src, "defaults"))
    os.makedirs(os.path.join(base, "tools"))
    for name in have:
        os.makedirs(os.path.join(base, "tools", name))
        open(os.path.join(base, "tools", name, "x.exe"), "w").write("OLD")
    open(os.path.join(base, "tools", ".build"), "w").write(old_stamp)
    bundled._inside = lambda: src
    return src, base


def content(base, name):
    try:
        return open(os.path.join(base, "tools", name, "x.exe")).read()
    except OSError:
        return None


real_copytree = shutil.copytree

print("--- та же версия, но новой папки нет — докладываем только её ---")
src, base = fresh(have=("platform-tools", "scrcpy"))
bundled.unpack(base)
say(content(base, "telegram-bot-api") == "NEW", "сервер Telegram доложен")
say(content(base, "platform-tools") == "OLD", "уже разложенное этой версией не трогаем")

print("\n--- новая версия: файл на секунду занят — повтор ---")
src, base = fresh(have=("platform-tools", "scrcpy", "telegram-bot-api"), old_stamp="OLD")
fails = {"platform-tools": 1}


def flaky(a, b, *args, **kw):
    name = os.path.basename(b)
    if fails.get(name):
        fails[name] -= 1
        raise PermissionError(f"занято: {name}")
    return real_copytree(a, b, *args, **kw)


bundled.shutil.copytree = flaky
bundled.unpack(base)
say(all(content(base, n) == "NEW" for n in ("platform-tools", "scrcpy", "telegram-bot-api")),
    "после повтора разложено всё")
say(open(os.path.join(base, "tools", ".build")).read() == "NEW", "отметка версии поставлена")

print("\n--- одна папка не копируется совсем — остальные всё равно доходят ---")
src, base = fresh(have=(), old_stamp="OLD")
fails = {"platform-tools": 99}
bundled.unpack(base)
say(content(base, "scrcpy") == "NEW" and content(base, "telegram-bot-api") == "NEW",
    "scrcpy и сервер разложены, хотя adb не скопировался")
say(open(os.path.join(base, "tools", ".build")).read() == "OLD",
    "отметку не ставим — в следующий раз попробуем доложить adb")
fails = {}
bundled.unpack(base)
say(content(base, "platform-tools") == "NEW" and
    open(os.path.join(base, "tools", ".build")).read() == "NEW",
    "следующий запуск доложил и поставил отметку")
bundled.shutil.copytree = real_copytree

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
