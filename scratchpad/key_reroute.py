"""Ключ от другого сервиса находится запросом, а не по виду (без сети).

Живьём 2026-11-12: ключ DeepSeek (`sk-` и 32 знака — точь-в-точь как у
DashScope) вписали в settings.json мимо окна настроек. `config.preset_for_key`
ответил «Qwen», ключ ушёл в DashScope и на каждом кадре получал 401 — в
журнале «ключ недействителен», хотя ключ рабочий. Окно настроек в таком случае
само спрашивает сервисы, чей ключ; теперь то же делает и сам запрос к модели.

Проверяет: после 401 от сервиса из списка зовётся `detect_service`, адрес и
модель переключаются и записываются, тот же запрос повторяется уже туда;
свой адрес (не из списка) не трогается; сервисы обходятся один раз на ключ.
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402

tmp = tempfile.mkdtemp(prefix="pa_reroute_")
config.BASE = tmp

import prefs   # noqa: E402
import vision  # noqa: E402

prefs.PATH = os.path.join(tmp, "settings.json")
ok = True


def check(what, cond):
    global ok
    print(("  ок  " if cond else "  ОШИБКА  ") + what)
    ok = ok and bool(cond)


QWEN = config.API_PRESETS["qwen"][1]
DEEP = config.API_PRESETS["deepseek"][1]
KEY = "sk-" + "0123456789abcdef" * 2          # вид у DashScope и DeepSeek один
sent, asked = [], []


def fake_send(base, path, data, headers, timeout):
    """DashScope ключ отвергает, DeepSeek отвечает."""
    sent.append((base, json.loads(data).get("model")))
    if "dashscope" in base:
        return 401, '{"error": {"message": "Incorrect API key provided."}}'
    return 200, json.dumps({"choices": [{"message": {"content": "готово"}}]})


def fake_detect(key, timeout=20, tries=4, deadline=None, say=None):
    asked.append(key)
    return "deepseek", DEEP, ["deepseek-flash"], "deepseek-flash"


vision._send = fake_send
vision._proxied = lambda: False
vision.detect_service = fake_detect
vision.forget_models = lambda: None


def setup(url, model="qwen-vl-plus"):
    config.VISION_PROVIDER = "api"
    config.API_URL, config.API_KEY, config.API_MODEL = url, KEY, model
    sent.clear()
    asked.clear()
    vision._REROUTED.clear()
    if os.path.exists(prefs.PATH):
        os.remove(prefs.PATH)


print("догадка по виду ключа")
check("ключ вида sk-… принят за Qwen — отличить от DeepSeek по виду нечем",
      config.preset_for_key(KEY) == "qwen")

print("\nсервис из списка отверг ключ — ищем, чей он")
setup(QWEN)
answer = vision._post("/chat/completions", {"model": "qwen-vl-plus", "messages": []}, 30, vision.API)
check(f"запрос дошёл до того, кто ключ принял: {sent}",
      answer["choices"][0]["message"]["content"] == "готово"
      and sent == [(QWEN, "qwen-vl-plus"), (DEEP, "deepseek-flash")])
check("сервисы спрошены один раз", asked == [KEY])
check(f"адрес и модель переключены: {config.API_URL}, {config.API_MODEL}",
      config.API_URL == DEEP and config.API_MODEL == "deepseek-flash")
saved = json.load(open(prefs.PATH, encoding="utf-8"))
check("и записаны в настройки — в следующий запуск угадывать не придётся",
      saved.get("api_url") == DEEP and saved.get("api_model") == "deepseek-flash")
check("сам ключ в настройки этим не попал", KEY not in json.dumps(saved))

print("\nключ не подошёл никому — честная ошибка, без обхода по кругу")
setup(QWEN)
vision.detect_service = lambda key, **k: asked.append(key) or (None, "", [], "")
for attempt in range(2):
    try:
        vision._post("/chat/completions", {"model": "qwen-vl-plus"}, 30, vision.API)
        check("отказ остался отказом", False)
    except vision.VisionError as e:
        if not attempt:
            check(f"ошибка называет 401: {str(e)[:60]}", "401" in str(e))
check("сервисы обойдены один раз на ключ, а не на каждый кадр", asked == [KEY])
check("адрес не тронут", config.API_URL == QWEN)

print("\nсвой адрес, набранный руками, не подменяется")
setup("http://192.168.1.50:8000/v1")
vision.detect_service = fake_detect
vision._send = lambda base, path, data, headers, timeout: (401, "no")
try:
    vision._post("/chat/completions", {"model": "qwen-vl-plus"}, 30, vision.API)
    check("отказ остался отказом", False)
except vision.VisionError:
    check("свой сервер отверг ключ — сервисы из списка не спрашиваем", asked == [])
check("адрес прежний", config.API_URL == "http://192.168.1.50:8000/v1")

print("\nИТОГ: всё зелёное" if ok else "\nЕСТЬ ОШИБКИ")
sys.exit(0 if ok else 1)
