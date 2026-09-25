"""Что смотреть: одна тема и один язык. Правится в interests.json.

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
"""
import json
import os
import random
import re

import config

DEFAULTS = {
    "тема": "",          # пусто = тема не важна, смотрим всё на нужном языке
    "язык": "",          # пусто = язык не важен
    "поведение": {
        "секунд_на_взгляд": [1.5, 3.0],
        # Нейтральное бывает двух сортов, и они не равны: совпавшая ТЕМА на
        # чужом языке ценнее, чем свой язык на посторонней теме. Оба заданы
        # прямыми секундами, а не долей: доля считалась от обычного просмотра,
        # а тот сам случайный — выходило то 1.5, то 4 секунды, предсказать
        # нельзя. Где нужна понятная длительность, там и пишем секунды.
        "секунд_на_интересное": [20.0, 30.0],
        "секунд_на_тему_без_языка": [10.0, 15.0],
        "секунд_на_язык_без_темы": [1.5, 10.0],
        "секунд_на_неинтересное": [0.0, 0.0],
        "лайк": 0.25,
    },
}


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
    data["язык"] = str(user.get("язык", "")).strip().lower()

    # Перенос со старого формата со списками: берём первое, что там было.
    if not data["тема"]:
        old = (user.get("интересно") or {}).get("слова") or []
        data["тема"] = str(old[0]).strip().lower() if old else ""
    if not data["язык"]:
        old = (user.get("интересно") or {}).get("языки") or []
        data["язык"] = str(old[0]).strip().lower() if old else ""

    if isinstance(user.get("поведение"), dict):
        for key, value in user["поведение"].items():
            if key in data["поведение"]:
                data["поведение"][key] = value
    return data


NOTE = [
    "Всего две настройки: тема и язык.",
    "Тема — своими словами: «новости», «коты», «мемы про политику».",
    "Тем можно несколько через запятую: «коты, кулинария, мемы».",
    "Достаточно совпадения по ЛЮБОЙ из них.",
    "Модель сверяет описание ролика с темой по смыслу, а не по буквам.",
    "Язык — ru, en или другой. Пусто = не проверять.",
    "Совпало и то и другое — смотрю полностью и могу лайкнуть.",
    "Совпала тема, но язык чужой — смотрю секунд_на_тему_без_языка, без лайка.",
    "Совпал только язык — смотрю секунд_на_язык_без_темы, без лайка.",
    "Не совпало ничего — листаю сразу.",
    "Рекламу листаю сразу в любом случае: досмотр она читает как «хочу ещё».",
    "После правки ничего перезапускать не надо — файл читается каждую сессию.",
]


def save(taste):
    """Записать настройки обратно в файл."""
    data = {"_как_пользоваться": NOTE,
            "тема": taste.get("тема", ""),
            "язык": taste.get("язык", ""),
            "поведение": taste["поведение"]}
    with open(config.INTERESTS, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return config.INTERESTS


def set_choice(topic=None, lang=None, taste=None):
    """Задать тему и/или язык. None — не трогать."""
    taste = taste or load()
    taste.pop("_ошибка", None)
    if topic is not None:
        taste["тема"] = topic.strip().lower()
    if lang is not None:
        taste["язык"] = lang.strip().lower()
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
    """Совпал ли язык. Неизвестный язык не считается чужим.

    Модель называет язык не всегда, и наказывать ролик за её молчание
    нельзя: так под нож уходили бы кадры без речи.

    В причине пишем ОБА языка. Раньше стоял только распознанный, и строка
    «не язык «ru»» читалась как «модель не смогла определить русский», хотя
    означала «распознан русский, а в настройках стоит английский».
    """
    lang = str(frame.get("язык", "")).strip().lower()
    if not lang:
        return True, "язык не разобран"
    if lang == want_lang:
        return True, f"язык {lang}"
    return False, f"язык {lang}, а нужен {want_lang}"


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

    Тем может быть несколько через запятую, и достаточно любой. Проверяем по
    очереди и останавливаемся на первой подошедшей: каждая проверка — это
    отдельный вопрос к модели, лишние ни к чему.
    """
    wanted = topics({"тема": want})
    if not wanted:
        return True, ""

    for topic in wanted:
        ok, related = _one_topic_matches(frame, topic, expansions, judge)
        if ok:
            note = f"тема «{topic}»"
            return True, (f"{note} ({related})" if related else note)

    if len(wanted) == 1:
        return False, f"тема не «{wanted[0]}»"
    return False, "тема не из списка: " + ", ".join(f"«{t}»" for t in wanted)


def _is_ad(frame):
    """Реклама ли это. Модель отвечает и полем, и категорией — верим обоим."""
    if frame.get("реклама") in (True, 1, "true", "да"):
        return True
    return str(frame.get("категория", "")).strip().lower() == "реклама"


def matched_none(taste):
    """Пустая карта совпадений: заданные условия есть, но их не смотрели."""
    return {key: False for key, field in (("язык", "язык"), ("тема", "тема"))
            if taste.get(field, "")}


def classify(frame, taste=None, expansions=None, judge=None):
    """Насколько ролик подходит: (вердикт, причина, что совпало).

    Вердикт — 'интересно' | 'нейтрально' | 'мимо'. Считаются только заданные
    условия: пустое поле ничему не мешает и в счёт не идёт. Совпало всё —
    интересно, часть — нейтрально, ничего — мимо. При одном заданном условии
    средней ступени не бывает.

    Третьим значением идут сами совпадения (`{"язык": True, "тема": False}`,
    незаданное условие отсутствует). Они нужны, чтобы решать время просмотра
    по данным, а не разбирать обратно человеческую строку причины.
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

    matched, hits, misses = {}, [], []
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
    if not hits and not misses:
        return "интересно", "ничего не задано", matched
    if not misses:
        return "интересно", " и ".join(hits), matched
    if not hits:
        return "мимо", " и ".join(misses), matched
    return "нейтрально", f"{hits[0]}, но {misses[0]}", matched


SKIP_VERDICTS = ("мимо", "реклама")


def decide(frame, taste=None, expansions=None, judge=None):
    """Что делать с роликом: (пролистать?, вердикт, причина, что совпало)."""
    verdict, why, matched = classify(frame, taste, expansions, judge)
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
    "тема": ("секунд_на_тему_без_языка", [10.0, 15.0]),
    # Совпал только язык — смотреть особо нечего.
    "язык": ("секунд_на_язык_без_темы", [1.5, 10.0]),
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
    low, high = behaviour.get(key, default)
    return random.uniform(float(low), float(high))


def like_probability(verdict, taste=None):
    """Лайк ставим только тому, что совпало полностью.

    Нейтральному — никогда: лайк тянет рекомендации в свою сторону, и
    лайкать «наполовину подходящее» значит постепенно размывать тему.
    """
    if verdict != "интересно":
        return 0.0
    taste = taste or load()
    return float(taste["поведение"].get("лайк", 0.25))


# ------------------------------------------------------------- диалог

def pick(languages=("ru", "en", "es", "другой")):
    """Два вопроса: тема и язык. True, если что-то поменяли."""
    taste = load()
    print("\nОдна тема и один язык — всё остальное агент листает.\n")

    print(f"Сейчас тема: {taste['тема'] or 'не задана'}")
    print("Пиши своими словами: «новости», «коты», «мемы про политику».")
    print("Enter — оставить, прочерк «-» — убрать тему совсем.")
    topic = input("Тема: ").strip()

    print(f"\nСейчас язык: {taste['язык'] or 'не проверяется'}")
    print(f"Варианты: {', '.join(languages)}. Прочерк «-» — не проверять.")
    lang = input("Язык: ").strip().lower()

    if not topic and not lang:
        print("\nНичего не поменял.")
        return False

    if lang and lang not in languages and lang != "-":
        print(f"  «{lang}» — не язык из списка, оставляю как было")
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
    lines.append(f"  язык:  {taste['язык'] or 'не проверяется'}")
    lines.append("поведение:")
    for key, value in taste["поведение"].items():
        lines.append(f"   {key}: {value}")
    return "\n".join(lines)
