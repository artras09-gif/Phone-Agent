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
"""
import time

import adb
import config
import device
import human
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
    # публикация
    "опубликовать", "отправить", "поделиться", "share", "репост", "repost",
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


def _decide(png, menu, history, w, h, shrink=None, allow_done=False):
    """Спросить модель, что делать. Возвращает разобранное решение."""
    if menu:
        raw = vision.ask(png, build_prompt(menu, history, w, h),
                         system=SYSTEM, max_tokens=80, temperature=0.1,
                         shrink=shrink)
        hint = parse(raw, menu)
        if hint["действие"]:
            return hint

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


def escape(goal="лента не листается", package=None, done=None, log=None,
           max_steps=None, grab=None, shrink=None, deadline=None,
           allow_done=None, feed_labels=None):
    """Выбраться с незнакомого экрана. Возвращает отчёт-словарь.

    `done` — как проверить, что мы уже свободны (у каждой ленты свой признак,
    поэтому решает вызывающий). Не задан — считаем свободой то, что дерево
    перестало сниматься: в ленте с играющим видео так и есть.

    `grab` — чем снимать кадр. Сессия отдаёт свой `_grab`, чтобы кадры
    ложились в её папку; по умолчанию — обычный screencap.
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

    def free():
        if done is not None:
            return bool(done())
        return not ui.dump(retries=1, tolerant=True, timeout=4)

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

        png = grab() if grab is not None else _screencap()
        if png is None:
            report["почему"] = "кадр не снялся"
            break

        nodes = ui.dump(retries=1, tolerant=True, timeout=5)
        if not nodes:
            # Дерево не снимается — значит перед нами живая лента, а не окно.
            # Тревога ложная; тапать тут нельзя ни в коем случае.
            report["ok"] = step > 1
            report["почему"] = ("выбрались" if step > 1
                                else "дерево не снимается — это лента, помехи нет")
            break

        # Экран составления поста — единственный, где модель не спрашивают
        # вообще. Там нет безопасной кнопки: «Поделиться» публикует, «OK»
        # подтверждает, «Сохранить черновик» оставляет след. Выход один —
        # «назад», и решает это код, а не модель.
        #
        # 2026-08-30: именно так в личном аккаунте появилась история. Агент
        # промахнулся по кнопке, попал в редактор, не понял, где он, и стал
        # нажимать кнопки, чтобы «выбраться».
        if ui.looks_like_composer(nodes):
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

        menu = _menu(nodes, w, h)
        # Что за экран — берём из дерева, а не у модели: надписи там точные,
        # а лишний вопрос стоит секунду и ещё одну возможность соврать.
        report["экран"] = ", ".join(_texts(nodes, limit=3))[:120]

        # Вкладка ленты на экране — жмём её и не спрашиваем никого. Это тот
        # же принцип, на котором стоит весь проект: где есть точный признак,
        # догадка по картинке только вредит (см. `feed_tab`).
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
            hint = _decide(png, menu, history, w, h, shrink, may_finish)
        except vision.VisionError as e:
            report["почему"] = "модель недоступна: " + str(e)[:70]
            break

        action, button = hint["действие"], hint["кнопка"]
        target = (" " + _name_of(menu[button - 1])) if button else ""
        say("  выход {}/{}: [{}] -> {}{}".format(
            step, steps, report["экран"][:60], action, target))

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
        did = _run(action, button, str(hint.get("куда", "")), menu, w, h)
        if not did:
            report["почему"] = "действие выполнить не удалось"
            break
        report["steps"].append(did)

        if free():
            report["ok"] = True
            report["почему"] = "помогло: " + did
            break

        after = grab() if grab is not None else _screencap()
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
