"""Выпадающий список моделей: в нём должны быть ТОЛЬКО те, что видят картинки.

Ни сети, ни LM Studio: ответ сервиса подменяется. Проверяется три вещи —
отбор по метаданным, отбор по имени и кэш (без него окно ходило в облако
раз в 12 секунд).
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402
import vision  # noqa: E402

ok = True


def say(good, text):
    global ok
    ok &= good
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


# --- 1. по имени ------------------------------------------------------
print("--- узнаём визуальные модели по имени ---")
VISUAL = [
    "qwen-vl-plus", "qwen2.5-vl-7b-instruct", "qwen/qwen3-vl-30b",
    "gpt-4o", "gpt-4o-mini", "openai/gpt-4.1", "gpt-5",
    "anthropic/claude-sonnet-4", "google/gemini-2.5-flash",
    "llava-v1.6-34b", "minicpm-v-2_6", "gemma-3-27b-it",
    "mistralai/pixtral-12b", "meta-llama/llama-3.2-11b-vision-instruct",
    "opengvlab/internvl3-14b", "allenai/molmo-7b-d", "deepseek-vl2",
    "moondream2", "smolvlm-instruct", "google/paligemma-3b", "glm-4v-9b",
]
TEXT_ONLY = [
    "qwen3-coder-30b", "qwen2.5-coder-32b", "deepseek-chat", "mistral-large",
    "llama-3.3-70b-instruct", "gpt-3.5-turbo", "text-embedding-3-large",
    "gemini-embedding-001", "whisper-1", "tts-1", "dall-e-3",
    "gemma-2-27b", "claude-instant-1.2", "llama-guard-3-8b",
    "gpt-4o-audio-preview", "jina-reranker-v2",
]
miss = [m for m in VISUAL if not vision.looks_visual(m)]
wrong = [m for m in TEXT_ONLY if vision.looks_visual(m)]
say(not miss, f"визуальные узнаны: {len(VISUAL) - len(miss)}/{len(VISUAL)}"
              + (f", мимо: {miss}" if miss else ""))
say(not wrong, f"текстовые отсеяны: {len(TEXT_ONLY) - len(wrong)}/{len(TEXT_ONLY)}"
               + (f", пролезли: {wrong}" if wrong else ""))

# --- 2. метаданные важнее имени --------------------------------------
print("\n--- слово сервиса важнее догадки по имени ---")
say(vision._visual_by_meta({"architecture": {"input_modalities": ["text", "image"]}}) is True,
    "input_modalities с картинкой — визуальная")
say(vision._visual_by_meta({"architecture": {"input_modalities": ["text"]}}) is False,
    "только текст — не визуальная")
say(vision._visual_by_meta({"architecture": {"modality": "text+image->text"}}) is True,
    "старый вид modality понимается")
say(vision._visual_by_meta({"id": "нет метаданных"}) is None,
    "молчание сервиса отличается от отказа")

# --- 3. отбор целиком, как его увидит окно ---------------------------
print("\n--- что попадёт в выпадающий список ---")
ANSWER = [
    # сервис честно сказал про модальности
    {"id": "openai/gpt-4o", "architecture": {"input_modalities": ["text", "image"]}},
    {"id": "deepseek/deepseek-chat", "architecture": {"input_modalities": ["text"]}},
    # сервис молчит — судим по имени
    {"id": "qwen-vl-max"},
    {"id": "qwen-plus"},
    # ловушка: имя визуальное, но сервис сказал «только текст»
    {"id": "some-vl-text-only", "architecture": {"input_modalities": ["text"]}},
]
saved = (config.VISION_PROVIDER, vision.models_detailed)
config.VISION_PROVIDER = vision.API
vision.models_detailed = lambda kind=None: ANSWER
vision.forget_models()
got = [m["id"] for m in vision.installed_models(fresh=True)]

say("openai/gpt-4o" in got, "модель с картинками в списке есть")
say("qwen-vl-max" in got, "визуальная по имени в списке есть")
say("deepseek/deepseek-chat" not in got, "текстовая в список не попала")
say("qwen-plus" not in got, "текстовая по имени не попала")
say("some-vl-text-only" not in got,
    "слову сервиса верим больше, чем визуальному имени")

# --- 4. кэш ----------------------------------------------------------
print("\n--- кэш списка ---")
calls = {"n": 0}


def counted(kind=None):
    calls["n"] += 1
    return ANSWER


vision.models_detailed = counted
vision.forget_models()
vision.installed_models()
first = calls["n"]
for _ in range(5):
    vision.installed_models()
say(calls["n"] == first, f"пять обходов окна — {calls['n'] - first} лишних "
                         "запросов к сервису")
vision.installed_models(fresh=True)
say(calls["n"] == first + 1, "fresh=True спрашивает заново")

import prefs  # noqa: E402

vision.installed_models()
before = calls["n"]
prefs.save(api_model=config.API_MODEL)
vision.installed_models()
say(calls["n"] == before + 1, "правка настроек сбрасывает кэш")

config.VISION_PROVIDER, vision.models_detailed = saved
vision.forget_models()

# --- 5. чей ключ: спрашиваем сервисы, а не гадаем по началу строки ---
print("\n--- определение сервиса по ключу ---")
import json as _json          # noqa: E402
import urllib.request as _ur  # noqa: E402


class _Answer:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return _json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


asked = []


def fake_open(req, timeout=None):
    """DashScope ключ не признаёт, OpenRouter признаёт."""
    url = req.full_url
    asked.append(url)
    if "dashscope" in url:
        raise _ur.HTTPError(url, 401, "Unauthorized", {}, None)
    return _Answer({"data": [
        {"id": "qwen/qwen2.5-vl-72b-instruct",
         "architecture": {"input_modalities": ["text", "image"]}},
        {"id": "deepseek/deepseek-chat",
         "architecture": {"input_modalities": ["text"]}},
    ]})


saved_open = _ur.urlopen
_ur.urlopen = fake_open
try:
    pid, url, visual = vision.detect_service("sk-обычный-на-вид-ключ")
finally:
    _ur.urlopen = saved_open

say(pid == "openrouter", f"ключ опознан как openrouter: {pid}")
say(bool(asked) and "dashscope" in asked[0],
    "сначала спрошен тот, на кого указывал вид ключа")
say(visual == ["qwen/qwen2.5-vl-72b-instruct"],
    f"в список попали только видящие картинки: {visual}")

print("\n--- выбор модели из списка ---")
names = ["stealth/space-bunny", "openai/gpt-4o", "qwen/qwen3-vl-32b", "x/vision-1"]
say(vision.pick_visual(names) == "qwen/qwen3-vl-32b",
    "по умолчанию берётся Qwen-VL — на нём меряли промпты")
say(vision.pick_visual(names, "openai/gpt-4o") == "openai/gpt-4o",
    "уже выбранная модель важнее предпочтения")
say(vision.pick_visual([]) == "", "пустой список не ломает выбор")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
