"""Что смотреть: тема и языки. Правится в interests.json.

Раньше тут были списки интересных и скучных категорий, слов и языков —
получалась путаница, в которой настройки противоречили друг другу
(тема «новости» в интересном и категория «новости» в скучном одновременно).
Теперь ровно два поля и три градации:

    реклама                -> «реклама»      листаем сразу, что бы ни совпало
    совпало и то и другое  -> «интересно»    смотрим полностью, можем лайкнуть
    совпало что-то одно    -> «нейтрально»   смотрим вполглаза, лайк никогда
    не совпало ничего      -> «мимо»         листаем сразу

Считаются только заданные условия. Если язык не задан, «совпало одно» и
«совпало всё» — это одно и то же, средней ступени просто не бывает, и
неподходящее уходит в «мимо». Так задумано: нейтральное имеет смысл
только когда есть чему не совпасть.

ТЕМА ВАЖНЕЕ ЯЗЫКА (2026-10-08, просьба пользователя: «больший акцент на
теме, чем на языке — некоторые соцсети вообще не могут уловить тему»).
Лента учится на досмотрах и лайках. Пока ролик «на своём языке, но не про
то» досматривался до десяти секунд, а «про то, но на чужом языке» не
лайкался, ленте шёл сигнал скорее про язык. Теперь:

    тема + язык            -> интересно   20-30 с, лайк
    тема, язык чужой       -> нейтрально  15-25 с, лайк вдвое реже
    только язык, тема мимо -> мимо        листаем сразу
                              (вернуть просмотр — секунд_на_язык_без_темы > 0)

Языков можно несколько — «ru, en, es»: подходит любой из них.

УМНАЯ ГРАДАЦИЯ: СНАЧАЛА ЯЗЫК, ПОТОМ ТЕМА (2026-10-11). Новый телефон быстро
подстраивается под язык, а с темой труднее: лента на испанском, но про
«каких-то мамочек». Поэтому приоритет зависит от того, насколько лента уже
настроена (`tuning`, по последним роликам ЭТОЙ соцсети на ЭТОМ телефоне):

    этап «язык» — роликов на нужном языке меньше 60%: учим язык.
        Свой язык без темы смотрим вполглаза (3-7 с), без лайка.
    этап «тема» — язык настроен: всё внимание теме.
        Свой язык без темы листаем сразу: досмотр учил бы ленту «мамочкам».
        Чем реже попадается тема, тем сильнее сигнал на каждом попадании:
        лайк чаще (до x2) и иногда пересмотр — лента учится на них.
"""
import json
import os
import random
import re

import config

DEFAULTS = {
    "тема": "",          # пусто = тема не важна, смотрим всё на нужном языке
    "язык": "",          # пусто = язык не важен; два — через запятую: «ru, en»
    "не_смотреть": "",   # что листать сразу, по смыслу: «нейросети, мамы с детьми»
    "поведение": {
        "секунд_на_взгляд": [1.5, 3.0],
        # Нейтральное бывает двух сортов, и они не равны: совпавшая ТЕМА на
        # чужом языке ценнее, чем свой язык на посторонней теме. Оба заданы
        # прямыми секундами, а не долей: доля считалась от обычного просмотра,
        # а тот сам случайный — выходило то 1.5, то 4 секунды, предсказать
        # нельзя. Где нужна понятная длительность, там и пишем секунды.
        "секунд_на_интересное": [20.0, 30.0],
        "секунд_на_тему_без_языка": [15.0, 25.0],
        # Ноль — ролик на своём языке, но не по теме, листается сразу, как
        # любой неподходящий. Больше нуля — смотрится вполглаза, как раньше.
        "секунд_на_язык_без_темы": [0.0, 0.0],
        "секунд_на_неинтересное": [0.0, 0.0],
        "лайк": 0.25,
        # Лайк ролику по теме на чужом языке — доля от обычного лайка. Тянет
        # ленту к теме, а её ленте уловить труднее всего.
        "лайк_теме_без_языка": 0.5,
        # Градация «сначала язык, потом тема». Пока роликов на нужном языке
        # в последних `окно_настройки` меньше `язык_настроен_доля`, ролик на
        # своём языке, но не по теме, смотрится `секунд_на_язык_пока_учим`.
        "секунд_на_язык_пока_учим": [3.0, 7.0],
        "язык_настроен_доля": 0.6,
        "окно_настройки": 40,
    },
}

# Версия формата. 2 — тема важнее языка (2026-10-08). Значения в файле,
# совпадающие с прежними умолчаниями, — не выбор человека, а записанные
# когда-то умолчания: их заменяем новыми. Своё он сохранит.
VERSION = 2
OLD_DEFAULTS = {
    "секунд_на_тему_без_языка": [10.0, 15.0],
    "секунд_на_язык_без_темы": [1.5, 10.0],
}

# Сколько языков можно выбрать сразу. Ограничение не по смыслу, а от мусора:
# в строку можно вписать что угодно.
MAX_LANGUAGES = 8


def load():
    """Прочитать настройки. Файла нет или он битый — работаем на умолчаниях."""
    data = json.loads(json.dumps(DEFAULTS))
    try:
        with open(config.INTERESTS, encoding="utf-8") as f:
            user = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        data["_ошибка"] = str(e)
        return data

    data["тема"] = str(user.get("тема", "")).strip().lower()
    data["не_смотреть"] = str(user.get("не_смотреть", "")).strip().lower()
    data["язык"] = str(user.get("язык", "")).strip().lower()

    # Перенос со старого формата со списками: берём первое, что там было.
    if not data["тема"]:
        old = (user.get("интересно") or {}).get("слова") or []
        data["тема"] = str(old[0]).strip().lower() if old else ""
    if not data["язык"]:
        old = (user.get("интересно") or {}).get("языки") or []
        data["язык"] = str(old[0]).strip().lower() if old else ""

    try:
        old_format = int(user.get("_версия", 1)) < VERSION
    except (TypeError, ValueError):
        old_format = True
    if isinstance(user.get("поведение"), dict):
        for key, value in user["поведение"].items():
            if key not in data["поведение"]:
                continue
            if old_format and _same_numbers(value, OLD_DEFAULTS.get(key)):
                continue                 # старое умолчание — берём новое
            data["поведение"][key] = value
    data["язык"] = ", ".join(languages(data))
    return data


def _same_numbers(a, b):
    try:
        return b is not None and [float(x) for x in a] == [float(x) for x in b]
    except (TypeError, ValueError):
        return False


def languages(taste=None):
    """Языки списком: «ru, en» -> ["ru", "en"]. Не больше MAX_LANGUAGES."""
    taste = taste if taste is not None else load()
    raw = taste.get("язык", "") if isinstance(taste, dict) else str(taste)
    out = []
    for part in re.split(r"[,;/\s]+", str(raw).strip().lower()):
        if part and part != "-" and part not in out:
            out.append(part)
    return out[:MAX_LANGUAGES]


NOTE = [
    "Всего две настройки: тема и язык (языков можно несколько через запятую: «ru, en, es»).",
    "Тема — своими словами: «новости», «коты», «мемы про политику».",
    "Тем можно несколько через запятую: «коты, кулинария, мемы».",
    "Достаточно совпадения по ЛЮБОЙ из них.",
    "Модель сверяет описание ролика с темой по смыслу, а не по буквам.",
    "Язык — ru, en, es, de, fr… Пусто = не проверять. Несколько — подходит любой.",
    "Градация: пока лента не настроена на язык (меньше язык_настроен_доля роликов",
    "на нужном языке), свой язык без темы смотрю секунд_на_язык_пока_учим.",
    "Язык настроен — всё внимание теме: свой язык без темы листаю сразу,",
    "а редкую тему лайкаю чаще, чтобы лента её поняла.",
    "Совпало и то и другое — смотрю полностью и могу лайкнуть.",
    "Совпала тема, но язык чужой — смотрю секунд_на_тему_без_языка, лайк вдвое реже.",
    "Совпал только язык — листаю (секунд_на_язык_без_темы больше нуля — смотрю вполглаза).",
    "Не совпало ничего — листаю сразу.",
    "Рекламу листаю сразу в любом случае: досмотр она читает как «хочу ещё».",
    "После правки ничего перезапускать не надо — файл читается каждую сессию.",
]


def save(taste):
    """Записать настройки обратно в файл."""
    data = {"_как_пользоваться": NOTE,
            "_версия": VERSION,
            "тема": taste.get("тема", ""),
            "язык": ", ".join(languages(taste)),
            "не_смотреть": taste.get("не_смотреть", ""),
            "поведение": taste["поведение"]}
    with open(config.INTERESTS, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return config.INTERESTS


def set_choice(topic=None, lang=None, taste=None, banned=None):
    """Задать тему, языки, «не смотреть». None — не трогать."""
    taste = taste or load()
    taste.pop("_ошибка", None)
    if topic is not None:
        taste["тема"] = topic.strip().lower()
    if banned is not None:
        taste["не_смотреть"] = ", ".join(topics({"тема": banned}))
    if lang is not None:
        taste["язык"] = ", ".join(languages({"язык": lang}))
    save(taste)
    return taste


# ------------------------------------------------- разворот темы в слова

EXPANSIONS = os.path.join(config.BASE, "topics_cache.json")


def load_expansions():
    try:
        with open(EXPANSIONS, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def topics(taste=None):
    """Темы списком. В настройке они пишутся через запятую.

    Совпадения достаточно по любой: «коты, кулинария» — это «коты ИЛИ
    кулинария», а не «и то и другое сразу».
    """
    taste = taste if taste is not None else load()
    raw = taste.get("тема", "") if isinstance(taste, dict) else str(taste)
    return [t.strip().lower() for t in str(raw).split(",") if t.strip()]


def ensure_expansion(topic, expander):
    """Развернуть темы в слова и запомнить. Нужно, когда модель недоступна.

    Разворачивание вынесено наружу: модулю всё равно, кто именно превращает
    «политику» в «депутат, выборы, митинг». Принимает и одну тему строкой,
    и несколько через запятую — разворачивается каждая.
    """
    cache = load_expansions()
    wanted = [t for t in topics({"тема": topic}) if t not in cache]
    if not wanted:
        return cache

    for key in wanted:
        try:
            cache[key] = expander(key)
        except Exception:
            cache[key] = []
    try:
        with open(EXPANSIONS, "w", encoding="utf-8", newline="\n") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
    return cache


# ----------------------------------------------------- поиск по словам

_ENDINGS = ("иями", "ами", "ями", "ому", "ему", "ого", "его", "ыми", "ими",
            "ов", "ев", "ей", "ой", "ый", "ая", "ое", "ые", "ий", "ья", "ую",
            "юю", "ых", "их", "ам", "ям", "ах", "ях", "ом", "ем", "ет", "ит",
            "ут", "ют", "ат", "ят", "ла", "ло", "ли", "ть",
            "а", "я", "ы", "и", "у", "ю", "е", "о", "ь", "й")

MIN_WORD = 3


def _stem(word):
    """Слово без окончания: «котом» -> «кот», «монтажную» -> «монтажн»."""
    for ending in _ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= MIN_WORD:
            return word[:-len(ending)]
    return word


def _stems(text):
    return {_stem(w) for w in re.findall(r"\w+", text.lower())
            if len(w) >= MIN_WORD}


def matches(entry, text, text_stems=None):
    """Встречается ли слово в описании, с поправкой на склонения.

    Слово может прийти из `topics_cache.json`, а его правят руками — число
    или null вместо строки роняли решение прямо посреди сессии.
    """
    entry = str(entry or "").strip().lower()
    text = str(text or "")
    if not entry:
        return False
    stems = text_stems if text_stems is not None else _stems(text)
    words = [w for w in re.findall(r"\w+", entry) if len(w) >= MIN_WORD]
    if not words:
        return entry in text.lower()

    for word in words:
        root = _stem(word)
        for stem in stems:
            short, long_ = sorted((root, stem), key=len)
            if long_.startswith(short) and len(long_) - len(short) <= 1:
                return True
    return False


# ----------------------------------------------------------- решение

def _haystack(frame):
    return " ".join(str(frame.get(key, "")) for key in
                    ("тема", "текст", "описание", "автор")).lower()


def _lang_matches(frame, want_lang):
    """Совпал ли язык (любой из заданных). Неизвестный не считается чужим.

    Модель называет язык не всегда, и наказывать ролик за её молчание
    нельзя: так под нож уходили бы кадры без речи.

    В причине пишем ОБА языка. Раньше стоял только распознанный, и строка
    «не язык «ru»» читалась как «модель не смогла определить русский», хотя
    означала «распознан русский, а в настройках стоит английский».
    """
    wanted = languages({"язык": want_lang})
    lang = str(frame.get("язык", "")).strip().lower()
    if not lang:
        return True, "язык не разобран"
    if lang in wanted:
        return True, f"язык {lang}"
    return False, f"язык {lang}, а нужен {' или '.join(wanted)}"


def _one_topic_matches(frame, want_topic, expansions, judge):
    """Про эту ли тему ролик. Сначала спрашиваем модель, потом ищем словами."""
    theme = str(frame.get("тема", "")).strip()
    if judge and theme:
        try:
            # Модель посмотрела и сказала «не про это» — ей и верим.
            return bool(judge(theme, want_topic)), ""
        except Exception:
            pass        # модель отвалилась — падаем на поиск по словам

    text = _haystack(frame)
    stems = _stems(text)
    expansions = expansions if expansions is not None else load_expansions()
    if matches(want_topic, text, stems):
        return True, ""
    for related in expansions.get(want_topic, []):
        if matches(related, text, stems):
            return True, related
    return False, ""


def _topic_matches(frame, want, expansions=None, judge=None):
    """Подходит ли ролик хоть под одну из тем.

    Тем может быть несколько через запятую, и достаточно любой. С моделью-
    судьёй вопросы по всем темам задаются ОДНОВРЕМЕННО: по очереди они
    складывались, и на двух темах «мимо» стоило лишние 1.4-2.8 с (журнал
    2026-10-01: «тема 2.97» при «модель 2.56»). Лишний запрос дешевле этих
    секунд — ролик всё это время висит на экране.
    """
    wanted = topics({"тема": want})
    if not wanted:
        return True, ""

    # Облачная модель уже ответила «по теме» прямо в разборе кадра
    # (vision.EXTRA_FIELDS) — второй запрос к судье не нужен. Замер
    # 2026-10-01: 29 из 30 совпадений с отдельным судьёй, а экономия —
    # целый запрос (1.2-1.6 с) на каждом ролике.
    said = frame.get("по_теме")
    if isinstance(said, bool):
        if not said:
            if len(wanted) == 1:
                return False, f"тема не «{wanted[0]}»"
            return False, "тема не из списка: " + ", ".join(f"«{t}»" for t in wanted)
        # «Да» по кадру — повод, а не приговор (жалоба 2026-10-12: нейроролик
        # «мама с младенцем» прошёл как «монтаж»). Полный просмотр и лайк
        # учат ленту, поэтому попадание подтверждает судья по описанию:
        # стоит запрос только на попаданиях, «мимо» он не замедляет.
        if judge is not None:
            confirmed = _confirm_topic(frame, wanted, judge)
            if confirmed is False:
                return False, "по кадру похоже на тему, но описание не про неё"
            if confirmed:
                return True, f"тема «{confirmed}»"
        return True, (f"тема «{wanted[0]}»" if len(wanted) == 1 else "тема из списка")

    if judge and len(wanted) > 1:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=len(wanted)) as pool:
            results = list(pool.map(
                lambda t: _one_topic_matches(frame, t, expansions, judge), wanted))
    else:
        results = None

    for n, topic in enumerate(wanted):
        ok, related = (results[n] if results is not None
                       else _one_topic_matches(frame, topic, expansions, judge))
        if ok:
            note = f"тема «{topic}»"
            return True, (f"{note} ({related})" if related else note)

    if len(wanted) == 1:
        return False, f"тема не «{wanted[0]}»"
    return False, "тема не из списка: " + ", ".join(f"«{t}»" for t in wanted)


def _confirm_topic(frame, wanted, judge):
    """Подтвердить «по теме» судьёй по описанию. Тема, False или None (не смог)."""
    theme = str(frame.get("тема", "")).strip()
    if not theme:
        return None
    from concurrent.futures import ThreadPoolExecutor

    def one(topic):
        try:
            return bool(judge(theme, topic))
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=len(wanted)) as pool:
        answers = list(pool.map(one, wanted))
    for topic, answer in zip(wanted, answers):
        if answer:
            return topic
    return None if all(a is None for a in answers) else False


def banned_hit(frame, taste, judge=None):
    """Ролик из «не смотреть»: (да?, что именно). Модель ответила полем
    «запрет» — верим ему; нет поля (своя модель) — спрашиваем судью."""
    banned = topics({"тема": taste.get("не_смотреть", "")})
    if not banned:
        return False, ""
    said = frame.get("запрет")
    if isinstance(said, bool):
        return said, ", ".join(banned)
    theme = str(frame.get("тема", "")).strip()
    if judge is None or not theme:
        return False, ""
    for item in banned:
        try:
            if judge(theme, item):
                return True, item
        except Exception:
            pass
    return False, ""


def _is_ad(frame):
    """Реклама ли это. Модель отвечает и полем, и категорией — верим обоим."""
    if frame.get("реклама") in (True, 1, "true", "да"):
        return True
    return str(frame.get("категория", "")).strip().lower() == "реклама"


def matched_none(taste):
    """Пустая карта совпадений: заданные условия есть, но их не смотрели."""
    return {key: False for key, field in (("язык", "язык"), ("тема", "тема"))
            if taste.get(field, "")}


def topic_key(taste):
    """Тема в виде ключа для базы: «Коты,  кулинария» == «коты, кулинария»."""
    return ", ".join(topics(taste))


def tuning(app, taste=None, rows=None):
    """Насколько лента ЭТОЙ соцсети уже настроена: этап, доли, пояснение.

    Считается по последним `окно_настройки` разобранным роликам (без
    рекламы и сбоев кадра) этого телефона. Возвращает dict:
      этап        — «язык» (учим язык) или «тема» (язык настроен / не задан);
      язык        — доля роликов на нужном языке среди тех, где он понят;
      тема        — доля роликов по теме среди тех, где тема проверялась;
      усиление    — во сколько раз чаще лайк на попадании в тему (1.0-2.0);
      текст       — для журнала и окна.
    `rows` — для проверок без базы: список (язык, по_теме) от новых к старым.
    """
    taste = taste or load()
    behaviour = taste["поведение"]
    window = int(behaviour.get("окно_настройки", 40) or 40)
    if rows is None:
        try:
            import jobs
            rows = jobs.recent_feed(app, window, topic=topic_key(taste))
        except Exception:
            rows = []
    rows = list(rows)[:window]
    wanted = languages(taste)

    known_lang = [lang for lang, _ in rows if lang]
    lang_share = (sum(1 for lang in known_lang if lang in wanted) / len(known_lang)
                  if wanted and len(known_lang) >= 10 else None)
    known_topic = [t for _, t in rows if t in (0, 1, True, False)]
    topic_share = (sum(1 for t in known_topic if t) / len(known_topic)
                   if taste.get("тема") and len(known_topic) >= 10 else None)

    need = float(behaviour.get("язык_настроен_доля", 0.6) or 0.6)
    if not wanted or not taste.get("тема"):
        stage = "тема" if taste.get("тема") else "язык"
    else:
        stage = "тема" if (lang_share is not None and lang_share >= need) else "язык"

    boost = 1.0
    if stage == "тема" and topic_share is not None:
        boost = 2.0 if topic_share < 0.1 else (1.5 if topic_share < 0.25 else 1.0)

    parts = []
    if wanted:
        parts.append(f"язык {'/'.join(wanted)} " + (
            f"{lang_share:.0%}" if lang_share is not None else "пока мало данных"))
    if taste.get("тема"):
        parts.append("тема " + (f"{topic_share:.0%}" if topic_share is not None
                                else "пока мало данных"))
    if stage == "язык" and wanted and taste.get("тема"):
        verdict = "учу язык: свой язык без темы смотрю вполглаза"
    elif wanted and taste.get("тема"):
        verdict = "язык настроен — упор на тему"
    else:
        verdict = "упор на тему" if taste.get("тема") else "упор на язык"
    if boost > 1:
        verdict += f", тема редкая — лайк x{boost:g}"
    text = (", ".join(parts) + f" (по {len(rows)} роликам) — " + verdict) if parts else verdict
    return {"этап": stage, "язык": lang_share, "тема": topic_share,
            "усиление": boost, "роликов": len(rows), "текст": text,
            # Для окна: сколько роликов уже учтено (доля считается от 10),
            # какие языки и какой порог.
            "языков_учтено": len(known_lang), "тем_учтено": len(known_topic),
            # Каждый язык отдельно — когда их несколько, видно, какой лента
            # уже даёт, а какой нет.
            "по_языкам": {w: (sum(1 for lang in known_lang if lang == w) / len(known_lang)
                              if len(known_lang) >= 10 else None) for w in wanted},
            "языки": wanted, "порог": need}


def classify(frame, taste=None, expansions=None, judge=None, tuned=None):
    """Насколько ролик подходит: (вердикт, причина, что совпало).

    Вердикт — 'интересно' | 'нейтрально' | 'мимо'. Считаются только заданные
    условия: пустое поле ничему не мешает и в счёт не идёт. Совпало всё —
    интересно, часть — нейтрально, ничего — мимо. При одном заданном условии
    средней ступени не бывает.

    Третьим значением идут сами совпадения (`{"язык": True, "тема": False}`,
    незаданное условие отсутствует). Они нужны, чтобы решать время просмотра
    по данным, а не разбирать обратно человеческую строку причины. Туда же
    кладутся «этап» и «усиление» из `tuned` (см. `tuning`).
    """
    taste = taste or load()
    want_lang = taste.get("язык", "")
    want_topic = taste.get("тема", "")

    # Реклама листается сразу, независимо от темы и языка. Досматривать её
    # незачем: интереса она не отражает, а лента считает досмотр сигналом
    # «показывай ещё такое» — и начинает подсовывать больше рекламы.
    # Отдельный вердикт, а не «мимо»: в сводке видно, сколько её было.
    if _is_ad(frame):
        return "реклама", "рекламная вставка", matched_none(taste)
    # «Не смотреть» — сразу мимо, что бы ни совпало: человек сам сказал, что
    # этого в ленте не хочет, а любой досмотр учит её обратному.
    hit, what = banned_hit(frame, taste, judge)
    if hit:
        return "мимо", f"не смотреть: {what}", matched_none(taste)

    matched, hits, misses = {}, [], []
    stage = (tuned or {}).get("этап", "тема")
    boost = float((tuned or {}).get("усиление", 1.0) or 1.0)
    if want_lang:
        ok, note = _lang_matches(frame, want_lang)
        matched["язык"] = ok
        (hits if ok else misses).append(note)
    if want_topic:
        ok, note = _topic_matches(frame, want_topic, expansions, judge)
        matched["тема"] = ok
        (hits if ok else misses).append(note)

    # Причины уже написаны целиком («тема не «кулинария»», «язык ru, а нужен
    # en»), приставку «не» здесь не добавляем: раньше выходило «не язык «ru»»,
    # и это читалось как «язык не распознан».
    if want_topic:
        matched["этап"] = stage
        if matched.get("тема") and boost > 1:
            matched["усиление"] = boost
    if not hits and not misses:
        return "интересно", "ничего не задано", matched
    if not misses:
        return "интересно", " и ".join(hits), matched
    if not hits:
        return "мимо", " и ".join(misses), matched
    # Тема задана и не совпала, совпал только язык. Пока лента не настроена
    # на язык (этап «язык»), такой ролик смотрим вполглаза — так лента
    # быстрее понимает язык. Язык настроен (этап «тема») — листаем сразу:
    # досмотр «своего языка не про то» учил бы ленту не той теме (жалоба
    # 2026-10-11: «контент на испанском, но про каких-то мамочек»).
    if matched.get("тема") is False:
        key = "секунд_на_язык_пока_учим" if stage == "язык" else "секунд_на_язык_без_темы"
        low_high = taste["поведение"].get(key, [0.0, 0.0])
        if max(float(x) for x in low_high) <= 0:
            return "мимо", misses[0] + ("; язык уже настроен — нужна тема"
                                        if stage == "тема" and want_lang else ""), matched
    return "нейтрально", f"{hits[0]}, но {misses[0]}", matched


SKIP_VERDICTS = ("мимо", "реклама")


def decide(frame, taste=None, expansions=None, judge=None, tuned=None):
    """Что делать с роликом: (пролистать?, вердикт, причина, что совпало)."""
    verdict, why, matched = classify(frame, taste, expansions, judge, tuned)
    return verdict in SKIP_VERDICTS, verdict, why, matched


def peek_seconds(taste=None):
    """Сколько «смотреть» до решения. За это время модель успевает ответить."""
    taste = taste or load()
    low, high = taste["поведение"]["секунд_на_взгляд"]
    return random.uniform(float(low), float(high))


def patience_seconds(taste=None):
    """Сколько всего терпеть неподходящий ролик — от появления до свайпа."""
    taste = taste or load()
    low, high = taste["поведение"].get("секунд_на_неинтересное", [0.0, 0.0])
    return random.uniform(float(low), float(high))


# Секунды считаются ОТ ПОЯВЛЕНИЯ ролика на экране, то есть включают время
# решения: цикл досыпает остаток. Ставим около восьми — так и просили.
NEUTRAL_SECONDS = {
    # Совпала тема на чужом языке — это по делу, просто озвучено не так.
    "тема": ("секунд_на_тему_без_языка", [15.0, 25.0]),
    # Совпал только язык — смотреть особо нечего (по умолчанию и не смотрим).
    "язык": ("секунд_на_язык_без_темы", [0.0, 0.0]),
}

INTERESTING_SECONDS = ("секунд_на_интересное", [20.0, 30.0])


def watch_seconds(verdict, matched, full, taste=None):
    """Сколько держать ролик на экране: секунды.

    `full` — сколько смотрели бы по обычному распределению (`human.dwell()`).

    Совпало всё — смотрим до тридцати секунд: это то, ради чего лента и
    настраивалась. Столько можно держать только потому, что просмотр обрывается
    на повторе (`session._watch_video`): без этого короткий ролик прокрутился
    бы три раза, а со стороны это выглядит зависшим ботом.

    Нижняя граница не украшательство: `dwell()` начинается с 1.5 с, и
    подходящий ролик регулярно получал МЕНЬШЕ неподходящего (в живом прогоне
    «смотрю 1.8с» рядом с «вполглаза 5.9с») — ступени переворачивались.
    """
    # Всё, что решено листать, держим ноль секунд. Проверяем по списку, а не
    # по одному «мимо»: с появлением «рекламы» вердиктов стало больше, и
    # «не нейтрально — значит интересно» тихо давало рекламе полный просмотр.
    if verdict in SKIP_VERDICTS:
        return 0.0

    taste = taste or load()
    behaviour = taste["поведение"]
    if verdict != "нейтрально":
        key, default = INTERESTING_SECONDS
        low, high = behaviour.get(key, default)
        floor = max(float(behaviour.get(k, d)[1])
                    for k, d in NEUTRAL_SECONDS.values())
        return max(random.uniform(float(low), float(high)), full, floor)

    key, default = NEUTRAL_SECONDS["тема" if matched.get("тема") else "язык"]
    if not matched.get("тема") and matched.get("этап") == "язык":
        key, default = "секунд_на_язык_пока_учим", [3.0, 7.0]
    low, high = behaviour.get(key, default)
    return random.uniform(float(low), float(high))


def like_probability(verdict, taste=None, matched=None):
    """Лайк — тому, что совпало полностью, и реже — теме на чужом языке.

    Ролику, где совпал только язык, — никогда: лайк тянет рекомендации в
    свою сторону, и лайкать «не про то» значит размывать тему. А тема на
    чужом языке — как раз то, что ленте надо показать: лайк с долей
    `лайк_теме_без_языка` от обычного (по умолчанию вдвое реже).
    """
    taste = taste or load()
    matched = matched or {}
    # Редкая тема на этапе «тема»: каждое попадание — сильнее сигнал (см.
    # `tuning`). Потолок — чтобы агент не лайкал всё подряд.
    full = min(0.6, float(taste["поведение"].get("лайк", 0.25))
               * float(matched.get("усиление", 1.0) or 1.0))
    if verdict == "интересно":
        return full
    if verdict == "нейтрально" and matched.get("тема"):
        return full * float(taste["поведение"].get("лайк_теме_без_языка", 0.5))
    return 0.0


# ------------------------------------------------------------- диалог

def pick(choices=None):
    """Два вопроса: тема и языки. True, если что-то поменяли."""
    import vision
    choices = choices or vision.LANGUAGES
    taste = load()
    print("\nТема и до двух языков — всё остальное агент листает.\n")

    print(f"Сейчас тема: {taste['тема'] or 'не задана'}")
    print("Пиши своими словами: «новости», «коты», «мемы про политику».")
    print("Enter — оставить, прочерк «-» — убрать тему совсем.")
    topic = input("Тема: ").strip()

    print(f"\nСейчас язык: {taste['язык'] or 'не проверяется'}")
    print(f"Варианты: {', '.join(choices)}. Два — через запятую («ru, en»). "
          "Прочерк «-» — не проверять.")
    lang = input("Язык: ").strip().lower()

    if not topic and not lang:
        print("\nНичего не поменял.")
        return False

    if lang and lang != "-":
        bad = [x for x in languages({"язык": lang}) if x not in choices]
        if bad:
            print(f"  «{', '.join(bad)}» — не язык из списка, оставляю как было")
            lang = ""

    set_choice(topic="" if topic == "-" else (topic or None),
               lang="" if lang == "-" else (lang or None),
               taste=taste)
    return True


def describe(taste=None):
    """Человекочитаемая сводка — для команды interests и doctor."""
    taste = taste or load()
    lines = []
    if "_ошибка" in taste:
        lines.append(f"[!] interests.json не прочитан ({taste['_ошибка']}), "
                     "работаю на умолчаниях")
    lines.append(f"файл: {config.INTERESTS}")
    lines.append(f"  тема:  {taste['тема'] or 'не задана — смотрю всё'}")
    lines.append(f"  язык:  {' или '.join(languages(taste)) or 'не проверяется'}")
    lines.append("поведение:")
    for key, value in taste["поведение"].items():
        lines.append(f"   {key}: {value}")
    return "\n".join(lines)
