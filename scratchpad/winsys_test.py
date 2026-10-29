"""Без PowerShell и cmd (2026-10-30: Касперский счёл exe трояном за скрытый
PowerShell с закодированной командой — «никогда ничего подобного не делай»).

1. `winsys` — командная строка и путь процесса, ярлык — на живой Windows.
2. Удаление программы находит свои окна и свой сервер Telegram через `winsys`.
3. Сторож: в коде программы нет ни запуска PowerShell/cmd/wscript, ни
   закодированных команд, ни обхода политики — чтобы это не вернулось.
"""
import ast
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import bundled   # noqa: E402
import install   # noqa: E402
import tgserver  # noqa: E402
import winsys    # noqa: E402

ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


tmp = tempfile.mkdtemp(prefix="pa_winsys_")
windows = os.name == "nt"

print("--- winsys на этом ПК ---")
me = os.getpid()
line = winsys.command_line(me)
say(not windows or (line and "winsys_test.py" in line), f"своя командная строка: {str(line)[:80]}")
path = winsys.image_path(me)
say(not windows or os.path.normcase(path or "") == os.path.normcase(sys.executable),
    f"свой exe: {path}")
say(winsys.command_line(99999999) is None and winsys.image_path(99999999) is None,
    "нет такого процесса — None, без исключения")

lnk = os.path.join(tmp, "Ярлык теста.lnk")
made = winsys.make_shortcut(lnk, sys.executable, os.path.dirname(sys.executable),
                            sys.executable, "PhoneAgent")
data = open(lnk, "rb").read() if os.path.exists(lnk) else b""
say(not windows or (made and data[:4] == b"L\x00\x00\x00"
                    and os.path.basename(sys.executable).lower().encode() in data.lower()),
    f"ярлык записан и ведёт на {os.path.basename(sys.executable)}")
say(not windows or winsys.make_shortcut(lnk, sys.executable), "поверх старого — тоже")
say(not winsys.make_shortcut(os.path.join(tmp, "нет папки", "x.lnk"), sys.executable),
    "папки нет — False, без исключения")

print("\n--- удаление: свои окна и свой сервер Telegram ---")
profile = os.path.join(tmp, "base", ".uiprofile")
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)",
                          f"--user-data-dir={profile}"])
other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)",
                          "--user-data-dir=C:\\чужой\\профиль"])
time.sleep(0.5)
real_pids = bundled.pids
bundled.pids = lambda image=None: [child.pid, other.pid] if image == "chrome.exe" else []
found = install._browser_with(profile)
say(windows is False or list(found) == [child.pid],
    f"окно программы — по профилю в командной строке, чужое не тронуто: {list(found)}")
install._taskkill(child.pid, force=True)
child.wait(10)
say(child.poll() is not None, "taskkill /F завершил процесс")
other.kill()
other.wait(10)

killed = []
real_taskkill, real_image = install._taskkill, winsys.image_path
install._taskkill = lambda pid, force=False: killed.append((pid, force))
base = os.path.join(tmp, "base")
paths = {11: os.path.join(base, "tools", "telegram-bot-api", "telegram-bot-api.exe"),
         12: r"C:\Свой сервер\telegram-bot-api.exe"}
bundled.pids = lambda image=None: [11, 12] if image == "telegram-bot-api.exe" else []
winsys.image_path = lambda pid: paths.get(pid)
install.stop_our_tgserver(base)
say(killed == [(11, True)], f"гасим только сервер из папки программы: {killed}")
install._taskkill, winsys.image_path, bundled.pids = real_taskkill, real_image, real_pids

print("\n--- сервер Telegram: --local из командной строки процесса ---")
real_run, real_cmd = tgserver._run, winsys.command_line
tgserver._run = lambda cmd, timeout=10: {
    "tasklist": '"telegram-bot-api.exe","4242","Console","1","20 000 K"\n',
    "netstat": "  TCP    127.0.0.1:8081    0.0.0.0:0    LISTENING    4242\n",
}.get(cmd[0], "")
winsys.command_line = lambda pid: (r'"C:\tg\telegram-bot-api.exe" --local --http-port 8081'
                                   if pid == 4242 else None)
got = tgserver._processes() if windows else [("http://127.0.0.1:8081", True)]
say(got == [("http://127.0.0.1:8081", True)], f"нашёлся и он --local: {got}")
tgserver._run, winsys.command_line = real_run, real_cmd

print("\n--- сторож: ничего похожего на вирус в коде программы ---")
SHELLS = ("powershell", "pwsh", "cmd", "cmd.exe", "wscript", "cscript", "mshta",
          "rundll32", "regsvr32", "certutil", "bitsadmin", "schtasks")
# Во всех строках кода, вместе со строками документации: они попадают в exe.
WORDS = ("powershell", "cmd /c", "encodedcommand", "executionpolicy", "frombase64string",
         "invoke-expression", "downloadstring", "downloadfile", "-windowstyle hidden")
bad = []
for name in sorted(os.listdir(ROOT)):
    if not name.endswith(".py") or name == "env.py":
        continue
    tree = ast.parse(open(os.path.join(ROOT, name), encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            first = next((a for a in node.args if isinstance(a, (ast.Constant, ast.List,
                                                                  ast.JoinedStr))), None)
            if isinstance(first, ast.List) and first.elts:
                first = first.elts[0]
            text = ""
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                text = first.value
            elif isinstance(first, ast.JoinedStr):
                text = "".join(v.value for v in first.values
                               if isinstance(v, ast.Constant) and isinstance(v.value, str))
            head = text.strip().split(" ")[0].lower() if text.strip() else ""
            if head in SHELLS:
                bad.append(f"{name}:{node.lineno} запускает {head}")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            low = node.value.lower()
            for word in WORDS:
                if word in low:
                    bad.append(f"{name}:{node.lineno} «{word}»")
say(not bad, "нет запуска PowerShell/cmd/wscript и закодированных команд"
    + ("" if not bad else ":\n      " + "\n      ".join(bad)))

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
