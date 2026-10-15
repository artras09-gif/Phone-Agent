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
import subprocess
import time
import urllib.error
import urllib.request

import config

# Не 8081: его любят занимать чужие dev-серверы. Наш — редкий.
PORT = 18081
EXE = "telegram-bot-api.exe"
_STATE = {"proc": None, "url": "", "why": ""}


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
        _STATE.update(url="", why="")
        return ""
    local = f"http://127.0.0.1:{PORT}"
    proc = _STATE["proc"]
    if proc is not None and proc.poll() is None and _alive(local):
        _STATE.update(url=local, why="")
        return local
    # Остался от прошлого запуска программы (её закрыли принудительно) —
    # берём его, второй на тот же порт всё равно не встанет. Но только если
    # это правда Bot API: порт могла занять чужая программа.
    if _alive(local):
        if not token or _is_ours(local, token):
            _STATE.update(url=local, why="")
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
            _STATE.update(url=local, why="")
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
    _STATE.update(proc=None, url="")
