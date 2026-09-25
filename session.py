"""Живая сессия в ленте: листаем, смотрим, изредка лайкаем.

Ни одного жёсткого числа: длина сессии, время на видео, геометрия свайпа
и решение поставить лайк — всё случайно. Ровные интервалы и одинаковые
свайпы палят автоматизацию быстрее, чем что угодно другое.
"""
import json
import queue
import random
import re
import threading
import time

import abort
import adb
import config
import device
import escape
import human
import interests
import jobs
import runlog
import ui
import vision


def _feed_config(app):
    with open(config.RECIPES, encoding="utf-8") as f:
        return json.load(f)["_feed_apps"][app]


def _all_packages():
    """Все приложения, которые агент вообще открывает.

    То же, что `poster.known_packages`, но своё: `poster` тянет за собой
    публикацию, а сессии она не нужна. Нужны они здесь ради одного —
    прибить чужую «картинку в картинке» перед заходом в ленту.
    """
    with open(config.RECIPES, encoding="utf-8") as f:
        data = json.load(f)
    found = set()
    for value in list(data.values()) + list((data.get("_feed_apps") or {}).values()):
        if isinstance(value, dict) and value.get("package"):
            found.add(value["package"])
    return sorted(found)


def _pick(nodes, selectors):
    """Первый подходящий узел, по которому реально можно попасть.

    Две ловушки, обе споткнулись живьём:
      * на каждую вкладку заводится ДВА узла — кнопка с границами и текстовая
        подпись с (0,0,0,0); тап по подписи уходит в никуда;
      * `desc` сравнивается по вхождению, поэтому «Reels» совпадает и с
        «контейнер для панели видео Reels» посреди экрана — тап по нему
        открыл сторис вместо ленты.
    Отсюда: только узлы с площадью, кликабельные впереди, и среди равных —
    меньший по площади: кнопка навигации мельче любого контейнера.
    """
    for sel in selectors:
        hits = [n for n in ui.find(nodes, **sel) if n.area > 0]
        if not hits:
            continue
        hits.sort(key=lambda n: (not n.clickable, n.area))
        return hits[0]
    return None


def _open_feed(cfg, log):
    """Открыть именно ЛЕНТУ, а не просто приложение.

    Прямая ссылка (`open_uri`) — основной путь: `instagram://reels_home` и
    `youtube.com/shorts` попадают в ленту сразу, мимо любых вкладок. У
    Instagram это единственный надёжный способ: его главная показывает те же
    ролики, и по дереву «Дом» от «Reels» не отличить.

    Запасной путь — открыть приложение обычно и поискать вкладки.
    """
    package = cfg["package"]
    uri = cfg.get("open_uri")

    # Соседние видеоприложения оставляют после себя «картинку в картинке» —
    # плавающее окошко в правом нижнем углу. Оно ЕСТ ТАПЫ: поймано живьём
    # 2026-08-30, окно Instagram висело ровно поверх кнопки «Поделиться»
    # TikTok, и лист «поделиться» не открывался пять роликов подряд, а в
    # журнале это выглядело как «лист не открылся». Раньше их прибивали
    # только перед публикацией (`poster`), а лента страдала так же.
    try:
        device.close_overlays(_all_packages(), keep=package)
    except Exception as e:                       # чужое окно важнее падения
        log.append(f"  не смог прибрать соседние приложения: {e}")

    if uri:
        device.open_uri(uri, package)
        if device.wait_for_app(package, timeout=25):
            # Приложение поднялось — но ЛЕНТА ли это? Раньше здесь стоял
            # `return True`, и ссылке верили на слово.
            #
            # Поймано живьём 2026-08-13: YouTube не открыл ссылку вовсе —
            # показал свою главную и тост «Не удалось загрузить ссылку», а
            # агент 5 свайпов из 5 сделал по ней, не заметив подмены (кадры
            # не менялись вообще, разница 0.0). Ссылка может не сработать по
            # чему угодно: нет связи, приложение обновилось, ссылка устарела.
            #
            # Проверять есть чем: у shorts и reels в рецепте заданы
            # `feed_markers`. Не сошлось — идём обычным путём, через вкладки.
            if _ensure_feed(cfg, log):
                return True
            log.append("ссылка открыла не ленту — захожу через вкладки")
        else:
            log.append("прямая ссылка не сработала, открываю приложение")

    device.open_app(package)
    if not device.wait_for_app(package, timeout=25):
        return False
    _ensure_feed(cfg, log)
    return True


def _ensure_feed(cfg, log, timeout=4):
    """Привести приложение в листаемую ленту. Вкладки берутся из рецепта.

    Открытое приложение — ещё не лента: оно восстанавливает тот экран, на
    котором его закрыли. Поймано живьём — сессия семь минут свайпала страницу
    профиля и не разобрала ни одного ролика: свайп там ничего не листает, а
    модель послушно описывала «пользователь просит отправить фотографии».
    У YouTube и Instagram то же самое по-своему: они открываются на своей
    главной, а Shorts и Reels — отдельные вкладки внизу.

    Промаха бывает два, и лечатся они по отдельности:
      1. нижняя вкладка (`feed_tabs`) — оказались не в той ленте;
      2. вкладка внутри ленты (`for_you_tabs`) — у TikTok это «Подписки»,
         где у свежего аккаунта пусто. У Shorts и Reels такого шага нет.

    Признак живой ленты — дерево НЕ снимается: uiautomator ждёт покоя
    интерфейса, а в ленте играет видео. Поэтому пустой дамп здесь означает
    «всё хорошо», а снявшийся — «мы не там».
    """
    feed_tabs = cfg.get("feed_tabs") or []
    for_you_tabs = cfg.get("for_you_tabs") or []
    markers = cfg.get("feed_markers") or []

    def in_feed(wait=0.0):
        """Мы в ленте? Способ зависит от приложения.

        У TikTok признак — дерево вообще перестало сниматься: uiautomator
        ждёт покоя, а там играет видео. У Shorts и Reels дерево снимается и
        во время проигрывания, поэтому им заданы `feed_markers` — кнопки
        плеера. Лента грузится не мгновенно (у Shorts бывает секунд восемь),
        поэтому признак ждём, а не проверяем однократно.
        """
        deadline = time.time() + wait
        while True:
            tree = ui.dump(retries=1, tolerant=True, timeout=timeout)
            if not markers:
                if not tree:
                    return True
            elif tree and _pick(tree, markers) is not None:
                return True
            if time.time() >= deadline:
                return False
            time.sleep(1.0)

    nodes = ui.dump(retries=1, tolerant=True, timeout=timeout)
    if not nodes:
        return True
    if markers and _pick(nodes, markers) is not None:
        return True

    moved = []

    # Вкладка внутри ленты видна, только когда мы уже в ней — это и есть
    # признак «нижнюю кнопку жать не надо».
    inside = _pick(nodes, for_you_tabs) if for_you_tabs else None
    if inside is None:
        tab = _pick(nodes, feed_tabs)
        if tab is None:
            return False       # незнакомый экран — разберётся _unstick по ходу
        ui.tap_node(tab)
        human.pause(1.0, 1.8)
        moved.append("лента")
        if in_feed(wait=10.0):
            log.append("вернулся в ленту: " + ", ".join(moved))
            return True
        nodes = ui.dump(retries=1, tolerant=True, timeout=timeout) or []

    if for_you_tabs:
        for_you = _pick(nodes, for_you_tabs)
        if for_you is not None:
            ui.tap_node(for_you)
            human.pause(1.2, 2.2)
            moved.append("рекомендации")

    # Верим не тапу, а результату.
    if not in_feed(wait=6.0):
        log.append("в ленту попасть не удалось — работаю как есть")
        return False

    if moved:
        log.append("вернулся в ленту: " + ", ".join(moved))
    return True


def _raw_scaled(state):
    """Сырой кадр экрана, ужатый на ПК через ffmpeg. None — не вышло.

    Заголовок `screencap`: ширина, высота, формат и — с Android 9 — ещё и
    цветовое пространство. Какой именно, определяем по остатку: пиксели это
    ровно ширина*высота*4 байта.
    """
    import struct
    import subprocess
    try:
        import stream as stream_mod
        exe = stream_mod.ffmpeg_exe()
    except Exception:
        return None
    if not exe:
        return None
    try:
        raw = adb.exec_out("screencap", timeout=20)
    except adb.AdbError:
        return None
    if not raw or len(raw) < 16:
        return None
    w, h, _fmt = struct.unpack("<III", raw[:12])
    head = 16 if len(raw) - 16 == w * h * 4 else 12
    if len(raw) - head != w * h * 4:
        return None
    factor = config.VISION_SHRINK or 1
    try:
        done = subprocess.run(
            [exe, "-loglevel", "error",
             "-f", "rawvideo", "-pix_fmt", "rgba", "-s", "%dx%d" % (w, h),
             "-i", "pipe:0", "-vf", "scale=%d:%d" % (w // factor, h // factor),
             "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1"],
            input=raw[head:], capture_output=True, timeout=25)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout or None


def _screen_shot(state, live):
    """Снимок экрана: (png, ужат ли он уже).

    Ужатый кадр берём только для РЕШЕНИЯ: остальные пути (слепой разбор,
    вес кадра) считают, что картинка полного размера.
    """
    if live and config.SHOT_RAW_SCALED:
        png = _raw_scaled(state)
        if png:
            return png, True
    try:
        return adb.exec_out("screencap -p", timeout=20), False
    except adb.AdbError:
        return None, False


def _grab(state, index, live=False):
    """Снять кадр и положить его на диск: (путь, байты) или (None, None).

    Съёмка — единственное место, где сессия ходит за картинкой, поэтому
    и обработка сбоя adb тут одна на всех.
    """
    # live — кадр нужен ИМЕННО СЕЙЧАС, в обход потока: тот отстаёт на 3-4 с
    # и отдал бы предыдущий ролик (см. DECIDE_FROM_SCREENCAP).
    feed = None if live else state.get("stream")
    scaled = False
    if feed is not None:
        png = feed.frame()
        if png is None:
            return None, None
    else:
        png, scaled = _screen_shot(state, live)
        if png is None:
            return None, None
    # Ужатый кадр весит в разы меньше — судить о пустоте его же порогом нельзя.
    state["frame_scaled"] = scaled
    blank_kb = config.BLANK_FRAME_SMALL_KB if scaled else None

    # Пустой кадр — погашенный экран ЛИБО лента, в которой нечего показать.
    # Отдавать такой модели бессмысленно: она честно опишет то, чего нет.
    # Сначала будим телефон и снимаем заново — это лечит первый случай.
    if vision.looks_blank(png, blank_kb):
        state["blank"] = state.get("blank", 0) + 1
        if not device.ensure_awake():
            state["grab_fail"] = "сбой"
            return None, None
        try:
            if feed is not None:
                png = feed.frame()
            else:
                png, scaled = _screen_shot(state, live)
                state["frame_scaled"] = scaled
                blank_kb = config.BLANK_FRAME_SMALL_KB if scaled else None
        except adb.AdbError:
            state["grab_fail"] = "сбой"
            return None, None
        if png is None or vision.looks_blank(png, blank_kb):
            # Экран разбудили, а кадр всё равно пустой — значит дело не в
            # телефоне, а в приложении: лента открыта, но контента в ней нет.
            # Поймано на Shorts: вкладка подсвечена, признаки ленты на месте,
            # а экран чёрный с крутилкой, потому что YouTube не отдаёт видео.
            # Считаем подряд идущие такие кадры — по одному судить нельзя,
            # между роликами чернота бывает и в норме.
            state["blank_streak"] = state.get("blank_streak", 0) + 1
            state["grab_fail"] = "пусто"
            return None, None

    # «Вес» кадра — по нему потом видно, что ролик пошёл по второму кругу.
    # Точку отсчёта портить снимком экрана нельзя: `_watch_video` сравнивает
    # её с кадрами ПОТОКА, а снимок и весит иначе, и размер у него другой.
    if not (live and state.get("stream") is not None):
        state["frame_size"] = len(png)
    state["blank_streak"] = 0
    state["grab_fail"] = ""
    return vision.save_frame(png, state["id"], index), png


def _capture(state, app, index, log):
    """Снять кадр текущего видео и отдать его на разбор.

    Съёмка дешёвая (~0.3 с по кабелю) и делается всегда, когда включён
    VISION_CAPTURE. Сам разбор занимает ~2 с, поэтому в режиме VISION_REALTIME
    он уходит в фоновый поток: модель думает над этим кадром, пока агент уже
    смотрит следующее видео, и «человеческие» тайминги не сбиваются.
    """
    path, png = _grab(state, index)
    if path is None:
        return

    if not config.VISION_REALTIME or state["blind"]:
        return
    # Подпись читаем здесь, в основном потоке: два потока, одновременно
    # дёргающие adb, мешают друг другу.
    caption = _read_caption(state)
    try:
        state["queue"].put_nowait((path, png, caption))
    except queue.Full:
        # Модель не успевает за лентой — кадр не теряется, он лежит на диске
        # и достанется команде analyze.
        state["skipped"] += 1


def _read_caption(state):
    """Подпись под роликом из дерева интерфейса: (текст, автор, музыка).

    В ленте дерево снимается редко: uiautomator ждёт покоя интерфейса,
    а видео играет. Поэтому одна попытка, tolerant, и пустой результат —
    это норма, а не ошибка: описание дополняет картинку, но не заменяет её.
    Если дерево не даётся подряд, перестаём тратить на него время вовсе.
    """
    if not config.READ_CAPTION or state.get("no_caption", 0) >= config.CAPTION_ATTEMPTS:
        return "", "", ""
    try:
        # Короткий таймаут вместо штатного: в ленте dump всё равно не снимется,
        # а ждать его 12 секунд на каждом ролике — половина времени сессии.
        nodes = ui.dump(retries=1, tolerant=True, timeout=5)
    except adb.AdbError:
        nodes = []
    if not nodes:
        state["no_caption"] = state.get("no_caption", 0) + 1
        return "", "", ""

    state["no_caption"] = 0
    return ui.feed_caption(nodes, screen=state.get("screen"),
                           package=state.get("package"))


def _feed_frozen(state, png):
    """Лента вообще сдвинулась? Сравнение кадра с предыдущим.

    Это главная проверка на окно поверх ленты, и она НЕ спрашивает модель.
    Разбор 12 кадров настоящего залипания показал, почему: модель описывает
    то окно («пользователи подписываются на друзей»), то ролик, который видно
    ПОЗАДИ окна («название видео не видно»), то вообще своё («набор друзей»).
    По описанию ловилось 8 кадров из 12, а картинка ловит все: свайп внутри
    окна листает его список, ролик позади остаётся тот же, и кадры почти
    совпадают.

    Замерено на 98 сохранённых сессиях: живая лента даёт разницу 61.7
    (медиана), залипшая — 4.3. Порог и число повторов — в config.

    Одной похожей пары мало: попадаются ролики, которые и сами почти не
    меняются (статичная картинка с текстом). Поэтому ждём `FEED_SAME_TIMES`
    подряд — это ловит все известные поломки и почти не даёт ложных.
    """
    sig = vision.frame_signature(png)
    prev = state.get("last_sig")
    state["last_sig"] = sig
    # Отпечаток не снялся — молчащая проверка не должна останавливать сессию.
    if sig is None or prev is None:
        state["same_frames"] = 0
        return False

    state["last_diff"] = vision.frames_differ(prev, sig)
    if state["last_diff"] >= config.FEED_SAME_LIMIT:
        state["same_frames"] = 0
        return False
    state["same_frames"] = state.get("same_frames", 0) + 1
    return state["same_frames"] >= config.FEED_SAME_TIMES


def _forget_frames(state):
    """Забыть накопленное про кадры — после любой попытки расклинить.

    Без этого следующий же кадр снова сравнивался бы с тем, что был ДО
    вмешательства, и тревога звучала бы второй раз подряд на пустом месте.
    """
    state["last_sig"] = None
    state["same_frames"] = 0
    state.setdefault("themes", []).clear()


def _looks_stuck(state, theme, window=3, overlap=0.5):
    """Лента ли перед нами. True, если тема повторяется от кадра к кадру.

    Признак специально не спрашивается у модели: 3B-модель не отличает окно
    поверх ленты от штатных кнопок TikTok (проверено — отвечает «окно» на всё
    подряд). Зато повтор описания она даёт сама: когда агент застревает,
    двенадцать кадров подряд описываются почти одинаково.
    """
    words = {w for w in theme.lower().split() if len(w) > 3}
    recent = state.setdefault("themes", [])
    similar = 0
    for old in recent:
        if not words or not old:
            continue
        if len(words & old) / max(len(words), 1) >= overlap:
            similar += 1

    recent.append(words)
    del recent[:-window]
    return similar >= window - 1


def _escape_now(state, goal, log):
    """Пустить модель выбираться с незнакомого экрана самой (escape.py).

    Возвращает True, если помеха ушла. Отличие от `_rescue`: тот спрашивал
    один совет по координатам и требовал `VISION_MAY_TAP`, а здесь модель
    выбирает элемент из дерева и видит, чем кончилась прошлая попытка.
    Промахнуться мимо кнопки нельзя, поэтому отдельного разрешения нет.
    """
    if not config.ESCAPE_ENABLED or state["blind"]:
        return False

    cfg = state.get("cfg") or {}
    markers = cfg.get("feed_markers") or []

    def shot():
        # Кадры спасателя нумеруются с 900, чтобы не смешиваться с роликами.
        state["rescues"] += 1
        return _grab(state, 900 + state["rescues"])[1]

    # Подписи вкладок ленты — из рецепта, а не из общего списка: у YouTube
    # лента это «Shorts», а «Главная» там ведёт на обычную главную.
    labels = [sel.get("desc") or sel.get("text") or ""
              for sel in (cfg.get("feed_tabs") or [])]
    labels = [x for x in labels if x] or None

    report = escape.escape(
        goal=goal, package=cfg.get("package"), log=log, grab=shot,
        feed_labels=labels,
        shrink=state.get("shrink"),
        # У Shorts и Reels дерево снимается и в ленте, поэтому свободу там
        # определяют маркеры из рецепта, а не пустой дамп.
        done=(lambda: _ensure_feed(cfg, log)) if markers else None,
        allow_done=bool(markers),
        deadline=time.time() + config.ESCAPE_MAX_SECONDS)

    if str(report["почему"]).startswith("модель недоступна"):
        # Тот же уговор, что и в `_capture`: один раз сказали — и больше не
        # ждём таймаут на каждой помехе.
        state["blind"] = True
    return report["ok"]


def _unstick(state, package, log):
    """Убрать окно, перекрывшее ленту. Возвращает описание того, что сделали.

    Лесенка от дешёвого к надёжному. Кнопку в дереве найти удаётся редко:
    поверх диалога играет видео, и uiautomator не дожидается покоя. «Назад»
    закрывает не всё — запрос доступа к контактам, например, переживает его.
    Перезапуск приложения убирает любой внутренний диалог гарантированно,
    поэтому он и стоит последней ступенью перед сдачей.
    """
    step = state["popups"]
    cfg = state.get("cfg") or {"package": package}

    # Известная кнопка и модель пробуются на КАЖДОЙ ступени, а не только на
    # первой. Раньше модель звали один раз, и если она не угадала сразу, дальше
    # шла одна грубая сила — «назад» и перезапуск. А выбрать кнопку она может
    # и со второго захода: экран после «назад» уже другой, и список кнопок
    # другой. Ложной тревоги это не боится: если дерево не снимается, значит
    # перед нами живая лента, и escape уходит, ничего не нажав.
    # ПЕРВЫМ ДЕЛОМ: не оказались ли мы на экране составления поста. Там
    # нельзя ни закрывать окна, ни спрашивать модель — каждая кнопка что-то
    # публикует. Уходим «назад», а не помогло — перезапуском приложения.
    # 2026-08-30: именно на таком экране в личном аккаунте появилась история.
    here = ui.dump(retries=1, tolerant=True, timeout=5)
    if ui.looks_like_composer(here):
        log.append("  это экран публикации — ухожу назад, ничего не нажимая")
        device.back()
        human.pause(0.8, 1.6)
        if ui.looks_like_composer(ui.dump(retries=1, tolerant=True, timeout=5)):
            device.stop_app(package)
            human.pause(1.0, 2.0)
            _open_feed(cfg, log)
            return "ушёл с экрана публикации перезапуском"
        _ensure_feed(cfg, log)
        return "ушёл с экрана публикации"

    if ui.dismiss_popup(here):
        _ensure_feed(cfg, log)
        return "закрыл по кнопке"
    if _escape_now(state, "лента не листается, поверх неё окно", log):
        _ensure_feed(cfg, log)
        return "выбрался сам"

    if step == 1:
        # На первой попытке «назад» НЕ жмём. Тревога бывает ложной (ролик,
        # который сам почти не меняется), а «назад» на живой ленте уводит из
        # приложения — то есть ошибка обходилась дороже самой поломки.
        # Ничего не делать здесь безопасно: окно никуда не денется, и вторая
        # тревога придёт через пару кадров, уже со ступенью «назад».
        return "кнопки не нашлось, жду подтверждения (назад пока не жму)"

    if step == 2:
        # «Назад» уже не помог: возможно, мы вообще ушли из ленты — например,
        # на профиль автора. Перезапуск убирает любой внутренний диалог,
        # поэтому тянуть со второй попыткой «назад» смысла нет.
        device.back()
        human.pause(0.8, 1.6)
        if device.wait_for_app(package, timeout=10):
            _ensure_feed(cfg, log)
            return "нажал «назад» ещё раз"

    device.stop_app(package)
    human.pause(1.0, 2.0)
    # Перезапуск возвращает приложение, но НЕ ленту: холодный старт открывает
    # главную. Раньше здесь на этом и успокаивались.
    if not _open_feed(cfg, log):
        return "перезапустил приложение, но оно не открылось"
    ui.dismiss_popup(ui.dump(retries=1, tolerant=True, timeout=5))

    # После перезапуска экран новый — и модель стоит спросить ещё раз. Именно
    # здесь она и нужна: холодный старт TikTok любит показать своё окно
    # («Подпишитесь на друзей», запрос доступа), и без этого шага следующая
    # тревога пришла бы только через несколько кадров.
    if _escape_now(state, "после перезапуска мешает окно", log):
        _ensure_feed(cfg, log)
        return "перезапустил приложение и убрал окно"
    return "перезапустил приложение"


def _look_and_decide(state, app, index, taste, log):
    """Взглянуть на ролик, узнать тему и решить, смотреть или листать.

    Возвращает (вердикт, пролистать?, сколько секунд держать на экране).
    Разбор здесь синхронный — решать задним числом бессмысленно. Пауза
    «взгляда» и время ответа модели идут параллельно, поэтому ожидание почти
    не заметно: модель отвечает за ~2 с, взгляд длится 1.5-3 с.

    Длительность просмотра считается здесь же, а не в цикле: тут известно
    и что совпало, и сколько уже потрачено на решение, а цикл получает
    готовое число и просто досыпает остаток.
    """
    if state["blind"]:
        abort.sleep(interests.peek_seconds(taste))
        return None, False, None

    started = time.time()
    phases = {}
    path, png = _grab(state, index, live=config.DECIDE_FROM_SCREENCAP)
    phases["кадр"] = time.time() - started
    if path is None:
        # Кадр за кадром пусто, хотя экран разбужен — это не «не повезло со
        # съёмкой», а лента без контента. Молча спать дальше нельзя: раньше
        # сессия так и крутилась «пустой кадр -> пауза -> свайп» до конца
        # отведённого времени, ни разу об этом не сказав.
        if (state.get("grab_fail") == "пусто"
                and state.get("blank_streak", 0) >= config.EMPTY_FEED_TIMES):
            return "нет контента", None, None
        abort.sleep(interests.peek_seconds(taste))
        return None, False, None

    # Проверяем ДО обращения к модели. Если поверх ленты висит окно, спрашивать
    # про этот кадр незачем: модель опишет фон или само окно, а решение по
    # такому описанию всё равно мусорное — и это ещё и лишний запрос.
    if _feed_frozen(state, png):
        return "застряли", None, None

    mark_at = time.time()
    caption, author, music = _read_caption(state)
    phases["подпись"] = time.time() - mark_at
    mark_at = time.time()
    # Ужатие считается ОТ размера кадра: снимок экрана 1080x2400, кадр потока
    # 720x1600. Общий множитель на оба источника давал модели картинку вдвое
    # большей площади и лишние 0.4 с на запрос (замер: 1.25 с против 0.87 с).
    # Кадр уже ужат на ПК — модели ужимать нечего. Иначе множитель считается
    # от размера источника: снимок 1080x2400, кадр потока 720x1600.
    shrink = (1 if state.get("frame_scaled")
              else (config.VISION_SHRINK if config.DECIDE_FROM_SCREENCAP
                    else state.get("shrink")))
    try:
        data = vision.describe_frame(png, caption, author, music,
                                     shrink=shrink)
    except vision.VisionError as e:
        state["blind"] = True
        log.append(f"  решения по теме отключены: {str(e)[:80]}")
        return None, False, None

    # Тема пустая — кадр не разобран (модель сорвалась или ответила мусором).
    # Выносить по такому вердикт нельзя: «тема не совпала» при неизвестном
    # языке даёт «нейтрально», и сессия молча превращается в «смотрю всё».
    if not str(data.get("тема", "")).strip():
        state["unparsed"] = state.get("unparsed", 0) + 1
        if state["unparsed"] >= 3:
            state["blind"] = True
            log.append("  решения по теме отключены: модель не разбирает кадры")
        return None, False, None
    state["unparsed"] = 0

    # Лента не двигается — значит поверх неё окно приложения («Подпишитесь
    # на друзей», комментарии, запрос разрешения). Свайпы в нём листают его
    # список, а не ленту, и агент способен разбирать одно и то же бесконечно.
    if _looks_stuck(state, str(data.get("тема", ""))):
        return "застряли", None, None

    phases["модель"] = time.time() - mark_at
    mark_at = time.time()
    jobs.add_content(state["id"], app, path, data)
    phases["база"] = time.time() - mark_at
    mark_at = time.time()
    skip, verdict, why, matched = interests.decide(
        data, taste, state.get("expansions"), judge=vision.judge_topic)
    phases["тема"] = time.time() - mark_at

    # Что именно совпало, нужно и ЗА пределами этой функции: заход в
    # комментарии разрешён только на своём языке. Разбирать обратно строку
    # причины нельзя — она человеческая и меняется.
    state["matched"] = matched

    # Решили листать — листаем сразу. Человек тоже не досматривает ролик,
    # который ему не подходит: понял за секунду и смахнул. Добираем «взгляд»
    # только для тех, что остаёмся смотреть.
    if not skip:
        left = interests.peek_seconds(taste) - (time.time() - started)
        if left > 0:
            abort.sleep(left)

    state["why"] = why
    # «Настроение» тянет время просмотра за собой: в полосе, когда человек
    # завис, дольше держится ВСЁ, а не отдельный ролик. Множитель общий на
    # все ступени, поэтому их порядок между собой сохраняется — интересное
    # по-прежнему не короче нейтрального.
    watch = (interests.watch_seconds(verdict, matched, human.dwell(), taste)
             * state.get("mood", 1.0) * state.get("daypart", 1.0))
    # Потолок тот же, что у залипания. Настроение доходит до 2.5, и без него
    # «интересно» на 20 секунд растянулось бы до пятидесяти. С кадрами из
    # потока это неопасно (просмотр оборвётся на конце ролика), а вот без них
    # обрывать нечем — там потолок много скромнее.
    if watch:
        watch = min(watch, _deep_watch_seconds(state))
    mark = "листаю" if skip else ("вполглаза" if verdict == "нейтрально"
                                  else "смотрю")
    # В логе именно ВРЕМЯ НА ЭКРАНЕ, а не время решения: раньше стояло второе,
    # и «вполглаза за 2.2с» читалось как «посмотрел две секунды», хотя это
    # думала модель, а просмотр шёл после строки.
    # Разбор по фазам показываем всегда, когда решение затянулось: «стало
    # медленнее» — жалоба регулярная, и без разбора на неё отвечают догадками,
    # а поймать медленный всплеск вручную не выходит, он приходит волнами.
    spent = time.time() - started
    detail = ""
    if config.TIMING or spent >= config.SLOW_DECISION_SEC:
        detail = " [" + " ".join(f"{k} {v:.2f}" for k, v in phases.items()) + "]"
    log.append(f"  {mark} {watch:.1f}с (решение {time.time() - started:.1f}с){detail}: "
               f"{verdict} ({why}) — {str(data.get('тема', ''))[:46]}")
    jobs.set_verdict(path, verdict, skip)
    _watch_speed(state, phases, log)
    return verdict, skip, watch


def _deep_watch_due(state, watched):
    """Пора ли залипнуть на этом ролике и досмотреть его целиком."""
    at = state.get("deep_at")
    return bool(at) and watched >= at


def _deep_watch_rearm(state, watched, log=None):
    """Назначить, на каком по счёту ролике залипнем в следующий раз."""
    gap = human.deep_watch_gap(scale=state.get("daypart", 1.0))
    state["deep_at"] = (watched + gap) if gap else 0
    if log is not None and gap:
        log.append(f"  (следующее залипание через {gap} видео)")


def _leave_due(state, watched):
    """Пора ли отложить телефон: выйти из приложения и вернуться."""
    at = state.get("leave_at")
    return bool(at) and watched >= at


def _leave_rearm(state, watched, log=None):
    """Назначить, на каком по счёту ролике выйдем в следующий раз.

    Второй вид залипания, парный к «досмотреть целиком». Считается так же —
    интервалом в роликах, а не шансом на каждом: шанс давал кучность (то три
    раза подряд, то ни разу за сессию), интервал держит частоту, оставляя
    случайным сам момент.
    """
    gap = human.deep_watch_gap(span=getattr(config, "LEAVE_APP_EVERY", None),
                               scale=state.get("daypart", 1.0))
    state["leave_at"] = (watched + gap) if gap else 0
    if log is not None and gap:
        log.append(f"  (следующий выход из приложения через {gap} видео)")


def _deep_watch_seconds(state):
    """Предел на «досмотреть целиком».

    Обычно не достигается: `_watch_video` обрывает ролик на возврате картинки
    к начальной, то есть на настоящем его конце. Но поймать это можно только
    по кадрам из потока — без них предел работает вслепую, и тогда он должен
    быть скромным, иначе восьмисекундный ролик прокрутится одиннадцать раз.
    """
    if state.get("stream") is not None and state.get("frame_size"):
        return float(config.DEEP_WATCH_MAX_SEC)
    return float(config.DEEP_WATCH_NO_STREAM_SEC)


def _video_playing(state, wait=0.9):
    """Идёт ли ролик прямо сейчас. None — сказать нечем.

    Способ один на все приложения: два кадра врозь. Играет — картинка
    меняется, на паузе — нет. Признаки в дереве для этого не годятся: у
    TikTok дерево в ленте вообще не снимается, а у Shorts кнопка плеера
    называется по-разному в зависимости от языка приложения.
    """
    first = _fresh_shot(state)
    if not first:
        return None
    if abort.sleep(wait):
        return None
    second = _fresh_shot(state)
    if not second:
        return None
    return vision.frames_differ(vision.frame_signature(first),
                                vision.frame_signature(second)) \
        > config.PAUSED_FRAME_LIMIT


def _fresh_shot(state):
    """Снимок ИМЕННО СЕЙЧАС. Всегда `screencap`, минуя поток кадров.

    Поток отдаёт кадр возрастом до нескольких секунд — он пишется кусками по
    `STREAM_CHUNK_SEC`, — и на запрос свежего часто отвечает пустотой. Для
    просмотра это неважно, а здесь важно: обе проверки, «играет ли ролик» и
    «открылась ли панель», про состояние в конкретный момент.

    Поймано живьём: с потоком обе возвращали «не знаю», и заход в
    комментарии либо не начинался, либо откатывался на ровном месте.
    Заодно порог `COMMENTS_PANEL_RATIO` измерялся именно на `screencap`, и
    сравнивать надо тем же способом, каким мерили.

    Стоит ~0.6 с, но зовётся только в заходе в комментарии — раз в несколько
    минут, на фоне его же тридцати секунд.
    """
    try:
        return adb.exec_out("screencap -p", timeout=20)
    except adb.AdbError:
        return None


def _frame_size(state):
    """Вес текущего кадра в байтах. 0 — снять не вышло.

    Тем же способом ловится зацикливание ролика: у сложной картинки PNG
    тяжёлый, у простой лёгкий. Крупная светлая плашка комментариев роняет
    вес втрое — это и есть признак, что панель открылась.
    """
    png = _fresh_shot(state)
    return len(png) if png else 0


def _tap_video(state, w, h):
    """Одиночный тап по видео — пауза или продолжение.

    Точка та же, что у лайка: зона уже выверена (20 000 бросков, ноль
    попаданий в колонку кнопок и в полосу перемотки).
    """
    x, y = human.like_point(w, h)
    adb.tap(x, y)
    human.pause(0.4, 0.9)


def _resume_if_paused(state, w, h, log):
    """Вернуть ролик в проигрывание, если он остался на паузе."""
    playing = _video_playing(state)
    if playing is False:
        _tap_video(state, w, h)
        if _video_playing(state) is False:
            log.append("  ролик остался на паузе — листаю дальше")
            return False
    return True


def _may_read_comments(state):
    """Прошло ли достаточно с прошлого захода в комментарии."""
    last = state.get("commented_at") or state.get("started_at") or 0
    return time.time() - last >= config.COMMENTS_EVERY_SEC


def _read_comments(state, cfg, w, h, log):
    """Открыть комментарии, полистать, закрыть. True — сходили.

    Единственное место, где агент тапает НЕ по лайку. Поэтому каждый шаг
    подтверждается, а любая неудача откатывается назад: тапы в незнакомый
    интерфейс уже уводили сессию из ленты на чужой профиль.

    Ключевой приём: сначала ставим ролик на паузу одиночным тапом. Пока
    видео играет, `uiautomator` не снимает дерево («could not get idle
    state»), и кнопку комментариев пришлось бы искать по координатам —
    то есть гадать. На паузе экран статичен, дерево снимается, и кнопка
    находится обычным селектором, как всё остальное в проекте.
    """
    selectors = cfg.get("comment_selectors") or []
    if not selectors:
        return False                        # для этой ленты не заведено

    # Лента могла не сдвинуться — тогда под пальцем не видео, а окно
    # приложения. Та же оговорка, что у лайка, и та же цена ошибки.
    if state.get("same_frames"):
        return False
    if _video_playing(state) is not True:
        return False                        # уже на паузе или кадров нет

    state["commented_at"] = time.time()

    # Пауза со второй попытки. Одиночный тап срабатывает не всегда: он мог
    # уйти в надпись поверх видео или прийтись на смену ролика. Живьём из
    # двух заходов один спотыкался именно здесь. Проверяем состояние ПОСЛЕ
    # каждого тапа, а не считаем, что тап равен паузе.
    for attempt in range(2):
        _tap_video(state, w, h)
        if _video_playing(state) is False:
            break
    else:
        # Ролик так и не остановился. Ничего не открылось — идём дальше.
        # Тапали мы при этом по видео, то есть не задели ничего лишнего.
        log.append("  хотел глянуть комментарии, но пауза не сработала")
        return False

    # Терпеливее, чем везде: у Shorts дерево снимается и на ходу, но не
    # мгновенно — с `retries=1, timeout=6` приходил пустой ответ там, где
    # через секунду лежало 111 узлов. Заход в комментарии бывает раз в
    # четыре минуты, лишние секунды тут ничего не стоят.
    tree = ui.dump(retries=2, tolerant=True, timeout=9)
    node = _pick(tree, selectors) if tree else None
    if node is None:
        log.append("  комментариев не нашёл — возвращаю ролик")
        _resume_if_paused(state, w, h, log)
        return False

    # Дерево нужно было ТОЛЬКО чтобы узнать, где кнопка. У TikTok тапать по
    # ней надо уже по ИГРАЮЩЕМУ ролику: на паузе он тратит первый тап на
    # возобновление, и панель не открывается. У Shorts и Reels наоборот —
    # там паузу держим (то же различие, что и у листа «поделиться»).
    needs_playing = cfg.get("comments_need_playing",
                            cfg.get("share_needs_playing", True))
    if needs_playing:
        _tap_video(state, w, h)             # снова играет
    before = _frame_size(state)
    ui.tap_node(node)
    human.pause(1.2, 2.0)

    # Проверять открытие по дереву НЕЛЬЗЯ: при открытых комментариях видео
    # продолжает играть сверху, и `uiautomator` по-прежнему не снимает
    # ничего. Зато панель — крупная светлая плашка, и кадр от неё резко
    # легчает. Замерено на живых роликах: 2248→903, 1999→599, 2307→861 КБ,
    # то есть отношение 0.30-0.40 при пороге 0.65.
    after = _frame_size(state)
    opened = bool(before and after and after / before < config.COMMENTS_PANEL_RATIO)
    if not opened:
        log.append("  комментарии не открылись — откатываюсь")
        device.back()
        human.pause(0.6, 1.2)
        _ensure_feed(cfg, log)
        _resume_if_paused(state, w, h, log)
        return False

    # Читаем: пара коротких протяжек по списку с паузами на чтение.
    reads = random.randint(1, 3)
    for _ in range(reads):
        if abort.requested():
            break
        human.scroll_comments(w, h)
        human.pause(*config.COMMENTS_READ_SEC)

    device.back()
    human.pause(0.6, 1.2)
    log.append(f"  заглянул в комментарии (протяжек: {reads})")

    # Назад в ленту — и вернуть ролик в проигрывание.
    _ensure_feed(cfg, log)
    _resume_if_paused(state, w, h, log)
    return True


# Адрес ролика в том виде, в каком его отдаёт «поделиться»: у TikTok это
# короткая ссылка `https://www.tiktok.com/t/ZTDfdsRYo/`.
_URL = re.compile(r"https?://\S+")

# Число из подписи кнопки: «Поставить лайк. Число лайков: 1578».
_COUNT = re.compile(
    r"(\d[\d  ]*(?:[.,]\d+)?)\s*(тыс|млн|млрд|k|m|b|к|м)?", re.I)

_MULTIPLIER = {"тыс": 1_000, "к": 1_000, "k": 1_000,
               "млн": 1_000_000, "м": 1_000_000, "m": 1_000_000,
               "млрд": 1_000_000_000, "b": 1_000_000_000}


def _parse_count(text):
    """Сколько лайков стоит в подписи. None — не разобрали.

    Берём ПОСЛЕДНЕЕ число строки: счётчик стоит после двоеточия
    («Число лайков: 1578»), а до него попадается что угодно.

    Разделители неоднозначны, и это не придирка: «1,5 млн» — это полтора
    миллиона, а «1,500» в английском интерфейсе — полторы тысячи. Решает
    суффикс: есть он — запятая дробная, нет — она разделяет разряды.
    """
    if not text:
        return None
    best = None
    for m in _COUNT.finditer(text):
        raw = m.group(1).replace(" ", "").replace(" ", "")
        suffix = (m.group(2) or "").lower()
        try:
            if suffix:
                value = float(raw.replace(",", ".")) * _MULTIPLIER[suffix]
            else:
                value = float(re.sub(r"[.,]", "", raw))
        except (ValueError, KeyError):
            continue
        best = int(value)
    return best


def _clean(text):
    """Убрать из подписи то, что мешает её читать.

    TikTok добивает обрезанное описание пачкой невидимых `﻿` — в
    журнале и в выгрузке это выглядит как мусор, а в CSV ломает столбцы.
    """
    text = re.sub(r"[﻿​‎‏]+", "", text or "")
    return re.sub(r"\s+", " ", text).strip()


def _point(cfg, key, w, h):
    """Точка из `recipes.json` в пикселях. None — не задана.

    Хранится ДОЛЯМИ экрана, а не пикселями: телефоны разного разрешения, а
    правая колонка кнопок стоит у всех примерно на одной доле высоты.

    Точки нужны там, где кнопок нет в дереве вовсе — в Shorts и Reels. Это
    заведомо хуже селекторов (обновление приложения двигает колонку, и
    промах ничем не отличается от попадания), поэтому каждый шаг после тапа
    ещё и проверяется: открылся ли лист, нашлась ли строка поиска, вернулась
    ли лента. Не сошлось — откатываемся, ролик остаётся нетронутым.
    """
    pt = cfg.get(key)
    if not pt or len(pt) != 2:
        return None
    return int(float(pt[0]) * w), int(float(pt[1]) * h)


def _tap_any(target):
    """Тапнуть по узлу или по точке — смотря что нашлось.

    По узлу — обязательно через `ui.tap_node`: он бьёт в случайную точку
    внутри элемента, а не в математический центр. Ровный центр кнопки — сам
    по себе признак автомата, и терять эту мелочь из-за того, что у соседней
    ленты кнопки нет в дереве, незачем.
    """
    if hasattr(target, "center"):
        return ui.tap_node(target)
    adb.tap(*target)
    return target


def _points(cfg, key, w, h):
    """Список точек — путь из нескольких тапов (лента → главная → поиск)."""
    out = []
    for pt in cfg.get(key) or []:
        if pt and len(pt) == 2:
            out.append((int(float(pt[0]) * w), int(float(pt[1]) * h)))
    return out


_LIKES_PROMPT = (
    "На картинке кадр из ленты коротких видео. В правой колонке значков "
    "есть сердечко, под ним подписано число лайков. Ответь ТОЛЬКО этим "
    "числом так, как оно написано на экране (например: 12,3 тыс. или 1.5M). "
    "Если числа не видно — ответь: нет."
)


def _likes_by_vision(log):
    """Прочитать число лайков с картинки.

    Нужно для Shorts и Reels: правой колонки кнопок в их дереве нет вовсе
    (проверено живьём), а значит `like_count_selectors` там ничего не найдёт
    и порог `LINK_MIN_LIKES` проверять нечем. Зрение в проекте уже есть, и
    один запрос на ЛАЙКНУТЫЙ ролик — цена небольшая: лайк ставится примерно
    каждому четырнадцатому.

    Ошибиться модель может, поэтому ответ проходит те же `_parse_count` и
    здравый смысл: не число — ссылку не берём.
    """
    if not config.LINK_LIKES_BY_VISION:
        return None
    ok, _ = vision.available()
    if not ok:
        log.append("  зрение недоступно — число лайков не прочитать")
        return None
    try:
        png = adb.exec_out("screencap -p", timeout=20)
    except adb.AdbError:
        return None
    if not png or vision.looks_blank(png):
        return None
    try:
        answer = vision.ask(png, _LIKES_PROMPT, max_tokens=24, temperature=0)
    except vision.VisionError as e:
        log.append(f"  зрение не ответило про лайки: {e}")
        return None
    if vision.looks_degenerate(answer) or "нет" in (answer or "").lower()[:6]:
        return None
    likes = _parse_count(answer)
    if likes:
        log.append(f"  лайков по картинке: {likes} («{_clean(answer)[:24]}»)")
    return likes


def _read_clipboard(cfg, route, log):
    """Прочитать буфер обмена — единственным доступным здесь способом.

    С Android 10 буфер читает только приложение на переднем плане, а
    `adb shell cmd clipboard` на этом телефоне не реализован вовсе
    (Android 12, рута нет) — проверено. Поэтому скопированное вставляется
    в поле поиска самого приложения и читается уже из дерева.

    Поле ПОИСКА, а не комментария, по двум причинам: экран поиска статичен,
    там не играет видео и дерево снимается (при открытых комментариях оно
    по-прежнему пустое — видео играет сверху), и рядом нет кнопки
    «отправить», то есть промахнуться в чужую ленту нечем.

    В самой ленте кнопки поиска в дереве нет — заходим через «Интересное»
    (в Shorts и Reels — по точкам из `recipes.json`, там и вкладок в дереве
    нет: сначала домашняя вкладка, потом значок поиска).

    `route` — путь до строки поиска: список точек, по которым надо тапнуть.
    """
    # Прямая ссылка на экран поиска, если она у ленты есть. Надёжнее тапов по
    # вкладкам: у Instagram первый тап после паузы уходит на возобновление
    # ролика, экран не меняется, и дерево остаётся деревом ЛЕНТЫ — то есть
    # пустым. Ссылка переключает экран независимо от того, что под пальцем.
    uri = cfg.get("search_uri")
    if uri:
        device.open_uri(uri, cfg.get("package"))
        human.pause(1.6, 2.4)
    else:
        for step in route:
            _tap_any(step)
            human.pause(1.6, 2.4)

    tree = ui.dump(retries=2, tolerant=True, timeout=10)
    box = _pick(tree, cfg.get("search_boxes") or []) if tree else None
    field = ui.find_one(tree, cls="EditText") if tree else None

    # Первый тап после паузы приложение тратит на возобновление ролика, а не
    # на кнопку — поймано живьём и в TikTok, и в Reels: экран не менялся
    # вовсе, и в журнале это выглядело как «строки поиска не нашёл». Поэтому
    # последний шаг пути повторяется один раз. Повтор безопасен: если первый
    # тап всё-таки прошёл, мы уже на экране поиска, и там этой кнопки нет.
    if box is None and field is None and route:
        log.append("  экран не сменился — повторяю переход к поиску")
        _tap_any(route[-1])
        human.pause(1.6, 2.4)
        tree = ui.dump(retries=2, tolerant=True, timeout=10)
        box = _pick(tree, cfg.get("search_boxes") or []) if tree else None
        field = ui.find_one(tree, cls="EditText") if tree else None

    if box is None and field is None:
        log.append("  строки поиска не нашёл — прочитать буфер нечем")
        return ""

    # Поле уже открыто и готово принимать ввод — второй тап по «поиску» тогда
    # лишний: в YouTube значок поиска сразу открывает экран с курсором в
    # строке, и повторный тап уводил бы с него.
    if field is None:
        ui.tap_node(box)
        human.pause(1.2, 2.0)
        tree = ui.dump(retries=2, tolerant=True, timeout=10)
        field = ui.find_one(tree, cls="EditText") if tree else None
    if field is None:
        log.append("  поля ввода на экране поиска нет")
        return ""

    ui.tap_node(field)
    human.pause(0.5, 0.9)
    adb.keyevent("KEYCODE_PASTE")
    human.pause(0.8, 1.4)

    text = ""
    for node in ui.dump(retries=2, tolerant=True, timeout=10) or []:
        if "EditText" in node.cls and node.text:
            text = node.text
            break

    # Чистим за собой: иначе адрес останется в строке поиска и всплывёт
    # хозяину телефона при первом же заходе. Курсор после вставки стоит в
    # конце, поэтому хватает забоя — одним вызовом, а не семьюдесятью.
    if text:
        adb.shell("input keyevent " + " ".join(["KEYCODE_DEL"] * 70),
                  check=False)

    found = _URL.search(text)
    if not found:
        log.append("  в поле поиска адреса не оказалось")
    return found.group(0) if found else ""


def _back_to_feed(cfg, home_at, log):
    """Вернуться из поиска в ленту.

    Одного «назад» мало: первый закрывает клавиатуру, второй уводит с
    экрана поиска — и только тогда видна нижняя панель с «Главной».
    Признак возврата тот же, что и везде у TikTok: дерево перестало
    сниматься, значит играет видео.

    В Shorts и Reels пустое дерево ленты — не признак: у них оно пустое
    почти всегда. Там возврат подтверждает `_ensure_feed` по своим
    признакам ленты (`feed_markers`), и `home_at` — точка, а не узел.
    """
    for _ in range(2):
        device.back()
        human.pause(0.6, 1.1)

    blind = not (cfg.get("home_tabs") or [])
    for _ in range(4):
        tree = ui.dump(retries=1, tolerant=True, timeout=5)
        if not tree and not blind:
            return True
        tab = _pick(tree, cfg.get("home_tabs") or []) if tree else None
        if tab is not None:
            ui.tap_node(tab)
        elif home_at:
            adb.tap(*home_at)
        else:
            device.back()
        human.pause(1.2, 2.0)
        if blind and _ensure_feed(cfg, log):
            return True

    _ensure_feed(cfg, log)
    return False


# Слова, по которым что-то УХОДИТ наружу: в историю, в чат, в ленту. Агент
# берёт из листа «поделиться» ровно один пункт — «копировать ссылку», и всё
# остальное для него запрещено. Это последняя преграда: даже если селектор
# промахнётся или разметка листа поменяется, нажать «Добавить в историю»
# он не сможет.
#
# Заведено 2026-08-30 после того, как в аккаунте появилась история, которую
# никто не публиковал. Прямую причину доказать не удалось, но кандидатов было
# два, оба мои: фиксированная координата «поделиться» попадала на живом
# экране в «Комментарий» и «Ещё» (колонка съезжает от длины подписи), а выбор
# строки «по месту» работал во всех сетях, а не только в YouTube.
SENDING_WORDS = (
    "истори", "story", "отправ", "send", "опубликов", "publish", "post",
    "репост", "repost", "директ", "direct", "поделиться в", "share to",
    "добавить в", "add to", "чат", "chat",
)


def _is_sending(node):
    """Кнопка что-то отправляет или публикует? Тогда не трогаем."""
    if node is None:
        return False
    label = ((getattr(node, "desc", "") or "") + " "
             + (getattr(node, "text", "") or "")).lower()
    return any(word in label for word in SENDING_WORDS)


def _link_row(sheet, w):
    """Пункт «копировать ссылку», у которого в дереве НЕТ подписи.

    Так устроен лист YouTube: на экране написано «Коп. ссылку», а в дереве
    это безымянный кликабельный блок во всю ширину (замер 2026-08-30: из 37
    узлов подписаны только пять значков приложений). Селектору не за что
    зацепиться, поэтому берём по строению листа.

    Правило: строка ссылки — ПЕРВАЯ кликабельная полоса во всю ширину,
    лежащая НИЖЕ ряда значков приложений. Ряд опознаётся по самим значкам:
    они кликабельны и узкие. Ниже строки ссылки идёт «Быстрая отправка» —
    её отсекает именно слово «первая».

    Опора на порядок, а не на подпись, поэтому промах возможен. Он не
    страшен: вставленное в строку поиска всё равно проверяется на адрес, и
    не-адрес просто не сохранится.
    """
    icons = [n for n in sheet
             if n.clickable and 0 < (n.bounds[2] - n.bounds[0]) < w * 0.30]
    if not icons:
        return None
    strip_bottom = max(n.bounds[3] for n in icons)

    rows = [n for n in sheet
            if n.clickable
            and (n.bounds[2] - n.bounds[0]) > w * 0.60
            and n.bounds[1] >= strip_bottom
            # `touch_outside` растянут на весь экран и закрыл бы лист.
            and (n.bounds[3] - n.bounds[1]) < strip_bottom]
    if not rows:
        return None
    rows.sort(key=lambda n: n.bounds[1])
    return rows[0]


def _likes_when_liked(tree, cfg):
    """Число лайков у УЖЕ ЛАЙКНУТОГО ролика. None — не нашли.

    Нужно потому, что TikTok переименовывает кнопку после нажатия:
    до лайка `desc` = «Поставить лайк. Число лайков: 589,4 тыс.», после —
    «Вам понравилось это видео», и число уезжает в ОТДЕЛЬНЫЙ узел рядом
    («589,4 тыс.») без единого слова про лайки. А сюда мы попадаем только
    после своего же лайка, то есть обычный селектор не совпадал никогда.

    По подписи такой узел не отличить от счётчика комментариев или
    «Избранного» — они выглядят так же. Поэтому опора на РАСПОЛОЖЕНИЕ:
    берём число, лежащее внутри границ самой кнопки лайка.
    """
    button = _pick(tree, cfg.get("liked_selectors") or [])
    if button is None:
        return None
    x1, y1, x2, y2 = button.bounds
    if x2 <= x1 or y2 <= y1:
        return None
    best = None
    for node in tree:
        nx1, ny1, nx2, ny2 = node.bounds
        inside = (nx1 >= x1 and ny1 >= y1 and nx2 <= x2 and ny2 <= y2)
        if not inside or node is button:
            continue
        value = _parse_count(node.text or node.desc)
        if value is not None:
            # Самый нижний: счётчик стоит под значком сердца.
            if best is None or ny1 > best[0]:
                best = (ny1, value)
    return best[1] if best else None


def _save_link(state, cfg, w, h, log, app):
    """Забрать адрес понравившегося ролика и записать его в базу.

    Делается только после НАСТОЯЩЕГО лайка и только у роликов, набравших
    `config.LINK_MIN_LIKES`. Порог здесь не каприз: заход стоит семи тапов
    и перехода в «Интересное» и обратно, то есть секунд десяти, — на каждом
    лайкнутом ролике это и время, и лишние следы.

    Каждый шаг подтверждается, любая неудача откатывается назад. Вызывается
    в конце просмотра, перед свайпом: если лента после возврата покажет
    другой ролик, терять уже нечего.

    Лент две породы, и путь у них разный:

    * **TikTok** — всё есть в дереве: и счётчик лайков, и кнопка «Поделиться»,
      и нижние вкладки. Но дерево снимается только НА ПАУЗЕ, а тапать надо по
      уже ИГРАЮЩЕМУ ролику: с паузы первый тап уходит на возобновление. Это
      находка с комментариев, и порядок здесь тот же.
    * **Shorts и Reels** — правой колонки кнопок в дереве нет вовсе (проверено
      живьём), зато дерево снимается и на ходу, так что паузы не нужно. Там
      число лайков читает зрение, а по кнопкам агент бьёт ТОЧКАМИ из
      `recipes.json`. Держится это не на вере в координаты, а на проверках
      после каждого тапа: не открылся лист — откат, нет строки поиска — откат.
    """
    by_point = bool(cfg.get("share_point"))
    if not (cfg.get("share_selectors") or by_point):
        return False                        # для этой ленты не заведено
    if state.get("same_frames"):
        return False                        # под пальцем не видео, а окно
    if _video_playing(state) is not True:
        return False

    # Пауза нужна только там, где без неё не снимается дерево. В Shorts и
    # Reels оно снимается на ходу, а лишняя пауза — лишний тап по видео,
    # который сам по себе может открыть что угодно.
    paused = False
    if not by_point:
        for _ in range(2):
            _tap_video(state, w, h)
            if _video_playing(state) is False:
                paused = True
                break
        else:
            log.append("  хотел забрать ссылку, но пауза не сработала")
            return False

    tree = ui.dump(retries=1, tolerant=True, timeout=6) or []
    if not tree and not by_point:
        log.append("  дерево не снялось — ссылку не забрал")
        _resume_if_paused(state, w, h, log)
        return False

    counter = _pick(tree, cfg.get("like_count_selectors") or [])
    likes = _parse_count(counter.desc) if counter is not None else None
    if likes is None:
        likes = _likes_when_liked(tree, cfg)
    if likes is None and cfg.get("likes_by_vision"):
        likes = _likes_by_vision(log)
    if likes is None:
        log.append("  число лайков не прочиталось — ссылку не забираю")
        _resume_if_paused(state, w, h, log)
        return False
    if likes < config.LINK_MIN_LIKES:
        log.append(f"  ссылку не беру: лайков {likes}, "
                   f"нужно от {config.LINK_MIN_LIKES}")
        _resume_if_paused(state, w, h, log)
        return False

    share = _pick(tree, cfg.get("share_selectors") or [])
    discover = _pick(tree, cfg.get("discover_tabs") or [])
    home = _pick(tree, cfg.get("home_tabs") or [])

    # Узел, если он есть в дереве; иначе — точка из рецепта. Дальше обоими
    # распоряжается `_tap_any`, и остальному коду разница не видна.
    share_at = share if share is not None else _point(cfg, "share_point", w, h)
    route = ([discover] if discover is not None
             else _points(cfg, "search_points", w, h))
    if share_at is None or not route:
        log.append("  кнопок для ссылки нет — возвращаю ролик")
        _resume_if_paused(state, w, h, log)
        return False
    # Координаты запоминаем сейчас: после закрытия листа лента снова
    # заиграет, и дерево перестанет сниматься.
    home_at = home.center if home is not None else _point(cfg, "home_point", w, h)

    # Автор и описание достаются из ТОГО ЖЕ дерева — раз уж оно снялось,
    # это бесплатно, а список ссылок без подписи читать невесело.
    #
    # Именно селекторами, а НЕ через ui.feed_caption: тот отбирает подписи по
    # положению на экране, а здесь дерево полное, вместе с правой колонкой, и
    # в «описание» уезжала самая длинная строка оттуда — «Добавьте это видео
    # в Избранное или удалите его из Избранного». Поймано на первой же живой
    # записи.
    author = caption = ""
    node = _pick(tree, cfg.get("author_selectors") or [])
    if node is not None:
        # У каждой сети своя обёртка вокруг имени: «Профиль x» (TikTok),
        # «Фото профиля x» (Reels), «Перейти на канал "@x"» (Shorts).
        # Кавычки снимаются отдельно — иначе они уедут в подборку.
        author = re.sub(
            r"^(перейти на канал|фото профиля|профиль|go to channel|profile)\s*",
            "", node.desc, flags=re.I)
        author = author.strip().strip('"«»')
    node = _pick(tree, cfg.get("caption_selectors") or [])
    if node is not None:
        caption = node.text or node.desc
    if not (author or caption) and not cfg.get("caption_selectors"):
        caption, author, _ = ui.feed_caption(tree, screen=state.get("screen"),
                                             package=state.get("package"))
    author, caption = _clean(author)[:60], _clean(caption)[:400]

    # Снимать паузу перед листом нужно ТОЛЬКО TikTok: там первый тап после
    # паузы уходит на возобновление. У Shorts и Reels ровно наоборот — пока
    # ролик играет, дерево листа не снимается вовсе (замер 2026-08-30: у Reels
    # 0 узлов на играющем против 205 на паузе), поэтому паузу держим до конца.
    if paused and cfg.get("share_needs_playing"):
        _tap_video(state, w, h)
    _tap_any(share_at)
    human.pause(1.4, 2.2)

    # Лист «поделиться», в отличие от панели комментариев, деревом снимается:
    # проверено живьём, 93 узла. Поэтому здесь обычный селектор, а не вес кадра.
    sheet = ui.dump(retries=2, tolerant=True, timeout=10)
    link = _pick(sheet, cfg.get("link_selectors") or []) if sheet else None
    if link is None and sheet and cfg.get("link_row_fallback"):
        # Разрешаем поимённо, а не всем подряд: выбор «по месту» опирается на
        # порядок строк, и в чужом листе на этом месте может оказаться кнопка
        # ОТПРАВКИ. У YouTube проверено — там «Коп. ссылку» первая под рядом
        # значков; у TikTok и Reels пункт подписан, и запасной путь не нужен.
        link = _link_row(sheet, w)
    # Последняя преграда перед нажатием: что бы селектор ни выбрал, кнопку
    # отправки жать нельзя. Лучше не забрать ссылку, чем что-то опубликовать.
    if _is_sending(link):
        log.append("  выбралась кнопка отправки (%r) — НЕ жму, откатываюсь"
                   % ((link.desc or link.text or "")[:40]))
        link = None

    if link is None:
        log.append("  лист «поделиться» не открылся — откатываюсь")
        device.back()
        human.pause(0.6, 1.2)
        _ensure_feed(cfg, log)
        _resume_if_paused(state, w, h, log)
        return False

    ui.tap_node(link)                       # адрес ушёл в буфер обмена
    human.pause(1.0, 1.6)

    url = _read_clipboard(cfg, route, log)
    _back_to_feed(cfg, home_at, log)
    if not url:
        return False

    fresh = jobs.add_link(url, app, likes, author=author, caption=caption,
                          session=state.get("id", ""))
    log.append((f"  ссылка сохранена ({likes} лайков): {url}") if fresh
               else f"  ссылка уже была в списке: {url}")
    return True


def _plan_like(state, verdict, taste, watch, cfg, w, h, log):
    """Задумать лайк ЗАРАНЕЕ и назначить ему момент ВНУТРИ просмотра.

    Раньше лайк ставился после того, как просмотр закончился, — то есть
    всегда в одной и той же точке относительно конца ролика. Теперь момент
    разыгрывается (`human.like_moment`), а ставит его `_watch_video`.

    Возвращает `at` (через сколько секунд от появления ролика), `do` (что
    сделать) и `done` — отметку, что лайк уже поставлен. Отметка списком,
    потому что её меняет замыкание.
    """
    done = [False]
    empty = {"at": None, "do": None, "done": done}

    chance = (interests.like_probability(verdict, taste)
              if verdict else config.LIKE_PROBABILITY)
    if not human.chance(chance):
        return empty

    # Лайк — единственное действие, которое бьёт вслепую по середине экрана.
    # Если предыдущий кадр был почти таким же, лента могла и не сдвинуться, а
    # под пальцем окажется не видео, а окно приложения. Пропустить лайк
    # дёшево, открыть чужой профиль и уйти из ленты — дорого.
    if state.get("same_frames"):
        log.append("  лайк пропущен: кадр не сменился, "
                   "под тапом может быть не видео")
        return empty

    def do():
        done[0] = True
        log.append("  " + like_current(cfg, w, h))

    return {"at": human.like_moment() * watch, "do": do, "done": done}


def _rewatch_loops(verdict):
    """Сколько раз разрешено пересмотреть ролик, прежде чем листать дальше.

    Просмотр обрывался на первом повторе ВСЕГДА — то есть агент не
    пересматривал ничего и никогда. Это безусловное правило, а безусловные
    правила как раз и заметны. Человек изредка досматривает понравившийся
    ролик по второму разу.

    Только для того, что зашло: крутить по второму кругу неподходящее — это
    и непохоже на человека, и вредно, потому что время просмотра тянет
    рекомендации за собой (та же причина, по которой `DEEP_WATCH_ON_SKIPPED`
    выключен).
    """
    if verdict is not None and verdict != "интересно":
        return 0
    return 1 if human.chance(config.REWATCH_PROBABILITY) else 0


def _may_distract(state):
    """Прошло ли достаточно с прошлого отвлечения.

    Первое отвлечение тоже не сразу: сессия, начавшаяся с ухода на домашний
    экран, выглядит странно.
    """
    last = state.get("distracted_at") or state.get("started_at") or 0
    return time.time() - last >= config.DISTRACT_EVERY_SEC


def _watch_video(state, watch, log, at=None, do=None, loops=0):
    """Досмотреть ролик, но не крутить его по второму кругу.

    `at` и `do` — отложенное действие: сделать `do()`, когда с появления
    ролика прошло `at` секунд. Через это ставится лайк, чтобы он не лежал
    всегда в одной точке (см. `human.like_moment`).

    `loops` — сколько ПОВТОРНЫХ кругов разрешено, прежде чем листать дальше.

    Длину ролика площадка не отдаёт. Зато видно, когда картинка вернулась к
    той, с которой начали: короткий ролик зациклен, и это уже повтор. Сидеть
    на нём двадцать секунд — значит прокрутить его трижды, а со стороны это
    выглядит зависшим ботом.

    Чтобы не спутать зацикливание со статичным кадром (там картинка похожа
    всегда), сначала ждём движения и только потом ловим возврат.
    """
    deadline = state["shown_at"] + watch
    feed = state.get("stream")
    first = state.get("frame_size") or 0
    fired = [False]

    def maybe_moment():
        """Сделать отложенное действие (лайк), когда до него дошло время."""
        if do is None or fired[0] or at is None:
            return
        if time.time() - state["shown_at"] >= at:
            fired[0] = True
            do()

    if feed is None:
        # Кадров нет — конец ролика ловить нечем, просто досиживаем. Спим
        # кусками, а не одним махом: иначе отложенный лайк опоздал бы на весь
        # просмотр, а кнопка «Остановить» ждала бы его конца.
        while True:
            left = deadline - time.time()
            if left <= 0:
                return False
            maybe_moment()
            if abort.sleep(min(config.LOOP_CHECK_SEC, left)):
                return False

    moved = False
    seen = 0
    round_at = state["shown_at"]
    while True:
        left = deadline - time.time()
        if left <= 0:
            return False
        maybe_moment()
        if abort.sleep(min(config.LOOP_CHECK_SEC, left)):
            return False

        png = feed.frame(max_age=1.0)
        if not png:
            continue
        # Точка отсчёта берётся ЗДЕСЬ, из первого кадра потока за просмотр.
        # Раньше её приносило решение, но оно теперь снимает экран, а вес
        # снимка с весом потока несравним. Отдельный запрос к потоку в
        # решении обходился в 6 с таймаута — замерено на живой сессии.
        if not first:
            first = len(png)
            state["frame_size"] = first
            continue
        apart = abs(len(png) - first) / first
        if not moved:
            moved = apart > config.LOOP_MOVED_PCT
        elif apart < config.LOOP_SAME_PCT:
            length = time.time() - round_at        # длина ролика: круг замкнулся
            spent = time.time() - state["shown_at"]

            # Иногда человек досматривает понравившийся ролик ещё раз. Раньше
            # просмотр обрывался на первом повторе ВСЕГДА — то есть агент не
            # пересматривал ничего и никогда, а безусловные правила как раз и
            # ловятся.
            if seen < loops:
                seen += 1
                moved = False
                round_at = time.time()
                deadline = min(time.time() + length,
                               state["shown_at"] + config.DEEP_WATCH_MAX_SEC)
                log.append(f"  зашло — смотрю ещё раз ({length:.0f}с)")
                continue

            log.append(f"  ролик короткий, пошёл по второму кругу "
                       f"({spent:.0f}с) — листаю")
            return True


def _watch_speed(state, phases, log):
    """Модель тормозит подряд — перезагрузить её, один раз за сессию.

    qwen2.5-vl в LM Studio со временем начинает думать втрое дольше на тех же
    кадрах: вместо секунды выходит 3.5-4. Само проходит, но ждать этого можно
    долго. Реагируем на три медленных решения подряд, чтобы одиночный всплеск
    не дёргал модель зря, и пишем в журнал результат — по нему видно, помогло
    ли вообще.
    """
    if phases.get("модель", 0) < config.SLOW_DECISION_SEC:
        state["slow"] = 0
        return

    state["slow"] = state.get("slow", 0) + 1
    if state["slow"] < 3 or state.get("reloaded"):
        return

    state["reloaded"] = True
    state["slow"] = 0
    # Модель в облаке: перезагружать нечего, и молчать тоже нельзя — медленные
    # ответы там значат перегрузку или узкий канал, а не залипшую модель.
    if not vision.can_reload():
        log.append(f"  модель думает по {phases['модель']:.1f}с "
                   f"({vision.where()}) — перезагрузить нечего")
        return

    ok, name = vision.available()
    log.append(f"  модель думает по {phases['модель']:.1f}с — перезагружаю")
    if ok and vision.reload_model(name):
        vision.warm_up()
        log.append("  модель перезагружена")
    else:
        log.append("  перезагрузить не вышло")


def _analyzer(state, app, log):
    """Фоновый разбор кадров. Один поток: модель всё равно одна."""
    while True:
        item = state["queue"].get()
        if item is None:
            return
        path, png, (caption, author, music) = item
        try:
            data = vision.describe_frame(png, caption, author, music,
                                     shrink=state.get('shrink'))
        except vision.VisionError as e:
            # Один раз сказали — и больше не дёргаем модель до конца сессии,
            # иначе каждый кадр будет впустую ждать таймаут.
            state["blind"] = True
            log.append(f"  разбор на лету отключён: {str(e)[:80]}")
            return
        jobs.add_content(state["id"], app, path, data)
        log.append(f"  вижу: {data.get('категория', '?')} — "
                   f"{str(data.get('тема', ''))[:60]}")


def _rescue(state, goal, w, h, log):
    """Незнакомый экран: спросить модель. Тапаем только если разрешено."""
    if not config.VISION_ENABLED or state["blind"]:
        return False

    # Кадры спасателя нумеруются с 900, чтобы не смешиваться с роликами.
    path, png = _grab(state, 900 + state["rescues"])
    if path is None:
        return False
    state["rescues"] += 1

    try:
        hint = vision.rescue(png, goal=goal, screen_size=(w, h))
    except vision.VisionError as e:
        state["blind"] = True
        log.append(f"  спасатель недоступен: {str(e)[:80]}")
        return False

    log.append(f"  спасатель: {hint.get('экран', '?')} -> {hint['действие']}"
               f" ({hint.get('почему', '')})")

    if not config.VISION_MAY_TAP:
        return False
    if hint["действие"] == "tap":
        adb.tap(hint["x"], hint["y"])
        human.pause(0.8, 1.6)
        return True
    if hint["действие"] == "back":
        device.back()
        return True
    if hint["действие"] == "home":
        device.go_home()
        return True
    return False


def like_current(cfg, w, h):
    """Лайк двойным тапом по середине кадра.

    Раньше сначала искалась кнопка в дереве. От этого отказались: в ленте
    дерево не снимается никогда, а если оно вдруг снялось — значит, мы уже
    не в ленте, и тап по найденному «сердечку» уводит агента ещё дальше.
    Один раз он так открыл профиль автора и листал уже там.

    Двойной тап по центру безопасен, ПОКА под ним видео. Это условие
    выполняется не всегда: у окна «Подпишитесь на друзей» ровно в этой
    области лежит список аккаунтов, и двойной тап открывает чужой профиль
    (проверено по сохранённому кадру: окно занимает 43-1037 по ширине и
    510-1884 по высоте, а тап приходится на 432-648 / 1080-1440 — целиком
    внутрь). Поэтому решение о лайке принимает вызывающий: он знает, стоит
    ли лента на месте.
    """
    human.double_tap(*human.like_point(w, h))
    return "лайк двойным тапом"


def browse(app="tiktok", duration=None):
    """Одна сессия просмотра ленты. Возвращает текстовый отчёт."""
    cfg = _feed_config(app)
    package = cfg["package"]

    duration = duration or random.uniform(config.SESSION_MIN_SEC,
                                          config.SESSION_MAX_SEC)
    log = runlog.Log(f"сессия в {app}, план {duration/60:.1f} мин")

    # Заодно прибираемся: кадры копятся по 20 МБ за сессию и не удалялись
    # никогда. Стоит миллисекунды, поэтому отдельного расписания не заводим.
    freed = vision.tidy_up()
    if freed >= 1:
        log.append(f"убрал старые кадры и логи: {freed:.0f} МБ")

    if not device.unlock():
        log.append("не смог разблокировать телефон")
        return log.text()

    device.keep_awake(True)
    # Заводим до try: блок завершения гасит поток, а ранний выход (телефон не
    # проснулся, приложение не открылось) случается раньше, чем поток создан —
    # и вместо понятной причины сессия падала с UnboundLocalError.
    feed, shrink = None, None
    try:
        # Проверяем экран снимком, а не опросом: unlock() выше мог отчитаться
        # об успехе при погашенном экране — так уже случалось.
        if not device.ensure_awake():
            return "экран не включился — сессия отменена"

        w, h = adb.screen_size()
        if not _open_feed(cfg, log):
            return "приложение не открылось"

        # Стартовые окна: обновления, «оцените нас», разрешения.
        # Дамп с коротким таймаутом: в ленте он всё равно не снимется, а его
        # штатное ожидание идёт в счёт первого ролика — тот успевает
        # прокрутиться по второму разу, пока мы ищем несуществующее окно.
        ui.dismiss_popup(ui.dump(retries=1, tolerant=True, timeout=4))
        # Проверяем ленту ВСЕГДА, а не только когда ссылки нет. Окно на старте
        # (его закрыли строкой выше) могло увести экран, да и сама ссылка
        # приводит куда обещала не всегда — см. _open_feed.
        _ensure_feed(cfg, log)

        deadline = time.time() + duration
        watched = likes = skipped_by_taste = saved_links = 0
        taste = interests.load()

        # Поток кадров, если он выбран: даёт кадр за 0.09 с вместо 0.6-1.6 с.
        if config.FRAME_SOURCE == "stream":
            import stream as stream_mod
            try:
                feed = stream_mod.Stream().start()
                # Кадр из потока уже 720x1600, но модели всё равно дорого:
                # уменьшаем вдвое до тех же ~360x800, что и у снимка экрана.
                shrink = 2
                log.append(f"кадры из потока {config.STREAM_SIZE}")
            except RuntimeError as e:
                log.append(f"поток недоступен, снимаю экран: {str(e)[:70]}")

        # blind — зрение отвалилось, больше не дёргаем; rescues — счётчик кадров
        state = {"id": vision.new_session_id(), "blind": False, "rescues": 0,
                 "queue": queue.Queue(maxsize=4), "skipped": 0,
                 "screen": (w, h), "no_caption": 0, "package": package,
                 # когда текущий ролик появился на экране — от этой точки
                 # считается, сколько его ещё смотреть
                 "shown_at": time.time(), "popups": 0, "cfg": cfg,
                 # отпечаток предыдущего кадра и сколько раз подряд он совпал:
                 # так видно, что лента стоит на месте (см. _feed_frozen)
                 "last_sig": None, "same_frames": 0, "last_diff": 255.0,
                 # от этой точки отсчитывается выдержка между отвлечениями
                 "started_at": time.time(),
                 # темы развёрнуты в слова заранее: «политика» сама по себе
                 # в описании ролика не встретится, а «выборы» — встретится
                 # тема разворачивается в слова заранее — запасной путь,
                 # если модель отвалится посреди сессии
                 "expansions": interests.ensure_expansion(
                     taste["тема"], vision.expand_topic),
                 "stream": feed, "shrink": shrink}
        # Время суток: вечером в ленту залипают, утром листают на бегу.
        # Постоянный множитель на всю сессию — он про время, а не про
        # настроение; за колебания внутри сессии отвечает дрейф.
        state["daypart"] = human.daypart()
        # На каком по счёту ролике залипнем и досмотрим его целиком. Первое
        # залипание тоже не сразу с порога: сессия, начавшаяся с того, что
        # человек уставился в первый же ролик, выглядит странно.
        _deep_watch_rearm(state, 0)
        _leave_rearm(state, 0)
        # «Настроение» сессии: медленно блуждает вокруг единицы и тянет за
        # собой время просмотра. Начинаем не с ровной единицы — сессии
        # начинаются по-разному.
        state["mood"] = human.drift(1.0)

        # Решать по теме можно только когда модель реально отвечает: иначе
        # агент будет ждать таймаут на каждом ролике.
        decide_mode = config.VISION_DECIDE and config.VISION_CAPTURE
        if decide_mode:
            ok, why = vision.available()
            if not ok:
                decide_mode = False
                log.append(f"решения по теме выключены: {why}")
            else:
                log.append(f"решения по теме: {why} ({vision.where()})")
                # Разогрев. LM Studio выгружает модель по простою, и первый
                # запрос платит за загрузку: замерено 8.15 с против обычной
                # секунды. Без этого первый ролик сессии висел на экране
                # лишние семь секунд — заметно и на глаз, и со стороны.
                # У облака своя причина: рукопожатие TLS и проверка ключа —
                # лучше упереться в неверный ключ здесь, чем на первом ролике.
                warm = time.time()
                if vision.warm_up():
                    spent = time.time() - warm
                    if spent > 1.5:
                        log.append(f"модель разогрета за {spent:.1f}с")

        # Фоновый разбор нужен только когда решения не принимаются: в режиме
        # решений кадр разбирается синхронно, иначе решать было бы не по чему.
        worker = None
        if config.VISION_CAPTURE and config.VISION_REALTIME and not decide_mode:
            worker = threading.Thread(target=_analyzer, args=(state, app, log),
                                      daemon=True)
            worker.start()

        while time.time() < deadline:
            if abort.requested():
                log.append("остановлено")
                break
            if config.stop_requested():
                log.append("стоп-кран")
                break

            try:
                watched += 1
                verdict = None
                # Внимание не постоянно: идёт полоса быстрого пролистывания,
                # потом медленная. Раньше каждый ролик брал свои числа заново
                # и независимо от предыдущих — правдоподобные поодиночке, они
                # не складывались в правдоподобную последовательность.
                state["mood"] = human.drift(state.get("mood", 1.0))

                if decide_mode:
                    # Взгляд на ролик: столько же, сколько человеку нужно, чтобы
                    # понять, интересно ли ему. Модель отвечает за это же время.
                    verdict, skip, watch = _look_and_decide(
                        state, app, watched, taste, log)

                    if verdict == "нет контента":
                        # Лента открыта, но пустая. Лечится не тем же, чем
                        # окно поверх: перезапускать приложение четыре раза
                        # при оборванной связи — впустую жечь минуты.
                        watched -= 1
                        state["empty"] = state.get("empty", 0) + 1
                        if state["empty"] == 1:
                            log.append("  контент не грузится — жду, "
                                       "может подтянется")
                            abort.sleep(random.uniform(6, 10))
                        elif state["empty"] == 2:
                            log.append("  контента всё нет — перезапускаю "
                                       + app)
                            device.stop_app(package)
                            human.pause(1.0, 2.0)
                            _open_feed(cfg, log)
                        else:
                            log.append("  лента пуста, приложение не помогло: "
                                       "похоже, нет связи или сеть недоступна "
                                       "— заканчиваю сессию")
                            break
                        state["blank_streak"] = 0
                        _forget_frames(state)
                        state["shown_at"] = time.time()
                        continue

                    if verdict == "застряли":
                        watched -= 1
                        state["popups"] += 1
                        # Причину называем вслух: «окно поверх» и «нет
                        # контента» лечатся по-разному, и в журнале их не
                        # должно быть видно одинаково.
                        why = ("кадр не сменился, разница %.1f — похоже, окно "
                               "поверх ленты" % state["last_diff"]) \
                            if state.get("same_frames") else \
                            "описание повторяется от кадра к кадру"
                        log.append("  лента не двигается [%s]: %s"
                                   % (why, _unstick(state, package, log)))
                        _forget_frames(state)
                        state["shown_at"] = time.time()
                        # Счётчик считает помехи ПОДРЯД, а не за всю сессию:
                        # обойдённая помеха обнуляет его (см. ниже, в обычном
                        # пути). Иначе четыре РАЗНЫХ окна за час работы
                        # заканчивали сессию, хотя каждое было снято.
                        if state["popups"] >= config.STUCK_GIVE_UP:
                            log.append("  не помогает %d раз подряд — выхожу "
                                       "из сессии" % state["popups"])
                            break
                        continue

                    # Дошли сюда — значит лента живая и ролик разобран, то
                    # есть прошлая помеха снята. Обнуляем счётчик: он должен
                    # считать неудачи ПОДРЯД, иначе сессия копит их за весь
                    # прогон и заканчивается на ровном месте.
                    state["popups"] = 0

                    # Залипание: раз в 8-10 роликов человек не листает дальше,
                    # а досматривает один до конца. Без этого вся сессия
                    # состоит из отрезков одного порядка, и по времени
                    # просмотра она ровнее, чем бывает у людей.
                    if _deep_watch_due(state, watched):
                        if not skip or config.DEEP_WATCH_ON_SKIPPED:
                            watch = _deep_watch_seconds(state)
                            skip = False
                            log.append(f"  залип: досматриваю целиком "
                                       f"(до {watch:.0f}с)")
                            _deep_watch_rearm(state, watched, log)
                        # Ролик пролистывается, а залипать на неподходящем
                        # нельзя — НЕ перевзводим счётчик: залипание случится
                        # на ближайшем подходящем. Иначе на строгих вкусах,
                        # где мимо уходит почти всё, оно не сработало бы
                        # ни разу за сессию, и настройка была бы обманом.

                    if skip:
                        skipped_by_taste += 1
                        # Чужой язык листаем немедленно: тут и смотреть нечего.
                        # Остальное неподходящее держим на экране не дольше
                        # «секунд_на_неинтересное» — если разбор уложился
                        # быстрее, добираем остаток, чтобы свайп не выглядел
                        # рефлексом.
                        if "язык" not in str(state.get("why", "")):
                            left = (interests.patience_seconds(taste)
                                    * state.get("mood", 1.0)
                                    * state.get("daypart", 1.0)
                                    - (time.time() - state["shown_at"]))
                            if left > 0:
                                abort.sleep(left)
                        human.scroll_feed(w, h, "up", quick=True)
                        state["shown_at"] = time.time()
                        continue
                    # Досматриваем ОСТАТОК: ролик уже висит на экране с момента
                    # свайпа, и запуск приложения, съёмка кадра и ответ модели —
                    # это тоже просмотр. Без вычета короткий ролик успевал
                    # прокрутиться по второму-третьему разу.
                    # Кадра или модели не было — вердикта нет, смотрим как
                    # обычный человек без разбора.
                    if watch is None:
                        watch = human.dwell()
                    plan = _plan_like(state, verdict, taste, watch, cfg, w, h, log)
                    _watch_video(state, watch, log, at=plan["at"], do=plan["do"],
                                 loops=_rewatch_loops(verdict))
                else:
                    # Слепой режим: вердиктов нет, но залипание тут тем более
                    # уместно — иначе все ролики получают время из одного и
                    # того же распределения, без единого исключения.
                    if _deep_watch_due(state, watched):
                        blind_watch = _deep_watch_seconds(state)
                        log.append(f"  залип: досматриваю целиком "
                                   f"(до {blind_watch:.0f}с)")
                        _deep_watch_rearm(state, watched, log)
                    else:
                        blind_watch = (human.dwell()
                                       * state.get("mood", 1.0)
                                       * state.get("daypart", 1.0))
                    plan = _plan_like(state, None, taste, blind_watch,
                                      cfg, w, h, log)
                    _watch_video(state, blind_watch, log, at=plan["at"],
                                 do=plan["do"], loops=_rewatch_loops(None))
                    if config.VISION_CAPTURE:
                        _capture(state, app, watched, log)

                # Лайк, если он был задуман, а до его момента просмотр не
                # дожил: ролик оборвался на повторе раньше. Ставим здесь —
                # иначе задуманные лайки терялись бы на коротких роликах, и
                # их доля молча уехала бы вниз.
                if plan["do"] is not None and not plan["done"][0]:
                    plan["do"]()
                    human.pause(0.5, 1.5)
                if plan["done"][0]:
                    likes += 1

                    # Забрать адрес ролика. Только на настоящем лайке и
                    # только у заметных роликов (порог в config): заход
                    # стоит семи тапов и перехода в «Интересное» и обратно.
                    # Место выбрано специально в конце просмотра — если
                    # лента после возврата покажет другой ролик, терять
                    # уже нечего, следующим действием и так свайп.
                    if config.LINK_SAVE and _save_link(state, cfg, w, h,
                                                       log, app):
                        saved_links += 1

                # Заглянуть в комментарии. Только на том, что зашло: человек
                # не идёт читать обсуждение под роликом, который ему и так
                # неинтересен, — а ещё это лишний тап и лишние двадцать
                # секунд там, где они ничего не дают.
                # Язык обязан совпасть: читать обсуждение на языке, которого
                # не понимаешь, человек не идёт — а для ленты это ещё и
                # сигнал «мне интересно вот это», который бить не надо.
                # Язык не задан вовсе — значит и нарушать нечего.
                own_language = (state.get("matched") or {}).get("язык", True)

                if (verdict in (None, "интересно")
                        and own_language
                        and _may_read_comments(state)
                        and human.chance(config.COMMENTS_PROBABILITY)):
                    _read_comments(state, cfg, w, h, log)

                # Отвлечения разыгрываются на каждом ролике, а роликов в
                # минуту бывает под двадцать — без выдержки агент отвлекался
                # поминутно. Выдержка общая на оба вида: и пауза, и выход на
                # домашний экран — для человека это одно и то же «отвлёкся».
                if _may_distract(state) and human.chance(config.DISTRACT_PROBABILITY):
                    state["distracted_at"] = time.time()
                    secs = human.distracted()
                    log.append(f"  отвлёкся на {secs:.0f} сек")

                # Второй вид залипания: отложил телефон совсем. Не шансом, а
                # по счёту роликов — как «досмотреть целиком».
                if _leave_due(state, watched):
                    state["distracted_at"] = time.time()
                    device.go_home()
                    human.pause(3, 12)
                    # Вернуться в приложение мало: YouTube и Instagram
                    # открываются на своей главной, а не на ленте.
                    _open_feed(cfg, log)
                    log.append("  выходил из приложения и вернулся")
                    _leave_rearm(state, watched, log)

                human.scroll_feed(w, h, "up")
                state["shown_at"] = time.time()

                # Иногда человек возвращается к предыдущему видео.
                if human.chance(0.04):
                    human.scroll_feed(w, h, "down")
                    abort.sleep(human.dwell() * 0.6)
                    human.scroll_feed(w, h, "up")
                    state["shown_at"] = time.time()
                    log.append("  вернулся на видео назад")

                # Не улетели ли мы случайно в другое приложение.
                pkg, _ = adb.current_app()
                if pkg and pkg != package:
                    if not ui.dismiss_popup():
                        # Дерево не помогло — пусть посмотрит модель.
                        if not _rescue(state, f"вернуться в ленту {app}", w, h, log):
                            device.back()
                            human.pause(1, 2)

            except adb.AdbError as e:
                # Подвисший adb или не снявшийся дамп не должны убивать сессию:
                # ленту можно листать и вслепую.
                log.append(f"  сбой adb, продолжаю: {str(e)[:70]}")
                human.pause(1, 3)

        if worker and worker.is_alive():
            # Дать модели доработать очередь, но не держать сессию долго:
            # недоразобранное подберёт analyze.
            state["queue"].put(None)
            worker.join(timeout=30)

        summary = f"итог: {watched} видео, {likes} лайков"
        if saved_links:
            summary += f", {saved_links} ссылок сохранено"
        if skipped_by_taste:
            summary += f", {skipped_by_taste} пролистано не глядя"
        log.append(summary)
        if config.VISION_CAPTURE and watched:
            tail = "" if config.VISION_REALTIME else "  (разобрать: python main.py analyze)"
            if state["skipped"]:
                tail = (f"  ({state['skipped']} кадров модель не успела разобрать, "
                        "их подберёт analyze)")
            log.append(f"кадры: {vision.frames_dir(state['id'])}{tail}")
        return log.text()

    finally:
        if feed is not None:
            feed.stop()
        device.keep_awake(False)
        device.go_home()
        human.pause(1, 2)
        device.lock()
