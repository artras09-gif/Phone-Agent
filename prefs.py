"""Настройки, которые меняются мышкой, а не правкой config.py.

Где считать зрение, по какому адресу, какой моделью и с каким телефоном
работать — всё это выбирается в окне и должно переживать перезапуск, поэтому
лежит в `settings.json` рядом с программой. В собранном exe править
`config.py` вообще нельзя — он внутри.

Пусто в любом поле значит «как в config.py»: модель ищется среди загруженных,
адрес берётся стандартный, устройство выбирается автоматически (кабель важнее
сети).

Ключ от сервиса лежит здесь же. `settings.json` намеренно не попадает в
комплект (см. build_exe.BESIDE) — ровно затем, чтобы ключ не уехал вместе с
программой к другому человеку.
"""
import json
import os

import adb
import config

PATH = os.path.join(config.BASE, "settings.json")

DEFAULTS = {
    "serial": "",            # телефон; пусто = выбрать самому
    "vision_provider": "",   # local | api
    "vision_url": "",        # адрес своего сервера (LM Studio, vLLM…)
    "vision_model": "",      # модель на своём сервере
    "api_url": "",           # адрес сервиса по API
    "api_key": "",
    "api_model": "",
}

# Как назывались поля до того, как «облако Qwen» стало «любой сервис по API».
# Прочитать старый файл важнее чистоты: в нём лежит уже вписанный ключ.
RENAMED = {"qwen_key": "api_key", "qwen_model": "api_model"}


def load():
    data = dict(DEFAULTS)
    try:
        with open(PATH, encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, json.JSONDecodeError):
        return data

    for old, new in RENAMED.items():
        if isinstance(saved.get(old), str) and not saved.get(new):
            saved[new] = saved[old]
    # Провайдер тоже переименовался: "qwen" -> "api".
    if saved.get("vision_provider") in ("qwen", "cloud", "dashscope"):
        saved["vision_provider"] = "api"

    for key in DEFAULTS:
        if isinstance(saved.get(key), str):
            data[key] = saved[key].strip()
    return data


def save(**changes):
    data = load()
    data.update({k: str(v).strip() for k, v in changes.items() if k in DEFAULTS})
    with open(PATH, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    apply(data)
    return data


def apply(data=None):
    """Применить настройки к работающему процессу.

    `config.SERIAL` важнее внутреннего выбора adb: когда он задан,
    `_resolve_multiple` не вмешивается, и телефон остаётся тем, что выбрали.

    Пустое поле ничего не перетирает — так переменные окружения и правка
    config.py остаются рабочим путём для того, кто запускает из исходников.
    """
    data = data or load()

    # ВНИМАНИЕ, порядок здесь не косметический. `ANDROID_SERIAL` важнее
    # сохранённого выбора: сохранённый — это «какой телефон я ткнул мышкой в
    # окне», то есть значение по умолчанию, а переменная окружения — прямое
    # назначение от того, кто запустил процесс. Надзиратель (`fleet`) именно
    # ею и раздаёт телефоны своим службам.
    #
    # Пока было наоборот, все службы разом уезжали на телефон из
    # settings.json: три процесса вели ОДИН телефон, а два других стояли.
    chosen = os.environ.get("ANDROID_SERIAL") or data["serial"] or None
    config.SERIAL = chosen
    if chosen:
        adb.prefer(chosen)

    for field, option in (("vision_provider", "VISION_PROVIDER"),
                          ("vision_url", "VISION_URL"),
                          ("vision_model", "VISION_MODEL"),
                          ("api_url", "API_URL"),
                          ("api_key", "API_KEY"),
                          ("api_model", "API_MODEL")):
        if data[field]:
            setattr(config, option, data[field])
    # Модель на своём сервере — единственное поле, где пусто значит «ищи
    # сам», и это осмысленный выбор: его надо уметь вернуть обратно.
    config.VISION_MODEL = data["vision_model"]

    # Вписали ОДИН ключ и больше ничего — остальное подставляем сами.
    # Пустой адрес у сервиса означал бы «не задан адрес сервиса» и молча
    # выключенное зрение, а выбирать сервис из списка после того, как ключ
    # уже вставлен, человеку незачем: по самому ключу видно, чей он.
    if str(config.VISION_PROVIDER).strip().lower() in (
            "api", "qwen", "cloud", "dashscope", "облако"):
        url, model = config.api_defaults(config.API_KEY, config.API_URL)
        if not config.API_URL.strip():
            config.API_URL = url
        if not (config.API_MODEL or "").strip():
            config.API_MODEL = model

    # Очередь к модели зависит от того, где она считается, а провайдера
    # только что могли поменять. В config.py это значение вычисляется при
    # загрузке модуля и после переключения на облако осталось бы прежним —
    # телефоны продолжали бы ходить к сервису по одному.
    if config.VISION_PROVIDER == "local":
        config.VISION_MAX_PARALLEL = max(1, config.VISION_MAX_PARALLEL or 1)
    else:
        config.VISION_MAX_PARALLEL = 0
    return data
