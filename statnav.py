# -*- coding: utf-8 -*-
"""Сама находит экран статистики, когда жёсткий маршрут сломался.

Маршруты в `accstats` записаны руками: «☰ → TikTok Studio → Аналитика».
Приложение обновилось, кнопка переехала или переименовалась — маршрут
приходит не туда, и раньше это значило «чинить руками» после каждого
обновления (19.11 было уже десятое такое).

Здесь три ступени, от дешёвой к дорогой:

1. **Жёсткий маршрут** (`accstats.SHOTS`) — как раньше, первым. Не тронут.
2. **Запомненный путь** (`routes.json`) — тот, что модель нашла в прошлый
   раз. Повторяется без модели: кнопки узнаются по подписи, id и месту,
   а не по координатам. Быстро и бесплатно.
3. **Модель ищет дорогу** — смотрит снимок и список кнопок, жмёт, видит, что
   получилось, жмёт дальше, пока на экране не окажется статистика. Найденный
   путь запоминается (ступень 2), и следующий раз идёт без модели.

Модель выбирает кнопку ИЗ СПИСКА, а не точку на экране (так надёжнее, см.
`escape.py`). В список не попадает ничего, что может навредить: публикация,
сообщения, переключение аккаунта, истории, оплата, удаление, переключатели
настроек (`NEVER`). Начинается всё со своего профиля, и только в аккаунтах,
которые уже хоть раз давали статистику (`accstats._nav_allowed`): у личного
Instagram статистики нет, и модель в него не заходит.

Эталоны: при каждом найденном пути рядом с ним сохраняются деревья экранов
каждого шага (`routes/<сеть>/<версия>/`). По ним `scratchpad/golden_routes.py`
проверяет без телефона, что путь всё ещё узнаётся; при поломке сохраняется
дерево экрана, где путь оборвался, — видно, что именно поменяли в приложении.
"""
import datetime as dt
import json
import os
import re
import time

import abort
import adb
import config
import device
import escape
import human
import ui
import vision

# Что ищем — словами для модели. Подписи на двух языках: телефоны бывают
# и с русским, и с английским интерфейсом.
GOALS = {
    "tiktok": ("TikTok", "TikTok Studio → «Статистика» / «Аналитика» (Analytics): экран, где "
                         "ВВЕРХУ выбирается период (7 дней, 28 дней, «Другое» / Custom) и есть "
                         "«Просмотры публикаций» (Post views). Главная TikTok Studio (вкладки "
                         "«Инструменты» и «Трансляция») — ещё не цель: оттуда — в карточку "
                         "«Статистика ›»"),
    "shorts": ("YouTube Studio", "раздел «Аналитика» (Analytics) канала: выбор периода "
                                 "(28 дней и т. п.), число просмотров (Views) и график. "
                                 "Главная Studio («Панель управления») — ещё не цель"),
    "reels": ("Instagram", "статистика СВОЕГО профиля («Статистика» / Insights → «Обзор»): "
                           "выбор периода (последние 7 / 30 дней) и число просмотров (Views). "
                           "Профиль и «Ваша панель» — ещё не цель"),
    "facebook": ("Facebook", "панель профессионала (Professional dashboard) → Insights / "
                             "Analytics: выбор периода и число просмотров (Views)"),
    "threads": ("Threads", "статистика профиля (Insights): выбор периода (7 / 14 / 30 дней) "
                           "и число просмотров (Views)"),
}

# Чем экран похож на статистику, но ею не является — для строгой проверки
# (`look`). 25.11: главная TikTok Studio («Статистика: 50 тыс. за 7 дн.»)
# прошла за статистику, и самопочинка промолчала.
NOT_STATS = {
    "tiktok": "Главная TikTok Studio (вкладки «Инструменты» и «Трансляция», карточки "
              "«Статистика», «Монетизация») — НЕ экран статистики.",
    "shorts": "Главная YouTube Studio («Панель управления», «Последняя опубликованная») — "
              "НЕ экран статистики.",
    "reels": "Профиль и «Ваша панель» (профессиональная панель) — НЕ экран статистики.",
    "facebook": "Профиль и лента — НЕ экран статистики.",
    "threads": "Профиль — НЕ экран статистики.",
}

# Сверх запретов `escape.NEVER`: в статистике модели нечего делать в
# переписке, чужих аккаунтах, историях и камере. Подписи проверяются по
# вхождению, без учёта регистра.
NEVER = escape.NEVER + [
    "direct", "сообщени", "message", "inbox", "чат", "chat", "ответить", "reply",
    "коммент", "comment",
    "переключ", "switch account", "switch profile", "добавить аккаунт", "add account",
    "выйти", "log out", "logout", "sign out",
    "истори", "story", "stories", "камер", "camera", "прямой эфир", "go live",
    "редактировать", "edit profile", "изменить профиль",
    "реклам", "promote", "boost", "продвиж", "монетиз", "monetiz",
    "загрузить", "upload", "новая публикац", "new post", "черновик", "draft",
    # Кампании и программы для авторов на главной TikTok Studio («Join», «Join
    # Now» — живьём 25.11): вступить в кампанию — не путь к статистике.
    "join", "присоедин", "участвов", "подать заявку", "apply", "unirse", "participar",
]

# Переключатели и галочки модели не даём вообще: в настройках, через
# которые идёт путь (Instagram: «Настройки и действия»), они меняют аккаунт.
TOGGLES = ("switch", "checkbox", "togglebutton", "radiobutton")

NAV_ASK = """Снимок экрана телефона, приложение {app}.
Цель: открыть {goal}.
Кнопки на экране:
{menu}{tried}
Ответь ТОЛЬКО JSON без пояснений:
{{"готово": true — если цель УЖЕ на экране (видно число просмотров и выбор периода), иначе false,
 "кнопка": номер кнопки из списка, которая ведёт к цели, или 0,{point}
 "иначе": "back" | "swipe" | "wait" — если ни одна кнопка не ведёт к цели}}"""

# «Руки»: на веб-страницах внутри приложения кнопок в дереве нет вовсе
# (25.11, новая TikTok Studio: «Статистика ›» нарисована, а в дереве — только
# вкладки и «Join»), и выбрать из списка модели нечего. Тогда она называет
# точку на снимке — с проверенной меткостью (`escape.hands_ready`).
NAV_POINT = """
 "точка": {{"x": число, "y": число, "что": "что это"}} — только если нужный элемент ВИДЕН
   на снимке, но его НЕТ в списке кнопок; координаты в пикселях снимка {iw}x{ih},"""

LOOK_ASK = """Снимок экрана телефона, приложение {app}.
Это экран статистики аккаунта, где видно число просмотров (Views / «Просмотры») И есть
переключатель периода (7 дней, 28 дней, «Последние 30 дней», «Другое», Custom и т. п.)?
{not_stats}
Ответь ТОЛЬКО JSON без пояснений:
{{"статистика": true или false,
 "выбор_периода": true, если на экране есть переключатель периода, иначе false,
 "период": "период, за который показаны числа, как написан на экране (например «Nov 18 - Nov 18», «Last 7 days», «28 дней»)",
 "просмотры": "число просмотров за этот период, как написано на экране"}}
Не экран статистики или числа не видно — {{"статистика": false}}."""

# Почему поиск пути не удался — для отчёта (`accstats._self_nav`).
LAST = {"why": ""}

FAIL_DAYS = 7         # не нашла — столько дней не искать на той же версии

NUMBER = re.compile(r"^[\d\s.,  ]+[KkMmКкМм]?$|^[\d\s.,  ]*\d\s*(тыс|млн)\.?$")


def _say(log, line):
    if log is not None:
        log.append(line)
    else:
        print(line, flush=True)


# ------------------------------------------------------------ хранилище

def _root():
    return os.path.join(os.path.dirname(config.QUEUE_DIR), "analytics")


def routes_path():
    return os.path.join(_root(), "routes.json")


def load_routes():
    try:
        with open(routes_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_routes(data):
    os.makedirs(_root(), exist_ok=True)
    tmp = routes_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, routes_path())


def _golden_dir(net, ver):
    safe = re.sub(r"[^\w.\-]", "_", ver or "без-версии")
    return os.path.join(_root(), "routes", net, safe)


def tree_to_json(nodes):
    """Дерево экрана — в JSON, из которого `tree_from_json` соберёт те же Node."""
    out = []
    for n in nodes or []:
        x1, y1, x2, y2 = n.bounds
        out.append({"text": n.text, "resource-id": n.rid, "content-desc": n.desc,
                    "class": n.cls, "package": n.pkg,
                    "clickable": "true" if n.clickable else "false",
                    "enabled": "true" if n.enabled else "false",
                    "selected": "true" if n.selected else "false",
                    "bounds": f"[{x1},{y1}][{x2},{y2}]"})
    return out


def tree_from_json(items):
    return [ui.Node(a) for a in items or []]


def _keep_tree(folder, name, nodes, w, h):
    try:
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, name + ".json"), "w", encoding="utf-8") as f:
            json.dump({"w": w, "h": h, "nodes": tree_to_json(nodes)}, f, ensure_ascii=False)
    except OSError:
        pass


# --------------------------------------------------------- кнопки и путь

def _label(node):
    return escape._label_of(node)


def forbidden(label):
    low = (label or "").lower().strip()
    if any(low == d.lower() for d in ui.DISMISS_LABELS):
        return False
    return any(bad in low for bad in NEVER)


def menu(nodes, w, h, limit=20):
    """Кнопки, которые модели можно нажимать по пути к статистике."""
    picked = escape._menu(nodes, w, h, limit=limit * 2, risky=False)
    out = [n for n in picked
           if not forbidden(_label(n)) and not any(t in n.cls.lower() for t in TOGGLES)]
    return out[:limit]


def signature(node, w, h):
    """Как узнать кнопку в следующий раз: подпись, id, место в долях экрана."""
    x, y = node.center
    return {"label": _label(node), "rid": node.rid.split("/")[-1],
            "x": round(x / w, 3), "y": round(y / h, 3)}


def match(nodes, sig, w, h):
    """Найти кнопку по подписи; нет подписи — по id и месту. None — нет такой."""
    want = (sig.get("label") or "").casefold()
    cands = [n for n in nodes or [] if n.clickable and n.enabled and n.area > 0]

    def dist(n):
        x, y = n.center
        return ((x / w - sig.get("x", 0.5)) ** 2 + (y / h - sig.get("y", 0.5)) ** 2) ** 0.5

    if want:
        same = [n for n in cands if _label(n).casefold() == want]
        if not same:
            # Подпись на кнопке — часто у дочернего текста, а не у самой
            # кнопки: ищем текст и берём кликабельного «родителя» по рамке.
            texts = [n for n in nodes or [] if (n.text or n.desc).strip().casefold() == want]
            same = [c for c in cands for t in texts
                    if c.bounds[0] <= t.center[0] <= c.bounds[2]
                    and c.bounds[1] <= t.center[1] <= c.bounds[3]]
        return min(same, key=dist) if same else None
    if sig.get("rid"):
        same = [n for n in cands if n.rid.split("/")[-1] == sig["rid"]]
        if same:
            return min(same, key=dist)
    near = [n for n in cands if not _label(n) and dist(n) < 0.06]
    return min(near, key=dist) if near else None


def _do(step, w, h):
    what = step.get("do")
    if what == "swipe":
        human.scroll_feed(w, h, "up", quick=True)
        human.pause(0.8, 1.4)
    elif what == "wait":
        human.pause(1.5, 2.5)


def replay(steps, w, h, keep=None):
    """Пройти запомненный путь. True — прошли все шаги. keep — папка, куда
    положить дерево экрана, на котором путь оборвался."""
    for i, step in enumerate(steps, 1):
        if abort.requested():
            return False
        if step.get("do"):
            _do(step, w, h)
            continue
        if step.get("point"):
            # Точка с веб-страницы: кнопки в дереве нет, жмём по месту.
            human.pause(1.0, 1.6)
            adb.tap(int(step["point"][0] * w), int(step["point"][1] * h))
            human.pause(1.8, 2.8)
            continue
        node = None
        for _ in range(3):                       # экран мог ещё грузиться
            nodes = ui.dump(retries=1, tolerant=True)
            node = match(nodes, step, w, h)
            if node is not None:
                break
            human.pause(1.2, 1.8)
        if node is None:
            if keep:
                _keep_tree(keep, f"обрыв_шаг{i}", nodes, w, h)
            return False
        ui.tap_node(node)
        human.pause(1.5, 2.5)
    return True


# ------------------------------------------------------------- модель

def look(png_path, app, net=""):
    """Что модель видит на снимке: {"статистика", "период", "просмотры"}.
    «Статистика» — только с переключателем периода: сводка «за 7 дней» на
    главной Studio — не она. {} — модель не ответила или ответила не по форме."""
    try:
        with open(png_path, "rb") as f:
            raw = vision.ask(f.read(), LOOK_ASK.format(app=app, not_stats=NOT_STATS.get(net, "")),
                             max_tokens=200, temperature=0.0)
    except Exception as e:
        print(f"  [путь] модель не ответила: {str(e)[:80]}", flush=True)
        return {}
    data = vision._json_from(raw or "")
    if not isinstance(data, dict):
        return {}
    views = str(data.get("просмотры") or "").strip()
    return {"статистика": data.get("статистика") is True and data.get("выбор_периода") is True,
            "период": str(data.get("период") or "").strip()[:60],
            "просмотры": views if NUMBER.match(views) and not views.startswith("-") else ""}


def _ask_nav(png, app, goal, items, history, w, h, hands=None, iw=0, ih=0):
    tried = ("\nУже пробовали: " + "; ".join(history[-5:]) + ".") if history else ""
    point = NAV_POINT.format(iw=iw, ih=ih) if hands else ""
    raw = vision.ask(png, NAV_ASK.format(app=app, goal=goal, point=point,
                                         menu=escape._describe_menu(items, w, h), tried=tried),
                     max_tokens=escape.ANSWER_TOKENS, temperature=0.1, think=True,
                     shrink=1 if hands else None)
    data = vision._json_from(raw or "")
    return data if isinstance(data, dict) else {}


def _screen_key(nodes):
    """Чем экран отличается от других — чтобы заметить, что ходим по кругу."""
    return "|".join(sorted({(n.text or n.desc or "").strip()[:30] for n in nodes or []
                            if n.clickable and (n.text or n.desc)})[:40])


def _point_ok(nodes, x, y, w, h, what):
    """Можно ли жать по точке модели: не системные полосы, не запретное под
    пальцем и не запретное в её же описании. (да/нет, почему)."""
    if not (h * 0.04 < y < h * 0.95 and 0 <= x <= w):
        return False, "точка вне приложения"
    if forbidden(what):
        return False, f"«{what}» — запрещено"
    node, _ = escape._node_at(nodes, x, y, w, h)
    if node is not None:
        label = _label(node)
        if label and forbidden(label):
            return False, f"под точкой «{label}» — запрещено"
        if any(t in node.cls.lower() for t in TOGGLES):
            return False, "под точкой переключатель"
    return True, ""


def explore(net, package, w, h, log=None, max_steps=None, keep=None, goal=None):
    """Модель ищет дорогу к статистике: (путь, "") если модель сказала
    «готово», иначе (None, почему). Почему "down" — модель не отвечает:
    это сбой связи, а не «статистики нет». Проверку «это правда
    статистика» делает вызывающий — по снимку. goal — своя цель вместо
    «дойти до статистики» (выбор дня, `pick_day`)."""
    app, goal = GOALS[net][0], goal or GOALS[net][1]
    steps, history = [], []
    tapped = {}                # экран -> что на нём уже жали (не ходить по кругу)
    for n in range(1, (max_steps or config.STATS_NAV_STEPS) + 1):
        if abort.requested():
            return None, "остановлено"
        if adb.current_app()[0] != package:
            # Ушли в другое приложение (браузер, магазин) — назад, и если не
            # вернулись, дальше не идём: чужой экран не наш.
            device.back()
            human.pause(1, 1.5)
            if adb.current_app()[0] != package:
                _say(log, f"  [путь] {app}: вышли из приложения — остановился")
                return None, "вышли из приложения"
            if steps:
                steps.pop()
            history.append("последняя кнопка уводила из приложения")
            continue
        nodes = ui.dump(retries=1, tolerant=True)
        png, iw, ih = escape._full_shot()
        if not png:
            human.pause(1, 2)
            continue
        if keep:
            _keep_tree(keep, f"шаг{n}", nodes, w, h)
        key = _screen_key(nodes)
        done_here = tapped.setdefault(key, set())
        # Что на этом же экране уже жали — модели не предлагаем: 25.11 она
        # двенадцать шагов подряд ходила «значок → Просмотры профиля → назад».
        items = [it for it in menu(nodes, w, h) if _label(it) + str(it.bounds) not in done_here]
        if done_here:
            history.append("на этом экране уже пробовали: " + ", ".join(
                sorted(x.split("(")[0] or "значок" for x in done_here))[:120])
        hands = escape.hands_ready(iw, ih) if config.ESCAPE_HANDS else None
        try:
            answer = _ask_nav(png, app, goal, items, history, w, h, hands, iw, ih)
        except Exception as e:
            _say(log, f"  [путь] {app}: модель не ответила — {str(e)[:80]}")
            return None, "down"
        if answer.get("готово") is True:
            return steps, ""
        try:
            button = int(answer.get("кнопка") or 0)
        except (TypeError, ValueError):
            button = 0
        if 1 <= button <= len(items):
            node = items[button - 1]
            done_here.add(_label(node) + str(node.bounds))
            sig = signature(node, w, h)
            sig["tree"] = f"шаг{n}"              # эталон: на каком экране жали
            steps.append(sig)
            history.append(f"нажали {escape._name_of(node)}")
            ui.tap_node(node)
            human.pause(1.5, 2.5)
            continue
        spot = answer.get("точка")
        if hands and isinstance(spot, dict):
            try:
                px, py = escape._to_image(float(spot.get("x")), float(spot.get("y")), hands, iw, ih)
            except (TypeError, ValueError):
                px = py = None
            if px is not None:
                x, y = int(px * w / iw), int(py * h / ih)
                what = str(spot.get("что") or "").strip()[:60]
                fine, why = _point_ok(nodes, x, y, w, h, what)
                if not fine:
                    history.append(f"точку «{what}» не нажал: {why}")
                    continue
                done_here.add(f"точка {what}({x // 40},{y // 40})")
                steps.append({"point": [round(x / w, 3), round(y / h, 3)], "что": what,
                              "tree": f"шаг{n}"})
                history.append(f"нажали точку «{what}»")
                _say(log, f"  [путь] {app}: жму по точке «{what}» ({x},{y})")
                adb.tap(x, y)
                human.pause(1.8, 2.8)
                continue
        other = str(answer.get("иначе") or "").lower().strip()
        if other in ("back", "назад"):
            device.back()
            human.pause(1, 1.6)
            if steps:
                steps.pop()                      # шаг назад — тот шаг был лишним
            history.append("назад")
        elif other in ("swipe", "свайп"):
            steps.append({"do": "swipe"})
            _do({"do": "swipe"}, w, h)
            history.append("пролистали вниз")
        elif other in ("wait", "ждать"):
            _do({"do": "wait"}, w, h)
            history.append("подождали")
        else:
            history.append("непонятный ответ")
    _say(log, f"  [путь] {app}: за {max_steps or config.STATS_NAV_STEPS} шагов статистику не нашёл")
    return None, "не дошла за отведённые шаги"


# -------------------------------------------------------------- главное

def find_stats(net, package, snap, start, ver="", log=None):
    """Дойти до экрана статистики сети и снять его.

    snap() -> путь к снимку или None (снимок кладёт `accstats._snap`, он же
    проверяет, что на экране своё приложение); start() -> True, если стоим
    на своём профиле (или в приложении статистики). Возвращает
    {"path", "period", "views", "how"} или None.
    """
    LAST["why"] = ""
    if not (config.STATS_SELF_NAV and vision.available()[0]):
        LAST["why"] = "зрение выключено или не настроено"
        return None
    app = GOALS[net][0]
    w, h = adb.screen_size()
    routes = load_routes()
    route = routes.get(net) or {}
    gold = _golden_dir(net, ver)

    def check(how):
        path = snap()
        if not path:
            return None
        seen = look(path, app, net)
        if seen.get("статистика") and seen.get("просмотры"):
            return {"path": path, "period": seen["период"], "views": seen["просмотры"], "how": how}
        try:
            os.remove(path)                      # не тот экран — в альбом не идёт
        except OSError:
            pass
        return None

    if route.get("steps") and start():
        if replay(route["steps"], w, h, keep=gold):
            got = check("по запомненному пути")
            if got:
                route["ok"] = route.get("ok", 0) + 1
                route["last"] = dt.date.today().isoformat()
                routes[net] = route
                _save_routes(routes)
                return got
        _say(log, f"  [путь] {app}: запомненный путь не сработал — ищу заново")

    # Модель уже искала на этой версии приложения и не нашла — не бродить
    # по нему каждый день: у сети может просто не быть статистики (личный
    # аккаунт без панели). Новая версия или неделя прошла — пробуем снова.
    failed = route.get("failed") or {}
    if failed.get("ver") == ver and failed.get("on", "") >= \
            (dt.date.today() - dt.timedelta(days=FAIL_DAYS)).isoformat():
        LAST["why"] = (f"{failed['on']} модель уже не нашла её на этой версии приложения "
                       f"({failed.get('why', '')}) — снова поищу после обновления")
        _say(log, f"  [путь] {app}: {failed['on']} модель уже не нашла статистику на этой версии — "
                  "не ищу до обновления приложения")
        return None

    def give_up(why):
        LAST["why"] = why
        _say(log, f"  [путь] {app}: {why}")
        route["failed"] = {"ver": ver, "on": dt.date.today().isoformat(), "why": why}
        routes[net] = route
        _save_routes(routes)
        return None

    if not start():
        LAST["why"] = "не открыл свой профиль — искать не с чего"
        _say(log, f"  [путь] {app}: не открыл свой профиль — искать не с чего")
        return None
    _say(log, f"  [путь] {app}: маршрут не сработал — модель ищет дорогу к статистике")
    started = time.time()
    steps, why = explore(net, package, w, h, log, keep=gold)
    if abort.requested() or why == "down":
        LAST["why"] = "модель зрения не ответила"
        return None                    # сбой связи — не повод забыть сеть на неделю
    if steps is None:
        return give_up(f"модель не дошла до статистики ({why})")
    got = check("путь нашла модель")
    if not got:
        return give_up("модель сказала «готово», но на снимке не статистика")
    routes[net] = {"steps": steps, "ver": ver, "learned": dt.date.today().isoformat(), "ok": 0}
    _save_routes(routes)
    try:
        os.makedirs(gold, exist_ok=True)
        with open(os.path.join(gold, "route.json"), "w", encoding="utf-8") as f:
            json.dump({"net": net, "ver": ver, "steps": steps, "w": w, "h": h},
                      f, ensure_ascii=False, indent=1)
    except OSError:
        pass
    _say(log, f"  [путь] {app}: нашёл за {len(steps)} шаг(а), {time.time() - started:.0f} с — "
              "запомнил, в следующий раз без модели")
    return got


# ------------------------------------------------------------ выбор дня
#
# Дошла до статистики — но на ней период, открытый по умолчанию (обычно
# «7 дней»: живьём 25.11 модель принесла «18–24 нояб, 104K» вместо вчера).
# Тогда вторая цель на том же экране: выбрать ОДИН день — вчера (или
# позавчера: TikTok досчитывает день с опозданием). Путь к нему запоминается
# отдельно от пути к статистике и повторяется без модели.

DAY_GOAL = ("на ЭТОМ экране статистики выбрать период ровно в ОДИН день — {day} "
            "(«Вчера» / Yesterday, или этот день в календаре: «Другое» / Custom → "
            "день → «Применить»). Цель достигнута, когда вверху показан только этот день")


def pick_day(net, package, snap, accept, day, ver="", log=None):
    """Выбрать на открытой статистике один день. accept(период) -> True,
    если подпись периода — нужный день. {"path", "period", "views", "how"}
    или None (тогда остаётся то, что было)."""
    if not (config.STATS_SELF_NAV and vision.available()[0]):
        return None
    app = GOALS[net][0]
    w, h = adb.screen_size()
    key = net + "|day"
    routes = load_routes()
    route = routes.get(key) or {}
    gold = os.path.join(_golden_dir(net, ver), "день")

    def check(how):
        path = snap()
        if not path:
            return None
        seen = look(path, app, net)
        if seen.get("статистика") and seen.get("просмотры") and accept(seen.get("период", "")):
            return {"path": path, "period": seen["период"], "views": seen["просмотры"], "how": how}
        try:
            os.remove(path)
        except OSError:
            pass
        return None

    if route.get("steps") and replay(route["steps"], w, h, keep=gold):
        got = check("день выбран по запомненному пути")
        if got:
            return got
    _say(log, f"  [путь] {app}: на статистике не тот период — модель выбирает {day}")
    steps, why = explore(net, package, w, h, log, max_steps=8, keep=gold,
                         goal=DAY_GOAL.format(day=day))
    if steps is None:
        _say(log, f"  [путь] {app}: день выбрать не вышло ({why or 'модель не дошла'})")
        return None
    got = check("день выбрала модель")
    if not got:
        _say(log, f"  [путь] {app}: модель сказала «готово», но на снимке не {day}")
        return None
    routes = load_routes()
    routes[key] = {"steps": steps, "ver": ver, "learned": dt.date.today().isoformat(), "ok": 0}
    _save_routes(routes)
    _say(log, f"  [путь] {app}: день выбран за {len(steps)} шаг(а) — запомнил")
    return got
