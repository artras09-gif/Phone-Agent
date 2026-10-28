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
        "steps": [{"find": ["Профиль", "Profile", "Perfil"], "bottom": True, "exact": True}],
        # Лента играет видео — дерево экрана не снимается (живьём 2026-10-17).
        # Касание по центру ставит на паузу, тогда кнопки видны. Не помогло —
        # «Профиль» стоит справа в нижней панели (доли экрана, снято живьём).
        "pause_xy": [0.5, 0.42],
        "tap_xy": [[0.9, 0.92]],
        "own": ["Изменить профиль", "Edit profile", "Редактировать профиль", "Editar perfil"],
    },
    "reels": {
        "package": "com.instagram.android",
        "steps": [{"find": ["Профиль", "Profile", "Perfil"], "bottom": True, "exact": True}],
        # В сетке «Публикации» просмотров нет — они на вкладке Reels профиля.
        "grid_tab": ["Reels"],
        "own": ["Редактировать профиль", "Изменить профиль", "Edit profile", "Editar perfil"],
    },
    "shorts": {
        "package": "com.google.android.youtube",
        "steps": [{"find": ["Вы", "You", "Tú"], "bottom": True, "exact": True},
                  {"find": ["Перейти на канал", "View channel", "Ваш канал", "Your channel",
                            "Ver canal", "Tu canal"]}],
        "grid_tab": ["Shorts"],
        # Живьём 2026-10-17 на своём канале — «Аналитика» и «Параметры канала».
        "own": ["Параметры канала", "Channel settings", "Аналитика", "Analytics",
                "Управление видео", "Управлять видео", "Manage videos", "Настроить канал",
                "Customize channel", "Изменить канал", "Edit channel",
                # Телефон пользователя — на испанском (его снимки 2026-10-18).
                "Configuración del canal", "Estadísticas", "Gestionar vídeos",
                "Personalizar canal", "Editar canal"],
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


def _account_name(nodes, w, h):
    """Имя аккаунта в шапке своего профиля («ontwos.studio», «Ease.motion»).

    Живьём 2026-10-22: касание пришлось во всплывшее уведомление о личном
    сообщении, и Instagram переключился на ЛИЧНЫЙ аккаунт — статистика
    снялась с него. Имя в подписи альбома делает такую подмену видной."""
    # Самая верхняя подпись по центру — заголовок шапки (логин). У Instagram
    # ниже, но раньше в дереве, стоит отображаемое имя («easyease.studio» при
    # логине «ontwos.studio»), поэтому — по высоте, а не по порядку.
    names = [n for n in nodes or [] if (n.text or "").strip() and 0 < n.bounds[1] < h * 0.12
             and w * 0.2 < (n.bounds[0] + n.bounds[2]) / 2 < w * 0.8
             and len(n.text.strip()) <= 40 and not n.text.strip().isdigit()]
    return min(names, key=lambda n: n.bounds[1]).text.strip() if names else ""


def _is_own(nodes, cfg):
    words = [w.lower() for w in cfg.get("own", [])]
    return any(w in f"{n.text} {n.desc}".lower() for n in nodes or [] for w in words)


NAV_WORDS = {"главная", "home", "интересное", "discover", "друзья", "friends", "создать",
             "create", "входящие", "inbox", "профиль", "profile", "вы", "you", "подписки",
             "subscriptions", "поиск", "search", "reels", "shorts", "inicio", "amigos",
             "crear", "bandeja de entrada", "perfil", "tú", "suscripciones", "buscar"}
FOLLOWERS = re.compile(r"подписчик|followers|subscribers|фолловер|seguidores|suscriptores", re.I)
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
    if not result["why"]:
        result["account"] = _account_name(nodes, w, h)
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


# Плитки профиля (2026-10-22, снимок человека — вкладка Reels его Instagram:
# «надо чтобы мог собирать плитки вот этих экранов, где просмотры видны»).
# Под каждой плиткой — просмотры видео с самого начала, это и есть
# «за всё время». Снимаем свой профиль на вкладке с видео: экран как есть и,
# если видео больше, чем влезло, — ещё один, пролистав.
TILE_FILES = {"tiktok": "tiktok_tiles", "shorts": "youtube_tiles", "reels": "instagram_tiles"}
TILE_TITLES = {"tiktok": "TikTok — плитки профиля", "shorts": "YouTube — плитки канала (Shorts)",
               "reels": "Instagram — плитки профиля (Reels)"}


def _tile_keys(nodes, h):
    return {(v.get("title") or "", v.get("views")) for v in parse_profile(nodes, h).get("videos") or []}


def _shot_tiles(net, folder, w, h, videos):
    """Снимки плиток своего профиля. Список путей (может быть пустым)."""
    cfg = NETS[net]
    nodes = ui.dump(retries=2, tolerant=True)
    tab = _pick(nodes, cfg["grid_tab"], h, top_only=True) if cfg.get("grid_tab") else None
    if tab is not None and tab.bounds[1] > h * 0.3:
        # Сетка низко (у Instagram сверху блок «подпишитесь на кого-нибудь», у
        # YouTube — шапка канала: живьём 2026-10-22 влез один ряд плиток) —
        # подтянуть её наверх, чтобы плитки вошли в кадр. Медленно — без разгона.
        dy = tab.bounds[1] - int(h * 0.15)
        adb.shell(f"input swipe {w // 2} {int(h * 0.8)} {w // 2} {int(h * 0.8) - dy} 900", check=False)
        human.pause(2, 2.5)
        nodes = ui.dump(retries=2, tolerant=True)
    out = []
    first = _snap(folder, TILE_FILES[net])
    if first:
        out.append(first)
    seen = _tile_keys(nodes, h)
    adb.shell(f"input swipe {w // 2} {int(h * 0.8)} {w // 2} {int(h * 0.35)} 900", check=False)
    human.pause(2, 2.5)
    more = _tile_keys(ui.dump(retries=2, tolerant=True), h)
    if more - seen:
        second = _snap(folder, TILE_FILES[net] + "2")
        if second:
            out.append(second)
            # У TikTok четвёртая плитка видна только здесь. Не объединять: пока
            # листали, у видео прибавились просмотры, и оно посчиталось бы дважды.
            seen = more if len(more) > len(seen) else seen
    for _ in range(3):                                 # обратно наверх профиля
        adb.shell(f"input swipe {w // 2} {int(h * 0.3)} {w // 2} {int(h * 0.85)} 300", check=False)
        human.pause(0.6, 0.9)
    views = [v for _, v in seen if isinstance(v, int)] or \
        [v.get("views") for v in videos or [] if isinstance(v.get("views"), int)]
    if out and views:
        _note(folder, TILE_FILES[net],
              f"видно {len(views)} видео, просмотров на них {_fmt(sum(views))}")
    return out


def collect(net, log=None, shots=False, days=None, folder=None, tiles=True):
    """Снять цифры профиля одной сети. {"followers", "videos", "why"}.
    days — период снимков статистики: 1 (вчера) или 2 дня, None — как в
    приложении. folder — куда класть снимки (по умолчанию — папка дня),
    tiles — снимать ли ещё и плитки профиля."""
    cfg = NETS[net]
    say = log.append if log is not None else (lambda s: None)
    result = {"followers": None, "videos": [], "why": ""}
    try:
        _read_profile(net, cfg, result)
        # Снимки — только со СВОЕГО открытого профиля: иначе «☰ справа вверху»
        # нажалось бы на любом экране, где агент застрял.
        if shots and not result["why"] and net in SHOTS:
            folder = folder or shots_dir()
            try:
                w, h = adb.screen_size()
                if tiles:
                    try:
                        result["tiles"] = _shot_tiles(net, folder, w, h, result["videos"])
                    except (adb.AdbError, RuntimeError, OSError) as e:
                        result["tiles"] = []
                        say(f"  {TITLES[net]}: плитки снять не вышло — {str(e)[:80]}")
                result["shot"] = SHOTS[net](folder, w, h, days)
            except (adb.AdbError, RuntimeError, OSError) as e:
                result["shot"] = None
                say(f"  {TITLES[net]}: снимок статистики не вышел — {str(e)[:80]}")
            note = _notes_in(folder).get(SHOT_FILES[net])
            if note:
                say(f"  {TITLES[net]}: {note}")
            if result.get("account"):
                _note(folder, SHOT_FILES[net] + "_account", result["account"])
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
        + (f", плиток {len(result['tiles'])}" if result.get("tiles") else "")
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


def daily_dir(day=None):
    """Снимки ежедневной «статистики за вчера» — отдельно от снятых по /stats:
    сеть, что не успела посчитать вчера, переснимается, а остальные ждут."""
    day = day or dt.date.today()
    return os.path.join(os.path.dirname(config.QUEUE_DIR), "analytics", "daily", day.isoformat())


# Чьё приложение должно быть на снимке. Живьём 2026-10-22: касание по
# «Все время» вверху экрана YouTube Studio пришлось во всплывшее уведомление
# Instagram — открылась ЛИЧНАЯ ПЕРЕПИСКА, и она легла в альбом под подписью
# «YouTube Studio». Альбом уходит в Telegram, бывает — в общий чат. Поэтому
# снимок сохраняется, только если на экране то приложение, которое снимаем.
SNAP_APPS = {"tiktok": "com.zhiliaoapp.musically", "instagram": "com.instagram.android",
             "youtube_tiles": "com.google.android.youtube"}


def _snap_app(name):
    if name.startswith("youtube"):
        return SNAP_APPS["youtube_tiles"] if "tiles" in name else STUDIO
    if name.startswith("facebook"):
        return META_SUITE
    return SNAP_APPS.get(name.split("_")[0])


# Всплывашки внутри приложения: «КОЧЕР подписался(ась) на вас · Добавить в
# ответ» поверх плиток TikTok (живьём 2026-10-22, альбом пользователя). Сами
# уходят за несколько секунд — ждём, ничего не нажимая.
BANNER = re.compile(r"подписал(ся|ась|ся\(ась\))? на вас|followed you|started following|"
                    r"te sigue|comenz[óo] a seguirte|empez[óo] a seguirte|понравил|liked your|"
                    r"прокомментировал|commented|упомянул|mentioned you|отметил", re.I)


def _screen_sample():
    """Грубая выборка пикселей экрана (каждый 16-й в каждой 12-й строке) и
    доля почти чёрных среди них в нижних двух третях. None — не снялось."""
    shot = _raw_screen()
    if shot is None:
        return None
    w, h, px = shot
    sample = bytes(px[(y * w + x) * 4 + c] for y in range(0, h, 12) for x in range(0, w, 16)
                   for c in (0, 1, 2))
    low = [(y * w + x) * 4 for y in range(h // 3, h, 12) for x in range(0, w, 16)]
    dark = sum(1 for i in low if px[i] < 24 and px[i + 1] < 24 and px[i + 2] < 24) / max(1, len(low))
    return sample, dark


def _wait_clean(tiles=False, limit=10.0):
    """Перед снимком: всплывашка приложения ушла и экран перестал меняться
    (превью догрузились). Плитки — ещё и не почти сплошь чёрные: Instagram
    рисует сетку чёрными квадратами, пока не придут превью (живьём 22.10)."""
    end = time.time() + limit
    while time.time() < end:
        nodes = ui.dump(retries=1, tolerant=True)
        h = max((n.bounds[3] for n in nodes or []), default=2400) or 2400
        if not any(BANNER.search(f"{n.text} {n.desc}") and n.bounds[1] < h * 0.25 for n in nodes or []):
            break
        time.sleep(1.0)
    prev = _screen_sample()
    while prev is not None and time.time() < end:
        time.sleep(1.0)
        cur = _screen_sample()
        if cur is None:
            break
        same = sum(1 for a, b in zip(prev[0], cur[0]) if abs(a - b) < 12) / max(1, len(cur[0]))
        prev = cur
        if same > 0.985 and not (tiles and cur[1] > 0.85):
            break


def _snap(folder, name, package=None):
    """Снимок экрана в папку дня. None — не снят: в том числе если на экране
    оказалось чужое приложение (тогда — пояснение к снимку)."""
    want = package or _snap_app(name)
    front = adb.current_app()[0]
    if want and front != want:
        _note(folder, name.rstrip("2"), f"снимок не сделан: на экране оказалось другое приложение "
                                        f"({front or 'неизвестно'}) — чужой экран не отправляю")
        return None
    try:
        _wait_clean(tiles="tiles" in name)
    except (adb.AdbError, RuntimeError, OSError):
        pass                       # не дождались — снимаем как есть
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{name}.png")
    adb.screenshot(path)
    if adb.current_app()[0] != front:
        # Пока снимали, на экран вылезло другое — снимку не верим.
        os.remove(path)
        return None
    return path if os.path.isfile(path) and os.path.getsize(path) > 10_000 else None


def _installed(package):
    return bool((adb.shell(f"pm path {package}", check=False) or "").strip())


def _label(n):
    return (n.text or n.desc or "").strip()


def _clickable_at(nodes, label):
    """Самая мелкая нажимаемая область, внутри которой стоит подпись (сама
    подпись часто не нажимается). Нет такой — сама подпись."""
    cx, cy = _center(label)
    boxes = [n for n in nodes if n.clickable and n.bounds[0] <= cx <= n.bounds[2]
             and n.bounds[1] <= cy <= n.bounds[3]]
    return min(boxes, key=lambda n: (n.bounds[2] - n.bounds[0]) * (n.bounds[3] - n.bounds[1])) \
        if boxes else label


def _reading_order(nodes):
    """Узлы по порядку чтения: строками сверху вниз, в строке слева направо.
    Строка — узлы, чьи середины по высоте почти совпадают (у обведённой
    «сегодня» клетка на пару пикселей больше соседних)."""
    rows = []
    for n in sorted(nodes, key=lambda n: _center(n)[1]):
        tall = n.bounds[3] - n.bounds[1]
        if rows and abs(_center(rows[-1][0])[1] - _center(n)[1]) < max(8, tall / 3):
            rows[-1].append(n)
        else:
            rows.append([n])
    return [n for row in rows for n in sorted(row, key=lambda n: _center(n)[0])]


def _raw_screen():
    """Снимок экрана пикселями, без PIL: (ширина, высота, RGBA-байты) или None.
    `screencap` без `-p` отдаёт заголовок (12 байт, с Android 10 — 16) и
    сырые пиксели."""
    import struct
    data = adb.exec_out("screencap", timeout=30)
    if len(data) < 16:
        return None
    w, h, fmt = struct.unpack_from("<III", data, 0)
    head = len(data) - w * h * 4
    if head not in (12, 16) or fmt not in (1, 2, 5):
        return None
    px = memoryview(data)[head:]
    if fmt == 5:                                          # BGRA — переставить
        px = bytearray(px)
        px[0::4], px[2::4] = px[2::4], px[0::4]
    return w, h, px


def _wide_band(test, low=0.7):
    """Кнопка во всю ширину внизу экрана, найденная ПО ЦВЕТУ: y её середины
    или None (нет такой — значит, и кнопка не горит).

    Дереву экрана внизу верить нельзя: на Redmi оно обрезано по высоте окна
    приложения (2177 из 2400, `dumpsys window`: app=1080x2177), а TikTok
    рисует «Обновить» ниже — в дереве от неё полоска в 27 пикселей не там."""
    shot = _raw_screen()
    if shot is None:
        return None
    w, h, px = shot
    xs = range(w // 20, w - w // 20, 6)
    band = []
    for y in range(h - 1, int(h * low), -2):
        row = y * w * 4
        hits = sum(1 for x in xs if test(px[row + 4 * x], px[row + 4 * x + 1], px[row + 4 * x + 2]))
        if hits > 0.6 * len(xs):
            band.append(y)
        elif band:
            break
    if not band or band[0] - band[-1] < h * 0.015:
        return None
    return (band[0] + band[-1]) // 2


# ------------------------------------------------------- период снимков
#
# «Надо выбор, чтобы он мог показать стату за один день или за два дня»
# (2026-10-22). Как выбирается период, снято живьём на Redmi:
# - TikTok Studio → «Аналитика»: «7 дней · 28 · 60 · 365 · Другое». «Другое» —
#   календарь; один день — два касания по одной дате, затем «Обновить».
#   Даты у TikTok сдвинуты: выбрал 21 окт. — показывает 20-е, тот же
#   последний полный день, которым кончаются его «7 дней». Поэтому конец
#   диапазона — вчера. Чисел в дереве нет, только клетки: сегодня — та
#   единственная, что на пару пикселей больше (обведена кольцом).
# - Instagram «Статистика»: «30 дней ▾» → «Пользовательский диапазон» →
#   календарь с числами; одно касание — один день, два — диапазон,
#   «Применить». Последний доступный день — позавчера (21 окт. 22-го ещё
#   серое). Выбор Instagram запоминает — обычный снимок возвращает 30 дней.
# - YouTube Studio: периоды 7/28/90/365 дней, короче нет. Ближе всего —
#   «В реальном времени»: 48 часов по часам и лучшие видео за них.
#
# «Мне каждый день нужно, чтобы он отправлял стату за полный прошлый день»
# (2026-10-28). Поэтому 1 день — это ВЧЕРА, а не «последний доступный».
# Живьём на Redmi 28.10 в 23:15:
# - Instagram: 27-е (вчера) выбирается, с 28-го серые. Одно касание и
#   «Применить» дают один день (снимок 24.10 — «23 окт.» на графике).
# - TikTok отстаёт на два дня: «7 дней» подписаны «21 окт - 27 окт», но на
#   графике последняя точка — 26-е, и выбор 27-го показывает «26 окт - 26
#   окт». 22.10 в 12:23 было так же (20-е). Сегодняшнюю клетку выбрать
#   нельзя. Поэтому TikTok — последний посчитанный им день, с подписью,
#   какой именно (видно по «26 окт - 26 окт»).
# - YouTube Studio: дня в чипах нет. Зато касание последней точки графика
#   «Просмотры» за 28 дней показывает подсказку «27 окт. 2026 г.: 60» —
#   число за день. Подсказка нарисована на холсте, в дереве её нет: дату
#   на снимке видно, а проверить её программе нечем. График за 7 дней на
#   Redmi не рисуется вовсе (дважды 28.10), поэтому 28.

# «Собери стату за всё время» (2026-10-22). Периода «всё время» нет у двух
# из трёх: TikTok Studio показывает самое большее 365 дней, Instagram — 90.
# Поэтому к «всему времени» прилагаются плитки профиля: под каждым видео там
# его просмотры с самого начала.
ALL_TIME = -1
PERIOD_WORDS = {1: "вчера", 2: "2 дня", ALL_TIME: "всё время"}


def notes(day=None):
    """Пояснения к снимкам: {"tiktok": "за 19 окт", ...}."""
    return _notes_in(shots_dir(day))


def _notes_in(folder):
    try:
        with open(os.path.join(folder, "notes.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _note(folder, name, text):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "notes.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    data[name] = text
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def _span(first, last):
    """«19.10» или «19.10–20.10»."""
    return f"{first:%d.%m}" if first == last else f"{first:%d.%m}–{last:%d.%m}"


# Месяц по началу слова: «окт», «Oct», «oct.», «ene» — RU/EN/ES (телефон
# пользователя на испанском).
MONTHS = (("янв", 1), ("фев", 2), ("мар", 3), ("апр", 4), ("мая", 5), ("май", 5),
          ("июн", 6), ("июл", 7), ("авг", 8), ("сен", 9), ("окт", 10), ("ноя", 11),
          ("дек", 12), ("jan", 1), ("ene", 1), ("feb", 2), ("mar", 3), ("apr", 4),
          ("abr", 4), ("may", 5), ("jun", 6), ("jul", 7), ("aug", 8), ("ago", 8),
          ("sep", 9), ("set", 9), ("oct", 10), ("nov", 11), ("dec", 12), ("dic", 12))


def _day_of(text):
    """Дата из подписи периода: «27 окт», «Oct 27», «27 oct.». Год — ближайший
    прошедший. None — в подписи нет даты."""
    m = re.search(r"(\d{1,2})\.?\s*([^\W\d_]{3,})|([^\W\d_]{3,})\.?\s*(\d{1,2})\b", text or "")
    if not m:
        return None
    day, word = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
    month = next((n for start, n in MONTHS if word.lower().startswith(start)), None)
    today = dt.date.today()
    try:
        out = dt.date(today.year, month, int(day)) if month else None
    except ValueError:
        return None
    if out and out > today:
        out = dt.date(today.year - 1, month, int(day))
    return out


def _day_note(folder, name, day, shown, late):
    """Пояснение к снимку «за вчера»; late — почему день на снимке раньше
    вчерашнего. Какой день на нём — отметкой `<name>_day`: по ней ежедневная
    статистика решает, переснимать ли сеть позже."""
    yesterday = dt.date.today() - dt.timedelta(days=1)
    if day is not None:
        _note(folder, name + "_day", day.isoformat())
    if day == yesterday:
        return f"за вчера, {day:%d.%m}"
    if day is not None and day < yesterday:
        return f"за {day:%d.%m} — {late}"
    return f"за {shown}" if shown else "за вчера"


TT_TITLE = "TikTok Studio"
TT_OTHER = ["Другое", "Custom", "Personalizado", "Personalizar", "Otro", "Otros"]
TT_CHIP = re.compile(r"^\s*(7|28|60|365)\s*(дн|day|día|dia)", re.I)
# «14 окт - 20 окт», «Oct 14 - Oct 20», «14 oct - 20 oct».
TT_RANGE = re.compile(r"^(\d{1,2}[^-–\d]{1,10}|[^-–\d]{2,10}\d{1,2})\s[-–]\s"
                      r"(\d{1,2}[^-–\d]{1,10}|[^-–\d]{2,10}\d{1,2})$")


def _tiktok_red(r, g, b):
    """Фирменный красный TikTok (#FE2C55) — горящая «Обновить». Бледно-розовая
    (не выбрана дата) сюда не подходит."""
    return r > 200 and g < 110 and 40 < b < 150


def _tt_cells(w):
    """Клетки календаря TikTok по порядку дат и номер сегодняшней в нём.
    ([], None) — календарь не разобран."""
    for _ in range(5):
        nodes = ui.dump(retries=2, tolerant=True)
        cells = [n for n in nodes or [] if not _label(n)
                 and 0.07 * w < n.bounds[2] - n.bounds[0] < w / 7 - 6
                 and abs((n.bounds[2] - n.bounds[0]) - (n.bounds[3] - n.bounds[1])) <= 8]
        if len(cells) >= 20:
            break
        time.sleep(1.0)
    else:
        return [], None
    cells = _reading_order(cells)
    sizes = {}
    for c in cells:
        sizes[c.bounds[2] - c.bounds[0]] = sizes.get(c.bounds[2] - c.bounds[0], 0) + 1
    usual = max(sizes, key=sizes.get)
    odd = [i for i, c in enumerate(cells) if abs(c.bounds[2] - c.bounds[0] - usual) >= 3]
    if len(odd) != 1:
        return [], None
    # Проверка, что «другая» клетка и правда сегодня: она стоит в столбце
    # сегодняшнего дня недели (неделя с воскресенья или с понедельника).
    col = int(_center(cells[odd[0]])[0] // (w / 7))
    wd = dt.date.today().weekday()
    if col not in ((wd + 1) % 7, wd):
        return [], None
    return cells, odd[0]


def _tt_pick_range(days, w):
    """В открытом календаре TikTok выбрать `days` последних дней и нажать
    «Обновить». True — выбрано."""
    for back in (1, 2):              # конец — вчера; не выбирается — позавчера
        for k in (back + days - 1, back):     # начало, конец (день — дважды та же дата)
            cells, today = _tt_cells(w)      # после касания календарь сдвигается
            if today is None or today - k < 0:
                return False
            ui.tap_node(cells[today - k])
            human.pause(1.0, 1.4)
        y = _wide_band(_tiktok_red)
        if y is not None and adb.current_app()[0] != SNAP_APPS["tiktok"]:
            return False                     # по цвету — только внутри TikTok
        if y is not None:
            adb.tap(w // 2, y)
            human.pause(4, 5)
            return True
    return False


def _tt_range_text(nodes, h):
    """Подпись периода под «Основные метрики»: «19 окт - 19 окт» -> «19 окт»."""
    for n in nodes or []:
        text = _label(n)
        if n.bounds[1] < h * 0.4 and TT_RANGE.match(text):
            a, _, b = re.split(r"\s([-–])\s", text, maxsplit=1)
            return a if a.strip() == b.strip() else text
    return ""


def _tt_period(days, w, h):
    """На «Аналитике» TikTok выбрать период. Подпись периода («19 окт»; "" —
    выбран, но подписи не прочитать) или None — не вышло."""
    nodes = ui.dump(retries=2, tolerant=True)
    other = next((n for n in nodes or [] if _label(n) in TT_OTHER and n.bounds[1] < h * 0.3), None)
    if other is None:
        return None
    ui.tap_node(_clickable_at(nodes, other))
    human.pause(2, 2.5)
    if not _tt_pick_range(days, w):
        device.back()                                 # закрыть календарь
        human.pause(1, 1.5)
        return None
    return _tt_range_text(ui.dump(retries=2, tolerant=True), h)


def _tt_longest(w, h):
    """«Всё время» в TikTok Studio — самый длинный из готовых периодов (365
    дней). Ряд периодов листается вбок: длинный может быть за краем. Подпись
    периода или None."""
    for _ in range(3):
        nodes = ui.dump(retries=2, tolerant=True)
        chips = [n for n in nodes or [] if TT_CHIP.match(_label(n)) and n.bounds[1] < h * 0.35]
        top = max(chips, key=lambda n: int(TT_CHIP.match(_label(n)).group(1)), default=None)
        if top is not None and TT_CHIP.match(_label(top)).group(1) == "365":
            ui.tap_node(_clickable_at(nodes, top))
            human.pause(4, 5)
            return _tt_range_text(ui.dump(retries=2, tolerant=True), h) or "365 дней"
        if not chips:
            return None
        y = _center(chips[0])[1]
        adb.shell(f"input swipe {int(w * 0.8)} {y} {int(w * 0.25)} {y} 350", check=False)
        human.pause(1, 1.4)
    return None


def _menu_icon(nodes, w, h):
    """☰ в правом верхнем углу профиля: крайний правый значок.

    Не обязательно «нажимаемый»: живьём 2026-10-22 у TikTok ☰ — простой
    ImageView, а нажимаемой рядом оказалась «Просмотры профиля» (следы) —
    её брать нельзя. Поэтому — самый правый узел размером со значок."""
    icons = [n for n in nodes or [] if n.bounds[0] > w * 0.82 and 0 < n.bounds[1] < h * 0.12
             and w * 0.04 < n.bounds[2] - n.bounds[0] < w * 0.16
             and 0 < n.bounds[3] - n.bounds[1] < h * 0.08]
    if not icons:
        return None
    right = max(n.bounds[2] for n in icons)
    return min((n for n in icons if n.bounds[2] >= right - 4),
               key=lambda n: (not n.clickable, n.bounds[2] - n.bounds[0]))


def _shot_tiktok(folder, w, h, days=None):
    """С своего профиля: ☰ → TikTok Studio → «Статистика» → снимок."""
    menu = _menu_icon(ui.dump(retries=2, tolerant=True), w, h)
    if menu is None:
        _note(folder, "tiktok", "не нашёл меню ☰ в профиле")
        return None
    ui.tap_node(menu)
    human.pause(1.5, 2.2)
    studio = _pick(ui.dump(retries=2, tolerant=True), [TT_TITLE], h, exact=True) or \
        next((n for n in ui.dump(retries=1, tolerant=True) if _label(n) == TT_TITLE), None)
    if studio is None:
        device.back()
        _note(folder, "tiktok", "в меню профиля нет TikTok Studio")
        return None
    ui.tap_node(studio)
    human.pause(4, 5)

    def studio_title():
        return next((n for n in ui.dump(retries=2, tolerant=True)
                     if _label(n) == TT_TITLE and n.bounds[1] < h * 0.15), None)

    # Карточка «Статистика» — сразу под заголовком (её текста в дереве нет).
    title = studio_title()
    below = (title.bounds[3] if title else int(h * 0.081)) + int(h * 0.062)
    adb.tap(int(w * 0.5), below)
    human.pause(6, 7)
    nodes = ui.dump(retries=2, tolerant=True)
    if not any(TT_CHIP.match(_label(n)) or _label(n) in TT_OTHER for n in nodes or []):
        # Под заголовком оказалось не то (у других аккаунтов сверху бывает
        # баннер). Главная TikTok Studio — тоже статистика: просмотры,
        # подписчики и лайки за 7 дней. Снимаем её, а не что попало.
        if studio_title() is None:
            device.back()
            human.pause(1.5, 2)
        if studio_title() is None:
            _note(folder, "tiktok", "«Аналитика» в TikTok Studio не открылась")
            return None
        path = _snap(folder, "tiktok")
        if path:
            _note(folder, "tiktok", "главная TikTok Studio, 7 дней (раздел «Аналитика» не открылся)")
        device.back()
        return path
    span = _tt_range_text(nodes, h)
    note = f"за {span}" if span else ""
    if days == ALL_TIME:
        picked = _tt_longest(w, h)
        note = (f"за {picked} — дольше TikTok Studio не показывает; всё время — на плитках профиля"
                if picked else "период 365 дней выбрать не вышло" + (f" — показано {span}" if span else ""))
    elif days:
        picked = _tt_period(days, w, h)
        if picked is None:
            note = f"за {PERIOD_WORDS[days]} выбрать не вышло" + (f" — показано {span}" if span else "")
        elif days == 1:
            # Выбрал вчера, а TikTok его ещё не посчитал — покажет позавчера
            # (живьём 22.10 в 12:23 и 28.10 в 23:34).
            note = _day_note(folder, "tiktok", _day_of(picked), picked,
                             "TikTok Studio считает с отставанием, вчерашнего дня у него ещё нет")
        else:
            note = f"за {picked or PERIOD_WORDS[days]}"
    path = _snap(folder, "tiktok")
    if path and note:
        _note(folder, "tiktok", note)
    for _ in range(3):                                      # обратно к профилю
        device.back()
        human.pause(0.8, 1.2)
    return path


YT_TABS = ["Обзор", "Overview", "Resumen", "Descripción general", "Visión general"]
YT_REALTIME = re.compile(r"^\s*60\s*(мин|min)", re.I)


def _yt_analytics(w, h):
    """Вкладка «Аналитика» YouTube Studio — средняя внизу. True — открыта.

    Сразу после запуска касание теряется (живьём 2026-10-22 первое попало
    в загрузку и осталась «Главная»), поэтому — с проверкой и повтором."""
    for _ in range(3):
        nodes = ui.dump(retries=2, tolerant=True)
        if any(_label(n) in YT_TABS and n.bounds[1] < h * 0.2 for n in nodes or []):
            return True
        bar = [n for n in nodes or [] if n.clickable and n.bounds[1] > h * 0.85
               and n.bounds[0] <= w / 2 <= n.bounds[2] and n.bounds[2] - n.bounds[0] < w / 2]
        if bar:
            ui.tap_node(bar[0])
        else:
            low = [n.bounds[1] for n in nodes or [] if n.clickable and n.bounds[1] > h * 0.85]
            adb.tap(w // 2, (min(low) + 20) if low else int(h * 0.9))
        human.pause(4, 5)
    return False


def _yt_realtime(w, h):
    """С «Обзора» — в карточку «В реальном времени» (48 часов). True — открыта.
    Подписи карточки в дереве нет: это последняя большая карточка страницы,
    а узнаём её по переключателю «60 мин · 48 ч» внутри."""
    for _ in range(2):
        adb.shell(f"input swipe {w // 2} {int(h * 0.75)} {w // 2} {int(h * 0.25)} 400", check=False)
        human.pause(1.2, 1.6)
    nodes = ui.dump(retries=2, tolerant=True)
    cards = [n for n in nodes or [] if n.clickable and n.bounds[2] - n.bounds[0] > w * 0.8
             and n.bounds[3] - n.bounds[1] > h * 0.15 and n.bounds[1] > h * 0.08]
    for card in sorted(cards, key=lambda n: -n.bounds[1])[:2]:
        # По заголовку карточки, а не в случайное место: внутри строки видео.
        adb.tap(w // 2, card.bounds[1] + int(h * 0.03))
        human.pause(3, 4)
        if any(YT_REALTIME.match(_label(n)) for n in ui.dump(retries=2, tolerant=True) or []):
            return True
        device.back()
        human.pause(1.5, 2)
    return False


# Период в YouTube Studio выбирается не на «Обзоре», а внутри карточки
# «Просмотры»: ряд «7 ДН · 28 ДН · 90 ДН · 365 ДН · Окт. · … · 2026 · 2025 ·
# Все время» листается вбок, «Все время» — в самом конце (живьём 2026-10-22).
YT_CHIP = re.compile(r"^\s*\d+\s*(дн|d|day|días?|dias?)\b", re.I)
YT_MONTH = re.compile(r"^\s*28\s*(дн|d|day|días?|dias?)\b", re.I)
YT_LIFETIME = re.compile(r"вс[её]\s+врем|lifetime|desde siempre|desde el principio|todo el tiempo", re.I)


def _yt_views_card(w, h):
    """С «Обзора» YouTube Studio — в карточку «Просмотры». True — нажата."""
    nodes = ui.dump(retries=2, tolerant=True)
    cards = [n for n in nodes or [] if n.clickable and n.bounds[2] - n.bounds[0] > w * 0.6
             and h * 0.08 < n.bounds[1] < h * 0.4 and n.bounds[3] - n.bounds[1] > h * 0.08]
    if not cards:
        return False
    # Первая карточка ряда — «Просмотры». По заголовку, а не в середину:
    # в середине карточки — график.
    card = min(cards, key=lambda n: (n.bounds[1], n.bounds[0]))
    adb.tap(card.bounds[0] + int(w * 0.15), card.bounds[1] + int(h * 0.03))
    human.pause(3, 4)
    return True


def _yt_chart(nodes, w, h):
    """График «Просмотров»: блок во всю ширину под чипами периода, не
    нажимаемый (живьём 28.10 — 44,437–1036,987). Пока график грузится, его
    нет, и на его месте — строки «Самого популярного контента»."""
    boxes = [n for n in nodes or [] if not n.clickable and n.bounds[2] - n.bounds[0] > w * 0.85
             and h * 0.15 < n.bounds[3] - n.bounds[1] < h * 0.4 and h * 0.12 < n.bounds[1] < h * 0.4]
    return min(boxes, key=lambda n: n.bounds[1]) if boxes else None


def _yt_day(w, h):
    """«Просмотры» за 28 дней и касание последней точки графика: в подсказке
    над ним — число за последний посчитанный день. True — подсказка вызвана.

    Касание — только внутрь блока графика, и только когда он устоялся: живьём
    28.10 касание по недогруженной странице попало в строку видео и открыло
    его статистику."""
    if not _yt_views_card(w, h):
        return False
    nodes = ui.dump(retries=2, tolerant=True)
    chip = next((n for n in nodes or [] if YT_MONTH.match(_label(n)) and n.bounds[1] < h * 0.25), None)
    if chip is None:
        return False                                   # не та карточка
    ui.tap_node(chip)                                  # Studio мог запомнить другой период
    human.pause(2, 3)
    chart = None
    for _ in range(10):
        found = _yt_chart(ui.dump(retries=2, tolerant=True), w, h)
        if found is not None and chart is not None and found.bounds == chart.bounds:
            break                                      # два раза подряд на месте
        chart = found
        time.sleep(1.5)
    else:
        return False
    # У самого правого края: точки за 28 дней стоят через ~31 px, последняя —
    # в 2 px от края блока. Живьём 28.10 касание в 21 px от края подсказало
    # 26-е вместо 27-го (подсказка берёт ближайшую точку).
    adb.tap(chart.bounds[2] - 3, (chart.bounds[1] + chart.bounds[3]) // 2)
    human.pause(0.8, 1.2)
    if not any(YT_CHIP.match(_label(n)) for n in ui.dump(retries=2, tolerant=True) or []):
        device.back()                                  # ушли со страницы — вернуться
        human.pause(1.5, 2)
        return False
    return True


def _yt_lifetime(w, h):
    """«Обзор» YouTube Studio → карточка «Просмотры» → «Все время». True — выбрано."""
    if not _yt_views_card(w, h):
        return False
    for _ in range(5):
        nodes = ui.dump(retries=2, tolerant=True)
        chips = [n for n in nodes or [] if n.bounds[1] < h * 0.25
                 and (YT_CHIP.match(_label(n)) or YT_LIFETIME.search(_label(n)))]
        if not chips:
            return False                               # не та карточка
        hit = next((n for n in chips if YT_LIFETIME.search(_label(n))
                    and n.bounds[2] - n.bounds[0] > w * 0.08), None)
        if hit is not None:
            ui.tap_node(_clickable_at(nodes, hit))
            human.pause(4, 5)
            return True
        y = _center(chips[0])[1]
        adb.shell(f"input swipe {int(w * 0.85)} {y} {int(w * 0.15)} {y} 350", check=False)
        human.pause(1, 1.4)
    return False


def _shot_youtube(folder, w, h, days=None):
    """YouTube Studio → вкладка «Аналитика» (как снимок человека); за 1–2 дня —
    «В реальном времени», 48 часов."""
    if not _installed(STUDIO):
        _note(folder, "youtube", "YouTube Studio не установлен — снята страница канала")
        return _snap(folder, "youtube", "com.google.android.youtube")   # хотя бы страница канала
    # С чистого листа: иначе Studio открывается там, где его бросили
    # (живьём — внутри «Просмотров»), и вкладка «Аналитика» уже нажата.
    # Загрузки идут через приложение YouTube, Studio закрывать можно.
    device.stop_app(STUDIO)
    device.open_app(STUDIO)
    if not device.wait_for_app(STUDIO, timeout=25):
        _note(folder, "youtube", "YouTube Studio не открылся")
        return None
    human.pause(5, 6)
    if not _yt_analytics(w, h):
        path = _snap(folder, "youtube")
        if path:
            _note(folder, "youtube", "вкладка «Аналитика» не открылась — снята главная Studio")
        device.go_home()
        return path
    human.pause(2, 3)
    note = "последние 28 дней"
    if days == ALL_TIME:
        note = ("за всё время (просмотры и самое популярное)" if _yt_lifetime(w, h)
                else "«за всё время» выбрать не вышло — 28 дней")
    elif days == 1:
        if _yt_day(w, h):
            note = ("за вчера: просмотры за последний посчитанный день — число и дата в "
                    "подсказке на графике; график и список — за 28 дней")
            _note(folder, "youtube_day", "chart")
        else:
            note = "за вчера выбрать не вышло — 28 дней"
    elif days:
        note = "последние 48 часов (короче периода в YouTube Studio нет)" \
            if _yt_realtime(w, h) else f"за {PERIOD_WORDS[days]} выбрать не вышло — 28 дней"
    path = _snap(folder, "youtube")
    if path:
        _note(folder, "youtube", note)
    device.go_home()
    return path


# Живьём 2026-10-18 (Redmi, личный аккаунт): ☰ профиля → «Настройки и
# действия» → внизу раздел «Статистика и инструменты» → «Ваша панель» → в
# блоке «Статистика» строка «Просмотры» → экран «Статистика · Обзор»
# (просмотры, прирост подписчиков, график, просмотры по типу контента) — тот
# же, что «Estadísticas · Resumen» на снимке человека. У профессиональных
# аккаунтов пункт зовётся «Профессиональная панель» или сразу «Статистика».
IG_ENTRY = ["Ваша панель", "Your dashboard", "Tu panel", "Профессиональная панель",
            "Professional dashboard", "Panel profesional", "Panel para profesionales",
            "Статистика", "Insights", "Estadísticas"]
IG_VIEWS = ["Просмотры", "Views", "Visualizaciones"]
IG_INSIGHTS_TITLE = ["Статистика", "Insights", "Estadísticas"]
IG_SHEET_OK = ["Продолжить", "Continue", "Continuar"]


def _row(nodes, label):
    """Нажимаемая строка списка, внутри которой стоит подпись (сама подпись
    обычно не нажимается, а весь экран-контейнер — слишком большая цель)."""
    rows = [n for n in nodes if n.clickable and n.bounds[1] <= label.bounds[1]
            and n.bounds[3] >= label.bounds[3] and n.bounds[3] - n.bounds[1] < 400]
    return min(rows, key=lambda n: n.bounds[3] - n.bounds[1]) if rows else label


def _find_scrolling(words, w, h, swipes=8):
    """Пункт списка по точной подписи, листая вниз. (узел, дерево) или (None, дерево)."""
    nodes = []
    for _ in range(swipes + 1):
        nodes = ui.dump(retries=2, tolerant=True)
        for word in words:                                # порядок = приоритет
            hit = next((n for n in nodes if (n.text or n.desc).strip() == word
                        and n.bounds[3] > n.bounds[1] and n.bounds[1] > h * 0.08), None)
            if hit is not None:
                return hit, nodes
        adb.shell(f"input swipe {w // 2} {int(h * 0.8)} {w // 2} {int(h * 0.35)} 450", check=False)
        human.pause(1.0, 1.4)
    return None, nodes


IG_DROP = re.compile(r"\d+\s*(дн|day|día|dia)|пользовательск|custom|personaliz", re.I)
IG_CUSTOM = re.compile(r"пользовательск|custom|personaliz", re.I)
IG_MONTH = re.compile(r"^30\s*(дн|day|día|dia)|^(last|últimos|ultimos|последние)\s*30", re.I)
IG_APPLY = ["Применить", "Apply", "Aplicar"]


def _ig_days(nodes, w):
    """Числа календаря Instagram по порядку дат: [(число, клетка или None — серое)]."""
    nums = _reading_order([n for n in nodes or [] if re.fullmatch(r"\d{1,2}", (n.text or "").strip())])
    cells = [n for n in nodes or [] if n.clickable and n.bounds[2] - n.bounds[0] < w / 5]
    out = []
    for n in nums:
        cx, cy = _center(n)
        cell = next((c for c in cells if c.bounds[0] <= cx <= c.bounds[2]
                     and c.bounds[1] <= cy <= c.bounds[3]), None)
        out.append((int(n.text.strip()), cell))
    return out


def _ig_pick_range(days, w):
    """В календаре Instagram выбрать `days` последних доступных дней.
    (первый, последний) день или None.

    Календарь — сплошной ряд дат: конец прошлого месяца, потом этот. Сегодня
    — ПОСЛЕДНЕЕ вхождение сегодняшнего числа; дата клетки — сегодня минус
    расстояние до неё. Последний доступный день — последняя нажимаемая
    клетка перед сегодня."""
    today = dt.date.today()
    picked = None
    for step in range(2 if days > 1 else 1):
        seq = _ig_days(ui.dump(retries=2, tolerant=True), w)
        now = max((i for i, (d, _) in enumerate(seq) if d == today.day), default=None)
        if now is None:
            return None
        live = [i for i, (_, cell) in enumerate(seq[:now]) if cell is not None]
        if not live:
            return None
        end = live[-1]
        start = end - (days - 1)
        first = today - dt.timedelta(days=now - start)
        last = today - dt.timedelta(days=now - end)
        if start not in live or seq[start][0] != first.day or seq[end][0] != last.day:
            return None                                # ряд дат не сплошной — не трогаем
        ui.tap_node(seq[start if step == 0 else end][1])
        human.pause(1.0, 1.4)
        picked = (first, last)
    return picked


def _ig_period(days, w, h):
    """На «Статистике» Instagram выбрать период. Пояснение к снимку или None —
    не вышло. Без days — вернуть обычные 30 дней (выбор Instagram помнит)."""
    nodes = ui.dump(retries=2, tolerant=True)
    drop = next((n for n in nodes or [] if IG_DROP.search(_label(n))
                 and h * 0.1 < n.bounds[1] < h * 0.35 and n.bounds[0] > w * 0.25), None)
    if drop is None:
        return None
    if not days and IG_MONTH.search(_label(drop)):
        return "последние 30 дней"
    ui.tap_node(_clickable_at(nodes, drop))
    human.pause(1.5, 2)
    nodes = ui.dump(retries=2, tolerant=True)
    if days == ALL_TIME:
        # Готовые периоды («7 дней», «30», «90») — берём самый длинный.
        presets = [(int(re.search(r"\d+", _label(n)).group()), n) for n in nodes or []
                   if IG_DROP.search(_label(n)) and not IG_CUSTOM.search(_label(n))
                   and re.search(r"\d+", _label(n)) and n.bounds[1] > drop.bounds[3]]
        if not presets:
            device.back()
            human.pause(1, 1.5)
            return None
        most, node = max(presets, key=lambda p: p[0])
        ui.tap_node(_clickable_at(nodes, node))
        human.pause(5, 6)
        return f"последние {most} дней — дольше статистика Instagram не показывает; всё время — на плитках профиля"
    want = IG_CUSTOM if days else IG_MONTH
    option = next((n for n in nodes or [] if want.search(_label(n))
                   and n.bounds[1] > drop.bounds[3]), None)
    if option is None:
        device.back()
        human.pause(1, 1.5)
        return None
    ui.tap_node(_clickable_at(nodes, option))
    human.pause(2, 2.5)
    if not days:
        human.pause(2, 3)
        return "последние 30 дней"
    span = _ig_pick_range(days, w)
    apply = next((n for n in ui.dump(retries=2, tolerant=True) or []
                  if _label(n) in IG_APPLY and n.clickable), None)
    if span is None or apply is None:
        device.back()                                  # закрыть календарь, ничего не применяя
        human.pause(1, 1.5)
        return None
    ui.tap_node(apply)
    human.pause(5, 6)
    IG_PICKED["span"] = span
    return f"за {_span(*span)}"


IG_PICKED = {}          # какие дни выбраны в последний раз: {"span": (первый, последний)}


def _shot_instagram(folder, w, h, days=None):
    """«Статистика · Обзор» Instagram (как «Estadísticas · Resumen» у человека)."""
    nodes = ui.dump(retries=2, tolerant=True)
    menu = [n for n in nodes if n.clickable and n.bounds[1] < h * 0.12 and n.bounds[0] > w * 0.82]
    if not menu:
        _note(folder, "instagram", "не нашёл меню ☰ — снят профиль")
        return _snap(folder, "instagram")
    ui.tap_node(max(menu, key=lambda n: n.bounds[0]))       # ☰ — «Параметры»
    human.pause(2, 2.5)
    entry, nodes = _find_scrolling(IG_ENTRY, w, h)
    if entry is None:
        device.back()
        _note(folder, "instagram", "в настройках нет «Статистики» — снят профиль")
        return _snap(folder, "instagram")                    # хотя бы профиль
    ui.tap_node(_row(nodes, entry))
    human.pause(4, 5)
    backs = 2                                                # к настройкам и к профилю
    nodes = ui.dump(retries=2, tolerant=True)
    insights = (entry.text or entry.desc).strip() in IG_INSIGHTS_TITLE
    if not insights:
        # Панель. Первый раз поверх неё шторка «Посмотрите статистику…» с
        # «Продолжить» — закрываем «назад», ничего не принимая. Ссылку
        # «Выключить статистику» не трогаем никогда.
        if any((n.text or n.desc).strip() in IG_SHEET_OK and n.clickable for n in nodes):
            device.back()
            human.pause(1.2, 1.6)
            nodes = ui.dump(retries=2, tolerant=True)
        views = next((n for n in nodes if (n.text or n.desc).strip() in IG_VIEWS
                      and h * 0.15 < n.bounds[1] < h * 0.6), None)
        if views is not None:
            ui.tap_node(views)
            human.pause(6, 7)
            backs = 3
            insights = True
    if insights:
        IG_PICKED.clear()
        note = _ig_period(days, w, h)
        if note is None:
            note = (f"за {PERIOD_WORDS[days]} выбрать не вышло — обычный период" if days
                    else "")
        elif days == 1 and IG_PICKED.get("span"):
            # Вчера ещё серое — выбран последний доступный день, он раньше.
            first, last = IG_PICKED["span"]
            note = _day_note(folder, "instagram", last if first == last else None,
                             _span(first, last), "вчерашний день Instagram ещё не открыл")
        _note(folder, "instagram", note)
    else:
        _note(folder, "instagram", "статистика не открылась — снята панель")
    path = _snap(folder, "instagram")
    for _ in range(backs):
        device.back()
        human.pause(0.8, 1.2)
    return path


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
    """Снимки последнего дня: ([(путь, подпись)], дата) или ([], None).
    В подписи — период («за 19.10–20.10») или что не вышло."""
    root = os.path.join(os.path.dirname(config.QUEUE_DIR), "analytics")
    dates = sorted(d for d in os.listdir(root) if re.fullmatch(r"\d{4}-\d\d-\d\d", d)) \
        if os.path.isdir(root) else []
    for day in reversed(dates):
        items = shots_in(os.path.join(root, day))
        if items:
            return items, day
    return [], None


def shots_in(folder):
    """Снимки папки с подписями, по порядку сетей: [(путь, подпись)]."""
    said = _notes_in(folder)
    items = []
    for net in ("shorts", "reels", "facebook", "tiktok"):
        who = said.get(SHOT_FILES[net] + "_account")

        def named(title):
            # «Instagram — статистика» -> «Instagram (ontwos.studio) — статистика»
            return title.replace(" — ", f" ({who}) — ", 1) if who else title

        path = os.path.join(folder, f"{SHOT_FILES[net]}.png")
        if os.path.isfile(path):
            note = said.get(SHOT_FILES[net])
            items.append((path, named(SHOT_TITLES[net]) + (f", {note}" if note else "")))
        if net in TILE_FILES:
            note = said.get(TILE_FILES[net])
            for k, suffix in enumerate(("", "2")):
                tile = os.path.join(folder, f"{TILE_FILES[net]}{suffix}.png")
                if os.path.isfile(tile):
                    items.append((tile, named(TILE_TITLES[net]) + (" (дальше)" if k else "")
                                  + (f": {note}" if note and not k else "")))
    return items


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


def collect_all(nets=None, log=None, days=None):
    """Пройти все сети по очереди и записать. Возвращает {сеть: данные}.
    days — период снимков: 1 или 2 дня, None — как в приложениях.

    Если снимали по просьбе «/stats сейчас» — снимки и цифры уходят туда,
    откуда просили, и за тот период, что просили. Пора ежедневной
    статистики за вчера — снимается она (`daily_run`)."""
    asked = take_request()
    if asked is None and daily_due():
        return daily_run(log)
    if asked is None:
        jobs.log_event("account_stats", "попытка")      # плановая — две в день
    elif days is None:
        days = asked.get("days")
    try:
        return _collect_all(nets, log, days)
    finally:
        if asked is not None:
            _answer(asked.get("address", ""))


def _answer(address):
    try:
        import daystats
        import telegram_bot

        telegram_bot.reply_to(address, role="stats")
        try:
            if not telegram_bot.send_shots(fresh=True):
                telegram_bot.send("Снимки статистики не получились — телефон подключён "
                                  "и разблокирован? Подробности — в журнале окна.")
        finally:
            telegram_bot.set_reply(None)
    except Exception:
        pass


def _collect_all(nets=None, log=None, days=None, folder=None, fresh=True, full=True):
    """folder — куда снимать (папка дня); fresh — сначала убрать прошлые
    снимки этой папки; full — ещё плитки профилей и Meta Business Suite."""
    folder = folder or shots_dir()
    # Только свежее: в альбом не должны попасть снимки прошлой съёмки, если
    # эта где-то не удалась.
    import glob
    for old in (glob.glob(os.path.join(folder, "*.png"))
                + glob.glob(os.path.join(folder, "notes.json"))) if fresh else []:
        try:
            os.remove(old)
        except OSError:
            pass
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
            data = collect(net, log, shots=True, days=days, folder=folder, tiles=full)
            if data["followers"] is not None or data["videos"]:
                save(net, data)
            out[net] = data
        try:
            w, h = adb.screen_size()
            fb = _shot_facebook(folder, w, h) if full else None
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


def request_now(address="", days=None):
    """Попросили «/stats сейчас» — снять при первой свободной минуте телефона и
    прислать туда, откуда попросили (days — за сколько дней)."""
    os.makedirs(os.path.dirname(_request_path()), exist_ok=True)
    with open(_request_path(), "w", encoding="utf-8") as f:
        json.dump({"address": address, "at": time.time(), "days": days}, f)


def take_request():
    """Забрать просьбу ({"address", "days"}) или None."""
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
    if daily_due(now):
        return True                                   # статистика за вчера
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


# ------------------------------------------------- статистика за вчера, сама
#
# «Мне каждый день нужно, чтобы он отправлял стату за полный прошлый день»
# (2026-10-28). Время — `telegram_bot.daily_stats_time()` (по умолчанию
# config.DAILY_STATS_AT, меняется командой «/stats каждый день 10:00»).
# Если вчерашний день Instagram ещё не открыл (время поставили слишком
# раннее), он переснимается через DAILY_STATS_RETRY_MIN. Альбом уходит один
# раз: когда Instagram готов — или в последнюю попытку перед
# DAILY_STATS_UNTIL, с честной подписью, за какой день вышло.

# Чьего вчерашнего дня ждать. TikTok — нет: он отстаёт на два дня и к
# вечеру (живьём 28.10 в 23:43 — последний день 26-е), ожидание только
# задержало бы альбом. YouTube — нет: дата у него в подсказке на холсте,
# её видно на снимке, а прочесть нечем.
DAY_CHECKED = ("reels",)


def _daily_events(now):
    start = time.mktime(now.date().timetuple())
    with jobs.connect() as con:
        return con.execute("SELECT at, payload FROM events WHERE kind='stats_daily' AND at>=?"
                           + jobs._mine()[0], (start,) + jobs._mine()[1]).fetchall()


def daily_due(now=None):
    """Пора снимать статистику за вчера: время настало, сегодня альбом ещё
    не ушёл, и с прошлой попытки прошло DAILY_STATS_RETRY_MIN."""
    import telegram_bot

    now = now or dt.datetime.now()
    at = telegram_bot.daily_stats_time()
    if not at or not telegram_bot.load_settings()["token"]:
        return False
    h, m = map(int, at.split(":"))
    if now < dt.datetime.combine(now.date(), dt.time(h, m)):
        return False
    rows = _daily_events(now)
    if any(r["payload"] == "sent" for r in rows):
        return False
    last = max((r["at"] for r in rows), default=0)
    return time.mktime(now.timetuple()) - last >= config.DAILY_STATS_RETRY_MIN * 60


def _behind(folder, nets):
    """Сети, у которых снимка за вчера в папке нет."""
    said = _notes_in(folder)
    yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    return [net for net in nets
            if not os.path.isfile(os.path.join(folder, SHOT_FILES[net] + ".png"))
            or (net in DAY_CHECKED and said.get(SHOT_FILES[net] + "_day") != yesterday)]


def daily_run(log=None):
    """Одна попытка статистики за вчера. Готово — альбом в тему статистики
    (нет темы — владельцу). Возвращает {сеть: данные} снятого."""
    import telegram_bot

    now = dt.datetime.now()
    folder = daily_dir()
    nets = [n for n in config.FEED_APPS if n in SHOTS]
    first = not _daily_events(now)
    jobs.log_event("stats_daily", "try")
    todo = nets if first else _behind(folder, nets)
    for net in todo:                                  # прошлая отметка дня — не в счёт
        _note(folder, SHOT_FILES[net] + "_day", "")
    out = _collect_all(todo, log, days=1, folder=folder, fresh=first, full=False) if todo else {}
    behind = _behind(folder, nets)
    h, m = map(int, config.DAILY_STATS_UNTIL.split(":"))
    later = now + dt.timedelta(minutes=config.DAILY_STATS_RETRY_MIN)
    if behind and later <= dt.datetime.combine(now.date(), dt.time(h, m)):
        if log is not None:
            log.append("за вчера ещё не посчитали: " + ", ".join(TITLES[n] for n in behind)
                       + f" — пересниму в {later:%H:%M}")
        return out
    sent = telegram_bot.send_daily_stats(shots_in(folder), now.date() - dt.timedelta(days=1))
    jobs.log_event("stats_daily", "sent" if sent else "not sent")
    if log is not None:
        log.append("статистика за вчера отправлена" if sent else
                   "статистику за вчера отправить не вышло — попробую позже")
    return out
