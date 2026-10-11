"""Что, куда и когда публиковать — разбор подписи к видео.

Один разбор на все входы: подпись к видео в Telegram-боте и `.txt` рядом с
видео в папке `watch/`. Два вида записи.

1. ШАБЛОН (2026-10-11, «нужно понять, какая система у поста должна быть,
   чтобы бот запостил всё, что нужно, с нужным описанием и в нужное время»).
   Служебные строки начинаются со слова и двоеточия, всё после «описание:»
   уходит в пост КАК ЕСТЬ — с переносами строк, хэштегами и ссылками:

       куда: тикток, рилс
       когда: завтра 18:30
       описание:
       Текст поста как есть #хэштег
       вторая строка

   Своё описание для отдельной сети — «описание шортс: …» (у YouTube это
   название ролика, до 100 знаков). Строки «куда»/«когда» необязательны:
   без «куда» — во все сети, без «когда» — сразу.

2. СВОБОДНАЯ ПОДПИСЬ (как было): названия сетей и «в 18:30» ищутся прямо в
   тексте и из него вырезаются. Удобно для короткой подписи, но слово
   «тикток» внутри описания тоже примется за сеть — для точного текста
   нужен шаблон.
"""
import datetime as dt
import re

# Как назвать сеть. Ключ — имя маршрута в recipes.json. Это ОСНОВЫ слов: к
# ним разрешено до трёх букв хвоста, чтобы ловились падежи («в инсту»).
ALIASES = {
    "tiktok": ("tiktok", "тикток", "тик-ток", "тт"),
    "shorts": ("shorts", "шорт", "youtube", "ютуб", "yt"),
    "reels": ("reels", "рилс", "рилз", "instagram", "инст", "ig"),
}
TITLES = {"tiktok": "TikTok", "shorts": "YouTube Shorts", "reels": "Instagram Reels"}

# YouTube обрезает название на ста знаках; в поле его вбивается именно
# описание поста.
LIMITS = {"shorts": 100, "tiktok": 2200, "reels": 2200}

KEY_WHERE = ("куда", "сети", "соцсети", "где")
KEY_WHEN = ("когда", "время", "дата")
KEY_TEXT = ("описание", "текст", "подпись")
ALL_WORDS = ("все", "всё", "везде", "all")
NOW_WORDS = ("сейчас", "сразу", "now", "немедленно")

WEEKDAYS = {"пн": 0, "понедельник": 0, "вт": 1, "вторник": 1, "ср": 2, "среда": 2,
            "среду": 2, "чт": 3, "четверг": 3, "пт": 4, "пятница": 4, "пятницу": 4,
            "сб": 5, "суббота": 5, "субботу": 5, "вс": 6, "воскресенье": 6}

RE_KEY = re.compile(r"^\s*([а-яёa-z]+)(?:\s+([а-яёa-z-]+))?\s*:\s?(.*)$", re.I)
RE_TIME = re.compile(r"(?<![\d.:])([01]?\d|2[0-3])[:.]([0-5]\d)(?![\d.:])")
RE_DATE = re.compile(r"(?<![\d:])([0-3]?\d)\.([01]?\d)(?:\.(\d{2,4}))?(?![\d:])")
RE_FREE_TIME = re.compile(r"(?:^|\s)(?:в\s*)?([01]?\d|2[0-3])[:.]([0-5]\d)(?=\s|$)")


class SpecError(ValueError):
    """Подпись не разобрать — текст ошибки уходит человеку как есть."""


def target_of(word):
    """«рилс» -> "reels"; не сеть — None."""
    word = word.strip().lower().lstrip("#")
    for target, stems in ALIASES.items():
        for stem in stems:
            if word.startswith(stem) and len(word) - len(stem) <= 3:
                return target
    return None


def is_template(text):
    """Похоже ли на шаблон: есть хоть одна служебная строка «ключ: …»."""
    for line in str(text or "").splitlines():
        m = RE_KEY.match(line)
        if m and _key_kind(m.group(1), m.group(2)) is not None:
            return True
    return False


def _key_kind(word, extra):
    word = word.lower()
    if word in KEY_WHERE and not extra:
        return "where"
    if word in KEY_WHEN and not extra:
        return "when"
    if word in KEY_TEXT:
        if not extra:
            return "text"
        target = target_of(extra)
        return ("text", target) if target else None
    return None


def parse_when(text, now=None):
    """«завтра 18:30», «12.10 9:00», «пт 18:00», «18:30», «сейчас».

    Возвращает (datetime или None, точное ли время). None — публиковать сразу.
    Время без даты в прошлом — значит завтра («в 9:00», присланное вечером).
    Дата в прошлом — ошибка: молча переносить на год нельзя.
    """
    now = now or dt.datetime.now()
    raw = str(text or "").strip().lower()
    if not raw or any(w == raw or raw.startswith(w + " ") for w in NOW_WORDS):
        return None, False

    # «12.10 18:30»: и дата, и время подходят под «ЧЧ.ММ». Время — то, что
    # через двоеточие; если двоеточия нет нигде — последнее из найденных.
    found = list(RE_TIME.finditer(raw))
    colon = [m for m in found if ":" in m.group(0)]
    t = (colon or found or [None])[-1]
    rest = (raw[:t.start()] + " " + raw[t.end():]) if t else raw
    day, explicit, weekday = None, False, False
    d = RE_DATE.search(rest)
    if d:
        year = int(d.group(3)) if d.group(3) else now.year
        if year < 100:
            year += 2000
        try:
            day = dt.date(year, int(d.group(2)), int(d.group(1)))
        except ValueError:
            raise SpecError(f"не понял дату «{d.group(0)}» — пиши как 12.10 или 12.10.2026")
        if not d.group(3) and day < now.date():
            day = day.replace(year=day.year + 1)      # «05.01», присланное в декабре
        explicit = True
    elif "послезавтра" in rest:
        day, explicit = now.date() + dt.timedelta(days=2), True
    elif "завтра" in rest:
        day, explicit = now.date() + dt.timedelta(days=1), True
    elif "сегодня" in rest:
        day, explicit = now.date(), True
    else:
        for word in re.findall(r"[а-яё]+", rest):
            if word in WEEKDAYS:
                ahead = (WEEKDAYS[word] - now.weekday()) % 7
                day, explicit, weekday = now.date() + dt.timedelta(days=ahead), True, True
                break

    if not t:
        if day is None:
            raise SpecError(f"не понял время «{text}» — пиши как 18:30, "
                            "«завтра 18:30», «12.10 18:30» или «сейчас»")
        raise SpecError(f"в «{text}» есть день, но нет часа — допиши, например «{text} 18:30»")

    clock = dt.time(int(t.group(1)), int(t.group(2)))
    if day is None:
        when = dt.datetime.combine(now.date(), clock)
        if when <= now:
            when += dt.timedelta(days=1)
        return when, True
    when = dt.datetime.combine(day, clock)
    if when <= now:
        if not weekday:
            raise SpecError(f"{when:%d.%m %H:%M} уже прошло")
        when += dt.timedelta(days=7)                   # «пт 18:00» в пятницу вечером
    return when, True


def _targets_of(text):
    raw = str(text or "").strip().lower()
    if not raw or raw in ALL_WORDS:
        return list(ALIASES)
    found, bad = [], []
    for word in re.split(r"[,;/\s]+|\sи\s", raw):
        if not word or word in ("и", "в"):
            continue
        target = target_of(word)
        if target:
            if target not in found:
                found.append(target)
        elif word not in ALL_WORDS:
            bad.append(word)
    if bad:
        raise SpecError("не знаю сеть «%s» — можно: тикток, рилс (инстаграм), "
                        "шортс (ютуб) или «все»" % ", ".join(bad))
    return found or list(ALIASES)


def _template(text, now):
    targets, when, exact = list(ALIASES), None, False
    main, per = [], {}
    current = None                      # куда пишем строки описания
    for line in str(text).splitlines():
        m = RE_KEY.match(line)
        kind = _key_kind(m.group(1), m.group(2)) if m else None
        if kind is None:
            if current is None:
                current = main          # текст без «описание:» — тоже описание
            current.append(line)
            continue
        value = m.group(3)
        if kind == "where":
            targets = _targets_of(value)
            current = None
        elif kind == "when":
            when, exact = parse_when(value, now)
            current = None
        else:
            target = kind[1] if isinstance(kind, tuple) else None
            current = per.setdefault(target, []) if target else main
            if value.strip():
                current.append(value)
    caption = "\n".join(main).strip()
    captions = {t: "\n".join(lines).strip() for t, lines in per.items()
                if "\n".join(lines).strip()}
    return targets, when, exact, caption, captions


def _free(text, now):
    """Свободная подпись — как было: сети и время ищутся в самом тексте."""
    # Адреса на время разбора прячем: иначе «instagram», «reels», «shorts»
    # ловятся внутри ссылки и вырезаются из неё.
    import weblink

    stashed = []

    def hide(match):
        stashed.append(match.group(0))
        return "\x00{}\x00".format(len(stashed) - 1)

    found, cleaned = [], weblink.RE_BARE.sub(hide, weblink.RE_URL.sub(hide, text))
    for target, words in ALIASES.items():
        for word in words:
            pattern = re.compile(rf"(?<!\w)#?{re.escape(word)}\w{{0,3}}(?!\w)",
                                 re.IGNORECASE | re.UNICODE)
            if pattern.search(cleaned):
                if target not in found:
                    found.append(target)
                cleaned = pattern.sub(" ", cleaned)
    cleaned = re.sub(r"\x00(\d+)\x00", lambda m: stashed[int(m.group(1))], cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip(" ,.;:—-")

    when = None
    match = RE_FREE_TIME.search(cleaned)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        when = (now or dt.datetime.now()).replace(hour=hour, minute=minute,
                                                  second=0, microsecond=0)
        if when <= (now or dt.datetime.now()):
            when += dt.timedelta(days=1)
        cleaned = re.sub(r"[ \t]{2,}", " ", RE_FREE_TIME.sub(" ", cleaned, count=1)).strip()
    return found or list(ALIASES), when, when is not None, cleaned, {}


def parse(text, now=None):
    """Разобрать подпись. Возвращает dict:

        targets  — сети по порядку публикации;
        when     — datetime или None (сразу);
        exact    — время названо явно: ради него можно прервать сессию;
        caption  — общее описание;
        captions — своё описание для отдельных сетей {сеть: текст};
        template — подпись была шаблоном (текст уходит без вырезаний);
        notes    — предупреждения для человека (обрезка названия YouTube).
    Ошибка разбора — SpecError с понятным текстом.
    """
    now = now or dt.datetime.now()
    text = str(text or "").replace("\r\n", "\n").strip()
    template = is_template(text)
    targets, when, exact, caption, captions = (_template if template else _free)(text, now)
    spec = {"targets": targets, "when": when, "exact": exact, "caption": caption,
            "captions": {t: c for t, c in captions.items() if t in targets},
            "template": template, "notes": []}
    for target in captions:
        if target not in targets:
            spec["notes"].append(f"есть описание для {TITLES[target]}, но в «куда» "
                                 "этой сети нет — туда не публикую")
    for target in targets:
        limit = LIMITS.get(target)
        body = caption_for(spec, target)
        if limit and len(body) > limit:
            spec["notes"].append(
                f"{TITLES[target]}: описание {len(body)} знаков, влезет {limit} — "
                + ("обрежу по последнему целому слову. Своё короткое: строка "
                   "«описание шортс: …»" if target == "shorts" else "лишнее обрежу"))
    return spec


def caption_for(spec, target):
    """Описание для конкретной сети: своё, если задано, иначе общее."""
    captions = spec.get("captions") or {}
    return (captions.get(target) or spec.get("caption") or "").strip()


def shorts_title(text):
    """Название ролика YouTube из описания: первая непустая строка.

    В поле названия влезает 100 знаков, и туда уходит описание. Многострочное
    описание целиком в название не годится — берём первую строку.
    """
    for line in str(text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def fit(text, target):
    """Уложить описание в предел сети — по последнему целому слову."""
    limit = LIMITS.get(target)
    text = str(text or "").strip()
    if not limit or len(text) <= limit:
        return text
    cut = text[:limit]
    space = max(cut.rfind(" "), cut.rfind("\n"))
    return (cut[:space] if space > limit * 0.6 else cut).rstrip(" ,.;:—-\n")


def describe(spec, job_id=None, now=None):
    """Подтверждение человеку: ровно то, что уйдёт в пост, — без догадок."""
    today = (now or dt.datetime.now()).date()
    head = f"Задача #{job_id}" if job_id is not None else "Пост"
    where = ", ".join(TITLES.get(t, t) for t in spec["targets"])
    if spec["when"] is None:
        when = "сейчас"
    else:
        days = (spec["when"].date() - today).days
        day = {0: "сегодня", 1: "завтра", 2: "послезавтра"}.get(
            days, f"{spec['when']:%d.%m}")
        when = f"{day} в {spec['when']:%H:%M}"
    lines = [f"{head}", f"Куда: {where}", f"Когда: {when}"]
    shared = [t for t in spec["targets"] if t not in (spec.get("captions") or {})]
    if shared:
        body = spec["caption"] or "(без описания)"
        label = "Описание" if len(shared) == len(spec["targets"]) else \
            "Описание (" + ", ".join(TITLES[t] for t in shared) + ")"
        lines += ["", f"{label}:", body]
    for target, body in (spec.get("captions") or {}).items():
        lines += ["", f"Описание для {TITLES[target]}:", body]
    if spec["notes"]:
        lines += [""] + ["⚠ " + n for n in spec["notes"]]
    return "\n".join(lines)


TEMPLATE_HELP = """Шаблон поста — пришли ВИДЕО файлом, а это в подписи:

куда: тикток, рилс, шортс
когда: завтра 18:30
описание:
Текст поста как есть #хэштеги
можно в несколько строк

Что можно писать:
• куда — тикток, рилс (инстаграм), шортс (ютуб), «все». Нет строки — во все.
• когда — 18:30, «сегодня 18:30», «завтра 9:00», «12.10 18:30», «пт 20:00», «сейчас». Нет строки — сразу.
• описание — всё после него уходит в пост без изменений.
• своё описание для сети — «описание шортс: Название до 100 знаков» (у YouTube это название).

Пост со временем выходит точно в срок: если в этот момент идёт сессия, я её приостановлю, опубликую и досмотрю остаток.
Посмотреть очередь — /queue, отменить — /cancel НОМЕР."""
