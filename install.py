# -*- coding: utf-8 -*-
"""«Установленные приложения» Windows: записаться туда и удалиться без следа.

Собранный PhoneAgent.exe ничего не устанавливает мастером — это один файл.
Но удалять его, как нормальную программу, через «Параметры → Приложения»
должно быть можно. Поэтому при каждом запуске exe (не из исходников)
`register` записывает себя в раздел удаления ТЕКУЩЕГО пользователя (HKCU,
права администратора не нужны) и кладёт ярлык в меню «Пуск». Переложили exe
в другую папку — запись обновится при следующем запуске.

`uninstall` убирает ВСЁ, что программа оставила:

  * рабочую папку %LOCALAPPDATA%\\PhoneAgent — база, очередь, настройки с
    ключом, кадры, журналы, встроенный adb, профиль окна;
  * запись в «Приложениях» и ярлык в «Пуске»;
  * забытые распаковки во временной папке;
  * `%USERPROFILE%\\.android` и `%TEMP%\\adb.log` — ТОЛЬКО если их создала
    сама программа (см. `bundled.remember_first_run`);
  * сам exe — через секунду после выхода, когда файл отпустит Windows.

Чего не трогаем и не можем: журналы самой Windows о запусках программ
(Prefetch и подобное) — это не данные программы, и чистить системные
области без спроса нельзя.
"""
import json
import os
import shutil
import subprocess
import sys
import time

NAME = "PhoneAgent"
KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\PhoneAgent"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Что программа кладёт в свою папку. В переносном режиме (рабочая папка —
# та, где лежит exe) удаляем ТОЛЬКО это: рядом могут быть чужие файлы.
OWN_ITEMS = (
    "jobs.db", "jobs.db-wal", "jobs.db-shm", "settings.json", "telegram.json",
    "pin.txt", "remote.txt", "devices", "frames", "logs", "queue", "watch",
    "tools", ".uiprofile", ".fleet", ".gates", "STOP", "topics_cache.json",
    ".vision_model", "install.json", "recipes.json", "interests.json",
    "plan.json", "ADBKeyboard.apk", "ИНСТРУКЦИЯ.html", "portable.txt",
)


def exe_path():
    return os.path.abspath(sys.executable)


def default_base():
    return os.path.join(os.environ.get("LOCALAPPDATA")
                        or os.path.expanduser("~"), NAME)


def shortcut_path():
    return os.path.join(os.environ.get("APPDATA") or "", "Microsoft", "Windows",
                        "Start Menu", "Programs", f"{NAME}.lnk")


def _stamp():
    try:
        with open(os.path.join(sys._MEIPASS, "build.txt"), encoding="utf-8") as f:
            return f.read().strip()
    except (OSError, AttributeError):
        return ""


def _folder_kb(path):
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total // 1024


def _read_mark(base):
    try:
        with open(os.path.join(base, "install.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _make_shortcut(target):
    """Ярлык в «Пуске». Своих средств у Python нет — просим PowerShell.
    Пути передаём переменными окружения, а не в строке команды: кириллица и
    пробелы в пути ломают кавычки."""
    lnk = shortcut_path()
    if not os.path.isdir(os.path.dirname(lnk)):
        return False
    env = dict(os.environ, PA_LNK=lnk, PA_EXE=target,
               PA_DIR=os.path.dirname(target))
    script = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:PA_LNK);"
              "$s.TargetPath=$env:PA_EXE;$s.WorkingDirectory=$env:PA_DIR;"
              "$s.IconLocation=$env:PA_EXE+',0';$s.Description='PhoneAgent';$s.Save()")
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                        script], env=env, capture_output=True, timeout=30,
                       creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return False
    return os.path.exists(lnk)


def register(base):
    """Записаться в «Установленные приложения» и в «Пуск». Молча, без ошибок."""
    if not getattr(sys, "frozen", False):
        return False
    try:
        import winreg
    except ImportError:
        return False

    exe = exe_path()
    want = {
        "DisplayName": NAME,
        "DisplayVersion": _stamp(),
        "Publisher": NAME,
        "DisplayIcon": f"{exe},0",
        "InstallLocation": os.path.dirname(exe),
        "UninstallString": f'"{exe}" uninstall',
        "QuietUninstallString": f'"{exe}" uninstall --yes',
    }

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as k:
            have = {n: winreg.QueryValueEx(k, n)[0] for n in want
                    if _has(k, n)}
    except OSError:
        have = {}

    if have != want:
        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, KEY) as k:
                for n, v in want.items():
                    winreg.SetValueEx(k, n, 0, winreg.REG_SZ, v)
                winreg.SetValueEx(k, "InstallDate", 0, winreg.REG_SZ,
                                  time.strftime("%Y%m%d"))
                winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)
                size = os.path.getsize(exe) // 1024 + _folder_kb(base)
                winreg.SetValueEx(k, "EstimatedSize", 0, winreg.REG_DWORD,
                                  min(size, 0xFFFFFFFF))
        except OSError:
            return False

    # Ярлык пересоздаём, только если его нет или он ведёт на старое место:
    # PowerShell стоит секунду, звать его на каждом запуске незачем.
    mark = _read_mark(base)
    if mark.get("shortcut_target") != exe or not os.path.exists(shortcut_path()):
        if _make_shortcut(exe):
            mark["shortcut_target"] = exe
            try:
                with open(os.path.join(base, "install.json"), "w",
                          encoding="utf-8") as f:
                    json.dump(mark, f, ensure_ascii=False, indent=1)
            except OSError:
                pass
    return True


def _has(key, name):
    import winreg
    try:
        winreg.QueryValueEx(key, name)
        return True
    except OSError:
        return False


# ------------------------------------------------------------- удаление

def _others():
    """Другие экземпляры PhoneAgent — кроме себя и своего загрузчика."""
    import bundled

    me = {os.getpid(), os.getppid()}
    return [p for p in bundled.pids(os.path.basename(exe_path())) or [] if p not in me]


def _close_windows(base):
    """Закрыть окна программы — браузер с НАШИМ профилем.

    Закрытое окно — это штатный выход: работающий экземпляр сам остановит
    идущую сессию (телефон при этом заблокируется) и завершится. Грубое
    убийство процесса оставило бы телефон разблокированным в приложении.

    Закрываем ВЕЖЛИВО — `taskkill` без `/F` шлёт окну WM_CLOSE, как крестик.
    Проверено 2026-09-29: принудительное убийство всех процессов браузера
    разом приводило к тому, что работающий PhoneAgent не замечал закрытия и
    висел все 50 секунд до добивания, а Chrome не успевал убрать свои
    временные файлы и оставлял мусор в %TEMP%. Вежливо — программа выходит
    за 4 с, и Chrome прибирается за собой. Силой — только если окно за 15 с
    так и не закрылось.

    Возвращает номера процессов браузера, которые были окнами программы: по
    ним потом убираются служебные папки Chrome во временной папке.
    """
    profile = os.path.join(base, ".uiprofile")
    env = dict(os.environ, PA_PROFILE=profile)
    script = (
        "function Ours { Get-CimInstance Win32_Process | Where-Object { "
        "$_.CommandLine -and $_.CommandLine.Contains($env:PA_PROFILE) } };"
        "Ours | ForEach-Object { 'PID ' + $_.ProcessId };"
        "Ours | Where-Object { $_.CommandLine -notmatch '--type=' } | "
        "ForEach-Object { taskkill /PID $_.ProcessId | Out-Null };"
        "$t = Get-Date; while ((Ours) -and ((Get-Date) - $t).TotalSeconds -lt 15) "
        "{ Start-Sleep -Milliseconds 500 };"
        "Ours | ForEach-Object { Stop-Process -Id $_.ProcessId -Force "
        "-ErrorAction SilentlyContinue }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                              "-Command", script], env=env, capture_output=True,
                             text=True, timeout=60, creationflags=NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [int(line.split()[1]) for line in out.splitlines()
            if line.startswith("PID ") and line.split()[1].isdigit()]


def _drop_chrome_temp(pids):
    """Служебные папки Chrome во %TEMP% — только тех процессов, что были окнами
    программы. Имя у них вида `chrome_<что-то>_<pid>_<число>`: по номеру
    процесса и отличаем свои от папок обычного браузера человека."""
    temp = os.environ.get("TEMP") or ""
    if not temp or not pids or not os.path.isdir(temp):
        return
    wanted = {str(p) for p in pids}
    for name in os.listdir(temp):
        if not name.startswith("chrome_"):
            continue
        if any(part in wanted for part in name.split("_")):
            _remove(os.path.join(temp, name), attempts=3)


def _step(started, text):
    print(f"  [{time.time() - started:4.1f} с] {text}", flush=True)


def _stop_everything(base, started):
    """Остановить всё программное. Возвращает номера бывших окон браузера."""
    pids = _close_windows(base)
    _step(started, "окна программы закрыты")
    deadline = time.time() + 50          # столько даётся на остановку сессии
    while _others() and time.time() < deadline:
        time.sleep(1)
    left = _others()
    _step(started, "программа завершилась сама" if not left
          else f"не завершилась сама за 50 с: {left}")
    for pid in left:                     # не ушёл сам — завершаем
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True,
                       creationflags=NO_WINDOW)
    adb = os.path.join(base, "tools", "platform-tools", "adb.exe")
    if os.path.exists(adb):
        try:
            subprocess.run([adb, "kill-server"], capture_output=True, timeout=15,
                           creationflags=NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            pass
    return pids


def _remove(path, attempts=10):
    """Удалить файл или папку, переждав, пока их отпустят процессы."""
    for _ in range(attempts):
        if not os.path.lexists(path):
            return True
        try:
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
            return True
        except OSError:
            time.sleep(0.7)
    return not os.path.lexists(path)


def _message(text, question=False):
    """Окно с сообщением. Вернёт True, если на вопрос ответили «Да»."""
    try:
        import ctypes

        flags = 0x24 if question else 0x40    # «Да/Нет» с вопросом / сведения
        return ctypes.windll.user32.MessageBoxW(None, text, NAME, flags) == 6
    except (OSError, AttributeError):
        return not question


def uninstall(base, ask=True):
    """Удалить программу со всеми её данными. Код возврата для командной строки."""
    if not getattr(sys, "frozen", False):
        # Из исходников рабочая папка — это папка с КОДОМ. Стереть её было бы
        # катастрофой, поэтому удаление живёт только в собранном exe.
        print("удаление — только для собранного PhoneAgent.exe")
        return 1

    exe = exe_path()
    portable = os.path.normcase(os.path.abspath(base)) != \
        os.path.normcase(os.path.abspath(default_base()))
    if portable and os.path.normcase(os.path.abspath(base)) != \
            os.path.normcase(os.path.dirname(exe)):
        print(f"рабочая папка неожиданная, удалять не буду: {base}")
        return 1

    if ask and not _message(
            "Удалить PhoneAgent и все его данные?\n\n"
            "Будут удалены: очередь публикаций, расписание, вкусы, настройки "
            "вместе с ключом сервиса и токеном бота, кадры, журналы, "
            "встроенный adb и сама программа.\n\nОтменить это нельзя.",
            question=True):
        return 1

    started = time.time()
    mark = _read_mark(base)               # прочесть ДО того, как сотрём папку
    window_pids = _stop_everything(base, started)
    _step(started, "adb остановлен")
    _drop_chrome_temp(window_pids)

    left = []
    lnk = shortcut_path()
    if os.path.exists(lnk) and not _remove(lnk):
        left.append(lnk)
    try:
        import winreg

        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, KEY)
    except OSError:
        pass

    if portable:
        for name in OWN_ITEMS:
            path = os.path.join(base, name)
            if os.path.lexists(path) and not _remove(path):
                left.append(path)
    elif not _remove(base):
        left.append(base)

    # Следы adb вне своей папки — только если их создала программа.
    home = os.path.expanduser("~")
    if mark and not mark.get("android_dir_existed", True):
        android = os.path.join(home, ".android")
        if os.path.lexists(android) and not _remove(android):
            left.append(android)
    temp = os.environ.get("TEMP") or ""
    if mark and not mark.get("adb_log_existed", True) and temp:
        log = os.path.join(temp, "adb.log")
        if os.path.exists(log) and not _remove(log):
            left.append(log)

    _step(started, "данные удалены")
    import bundled

    bundled.sweep_stale(keep=getattr(sys, "_MEIPASS", ""))

    # Сам exe и свою распаковку удалить изнутри нельзя — они заняты этим же
    # процессом. Поручаем командной строке сделать это через пару секунд.
    mei = getattr(sys, "_MEIPASS", "")
    tail = f'& rmdir /s /q "{mei}"' if mei else ""
    subprocess.Popen(f'cmd /c ping 127.0.0.1 -n 4 >nul & del /f /q "{exe}" {tail}',
                     creationflags=NO_WINDOW | getattr(subprocess, "DETACHED_PROCESS", 0),
                     close_fds=True)

    if left:
        text = ("PhoneAgent удалён, но часть файлов занята и осталась:\n\n"
                + "\n".join(left[:6]) + "\n\nИх можно удалить вручную.")
    else:
        text = "PhoneAgent удалён. Следов программы на компьютере не осталось."
    print(text)
    if ask:
        _message(text)
    return 0 if not left else 2
