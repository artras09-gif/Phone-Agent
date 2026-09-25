# -*- coding: utf-8 -*-
"""Вставил ОДИН ключ и больше ничего — всё должно заработать.

Ради этого случая всё и делалось. Раньше, чтобы ключ начал использоваться,
надо было ещё переключить «где считать» на API, выбрать сервис и сохранить;
поле ключа до переключения было вовсе скрыто. Ключ молча ложился в файл и не
работал — `_endpoint` подставляет его только на API.

Проверяем: один ключ включает API сам, адрес и модель подставляются по виду
ключа, настройки СВОЕГО сервера при этом не затираются, а неверный ключ
называется неверным сразу, а не посреди сессии.

Боевой `settings.json` не трогаем: `config.BASE` уводится во временную папку.
Сети стенду не нужно — ответ сервиса подменяется.
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402

LIVE_SETTINGS = os.path.join(config.BASE, "settings.json")

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
    live_base = config.BASE

    tmp = tempfile.mkdtemp(prefix="key_only_")
    for name in ("webui.html", "recipes.json"):
        shutil.copy(os.path.join(live_base, name), os.path.join(tmp, name))
    config.BASE = tmp
    config.DB_PATH = os.path.join(tmp, "jobs.db")
    for attr in ("INTERESTS", "WATCH_DIR", "QUEUE_DIR", "FRAMES_DIR",
                 "LOGS_DIR", "RECIPES"):
        if hasattr(config, attr):
            setattr(config, attr,
                    os.path.join(tmp, os.path.basename(getattr(config, attr))))

    import prefs
    import vision
    import webui

    prefs.PATH = os.path.join(tmp, "settings.json")
    assert prefs.PATH != LIVE_SETTINGS

    # Сервис отвечает не по сети, а отсюда: стенд не должен зависеть ни от
    # связи, ни от того, оплачен ли чей-то ключ сегодня.
    verdict = [True, "ключ рабочий, модель qwen-vl-plus"]
    vision.verify = lambda kind=None: tuple(verdict)

    port = 8793
    from http.server import ThreadingHTTPServer
    server = ThreadingHTTPServer(("127.0.0.1", port), webui.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(0.6)

    def call(body):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/settings",
            data=json.dumps(body).encode(), method="POST")
        req.add_header("Host", f"127.0.0.1:{port}")
        req.add_header("X-Token", webui.TOKEN)
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))

    def saved():
        return json.load(open(prefs.PATH, encoding="utf-8"))

    print("== было: считаем у себя, свой адрес и своя модель ==")
    call({"vision_provider": "local",
          "url": "http://192.168.0.50:1234",
          "vision_model": "qwen2.5-vl-7b"})
    check("свой сервер записан", saved().get("vision_url", "").startswith(
        "http://192.168.0.50:1234"), saved().get("vision_url"))

    print("\n== вставили ОДИН ключ, больше ничего ==")
    res = call({"api_key": "sk-abcdefgh0123456789"})
    check("окно приняло", res.get("ok"), str(res)[:80])
    check("сказало, что переключило само",
          "по API" in str(res.get("message", "")), res.get("message"))
    check("ключ сохранён", saved().get("api_key") == "sk-abcdefgh0123456789")
    check("зрение переключилось на API", vision.provider() == vision.API,
          vision.provider())
    check("адрес сервиса подставился сам",
          "dashscope" in config.API_URL, config.API_URL)
    check("модель подставилась сама",
          config.API_MODEL == "qwen-vl-plus", config.API_MODEL)

    print("\n== настройки своего сервера не затёрты ==")
    check("свой адрес цел", saved().get("vision_url", "").startswith(
        "http://192.168.0.50:1234"), saved().get("vision_url"))
    check("своя модель цела", saved().get("vision_model") == "qwen2.5-vl-7b",
          saved().get("vision_model"))
    check("адрес сервиса НЕ взят у своего сервера",
          "192.168.0.50" not in config.API_URL, config.API_URL)

    print("\n== ключ OpenRouter узнаётся по началу ==")
    call({"api_key": "sk-or-v1-0123456789abcdef"})
    check("адрес OpenRouter", "openrouter" in config.API_URL, config.API_URL)
    check("и его модель", config.API_MODEL.startswith("qwen/"), config.API_MODEL)

    print("\n== неверный ключ называется неверным СРАЗУ ==")
    verdict[:] = [False, "сервис не принял ключ (401) — он недействителен"]
    res = call({"api_key": "sk-мертвый-ключ"[:20]})
    check("окно ответило отказом", res.get("ok") is False, str(res)[:80])
    check("и назвало причину", "401" in str(res.get("error", "")),
          res.get("error"))
    good, why = vision.available()
    check("зрение больше не считается готовым", good is False, f"{good}, {why}")

    print("\n== ключ наружу не отдаётся ==")
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/state")
    req.add_header("Host", f"127.0.0.1:{port}")
    with urllib.request.urlopen(req, timeout=15) as r:
        chosen = json.loads(r.read().decode("utf-8")).get("chosen") or {}
    check("ключа в ответе нет", chosen.get("api_key") in ("", None),
          repr(chosen.get("api_key")))
    check("но видно, что он есть", chosen.get("has_key") is True)

    check("боевой settings.json не тронут", digest(LIVE_SETTINGS) == before)
    server.shutdown()
    print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПРОВАЛЫ")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
