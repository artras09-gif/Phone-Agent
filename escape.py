# -*- coding: utf-8 -*-
"""Выход из незнакомого экрана: у модели есть инструменты, а не одна догадка.

`vision.rescue` спрашивал ОДИН раз: «вот кадр, что нажать?» — и на этом всё.
Совет не сработал — сессия откатывалась к лесенке `_unstick`: назад, назад,
перезапуск приложения. Для окна «Подпишитесь на друзей» это плохая сделка:
перезапуск стоит полминуты и теряет место в ленте, а окно закрывается одним
крестиком, который просто надо найти.

Здесь модель ходит кругами: посмотрела -> выбрала действие -> увидела, что
получилось -> выбрала следующее. Три вещи, из-за которых это вообще работает:

1. **Жмём НЕ координаты, а элемент из дерева.** У 3B-модели попадание по
   пикселям никакое (проверено ещё в `rescue`: половина ответов — координаты
   вне экрана, и их приходилось отбрасывать). Зато выбрать «кнопку номер 3»
   из готового списка она может. Дерево на таких экранах СНИМАЕТСЯ: помеха
   статична, это не лента с играющим видео.

2. **Модель видит, что уже пробовала и чем кончилось.** Без этого она жмёт
   одну и ту же кнопку до конца лимита.

3. **Живая лента сюда не попадает.** Если дерево не снялось — значит перед
   нами лента, тревога ложная, и мы молча уходим. Это тот же признак, на
   котором стоит `_ensure_feed`, и он же защищает от тапов по видео.

Запреты (`NEVER`) не обсуждаются с моделью: элемент с такой подписью просто
не попадает в список, который ей показывают. Права, деньги, вход в аккаунт,
подписки на людей и публикация — мимо, чем бы модель это ни обосновала.

**Руки (2026-09-30).** Список бессилен в двух случаях: нужный крестик без
подписи и неотличим от соседних значков, и окно поверх ИГРАЮЩЕГО видео —
дерево тогда не снимается, и раньше агент решал «это лента» и уходил ни с
чем. Для них модель получает полноразмерный снимок и называет ТОЧКУ. Облачные
модели попадают (замер: 14 из 18, опасных 0), 3B — нет, поэтому сначала
проверка на картинке с красной точкой (`hands_ready`): промахнулась больше
допуска — рук нет, остаётся список. Предохранители — в `_hand_tap`.
"""
import json
import random
import re
import time

import adb
import config
import device
import human
import picture
import ui
import vision


# Подписи, по которым не жмут никогда. Сравнение — вхождением в нижнем
# регистре, поэтому куски слов взяты короткие, но не настолько, чтобы
# ловить лишнее («разрешить» не должно ловить «Не разрешать»).
NEVER = [
    # права
    "разрешить", "allow", "предостав", "включить уведомл", "turn on",
    # деньги
    "купить", "оплат", "premium", "buy", "pay", "subscribe",
    # аккаунт
    "войти", "log in", "sign in", "sign up", "зарегистр", "продолжить с",
    "continue with", "использовать номер", "по номеру телефона",
    # необратимое
    "удалить", "delete", "заблокир", "block", "пожалов", "report",
    # чужие соцсвязи
    "подписаться", "подписки", "follow", "добавить друз", "пригласить", "invite",
    # публикация — и создание: новый ролик никогда не выход из тупика, а
    # «+» в углу Reels ведёт прямо в камеру и редактор (замер рук, 2026-09-30)
    "опубликовать", "отправить", "поделиться", "share", "репост", "repost",
    "создать", "create",
    # порча рекомендаций и жалобы
    "жалоб", "неинтересно", "not interested",
    # трансляции и истории
    "транслировать", "добавить в истори", "add to story",
    # магазин
    "обновить", "update", "установить", "install",
]

# Признаки экрана, С КОТОРОГО МОЖНО ОТПРАВИТЬ ВИДЕО. Найдено живьём: лист
# «поделиться» в TikTok деревом снимается, и в список кнопок попадали имена
# аккаунтов из списка контактов, «SMS», «Telegram», «Жалоба», «Неинтересно» — по
# подписи ни одну из них не отличить от безобидной. Тап по такой отправляет
# ролик человеку или портит рекомендации, а помочь выбраться не может ни одна:
# на подобных листах выход всегда один — крестик, «назад» или смахнуть вниз.
#
# Поэтому на таком экране модели показывают ТОЛЬКО закрывающие кнопки и
# значки без подписи. Свобода выбора остаётся, цена ошибки исчезает.
SENDING_MARKS = ("отправить", "поделиться", "репост", "жалоб", "неинтересно",
                 "добавить в истори", "транслировать", "send to", "share to",
                 # Экран составления поста — самый дорогой из возможных
                 # промахов: там КАЖДАЯ кнопка что-то публикует.
                 # 2026-08-30 в личном аккаунте так появилась история.
                 "опубликовать", "ваша истори", "your story", "publish",
                 "сохранить черновик", "save draft", "загрузить")

# Действия, которые модель имеет право назвать.
ACTIONS = ("tap", "back", "swipe", "wait", "done", "none")


def _forbidden(label):
    """Нельзя ли жать элемент с такой подписью."""
    low = label.lower().strip()
    # Явные отказы разрешены всегда: «Не разрешать» — это отказ от прав,
    # а не выдача, и по вхождению его легко спутать с запретом.
    if any(low == d.lower() for d in ui.DISMISS_LABELS):
        return False
    return any(bad in low for bad in NEVER)


# Слова в resource-id, по которым видно назначение кнопки. У TikTok id
# обфусцированы («pge», «gl4»), и подставлять их вместо подписи оказалось
# вредно: модель послушно выбирала «кнопку gl4», хотя ни она, ни мы не
# знаем, что это. У системных диалогов и части приложений id осмысленные —
# ради них список и оставлен.
ID_WORDS = ("close", "back", "cancel", "skip", "exit", "dismiss", "home",
            "later", "deny", "negative", "quit", "not_now")


def _label_of(node):
    """Как назвать элемент в списке для модели. Пусто = подписи нет."""
    label = (node.text or node.desc or "").strip()
    if label:
        return label
    rid = node.rid.split("/")[-1].lower()
    if any(word in rid for word in ID_WORDS):
        return rid.replace("_", " ")
    return ""


def _where(node, w, h):
    """Человеческое «внизу справа» — модели так проще сверить со скриншотом."""
    x, y = node.center
    col = "слева" if x < w / 3 else ("справа" if x > 2 * w / 3 else "по центру")
    row = "вверху" if y < h / 3 else ("внизу" if y > 2 * h / 3 else "в середине")
    return row + " " + col


def _rank(node):
    """Чем меньше число, тем выше в списке.

    Порядок не косметика: список обрезается, а важное — крестик и «Не
    сейчас» — обязано в него попасть. Крупные панели уходят вниз: тап по
    контейнеру во весь экран не закрывает ничего (на этом уже обжигались
    в `close_button`).
    """
    label = _label_of(node).lower()
    if any(label == d.lower() for d in ui.DISMISS_LABELS):
        return 0
    if any(d.lower() in (node.desc or "").lower() for d in ui.CLOSE_DESCS):
        return 1
    if label in NAV_LABELS:
        return 2
    return 3


# Нижние вкладки, которыми возвращаются в ленту. Держатся отдельно, потому
# что переживают запрет «опасного экрана» (см. `_menu`): вкладка навигации
# ничего не отправляет и никого не оповещает, она просто меняет экран.
NAV_LABELS = frozenset(x.lower() for x in (
    "Главная", "Home", "Для вас", "For You", "Рекомендации",
    "Shorts", "Reels", "Видео Reels", "Лента", "Feed",
))


# Значков без подписи на экране бывает десяток, и все они для модели на
# одно лицо — различить их можно только по картинке. Поэтому берём немного
# и самые мелкие: крестик закрытия мельче любого контейнера.
UNNAMED_LIMIT = 3
UNNAMED_MAX_SHARE = 1 / 12.0     # больше этой доли экрана — уже не значок


def risky_screen(nodes):
    """Можно ли с этого экрана что-то отправить или испортить ленту."""
    for node in nodes:
        low = ((node.text or "") + " " + (node.desc or "")).lower()
        if any(mark in low for mark in SENDING_MARKS):
            return True
    return False


def _menu(nodes, w, h, limit=12, risky=None):
    """Кликабельные элементы, которые модели разрешено выбирать."""
    if risky is None:
        risky = risky_screen(nodes)
    named, icons, seen = [], [], set()
    for node in nodes:
        if not (node.clickable and node.enabled) or node.area <= 0:
            continue
        label = _label_of(node)
        if label and _forbidden(label):
            continue
        key = (label.lower(), node.bounds)
        if key in seen:
            continue
        seen.add(key)
        if label:
            named.append(node)
        elif node.area <= w * h * UNNAMED_MAX_SHARE:
            icons.append(node)

    named.sort(key=lambda n: (_rank(n), n.area))
    if risky:
        # Ранг 3 — «просто кнопка с подписью». На листе отправки такая может
        # оказаться контактом или жалобой, а закрыть экран ею всё равно нельзя.
        #
        # А вот вкладки навигации (ранг 2) остаются, и это важно: «опасным»
        # признаётся весь экран, на котором ХОТЬ ЧТО-ТО отправляет, — а у
        # обычного профиля это всего лишь кнопка «Поделиться профилем».
        # Пока вкладки резались заодно со всем, модель на профиле получала
        # три безымянных значка вместо «Главной», то есть теряла
        # единственный верный выход. Поймано охотой на баги, после того как
        # список `SENDING_MARKS` разросся.
        named = [n for n in named if _rank(n) < 3]
    icons.sort(key=lambda n: n.area)
    return (named + icons[:UNNAMED_LIMIT])[:limit]


def feed_tab(menu, feed_labels=None):
    """Вкладка ленты среди предложенных кнопок — или None, если её нет.

    Нужна, чтобы НЕ спрашивать модель там, где ответ известен точно. Замер на
    сохранённом экране «Входящие» (12 кнопок, 5 попыток): модель пять раз из
    пяти выбрала «Интересное» вместо «Главной» — то есть ушла бы в поиск
    вместо ленты. Кнопка с подписью «Главная» при этом стояла первой в списке.
    Причина понятна: среди имён чатов и «Создать новый чат» слово «интересное»
    ближе к вопросу «где тут ролики», чем «главная».

    Найденных вкладок должно быть ровно одна: у YouTube на одном экране есть
    и «Главная», и «Shorts», и какая из них лента — знает рецепт, а не мы.
    Неоднозначность отдаём модели.
    """
    wanted = frozenset(x.lower() for x in (feed_labels or NAV_LABELS))
    hits = [n for n in menu if _label_of(n).lower() in wanted]
    return hits[0] if len(hits) == 1 else None


def _texts(nodes, limit=6):
    """Надписи на экране — чтобы модель поняла, что это вообще за окно."""
    out, seen = [], set()
    for node in nodes:
        text = (node.text or "").strip()
        if len(text) < 3 or text.lower() in seen:
            continue
        seen.add(text.lower())
        out.append(text)
        if len(out) >= limit:
            break
    return out


SYSTEM = "Отвечай одним JSON без пояснений."

# ДВА КОРОТКИХ ВОПРОСА ВМЕСТО ОДНОГО ДЛИННОГО — это замер, а не вкусовщина.
#
# Первый живой прогон и гонка формулировок на сохранённом экране профиля
# (5 попыток на вариант, верный ответ — кнопка «Главная»):
#
#   длинный промпт: правила, надписи, поле «почему»   0/5, 1.1 с
#   + надписи экрана, без правил                      2/5, 0.4 с
#   «ты нажимаешь кнопки вместо человека»             0/5, 0.4 с
#   ТОЛЬКО список кнопок и один вопрос                5/5, 0.3 с
#
# 3B-модель на длинном промпте не рассуждает, а переписывает его куски:
# сначала дословно выдавала «Смахнуть подсказку-туториал» из примера правил,
# потом — «Надписи на экране: ...» из условия. Чем меньше текста рядом с
# вопросом, тем меньше ей есть что копировать.
#
# Поэтому спрашиваем по одному. Второй вопрос задаётся, только если кнопки
# не подошли, — на этот путь уходит меньшинство экранов.
ASK_BUTTON = """На экране приложения кнопки:
{menu}
{tried}
Какую нажать, чтобы вернуться к ленте с видео?
Ответь: {{"кнопка": номер}}. Ни одна не подходит — {{"кнопка": 0}}."""

ASK_ELSE = """Экран приложения, подходящих кнопок на нём нет.
{tried}
Что сделать, чтобы вернуться к ленте с видео?
Ответь одним из: {{"иначе": "back"}} — уйти назад, {{"иначе": "swipe"}} —
смахнуть экран, {{"иначе": "wait"}} — подождать загрузку{done}."""

DONE_OPTION = ', {"иначе": "done"} — лента с видео уже на экране'


def _name_of(node):
    """Как назвать нажатое в журнале."""
    label = _label_of(node)
    return "«{}»".format(label) if label else "значок {}".format(node.bounds[:2])


def _describe_menu(menu, w, h):
    """Список кнопок для модели. Без подписи — значит смотри на картинку."""
    lines = []
    for i, node in enumerate(menu, 1):
        label = _label_of(node)
        name = "«{}»".format(label) if label else "значок без подписи"
        lines.append("[{}] {} — {}".format(i, name, _where(node, w, h)))
    return "\n".join(lines) if lines else "(кликабельных кнопок не нашлось)"


def _describe_tried(history):
    """Что уже пробовали — одной строкой.

    Нарочно коротко и без нумерованного списка: длинный «протокол» модель
    начинает переписывать в ответ вместо того, чтобы делать вывод.
    """
    return "\nНе помогло: " + "; ".join(history[-3:]) + "." if history else ""


def build_prompt(menu, history, w, h, allow_done=False):
    """Первый вопрос — какую кнопку нажать. Им же пользуется escape_probe."""
    return ASK_BUTTON.format(menu=_describe_menu(menu, w, h),
                             tried=_describe_tried(history))


def parse(text, menu):
    """Разобрать ответ на первый вопрос: номер кнопки или 0."""
    data = vision._json_from(text)
    if not isinstance(data, dict):
        data = {}
    raw_button = data.get("кнопка", 0)
    try:
        button = int(raw_button or 0)
    except (TypeError, ValueError):
        button = 0

    if button and not (1 <= button <= len(menu)):
        # Номер вне списка — тот же симптом, что и координаты вне экрана у
        # старого `rescue`: модель придумала кнопку. Жать нечего.
        return {"действие": "none", "кнопка": 0,
                "почему": "кнопки №{} в списке нет".format(raw_button)}
    if button:
        return {"действие": "tap", "кнопка": button,
                "почему": "кнопка №{}".format(button)}
    return {"действие": "", "кнопка": 0, "почему": ""}


def parse_else(text, allow_done=False):
    """Разобрать ответ на второй вопрос: что делать, раз кнопки не подошли."""
    data = vision._json_from(text)
    if not isinstance(data, dict):
        data = {}
    action = str(data.get("иначе", "")).lower().strip()
    # Модели любят отвечать по-русски, хотя просили латиницей.
    action = {"назад": "back", "свайп": "swipe", "смахнуть": "swipe",
              "ждать": "wait", "подождать": "wait", "готово": "done",
              "ничего": "none", "": "none"}.get(action, action)
    if action not in ACTIONS or action == "tap":
        return {"действие": "none", "кнопка": 0,
                "почему": "непонятный ответ: " + text.strip()[:60]}
    if action == "done" and not allow_done:
        # Список кнопок сам по себе доказывает, что это НЕ лента: в ленте
        # дерево не снимается, и списка бы не было.
        return {"действие": "none", "кнопка": 0,
                "почему": "сказала «готово» там, где ленты быть не может"}
    return {"действие": action, "кнопка": 0, "почему": action}


def _ask_menu(png, menu, history, w, h, shrink=None):
    """Первый вопрос — какую кнопку из списка нажать. Пустое действие —
    ни одна не подошла (дальше — руки или второй вопрос)."""
    if not menu:
        return {"действие": "", "кнопка": 0, "почему": ""}
    raw = vision.ask(png, build_prompt(menu, history, w, h),
                     system=SYSTEM, max_tokens=80, temperature=0.1,
                     shrink=shrink)
    return parse(raw, menu)


def _ask_else(png, history, shrink=None, allow_done=False):
    """Второй вопрос — что делать, раз кнопки не подошли."""
    prompt = ASK_ELSE.format(tried=_describe_tried(history),
                             done=DONE_OPTION if allow_done else "")
    raw = vision.ask(png, prompt, system=SYSTEM, max_tokens=80,
                     temperature=0.1, shrink=shrink)
    return parse_else(raw, allow_done)


def _run(action, button, direction, menu, w, h):
    """Выполнить действие. Возвращает, что именно сделали, строкой."""
    if action == "tap":
        node = menu[button - 1]
        ui.tap_node(node)
        human.pause(0.8, 1.6)
        return "tap " + _name_of(node)
    if action == "back":
        device.back()
        human.pause(0.8, 1.6)
        return "back"
    if action == "swipe":
        way = {"вверх": "up", "вниз": "down",
               "влево": "left", "вправо": "right"}.get(direction, "up")
        human.scroll_feed(w, h, way, quick=True)
        human.pause(0.6, 1.2)
        return "swipe " + way
    if action == "wait":
        human.pause(1.5, 3.0)
        return "wait"
    return ""


# ------------------------------------------------------------------ руки

# Запас на ответ. Рассуждающие модели (deepseek-flash) сначала думают, и на
# 120 токенах ответ обрывался посреди JSON: `{"x": 198, "` — без «y».
ANSWER_TOKENS = 400

ASK_WHERE_DOT = ('Снимок {w}x{h} пикселей. Где красный круг? '
                 'Ответь: {{"x": число, "y": число}} в пикселях снимка.')

# Формулировка — та, что мерилась (`scratchpad/hands_bench.py`): 14 из 18.
ASK_POINT = """Снимок экрана телефона, {w}x{h} пикселей. Нужно вернуться к ленте видео{feed}.
Если на экране мешает окно, панель или меню — закрой его. Если это другой
раздел приложения — перейди на вкладку ленты.{tried}
Куда нажать? Ответь: {{"x": число, "y": число, "что": "что это за элемент"}}
— координаты в пикселях этого снимка. Нажать нечего — {{"иначе": "back"}}."""

# Второй взгляд — вблизи и с вопросом об опасности. Нужен там, где под
# пальцем нет кнопки из дерева: подписи, по которой проверить, нет, и
# остаётся спросить модель ещё раз, показав ей ровно то место.
#
# ТОЛЬКО вето, без права сдвинуть точку. Замер 2026-09-30: с вопросом «если
# кольцо мимо — укажи нужное» модель вблизи охотилась за кнопкой и на листе
# «Поделиться» перенесла верную точку (видео над листом) на «+» — создание
# нового ролика, — назвав это безопасным. Один вопрос давал 14/18, с
# «поправкой вблизи» — 8/18.
ASK_CLOSER = """Фрагмент снимка экрана телефона. Красное кольцо нарисовано мной поверх
снимка, это не часть экрана: внутри него то место, куда я собираюсь
нажать, чтобы вернуться к ленте видео{feed}.
Что внутри кольца? Ответь: {{"что": "что это", "кнопка": true или false, "опасно": true или false}}
«кнопка» — внутри кольца кнопка, значок или вкладка приложения, а не
картинка самого видео и не пустое место.
«опасно» — если нажатие может что-то отправить, опубликовать, создать,
подписаться на кого-то, купить или удалить."""

# Слова, по которым видно, что на полном снимке модель целилась в КНОПКУ.
# Если вблизи кнопки там нет — она промахнулась, и жать нельзя: замер
# 2026-09-30 — «крестик мини-плеера» вблизи оказывался «творогом в миске»,
# «вкладка Reels» — «превью ролика в сетке профиля».
CONTROL_WORDS = ("кнопк", "значок", "иконк", "крестик", "вкладк", "стрелк",
                 "button", "icon", "close", "tab")

# Как модель называет точки: в пикселях снимка, в тысячных долях (так
# отвечают Qwen3-VL и Gemini) или в долях единицы. Узнаётся проверкой, а не
# по имени модели. Ключ — (модель, ширина, высота); None — рук ей не дают.
_HANDS = {}

FEED_TITLES = {"com.zhiliaoapp.musically": "TikTok", "com.ss.android.ugc.trill": "TikTok",
               "com.google.android.youtube": "YouTube Shorts",
               "com.instagram.android": "Instagram Reels"}


def _point_of(text):
    """(x, y, что) из ответа модели или None."""
    data = vision._json_from(text or "")
    if isinstance(data, dict):
        try:
            return (float(data.get("x", data.get("х"))), float(data.get("y", data.get("у"))),
                    str(data.get("что", "")).strip()[:60])
        except (TypeError, ValueError):
            pass
    # Рассуждающая модель могла оборвать JSON на лимите — числа в нём уже есть.
    m = re.search(r'"[xх]"\s*:\s*(-?[\d.]+)\D+?"[yу]"\s*:\s*(-?[\d.]+)', text or "")
    return (float(m.group(1)), float(m.group(2)), "") if m else None


def _to_image(x, y, way, iw, ih):
    """Точка модели -> пиксели картинки размером iw x ih."""
    if way == "pixels":
        return x, y
    if way == "permille":
        return x * iw / 1000.0, y * ih / 1000.0
    if way == "fraction":
        return x * iw, y * ih
    _, sx, sy = way                  # ("fit", sx, sy) — подобрано по проверке
    return x * sx, y * sy


def _fit(answers, iw, ih):
    """Какая система координат лучше всего объясняет ответы: (способ, промах).

    Промах — средний по проверочным точкам, в пикселях картинки.

    Обычные шкалы (пиксели, тысячные, доли) — первыми и с большим запасом.
    Своя шкала — только для модели, которая ужимает снимок у себя и называет
    точки на ужатом, и только если ВСЕ точки дают один и тот же множитель.
    Урок 2026-09-30: оборванный ответ на одну из двух точек дал «свою шкалу»
    ×1.2, и половина нажатий ушла за нижний край экрана.
    """
    def miss(way):
        total = 0.0
        for (tx, ty), (ax, ay) in answers:
            x, y = _to_image(ax, ay, way, iw, ih)
            total += ((x - tx) ** 2 + (y - ty) ** 2) ** 0.5
        return total / len(answers)

    ways = ["pixels", "permille"]
    if all(ax <= 1.5 and ay <= 1.5 for _, (ax, ay) in answers):
        ways.append("fraction")
    best = min(ways, key=miss)
    if miss(best) <= config.HANDS_MAX_MISS:
        return best, miss(best)

    kx = [tx / ax for (tx, _), (ax, _) in answers if ax > 0]
    ky = [ty / ay for (_, ty), (_, ay) in answers if ay > 0]
    if len(kx) == len(ky) == len(answers):
        sx, sy = sum(kx) / len(kx), sum(ky) / len(ky)
        steady = all(abs(k / sx - 1) <= 0.12 for k in kx) and \
            all(abs(k / sy - 1) <= 0.12 for k in ky)
        if steady:
            fit = ("fit", sx, sy)
            return fit, miss(fit)
    return best, miss(best)


def hands_ready(iw, ih, say=None):
    """Можно ли модели нажимать точки — и как их читать. None — нельзя.

    Проверка один раз на модель: две красные точки на белом снимке того же
    размера, что и экран. deepseek-flash отвечает в пикселях с промахом
    35-60, у 3B-модели половина координат была вне экрана вовсе.
    """
    if not config.ESCAPE_HANDS or not iw or not ih:
        return None
    ok, model = vision.available()
    if not ok:
        return None
    key = (model, iw, ih)
    if key in _HANDS:
        return _HANDS[key]

    spots = [(int(iw * 0.75), int(ih * 0.79)), (int(iw * 0.185), int(ih * 0.21)),
             (int(iw * 0.86), int(ih * 0.33))]
    answers = []
    for spot in spots:
        png = picture.dots_png(iw, ih, [spot])
        if not png:
            return None                  # рисовать нечем — это не приговор модели
        try:
            raw = vision.ask(png, ASK_WHERE_DOT.format(w=iw, h=ih), system=SYSTEM,
                             max_tokens=ANSWER_TOKENS, temperature=0.1, shrink=1)
        except vision.VisionError:
            return None                  # связь, а не меткость — не запоминаем
        got = _point_of(raw)
        if got:
            answers.append((spot, got[:2]))

    # Ответили не на все точки — меткость не узнать. Оборванный ответ у
    # рассуждающей модели бывает и при хорошей меткости, поэтому рук не
    # даём, но и не запоминаем: следующая помеха спросит заново.
    if len(answers) < len(spots):
        if say is not None:
            say("  руки: {} ответила не на все проверочные точки — пока без рук".format(model))
        return None
    way, miss = _fit(answers, iw, ih)
    _HANDS[key] = way if miss <= config.HANDS_MAX_MISS else None
    if say is not None:
        name = way if isinstance(way, str) else "своя шкала"
        if _HANDS[key] is None:
            say("  руки: {} промахивается по проверочным точкам на {:.0f} пикс. — "
                "остаётся список кнопок".format(model, miss))
        else:
            say("  руки: {} попадает с промахом {:.0f} пикс. ({}) — можно нажимать "
                "по точке".format(model, miss, name))
    return _HANDS[key]


def _full_shot():
    """Полноразмерный снимок экрана: (png, ширина, высота) или (None, 0, 0).

    Сырой `screencap` и PNG на ПК (`picture`) — за доли секунды; `-p` — только
    запасной путь: там PNG кодирует телефон, и под видео это секунды.
    """
    try:
        raw = adb.exec_out("screencap", timeout=20)
    except adb.AdbError:
        raw = b""
    png = picture.from_screencap(raw, 1) if raw else None
    if png is None or vision.looks_blank(png):
        png = _screencap()
    if png is None:
        return None, 0, 0
    size = picture.size_of(png) or (0, 0)
    return png, size[0], size[1]


def _feed_moves(w, h):
    """Листается ли лента: свайп и сравнение кадров до и после.

    Свобода, когда дерева нет: окно поверх видео дерево не отдаёт так же, как
    сама лента, и по дереву их не различить. Порог — тот же, что у сессии.
    """
    first, _, _ = _full_shot()
    human.scroll_feed(w, h, "up", quick=True)
    human.pause(1.2, 2.0)
    second, _, _ = _full_shot()
    if first is None or second is None:
        return False
    return vision.frames_differ(vision.frame_signature(first),
                                vision.frame_signature(second)) >= config.FEED_SAME_LIMIT


def _ask_point(png, iw, ih, way, w, h, feed, history):
    """Спросить точку на полном снимке. Решение в координатах ЭКРАНА."""
    raw = vision.ask(png, ASK_POINT.format(
        w=iw, h=ih, feed=" " + feed if feed else "",
        tried=("\n" + _describe_tried(history).strip()) if history else ""),
        system=SYSTEM, max_tokens=ANSWER_TOKENS, temperature=0.1, shrink=1)
    data = vision._json_from(raw)
    other = str(data.get("иначе", "")).lower().strip() if isinstance(data, dict) else ""
    if other:
        return parse_else(json.dumps({"иначе": other}, ensure_ascii=False))
    got = _point_of(raw)
    if not got:
        return {"действие": "", "кнопка": 0, "почему": "руки: непонятный ответ"}
    x, y = _to_image(got[0], got[1], way, iw, ih)
    return {"действие": "point", "кнопка": 0, "img": (x, y), "что": got[2],
            "x": x * w / iw, "y": y * h / ih, "почему": "точка «{}»".format(got[2])}


def _node_at(nodes, x, y, w, h):
    """Кнопка под пальцем, с притяжением. (узел, мелкий ли) или (None, False).

    Мелкая кнопка под точкой — она. Нет её — ближайшая мелкая в пределах
    HANDS_SNAP_PX: промах модели 35-60 пикселей, а тап на 50 выше вкладки
    Reels пришёлся бы в пост (замер). Только потом крупный контейнер под
    точкой — жать тогда саму точку, а не случайное место панели во весь экран.
    """
    live = [n for n in nodes if n.clickable and n.enabled and n.area > 0]
    small = w * h * UNNAMED_MAX_SHARE

    def inside(n):
        x0, y0, x1, y1 = n.bounds
        return x0 <= x <= x1 and y0 <= y <= y1

    def gap(n):
        x0, y0, x1, y1 = n.bounds
        dx, dy = max(x0 - x, 0, x - x1), max(y0 - y, 0, y - y1)
        return (dx * dx + dy * dy) ** 0.5

    under = [n for n in live if inside(n)]
    tiny = [n for n in under if n.area <= small]
    if tiny:
        return min(tiny, key=lambda n: n.area), True
    near = [(gap(n), i) for i, n in enumerate(live)
            if n.area <= small and gap(n) <= config.HANDS_SNAP_PX]
    if near:
        return live[min(near)[1]], True
    if under:
        return min(under, key=lambda n: n.area), False
    return None, False


def _unquoted(text):
    """Описание без кавычек: «над меню «Поделиться»» не должно звучать как
    «нажать «Поделиться»» — опасность вблизи решает отдельный вопрос."""
    return re.sub(r"«[^»]*»|\"[^\"]*\"", " ", text or "")


def _yes(value):
    return value is True or str(value).lower() in ("true", "да", "1")


def _confirm(png, iw, ih, point, feed, claimed=""):
    """Второй взгляд вблизи. (x, y) на картинке — жать сюда; или (None, почему).

    `claimed` — как модель назвала цель на полном снимке («крестик окна»).
    """
    half = config.HANDS_ZOOM // 2
    x, y = point
    x0 = int(min(max(0, x - half), max(0, iw - 2 * half)))
    y0 = int(min(max(0, y - half), max(0, ih - 2 * half)))
    x1, y1 = min(iw, x0 + 2 * half), min(ih, y0 + 2 * half)
    crop = picture.crop_png(png, (x0, y0, x1, y1), ring=(x - x0, y - y0))
    if crop is None:
        return None, "не вырезался фрагмент для проверки"
    raw = vision.ask(crop, ASK_CLOSER.format(feed=" " + feed if feed else ""),
                     system=SYSTEM, max_tokens=ANSWER_TOKENS, temperature=0.1, shrink=1)
    data = vision._json_from(raw)
    if not isinstance(data, dict) or "опасно" not in data:
        # `_json_from` вытаскивает из оборванного JSON только поля разбора
        # ленты — «опасно» ищем сами.
        m = re.search(r'"опасно"\s*:\s*(true|false)', raw or "")
        if not m:
            return None, "вблизи модель не сказала, безопасно ли"
        data = {"опасно": m.group(1) == "true", "что": ""}
    what = str(data.get("что", "")).strip()[:60]
    if _yes(data["опасно"]):
        return None, "вблизи это «{}» — опасно".format(what)
    if _forbidden(_unquoted(what)):
        return None, "вблизи это «{}» — такое не жму".format(what)
    aimed = any(word in (claimed or "").lower() for word in CONTROL_WORDS)
    if aimed and "кнопка" in data and not _yes(data["кнопка"]):
        return None, "целилась в «{}», а вблизи там «{}»".format(claimed[:30], what)
    return (x, y), what


def _hand_tap(hint, nodes, w, h, png, iw, ih, feed):
    """Нажать точку, если это безопасно. (что сделали, None) или (None, почему нет).

    Предохранители по порядку:
      * точка на экране;
      * кнопка под пальцем (с притяжением) проверяется по ПОДПИСИ — тем же
        списком `NEVER`, что и меню; поле ввода не жмём;
      * нет мелкой кнопки под пальцем (окно поверх видео, фон) — второй
        взгляд вблизи, `_confirm`, и там модель отвечает, опасно ли.
    Экраны, откуда можно что-то отправить, сюда не доходят вовсе (`escape`).
    """
    x, y = hint["x"], hint["y"]
    if not (0 <= x < w and 0 <= y < h):
        return None, "точка ({:.0f}, {:.0f}) вне экрана".format(x, y)
    node, small = _node_at(nodes, x, y, w, h) if nodes else (None, False)
    if node is not None:
        label = _label_of(node)
        if label and _forbidden(label):
            return None, "под пальцем «{}» — такое не жму".format(label)
        if "EditText" in node.cls:
            return None, "под пальцем поле ввода"
        if small:
            ui.tap_node(node)
            human.pause(0.8, 1.6)
            return "точка -> " + _name_of(node), None

    at, what = _confirm(png, iw, ih, hint["img"], feed, hint.get("что", ""))
    if at is None:
        return None, what
    tx, ty = at[0] * w / iw, at[1] * h / ih
    # Палец не попадает в пиксель, но и не на 4 % экрана, как `jitter_px`:
    # крестик бывает шириной в 60 пикселей.
    adb.tap(int(tx + random.uniform(-4, 4)), int(ty + random.uniform(-4, 4)))
    human.pause(0.8, 1.6)
    return "точка ({:.0f}, {:.0f}) «{}»".format(tx, ty, what or hint.get("что", "")), None


def escape(goal="лента не листается", package=None, done=None, log=None,
           max_steps=None, grab=None, shrink=None, deadline=None,
           allow_done=None, feed_labels=None, stuck=False, feed=None):
    """Выбраться с незнакомого экрана. Возвращает отчёт-словарь.

    `done` — как проверить, что мы уже свободны (у каждой ленты свой признак,
    поэтому решает вызывающий). Не задан — считаем свободой то, что дерево
    перестало сниматься: в ленте с играющим видео так и есть.

    `grab` — чем снимать кадр. Сессия отдаёт свой `_grab`, чтобы кадры
    ложились в её папку; по умолчанию — обычный screencap. При
    `ESCAPE_FULL_FRAME` модель смотрит полноразмерный снимок, и `grab` не нужен.

    `stuck` — сессия уже знает, что лента встала (кадры не меняются после
    свайпов). Тогда пустое дерево значит не «это лента», а окно поверх
    играющего видео — и в дело идут руки. `feed` — как назвать ленту модели.
    """
    steps = max_steps if max_steps is not None else config.ESCAPE_MAX_STEPS
    report = {"ok": False, "steps": [], "почему": "", "экран": ""}

    def say(line):
        if log is not None:
            log.append(line)

    if not config.VISION_ENABLED:
        report["почему"] = "зрение выключено"
        return report

    w, h = adb.screen_size()
    feed = feed or FEED_TITLES.get(package or "", "")
    full = config.ESCAPE_FULL_FRAME
    ask_shrink = 1 if full else shrink
    blind = False            # окно поверх видео: свобода — это «лента листается»
    hand_taps = 0

    def free():
        if blind:
            return _feed_moves(w, h)
        if done is not None:
            return bool(done())
        return not ui.dump(retries=1, tolerant=True, timeout=4)

    def shoot():
        """Кадр для модели: (png, ширина, высота)."""
        if full:
            return _full_shot()
        png = grab() if grab is not None else _screencap()
        size = (picture.size_of(png) or (0, 0)) if png else (0, 0)
        return png, size[0], size[1]

    # Можно ли модели вообще сказать «уже лента». Своей проверки признака у
    # неё нет, а ошибается она в одну сторону: видит ленту там, где профиль.
    # Когда свобода определяется пустым деревом (TikTok), снявшееся дерево
    # само доказывает, что мы не в ленте, — и вариант надо убрать. У Shorts
    # и Reels дерево снимается и в ленте, там вопрос осмыслен.
    may_finish = (done is not None) if allow_done is None else allow_done

    history = []
    tried = {}

    for step in range(1, steps + 1):
        if deadline and time.time() > deadline:
            report["почему"] = "время вышло"
            break

        # Не в том приложении — модели тут делать нечего. Поймано первым же
        # живым прогоном: «назад» вывалил агента на рабочий стол MIUI, и
        # дальше он честно выбирал кнопки ЛАУНЧЕРА («Экран 1», «Галерея»),
        # раз за разом, до конца лимита. Список кнопок сам по себе не
        # говорит, чьи это кнопки, — проверять надо снаружи, и это дёшево.
        if package:
            pkg, _ = adb.current_app()
            if pkg and pkg != package:
                say("  выход {}/{}: ушли в {} — возвращаю приложение".format(
                    step, steps, pkg.split(".")[-1]))
                device.open_app(package)
                device.wait_for_app(package, timeout=20)
                human.pause(1.0, 2.0)
                report["steps"].append("вернул приложение")
                history.append("ушли из приложения, вернулись обратно")
                if free():
                    report["ok"] = True
                    report["почему"] = "приложение открылось сразу на ленте"
                    break
                continue

        png, iw, ih = shoot()
        if png is None:
            report["почему"] = "кадр не снялся"
            break

        way = None
        nodes = ui.dump(retries=1, tolerant=True, timeout=5)
        if not nodes:
            way = hands_ready(iw, ih, say) if stuck else None
            if way is None:
                # Дерево не снимается — значит перед нами живая лента, а не
                # окно. Тревога ложная; тапать тут нельзя ни в коем случае.
                report["ok"] = step > 1
                report["почему"] = ("выбрались" if step > 1
                                    else "дерево не снимается — это лента, помехи нет")
                break
            # Лента стоит, а дерева нет: поверх играющего видео окно, которое
            # uiautomator не отдаёт. Раньше здесь сдавались. Сначала проверка,
            # не ложная ли тревога — нажимать по живой ленте незачем.
            if _feed_moves(w, h):
                report["ok"] = True
                report["почему"] = ("выбрались" if step > 1
                                    else "лента листается — помехи нет")
                break
            if not blind:
                blind = True
                say("  выход {}/{}: дерево не снимается, а лента стоит — окно поверх "
                    "видео, нажимаю по снимку".format(step, steps))
            if hand_taps >= config.HANDS_MAX_TAPS:
                report["почему"] = "нажатий по точке больше не даю"
                break
            png, iw, ih = _full_shot()
            if png is None:
                report["почему"] = "кадр не снялся"
                break
            try:
                hint = _ask_point(png, iw, ih, way, w, h, feed, history)
            except vision.VisionError as e:
                report["почему"] = "модель недоступна: " + str(e)[:70]
                break
            if hint["действие"] in ("done", ""):
                hint = {"действие": "none", "кнопка": 0,
                        "почему": hint.get("почему") or "руки: модель не указала точку"}
            menu = []

        # Экран составления поста — единственный, где модель не спрашивают
        # вообще. Там нет безопасной кнопки: «Поделиться» публикует, «OK»
        # подтверждает, «Сохранить черновик» оставляет след. Выход один —
        # «назад», и решает это код, а не модель.
        #
        # 2026-08-30: именно так в личном аккаунте появилась история. Агент
        # промахнулся по кнопке, попал в редактор, не понял, где он, и стал
        # нажимать кнопки, чтобы «выбраться».
        if nodes and ui.looks_like_composer(nodes):
            say("  выход {}/{}: экран публикации — не жму ничего, "
                "только «назад»".format(step, steps))
            device.back()
            human.pause(0.8, 1.6)
            report["steps"].append("ушёл с экрана публикации по «назад»")
            history.append("это был экран публикации, вышел назад")
            if free():
                report["ok"] = True
                report["почему"] = "ушёл с экрана публикации"
                break
            continue

        if nodes:
            risky = risky_screen(nodes)
            menu = _menu(nodes, w, h, risky=risky)
            # Что за экран — берём из дерева, а не у модели: надписи там
            # точные, а лишний вопрос стоит секунду и возможность соврать.
            report["экран"] = ", ".join(_texts(nodes, limit=3))[:120]

            # Вкладка ленты на экране — жмём её и не спрашиваем никого. Это
            # тот же принцип, на котором стоит весь проект: где есть точный
            # признак, догадка по картинке только вредит (см. `feed_tab`).
            tab = feed_tab(menu, feed_labels)
            if tab is not None and ("вкладка", _label_of(tab)) not in tried:
                tried[("вкладка", _label_of(tab))] = 1
                say("  выход {}/{}: [{}] -> вкладка ленты {}".format(
                    step, steps, report["экран"][:60], _name_of(tab)))
                ui.tap_node(tab)
                human.pause(0.9, 1.7)
                report["steps"].append("вкладка " + _name_of(tab))
                if free():
                    report["ok"] = True
                    report["почему"] = "вернулся по вкладке ленты"
                    break
                history.append("вкладка {} — лента не появилась".format(_name_of(tab)))
                continue

            try:
                hint = _ask_menu(png, menu, history, w, h, ask_shrink)
                # Кнопки из списка не подошли — руки. Но НЕ там, откуда можно
                # что-то отправить: на листе «Поделиться» каждая аватарка
                # шлёт ролик человеку, а выход и так один — крестик или
                # «назад». Решает список, а он там урезан до безопасного.
                if (not hint["действие"] and not risky
                        and hand_taps < config.HANDS_MAX_TAPS):
                    way = hands_ready(iw, ih, say)
                    if way is not None:
                        hint = _ask_point(png, iw, ih, way, w, h, feed, history)
                        if hint["действие"] == "done" and not may_finish:
                            hint = {"действие": "", "кнопка": 0, "почему": ""}
                if not hint["действие"]:
                    hint = _ask_else(png, history, ask_shrink, may_finish)
            except vision.VisionError as e:
                report["почему"] = "модель недоступна: " + str(e)[:70]
                break

        action, button = hint["действие"], hint["кнопка"]
        target = (" " + _name_of(menu[button - 1])) if button else ""
        if action == "point":
            target = " ({:.0f}, {:.0f}) «{}»".format(hint["x"], hint["y"], hint.get("что", ""))
        say("  выход {}/{}: [{}] -> {}{}".format(
            step, steps, report["экран"][:60] or ("окно поверх видео" if blind else ""),
            action, target))

        if action == "done":
            report["ok"] = free()
            report["почему"] = ("модель считает, что помехи нет"
                                if report["ok"]
                                else "модель сказала «готово», а это не лента")
            if report["ok"]:
                break
            history.append("сказали «done», но лента не появилась")
            continue

        if action == "none":
            report["почему"] = str(hint.get("почему", "модель не предложила действия"))
            break

        key = (action, button, str(hint.get("куда", "")))
        if action == "point":
            # Та же точка с точностью до промаха модели — тот же повтор.
            key = (action, round(hint["x"] / 80), round(hint["y"] / 80))
        tried[key] = tried.get(key, 0) + 1
        if tried[key] > 1:
            # Второй раз то же самое — модель зациклилась. Дальше решаем сами:
            # «назад» безопаснее любого повтора, а лесенка `_unstick` всё
            # равно ждёт следующей ступенью.
            say("  выход: повтор того же действия — жму «назад»")
            device.back()
            human.pause(0.8, 1.6)
            history.append("повтор -> нажали «назад» за модель")
            if free():
                report["ok"] = True
                report["почему"] = "закрылось по «назад»"
                break
            continue

        before = vision.frame_signature(png)
        if action == "point":
            hand_taps += 1
            did, refused = _hand_tap(hint, nodes, w, h, png, iw, ih, feed)
            if did is None:
                # Сомнительная точка — не жмём, а уходим «назад»: это выход,
                # который ничего не отправляет и почти всегда что-то закрывает.
                say("  выход: не жму — " + refused + "; вместо этого «назад»")
                history.append("точка «{}» — не нажал: {}".format(hint.get("что", ""), refused))
                device.back()
                human.pause(0.8, 1.6)
                did = "назад вместо сомнительной точки"
        else:
            did = _run(action, button, str(hint.get("куда", "")), menu, w, h)
        if not did:
            report["почему"] = "действие выполнить не удалось"
            break
        report["steps"].append(did)

        if free():
            report["ok"] = True
            report["почему"] = "помогло: " + did
            break

        after = shoot()[0]
        moved = (vision.frames_differ(before, vision.frame_signature(after))
                 if after else 255.0)
        history.append(did + " -> " + ("экран изменился, но лента не вернулась"
                                       if moved > 8 else "экран не изменился"))

    if not report["ok"] and not report["почему"]:
        report["почему"] = "не вышло за {} шагов".format(steps)
    say("  выход: {} — {}".format("получилось" if report["ok"] else "не вышло",
                                  report["почему"]))
    return report


def _screencap():
    try:
        png = adb.exec_out("screencap -p", timeout=20)
    except adb.AdbError:
        return None
    return None if vision.looks_blank(png) else png
