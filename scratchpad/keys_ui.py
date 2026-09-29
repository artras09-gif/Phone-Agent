"""Ключи вписываются мышкой в окне — сквозная проверка по HTTP.

Проверяется то, ради чего это и делалось: любой OpenAI-совместимый сервис
подключается через окно, ключ сохраняется, обратно наружу НЕ отдаётся, и
его можно стереть, не трогая остальное.

Боевой `settings.json` не трогаем: `config.BASE` уводится во временную папку,
а неизменность боевого файла проверяется по хешу до и после.
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402

LIVE_BASE = config.BASE
LIVE_SETTINGS = os.path.join(LIVE_BASE, "settings.json")

ok = True


def check(name, cond, got=""):
    global ok
    ok = ok and bool(cond)
    print(("  OK  " if cond else " ПРОВАЛ ") + name + (f"  [{got}]" if got else ""))


def digest(path):
    if not os.path.exists(path):
        return "нет файла"
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def main():
    before = digest(LIVE_SETTINGS)

    tmp = tempfile.mkdtemp(prefix="keys_ui_")
    # Страница и рецепты читаются с диска — кладём их рядом.
    for name in ("webui.html", "recipes.json", "ИНСТРУКЦИЯ.html"):
        shutil.copy(os.path.join(LIVE_BASE, name), os.path.join(tmp, name))
    config.BASE = tmp
    config.DB_PATH = os.path.join(tmp, "jobs.db")
    live_pin = config.PIN_FILE
    pin_before = digest(live_pin)
    config.PIN_FILE = os.path.join(tmp, "pin.txt")
    for attr in ("INTERESTS", "WATCH_DIR", "QUEUE_DIR", "FRAMES_DIR",
                 "LOGS_DIR", "RECIPES"):
        if hasattr(config, attr):
            old = getattr(config, attr)
            setattr(config, attr, os.path.join(tmp, os.path.basename(old)))

    import prefs
    import vision
    import webui

    # Ключ теперь проверяется живым запросом при сохранении. Здесь это не к
    # месту: адрес выдуман, сети у стенда быть не должно. Стенд про хранение —
    # подменяем ответ сервиса. Живую проверку гоняет `key_only`.
    vision.verify = lambda kind=None: (True, "проверка отключена в стенде")

    prefs.PATH = os.path.join(tmp, "settings.json")
    assert prefs.PATH != os.path.join(LIVE_BASE, "settings.json")

    # Порт выдаёт система: на фиксированном 8791 стенд однажды поймал сброс
    # соединения — HTTPServer на Windows пускает на занятый порт второго.
    from http.server import ThreadingHTTPServer
    server = ThreadingHTTPServer(("127.0.0.1", 0), webui.Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.6)

    def call(path, body=None):
        url = f"http://127.0.0.1:{port}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
        req.add_header("Host", f"127.0.0.1:{port}")
        if data is not None:
            req.add_header("X-Token", webui.TOKEN)
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))

    print("== свой сервис по адресу и ключу ==")
    res = call("/api/settings", {
        "vision_provider": "api",
        "url": "https://api.example.com/v1/chat/completions",
        "vision_model": "my-vision-7b",
        "api_key": "sk-МОЙ-КЛЮЧ-123",
    })
    check("окно приняло настройки", res.get("ok"), str(res)[:70])

    saved = json.load(open(prefs.PATH, encoding="utf-8"))
    check("ключ сохранён", saved.get("api_key") == "sk-МОЙ-КЛЮЧ-123")
    check("адрес причёсан до /v1",
          saved.get("api_url") == "https://api.example.com/v1",
          saved.get("api_url"))
    check("модель сохранена", saved.get("api_model") == "my-vision-7b")

    state = call("/api/state")
    chosen = state.get("chosen") or {}
    check("наружу ключ НЕ отдаётся", chosen.get("api_key") in ("", None),
          repr(chosen.get("api_key")))
    check("но видно, что он есть", chosen.get("has_key") is True)
    check("готовые адреса в списке есть",
          any(p["id"] == "qwen" for p in chosen.get("presets") or []))

    print("\n== ключ можно стереть ==")
    call("/api/settings", {"api_key": "-"})
    saved = json.load(open(prefs.PATH, encoding="utf-8"))
    check("ключ пуст", not saved.get("api_key"))
    check("адрес при этом остался",
          saved.get("api_url") == "https://api.example.com/v1")

    print("\n== переключение на локальную модель ==")
    call("/api/settings", {"vision_provider": "local",
                           "url": "http://192.168.0.50:1234",
                           "vision_model": "qwen2.5-vl-7b"})
    saved = json.load(open(prefs.PATH, encoding="utf-8"))
    check("адрес локального сервера сохранён",
          saved.get("vision_url", "").startswith("http://192.168.0.50:1234"))
    check("имя локальной модели сохранено",
          saved.get("vision_model") == "qwen2.5-vl-7b")

    print("\n== PIN экрана вписывается в окне ==")
    state = lambda: call("/api/state?since=0")["chosen"]   # noqa: E731
    check("сначала PIN нет", state().get("has_pin") is False)
    for bad in ("12a4", "12", "1" * 17, "12 34"):
        res = call("/api/pin", {"pin": bad})
        check(f"кривой PIN «{bad}» не принят", res.get("ok") is False
              and not os.path.exists(config.PIN_FILE), res.get("error", ""))
    res = call("/api/pin", {"pin": " 4821 "})
    check("PIN принят", res.get("ok") is True, res.get("message", ""))
    check("лёг в pin.txt рабочей папки цифрами",
          open(config.PIN_FILE, encoding="utf-8").read() == "4821")
    import device
    check("разблокировка его видит", device.read_pin() == "4821")
    got = state()
    check("окно знает, что PIN есть", got.get("has_pin") is True)
    check("но самого PIN в ответе нет", "4821" not in json.dumps(got))
    res = call("/api/pin", {"pin": "-"})
    check("прочерк стирает", res.get("ok") and not os.path.exists(config.PIN_FILE))
    check("и окно это видит", state().get("has_pin") is False)
    check("боевой pin.txt не тронут", digest(live_pin) == pin_before)

    print("\n== инструкция открывается из окна ==")
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/help", timeout=10) as r:
        text = r.read().decode("utf-8")
    check("по /help отдаётся инструкция", "Перенос и удаление" in text
          and "Блокировка экрана" in text)
    page = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10).read().decode("utf-8")
    check("в окне есть ссылка на неё", 'href="/help"' in page)

    print("\n== чужой запрос не проходит ==")
    try:
        url = f"http://127.0.0.1:{port}/api/settings"
        req = urllib.request.Request(url, data=b"{}", method="POST")
        req.add_header("Content-Type", "application/json")
        urllib.request.urlopen(req, timeout=10)
        check("без токена отказ", False, "прошёл!")
    except urllib.error.HTTPError as e:
        check("без токена отказ", e.code == 403, f"код {e.code}")

    server.shutdown()
    check("боевой settings.json не тронут", digest(LIVE_SETTINGS) == before)

    print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПРОВАЛЫ")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
