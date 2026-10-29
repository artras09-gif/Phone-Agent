"""Свой сервер Telegram Bot API на этом ПК — бот принимает видео до 2 ГБ.

Облачный Bot API (api.telegram.org) отдаёт боту файлы только до 20 МБ — это
ограничение Telegram, обойти его в коде бота нельзя (жалоба 2026-10-16:
«файл 73 МБ … что за хуйня?»). Официальный сервер telegram-bot-api,
запущенный у себя с ключом `--local`, отдаёт до 2000 МБ: файл он скачивает
сам и кладёт на диск, бот берёт его оттуда.

Нужны две вещи, которые за человека не получить: `api_id` и `api_hash` его
приложения с my.telegram.org. Они вписываются во вкладке «Бот». Без них —
всё как раньше, облако и 20 МБ.

Сам сервер — один exe, собранный из официальных исходников
(github.com/tdlib/telegram-bot-api), лежит в `tools/telegram-bot-api/`.
Слушает только 127.0.0.1: снаружи к нему не подключиться.
"""
import atexit
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

import config

# Не 8081: его любят занимать чужие dev-серверы. Наш — редкий.
PORT = 18081
EXE = "telegram-bot-api.exe"
# local: True — сервер с --local (файлы до 2 ГБ, путь на диске), False — без
# него (до 20 МБ, как облако), None — не знаем (чужой сервер по адресу).
_STATE = {"proc": None, "url": "", "why": "", "local": None, "source": ""}


def find_exe():
    """Где лежит сервер: рядом с программой (exe) или в инструментах."""
    roots = [os.path.join(config.BASE, "tools"),
             os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"),
             os.path.join(os.path.expanduser("~"), "tools")]
    for root in roots:
        path = os.path.join(root, "telegram-bot-api", EXE)
        if os.path.isfile(path):
            return path
    return ""


def _alive(url, timeout=2.0):
    """Отвечает ли сервер. Любой HTTP-ответ (даже 404) — жив."""
    try:
        with urllib.request.urlopen(url + "/", timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def why():
    """Почему своего сервера нет — для окна и журнала. Пусто — работает."""
    return _STATE["why"]


def url():
    """Адрес своего сервера или "" — тогда бот ходит в облако."""
    return _STATE["url"]


def is_local():
    return _STATE["local"]


def source():
    """Откуда сервер — для окна: «запущен программой», «уже был запущен …»."""
    return _STATE["source"]


# ------------------------------------------------------- уже запущенный сервер
#
# Жалоба 2026-10-16: «поставил на комп, на котором уже есть телеграм-хост, но
# бот не видит запущенный хост». Программа искала сервер только на своём порту
# 18081 и только когда вписаны api_id/api_hash. Чужой (поднятый руками, в
# Docker, другой программой) слушает обычно 8081 — его не видели вовсе, а ключей
# человек не вписывал: зачем, если сервер уже есть.

DEFAULT_PORTS = (PORT, 8081)


def _run(cmd, timeout=10):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              encoding="utf-8", errors="replace",
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except Exception:
        return ""


def _open(url, timeout=None):
    """Слушает ли кто-то порт. Закрытый порт localhost Windows отвергает не
    сразу, а через ~2 с повторов — поэтому короткий таймаут для своего ПК."""
    import socket
    import urllib.parse

    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or "127.0.0.1"
    port = parts.port or (443 if parts.scheme == "https" else 80)
    local = host in ("127.0.0.1", "localhost", "::1")
    try:
        with socket.create_connection((host, port), timeout=timeout or (0.3 if local else 2.0)):
            return True
    except OSError:
        return False


def _processes():
    """Запущенные telegram-bot-api на этом ПК: [(адрес, local или None)].

    Быстро: tasklist + netstat (доли секунды). Стоит ли --local — из командной
    строки процесса, функцией Windows (`winsys`).
    """
    # Раньше командную строку спрашивали у PowerShell, и за скрытый
    # PowerShell 29.10 Касперский счёл exe трояном.
    if os.name != "nt":
        return [parse_cmdline(ln) for ln in _run(["ps", "-eo", "args"]).splitlines()
                if "telegram-bot-api" in ln and not ln.startswith(("ps ", "grep "))]
    pids = set()
    for line in _run(["tasklist", "/FO", "CSV", "/NH"]).splitlines():
        parts = [x.strip('"') for x in line.split('","')]
        if len(parts) > 1 and "telegram-bot-api" in parts[0].lower():
            pids.add(parts[1].strip('"'))
    if not pids:
        return []
    found = []
    for line in _run(["netstat", "-ano", "-p", "TCP"]).splitlines():
        cols = line.split()
        if len(cols) >= 5 and cols[-1] in pids and cols[3].upper() == "LISTENING":
            host, _, port = cols[1].rpartition(":")
            if host in ("0.0.0.0", "127.0.0.1", "[::]", "[::1]"):
                found.append(f"http://127.0.0.1:{port}")
    import winsys

    modes = {}
    for pid in pids:
        line = winsys.command_line(int(pid)) or ""
        if "telegram-bot-api" in line:
            url_, local = parse_cmdline(line)
            modes[url_] = local
    return [(u, modes.get(u)) for u in dict.fromkeys(found)] or list(modes.items())


def parse_cmdline(line):
    """Адрес и режим сервера из его командной строки: (url, local)."""
    port = re.search(r"(?:--http-port[=\s]+|(?<!\S)-p\s*)(\d{2,5})", line)
    host = re.search(r"--http-ip-address[=\s]+(\S+)", line)
    ip = host.group(1).strip("\"'") if host else "127.0.0.1"
    if ip in ("0.0.0.0", "::", "[::]", ""):
        ip = "127.0.0.1"
    if ":" in ip and not ip.startswith("["):
        ip = f"[{ip}]"
    return f"http://{ip}:{port.group(1) if port else 8081}", "--local" in line


def candidates(configured="", scan=True):
    """Где искать сервер, по порядку: вписанный руками, обычные порты,
    найденные процессы. [(адрес, local или None, откуда)]."""
    out = []
    if configured:
        out.append((configured.rstrip("/"), None, f"указан руками: {configured}"))
    for port in DEFAULT_PORTS:
        out.append((f"http://127.0.0.1:{port}", None, f"уже запущен на этом ПК (порт {port})"))
    if scan:
        for found, local in _processes():
            out.append((found, local, f"уже запущен на этом ПК ({found})"))
    seen, uniq = set(), []
    for item in out:
        if item[0] not in seen:
            seen.add(item[0])
            uniq.append(item)
    return uniq


def discover(token, configured=""):
    """Найти работающий сервер Bot API. Адрес или "" — не нашли.

    Свой же процесс от этой программы — тоже находка. Проверка — getMe с
    нашим токеном: чужая программа на порту JSON Telegram не ответит.
    """
    tried = set()
    for scan in (False, True):               # сперва дёшево — порты, потом процессы
        for found, local, where in candidates(configured, scan=scan):
            if found in tried:
                continue
            tried.add(found)
            if not _open(found) or not _alive(found, timeout=1.5) or not _is_ours(found, token):
                continue
            if local is None and found.endswith(f":{PORT}"):
                local = True                 # наш порт — наш сервер, всегда --local
            if local is None and scan is False:
                # Нашли по порту — узнать режим по процессу, если он здесь.
                local = dict(_processes()).get(found)
            _STATE.update(url=found, why="", local=local, source=where)
            return found
    return ""


def _is_ours(url, token):
    """На порту правда Bot API, а не чужая программа: getMe отвечает JSON."""
    import json
    try:
        with urllib.request.urlopen(f"{url}/bot{token}/getMe", timeout=5) as r:
            return "ok" in json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return "ok" in json.loads(e.read().decode("utf-8"))
        except Exception:
            return False
    except Exception:
        return False


def start(api_id, api_hash, token="", wait=20.0):
    """Поднять сервер (или подхватить уже работающий). Адрес или ""."""
    api_id, api_hash = str(api_id or "").strip(), str(api_hash or "").strip()
    if not api_id or not api_hash:
        _STATE.update(url="", why="", local=None, source="")
        return ""
    local = f"http://127.0.0.1:{PORT}"
    proc = _STATE["proc"]
    if proc is not None and proc.poll() is None and _alive(local):
        _STATE.update(url=local, why="", local=True, source="запущен программой")
        return local
    # Остался от прошлого запуска программы (её закрыли принудительно) —
    # берём его, второй на тот же порт всё равно не встанет. Но только если
    # это правда Bot API: порт могла занять чужая программа.
    if _alive(local):
        if not token or _is_ours(local, token):
            _STATE.update(url=local, why="", local=True, source="запущен программой")
            return local
        _STATE.update(url="", why=f"порт {PORT} занят другой программой — файлы до 20 МБ")
        return ""
    exe = find_exe()
    if not exe:
        _STATE.update(url="", why="нет программы сервера (telegram-bot-api.exe) — "
                                   "файлы до 20 МБ, как в облаке")
        return ""
    work = os.path.join(config.BASE, "tgapi")
    os.makedirs(os.path.join(work, "tmp"), exist_ok=True)
    log = open(os.path.join(work, "server.log"), "ab")
    cmd = [exe, "--local", f"--api-id={api_id}", f"--api-hash={api_hash}",
           f"--http-port={PORT}", "--http-ip-address=127.0.0.1",
           f"--dir={work}", f"--temp-dir={os.path.join(work, 'tmp')}"]
    try:
        proc = subprocess.Popen(cmd, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as e:
        _STATE.update(url="", why=f"сервер не запустился: {e}")
        return ""
    _STATE["proc"] = proc
    atexit.register(stop)
    end = time.time() + wait
    while time.time() < end:
        if proc.poll() is not None:
            _STATE.update(url="", why="сервер сразу закрылся — проверь api_id и api_hash "
                                       f"(журнал: {os.path.join(work, 'server.log')})")
            return ""
        if _alive(local, timeout=1.0):
            _STATE.update(url=local, why="", local=True, source="запущен программой")
            return local
        time.sleep(0.5)
    _STATE.update(url="", why="сервер не ответил за 20 с")
    stop()
    return ""


def stop():
    proc = _STATE.get("proc")
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    _STATE.update(proc=None, url="", local=None, source="")
