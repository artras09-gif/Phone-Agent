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

    if op == "yt_audience":
        return _yt_audience(step, log_path, index)

    if op == "publish_more":
        # Instagram 451: кнопка экрана подписи — «Далее» (share_button), за
        # ней окно «О Reels» со СВОЕЙ кнопкой «Поделиться» (живьём 2026-10-16:
        # пост не уходил — окно лежит ПОВЕРХ экрана подписи, и прежняя защита
        # «экран подписи на месте — второй раз не жму» срабатывала на нём и
        # отчитывалась «готово», оставив открытую живую кнопку).
        #
        # Теперь: жмём только кнопку, которая НЕ share_button (не та же самая,
        # что уже нажата, — так двойного поста не бывает), и ждём, что экран
        # подписи уйдёт. Нет ни окна, ни ухода — честная ошибка.
        if config.DRY_RUN:
            return f"СУХОЙ ПРОГОН: второй экран публикации не проверяю ({label})"
        unless = step.get("unless", [])
        first = step.get("first_rid", "share_button")
        human.pause(2.0, 3.0)
        end = time.time() + timeout
        while time.time() < end:
            nodes = ui.dump(retries=1, tolerant=True)
            sheet = [n for sel in selectors for n in ui.find(nodes, **sel)
                     if n.area > 0 and n.enabled and n.rid.split("/")[-1] != first]
            if sheet:
                ui.tap_node(sheet[0])
                human.pause(1.5, 2.5)
                gone = time.time() + step.get("leave_timeout", 20)
                while time.time() < gone:
                    nodes = ui.dump(retries=1, tolerant=True)
                    if nodes and ui.find_any(nodes, unless) is None:
                        return f"второй экран: тап «{sheet[0].text or sheet[0].desc}» — опубликовано"
                    time.sleep(1.0)
                _snap(log_path, index, "STUCK")
                raise StepFailed("нажал «Поделиться» во втором окне, но экран подписи "
                                 "не ушёл — проверь, вышел ли пост")
            if nodes and ui.find_any(nodes, unless) is None:
                return f"второго окна нет — опубликовано первой кнопкой ({label})"
            time.sleep(1.0)
        _snap(log_path, index, "STUCK")
        with open(os.path.join(log_path, f"{index:02d}_screen.txt"), "w", encoding="utf-8") as f:
            f.write(ui.describe(nodes, limit=80))
        raise StepFailed("после финальной кнопки экран подписи остался, а кнопки «Поделиться» "
                         f"не появилось — пост не ушёл. Дерево экрана сохранено в {log_path}"
                         + _vision_note(log_path, index, label))

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

        if op == "publish" and not node.enabled:
            # Серая кнопка: нажатие ничего не сделает, а отчёт сказал бы
            # «готово» (так было с YouTube: аудитория не выбрана, видео не
            # прочитано). Ждём, пока оживёт, — иначе честная ошибка.
            end = time.time() + step.get("enabled_timeout", 30)
            while time.time() < end and not node.enabled:
                time.sleep(1.5)
                nodes = ui.dump(retries=1, tolerant=True)
                node = ui.find_any(nodes, selectors) or node
            if not node.enabled:
                _snap(log_path, index, "STUCK")
                with open(os.path.join(log_path, f"{index:02d}_screen.txt"),
                          "w", encoding="utf-8") as f:
                    f.write(ui.describe(nodes, limit=80))
                raise StepFailed(
                    f"кнопка «{node.text or node.desc}» неактивна — приложение не готово "
                    "публиковать (не выбрана аудитория, видео не прочитано или нет "
                    f"названия). Дерево экрана сохранено в {log_path}"
                    + _vision_note(log_path, index, label))

        if op == "publish" and config.DRY_RUN:
            _snap(log_path, index, "DRYRUN_publish")
            return f"СУХОЙ ПРОГОН: не нажимаю «{label}» (активна) -> {node}"

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


# ------------------------------------------------- YouTube: аудитория

def _full_rows(nodes, w, top=0, bottom=10 ** 6):
    """Кликабельные строки во всю ширину экрана — сверху вниз."""
    rows = [n for n in nodes if n.clickable and n.bounds[0] <= 2 and n.bounds[2] >= w - 2
            and 100 < n.bounds[3] - n.bounds[1] < 360 and top <= n.bounds[1] < bottom]
    return sorted(rows, key=lambda n: n.bounds[1])


def _yt_audience(step, log_path, index):
    """«Это видео предназначено для детей?» — «Видео не для детей».

    Без ответа YouTube держит «Загрузить» серой (живьём 2026-10-16, новый
    канал). Подписей у строк в дереве нет (ни «Доступ», ни «Аудитория», ни
    вариантов) — только кликабельные строки во всю ширину, поэтому по
    порядку: на экране информации под полем названия две строки — «Доступ» и
    «Аудитория»; на экране аудитории первая — «для детей», вторая — «не для
    детей». Канал уже отвечал раньше (кнопка живая) — шаг ничего не делает.
    """
    # Выбираем ВСЕГДА, даже при живой «Загрузить»: в новой раскладке (YouTube
    # 21.40, живьём 2026-10-16) кнопка активна и без аудитории, а что YouTube
    # спросит после нажатия — без настоящей загрузки не узнать. Выбор того же
    # варианта повторно ничего не портит.
    nodes = ui.dump(retries=2, tolerant=True)
    w, _ = adb.screen_size()
    title = ui.find_any(nodes, step.get("title", []))
    expand = ui.find_any(nodes, step.get("expand", []))
    top = title.bounds[3] if title is not None else 0
    bottom = expand.bounds[1] if expand is not None else 10 ** 6
    rows = _full_rows(nodes, w, top, bottom)
    if len(rows) < 2:
        return f"строки «Аудитория» не нашёл (строк под названием: {len(rows)}) — пропускаю"
    ui.tap_node(rows[1])
    human.pause(1.5, 2.5)
    nodes = ui.dump(retries=2, tolerant=True)
    options = _full_rows(nodes, w)
    back = ui.find_any(nodes, step.get("back", []))
    still = ui.find_any(nodes, step.get("upload", [])) is not None
    if len(options) < 2 or back is None or still:
        _snap(log_path, index, "STUCK")
        raise StepFailed("экран «Укажите аудиторию» не открылся или не узнан: вариантов "
                         f"{len(options)}, «Назад» {'есть' if back else 'нет'}")
    ui.tap_node(options[1])                      # «Видео не для детей»
    human.pause(1.0, 1.6)
    ui.tap_node(back)
    human.pause(1.5, 2.5)
    return "аудитория: «Видео не для детей»"


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

    def settle(seconds=15):
        """Дерево экрана, закрыв окна разрешений и «черновик с прошлого раза»."""
        # Одно чтение экрана за круг: на Redmi оно занимает ~4 с, и с двумя
        # чтениями (окна разрешений + сам экран) в 6 с не помещался даже один
        # круг — вопрос о черновике оставался неотвеченным (живьём 2026-10-15).
        end, nodes = time.time() + seconds, []
        while time.time() < end:
            nodes = ui.dump(retries=1, tolerant=True)
            if ui.handle_permission(nodes, strict=True):
                continue
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
            nodes, ok = settle(12)
            tried.append(f"миниатюра {where} {x},{y}")
            if ok:
                return nodes, True
        return nodes, False

    # Путь 1: «+» на главной. В дереве его НЕТ (живьём 2026-10-15, Instagram
    # 451: верхняя панель главной в дерево не попадает, а само дерево под
    # автовоспроизведением ленты снимается через раз) — поэтому, не найдя
    # подписи, жмём по месту. Что открылось — проверяем: «Новая публикация»
    # с вкладками ПУБЛИКАЦИЯ / ИСТОРИЯ / REELS (cam_dest_*) внизу. Если вместо
    # главной была вкладка Reels, на том же месте камера — её разбирает
    # `from_camera`, как запасной путь.
    device.stop_app(package)
    human.pause(0.8, 1.4)
    device.open_app(package)
    human.pause(3.0, 4.5)
    ui.handle_permission(strict=True)
    nodes, ok = ui.dump(retries=1, tolerant=True), False
    create = _exact(nodes, step.get("create_words", []))
    if create is not None:
        ui.tap_node(create)
        tried.append("«+» на главной")
    else:
        w, h = adb.screen_size()
        px, py = step.get("plus_xy", (0.06, 0.071))
        adb.tap(int(w * px), int(h * py))
        tried.append("«+» на главной (по месту)")
    human.pause(2.0, 3.0)
    if True:
        nodes, ok = settle(15)
        if not ok and ui.find_any(nodes, reel_tab) is not None:
            tab = ui.find_any(nodes, reel_tab)
            ui.tap_node(tab)
            human.pause(1.5, 2.2)
            tried.append("вкладка REEL")
            nodes, ok = settle(12)
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
    nodes, ok = settle(15)
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
    blind_since, paused = None, False
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
            # Дерево пустое — экран не читается, пока играет видео (живьём
            # 2026-10-16: обрезка YouTube). Случайное нажатие по видео ставило
            # его на паузу, и дерево сразу читалось. Поэтому сначала ОДНО
            # нажатие в центр (на видео) — пауза; по снимку — только потом:
            # модель промахивалась по «Далее» на 15% масштаба.
            if nodes:
                blind_since, paused = None, False
            elif blind_since is None:
                blind_since = time.time()
            blind = time.time() - blind_since if blind_since is not None else 0.0
            pause_xy = step.get("pause_xy")
            if pause_xy and not paused and blind > step.get("pause_after", 6):
                w, h = adb.screen_size()
                adb.tap(int(w * pause_xy[0]), int(h * pause_xy[1]))
                paused = True
                notes.append("экран не читался — нажал на видео (пауза)")
                human.pause(1.0, 1.6)
                continue
            # Ни цели, ни кнопки дольше `vision_after` секунд — ищем по снимку.
            waited = blind if pause_xy else time.time() - started
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


UPLOAD_WORDS = re.compile(r"(загрузк|загружа|uploading|публику|публикац|posting|sharing|"
                          r"обработк|processing|отправк)", re.I)
UPLOAD_PERCENT = re.compile(r"(?<![\d.,])(?:\d{1,2}|100)\s?%")


def _upload_on_screen(package):
    """Видно ли на экране приложения, что загрузка идёт («45 %», «Публикация…»)."""
    try:
        pkg, _ = adb.current_app()
        if pkg != package:
            return False
        nodes = ui.dump(retries=1, tolerant=True)
    except Exception:
        return False
    for n in nodes or []:
        text = f"{n.text} {n.desc}"
        if UPLOAD_PERCENT.search(text) or UPLOAD_WORDS.search(text):
            return True
    return False


def wait_upload(package, video_path, log):
    """Дождаться, пока опубликованное видео уйдёт в сеть. True — дождались.

    После «Опубликовать» приложение ещё минуты грузит видео в фоне. Раньше
    программа тут же удаляла файл с телефона (YouTube читает его во время
    загрузки) и шла в следующую сеть, закрывая предыдущее приложение, —
    загрузка обрывалась (2026-10-17: «не дожидается, пока загрузится видос»).

    Признаки, что грузит: телефон отправляет данные быстрее фона, в
    уведомлении приложения прогресс, на экране «45 %» или «Публикация…».
    Конец — всё это стихло на UPLOAD_IDLE_SEC секунд. TikTok сперва пережимает
    видео (сеть молчит, а работа идёт), поэтому, пока не ушла хотя бы треть
    файла, тишину в первые две минуты концом не считаем.
    """
    try:
        size = os.path.getsize(video_path)
    except OSError:
        size = 0
    started = time.time()
    first = device.tx_bytes()
    last_bytes, last_at = first, started
    known = first is not None
    # Без счётчиков сети — хотя бы по объёму: ~2 Мбит/с отправки.
    floor = config.UPLOAD_MIN_SEC if known else \
        max(config.UPLOAD_MIN_SEC, min(600, size / 1024 / 1024 * 4))
    idle_since, sent, peak = None, 0, 0.0
    while True:
        abort.sleep(config.UPLOAD_POLL_SEC)
        if abort.requested():
            log.append("  загрузку не дождался: остановили")
            return False
        now = time.time()
        rate = None
        tx = device.tx_bytes() if known else None
        if tx is not None and last_bytes is not None:
            rate = max(0, tx - last_bytes) / max(0.5, now - last_at)
            sent = tx - first
            peak = max(peak, rate)
        last_bytes, last_at = tx, now
        progress = device.upload_progress(package)
        busy = bool(progress) or (rate is not None and rate > config.UPLOAD_IDLE_RATE) \
            or _upload_on_screen(package)
        idle_since = None if busy else (idle_since or now)
        elapsed = now - started
        early = known and size and sent < size * 0.3 and elapsed < 120
        if idle_since and now - idle_since >= config.UPLOAD_IDLE_SEC \
                and elapsed >= floor and not early:
            how = f", ушло {sent / 1024 / 1024:.1f} МБ" if known else ""
            log.append(f"  загрузка закончилась: ждал {elapsed:.0f} с{how}")
            return True
        if elapsed >= config.UPLOAD_WAIT_MAX:
            log.append(f"  [!] загрузка не кончилась за {elapsed / 60:.0f} мин — "
                       "дальше не жду, файл на телефоне оставляю")
            return False


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
    uploaded = False
    opened = False
    try:
        if not device.unlock():
            report.append("не смог разблокировать телефон")
            return False, report.text()
        device.keep_awake(True)
        # Соседние видеоприложения умеют висеть поверх «картинкой в картинке»
        # и перехватывать тапы. Убираем их до начала маршрута.
        device.close_overlays(known_packages(recipes), keep=package)

        if recipe.get("needs_storage") and device.has_storage_access(package) is False:
            # Видео уходит по file://, и без доступа к файлам приложение его не
            # прочтёт: экран откроется, превью — заглушка, кнопка — серая.
            raise ui.NeedsPermission(
                f"у {recipe.get('title', target)} нет доступа к файлам телефона — "
                "видео ему не прочитать. На телефоне: Настройки → Приложения → "
                f"{recipe.get('title', target).split()[0]} → Разрешения → «Файлы и медиа» "
                "(или «Фото и видео») → Разрешить. Один раз")

        try:
            device.cleanup_pushed()           # недогруженное с прошлых раз
        except (adb.AdbError, OSError):
            pass
        remote = device.push_video(video_path)
        device.remember_pushed(remote)
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
        if published:
            report.append("  жду, пока видео догрузится…")
            uploaded = wait_upload(package, video_path, report)
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
        # Файл удаляем, только когда приложение его больше не читает: не
        # публиковали — или загрузка кончилась. Не дождались — оставляем,
        # его уберёт `cleanup_pushed` через несколько часов.
        if remote and (not published or uploaded):
            device.remove_remote(remote)
            device.forget_pushed(remote)
        with open(os.path.join(log_path, "report.txt"), "w", encoding="utf-8") as f:
            f.write(report.text())
