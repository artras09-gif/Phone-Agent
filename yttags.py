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
Нужно {lo}–{hi} тегов: по чему этот ролик будут искать, от общих к точным,
без повторов и без «#». Каждый тег — 1–4 слова.
ЯЗЫК: описание {lang_phrase}. ВСЕ теги — только на этом языке, ничего не
переводи на другие языки. Имена и названия (After Effects, ООН, Gaza) пиши
так, как они написаны в описании.
Ответь ТОЛЬКО тегами через запятую, одной строкой, без пояснений.

Описание ролика:
{caption}"""

# Язык описания — чтобы теги были на нём же (жалоба 2026-10-23: к испанскому
# «UN VOTO CONTRA 14» пришли русские теги — в просьбе стояло «смесь русских
# и английских»). Частые служебные слова каждого языка; побеждает тот, чьих
# слов в описании больше.
LANGS = {
    "ru": ("на русском", "и в не на что как это по с для из от к все так он она мы вы"),
    "uk": ("на украинском", "і та що як це по з для від до все так він вона ми ви є"),
    "es": ("на испанском", "el la los las de del que y en un una por con para es se su al lo como más pero"),
    "pt": ("на португальском", "o a os as de do da que e em um uma por com para é não se mais mas"),
    "en": ("на английском", "the a an of and to in is it for on with that this you are be at by from"),
    "fr": ("на французском", "le la les de des du et en un une est pour que qui dans pas sur au avec"),
    "de": ("на немецком", "der die das und ist nicht ein eine zu mit den von auf für sich dem auch"),
    "it": ("на итальянском", "il la di che e un una per non sono con del della gli le si come"),
}
_WORDS = {code: set(words.split()) for code, (_, words) in LANGS.items()}


def language(caption):
    """Код языка описания ("es", "ru"…) или "" — не понять."""
    words = re.findall(r"[^\W\d_]+", (caption or "").lower())
    if not words:
        return ""
    cyr = sum(1 for w in words if re.search(r"[а-яёіїєґ]", w))
    pool = ("ru", "uk") if cyr > len(words) / 2 else tuple(c for c in LANGS if c not in ("ru", "uk"))
    score = {c: sum(1 for w in words if w in _WORDS[c]) for c in pool}
    best = max(score, key=score.get)
    if score[best] == 0:
        return "ru" if cyr > len(words) / 2 else ""
    return best


def _same_language(tags, caption, lang):
    """Выбросить теги не на языке описания. Латиница при кириллическом
    описании остаётся, только если это слово из самого описания (sfx,
    After Effects); кириллица при латинском описании не остаётся никогда."""
    if not lang:
        return tags
    text = (caption or "").lower()
    out = []
    for tag in tags:
        has_cyr = bool(re.search(r"[а-яёіїєґ]", tag.lower()))
        if lang in ("ru", "uk"):
            if not has_cyr and tag.lower() not in text:
                continue
        elif has_cyr:
            continue
        out.append(tag)
    return out


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
        lang = language(caption)
        phrase = f"{LANGS[lang][0]} языке" if lang else "на том языке, на котором оно написано"
        answer = vision.ask_text(PROMPT.format(lo=COUNT[0], hi=COUNT[1], lang_phrase=phrase,
                                               caption=caption[:1500]),
                                 max_tokens=300, temperature=0.2, timeout=timeout)
        tags = _same_language(clean(answer), caption, lang)
        if len(tags) >= 3:
            LAST_SOURCE = "model"
            return tags
    except Exception:
        pass
    LAST_SOURCE = "hashtags"
    return from_hashtags(caption)
