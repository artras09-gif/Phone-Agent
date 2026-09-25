"""Выпадающий список моделей: в нём должны быть ТОЛЬКО те, что видят картинки.

Ни сети, ни LM Studio: ответ сервиса подменяется. Проверяется три вещи —
отбор по метаданным, отбор по имени и кэш (без него окно ходило в облако
раз в 12 секунд).
"""
import io
import os
import sys
import tempfile
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

# Путь настроек уводим во временную папку. Без этого стенд писал в БОЕВОЙ
# settings.json и подставлял туда модель по умолчанию — то есть каждый
# прогон батареи молча сбрасывал выбранную человеком модель. Поймано при
# перепроверке: после батареи в настройках оказывался «qwen-vl-plus»
# вместо выбранного. Так же, как в `keys_ui` и `key_only`.
БОЕВЫЕ = prefs.PATH
СНИМОК = io.open(БОЕВЫЕ, encoding="utf-8").read() if os.path.exists(БОЕВЫЕ) else None
prefs.PATH = os.path.join(tempfile.mkdtemp(prefix="pa_models_"), "settings.json")
assert prefs.PATH != БОЕВЫЕ

vision.installed_models()
before = calls["n"]
prefs.save(api_model=config.API_MODEL)
vision.installed_models()
say(calls["n"] == before + 1, "правка настроек сбрасывает кэш")

prefs.PATH = БОЕВЫЕ
if СНИМОК is not None:
    say(io.open(БОЕВЫЕ, encoding="utf-8").read() == СНИМОК,
        "боевой settings.json не тронут")

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
saved_sees = vision.model_sees
# Картинку «видит» только одна модель — та, что и должна выбраться.
vision.model_sees = lambda m, url, key, timeout=30: (
    (m == "qwen/qwen2.5-vl-72b-instruct", "зелёный"))
try:
    pid, url, visual, model = vision.detect_service("sk-обычный-на-вид-ключ")
finally:
    _ur.urlopen, vision.model_sees = saved_open, saved_sees

say(pid == "openrouter", f"ключ опознан как openrouter: {pid}")
say(bool(asked) and "dashscope" in asked[0],
    "сначала спрошен тот, на кого указывал вид ключа")
say(visual == ["qwen/qwen2.5-vl-72b-instruct"],
    f"в список попали только видящие картинки: {visual}")
say(model == "qwen/qwen2.5-vl-72b-instruct",
    f"рабочая модель подтверждена запросом: {model}")

print("\n--- списку моделей верить нельзя, запросу — можно ---")
# Ровно та ловушка, на которой обожглись живьём: /models у OpenRouter
# ПУБЛИЧНЫЙ и отдаёт сотни моделей даже без ключа.
_ur.urlopen = fake_open
vision.model_sees = lambda m, url, key, timeout=30: (False, "отказ 401")
try:
    pid2, _, _, model2 = vision.detect_service("sk-мусорный-ключ")
finally:
    _ur.urlopen, vision.model_sees = saved_open, saved_sees
say(pid2 != "openrouter" and not model2,
    f"публичный список не выдаёт мусорный ключ за рабочий: {pid2}, {model2!r}")

print("\n--- проверка зрения цветным квадратом ---")
png = vision._solid_png((220, 20, 20))
say(png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 50,
    f"квадрат рисуется своими руками: {len(png)} байт")

saved_choice = vision.random.choice
vision.random.choice = lambda seq: "красный"
answers = {}


def fake_ask(req, timeout=None):
    class R:
        def read(self):
            return _json.dumps({"choices": [{"message":
                               {"content": answers["said"]}}]}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    return R()


_ur.urlopen = fake_ask
try:
    answers["said"] = "Красный."
    good, _ = vision.model_sees("m", "http://x/v1", "k")
    say(good, "верно названный цвет — модель видит картинку")
    answers["said"] = "Я не могу просматривать изображения."
    good, _ = vision.model_sees("m", "http://x/v1", "k")
    say(not good, "отказ смотреть — модель не годится")
    answers["said"] = "синий"
    good, _ = vision.model_sees("m", "http://x/v1", "k")
    say(not good, "цвет назван наугад и мимо — не годится")
finally:
    _ur.urlopen, vision.random.choice = saved_open, saved_choice

print("\n--- выбор модели из списка ---")
names = ["a/first", "b/second", "c/third"]
vision.model_sees = lambda m, url, key, timeout=30: (m == "b/second", "")
try:
    say(vision.pick_visual(names, "", "http://x/v1", "k") == "b/second",
        "берётся та, что прошла проверку, а не первая по списку")
    say(vision.pick_visual(names, "c/third", "http://x/v1", "k") == "b/second",
        "не прошедшая проверку не берётся, даже если выбрана заранее")
    vision.model_sees = lambda m, url, key, timeout=30: (False, "")
    say(vision.pick_visual(names, "", "http://x/v1", "k") == "",
        "никто не прошёл — выбирать нечего")
finally:
    vision.model_sees = saved_sees
say(vision.pick_visual(names) == "a/first",
    "без адреса и ключа проверять нечем — отдаём первого")
say(vision.pick_visual([]) == "", "пустой список не ломает выбор")

# --- 6. рассуждающие модели -----------------------------------------
# Живой случай: deepseek-flash видит картинку, но весь запас токенов тратит
# на размышление, а `content` остаётся пустым. Пока это не учитывалось,
# модель выглядела слепой, а в базу ложился мусор вместо разбора.
print("\n--- модель, которая долго думает ---")
DUMA = {"choices": [{"finish_reason": "length", "message": {
    "content": "", "reasoning_content": "Мы видим изображение. Похоже на..."}}]}
ГОТОВО = {"choices": [{"finish_reason": "stop", "message": {
    "content": '{"тема": "котик"}', "reasoning_content": "думал-думал"}}]}

say(vision._thought_too_long(DUMA),
    "оборванное на размышлении узнаётся")
say(not vision._thought_too_long(ГОТОВО),
    "законченный ответ переспрашивать не нужно")
say(not vision._thought_too_long({"choices": [{"finish_reason": "length",
    "message": {"content": "хвост"}}]}),
    "обрыв обычной модели — это не про рассуждение")
say(vision._answer_of(DUMA).startswith("Мы видим"),
    "пустой content не выдаётся за пустой ответ: берём рассуждение")
say(vision._answer_of(ГОТОВО) == '{"тема": "котик"}',
    "когда ответ есть, рассуждение не мешает")

print("\n--- модальности в корне описания модели ---")
# DeepSeek кладёт input_modalities без обёртки architecture. Пока смотрели
# только внутрь неё, deepseek-flash считался текстовым.
say(vision._visual_by_meta(
    {"id": "deepseek-flash", "input_modalities": ["text", "image"]}) is True,
    "модальности в корне читаются")
say(vision._visual_by_meta(
    {"id": "deepseek-v4-pro", "input_modalities": ["text"]}) is False,
    "текстовая по корневым модальностям отсеивается")
say(vision._visual_by_meta({"id": "x", "architecture": {
    "input_modalities": ["text", "image"]}}) is True,
    "внутри architecture — по-прежнему читаются")

# --- 7. текстовые вопросы на рассуждающей модели --------------------
# Судья темы просил 4 токена и читал только content. На рассуждающей модели
# это давало «нет» ВСЕГДА, то есть вкусы молча отсекали вообще всё.
print("\n--- судья темы, когда модель думает вслух ---")
запросы = []


def fake_completion(model, messages, max_tokens, temperature, timeout, kind=None):
    запросы.append(max_tokens)
    if max_tokens < 100:            # первый, тесный запрос — не успел
        return {"choices": [{"finish_reason": "length", "message": {
            "content": "", "reasoning_content": "думаю над темой..."}}]}
    return {"choices": [{"finish_reason": "stop",
                         "message": {"content": "да"}}]}


saved_completion = vision._completion
saved_available = vision.available
vision._completion = fake_completion
vision.available = lambda kind=None: (True, "модель-думалка")
vision._NEEDS_ROOM.discard("модель-думалка")
try:
    say(vision.judge_topic("готовят борщ", "кулинария") is True,
        f"тесный ответ переспрошен с запасом: {запросы}")
    say(len(запросы) == 2 and запросы[1] > запросы[0],
        "второй запрос больше первого")
    запросы.clear()
    vision.judge_topic("готовят борщ", "кулинария")
    say(len(запросы) == 1,
        f"со второго раза запас сразу большой — лишнего запроса нет: {запросы}")
finally:
    vision._completion, vision.available = saved_completion, saved_available
    vision._NEEDS_ROOM.discard("модель-думалка")

# --- 8. разогрев шлёт настоящую картинку ----------------------------
# DeepSeek отвечает на пиксель 16x16 «unsupported image», и разогрев падал,
# а команда `vision` писала «сервис не ответил» при рабочем ключе.
print("\n--- картинка для разогрева ---")
import inspect  # noqa: E402

исходник = inspect.getsource(vision.warm_up)
say("_TINY_PNG" not in исходник,
    "разогрев больше не шлёт вырожденный пиксель")
say("_solid_png" in исходник, "шлётся нарисованный квадрат")
квадрат = vision._solid_png((10, 10, 10), 32)
say(квадрат[:8] == b"\x89PNG\r\n\x1a\n" and 32 == int.from_bytes(квадрат[16:20], "big"),
    f"квадрат корректный и 32x32, {len(квадрат)} байт")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
