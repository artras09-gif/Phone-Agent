"""Исполнитель маршрутов публикации из recipes.json."""
import json
import os
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




def _vision_note(log_path, index, label):
    """Спросить у зрения, что за экран нас застопорил.

    Только описание в лог: в маршруте публикации агент не жмёт ничего
    по подсказке модели, даже если VISION_MAY_TAP включён. Ошибка здесь
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

        if not device.wait_for_app(package, timeout=30):
            raise StepFailed(f"{package} не открылся")
        report.append("приложение открыто на экране публикации")

        ctx = {"caption": caption}
        for i, step in enumerate(recipe["steps"], start=1):
            line = run_step(step, ctx, log_path, i)
            report.append(f"  {i:2d}. {line}")

        report.append("готово")
        return True, report.text()

    except (StepFailed, adb.AdbError, RuntimeError) as e:
        report.append(f"ОШИБКА: {e}")
        return False, report.text()

    finally:
        device.keep_awake(False)
        if remote:
            device.remove_remote(remote)
        with open(os.path.join(log_path, "report.txt"), "w", encoding="utf-8") as f:
            f.write(report.text())
