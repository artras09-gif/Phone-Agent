"""Уже запущенный сервер Bot API находится сам (жалоба 2026-10-16: «поставил на
комп, на котором уже есть телеграм-хост, но бот не видит запущенный хост»).

Программа искала сервер только на своём порту 18081 и только с вписанными
api_id/api_hash. Теперь: адрес руками -> обычные порты -> процесс
telegram-bot-api на этом ПК; ключи не нужны. Сеть — поддельная, на 127.0.0.1.
"""
import http.server
import json
import os
import re
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402

tmp = tempfile.mkdtemp(prefix="pa_discover_")
config.BASE = tmp
config.SERIAL = None

import telegram_bot as bot  # noqa: E402
import tgserver             # noqa: E402

ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


TOKEN = "7000000001:" + "A" * 35
CALLS = []


class BotApi(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, kind="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        m = re.match(r"/bot([^/]+)/(\w+)", self.path)
        if not m:
            return self._send(404, {"ok": False, "error_code": 404, "description": "Not Found"})
        CALLS.append(m.group(2))
        if m.group(2) == "getFile":
            return self._send(200, {"ok": True, "result": {"file_path": "/var/lib/telegram-bot-api/x/videos/f.mp4"}})
        return self._send(200, {"ok": True, "result": {"username": "testbot"}})

    def do_POST(self):
        # Тело дочитать до ответа: иначе Windows рвёт соединение сбросом, и
        # клиент видел WinError 10053 вместо ответа (падало через раз, 30.10).
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        return self.do_GET()


class NotTelegram(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html>dev server</html>")


def serve(handler):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}"


API_URL = serve(BotApi)
OTHER_URL = serve(NotTelegram)
bot.API = "http://127.0.0.1:9"          # облака «нет»: работать может только найденный сервер
tgserver.DEFAULT_PORTS = ()              # настоящие 18081/8081 этого ПК не трогаем
bot.save_settings(TOKEN, "1")

print("--- командная строка сервера ---")
say(tgserver.parse_cmdline(r'"C:\tg\telegram-bot-api.exe" --local --api-id=1 --api-hash=ab '
                           r'--http-port=8090 --dir=C:\d') == ("http://127.0.0.1:8090", True),
    "--http-port=8090 --local — адрес и режим")
say(tgserver.parse_cmdline("telegram-bot-api -p 8085 --http-ip-address=0.0.0.0")
    == ("http://127.0.0.1:8085", False), "-p 8085, слушает все адреса — без --local")
say(tgserver.parse_cmdline("/usr/bin/telegram-bot-api --api-id 1")[0] == "http://127.0.0.1:8081",
    "порт не указан — 8081 (по умолчанию у telegram-bot-api)")

print("\n--- нет ни сервера, ни ключей ---")
tgserver._processes = lambda: []
say(bot.setup_server() == "" and bot.max_download_mb() == 20, "облако, до 20 МБ")

print("\n--- запущен на этом ПК (нашли по процессу), ключей нет ---")
tgserver._processes = lambda: [(OTHER_URL, True), (API_URL, True)]
got = bot.setup_server()
say(got == API_URL, f"найден сервер, а не чужой dev-сервер на соседнем порту: {got}")
say(bot.api_base() == API_URL and bot.max_download_mb() == 2000, "бот ходит туда, видео до 2 ГБ")
say("уже запущен" in tgserver.source(), f"окну: {tgserver.source()}")
tgserver.stop()

print("\n--- запущен без --local ---")
tgserver._processes = lambda: [(API_URL, False)]
bot.setup_server()
say(bot.api_base() == API_URL and bot.max_download_mb() == 20,
    "найден, но без --local — честно 20 МБ, а не обещание 2 ГБ")
tgserver.stop()

print("\n--- адрес вписан руками ---")
tgserver._processes = lambda: []
bot.save_settings(TOKEN, "1", server_url=API_URL.replace("http://", ""))
say(bot.server_url() == API_URL, f"адрес без http:// дополнен: {bot.server_url()}")
say(bot.setup_server() == API_URL and "указан руками" in tgserver.source(), "по адресу — найден")
say(bot.max_download_mb() == 2000, "режим неизвестен — считаем --local (ради него и ставят)")

print("\n--- сервер в Docker / на другом ПК: путь к файлу не наш ---")
path = bot._download("f", tmp, 100)
say(path is None and "Docker" in bot.DOWNLOAD["why"],
    f"не скачалось — и сказано почему: {bot.DOWNLOAD['why'][:70]}…")

print("\n--- адрес вписан, а сервер не отвечает ---")
bot.save_settings(TOKEN, "1", server_url="http://127.0.0.1:9")
tgserver.stop()
say(bot.setup_server() == "" and bot.api_base() == bot.API, "облако — и окно скажет, что адрес молчит")

print("\n--- ключи не трогают адрес и наоборот ---")
bot.save_settings(TOKEN, "1", api_id="123", api_hash="ab" * 16)
say(bot.server_url() == "http://127.0.0.1:9" and bot.api_keys() == ("123", "ab" * 16),
    "api_id/api_hash и адрес живут вместе в общем файле")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
