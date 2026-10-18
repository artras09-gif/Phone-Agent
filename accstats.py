"""Аналитика аккаунтов во всех трёх сетях — как «Аналитика по каналу» в
YouTube Studio (просьба 2026-10-17: «статистику я имел в виду как на фото из
всех соцсетей» — на фото: просмотры, подписчики, самый популярный контент).

API у сетей для этого либо нет, либо нужна регистрация приложения и вход через
браузер. Зато на телефоне все три аккаунта уже открыты, и в профиле видно
главное: подписчиков и просмотры последних видео. Агент раз в день заходит в
профиль каждой сети (только смотрит — ничего не нажимает, кроме вкладок),
снимает цифры и кладёт снимок в базу. Отчёт сравнивает снимки: сколько
прибавилось за сутки и за 28 дней, что смотрят больше всего.

Числа берутся из дерева экрана; не нашлись — спрашиваем модель зрения по
снимку экрана. Живьём селекторы НЕ проверены: телефона под рукой не было.
"""
import datetime as dt
import json
import os
import re
import tempfile
import time

import adb
import config
import device
import human
import jobs
import ui

TITLES = {"tiktok": "TikTok", "reels": "Instagram", "shorts": "YouTube"}

# Как дойти до своего профиля. Шаг — {"find": [варианты подписи], "bottom":
# только нижняя панель, "exact": подпись целиком}. ТОЧНО и ВНИЗУ — не каприз:
# у кнопки автора ролика в ленте TikTok описание «Профиль iramsfoodstory», и
# поиск по вхождению открыл ЧУЖОЙ профиль (живьём 2026-10-17).
NETS = {
    "tiktok": {
        "package": "com.zhiliaoapp.musically",
        "steps": [{"find": ["Профиль", "Profile"], "bottom": True, "exact": True}],
        # Лента играет видео — дерево экрана не снимается (живьём 2026-10-17).
        # Касание по центру ставит на паузу, тогда кнопки видны. Не помогло —
        # «Профиль» стоит справа в нижней панели (доли экрана, снято живьём).
        "pause_xy": [0.5, 0.42],
        "tap_xy": [[0.9, 0.92]],
        "own": ["Изменить профиль", "Edit profile", "Редактировать профиль"],
    },
    "reels": {
        "package": "com.instagram.android",
        "steps": [{"find": ["Профиль", "Profile"], "bottom": True, "exact": True}],
        # В сетке «Публикации» просмотров нет — они на вкладке Reels профиля.
        "grid_tab": ["Reels"],
        "own": ["Редактировать профиль", "Изменить профиль", "Edit profile"],
    },
    "shorts": {
        "package": "com.google.android.youtube",
        "steps": [{"find": ["Вы", "You"], "bottom": True, "exact": True},
                  {"find": ["Перейти на канал", "View channel", "Ваш канал", "Your channel"]}],
        "grid_tab": ["Shorts"],
        # Живьём 2026-10-17 на своём канале — «Аналитика» и «Параметры канала».
        "own": ["Параметры канала", "Channel settings", "Аналитика", "Analytics",
                "Управление видео", "Управлять видео", "Manage videos", "Настроить канал",
                "Customize channel", "Изменить канал", "Edit channel"],
    },
}


def _pick(nodes, words, h, bottom=False, exact=False, top_only=False):
    """Кнопка по подписи: точно или по вхождению, в нужной полосе экрана."""
    found = []
    for n in nodes or []:
        if not n.clickable:
            continue
        labels = [x.strip() for x in (n.desc, n.text) if x and x.strip()]
        hit = any((lab.lower() == w.lower()) if exact else (w.lower() in lab.lower())
                  for lab in labels for w in words)
        cy = (n.bounds[1] + n.bounds[3]) / 2
        if hit and (not bottom or cy > h * 0.86) and (not top_only or cy < h * 0.85):
            found.append(n)
    return found[0] if found else None


def _settled(wait=20.0):
    """Дерево экрана, когда сетка догрузилась: у Instagram вместо плиток
    сперва серые заглушки (`clips_grid_shimmer…`, живьём держались ~16 с), и
    чисел в них нет."""
    end = time.time() + wait
    while True:
        nodes = ui.dump(retries=2, tolerant=True)
        loading = any("shimmer" in (n.rid or "").lower() or "loading" in (n.rid or "").lower()
                      for n in nodes or [])
        if not loading or time.time() > end:
            return nodes
        time.sleep(1.0)


def _is_own(nodes, cfg):
    words = [w.lower() for w in cfg.get("own", [])]
    return any(w in f"{n.text} {n.desc}".lower() for n in nodes or [] for w in words)


NAV_WORDS = {"главная", "home", "интересное", "discover", "друзья", "friends", "создать",
             "create", "входящие", "inbox", "профиль", "profile", "вы", "you", "подписки",
             "subscriptions", "поиск", "search", "reels", "shorts"}
FOLLOWERS = re.compile(r"подписчик|followers|subscribers|фолловер", re.I)
# «просмотрел» (фраза «ваш профиль просмотрел 1 человек») — НЕ просмотры видео.
VIEWS_WORD = re.compile(r"просмотр(?:а|ов)?\b|views?\b|воспроизведени|plays?\b", re.I)
JUST_NUMBER = re.compile(r"^\s*▶?\s*\d[\d   .,]*\s*(тыс\.?|млн\.?|млрд\.?|[kmbкм]\.?)?\s*$", re.I)


def parse_count(text):
    """«1,2 тыс.» -> 1200, «15.3K» -> 15300, «7 подписчиков» -> 7. None — нет числа."""
    import session
    return session._parse_count(text)


# ---------------------------------------------------------------- разбор

def _center(n):
    x1, y1, x2, y2 = n.bounds
    return (x1 + x2) / 2, (y1 + y2) / 2


def parse_profile(nodes, height=2400):
    """Подписчики и просмотры видео с экрана профиля.

    Подписчики: узел с «подписчик/followers» — число в нём самом («7
    подписчиков») или ближайшее число над ним / слева (TikTok и Instagram
    пишут число отдельной строкой над подписью).
    Видео: числа в сетке ниже шапки — по порядку чтения, сверху вниз, слева
    направо (первым — свежее, если не закреплено). Подпись рядом — название.
    """
    followers, label_y = None, None
    for n in nodes:
        text = f"{n.text} {n.desc}".strip()
        if not FOLLOWERS.search(text):
            continue
        own = parse_count(FOLLOWERS.split(text)[0])
        label_y = _center(n)[1]
        if own is not None:
            followers = own
            break
        cx, cy = _center(n)
        near = [(abs(_center(m)[0] - cx) + abs(_center(m)[1] - cy), m) for m in nodes
                if m is not n and JUST_NUMBER.match(m.text or "")
                and _center(m)[1] <= cy + 5 and abs(_center(m)[1] - cy) < 250]
        if near:
            followers = parse_count(min(near, key=lambda x: x[0])[1].text)
            break

    top = (label_y or 0) + 60
    # Верх нижней панели — по её подписям; там значки («5» у «Входящих»),
    # это не просмотры. Подписей не видно — нижние 10 % экрана.
    nav = [n.bounds[1] for n in nodes if (n.text or n.desc or "").strip().lower() in NAV_WORDS
           and _center(n)[1] > height * 0.8]
    bottom = min(nav) - 10 if nav else height * 0.9
    videos = []
    for n in nodes:
        text = (n.text or n.desc or "").strip()
        # Длинное — фраза («ваш профиль просмотрел 1 человек»), а не подпись
        # под плиткой.
        has_word = bool(VIEWS_WORD.search(text))
        if not text or len(text) > (200 if has_word else 80) \
                or _center(n)[1] < top or _center(n)[1] > bottom:
            continue
        if JUST_NUMBER.match(text) or (VIEWS_WORD.search(text) and re.search(r"\d", text)):
            views = parse_count(VIEWS_WORD.split(text)[0] if VIEWS_WORD.search(text) else text)
            if views is None:
                continue
            # YouTube: «Воспроизвести короткое видео "ывпвапавпавп, 2 просмотра"».
            quoted = re.search(r'"([^"]+)"', text)
            inner = quoted.group(1) if quoted else text
            title = inner.rsplit(",", 1)[0].strip() if VIEWS_WORD.search(inner) and "," in inner else ""
            videos.append((round(_center(n)[1] / 40), _center(n)[0], views, title))
    videos.sort()
    seen, out = set(), []
    for row, x, views, title in videos:
        key = (row, round(x / 40))
        if key in seen:
            continue
        seen.add(key)
        out.append({"views": views, "title": title})
    return {"followers": followers, "videos": out[:12]}


def _ask_vision(png_path, net):
    """Запасной путь: спросить модель по снимку экрана. {} — не вышло."""
    try:
        import vision
        with open(png_path, "rb") as f:
            png = f.read()
        answer = vision.ask(
            png, f"Это мой профиль в {TITLES[net]}. Ответь ТОЛЬКО JSON без пояснений: "
                 '{"подписчики": число, "просмотры": [числа просмотров видео в сетке по '
                 "порядку сверху вниз, слева направо]}. «1,2 тыс.» пиши как 1200.",
            max_tokens=200, temperature=0.0)
        m = re.search(r"\{.*\}", answer or "", re.S)
        data = json.loads(m.group(0)) if m else {}
        views = [int(v) for v in data.get("просмотры", []) if str(v).isdigit()]
        followers = data.get("подписчики")
        return {"followers": int(followers) if str(followers).isdigit() else None,
                "videos": [{"views": v, "title": ""} for v in views][:12]}
    except Exception:
        return {}


# --------------------------------------------------------------- сбор

def _read_profile(net, cfg, result):
    """Дойти до профиля и снять цифры в `result`. Ранний выход — причина в result."""
    device.open_app(cfg["package"])
    if not device.wait_for_app(cfg["package"], timeout=25):
        result["why"] = "приложение не открылось"
        return result
    human.pause(3, 4)
    w, h = adb.screen_size()

    def find(step, wait):
        end = time.time() + wait
        nodes = []
        while True:
            nodes = ui.dump(retries=1, tolerant=True)
            node = _pick(nodes, step["find"], h, step.get("bottom"), step.get("exact"))
            if node is not None or time.time() > end:
                return node, nodes
            time.sleep(0.7)

    for i, step in enumerate(cfg["steps"]):
        node, nodes = find(step, 8)
        tries = 0
        while node is None and tries < 3:
            tries += 1
            if ui.dismiss_popup(nodes):
                pass
            elif len(nodes or []) < 8 and cfg.get("pause_xy"):
                px, py = cfg["pause_xy"]
                adb.tap(int(px * w), int(py * h))      # играет видео — пауза, дерево оживёт
            elif nodes and i == 0:
                # Приложение вернулось на чужой экран (профиль автора, открытое
                # видео) — там нижней панели нет. Назад, к главному экрану.
                device.back()
                human.pause(1.2, 1.8)
                if adb.current_app()[0] != cfg["package"]:
                    device.open_app(cfg["package"])
                    device.wait_for_app(cfg["package"], timeout=15)
            human.pause(1.5, 2)
            node, nodes = find(step, 5)
        if node is not None:
            ui.tap_node(node)
        elif i < len(cfg.get("tap_xy", [])) and len(nodes or []) < 8:
            # Вслепую — только когда дерева нет вовсе (видео не встаёт на
            # паузу): на любом другом экране так можно попасть не туда.
            x, y = cfg["tap_xy"][i]
            adb.tap(int(x * w), int(y * h))
        else:
            result["why"] = "не нашёл кнопку профиля"
            break
        human.pause(2.5, 3.5)
    nodes = ui.dump(retries=2, tolerant=True)
    if not result["why"] and not _is_own(nodes, cfg):
        # Чужой профиль (или не профиль вовсе) — его цифры в отчёт не
        # пускаем: лучше «не снял», чем чужие миллионы подписчиков.
        result["why"] = "открылся не мой профиль — цифры не беру"
    if not result["why"] and cfg.get("grid_tab"):
        tab = _pick(nodes, cfg["grid_tab"], h, top_only=True)   # не нижняя панель
        chosen = tab is not None and any(
            n.selected and n.bounds == tab.bounds for n in nodes or [])
        if tab is not None and not chosen:
            ui.tap_node(tab)
            human.pause(2.5, 3.5)
        nodes = _settled()
        if any("context_menu" in (n.rid or "") for n in nodes or []):
            # Повторное касание вкладки Reels открывает меню сортировки
            # (живьём 2026-10-17) — закрыть, сетка под ним та же.
            device.back()
            human.pause(1, 1.5)
            nodes = _settled()
    if result["why"]:
        return result
    found = parse_profile(nodes, h)
    readable = sum(1 for n in nodes or [] if (n.text or n.desc or "").strip()) >= 8
    # Модель — только если экран не читается вовсе. Прочитали, а видео нет —
    # это честный ноль (живьём: у TikTok видео не оказалось), и модель по
    # снимку могла бы «увидеть» просмотры, которых нет.
    if not readable:
        shot = os.path.join(tempfile.gettempdir(), f"pa_profile_{net}.png")
        try:
            adb.screenshot(shot)
            seen = _ask_vision(shot, net)
            found["followers"] = found["followers"] if found["followers"] is not None \
                else seen.get("followers")
            found["videos"] = found["videos"] or seen.get("videos", [])
        except Exception:
            pass
        finally:
            try:
                os.remove(shot)
            except OSError:
                pass
    result.update(found)
    if result["followers"] is None and not result["videos"] and not readable:
        # Экран прочитан, а цифр нет — новый пустой канал (живьём: Ease.motion
        # без подписчиков и видео), это не сбой.
        result["why"] = result["why"] or "цифр на экране профиля не нашёл"


def collect(net, log=None, shots=False):
    """Снять цифры профиля одной сети. {"followers", "videos", "why"}."""
    cfg = NETS[net]
    say = log.append if log is not None else (lambda s: None)
    result = {"followers": None, "videos": [], "why": ""}
    try:
        _read_profile(net, cfg, result)
        if shots and "не мой профиль" not in result["why"] and net in SHOTS:
            try:
                w, h = adb.screen_size()
                result["shot"] = SHOTS[net](shots_dir(), w, h)
            except (adb.AdbError, RuntimeError, OSError) as e:
                result["shot"] = None
                say(f"  {TITLES[net]}: снимок статистики не вышел — {str(e)[:80]}")
    except (adb.AdbError, RuntimeError) as e:
        result["why"] = str(e)[:120]
    finally:
        # Только на рабочий стол: приложение может догружать опубликованное
        # видео, закрывать его нельзя.
        try:
            device.go_home()
        except Exception:
            pass
    say(f"  {TITLES[net]}: подписчики {result['followers']}, видео {len(result['videos'])}"
        + (", снимок есть" if result.get("shot") else "")
        + (f" — {result['why']}" if result["why"] else ""))
    return result


# ------------------------------------------------------- снимки экранов
#
# «Где сбор статистики скриншотами, как я просил?» (2026-10-17): человеку нужны
# не только цифры, а сами экраны статистики — как его снимок YouTube Studio.
# Снимаем родные экраны аналитики: YouTube Studio → «Аналитика», TikTok →
# ☰ → TikTok Studio → «Статистика»; у личного Instagram аналитики нет —
# снимаем профиль. Пути сняты живьём 2026-10-18 на Redmi.

STUDIO = "com.google.android.apps.youtube.creator"


def shots_dir(day=None):
    day = day or dt.date.today()
    return os.path.join(os.path.dirname(config.QUEUE_DIR), "analytics", day.isoformat())


def _snap(folder, name):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{name}.png")
    adb.screenshot(path)
    return path if os.path.isfile(path) and os.path.getsize(path) > 10_000 else None


def _installed(package):
    return bool((adb.shell(f"pm path {package}", check=False) or "").strip())


def _shot_tiktok(folder, w, h):
    """С своего профиля: ☰ → TikTok Studio → «Статистика» → снимок."""
    nodes = ui.dump(retries=2, tolerant=True)
    menu = [n for n in nodes if n.clickable and n.bounds[1] < h * 0.12 and n.bounds[0] > w * 0.82]
    if not menu:
        return None
    ui.tap_node(max(menu, key=lambda n: n.bounds[0]))      # крайняя правая — ☰
    human.pause(1.5, 2.2)
    studio = _pick(ui.dump(retries=2, tolerant=True), ["TikTok Studio"], h, exact=True) or \
        next((n for n in ui.dump(retries=1, tolerant=True)
              if (n.text or n.desc).strip() == "TikTok Studio"), None)
    if studio is None:
        device.back()
        return None
    ui.tap_node(studio)
    human.pause(4, 5)
    # Карточка «Статистика» — сразу под заголовком (её текста в дереве нет).
    title = next((n for n in ui.dump(retries=2, tolerant=True)
                  if (n.text or n.desc).strip() == "TikTok Studio" and n.bounds[1] < h * 0.15), None)
    below = (title.bounds[3] if title else int(h * 0.081)) + int(h * 0.062)
    adb.tap(int(w * 0.5), below)
    human.pause(6, 7)
    opened = any(w_ in (n.text or n.desc) for n in ui.dump(retries=2, tolerant=True)
                 for w_ in ("Аналитика", "Analytics", "Analíticas", "Estadísticas"))
    path = _snap(folder, "tiktok")
    for _ in range(3 if opened else 2):                     # обратно к профилю
        device.back()
        human.pause(0.8, 1.2)
    return path


def _shot_youtube(folder, w, h):
    """YouTube Studio → вкладка «Аналитика» (как снимок человека)."""
    if not _installed(STUDIO):
        return _snap(folder, "youtube")                      # хотя бы страница канала
    device.open_app(STUDIO)
    if not device.wait_for_app(STUDIO, timeout=25):
        return None
    human.pause(5, 6)
    nodes = ui.dump(retries=2, tolerant=True)
    bottom = sorted((n for n in nodes if n.clickable and n.bounds[1] > h * 0.86), key=lambda n: n.bounds[0])
    y = (bottom[0].bounds[1] + bottom[0].bounds[3]) // 2 if bottom else int(h * 0.9)
    adb.tap(int(w * 0.5), y)                                 # «Аналитика» — средняя из пяти
    human.pause(6, 7)
    path = _snap(folder, "youtube")
    device.go_home()
    return path


IG_INSIGHTS = ["Статистика", "Insights", "Estadísticas", "Estadisticas"]
IG_DASHBOARD = ["Профессиональная панель", "Панель для профессионалов", "Professional dashboard",
                "Panel para profesionales", "Panel profesional"]
IG_SEE_ALL = ["Статистика аккаунта", "Account insights", "Estadísticas de la cuenta"]


def _shot_instagram(folder, w, h):
    """Профессиональный аккаунт — «Статистика» (Insights, как на снимке человека
    2026-10-18: «Estadísticas · Resumen»). Путь: ☰ профиля → «Статистика»;
    нет — «Профессиональная панель» → «Статистика аккаунта». У личного
    аккаунта статистики нет — снимок профиля."""
    nodes = ui.dump(retries=2, tolerant=True)
    menu = [n for n in nodes if n.clickable and n.bounds[1] < h * 0.12 and n.bounds[0] > w * 0.82]
    if menu:
        ui.tap_node(max(menu, key=lambda n: n.bounds[0]))   # ☰
        human.pause(1.5, 2.2)
        item = _pick(ui.dump(retries=2, tolerant=True), IG_INSIGHTS, h, exact=True)
        if item is not None:
            ui.tap_node(item)
            human.pause(6, 7)
            path = _snap(folder, "instagram")
            device.back()
            human.pause(0.8, 1.2)
            device.back()
            return path
        device.back()                                        # закрыть меню
        human.pause(0.8, 1.2)
        nodes = ui.dump(retries=2, tolerant=True)
    pro = _pick(nodes, IG_DASHBOARD, h)
    if pro is not None:
        ui.tap_node(pro)
        human.pause(4, 5)
        more = _pick(ui.dump(retries=2, tolerant=True), IG_SEE_ALL, h)
        if more is not None:
            ui.tap_node(more)
            human.pause(5, 6)
        path = _snap(folder, "instagram")
        device.back()
        human.pause(0.8, 1.2)
        if more is not None:
            device.back()
        return path
    return _snap(folder, "instagram")


META_SUITE = "com.facebook.pages.app"
META_INSIGHTS = ["Статистика", "Insights", "Estadísticas", "Estadisticas"]
META_MORE = ["Ещё", "Еще", "More", "Más", "Menú", "Меню", "Menu"]


def _shot_facebook(folder, w, h):
    """Meta Business Suite на телефоне → «Статистика» (как «Estadísticas» на снимке
    человека). Приложения нет — пропускаем: публикаций в Facebook агент не ведёт."""
    if not _installed(META_SUITE):
        return None
    device.open_app(META_SUITE)
    if not device.wait_for_app(META_SUITE, timeout=25):
        return None
    human.pause(5, 6)
    nodes = ui.dump(retries=2, tolerant=True)
    item = _pick(nodes, META_INSIGHTS, h, exact=True)
    if item is None:
        more = _pick(nodes, META_MORE, h, exact=True)
        if more is not None:
            ui.tap_node(more)
            human.pause(2, 3)
            item = _pick(ui.dump(retries=2, tolerant=True), META_INSIGHTS, h, exact=True)
    if item is None:
        device.go_home()
        return None
    ui.tap_node(item)
    human.pause(6, 7)
    path = _snap(folder, "facebook")
    device.go_home()
    return path


SHOTS = {"tiktok": _shot_tiktok, "shorts": _shot_youtube, "reels": _shot_instagram}
SHOT_FILES = {"tiktok": "tiktok", "shorts": "youtube", "reels": "instagram", "facebook": "facebook"}
SHOT_TITLES = {"tiktok": "TikTok Studio — аналитика", "shorts": "YouTube Studio — аналитика",
               "reels": "Instagram — статистика", "facebook": "Facebook — статистика (Meta Business Suite)"}


def latest_shots():
    """Снимки последнего дня: ([(путь, подпись)], дата) или ([], None)."""
    root = os.path.join(os.path.dirname(config.QUEUE_DIR), "analytics")
    days = sorted(d for d in os.listdir(root)) if os.path.isdir(root) else []
    for day in reversed(days):
        folder = os.path.join(root, day)
        items = []
        for net in ("shorts", "reels", "facebook", "tiktok"):
            path = os.path.join(folder, f"{SHOT_FILES[net]}.png")
            if os.path.isfile(path):
                items.append((path, SHOT_TITLES[net]))
        if items:
            return items, day
    return [], None


def _table(con):
    con.execute("CREATE TABLE IF NOT EXISTS account_stats (id INTEGER PRIMARY KEY "
                "AUTOINCREMENT, at REAL, device TEXT, net TEXT, followers INTEGER, "
                "videos TEXT)")


def save(net, data, at=None):
    with jobs.connect() as con:
        _table(con)
        con.execute("INSERT INTO account_stats (at, device, net, followers, videos) "
                    "VALUES (?,?,?,?,?)",
                    (at or time.time(), jobs.DEVICE, net, data.get("followers"),
                     json.dumps(data.get("videos") or [], ensure_ascii=False)))


def collect_all(nets=None, log=None):
    """Пройти все сети по очереди и записать. Возвращает {сеть: данные}.

    Если снимали по просьбе «/stats сейчас» — снимки и цифры уходят туда,
    откуда просили."""
    asked = take_request()
    if asked is None:
        jobs.log_event("account_stats", "попытка")      # плановая — две в день
    try:
        return _collect_all(nets, log)
    finally:
        if asked is not None:
            _answer(asked.get("address", ""))


def _answer(address):
    try:
        import daystats
        import telegram_bot

        telegram_bot.reply_to(address, role="stats")
        try:
            telegram_bot.send_shots()
            telegram_bot.send(daystats.day_text())
        finally:
            telegram_bot.set_reply(None)
    except Exception:
        pass


def _collect_all(nets=None, log=None):
    if not device.unlock():
        if log is not None:
            log.append("не смог разблокировать телефон — аналитику не снял")
        return {}
    out = {}
    device.keep_awake(True)          # экран гаснет за ~30 с, а сетка грузится дольше
    try:
        for net in nets or config.FEED_APPS:
            if net not in NETS:
                continue
            data = collect(net, log, shots=True)
            if data["followers"] is not None or data["videos"]:
                save(net, data)
            out[net] = data
        try:
            w, h = adb.screen_size()
            fb = _shot_facebook(shots_dir(), w, h)
            if log is not None and fb:
                log.append("  Facebook: снимок статистики есть")
        except (adb.AdbError, RuntimeError, OSError):
            pass
    finally:
        try:
            device.keep_awake(False)
            device.lock()
        except Exception:
            pass
    return out


# --------------------------------------------------------------- отчёт

def _snapshot(con, net, before=None):
    q = "SELECT * FROM account_stats WHERE net=?" + jobs._mine()[0]
    args = [net] + list(jobs._mine()[1])
    if before is not None:
        q += " AND at<=?"
        args.append(before)
    row = con.execute(q + " ORDER BY at DESC LIMIT 1", args).fetchone()
    if not row:
        return None
    return {"at": row["at"], "followers": row["followers"],
            "videos": json.loads(row["videos"] or "[]")}


def _fmt(n):
    return f"{int(n):,}".replace(",", " ")


def _plural(n, one, few, many):
    n = abs(int(n)) % 100
    if 11 <= n <= 19:
        return many
    n %= 10
    return one if n == 1 else few if 2 <= n <= 4 else many


def _delta(now, then):
    if now is None or then is None:
        return ""
    d = int(now) - int(then)
    return f" ({'+' if d >= 0 else '−'}{_fmt(abs(d))})" if d else " (без изменений)"


def views_gain(now, before, hint=0):
    """Сколько просмотров прибавилось между снимками и сколько вышло новых видео.

    Видео в профиле не подписаны номерами, а новое встаёт ПЕРВЫМ и сдвигает
    остальные. Ищем наименьший сдвиг, при котором старые видео не «потеряли»
    просмотры (они только растут; «1,2 тыс.» округляет — допуск 5 %). Новые —
    целиком в прибавку. (прибавка, новых видео) или (None, 0) — не сопоставить.
    """
    now = [int(v) for v in now]
    before = [int(v) for v in before]
    if not now or not before:
        return None, 0
    # Сколько видео агент сам опубликовал между снимками — пробуем этот сдвиг
    # первым: вирусное новое видео иначе сошло бы за «старое без сдвига».
    order = [hint] + [k for k in range(0, len(now) + 1) if k != hint] if 0 < hint <= len(now) \
        else range(0, len(now) + 1)
    for shift in order:
        pairs = list(zip(now[shift:], before))
        if pairs and all(n >= p * 0.95 for n, p in pairs) or (not pairs and shift == len(now)):
            return sum(now[:shift]) + sum(max(0, n - p) for n, p in pairs), shift
    return None, 0


def _posted(con, net, since):
    """Сколько видео агент опубликовал в эту сеть после `since`."""
    row = con.execute("SELECT COUNT(*) c FROM jobs WHERE status='done' AND done_at>? "
                      "AND (',' || targets || ',') LIKE ?" + jobs._mine()[0],
                      (since, f"%,{net},%") + jobs._mine()[1]).fetchone()
    return row["c"] if row else 0


def report_text(now=None):
    """«Аналитика по аккаунтам» для темы статистики. Пусто — снимков ещё нет."""
    now = now or time.time()
    blocks, total_views, total_subs, known = [], 0, 0, False
    with jobs.connect() as con:
        _table(con)
        for net in config.FEED_APPS:
            last = _snapshot(con, net)
            if not last:
                continue
            day = _snapshot(con, net, before=last["at"] - 20 * 3600)
            month = _snapshot(con, net, before=last["at"] - 27 * 86400)
            title = TITLES.get(net, net)
            old = now - last["at"] > 36 * 3600
            part = [title + (f" (снято {dt.datetime.fromtimestamp(last['at']):%d.%m})" if old else "")]
            if last["followers"] is not None:
                part.append(f"Подписчики: {_fmt(last['followers'])}"
                            + _delta(last["followers"], day and day["followers"])
                            + (f", за 28 дней{_delta(last['followers'], month['followers'])}"
                               if month and month["followers"] is not None else ""))
                if day and day["followers"] is not None:
                    total_subs += last["followers"] - day["followers"]
            videos = last["videos"]
            if videos:
                views = [v["views"] for v in videos]
                gain, fresh = views_gain(views, [v["views"] for v in day["videos"]],
                                         _posted(con, net, day["at"])) \
                    if day and day["videos"] else (None, 0)
                line = (f"Просмотры видео в профиле: {_fmt(sum(views))} "
                        f"({len(videos)} {_plural(len(videos), 'видео', 'видео', 'видео')})")
                if gain is not None:
                    known = True
                    total_views += gain
                    line += f", за сутки +{_fmt(gain)}" + (
                        f" (новых видео: {fresh})" if fresh else "")
                part.append(line)
                if month and month["videos"]:
                    gain28, _ = views_gain(views, [v["views"] for v in month["videos"]])
                    if gain28 is not None:
                        part.append(f"За 28 дней: +{_fmt(gain28)} просмотров")
                top = sorted(videos, key=lambda v: -v["views"])[:3]
                part.append("Самое популярное: " + " · ".join(
                    (f"«{v['title'][:30]}» {_fmt(v['views'])}" if v["title"] else _fmt(v["views"]))
                    for v in top))
                newest = videos[0]
                part.append("Новое видео: " + (f"«{newest['title'][:30]}» — " if newest["title"] else "")
                            + f"{_fmt(newest['views'])} "
                            + _plural(newest["views"], "просмотр", "просмотра", "просмотров"))
            blocks.append("\n".join(part))
    if not blocks:
        return ""
    stamp = dt.datetime.fromtimestamp(now).strftime("%d.%m")
    head = f"📈 Аналитика по аккаунтам — {stamp}"
    if known:
        head += (f"\nЗа сутки во всех сетях: +{_fmt(total_views)} "
                 + _plural(total_views, "просмотр", "просмотра", "просмотров")
                 + f", подписчиков {'+' if total_subs >= 0 else '−'}{_fmt(abs(total_subs))}")
    return head + "\n\n" + "\n\n".join(blocks)


def _request_path():
    return os.path.join(os.path.dirname(config.QUEUE_DIR), "analytics", "requested.json")


def request_now(address=""):
    """Попросили «/stats сейчас» — снять при первой свободной минуте телефона и
    прислать туда, откуда попросили."""
    os.makedirs(os.path.dirname(_request_path()), exist_ok=True)
    with open(_request_path(), "w", encoding="utf-8") as f:
        json.dump({"address": address, "at": time.time()}, f)


def take_request():
    """Забрать просьбу (адрес, откуда просили) или None."""
    try:
        with open(_request_path(), encoding="utf-8") as f:
            data = json.load(f)
        os.remove(_request_path())
        return data
    except (OSError, ValueError):
        return None


def due(now=None):
    """Пора ли снимать аналитику: время из ACCOUNT_STATS_TIMES прошло, а сегодня
    ещё не снимали, и бот настроен. Тема статистики не обязательна: снимки
    копятся, и когда её привяжут, «за сутки» и «за 28 дней» уже будет с чем
    сравнить."""
    import telegram_bot

    now = now or dt.datetime.now()
    if not telegram_bot.load_settings()["token"]:
        return False
    if os.path.isfile(_request_path()):
        return True                                   # «/stats сейчас»
    start = time.mktime(now.date().timetuple())
    with jobs.connect() as con:
        _table(con)
        done = con.execute("SELECT COUNT(*) c FROM account_stats WHERE at>=?"
                           + jobs._mine()[0], (start,) + jobs._mine()[1]).fetchone()["c"]
        # Две попытки в день, не больше: телефон не отвечал — не дёргать
        # его каждые полминуты до полуночи.
        tried = con.execute("SELECT COUNT(*) c FROM events WHERE kind='account_stats' "
                            "AND at>=?" + jobs._mine()[0], (start,) + jobs._mine()[1]
                            ).fetchone()["c"]
    if done or tried >= 2:
        return False
    for hhmm in config.ACCOUNT_STATS_TIMES:
        h, m = map(int, hhmm.split(":"))
        if now >= dt.datetime.combine(now.date(), dt.time(h, m)):
            return True
    return False
