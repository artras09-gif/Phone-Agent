r"""Сборка PhoneAgent в ОДИН exe-файл — как у нормальных программ.

    python build_exe.py

На выходе: Desktop\PhoneAgent.exe. Больше ничего: ни папки tools рядом, ни
файлов настроек, ни Python на той машине. Двойной щелчок — открывается окно.

Что лежит внутри и куда девается при запуске:

* `webui.html` — часть программы, читается прямо из распаковки
  (`webui._base_dir`, он же `sys._MEIPASS`).
* adb и scrcpy — внутри exe, при первом запуске `bundled.unpack` кладёт их в
  `%LOCALAPPDATA%\PhoneAgent\tools`. Прямо из временной распаковки adb
  запускать нельзя: его сервер переживает программу и держит папку.
* `recipes.json`, `interests.json`, `plan.json`, `ADBKeyboard.apk` — там же,
  и только если их ещё нет: правку человека новая версия не затирает.
* Нажитое (`jobs.db`, логи, кадры, настройки с ключом) — тоже в
  `%LOCALAPPDATA%\PhoneAgent`. Рядом с exe не появляется ничего.

Окно консоли не показывается (`--windowed`). Команды из cmd при этом
работают — `PhoneAgent.exe doctor` печатает в тот же cmd (`main._console_for_exe`).
Закрыл окно — программа завершается, идущую сессию перед этим останавливает.

Нужен PyInstaller — единственная зависимость проекта, и та только для сборки.
"""
import os
import shutil
import subprocess
import sys
import time

SRC = os.path.dirname(os.path.abspath(__file__))
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")
OUT = os.path.join(DESKTOP, "PhoneAgent.exe")
WORK = os.path.join(os.environ.get("TEMP", SRC), "phoneagent-build")

# Где брать adb и scrcpy для вшивания: своя среда проекта, потом ~/tools.
TOOL_ROOTS = (os.path.join(SRC, "env", "tools"),
              os.path.join(os.path.expanduser("~"), "tools"))

# Настройки по умолчанию. telegram.json и settings.json сюда НЕ попадают:
# в них токен бота и ключ сервиса, а exe уезжает к другому человеку.
DEFAULTS = ("recipes.json", "interests.json", "plan.json", "ADBKeyboard.apk",
            "ИНСТРУКЦИЯ.html")

# Из platform-tools adb нужны только эти. Остальное (fastboot, sqlite3,
# mke2fs...) — ещё 10 МБ, которые распаковывались бы на каждом запуске.
ADB_FILES = ("adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll",
             "libwinpthread-1.dll")


def find_tool(name):
    for root in TOOL_ROOTS:
        if not os.path.isdir(root):
            continue
        if name == "adb":
            path = os.path.join(root, "platform-tools")
            if os.path.exists(os.path.join(path, "adb.exe")):
                return path
        else:
            for entry in sorted(os.listdir(root), reverse=True):
                path = os.path.join(root, entry)
                if os.path.exists(os.path.join(path, "scrcpy.exe")):
                    return path
    return None


def data(src, dest):
    # Абсолютный путь обязателен: относительный PyInstaller считает от папки
    # со spec-файлом, а она у нас во временной.
    return [(os.path.abspath(src), dest)]


# Что выкинуть из Tcl/Tk. Замер 2026-09-29: exe распаковывает при КАЖДОМ
# запуске все свои файлы во временную папку, и их было 985 — из них 928 от
# Tcl/Tk. Антивирус проверяет каждый, и пустой `--help` стартовал 28.8 с
# против 1.3 с из исходников. Сама программа берёт от Tk только PhotoImage
# (ужать снимок экрана) — часовые пояса, переводы календаря и картинки
# диалогов ей не нужны. ttk НЕ трогаем: tk.tcl подгружает его при старте.
TCL_DROP = ("_tcl_data/tzdata/", "_tcl_data/msgs/", "_tk_data/msgs/",
            "_tk_data/images/", "_tcl_data/encoding/")
# Из кодировок оставляем только те, что бывают системными на русской Windows:
# без своей кодировки Tcl не падает, но выдаёт предупреждения в журнал.
TCL_KEEP_ENC = ("cp1251.enc", "cp1252.enc", "cp866.enc", "cp437.enc")

SPEC = r'''# сгенерировано build_exe.py — не править руками
import os

TCL_DROP = {drop!r}
TCL_KEEP_ENC = {keep!r}


def keep(dest):
    dest = dest.replace(os.sep, "/")
    if dest.startswith("_tcl_data/encoding/"):
        return os.path.basename(dest) in TCL_KEEP_ENC
    return not any(dest.startswith(p) for p in TCL_DROP)


a = Analysis([{main!r}], pathex=[{src!r}], datas={datas!r},
             hiddenimports=["bundled"], excludes=[], noarchive=False)
dropped = [d for d in a.datas if not keep(d[0])]
a.datas = [d for d in a.datas if keep(d[0])]
print(f"[build_exe] из Tcl/Tk выкинуто файлов: {{len(dropped)}}")
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [],
          name="PhoneAgent", console=False, icon={icon!r},
          upx=False, runtime_tmpdir=None)
'''


def run_pyinstaller():
    if os.path.exists(WORK):
        shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK, exist_ok=True)

    icon = os.path.join(SRC, "icon.ico")
    if not os.path.exists(icon):
        print("  [i] иконки нет, рисую (python make_icon.py)")
        subprocess.run([sys.executable, os.path.join(SRC, "make_icon.py")],
                       cwd=SRC, capture_output=True)

    # Отметка сборки: по ней распаковщик понимает, что инструменты в рабочей
    # папке от прежней версии и их пора обновить.
    stamp = os.path.join(WORK, "build.txt")
    with open(stamp, "w", encoding="utf-8") as f:
        f.write(time.strftime("%Y%m%d-%H%M%S"))

    extra = data(os.path.join(SRC, "webui.html"), ".") + data(stamp, ".")
    for name in DEFAULTS:
        path = os.path.join(SRC, name)
        if os.path.exists(path):
            extra += data(path, "defaults")
        else:
            print(f"  [i] нет {name}, пропускаю")

    adb = find_tool("adb")
    if not adb:
        raise SystemExit("нет adb (platform-tools) — без него собирать нечего.\n"
                         "Положи в ~/tools или запусти: python env.py")
    for name in ADB_FILES:
        path = os.path.join(adb, name)
        if os.path.exists(path):
            extra += data(path, "tools/platform-tools")

    scrcpy = find_tool("scrcpy")
    if scrcpy:
        extra += data(scrcpy, f"tools/{os.path.basename(scrcpy)}")
    else:
        print("  [!] нет scrcpy — в сборке не будет трансляции экрана")

    spec = os.path.join(WORK, "PhoneAgent.spec")
    with open(spec, "w", encoding="utf-8") as f:
        f.write(SPEC.format(drop=TCL_DROP, keep=TCL_KEEP_ENC,
                            main=os.path.join(SRC, "main.py"), src=SRC,
                            datas=extra,
                            icon=icon if os.path.exists(icon) else None))

    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm",
        "--distpath", os.path.join(WORK, "dist"),
        "--workpath", os.path.join(WORK, "build"),
        spec,
    ]
    print("собираю exe...")
    result = subprocess.run(cmd, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    if result.returncode != 0:
        print(result.stdout[-3000:])
        print(result.stderr[-3000:])
        raise SystemExit("PyInstaller не справился")
    for line in (result.stdout + result.stderr).splitlines():
        if "[build_exe]" in line:
            print("  " + line.split("[build_exe]", 1)[1].strip())

    exe = os.path.join(WORK, "dist", "PhoneAgent.exe")
    if not os.path.exists(exe):
        raise SystemExit("exe не появился")
    return exe


if __name__ == "__main__":
    built = run_pyinstaller()
    shutil.copy2(built, OUT)
    print(f"\nготово: {OUT}  ({os.path.getsize(OUT) / 1024 / 1024:.0f} МБ)")
