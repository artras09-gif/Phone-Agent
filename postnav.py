# -*- coding: utf-8 -*-
"""Публикация: модель ведёт к кнопке шага, когда маршрут её не нашёл.

Маршрут публикации (`recipes.json`) — шаги «главная → «+» → галерея →
ролик → Далее → описание → Опубликовать». Приложение обновилось и
показало другой экран — шаг не находит кнопку. Раньше модель тут только
«расчищала экран» и без цели могла уйти не туда (21.11 YouTube: «Открыть
редактор» → «Другие инструменты» → «Закрыть»).

Теперь, как в статистике (`statnav.py`), две ступени:

1. **Запомненный путь** — что модель нажимала в прошлый раз, чтобы от этого
   же места дойти до кнопки этого шага. Повторяется без модели.
2. **Модель с целью**: «нужна кнопка X» + список кнопок экрана → нажать,
   посмотреть, снова. Дошли — путь запоминается.

Только до экрана описания (`poster` зовёт это для шагов до ввода текста).
В список для модели не попадает ничего опасного: публикация, черновики,
удаление, поля текста, верхняя панель окна поста (`escape.post_menu`) и
сообщения, переключение аккаунта, истории, эфир (`statnav.NEVER`).
Финальную кнопку модель не нажимает никогда — её нажимает только маршрут.
"""
import datetime as dt
import json
import os
import time

import abort
import adb
import config
import device
import escape
import human
import statnav
import ui
import vision

ASK = """Снимок экрана телефона, приложение {app}. Мы публикуем видео: нужно дойти до
экрана, где есть {target}.
Кнопки на экране:
{menu}{tried}
Ответь ТОЛЬКО JSON без пояснений:
{{"кнопка": номер кнопки из списка, которая ведёт к цели, или 0,{point}
 "иначе": "back" | "swipe" | "wait" — если ни одна кнопка не ведёт к цели}}"""

# «Руки» — как в статистике (`statnav.NAV_POINT`): на экране-веб-странице
# кнопок в дереве нет, и выбрать из списка нечего (25.11, TikTok Studio).
POINT = """
 "точка": {{"x": число, "y": число, "что": "что это"}} — только если нужный элемент ВИДЕН
   на снимке, но его НЕТ в списке кнопок; координаты в пикселях снимка {iw}x{ih},"""


def _say(log, line):
    if log is not None:
        log.append(line)
    else:
        print(line, flush=True)


def routes_path():
    return os.path.join(statnav._root(), "post_routes.json")


def load_routes():
    try:
        with open(routes_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_routes(data):
    os.makedirs(statnav._root(), exist_ok=True)
    tmp = routes_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, routes_path())


def golden_dir(target, ver):
    """Эталоны пути: деревья экранов каждого шага — `post_routes/<сеть>/<версия>/`."""
    return os.path.join(statnav._root(), "post_routes", target,
                        os.path.basename(statnav._golden_dir(target, ver)))


# Запреты пути к экрану описания. Общие (`statnav.NEVER`, `escape.POST_NEVER`)
# здесь не годятся целиком: они закрывают «Создать», «Видео», «Добавить из
# галереи», «Далее» — а это и есть дорога к посту. Закрыто то, что делает
# необратимое или уводит из поста: публикация, загрузка, отправка, черновики,
# удаление, оплата, разрешения, сообщения, смена аккаунта, истории, эфир.
ROUTE_WORDS = ("создать", "create")
NEVER = [w for w in statnav.NEVER if w not in ROUTE_WORDS] + [
    "post", "опубликов", "publish", "share", "поделит", "отправ", "send",
    "draft", "черновик", "discard", "delete", "удал", "schedule", "расписан",
    "boost", "pay", "оплат", "allow", "разреш",
]


def forbidden(label):
    low = (label or "").lower().strip()
    if any(low == d.lower() for d in ui.DISMISS_LABELS):
        return False
    return any(bad in low for bad in NEVER)


def menu(nodes, w, h, limit=12, final=()):
    """Кнопки, которые модели можно жать по пути к шагу публикации: с
    подписью (безымянный значок не угадать), не поле текста, не переключатель
    и не финальная кнопка маршрута (`final` — её селекторы). Последнее важно:
    у Instagram финальная кнопка экрана подписи подписана «Далее»
    (share_button), а «Далее» по дороге к посту разрешено."""
    banned = {id(n) for sel in final for n in ui.find(nodes or [], **sel)}
    out, seen = [], set()
    for n in nodes or []:
        if id(n) in banned or "share" in n.rid.lower():
            continue
        label = escape._label_of(n)
        if not (n.clickable and n.enabled and label) or n.area <= 0 or n.area > w * h * 0.5:
            continue
        if forbidden(label) or "EditText" in n.cls                 or any(t in n.cls.lower() for t in statnav.TOGGLES):
            continue
        key = (label.lower(), n.bounds)
        if key not in seen:
            seen.add(key)
            out.append(n)
    out.sort(key=lambda n: (escape._rank(n), n.area))
    return out[:limit]


def point_ok(nodes, x, y, w, h, what, final=()):
    """Можно ли жать по точке модели на пути к посту: не системные полосы, не
    запретное в описании, под пальцем — не финальная кнопка маршрута, не
    запретная подпись, не поле текста и не переключатель. (да/нет, почему)."""
    if not (h * 0.04 < y < h * 0.95 and 0 <= x <= w):
        return False, "точка вне приложения"
    if forbidden(what):
        return False, f"«{what}» — запрещено"
    node, _ = escape._node_at(nodes, x, y, w, h)
    if node is None:
        return True, ""
    banned = {id(n) for sel in final for n in ui.find(nodes or [], **sel)}
    label = escape._label_of(node)
    if id(node) in banned or "share" in node.rid.lower():
        return False, "под точкой — финальная кнопка поста"
    if label and forbidden(label):
        return False, f"под точкой «{label}» — запрещено"
    if "EditText" in node.cls or any(t in node.cls.lower() for t in statnav.TOGGLES):
        return False, "под точкой поле текста или переключатель"
    return True, ""


def _version(package):
    try:
        import accstats
        return accstats._app_version(package)
    except Exception:
        return ""


def reach(target, package, label, selectors, goal, app="", log=None, final=()):
    """Дойти от текущего экрана до кнопки шага (`selectors`). Узел кнопки
    или None. Нажимает только путь к ней — саму кнопку жмёт маршрут."""
    if not (config.POST_NAV and package):
        return None

    def found():
        return ui.find_any(ui.dump(retries=1, tolerant=True, timeout=6), selectors)

    key = f"{target}|{label}"
    routes = load_routes()
    route = routes.get(key) or {}
    w, h = adb.screen_size()
    ver = _version(package)
    gold = golden_dir(target, ver)

    if route.get("steps"):
        if statnav.replay(route["steps"], w, h, keep=gold):
            node = found()
            if node is not None:
                route["ok"] = route.get("ok", 0) + 1
                route["last"] = dt.date.today().isoformat()
                routes[key] = route
                _save_routes(routes)
                _say(log, f"  путь: «{label}» — по запомненному пути, без модели")
                return node
        _say(log, f"  путь: «{label}» — запомненный путь не подошёл, ищу заново")

    if not vision.available()[0]:
        return None
    _say(log, f"  путь: «{label}» не нашёлся — модель ищет дорогу к нему")
    steps, history = [], []
    tapped = {}                # экран -> что на нём уже жали (не ходить по кругу)
    for n in range(1, config.POST_NAV_STEPS + 1):
        if abort.requested():
            return None
        if adb.current_app()[0] != package:
            device.back()
            human.pause(1, 1.5)
            if adb.current_app()[0] != package:
                _say(log, "  путь: вышли из приложения — остановился")
                return None
            if steps:
                steps.pop()
            history.append("последняя кнопка уводила из приложения")
            continue
        nodes = ui.dump(retries=1, tolerant=True)
        node = ui.find_any(nodes, selectors)
        if node is not None:
            routes[key] = {"steps": steps, "ver": ver, "learned": dt.date.today().isoformat(),
                           "ok": 0}
            _save_routes(routes)
            try:
                os.makedirs(gold, exist_ok=True)
                with open(os.path.join(gold, "route.json"), "w", encoding="utf-8") as f:
                    json.dump({"net": target, "ver": ver, "step": label, "steps": steps,
                               "w": w, "h": h}, f, ensure_ascii=False, indent=1)
            except OSError:
                pass
            _say(log, f"  путь: «{label}» нашёлся за {len(steps)} нажат. — запомнил")
            return node
        png, iw, ih = escape._full_shot()
        if not png:
            human.pause(1, 2)
            continue
        statnav._keep_tree(gold, f"шаг{n}", nodes, w, h)
        done_here = tapped.setdefault(statnav._screen_key(nodes), set())
        items = [it for it in menu(nodes, w, h, final=final)
                 if escape._label_of(it) + str(it.bounds) not in done_here]
        if done_here:
            history.append("на этом экране уже пробовали: " + ", ".join(
                sorted(x.split("(")[0] or "значок" for x in done_here))[:120])
        hands = escape.hands_ready(iw, ih) if config.ESCAPE_HANDS else None
        tried = ("\nУже пробовали: " + "; ".join(history[-5:]) + ".") if history else ""
        try:
            raw = vision.ask(png, ASK.format(app=app or package, target=goal,
                                             point=POINT.format(iw=iw, ih=ih) if hands else "",
                                             menu=escape._describe_menu(items, w, h), tried=tried),
                             max_tokens=escape.ANSWER_TOKENS, temperature=0.1, think=True,
                             shrink=1 if hands else None)
        except Exception as e:
            _say(log, f"  путь: модель не ответила — {str(e)[:80]}")
            return None
        answer = vision._json_from(raw or "")
        answer = answer if isinstance(answer, dict) else {}
        try:
            button = int(answer.get("кнопка") or 0)
        except (TypeError, ValueError):
            button = 0
        if 1 <= button <= len(items):
            pick = items[button - 1]
            done_here.add(escape._label_of(pick) + str(pick.bounds))
            sig = statnav.signature(pick, w, h)
            sig["tree"] = f"шаг{n}"
            steps.append(sig)
            history.append(f"нажали {escape._name_of(pick)}")
            _say(log, f"  путь: нажимаю {escape._name_of(pick)}")
            ui.tap_node(pick)
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
                fine, why = point_ok(nodes, x, y, w, h, what, final)
                if not fine:
                    history.append(f"точку «{what}» не нажал: {why}")
                    continue
                done_here.add(f"точка {what}({x // 40},{y // 40})")
                steps.append({"point": [round(x / w, 3), round(y / h, 3)], "что": what,
                              "tree": f"шаг{n}"})
                history.append(f"нажали точку «{what}»")
                _say(log, f"  путь: жму по точке «{what}»")
                adb.tap(x, y)
                human.pause(1.8, 2.8)
                continue
        other = str(answer.get("иначе") or "").lower().strip()
        if other in ("back", "назад"):
            device.back()
            human.pause(1, 1.6)
            if steps:
                steps.pop()
            history.append("назад")
        elif other in ("swipe", "свайп"):
            steps.append({"do": "swipe"})
            statnav._do({"do": "swipe"}, w, h)
            history.append("пролистали")
        elif other in ("wait", "ждать"):
            human.pause(1.5, 2.5)
            history.append("подождали")
        else:
            history.append("непонятный ответ")
        time.sleep(0.2)
    _say(log, f"  путь: за {config.POST_NAV_STEPS} шагов «{label}» не нашёлся")
    return None
