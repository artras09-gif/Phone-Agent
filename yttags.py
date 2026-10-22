"""Теги для YouTube по описанию ролика — их подбирает модель (DeepSeek).

Просьба 2026-10-19: «чтобы на основе описания дипсик ещё подбирал теги и
вбивал в ютубе». Теги в приложении YouTube — «Добавьте информацию» →
«Развернуть» → «Атрибуты» → «Добавьте теги», по одному, каждый с Enter (через
запятую приложение склеивает всё в ОДИН тег — проверено живьём).

У YouTube предел — 500 знаков на все теги вместе; берём с запасом. Хэштеги из
описания — хорошая подсказка модели, но тегами они не дублируются как есть:
теги — это слова, по которым ролик ищут, без «#».
"""
import re

MAX_TOTAL = 450          # предел YouTube — 500 знаков на все теги
MAX_TAG = 40
COUNT = (6, 10)          # «до 10 тегов достаточно» (2026-10-19)

PROMPT = """Подбери теги для YouTube к ролику по его описанию.
Нужно {lo}–{hi} тегов: по чему этот ролик будут искать. Смесь русских и
английских, от общих к точным, без повторов и без «#». Каждый тег — 1–4 слова.
Ответь ТОЛЬКО тегами через запятую, одной строкой, без пояснений.

Описание ролика:
{caption}"""


def clean(raw):
    """Строка модели -> список тегов: без «#», кавычек, повторов, в пределе."""
    out, seen, total = [], set(), 0
    for part in re.split(r"[,\n;]+", str(raw or "")):
        tag = re.sub(r"^[\s\-•*\d.)]+", "", part)          # «1. тег», «- тег»
        tag = tag.strip().strip("#\"'«»`").strip()
        tag = re.sub(r"\s+", " ", tag)
        if not tag or len(tag) > MAX_TAG or tag.lower() in seen:
            continue
        # Пробел внутри — YouTube считает тег в кавычках: +2 знака; запятая — +1.
        cost = len(tag) + (2 if " " in tag else 0) + 1
        if total + cost > MAX_TOTAL:
            break
        seen.add(tag.lower())
        out.append(tag)
        total += cost
    return out[:COUNT[1]]


def from_hashtags(caption):
    """Запасной путь без модели: хэштеги описания как теги."""
    return clean(", ".join(h for h in re.findall(r"#([\w-]+)", caption or "")))


# Откуда теги последнего `suggest`: "model" или "hashtags". Черновик бота
# говорит об этом вслух: «подобрал DeepSeek» при тегах из хэштегов — неправда
# (живьём 2026-10-22: без ключа теги совпали с хэштегами, а надпись осталась).
LAST_SOURCE = ""


def suggest(caption, timeout=40):
    """Теги к ролику: от модели, а не вышло — из хэштегов описания."""
    global LAST_SOURCE
    caption = str(caption or "").strip()
    LAST_SOURCE = ""
    if not caption:
        return []
    try:
        import vision
        answer = vision.ask_text(PROMPT.format(lo=COUNT[0], hi=COUNT[1], caption=caption[:1500]),
                                 max_tokens=300, temperature=0.3, timeout=timeout)
        tags = clean(answer)
        if len(tags) >= 3:
            LAST_SOURCE = "model"
            return tags
    except Exception:
        pass
    LAST_SOURCE = "hashtags"
    return from_hashtags(caption)
