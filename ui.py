"""Дерево интерфейса: снять дамп, найти элемент, дождаться, тапнуть.

Это замена нейросети. Вместо «посмотри на картинку и догадайся» мы читаем
у Android готовый список всех элементов экрана с их текстом, id и точными
границами. Быстрее, точнее и не врёт.
"""
import re
import time
import xml.etree.ElementTree as ET

import adb
import human

_BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
_REMOTE = "/sdcard/pa_dump.xml"


class Node:
    __slots__ = ("text", "rid", "desc", "cls", "pkg", "clickable", "enabled", "bounds")

    def __init__(self, a):
        self.text = (a.get("text") or "").strip()
        self.rid = a.get("resource-id") or ""
        self.desc = (a.get("content-desc") or "").strip()
        self.cls = a.get("class") or ""
        self.pkg = a.get("package") or ""
        self.clickable = a.get("clickable") == "true"
        self.enabled = a.get("enabled") == "true"
        m = _BOUNDS.match(a.get("bounds") or "")
        self.bounds = tuple(int(g) for g in m.groups()) if m else (0, 0, 0, 0)

    @property
    def center(self):
        x1, y1, x2, y2 = self.bounds
        return (x1 + x2) // 2, (y1 + y2) // 2

    @property
    def area(self):
        x1, y1, x2, y2 = self.bounds
        return max(0, x2 - x1) * max(0, y2 - y1)

    def __repr__(self):
        label = self.text or self.desc or self.rid.split("/")[-1] or self.cls.split(".")[-1]
        return f"<Node {label!r} {self.bounds}>"


def dump(retries=3, tolerant=False, timeout=30):
    """Снять дерево текущего экрана. Возвращает список Node.

    tolerant=True — вернуть [] вместо исключения. Нужно для экранов, где
    uiautomator не работает в принципе: пока в ленте играет видео,
    accessibility-слой никогда не приходит в покой, и дамп падает с
    «could not get idle state». Лента листается и без дерева, поэтому
    ронять из-за этого сессию нельзя.
    """
    last = None
    for attempt in range(retries):
        try:
            # 2>&1 — иначе текст ошибки уходит в stderr и теряется.
            out = adb.shell(f"uiautomator dump {_REMOTE} 2>&1", timeout=timeout, check=False)
            if "dumped to" not in out and "UI hierchary" not in out:
                # Частая беда: «could not get idle state» когда идёт анимация.
                last = out.strip()
                time.sleep(0.8 + attempt * 0.7)
                continue
            xml = adb.exec_out(f"cat {_REMOTE}", timeout=30).decode("utf-8", "replace")
            root = ET.fromstring(xml)
            return [Node(el.attrib) for el in root.iter("node")]
        except (adb.AdbError, ET.ParseError) as e:
            last = str(e)
            time.sleep(0.8 + attempt * 0.7)
    if tolerant:
        return []
    raise adb.AdbError(f"не удалось снять дерево: {last or 'пустой ответ uiautomator'}")


def _matches(node, text=None, contains=None, rid=None, desc=None,
             cls=None, clickable=None):
    if text is not None and node.text.lower() != text.lower():
        return False
    if contains is not None and contains.lower() not in node.text.lower() \
            and contains.lower() not in node.desc.lower():
        return False
    if rid is not None:
        # Разрешаем короткую запись: "publish_btn" вместо полного пакета.
        tail = node.rid.split("/")[-1]
        if node.rid != rid and tail != rid:
            return False
    if desc is not None and desc.lower() not in node.desc.lower():
        return False
    if cls is not None and cls.lower() not in node.cls.lower():
        return False
    if clickable is not None and node.clickable != clickable:
        return False
    return True


def find(nodes, **sel):
    """Все узлы, подходящие под селектор."""
    return [n for n in nodes if _matches(n, **sel)]


def find_one(nodes, **sel):
    """Первый подходящий узел или None. Кликабельные — в приоритете."""
    hits = find(nodes, **sel)
    if not hits:
        return None
    hits.sort(key=lambda n: (not n.clickable, -n.area))
    return hits[0]


def find_any(nodes, selectors):
    """Первое совпадение по списку селекторов (разные языки интерфейса)."""
    for sel in selectors:
        node = find_one(nodes, **sel)
        if node is not None:
            return node
    return None


def wait_any(selectors, timeout=20, interval=0.7):
    """Ждать появления любого из селекторов. Возвращает (Node, дерево).

    Дамп берётся терпимым: в играющей ленте uiautomator отвечает «could not
    get idle state», и это не повод ронять весь маршрут — просто на этом
    круге дерева нет, ждём следующего.
    """
    deadline = time.time() + timeout
    nodes = []
    while time.time() < deadline:
        nodes = dump(retries=1, tolerant=True)
        node = find_any(nodes, selectors)
        if node is not None:
            return node, nodes
        time.sleep(interval)
    return None, nodes


# Служебные подписи ленты: это не описание ролика, а элементы интерфейса.
_JUNK = re.compile(
    r"^(подписаться|follow|подписки|для тебя|поиск|live|\d+[.,]?\d*[kкmм]?|"
    r"нравится|комментарии|поделиться|сохранить|ещё|more)$", re.I)


def feed_caption(nodes, screen=None, package=None):
    """Достать описание ролика из дерева ленты: (текст, автор, музыка).

    Дерево в ленте снимается далеко не всегда, поэтому вызывающий код
    обязан переживать пустой результат: описание — приятная добавка
    к картинке, а не замена ей.

    package обязателен на практике: в дамп попадают и чужие окна — шторка
    уведомлений, статус VPN, всплывашки других приложений. Один раз так
    в «описание ролика» уехало уведомление ВКонтакте вместе со строкой
    «Connected to: 88c5b1f3...» от ZeroTier.
    """
    if not nodes:
        return "", "", ""

    if package:
        nodes = [n for n in nodes if not n.pkg or n.pkg == package]
        if not nodes:
            return "", "", ""

    author = music = ""
    parts = []
    height = screen[1] if screen else 0

    for node in nodes:
        text = (node.text or node.desc or "").strip()
        if not text or _JUNK.match(text):
            continue

        rid = node.rid.split("/")[-1].lower()
        low = text.lower()

        if not author and (text.startswith("@") or "author" in rid or "nickname" in rid):
            author = text.lstrip("@")[:60]
            continue
        if not music and ("music" in rid or "sound" in rid or "оригинальный звук" in low):
            music = text[:80]
            continue

        # Описание всегда внизу кадра, над панелью навигации: так отсекаются
        # счётчики справа и заголовки сверху.
        if height and not (0.55 * height < node.bounds[1] < 0.95 * height):
            continue
        if len(text) >= 3:
            parts.append(text)

    # Самая длинная строка снизу — почти всегда и есть описание.
    parts.sort(key=len, reverse=True)
    return (" ".join(parts[:3])[:400], author, music)


def tap_node(node):
    """Тап в случайную точку внутри элемента, а не строго в центр."""
    x, y = human.tap_point(node)
    adb.tap(x, y)
    return x, y


# Кнопки, которыми закрывается подавляющее большинство внезапных окон:
# обновления, «оцените нас», запросы разрешений, туториалы.
# Порядок важен: find_one идёт по списку сверху вниз, поэтому сначала явные
# отказы, потом нейтральные закрытия.
#
# «Разрешить» и «Allow» тут были и это была ошибка: агент, закрывая окно,
# мог выдать приложению доступ к контактам или геолокации. Права не выдаём
# никогда — только отклоняем или закрываем.
# Порядок важен: от явных отказов к нейтральным закрытиям.
#
# «ОК» и «OK» отсюда УБРАНЫ 2026-08-30. Они выглядят как закрытие, но на
# половине экранов это ПОДТВЕРЖДЕНИЕ: на экране публикации Reels кнопка «OK»
# стоит в правом верхнем углу и принимает подпись. Цена ошибки — пост в
# чужом аккаунте, а выигрыш — закрытие информационного окошка, которое и так
# закрывается «Понятно», «Закрыть» или крестиком.
DISMISS_LABELS = [
    "Не разрешать", "Запретить", "Отклонить", "Нет, спасибо", "Не сейчас",
    "Don't allow", "Deny", "No thanks", "Not now",
    "Позже", "Пропустить", "Закрыть", "Отмена", "Понятно",
    "Later", "Maybe later", "Skip", "Close", "Cancel", "Got it", "Dismiss",
]


# Крестик в углу окна подписи не имеет — только описание для незрячих.
# Держим отдельно от DISMISS_LABELS: там сравнение по тексту целиком, а тут
# по описанию, и оно ищется ВХОЖДЕНИЕМ. Список нарочно короткий и только из
# слов, которые ничего не подтверждают: «Закрыть» безопасно, «Разрешить» —
# нет (на этом уже обжигались, агент чуть не выдал доступ к контактам).
CLOSE_DESCS = ["Закрыть", "Close", "Отменить", "Dismiss"]


def close_button(nodes):
    """Крестик закрытия: кликабельный, с описанием «Закрыть», и САМЫЙ МЕЛКИЙ.

    Мельчайший — потому что описание ищется вхождением, и то же слово
    попадается у контейнера вокруг кнопки. На выборе «покрупнее» уже
    промахивались: `{"desc": "Reels"}` находил панель во весь экран.
    """
    found = []
    for label in CLOSE_DESCS:
        for node in find(nodes, desc=label, clickable=True):
            if node.enabled and node.area > 0:
                found.append(node)
    return min(found, key=lambda n: n.area) if found else None


# Экраны, на которых НЕЛЬЗЯ жать ничего вслепую. Здесь любая кнопка что-то
# отправляет или публикует, а в `DISMISS_LABELS` есть «ОК» и «Понятно» —
# и на экране публикации Reels кнопка «OK» стоит прямо в углу.
# Поймано 2026-08-30: в аккаунте появилась история, которую никто не выкладывал.
COMPOSER_MARKS = (
    "ваша история", "добавить в историю", "your story", "add to story",
    "опубликовать", "поделиться также", "share to", "новое видео reels",
    "сохранить черновик", "save draft", "загрузить видео",
)


def looks_like_composer(nodes):
    """Экран составления/отправки? Тогда вслепую тыкать нельзя."""
    for node in nodes or []:
        label = ((node.desc or "") + " " + (node.text or "")).lower()
        if any(mark in label for mark in COMPOSER_MARKS):
            return True
    return False


def dismiss_popup(nodes=None):
    """Попробовать закрыть неизвестное окно. True, если что-то нажали.

    Эта эвристика заменяет собой добрую половину случаев, где иначе
    понадобилась бы vision-модель.
    """
    # Дерево может не сняться (играющее видео) — это не повод падать:
    # раз окна не видно, значит и закрывать нечего.
    nodes = nodes if nodes is not None else dump(retries=2, tolerant=True)

    # На экране публикации не закрываем ничего: «ОК» там подтверждает, а не
    # отменяет, и цена ошибки — пост в чужом аккаунте.
    if looks_like_composer(nodes):
        return False
    for label in DISMISS_LABELS:
        node = find_one(nodes, text=label, clickable=True)
        if node and node.enabled:
            tap_node(node)
            human.pause(0.6, 1.4)
            return True
    # Подписи не нашлось — ищем крестик. У окна «Подпишитесь на друзей»
    # закрывающая кнопка нарисована значком и текста не имеет вовсе.
    node = close_button(nodes)
    if node is not None:
        tap_node(node)
        human.pause(0.6, 1.4)
        return True
    return False


def describe(nodes, limit=40):
    """Читаемый срез экрана — для логов и отладки селекторов."""
    lines = []
    for n in nodes:
        label = n.text or n.desc
        if not label and not n.rid:
            continue
        tag = "clickable" if n.clickable else "-"
        lines.append(f"{label!r:40} id={n.rid.split('/')[-1]:25} {tag:9} {n.bounds}")
        if len(lines) >= limit:
            break
    return "\n".join(lines)
