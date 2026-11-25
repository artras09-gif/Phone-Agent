"""Вход в Instagram для монтажа — окном браузера, без файла cookies.

Просьба 2026-11-25: «аккаунт подключил, но у меня нет куки — можно как-то
по-другому?». Можно: программа сама открывает на компьютере отдельное окно
Edge или Chrome со страницей входа Instagram. Человек входит в ЗАПАСНОЙ
аккаунт, а программа забирает вход прямо из этого окна и пишет его в
`montage/cookies.txt` (`linkfetch.save_cookies`). Окно закрывается само.

Как забирает: браузер запускается со своим профилем (`montage/browser`,
отдельно от личного браузера человека) и с отладочным портом на 127.0.0.1.
Через этот порт (протокол DevTools, по WebSocket) программа раз в пару
секунд спрашивает cookies — пока не появится `sessionid`, то есть вход.
Читать cookies из файлов браузера нельзя: Chrome и Edge шифруют их так, что
стандартной библиотекой не расшифровать.

Профиль остаётся: вход в нём живёт месяцами. Когда Instagram скажет
«нужен вход» снова, `refresh` откроет тот же профиль невидимо и заберёт
свежие cookies — без человека (`linkfetch.fetch`).
"""
import base64
import json
import os
import socket
import struct
import subprocess
import time
import urllib.parse
import urllib.request

import linkfetch

PROFILE = os.path.join(linkfetch.HOME, "browser")
LOGIN_URL = "https://www.instagram.com/accounts/login/"
HOME_URL = "https://www.instagram.com/"
LOGIN_TIMEOUT = 10 * 60
BROWSERS = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
)
QUIET = ("--no-first-run", "--no-default-browser-check", "--disable-sync",
         "--disable-extensions", "--disable-component-update",
         "--disable-background-networking")


class LoginError(RuntimeError):
    """Вход не забран — текст для человека."""


def browser():
    return next((b for b in BROWSERS if os.path.exists(b)), None)


def has_profile():
    return os.path.isdir(os.path.join(PROFILE, "Default"))


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ------------------------------------------------------- WebSocket, минимум

def _recv_exact(sock, n):
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("браузер закрыл соединение")
        data += chunk
    return data


def _ws_call(ws_url, method, params=None, timeout=15):
    """Один вызов DevTools: открыть WebSocket, отправить, дождаться ответа."""
    url = urllib.parse.urlparse(ws_url)
    sock = socket.create_connection((url.hostname, url.port), timeout)
    try:
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall((f"GET {url.path} HTTP/1.1\r\nHost: {url.hostname}:{url.port}\r\n"
                      "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = sock.recv(1024)
            if not chunk:
                raise ConnectionError("браузер не ответил на WebSocket")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError(head.split(b"\r\n", 1)[0].decode("latin-1"))
        payload = json.dumps({"id": 1, "method": method, "params": params or {}}).encode()
        mask = os.urandom(4)
        n = len(payload)
        frame = bytes([0x81])
        if n < 126:
            frame += bytes([0x80 | n])
        elif n < 65536:
            frame += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            frame += bytes([0x80 | 127]) + struct.pack(">Q", n)
        frame += mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        sock.sendall(frame)
        message = b""
        while True:
            b0, b1 = _recv_exact(sock, 2)
            size = b1 & 0x7F
            if size == 126:
                size = struct.unpack(">H", _recv_exact(sock, 2))[0]
            elif size == 127:
                size = struct.unpack(">Q", _recv_exact(sock, 8))[0]
            data = _recv_exact(sock, size)
            opcode = b0 & 0x0F
            if opcode == 8:
                raise ConnectionError("браузер закрыл WebSocket")
            if opcode in (1, 0):
                message += data
                if b0 & 0x80:
                    reply = json.loads(message.decode("utf-8"))
                    message = b""
                    if reply.get("id") == 1:
                        if "error" in reply:
                            raise ConnectionError(str(reply["error"]))
                        return reply.get("result") or {}
    finally:
        sock.close()


def _browser_ws(port, timeout=1.5):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))["webSocketDebuggerUrl"]


def instagram_cookies(port):
    """Cookies Instagram из открытого браузера или [] (ещё не поднялся)."""
    try:
        ws = _browser_ws(port)
        cookies = _ws_call(ws, "Storage.getCookies").get("cookies") or []
    except (OSError, ValueError, KeyError, ConnectionError):
        return []
    return [c for c in cookies if "instagram.com" in str(c.get("domain", ""))]


def to_netscape(cookies):
    """Cookies DevTools → текст cookies.txt (формат Netscape, его читает yt-dlp)."""
    lines = ["# Netscape HTTP Cookie File"]
    for c in cookies:
        domain = str(c.get("domain", ""))
        expires = int(c.get("expires") or 0)
        lines.append("\t".join([
            ("#HttpOnly_" if c.get("httpOnly") else "") + domain,
            "TRUE" if domain.startswith(".") else "FALSE",
            str(c.get("path") or "/"),
            "TRUE" if c.get("secure") else "FALSE",
            str(max(expires, 0)),
            str(c.get("name", "")),
            str(c.get("value", "")),
        ]))
    return "\n".join(lines) + "\n"


def _logged_in(cookies):
    return any(c.get("name") == "sessionid" and c.get("value") for c in cookies)


def _launch(url, headless=False):
    exe = browser()
    if not exe:
        raise LoginError("не нашёл на компьютере ни Edge, ни Chrome")
    os.makedirs(PROFILE, exist_ok=True)
    port = _free_port()
    args = [exe, f"--user-data-dir={PROFILE}", f"--remote-debugging-port={port}", *QUIET]
    args += ["--headless=new", url] if headless else [f"--app={url}", "--window-size=560,860"]
    try:
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        raise LoginError(f"браузер не запустился: {e}") from e
    return proc, port


def _close(proc, port):
    try:
        _ws_call(_browser_ws(port), "Browser.close", timeout=5)
    except (OSError, ValueError, KeyError, ConnectionError):
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def login(timeout=LOGIN_TIMEOUT, poll=2.0):
    """Открыть окно входа и ждать, пока человек войдёт. Текст для человека
    или LoginError."""
    proc, port = _launch(LOGIN_URL)
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            if proc.poll() is not None:
                raise LoginError("окно входа закрыли до того, как вход случился")
            cookies = instagram_cookies(port)
            if _logged_in(cookies):
                time.sleep(2)                   # Instagram дописывает csrftoken и прочее
                cookies = instagram_cookies(port) or cookies
                return linkfetch.save_cookies(to_netscape(cookies))
            time.sleep(poll)
        raise LoginError(f"за {timeout // 60} мин вход не случился — окно закрыл")
    finally:
        if proc.poll() is None:
            _close(proc, port)


def refresh(timeout=40):
    """Забрать свежие cookies из профиля, где уже входили, — невидимо.
    True — вход есть и записан."""
    if not has_profile() or not browser():
        return False
    try:
        proc, port = _launch(HOME_URL, headless=True)
    except LoginError:
        return False
    deadline = time.time() + timeout
    try:
        while time.time() < deadline and proc.poll() is None:
            cookies = instagram_cookies(port)
            if _logged_in(cookies):
                try:
                    linkfetch.save_cookies(to_netscape(cookies))
                    return True
                except ValueError:
                    return False
            time.sleep(1.5)
        return False
    finally:
        if proc.poll() is None:
            _close(proc, port)
