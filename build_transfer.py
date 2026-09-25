r"""Сборка переносимого комплекта PhoneAgent для другого ПК.

Запуск:  python build_transfer.py
На выходе: Desktop\PhoneAgent-transfer\ и одноимённый .zip.
В комплект кладутся исходники + adb + scrcpy; состояние (jobs.db, очередь,
логи, pin.txt) сознательно не переносится.
"""
import os, shutil, zipfile

SRC   = r"C:\Users\Admin\Desktop\PhoneAgent"
TOOLS = r"C:\Users\Admin\tools"
OUT   = r"C:\Users\Admin\Desktop\PhoneAgent-transfer"
ZIP   = r"C:\Users\Admin\Desktop\PhoneAgent-transfer.zip"

if os.path.exists(OUT):
    shutil.rmtree(OUT)
os.makedirs(OUT)

# ---------------------------------------------------------------- исходники
dst_app = os.path.join(OUT, "PhoneAgent")
os.makedirs(dst_app)
# .apk — ADBKeyboard едет в комплекте, чтобы новому ПК не нужен был интернет.
# .html — это НЕ документация: `webui.html` и есть окно приложения, `webui.py`
# читает его с диска при первом же открытии страницы и без него отвечает
# «нет webui.html». Раньше расширения тут не было, и комплект уезжал с
# неработающим окном. Заодно едут иконка и картинка для аватарки бота.
KEEP_EXT = (".py", ".json", ".bat", ".md", ".txt", ".apk", ".ps1",
            ".html", ".ico", ".png")
SKIP = {"screen.txt", "jobs.db", "STOP", "pin.txt", "build_transfer.py",
        "remote.txt", ".vision_model",
        # ЛИЧНОЕ — в комплект не едет никогда.
        # `settings.json` хранит ключ от облачного зрения, `telegram.json` —
        # токен бота. Комплект уезжает на другой ПК и к другому человеку;
        # уехавший токен даёт чужому полный доступ к боту, а ключ — к
        # оплаченной квоте. В `build_exe.BESIDE` их нет с самого начала,
        # а здесь недоставало: списки разошлись.
        #
        # Заодно `settings.json` увозит серийник ЭТОГО телефона, и на новом
        # ПК окно молча пыталось бы говорить с аппаратом, которого там нет.
        "settings.json", "telegram.json"}
copied = []
for name in sorted(os.listdir(SRC)):
    path = os.path.join(SRC, name)
    if os.path.isdir(path):
        continue
    if name in SKIP or not name.lower().endswith(KEEP_EXT):
        continue
    shutil.copy2(path, os.path.join(dst_app, name))
    copied.append(name)

# Проверки едут вместе с проектом: на новом ПК первым делом надо убедиться,
# что Python и зависимости встали правильно, а «python scratchpad\all_tests.py»
# отвечает на это за полминуты и без телефона. Берутся только .py — никакого
# состояния там и не бывает.
dst_tests = os.path.join(dst_app, "scratchpad")
os.makedirs(dst_tests, exist_ok=True)
tests = []
for name in sorted(os.listdir(os.path.join(SRC, "scratchpad"))):
    if name.lower().endswith(".py"):
        shutil.copy2(os.path.join(SRC, "scratchpad", name),
                     os.path.join(dst_tests, name))
        tests.append(name)

# пустые рабочие папки, чтобы структура была готова сразу
for d in ("watch", "queue", "logs", "frames"):
    os.makedirs(os.path.join(dst_app, d), exist_ok=True)
    with open(os.path.join(dst_app, d, ".keep"), "w") as f:
        f.write("")

# ------------------------------------------------------------------ tools
dst_tools = os.path.join(OUT, "tools")
for tool in ("platform-tools", "scrcpy-win64-v4.1"):
    shutil.copytree(os.path.join(TOOLS, tool), os.path.join(dst_tools, tool))

# ------------------------------------------------- патч меню: python -> %PY%
bat_path = os.path.join(dst_app, "PhoneAgent.bat")
with open(bat_path, encoding="cp866", newline="") as f:
    bat = f.read()
header = (
    'rem --- пути к зависимостям из комплекта, если запущено напрямую ---\r\n'
    'if not defined PY set "PY=python"\r\n'
    'if not defined ADB_PATH if exist "%~dp0..\\tools\\platform-tools\\adb.exe" '
    'set "ADB_PATH=%~dp0..\\tools\\platform-tools\\adb.exe"\r\n'
    'if not defined SCRCPY_PATH if exist "%~dp0..\\tools\\scrcpy-win64-v4.1\\scrcpy.exe" '
    'set "SCRCPY_PATH=%~dp0..\\tools\\scrcpy-win64-v4.1\\scrcpy.exe"\r\n'
)
bat = bat.replace('title PhoneAgent\r\n', 'title PhoneAgent\r\n\r\n' + header, 1)
bat = bat.replace('python main.py', '%PY% main.py')
assert 'python main.py' not in bat and '%PY% main.py' in bat and header in bat
with open(bat_path, "w", encoding="cp866", newline="") as f:
    f.write(bat)

# ------------------------------------------------------- ПРИЛОЖЕНИЕ.bat
# Единственный файл, который нужно запускать: открывает окно приложения.
# Установщиков в комплекте нет намеренно — ставится только Python, и об этом
# файл скажет сам, если его не найдёт.
#
# Латиницей внутри `echo` нарочно: сообщение об отсутствии Python читается в
# консоли с любой кодовой страницей, а до `chcp` дело может и не дойти.
START = """@echo off
chcp 866 >nul
setlocal
title PhoneAgent
set "ROOT=%~dp0"

rem --- зависимости лежат рядом, PATH трогать не надо ---
set "ADB_PATH=%ROOT%tools\\platform-tools\\adb.exe"
set "SCRCPY_PATH=%ROOT%tools\\scrcpy-win64-v4.1\\scrcpy.exe"

rem --- ищем Python ---
set "PY="
py -3 -c "import sys" >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if not defined PY (
  python -c "import sys" >nul 2>nul
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo.
  echo   Python ne naiden.
  echo   Postav ego s https://www.python.org/downloads/
  echo   i obyazatelno otmet galochku "Add python.exe to PATH".
  echo   Potom zapusti etot file zanovo.
  echo.
  pause
  exit /b 1
)

if not exist "%ADB_PATH%" (
  echo Ne naiden adb: "%ADB_PATH%"
  echo Papka tools dolzhna lezhat ryadom s etim failom.
  pause
  exit /b 1
)

cd /d "%ROOT%PhoneAgent"
%PY% main.py ui
if errorlevel 1 (
  echo.
  echo Okno ne otkrylos. Podrobnosti vyshe.
  pause
)
"""
with open(os.path.join(OUT, "ПРИЛОЖЕНИЕ.bat"), "w", encoding="cp866", newline="") as f:
    f.write(START.replace("\n", "\r\n"))

# Инструкция ложится наверх, рядом с кнопкой запуска: внутри папки с
# исходниками её никто не найдёт.
shutil.move(os.path.join(dst_app, "ИНСТРУКЦИЯ.html"),
            os.path.join(OUT, "ИНСТРУКЦИЯ.html"))

# ------------------------------------------- README: пути этой машины -> комплект
readme = os.path.join(dst_app, "README.md")
with open(readme, encoding="utf-8") as f:
    text = f.read()
REPL = [
    ("1. **ADB** уже лежит в `C:\\Users\\Admin\\tools\\platform-tools` и находится\n"
     "   автоматически. Если переставишь — задай `ADB_PATH`.",
     "1. **ADB** лежит в комплекте: папка `tools\\platform-tools` рядом с проектом.\n"
     "   Находится автоматически. Если переставишь — задай `ADB_PATH`."),
    ("Работает на **scrcpy** (лежит в `C:\\Users\\Admin\\tools\\scrcpy-win64-v4.1`,",
     "Работает на **scrcpy** (лежит в комплекте: `tools\\scrcpy-win64-v4.1`,"),
    ("C:\\Python314\\python.exe C:\\Users\\Admin\\Desktop\\PhoneAgent\\main.py serve\n"
     "```\n"
     "Рабочая папка — `C:\\Users\\Admin\\Desktop\\PhoneAgent`.",
     "<путь к python.exe> <куда распаковал>\\PhoneAgent\\main.py serve\n"
     "```\n"
     "Рабочая папка — папка `PhoneAgent`. adb и scrcpy найдутся сами,\n"
     "пока папка `tools` лежит рядом с ней."),
]
for old, new in REPL:
    assert old in text, old[:40]
    text = text.replace(old, new)
with open(readme, "w", encoding="utf-8", newline="\n") as f:
    f.write(text)

# ------------------------------------------------------------------- ZIP
if os.path.exists(ZIP):
    os.remove(ZIP)
with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as z:
    for root, dirs, files in os.walk(OUT):
        for name in files:
            full = os.path.join(root, name)
            z.write(full, os.path.relpath(full, os.path.dirname(OUT)))

size = os.path.getsize(ZIP) / 1024 / 1024
print("скопировано файлов проекта:", len(copied))
print(", ".join(copied))
print(f"zip: {ZIP}  {size:.1f} МБ")
