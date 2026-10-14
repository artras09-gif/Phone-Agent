"""Исполнитель маршрутов публикации из recipes.json."""
import json
import os
import re
import struct
import time

import abort
import adb
import config
import device
import human
import runlog
import ui
import vision


class StepFailed(RuntimeError):
    pass


def load_recipes():
    with open(config.RECIPES, encoding="utf-8") as f:
        return json.load(f)


def known_packages(recipes):
    """Все приложения, которые агент вообще открывает.

    Нужны, чтобы прибить их перед публикацией: чужое окно «картинка в
    картинке» перекрывает кнопки и ест тапы.
    """
    found = set()
    for value in recipes.values():
        if isinstance(value, dict) and value.get("package"):
            found.add(value["package"])
    for value in (recipes.get("_feed_apps") or {}).values():
        if isinstance(value, dict) and value.get("package"):
            found.add(value["package"])
    return sorted(found)


def _log_dir(tag):
    """Папка со скриншотами шагов публикации.

    Серийник в имени обязателен, когда телефонов несколько: `logs/` общая на
    всех, а имя складывалось из времени и площадки. Два телефона, начавшие
    публикацию в одну и ту же секунду, писали бы шаги В ОДНУ папку поверх
    друг друга (`exist_ok=True`, ошибки нет) — и разбирать сломанный маршрут
    пришлось бы по перемешанным экранам двух аппаратов.
    """
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    name = f"{stamp}_{tag}"
    if config.SERIAL:
        import devices

        name += "_" + devices.slug(config.SERIAL)
    path = os.path.join(config.LOG_DIR, name)
    os.makedirs(path, exist_ok=True)
    return path


def _snap(log_path, index, name):
    try:
        adb.screenshot(os.path.join(log_path, f"{index:02d}_{name}.png"))
    except adb.AdbError:
        pass


def _drop_snaps(log_path):
    """Убрать снимки шагов удачного маршрута, оставив текстовый отчёт."""
    try:
        names = os.listdir(log_path)
    except OSError:
        return
    for name in names:
        if name.lower().endswith(".png"):
            try:
                os.remove(os.path.join(log_path, name))
            except OSError:
                pass




def _vision_note(log_path, index, label):
    """Спросить у зрения, что за экран нас застопорил.

    Только описание в лог: в маршруте публикации агент не жмёт ничего
    по подсказке модели, никогда. Ошибка здесь
    стоит опубликованного не туда видео, а дерево экрана всё равно
    сохраняется рядом — по нему и правятся селекторы.
    """
    if not config.VISION_ENABLED:
        return ""
    try:
        png = adb.exec_out("screencap -p", timeout=25)
        hint = vision.rescue(png, goal=label, screen_size=adb.screen_size())
    except (adb.AdbError, vision.VisionError) as e:
        return f"\nзрение не помогло: {str(e)[:120]}"

    text = (f"экран: {hint.get('экран', '?')}\n"
            f"мешает: {hint.get('мешает', '?')}\n"
            f"модель предлагает: {hint.get('действие')} "
            f"({hint.get('почему', '')})")
    with open(os.path.join(log_path, f"{index:02d}_vision.txt"), "w",
              encoding="utf-8") as f:
        f.write(text)
    return "\n" + text


def run_step(step, ctx, log_path, index):
    """Выполнить один шаг маршрута. Возвращает описание для лога."""
    if abort.requested():
        # Между шагами — единственное безопасное место, где можно бросить
        # публикацию: посреди тапа приложение осталось бы на полпути.
        raise StepFailed("остановлено")

    op = step["op"]
    selectors = step.get("any", [])
    timeout = step.get("timeout", 20)
    label = step.get("desc", op)

    if op == "sleep":
        human.pause(step.get("min", 1), step.get("max", 3))
        return f"пауза ({label})"

    if op == "back":
        device.back()
        return f"назад ({label})"

    if op == "hide_keyboard":
        return ("клавиатура убрана" if device.hide_keyboard()
                else "клавиатуры на экране нет")

    if op == "tap_xy":
        # Доля экрана, а не пиксели: у кнопки нет ни текста, ни id (миниатюра
        # галереи в камере Instagram), но место у неё постоянное.
        w, h = adb.screen_size()
        x, y = int(w * step["x"]), int(h * step["y"])
        adb.tap(x, y)
        human.pause(1.0, 2.0)
        return f"тап по месту {x},{y} ({label})"

    if op == "tap_first":
        # Первое совпадение В ПОРЯДКЕ ДЕРЕВА, а не самое крупное: в галерее
        # так выбирается свежее видео — оно идёт первым.
        node, nodes = ui.wait_any(selectors, timeout=timeout)
        first = None
        for sel in selectors:
            hits = [n for n in ui.find(nodes, **sel) if n.area > 0]
            if hits:
                first = hits[0]
                break
        if first is None:
            _snap(log_path, index, "STUCK")
            raise StepFailed(f"не нашёл ни одного «{label}»")
        ui.tap_node(first)
        human.pause(1.0, 2.0)
        return f"выбрал первое «{label}» -> {first}"

    if op == "dismiss":
        return "закрыто окно" if ui.dismiss_popup() else "закрывать нечего"

    if op == "permissions":
        # Окна разрешений идут по одному (микрофон, потом камера): закрываем
        # все, что всплывут за отведённое время.
        closed, deadline = 0, time.time() + step.get("timeout", 6)
        while time.time() < deadline and closed < 3:
            if ui.handle_permission(strict=True):
                closed += 1
                deadline = time.time() + 3
            else:
                time.sleep(0.6)
        return (f"окон разрешений закрыто отказом: {closed}" if closed
                else "окон разрешений нет")

    if op == "advance_until":
        return _advance_until(step, ctx, log_path, index)

    if op == "reel_gallery":
        return _reel_gallery(step, ctx, log_path, index)

    if op in ("tap", "optional_tap", "wait", "publish"):
        node, nodes = ui.wait_any(selectors, timeout=timeout)

        if node is None:
            # Классика: маршрут перекрыт внезапным окном. Пробуем убрать и повторить.
            if ui.dismiss_popup(nodes):
                node, nodes = ui.wait_any(selectors, timeout=max(6, timeout // 2))

        if node is None:
            if op == "optional_tap":
                return f"пропущено, элемента нет ({label})"
            _snap(log_path, index, "STUCK")
            with open(os.path.join(log_path, f"{index:02d}_screen.txt"),
                      "w", encoding="utf-8") as f:
                f.write(ui.describe(nodes, limit=80))
            raise StepFailed(
                f"не нашёл элемент для «{label}». "
                f"Дерево экрана сохранено в {log_path} — поправь селектор в recipes.json"
                + _vision_note(log_path, index, label)
            )

        if op == "wait":
            return f"дождались «{label}» -> {node}"

        if op == "publish" and config.DRY_RUN:
            _snap(log_path, index, "DRYRUN_publish")
            return f"СУХОЙ ПРОГОН: не нажимаю «{label}» -> {node}"

        ui.tap_node(node)
        human.pause(0.8, 1.8)
        return f"тап «{label}» -> {node}"

    if op == "type":
        value = step.get("value", "")
        if value == "$caption":
            value = ctx.get("caption", "")
        if not value:
            return "текста нет, пропускаю"
        device.type_text(value)
        return f"введён текст ({len(value)} символов)"

    raise StepFailed(f"неизвестная операция: {op}")


# --------------------------------------------- Instagram: к галерее Reels

def _is_reel_gallery(nodes, gallery):
    """Галерея именно Reels (а не «Новая публикация» или «История»)."""
    for n in nodes:
        label = (n.text or n.desc).lower()
        if n.rid.endswith("gallery_title_text") and "reel" in label:
            return True
    return ui.find_any(nodes, [s for s in gallery if "rid" not in s]) is not None


def _exact(nodes, words):
    """Кликабельный узел, чья подпись (desc или text) — РОВНО одно из слов.

    Не вхождением: «Create» как кусок «Create story» или «Add to story» увёл
    бы в историю."""
    wanted = [w.lower() for w in words]
    for word in wanted:
        for n in nodes:
            if n.clickable and n.area > 0 and (n.desc or n.text).strip().lower() == word:
                return n
    return None


def _reel_gallery(step, ctx, log_path, index):
    """Открыть галерею «New reel» — по возможности БЕЗ камеры.

    Просьба 2026-10-14: «можно другим путём, чтобы он не открывал камеру»
    (на новом телефоне открывалась фронталка). Путь 1 — «+» («Создать») на
    главной: открывает галерею, камера не нужна. Путь 2 (запасной, как
    раньше) — камера из вкладки Reels и миниатюра галереи; на новом телефоне
    миниатюра стоит в одном ряду с переключателем STORY/REEL, поэтому место
    считается от этого ряда, а старое место — последним.
    """
    package = step.get("package", "com.instagram.android")
    gallery = step["gallery"]
    reel_tab = step.get("reel_tab", [])
    tried = []

    def settle(seconds=6):
        """Дерево экрана, закрыв окна разрешений и «черновик с прошлого раза»."""
        end, nodes = time.time() + seconds, []
        while time.time() < end:
            ui.handle_permission(strict=True)
            nodes = ui.dump(retries=1, tolerant=True)
            draft = ui.find_any(nodes, step.get("new_draft", []))
            if draft is not None:
                ui.tap_node(draft)
                human.pause(1.0, 1.6)
                continue
            if _is_reel_gallery(nodes, gallery):
                return nodes, True
            time.sleep(0.7)
        return nodes, False

    def from_camera(nodes):
        """Камера Reels открыта: режим REEL и миниатюра галереи."""
        tab = ui.find_any(nodes, reel_tab)
        if tab is not None and not tab.selected:
            ui.tap_node(tab)
            human.pause(1.0, 1.6)
            nodes = ui.dump(retries=1, tolerant=True) or nodes
            tab = ui.find_any(nodes, reel_tab) or tab
        w, h = adb.screen_size()
        spots = []
        if tab is not None:
            spots.append((int(w * step.get("thumb_x", 0.1)), tab.center[1], "в ряду REEL"))
        tx, ty = step.get("thumb_xy", (0.09, 0.885))
        spots.append((int(w * tx), int(h * ty), "старое место"))
        for x, y, where in spots:
            adb.tap(x, y)
            human.pause(1.5, 2.2)
            nodes, ok = settle(8)
            tried.append(f"миниатюра {where} {x},{y}")
            if ok:
                return nodes, True
        return nodes, False

    # Путь 1: «+» на главной.
    device.stop_app(package)
    human.pause(0.8, 1.4)
    device.open_app(package)
    nodes, ok = [], False
    end = time.time() + 15
    create = None
    while time.time() < end and create is None:
        ui.handle_permission(strict=True)
        nodes = ui.dump(retries=1, tolerant=True)
        create = _exact(nodes, step.get("create_words", []))
        if create is None:
            time.sleep(0.8)
    if create is not None:
        ui.tap_node(create)
        human.pause(1.5, 2.5)
        tried.append("«+» на главной")
        nodes, ok = settle(6)
        if not ok and ui.find_any(nodes, reel_tab) is not None:
            tab = ui.find_any(nodes, reel_tab)
            ui.tap_node(tab)
            human.pause(1.5, 2.2)
            tried.append("вкладка REEL")
            nodes, ok = settle(8)
            if not ok and ui.find_any(nodes, reel_tab) is not None:
                nodes, ok = from_camera(nodes)       # REEL открыл камеру
        if ok:
            return "галерея Reels: " + " -> ".join(tried)

    # Путь 2: камера из вкладки Reels (как раньше).
    device.open_uri(step.get("reels_uri", "instagram://reels_home"), package)
    human.pause(3.0, 5.0)
    cx, cy = step.get("camera_xy", (0.061, 0.071))
    w, h = adb.screen_size()
    adb.tap(int(w * cx), int(h * cy))
    human.pause(3.0, 4.5)
    tried.append("камера из Reels")
    nodes, ok = settle(6)
    if not ok:
        nodes, ok = from_camera(nodes)
    if ok:
        return "галерея Reels: " + " -> ".join(tried)
    _snap(log_path, index, "STUCK")
    with open(os.path.join(log_path, f"{index:02d}_screen.txt"), "w", encoding="utf-8") as f:
        f.write(ui.describe(nodes, limit=80))
    raise StepFailed("не открылась галерея Reels (пробовал: " + "; ".join(tried)
                     + f"). Дерево экрана сохранено в {log_path}"
                     + _vision_note(log_path, index, "галерея Reels"))


# ---------------------------------------------- промежуточные экраны

# Длина ролика на кнопке выбора длительности: «15s», «60s», «3m», «1 мин».
RE_LENGTH = re.compile(r"^\s*(\d+)\s*(s|с|сек\.?|m|м|мин\.?|min)\s*$", re.I)
# Такие кнопки не нажимаются никогда, даже если совпали с «вперёд».
NEVER_ADVANCE = ("upload", "загруз", "опубликов", "publish", "post", "поделиться", "share")


def _length_of(text):
    m = RE_LENGTH.match(str(text or ""))
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    return n * 60 if unit.startswith(("m", "м")) else n


def mp4_seconds(path):
    """Длина mp4/mov по заголовку (атом mvhd). None — не разобрать.

    Своими силами, без ffprobe: на чужом ПК его нет.
    """
    try:
        with open(path, "rb") as f:
            data = f.read(8 * 1024 * 1024)
            at = data.find(b"mvhd")
            if at < 0:
                # moov бывает в конце файла — смотрим хвост.
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 8 * 1024 * 1024))
                data = f.read()
                at = data.find(b"mvhd")
            if at < 0:
                return None
        version = data[at + 4]
        if version == 1:
            scale, length = struct.unpack(">IQ", data[at + 24:at + 36])
        else:
            scale, length = struct.unpack(">II", data[at + 16:at + 24])
        return length / scale if scale else None
    except (OSError, struct.error, IndexError):
        return None


def _length_chip(nodes):
    """Кнопка длины («15s») — только в верхней четверти экрана: внизу на
    полосе обрезки есть свои подписи времени («6.1s»), их трогать нельзя."""
    height = max((n.bounds[3] for n in nodes), default=0) or 1
    for n in nodes:
        if n.area > 0 and _length_of(n.text or n.desc) and n.center[1] < height * 0.25:
            return n
    return None


def _set_length(nodes, ctx, notes):
    """Экран обрезки YouTube: кнопка длины («15s») — дотянуть до длины видео.

    Если нажать «Done» на «15s», YouTube обрежет ролик до 15 секунд. Жмём
    кнопку длины, пока на ней не станет не меньше длины видео (или пока она
    не перестанет расти — дальше максимум сети).
    """
    want = ctx.get("seconds")
    if want is None and ctx.get("video"):
        want = ctx["seconds"] = mp4_seconds(ctx["video"])
    chip = _length_chip(nodes)
    if chip is None or want is None:
        return nodes
    seen = []
    for _ in range(4):
        have = _length_of(chip.text or chip.desc)
        if have >= want - 0.5:
            notes.append(f"длина {have} с — видео {want:.0f} с влезает целиком")
            return nodes
        if have in seen:
            break                       # пошла по кругу — больше не бывает
        seen.append(have)
        ui.tap_node(chip)
        human.pause(0.8, 1.4)
        nodes = ui.dump(retries=2, tolerant=True)
        chip = _length_chip(nodes)
        if chip is None:
            return nodes
    best = max(seen + [_length_of(chip.text or chip.desc) or 0])
    notes.append(f"[!] видео {want:.0f} с длиннее, чем YouTube даёт ({best} с) — обрежется")
    # Ставим максимум, который видели: если ушли по кругу на меньшее — докручиваем.
    for _ in range(4):
        if (_length_of(chip.text or chip.desc) or 0) >= best:
            break
        ui.tap_node(chip)
        human.pause(0.8, 1.4)
        nodes = ui.dump(retries=2, tolerant=True)
        chip = _length_chip(nodes)
        if chip is None:
            break
    return nodes


# Где кнопка «вперёд», если дерево экрана не снимается (под играющим видео
# uiautomator молчит). Только для кнопок перехода — «Готово»/«Далее»; что
# модель назвала, сверяется со списком, публикующие отбрасываются.
ASK_BUTTON_AT = """Снимок экрана телефона, {w}x{h} пикселей. Есть ли на нём кнопка
с надписью {labels}? Если есть — ответь {{"x": число, "y": число, "надпись":
"как написано на кнопке"}}: центр кнопки в пикселях этого снимка. Нет такой
кнопки — {{"нет": true}}."""

ASK_LENGTH_AT = """Снимок экрана телефона, {w}x{h} пикселей. Вверху экрана есть
кнопка выбора длительности ролика с надписью вроде «15s», «60s» или «3m»?
Если есть — ответь {{"x": число, "y": число, "надпись": "как написано"}}:
центр кнопки в пикселях этого снимка. Нет — {{"нет": true}}."""


def _vision_point(prompt):
    """(x, y на экране, надпись) по снимку или None. Меткость — как у рук."""
    import escape

    png, iw, ih = escape._full_shot()
    if png is None:
        return None
    way = escape.hands_ready(iw, ih)
    if way is None:
        return None
    try:
        raw = vision.ask(png, prompt.format(w=iw, h=ih), system=escape.SYSTEM,
                         max_tokens=escape.ANSWER_TOKENS, temperature=0.1, shrink=1,
                         think=escape.THINK)
    except vision.VisionError:
        return None
    data = vision._json_from(raw)
    if isinstance(data, dict) and data.get("нет") in (True, "true", "да"):
        return None
    got = escape._point_of(raw)
    if not got:
        return None
    label = str((data or {}).get("надпись", "") if isinstance(data, dict) else "").strip()
    x, y = escape._to_image(got[0], got[1], way, iw, ih)
    w, h = adb.screen_size()
    return int(x * w / iw), int(y * h / ih), label


def _advance_by_sight(advance, ctx, notes):
    """Нажать «вперёд», найдя её по снимку. Надпись (или None, если не нашли)."""
    words = []
    for sel in advance:
        word = sel.get("text") or sel.get("desc")
        if word and word not in words:
            words.append(word)
    hit = _vision_point(ASK_BUTTON_AT.replace("{labels}", " или ".join(f"«{w}»" for w in words)))
    if hit is None:
        return None
    x, y, label = hit
    low = label.lower()
    if not label or any(w in low for w in NEVER_ADVANCE) \
            or not any(w.lower() in low for w in words):
        notes.append(f"по снимку модель назвала «{label}» — не жму")
        return None
    if low in ("done", "готово") and ctx.get("video"):
        _length_by_sight(ctx, notes)
    adb.tap(x, y)
    return label


def _length_by_sight(ctx, notes):
    """Кнопка длины по снимку: дотянуть до длины видео, как `_set_length`."""
    want = ctx.get("seconds")
    if want is None:
        want = ctx["seconds"] = mp4_seconds(ctx["video"])
    if want is None:
        return
    seen = []
    for _ in range(4):
        hit = _vision_point(ASK_LENGTH_AT)
        have = _length_of(hit[2]) if hit else None
        if have is None:
            notes.append("кнопку длины по снимку не нашёл — длину не проверить")
            return
        if have >= want - 0.5:
            notes.append(f"длина {have} с (по снимку) — видео {want:.0f} с влезает")
            return
        if have in seen:
            notes.append(f"[!] видео {want:.0f} с длиннее, чем YouTube даёт ({max(seen)} с)")
            return
        seen.append(have)
        adb.tap(hit[0], hit[1])
        human.pause(1.0, 1.6)


def _advance_until(step, ctx, log_path, index):
    """Пройти промежуточные экраны («Done», «Next»), пока не появится цель.

    Нужно YouTube: по одним версиям он открывает сразу экран названия, по
    другим — сначала обрезку («Drag to adjust video» / «Done»), потом
    редактор («Next»), и только потом название (живой сбой 2026-10-14). Порядок
    и набор экранов от версии к версии разный, поэтому не шаги подряд, а
    круг: цель есть — готово; есть кнопка «вперёд» — жмём; нет — ждём.
    """
    targets = step.get("any", [])
    advance = step.get("advance", [])
    deadline = time.time() + step.get("timeout", 90)
    label = step.get("desc", "advance_until")
    done, notes, nodes = [], [], []
    last_tap = None
    started = time.time()
    sight_at = 0.0
    while time.time() < deadline:
        if abort.requested():
            raise StepFailed("остановлено")
        nodes = ui.dump(retries=1, tolerant=True)
        if ui.find_any(nodes, targets) is not None:
            tail = ("; " + "; ".join(done)) if done else ""
            return f"дошли до «{label}»{tail}" + ("".join("\n     " + n for n in notes))
        node = None
        for sel in advance:
            hits = [n for n in ui.find(nodes, **sel) if n.area > 0
                    and not any(w in (n.text + " " + n.desc).lower() for w in NEVER_ADVANCE)]
            if hits:
                node = hits[-1]          # нижняя — кнопка, а не заголовок
                break
        if node is None:
            # Ни цели, ни кнопки в дереве дольше `vision_after` секунд — экран
            # не читается (видео играет) или подписи другие. Ищем по снимку.
            waited = time.time() - started
            if step.get("vision_after") and waited > step["vision_after"] \
                    and time.time() - sight_at > 8:
                sight_at = time.time()
                pressed = _advance_by_sight(advance, ctx, notes)
                if pressed:
                    done.append(f"«{pressed}» (по снимку)")
                    human.pause(1.5, 2.5)
                    continue
            time.sleep(0.8)
            continue
        if step.get("length_chip"):
            nodes = _set_length(nodes, ctx, notes)
            again = [n for sel in advance for n in ui.find(nodes, **sel) if n.area > 0]
            node = again[-1] if again else node
        name = (node.text or node.desc or "?")[:30]
        # Тот же экран и та же кнопка, а цель не появилась — даём экрану время.
        if last_tap == (name, node.bounds) and time.time() - last_tap_at < 4:
            time.sleep(1.0)
            continue
        ui.tap_node(node)
        last_tap, last_tap_at = (name, node.bounds), time.time()
        done.append(f"«{name}»")
        human.pause(1.5, 2.5)
    _snap(log_path, index, "STUCK")
    with open(os.path.join(log_path, f"{index:02d}_screen.txt"), "w", encoding="utf-8") as f:
        f.write(ui.describe(nodes, limit=80))
    raise StepFailed(
        f"не дошёл до «{label}» (нажато: {', '.join(done) or 'ничего'}"
        + (f"; дерево экрана {'пустое' if not nodes else 'без нужных кнопок'}") + "). "
        + ("; ".join(notes) + ". " if notes else "")
        + f"Дерево экрана сохранено в {log_path}"
        + _vision_note(log_path, index, label))


# Папка разбора последней НЕУДАЧНОЙ публикации — для отчёта в Telegram.
LAST_FAIL = {"dir": "", "target": ""}


def failure_files(log_path):
    """(снимок экрана в байтах или None, текст «что на экране») из папки сбоя."""
    shot, screen = None, ""
    try:
        names = sorted(os.listdir(log_path))
    except OSError:
        return None, ""
    for name in reversed(names):
        path = os.path.join(log_path, name)
        if shot is None and name.lower().endswith(".png"):
            try:
                with open(path, "rb") as f:
                    shot = f.read()
            except OSError:
                pass
        if not screen and name.endswith(("_screen.txt", "_vision.txt")):
            try:
                with open(path, encoding="utf-8") as f:
                    screen = f.read()
            except OSError:
                pass
    return shot, screen


def post(video_path, caption, target, recipes=None):
    """Опубликовать видео в одну соцсеть. Возвращает (успех, отчёт)."""
    recipes = recipes or load_recipes()
    if target not in recipes:
        # Через Log, а не голой строкой: иначе при живом журнале сообщение
        # никто не напечатает — вызывающий на него уже не рассчитывает.
        return False, runlog.Log(f"нет маршрута «{target}» в recipes.json").text()

    recipe = recipes[target]
    package = recipe["package"]
    log_path = _log_dir(target)
    report = runlog.Log(f"=== {recipe.get('title', target)} : "
                        f"{os.path.basename(video_path)}")
    if config.DRY_RUN:
        report.append("режим: СУХОЙ ПРОГОН (финальная кнопка не нажимается)")

    remote = None
    published = False
    opened = False
    try:
        if not device.unlock():
            report.append("не смог разблокировать телефон")
            return False, report.text()
        device.keep_awake(True)
        # Соседние видеоприложения умеют висеть поверх «картинкой в картинке»
        # и перехватывать тапы. Убираем их до начала маршрута.
        device.close_overlays(known_packages(recipes), keep=package)

        remote = device.push_video(video_path)
        report.append(f"залито: {remote}")

        if recipe.get("open_uri"):
            # Instagram не принимает видео Intent'ом вовсе (проверено: и
            # content://, и file://, и явная активность — открывается главная
            # лента). Поэтому туда заходим как человек: открываем ленту и
            # дальше по шагам берём видео из галереи.
            device.open_uri(recipe["open_uri"], package)
        else:
            # Вид адреса задан в рецепте: единого, который принимают все, нет.
            # YouTube не открывается по content:// вовсе, Instagram игнорирует
            # file:// — оба случая пойманы живьём. Гадать в бою нельзя:
            # неудачная попытка оставляет приложение в странном состоянии.
            device.share_video(
                remote, package, caption,
                component=recipe.get("share_component"),
                use_content=recipe.get("share_uri", "content") != "file")

        opened = True
        if not device.wait_for_app(package, timeout=30):
            raise StepFailed(f"{package} не открылся")
        report.append("приложение открыто на экране публикации")

        ctx = {"caption": caption, "video": video_path}
        for i, step in enumerate(recipe["steps"], start=1):
            line = run_step(step, ctx, log_path, i)
            report.append(f"  {i:2d}. {line}")

        published = not config.DRY_RUN
        report.append("готово")
        # Маршрут прошёл — снимки шагов больше не нужны: они для разбора
        # СБОЯ, а сбоя не было. Остаётся текстовый отчёт. Иначе каждая
        # публикация оставляла по десятку снимков экрана на неделю.
        _drop_snaps(log_path)
        return True, report.text()

    except (StepFailed, adb.AdbError, RuntimeError) as e:
        # NeedsPermission — тоже RuntimeError: текст для человека готовый.
        report.append(f"ОШИБКА: {e}")
        LAST_FAIL.update(dir=log_path, target=target)
        _snap(log_path, 99, "FAIL")
        return False, report.text()

    finally:
        # Кнопку «Опубликовать» не нажали — сухой прогон или сбой посреди
        # маршрута — значит приложение стоит на экране публикации с набранной
        # подписью и ЖИВОЙ кнопкой. Поймано 2026-09-29: после сухого прогона
        # личный Instagram так и висел на «Новое видео Reels» с кнопкой
        # «Поделиться», телефон не заблокирован. Одно случайное касание —
        # и ролик в личном аккаунте. Закрываем процесс целиком: это не
        # нажимает в приложении ничего (в отличие от «назад», за которым у
        # Instagram идёт окно «Сохранить черновик / Удалить»), а
        # недоделанный ролик при этом не сохраняется.
        if opened and not published:
            try:
                device.stop_app(package)
                report.append("приложение закрыто: экран публикации не оставлен")
            except adb.AdbError:
                report.append("[!] не смог закрыть приложение — "
                              "экран публикации мог остаться открытым")
        device.keep_awake(False)
        if remote:
            device.remove_remote(remote)
        with open(os.path.join(log_path, "report.txt"), "w", encoding="utf-8") as f:
            f.write(report.text())
