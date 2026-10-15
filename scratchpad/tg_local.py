"""Свой сервер Telegram Bot API (видео до 2 ГБ) — без сети и без настоящего
сервера: и «свой сервер», и «облако» — заглушки на 127.0.0.1.

Проверяется ровно то, о чём просил пользователь: на одном ПК всё работает
так же хорошо — без ключей бот как раньше (облако, 20 МБ); с ключами — свой
сервер; сервер не поднялся — бот остаётся в облаке и не ломается.
"""
import json
import os
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402

tmp = tempfile.mkdtemp(prefix="pa_tglocal_")
config.BASE = tmp
config.QUEUE_DIR = os.path.join(tmp, "queue")
os.makedirs(config.QUEUE_DIR)

import telegram_bot as bot  # noqa: E402
import tgserver             # noqa: E402

ok = True


def say(cond, text):
    global ok
    ok &= bool(cond)
    print(("  OK  " if cond else "  НЕТ ") + text)


calls = {"cloud": [], "local": []}
big = os.path.join(tmp, "server_files", "video.mp4")
os.makedirs(os.path.dirname(big))


def make(kind):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _answer(self):
            method = self.path.rsplit("/", 1)[-1]
            calls[kind].append(method)
            if method == "getFile":
                res = {"ok": True, "result": {"file_path": big}}
            elif method == "getMe":
                res = {"ok": True, "result": {"username": f"{kind}_bot"}}
            else:
                res = {"ok": True, "result": True}
            body = json.dumps(res).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = _answer
    return H


cloud = ThreadingHTTPServer(("127.0.0.1", 0), make("cloud"))
threading.Thread(target=cloud.serve_forever, daemon=True).start()
bot.API = f"http://127.0.0.1:{cloud.server_address[1]}"
TOKEN = "1234567:" + "A" * 35

print("--- без ключей — как раньше ---")
bot.save_settings(TOKEN, "1")
say(bot.api_keys() == ("", "") and bot.setup_server() == "", "ключей нет — свой сервер не поднимается")
say(bot.api_base() == bot.API and bot.max_download_mb() == 20, "облако, 20 МБ")
sent = []
bot.send = lambda text, **kw: sent.append(text) or True
bot._handle({"chat": {"id": 1}, "video": {"file_id": "f", "file_size": 73 << 20}, "caption": "x"})
say(sent and "api_id" in sent[-1] and "окно программы" in sent[-1],
    f"73 МБ в облаке — понятно, что сделать: {sent[-1][:90] if sent else ''}…")

print("\n--- ключи вписаны, а сервера нет — бот не ломается ---")
bot.save_settings(TOKEN, "1", api_id="12345", api_hash="0123456789abcdef0123456789abcdef")
say(bot.load_settings()["token"] == TOKEN and bot.api_keys()[0] == "12345", "ключи сохранены, токен цел")
bot.save_settings(TOKEN, "1")
say(bot.api_keys() == ("12345", "0123456789abcdef0123456789abcdef"),
    "сохранение токена ключи НЕ стирает")
tgserver.find_exe = lambda: ""
tgserver.PORT = 1                    # на порту 1 никого — свой сервер не найден
say(bot.setup_server() == "" and bot.api_base() == bot.API, "сервера нет — бот в облаке")
say("нет программы сервера" in tgserver.why(), f"и сказано почему: {tgserver.why()}")
say("logOut" not in calls["cloud"], "из облака при этом НЕ выписан (иначе бот бы замолчал)")

print("\n--- свой сервер работает ---")
local = ThreadingHTTPServer(("127.0.0.1", 0), make("local"))
threading.Thread(target=local.serve_forever, daemon=True).start()
tgserver.PORT = local.server_address[1]
url = bot.setup_server()
say(url and bot.api_base() == url and bot.max_download_mb() == 2000, f"свой сервер: {url}, до 2 ГБ")
say(calls["cloud"].count("logOut") == 1, "из облака выписан один раз")
bot.setup_server()
say(calls["cloud"].count("logOut") == 1, "на следующем запуске второй раз не выписывает")
say(TOKEN not in json.dumps(json.load(open(bot.settings_path(), encoding="utf-8")).get("cloud_logged_out")),
    "отметка о выписке — не сам токен")

calls["local"].clear()
bot._call("getUpdates", {"offset": 0, "timeout": 1})
say(calls["local"] == ["getUpdates"], "запросы идут в свой сервер")

open(big, "wb").write(os.urandom(4096))
path = bot._download("f", config.QUEUE_DIR)
say(path and os.path.isfile(path) and not os.path.exists(big),
    "файл со своего сервера забран с диска (перенесён в очередь)")
sent.clear()
bot._handle({"chat": {"id": 1}, "video": {"file_id": "f", "file_size": 73 << 20}, "caption": "x"})
say(not any("20 МБ" in t for t in sent), "73 МБ через свой сервер — без отказа")

print("\n--- новый токен — выписать и его ---")
TOKEN2 = "7654321:" + "B" * 35
bot.save_settings(TOKEN2, "1")
bot.setup_server()
say(calls["cloud"].count("logOut") == 2, "новый токен тоже выписан из облака (один раз)")

print("\n--- ключи стёрли — назад в облако ---")
bot.save_settings(TOKEN2, "1", api_id="", api_hash="")
say(bot.setup_server() == "" and bot.api_base() == bot.API, "без ключей — снова облако")

print("\n--- чужая программа на порту ---")


class Other(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"<html>not telegram</html>")


other = ThreadingHTTPServer(("127.0.0.1", 0), Other)
threading.Thread(target=other.serve_forever, daemon=True).start()
tgserver.PORT = other.server_address[1]
bot.save_settings(TOKEN2, "1", api_id="12345", api_hash="0123456789abcdef0123456789abcdef")
say(bot.setup_server() == "" and "занят другой программой" in tgserver.why(),
    f"порт занят не сервером Telegram — не используем: {tgserver.why()}")

page = open(os.path.join(os.path.dirname(HERE), "webui.html"), encoding="utf-8").read()
say('id="bot-api-id"' in page and 'id="bot-api-hash"' in page and 'id="bot-big"' in page,
    "во вкладке «Бот» — поля для ключей и строка «до 2 ГБ / до 20 МБ»")

for s in (cloud, local, other):
    s.shutdown()
shutil.rmtree(tmp, ignore_errors=True)
print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
