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
import re
import shutil
import subprocess
import sys
import time
import zipfile

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
# Сценарии Windows во встроенное не кладём (см. scrcpy ниже).
SCRIPT_EXT = (".vbs", ".bat", ".cmd", ".ps1", ".js", ".wsf")


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


# Монтаж (montage.py) — ffmpeg и yt-dlp. 26.11 монтаж не начался: ffmpeg на
# ПК был без ffprobe, программа пошла его докачивать, а связи не было.
# Теперь оба вшиты: ffmpeg — zip-архивом (96 МБ -> 36 МБ; голым .exe он
# распаковывался бы во временную папку при КАЖДОМ запуске, и антивирус
# проверял бы его каждый раз), yt-dlp — как есть (3 МБ). Откуда берутся, по
# порядку: из прошлого релиза (сборка на GitHub кладёт их в env/tools), с
# этого ПК, и только если нет нигде — с сайтов, со сверкой контрольной суммы.
FFMPEG_ZIP_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/"


def _fetch(url, dest):
    import hashlib
    import urllib.request
    digest = hashlib.sha256()
    req = urllib.request.Request(url, headers={"User-Agent": "PhoneAgent-build"})
    with urllib.request.urlopen(req, timeout=600) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            digest.update(chunk)
            f.write(chunk)
    return digest.hexdigest()


def _text(url):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "PhoneAgent-build"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def pack_ffmpeg(work):
    """tools/ffmpeg/ffmpeg.zip с одним ffmpeg.exe. Путь к zip или None."""
    for root in TOOL_ROOTS:
        ready = os.path.join(root, "ffmpeg", "ffmpeg.zip")
        if os.path.exists(ready):
            return ready
    found = next((os.path.join(r, "ffmpeg", "ffmpeg.exe") for r in TOOL_ROOTS
                  if os.path.exists(os.path.join(r, "ffmpeg", "ffmpeg.exe"))), None)
    if found is None:
        sys.path.insert(0, SRC)
        try:
            import stream
            found = stream.ffmpeg_exe()
        except Exception:
            found = None
    os.makedirs(os.path.join(work, "ffmpeg"), exist_ok=True)
    out = os.path.join(work, "ffmpeg", "ffmpeg.zip")
    if found is None:
        print("  ffmpeg на этом ПК нет — беру сборку gyan.dev...")
        whole = os.path.join(work, "ffmpeg-full.zip")
        try:
            want = _text(FFMPEG_ZIP_URL + ".sha256").split()[0].lower()
            if _fetch(FFMPEG_ZIP_URL, whole) != want:
                raise OSError("контрольная сумма не сошлась")
            with zipfile.ZipFile(whole) as z:
                name = next(n for n in z.namelist() if n.endswith("/bin/ffmpeg.exe"))
                found = os.path.join(work, "ffmpeg.exe")
                with z.open(name) as src, open(found, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        except (OSError, StopIteration, zipfile.BadZipFile, IndexError) as e:
            print(f"  [!] ffmpeg не достал ({e}) — монтаж будет докачивать его сам")
            return None
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.write(found, "ffmpeg.exe")
    return out


def pack_ytdlp(work):
    """tools/yt-dlp/yt-dlp — сам код yt-dlp одним файлом. Путь или None."""
    for root in TOOL_ROOTS:
        ready = os.path.join(root, "yt-dlp", "yt-dlp")
        if os.path.exists(ready):
            return ready
    os.makedirs(os.path.join(work, "yt-dlp"), exist_ok=True)
    out = os.path.join(work, "yt-dlp", "yt-dlp")
    try:
        sums = _text(YTDLP_URL + "SHA2-256SUMS")
        want = next(line.split()[0].lower() for line in sums.splitlines()
                    if line.split()[-1:] == ["yt-dlp"])
        if _fetch(YTDLP_URL + "yt-dlp", out) != want:
            raise OSError("контрольная сумма не сошлась")
        return out
    except (OSError, StopIteration) as e:
        print(f"  [!] yt-dlp не достал ({e}) — монтаж скачает его сам при первой ссылке")
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
             hiddenimports={hidden!r}, excludes=[], noarchive=False)
dropped = [d for d in a.datas if not keep(d[0])]
a.datas = [d for d in a.datas if keep(d[0])]
print(f"[build_exe] из Tcl/Tk выкинуто файлов: {{len(dropped)}}")
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [],
          name="PhoneAgent", console=False, icon={icon!r},
          upx=False, runtime_tmpdir=None)
'''


def ytdlp_stdlib():
    """Модули стандартной библиотеки, которые импортирует yt-dlp.

    Монтажёр запускает yt-dlp (zip с кодом на Python) самим PhoneAgent.exe
    (`main._run_ytdlp`). PyInstaller кладёт в exe только те модули, что
    импортирует НАША программа, — а yt-dlp тянет http.cookiejar, xml.etree,
    email и прочее. Список берётся из самого zip: обновится yt-dlp —
    пересборка возьмёт и его новые импорты."""
    sys.path.insert(0, SRC)
    import linkfetch

    try:
        path, _ = linkfetch.ensure("stable-py")
    except linkfetch.FetchError as e:
        print(f"  [!] нет yt-dlp для разбора импортов ({e}) — монтаж по ссылкам в exe "
              "может не скачивать")
        return []
    names = set()
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if not name.endswith(".py"):
                continue
            code = z.read(name).decode("utf-8", "replace")
            for m in re.finditer(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))",
                                 code, re.M):
                module = m.group(1) or m.group(2)
                if module.split(".")[0] in sys.stdlib_module_names:
                    names.add(module)
    drop = ("tkinter", "idlelib", "turtle", "test", "lib2to3", "ensurepip", "venv")
    return sorted(n for n in names if n.split(".")[0] not in drop)


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
    # Шаблоны монтажа (`montage.py`) читаются прямо из распаковки, как
    # webui.html; свои шаблоны человек кладёт в %LOCALAPPDATA%\PhoneAgent\templates.
    templates = os.path.join(SRC, "templates")
    for root, _, files in os.walk(templates):
        for name in files:
            rel = os.path.relpath(root, SRC).replace(os.sep, "/")
            extra += data(os.path.join(root, name), rel)
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

    # Свой сервер Telegram Bot API (видео до 2 ГБ) — собран из официальных
    # исходников, лежит в ~/tools/telegram-bot-api. Нет — бот работает через
    # облако, как раньше (до 20 МБ).
    tgapi = next((os.path.join(r, "telegram-bot-api", "telegram-bot-api.exe")
                  for r in TOOL_ROOTS
                  if os.path.exists(os.path.join(r, "telegram-bot-api", "telegram-bot-api.exe"))),
                 None)
    if tgapi:
        # Вся папка: рядом с сервером лежат msvcp140/vcruntime140 — на чужом
        # ПК без пакета Visual C++ сервер без них не запустится.
        folder = os.path.dirname(tgapi)
        for entry in sorted(os.listdir(folder)):
            if entry.lower().endswith((".exe", ".dll")):
                extra += data(os.path.join(folder, entry), "tools/telegram-bot-api")
    else:
        print("  [!] нет telegram-bot-api.exe — бот будет брать видео только до 20 МБ")

    packs = os.path.join(WORK, "packs")
    zipped = pack_ffmpeg(packs)
    if zipped:
        extra += data(zipped, "tools/ffmpeg")
        print(f"  ffmpeg для монтажа: {os.path.getsize(zipped) // 2 ** 20} МБ в архиве")
    ytdlp = pack_ytdlp(packs)
    if ytdlp:
        extra += data(ytdlp, "tools/yt-dlp")

    scrcpy = find_tool("scrcpy")
    if scrcpy:
        # Пофайлово и без сценариев: в архиве scrcpy лежат scrcpy-noconsole.vbs
        # (скрыто запускает программу) и .bat. Программе они не нужны, а
        # скрытый запуск сценарием внутри exe — повод для антивируса
        # (30.10: «проверь безопасность встроенных программ»).
        dest = f"tools/{os.path.basename(scrcpy)}"
        for entry in sorted(os.listdir(scrcpy)):
            if os.path.isfile(os.path.join(scrcpy, entry)) and \
                    not entry.lower().endswith(SCRIPT_EXT):
                extra += data(os.path.join(scrcpy, entry), dest)
    else:
        print("  [!] нет scrcpy — в сборке не будет трансляции экрана")

    spec = os.path.join(WORK, "PhoneAgent.spec")
    with open(spec, "w", encoding="utf-8") as f:
        f.write(SPEC.format(drop=TCL_DROP, keep=TCL_KEEP_ENC,
                            main=os.path.join(SRC, "main.py"), src=SRC,
                            datas=extra, hidden=["bundled"] + ytdlp_stdlib(),
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
