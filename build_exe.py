r"""Сборка PhoneAgent в .exe для передачи на другой компьютер.

Отличие от `build_transfer.py`: там едут исходники и на том конце нужен
Python, здесь — готовое приложение, которому не нужно ничего.

Запуск:  python build_exe.py
На выходе: Desktop\PhoneAgent-exe\ и одноимённый .zip.

Что важно знать про сборку:

* `webui.html` уезжает ВНУТРЬ exe — это часть программы, а не настройка.
  Код ищет его через `sys._MEIPASS` (см. `webui._base_dir`).
* `recipes.json` наоборот кладётся РЯДОМ: маршруты публикации правятся
  руками, когда соцсеть меняет интерфейс, а внутрь exe не залезешь.
* `adb.exe` остаётся отдельным файлом в `tools\` — это чужая программа,
  запаковывать её в свой exe незачем.
* Нажитое (`jobs.db`, очередь, логи, `pin.txt`) не переносится: на новом
  компьютере всё начинается с чистого листа.
"""
import os
import shutil
import subprocess
import sys
import zipfile

SRC = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(os.path.expanduser("~"), "tools")
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")
OUT = os.path.join(DESKTOP, "PhoneAgent-exe")
ZIP = OUT + ".zip"
WORK = os.path.join(os.environ.get("TEMP", SRC), "phoneagent-build")

# Рядом с exe. Настройки — да, состояние — нет.
# `telegram.json` сюда НЕ попадает: в нём токен бота, а комплект уезжает
# к другому человеку.
BESIDE = ("recipes.json", "interests.json", "plan.json", "ADBKeyboard.apk",
          "ИНСТРУКЦИЯ.html", "bot-avatar.png")

READ_ME = """PhoneAgent
==========

Двойной щелчок по PhoneAgent.exe — откроется окно приложения.
Python и что-либо ещё ставить не нужно.

Что нужно один раз сделать на телефоне
--------------------------------------
1. Настройки -> О телефоне -> семь раз нажать «Версия MIUI»
   (так включается режим разработчика).
2. Настройки -> Расширенные -> Для разработчиков -> включить
   «Отладка по USB».
3. Воткнуть кабель и подтвердить отпечаток на экране телефона.

Аватарка для Телеграм-бота
--------------------------
Файл bot-avatar.png. Отправьте его @BotFather командой /setuserpic
и выберите своего бота.

Русские описания к видео
------------------------
Нужна клавиатура ADBKeyboard — она лежит рядом (ADBKeyboard.apk).
Скопируй файл на телефон и установи его ТАМ: с компьютера MIUI
установку запрещает. Потом в приложении на вкладке «Вкусы» нажми
«Проверить и включить».

Подробная инструкция — файл ИНСТРУКЦИЯ.html, открывается браузером.

Что лежит в этой папке
----------------------
PhoneAgent.exe    само приложение
ИНСТРУКЦИЯ.html   как всем этим пользоваться
recipes.json      маршруты публикации; правится, когда соцсеть
                  поменяет интерфейс и кнопка перестанет находиться
interests.json    тема и язык: что смотреть в ленте
plan.json         расписание сессий
tools\\            adb (обязателен) и scrcpy (трансляция экрана)

Появятся сами при работе: jobs.db (очередь), watch, queue, logs, frames.

Предохранитель
--------------
В собранной версии публикация включена по-настоящему. Кнопка
«Опубликовать» выкладывает видео в аккаунт, отменить это нельзя.
"""


def run_pyinstaller():
    if os.path.exists(WORK):
        shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK, exist_ok=True)

    icon = os.path.join(SRC, "icon.ico")
    if not os.path.exists(icon):
        print("  [i] иконки нет, рисую (python make_icon.py)")
        subprocess.run([sys.executable, os.path.join(SRC, "make_icon.py")],
                       cwd=SRC, capture_output=True)

    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile",
        "--name", "PhoneAgent",
        *(["--icon", icon] if os.path.exists(icon) else []),
        # Абсолютный путь обязателен: относительный PyInstaller считает от
        # папки со spec-файлом, а она у нас во временной.
        "--add-data", f"{os.path.join(SRC, 'webui.html')};.",
        "--paths", SRC,
        "--distpath", os.path.join(WORK, "dist"),
        "--workpath", os.path.join(WORK, "build"),
        "--specpath", WORK,
        os.path.join(SRC, "main.py"),
    ]
    print("собираю exe...")
    result = subprocess.run(cmd, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    if result.returncode != 0:
        print(result.stdout[-3000:])
        print(result.stderr[-3000:])
        raise SystemExit("PyInstaller не справился")

    exe = os.path.join(WORK, "dist", "PhoneAgent.exe")
    if not os.path.exists(exe):
        raise SystemExit("exe не появился")
    return exe


def assemble(exe):
    if os.path.exists(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)

    shutil.copy2(exe, os.path.join(OUT, "PhoneAgent.exe"))
    for name in BESIDE:
        path = os.path.join(SRC, name)
        if os.path.exists(path):
            shutil.copy2(path, os.path.join(OUT, name))
        else:
            print(f"  [i] нет {name}, пропускаю")

    tools_out = os.path.join(OUT, "tools")
    for tool in ("platform-tools", "scrcpy-win64-v4.1"):
        src = os.path.join(TOOLS, tool)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(tools_out, tool))
        else:
            print(f"  [!] нет {src} — без него "
                  f"{'ничего не заработает' if 'platform' in tool else 'не будет трансляции экрана'}")

    with open(os.path.join(OUT, "ЧИТАЙ МЕНЯ.txt"), "w",
              encoding="utf-8-sig", newline="\r\n") as f:
        f.write(READ_ME)


def pack():
    if os.path.exists(ZIP):
        os.remove(ZIP)
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(OUT):
            for name in files:
                full = os.path.join(root, name)
                z.write(full, os.path.join("PhoneAgent-exe",
                                           os.path.relpath(full, OUT)))


def size_mb(path):
    if os.path.isfile(path):
        return os.path.getsize(path) / 1024 / 1024
    total = sum(os.path.getsize(os.path.join(r, n))
                for r, _, fs in os.walk(path) for n in fs)
    return total / 1024 / 1024


if __name__ == "__main__":
    exe = run_pyinstaller()
    assemble(exe)
    pack()
    print(f"\nготово")
    print(f"  папка: {OUT}  ({size_mb(OUT):.0f} МБ)")
    print(f"  архив: {ZIP}  ({size_mb(ZIP):.0f} МБ)")
