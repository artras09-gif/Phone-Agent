"""Telegram-приёмник: видео уходит в публикацию, ссылка — в подборку.

Два разных вида сообщений и два разных ответа на них:

* **Видео файлом** — публикация. Правила, ради которых всё и затевалось:
  сети не указаны — постим во все (указать можно в подписи: «#tiktok»,
  «reels», «шортс», указанное вырезается из текста); время не названо и
  расписание пустое — постим сразу, иначе кладём в очередь на это время.
* **Ссылка** — сохраняем в ту же подборку, что и лайкнутое с телефона, и
  ничего не публикуем. Соцсеть любая: имя определяется по домену
  (`weblink.py`), незнакомый домен тоже сохраняется — адрес нужен сам по
  себе. Можно несколько ссылок сразу и с припиской: она станет заметкой.

Ссылка проверяется РАНЬШЕ видео, но только если видео в сообщении нет:
подпись к ролику вида «взял отсюда: <адрес>» должна остаться публикацией.

Кто может писать — решает `config.BOT_OPEN`. При True отвечаем всем, каждому
в его чат (`set_reply`); «владелец» из `telegram.json` остаётся только адресом
для неспрошенного — вечерней подборки и отчётов из окна. Модели здесь нет и не
должно быть: разбор команд, сетей и времени — обычные строки и регулярки.

Только стандартная библиотека, без python-telegram-bot и requests.
"""
import datetime as dt
import json
import os
import re
import shutil
import threading
import time
import urllib.parse
import urllib.request

import abort
import config
import jobs
import weblink

API = "https://api.telegram.org"

# Как назвать сеть в подписи. Ключ — имя маршрута в recipes.json.
# Это ОСНОВЫ слов: к ним разрешено до трёх букв хвоста, чтобы ловились падежи
# («в инсту», «в тиктоке»). Поэтому «инст», а не «инстаграм».
ALIASES = {
    "tiktok": ("tiktok", "тикток", "тик-ток", "тт"),
    "shorts": ("shorts", "шорт", "youtube", "ютуб", "yt"),
    "reels": ("reels", "рилс", "рилз", "instagram", "инст", "ig"),
}

# Сколько ссылок берём из одного сообщения. Предел не от жадности:
# пересланный пост из канала легко несёт полсотни адресов в разметке, и подборка
# превратилась бы в его копию.
MAX_LINKS_PER_MESSAGE = 20

RE_TIME = re.compile(r"(?:^|\s)(?:в\s*)?([01]?\d|2[0-3])[:.]([0-5]\d)(?=\s|$)")

# Сколько Telegram отдаёт боту: файл больше 20 МБ `getFile` не выдаёт вовсе
# (ограничение Bot API, не наше). Говорим об этом сразу, а не «не смог
# скачать».
MAX_DOWNLOAD_MB = 20


# ------------------------------------------------------- настройки бота
#
# Несколько телефонов — несколько ботов (2026-10-16: «для нескольких телефонов
# нужно будет несколько ботов, продумай механику»). Бот принадлежит ТЕЛЕФОНУ:
# видео, присланное ему, публикуется на этом телефоне и больше ни на каком.
#
#   devices/<телефон>/telegram.json — свой бот телефона: токен, владелец,
#       группы и темы, отметка о выписке из облака;
#   telegram.json рядом с программой — общее: api_id/api_hash, адрес сервера
#       Bot API, и «общий бот» прежних версий. Общий бот тоже принадлежит
#       одному телефону («phone») — первому, кто его запустил: иначе два
#       телефона разыгрывали бы одного бота, и видео уезжало бы на случайный.
#
# Бота телефона опрашивает процесс этого телефона: окно, пока телефон в нём
# выбран, или служба телефона во «Флоте». Замок — на номер бота
# (`fleet.telegram_gate`), так что двое одного бота не читают.

def shared_path():
    return os.path.join(config.BASE, "telegram.json")


def _phone_path(serial=None):
    serial = config.SERIAL if serial is None else serial
    if not serial:
        return ""
    import devices
    return os.path.join(devices.ROOT, devices.slug(serial), "telegram.json")


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def settings_path():
    """Файл бота этого телефона: свой — если есть; иначе общий, если он
    ничей или этого телефона; иначе своего бота у телефона нет."""
    phone = _phone_path()
    if phone and _read(phone).get("token"):
        return phone
    shared = _read(shared_path())
    owner = str(shared.get("phone") or "")
    if not phone or not owner or owner == str(config.SERIAL):
        return shared_path()
    return phone


def load_settings():
    """Токен и владелец. Файл рядом с программой важнее переменных окружения:
    в собранном exe переменные задавать неудобно."""
    path = settings_path()
    shared = path == shared_path()
    data = {"token": config.TELEGRAM_TOKEN if shared else "",
            "owner": config.TELEGRAM_OWNER if shared else ""}
    saved = _read(path)
    if saved:
        data["token"] = str(saved.get("token") or data["token"]).strip()
        # Токен, сохранённый с мусором прежней версией, чистим и при чтении:
        # после обновления бот оживает сам, без повторной вставки.
        data["token"] = clean_token(data["token"]) or data["token"]
        data["owner"] = str(saved.get("owner") or data["owner"]).strip()
    return data


# Токен Telegram: число, двоеточие и 35 знаков. Ищем его ВНУТРИ того, что
# вставили: с токеном часто прилетают кавычки, «bot» спереди, пробел или
# невидимый символ из мессенджера. Telegram на такой адрес отвечает 404, и
# окно писало «нет связи с Telegram: HTTP Error 404» при рабочем токене
# (2026-10-14).
RE_TOKEN = re.compile(r"(\d{5,15}:[A-Za-z0-9_-]{30,50})")


def clean_token(raw):
    """Токен из вставленного текста или "" — если его там нет вовсе."""
    text = str(raw or "")
    text = re.sub(r"[\u200b-\u200f\u2060\ufeff\s]", "", text)   # невидимые и пробелы
    m = RE_TOKEN.search(text)
    return m.group(1) if m else ""


def _bot_of(token):
    """Номер бота — часть токена до двоеточия. Перевыпуск токена в @BotFather
    его не меняет, и очередь сообщений у бота та же: начать с нуля значило бы
    получить заново последнюю пачку, которую старый токен уже не успел
    подтвердить (ему отвечают 401), — и ответить на неё второй раз."""
    return str(token or "").split(":", 1)[0]


def bots():
    """Все боты программы: [{serial, path, token, username}]. serial "" — общий
    бот, ещё никому не доставшийся."""
    import devices

    out = []
    shared = _read(shared_path())
    if clean_token(shared.get("token")):
        out.append({"serial": str(shared.get("phone") or ""), "path": shared_path(),
                    "token": clean_token(shared["token"]),
                    "username": shared.get("username", "")})
    root = devices.ROOT
    for folder in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        path = os.path.join(root, folder, "telegram.json")
        data = _read(path)
        if clean_token(data.get("token")):
            out.append({"serial": str(data.get("phone") or folder), "path": path,
                        "token": clean_token(data["token"]),
                        "username": data.get("username", "")})
    return out


def taken_by(token):
    """Чей это бот, если не этого телефона: серийник другого телефона или ""."""
    me = str(config.SERIAL or "")
    mine = settings_path()
    for bot in bots():
        if _bot_of(bot["token"]) == _bot_of(token) and bot["path"] != mine \
                and bot["serial"] and bot["serial"] != me:
            return bot["serial"]
    return ""


def bind_shared():
    """Общий бот достаётся телефону, который его запустил первым."""
    if settings_path() != shared_path() or not config.SERIAL:
        return
    data = _read(shared_path())
    if data.get("token") and not data.get("phone"):
        data["phone"] = str(config.SERIAL)
        _write(shared_path(), data)


def save_settings(token, owner, api_id=None, api_hash=None, server_url=None):
    """Сохранить. api_id / api_hash / server_url — None: оставить, что было (их
    вписывают отдельно от токена, и сохранение токена не должно их стирать).

    Токен и владелец — боту ЭТОГО телефона: в его файл, если он есть или общий
    бот уже у другого телефона; иначе — в общий (один телефон — как раньше).
    """
    token = clean_token(token) if str(token or "").strip() else ""
    path = settings_path()
    data = _read(path)
    if data.get("token") and token and _bot_of(data["token"]) != _bot_of(token):
        # Другой бот — прежние группы, темы и владелец к нему не относятся.
        for key in ("groups", "username", "cloud_logged_out"):
            data.pop(key, None)
    # Владелец — это человек (его номер в Telegram), а не бот: для второго
    # бота он тот же. Иначе владельцем нового бота стал бы первый написавший.
    owner = str(owner or "").strip() or next(
        (str(_read(b["path"]).get("owner")) for b in bots() if _read(b["path"]).get("owner")), "")
    data.update(token=token, owner=owner)
    if config.SERIAL:
        data["phone"] = str(config.SERIAL)
    _write(path, data)
    if api_id is not None or api_hash is not None or server_url is not None:
        shared = _read(shared_path())
        if api_id is not None:
            shared["api_id"] = re.sub(r"\D", "", str(api_id))
        if api_hash is not None:
            shared["api_hash"] = re.sub(r"[^0-9a-fA-F]", "", str(api_hash)).lower()
        if server_url is not None:
            shared["server_url"] = _clean_url(server_url)
        _write(shared_path(), shared)
    return path


def _clean_url(raw):
    text = str(raw or "").strip().rstrip("/")
    if text and not re.match(r"^https?://", text):
        text = "http://" + text
    return text


def _saved():
    return _read(settings_path())


def _remember(**fields):
    """Дописать в файл бота (имя бота, отметки), не трогая остального."""
    path = settings_path()
    data = _read(path)
    if not data.get("token"):
        return
    data.update(fields)
    _write(path, data)


def api_keys():
    """(api_id, api_hash) с my.telegram.org — для своего сервера. Нет — ("", "")."""
    data = _read(shared_path())
    return str(data.get("api_id") or ""), str(data.get("api_hash") or "")


def server_url():
    """Адрес уже запущенного сервера Bot API, вписанный руками. Пусто — искать самим."""
    return str(_read(shared_path()).get("server_url") or "")


def api_base():
    """Куда ходит бот: свой сервер (файлы до 2 ГБ) или облако (до 20 МБ)."""
    import tgserver
    return tgserver.url() or API


def max_download_mb():
    import tgserver
    # Сервер без --local отдаёт столько же, сколько облако. Про чужой сервер,
    # найденный по адресу, режим неизвестен — считаем, что --local (ради него
    # такие и ставят), а «файл слишком большой» объясняем при скачивании.
    if tgserver.url() and tgserver.is_local() is not False:
        return 2000
    return MAX_DOWNLOAD_MB


def _token_mark(token):
    import hashlib
    return hashlib.sha1(str(token).encode()).hexdigest()[:12]


def setup_server():
    """Подключить сервер Bot API: уже запущенный — или свой, если есть ключи.

    Сначала ищем работающий (вписанный руками адрес, процесс telegram-bot-api
    на этом ПК, обычные порты) — ключи для него не нужны. Нет — поднимаем
    свой, если вписаны api_id и api_hash. Ни того ни другого — облако.

    Перед первой работой через сервер бота нужно один раз «выписать» из
    облака (logOut) — иначе Telegram не обещает доставлять ему сообщения.
    Отметка (не сам токен) хранится в файле бота, чтобы не делать это на
    каждом запуске.
    """
    import tgserver

    cfg = load_settings()
    if not cfg["token"]:
        tgserver.stop()
        return ""
    local = tgserver.discover(cfg["token"], server_url())
    if not local:
        api_id, api_hash = api_keys()
        if not (api_id and api_hash):
            tgserver.stop()
            if server_url():
                print(f"[бот] сервер Bot API по адресу {server_url()} не отвечает — "
                      "работаю через облако", flush=True)
            return ""
        local = tgserver.start(api_id, api_hash, token=cfg["token"])
    if not local:
        print(f"[бот] свой сервер Telegram не поднялся: {tgserver.why()} — работаю через облако",
              flush=True)
        return ""
    mark = _token_mark(cfg["token"])
    if _saved().get("cloud_logged_out") != mark:
        try:
            req = urllib.request.Request(f"{API}/bot{cfg['token']}/logOut", data=b"")
            with urllib.request.urlopen(req, timeout=20) as r:
                r.read()
            done = True
        except urllib.error.HTTPError:
            done = True             # уже выписан — тоже годится
        except Exception as e:
            print(f"[бот] не смог выписать бота из облака ({e}) — пробую так", flush=True)
            done = False
        if done:
            _remember(cloud_logged_out=mark)
    size = "видео до 2 ГБ" if tgserver.is_local() is not False else "без --local: видео до 20 МБ"
    print(f"[бот] сервер Telegram: {tgserver.source()} — {size}", flush=True)
    return local


# --------------------------------------------------------------- разбор

def parse_targets(text):
    """(куда постить, текст без служебных слов).

    Ничего не нашли — значит во все сети: так просил пользователь.
    """
    # Адреса на время разбора прячем. Иначе слова «instagram», «reels»,
    # «shorts» ловятся ВНУТРИ ссылки и вырезаются из неё: подпись
    # «взял отсюда: instagram.com/reel/AbC» превращалась в «https:// .com/ /AbC»
    # и уезжала в описание поста такой. Поймано охотой на баги.
    stashed = []

    def hide(match):
        stashed.append(match.group(0))
        return "\x00{}\x00".format(len(stashed) - 1)

    found, cleaned = [], weblink.RE_BARE.sub(hide, weblink.RE_URL.sub(hide, text))
    for target, words in ALIASES.items():
        for word in words:
            # Хвост в три буквы — это падежи: «в инсту», «в тиктоке». Границы
            # по не-букве, иначе «шортс:» с двоеточием не ловится.
            pattern = re.compile(rf"(?<!\w)#?{re.escape(word)}\w{{0,3}}(?!\w)",
                                 re.IGNORECASE | re.UNICODE)
            if pattern.search(cleaned):
                if target not in found:
                    found.append(target)
                cleaned = pattern.sub(" ", cleaned)

    targets = found or list(ALIASES)
    # Адреса возвращаем на место — в описание поста они должны уйти целыми.
    cleaned = re.sub(r"\x00(\d+)\x00",
                     lambda m: stashed[int(m.group(1))], cleaned)
    # После вырезанного слова остаются висячие знаки: «шортс: обзор» -> «: обзор».
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,.;:—-")
    return targets, cleaned


def parse_when(text):
    """(во сколько постить или None, текст без времени).

    Время в прошлом считаем завтрашним: «в 9:00», присланное вечером, —
    это про утро, а не про уже прошедшее сегодня.
    """
    match = RE_TIME.search(text)
    if not match:
        return None, text

    hour, minute = int(match.group(1)), int(match.group(2))
    now = dt.datetime.now()
    when = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if when <= now:
        when += dt.timedelta(days=1)
    return when, re.sub(r"\s{2,}", " ", RE_TIME.sub(" ", text, count=1)).strip()


# ------------------------------------------------------------- переписка

# Кнопки под полем ввода. Их две и ровно те, что просил пользователь: набирать
# «/links» с телефона неудобно, а больше ничего нажатием и не делается —
# публикация требует самого файла, её кнопкой не заменить.
# `is_persistent` держит клавиатуру раскрытой: без него Telegram прячет её
# после первого нажатия, и доставать её приходится значком.
BTN_LINKS = "📋 Ссылки"
BTN_POST = "📤 Постинг"
KEYBOARD = json.dumps({
    "keyboard": [[{"text": BTN_LINKS}, {"text": BTN_POST}]],
    "resize_keyboard": True,
    "is_persistent": True,
}, ensure_ascii=False)


def _call(method, params=None, timeout=70, _retry=True):
    token = load_settings()["token"]
    url = f"{api_base()}/bot{token}/{method}"
    data = urllib.parse.urlencode(params or {}).encode()
    try:
        with urllib.request.urlopen(url, data=data, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # Telegram кладёт причину в тело ответа: без него 409 «другая копия»
        # не отличить от 409 «включён webhook», а 429 — не узнать, сколько ждать.
        try:
            res = json.loads(e.read().decode("utf-8"))
        except Exception:
            res = {}
        if not isinstance(res, dict):
            res = {}
        res.update(ok=False, error=str(e))
        res.setdefault("error_code", e.code)
        wait = (res.get("parameters") or {}).get("retry_after")
        if e.code == 429 and _retry and method != "getUpdates" and wait and wait <= 30:
            # В группе Telegram пускает ~20 сообщений в минуту: подождать и
            # отправить, а не потерять ответ.
            time.sleep(float(wait))
            return _call(method, params, timeout, _retry=False)
        return res
    except Exception as e:
        return {"ok": False, "error": str(e)}


# Кому отвечаем ПРЯМО СЕЙЧАС. Бот открыт всем (`config.BOT_OPEN`), значит
# «владелец» больше не годится как единственный адрес: ответ должен уйти тому,
# кто написал. Хранить в переменной потока, а не передавать параметром, —
# потому что `send` зовут из глубины (`_save_incoming`, отчёт о публикации), и
# протаскивать chat_id через каждый вызов значило бы переписать весь модуль.
_REPLY = threading.local()


def set_reply(chat_id, thread=None, group=False):
    """Кому уйдут следующие `send` в ЭТОМ потоке. None — снова владельцу.

    `thread` — тема в группе с темами (ответ — в ту же тему), `group` — чат
    групповой: там не вешаем клавиатуру внизу (она вылезла бы у всех).
    """
    _REPLY.chat = str(chat_id) if chat_id else None
    _REPLY.thread = thread if chat_id else None
    _REPLY.group = bool(group) if chat_id else False
    _REPLY.role = None


def current_chat():
    return getattr(_REPLY, "chat", None)


def reply_address():
    """Куда отвечать — строкой для задачи: «чат» или «чат:тема»."""
    chat = current_chat()
    if not chat:
        return ""
    thread = current_thread()
    return f"{chat}:{thread}" if thread else chat


def reply_to(address, role="posts"):
    """Обратно из строки задачи. Пустая — владельцу, как раньше.

    У групп Telegram отрицательные номера, у людей — положительные. Задачи —
    это публикации, поэтому по умолчанию ответы идут в тему видео.
    """
    chat, _, thread = str(address or "").partition(":")
    set_reply(chat or None, thread=int(thread) if thread.isdigit() else None,
              group=chat.startswith("-"))
    _REPLY.role = role


def current_thread():
    return getattr(_REPLY, "thread", None)


def in_group():
    return getattr(_REPLY, "group", False)


# --------------------------------------------------------- группы
#
# Бот в групповом чате — тот же функционал, что в личке (просьба 2026-10-16).
# Работает только в чатах, куда его добавил владелец (или где владелец сам ему
# написал): бот открыт, и иначе любой, кто добавит его в свой чат, мог бы
# публиковать в аккаунты владельца. Список — в telegram.json, «groups».

GROUP_TYPES = ("group", "supergroup")
_ME = {}


def _me():
    """getMe — имя бота и «видит ли он все сообщения группы». Кэш."""
    if not _ME.get("username"):
        res = _call("getMe", timeout=20)
        if res.get("ok"):
            _ME.update(res["result"])
    return _ME


def _groups():
    return [dict(g, id=str(g["id"])) for g in _saved().get("groups", [])
            if isinstance(g, dict) and g.get("id")]


def allowed_groups():
    return {g["id"]: g.get("title", "") for g in _groups()}


def _write_groups(groups):
    data = _saved()
    data["groups"] = groups
    with open(settings_path(), "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def allow_group(chat_id, title=""):
    groups = _groups()
    if not any(g["id"] == str(chat_id) for g in groups):
        groups.append({"id": str(chat_id), "title": title})
        _write_groups(groups)


def drop_group(chat_id):
    groups = _groups()
    left = [g for g in groups if g["id"] != str(chat_id)]
    if len(left) != len(groups):
        _write_groups(left)


# Темы группы и что в какой делать (просьбы 2026-10-16: «хочу, чтобы он
# только в один конкретный писал», потом — «из одной темы брал видео на
# постинг, в другую отправлял ссылки, в третью постил статистику»).
#
# У каждого дела своя тема: posts — видео, черновики, отчёты о публикациях;
# links — присланные ссылки и подборка; stats — статистика после сессий и
# итог дня. Дело привязано к теме — работает только в ней и пишет только
# туда; не привязано — работает в любой теме, как раньше. Тема — номер или
# GENERAL («Общая»: у её сообщений номера нет).
GENERAL = "general"
ROLES = ("posts", "links", "stats")
ROLE_WORDS = {
    "posts": ("posts", "post", "video", "видео", "посты", "пост", "постинг"),
    "links": ("links", "link", "ссылки", "ссылка", "ссылок"),
    "stats": ("stats", "stat", "статистика", "стата", "статистику"),
}
ROLE_TITLES = {"posts": "видео на постинг", "links": "ссылки", "stats": "статистика"}


def _roles(chat_id):
    """{дело: {"topic": номер|GENERAL, "name": название}} — что куда привязано."""
    for g in _groups():
        if g["id"] == str(chat_id):
            roles = {r: v for r, v in (g.get("roles") or {}).items()
                     if r in ROLES and isinstance(v, dict) and v.get("topic")}
            if not roles and g.get("topic"):
                # v2026.10.16.9: «/here» сажал всё в одну тему.
                roles = {r: {"topic": g["topic"], "name": g.get("topic_name", "")}
                         for r in ROLES}
            return roles
    return {}


def role_home(role):
    """Где живёт дело по ВСЕМ чатам бота: (чат, тема) или None — нигде не привязано.

    Дело одно на бота, а не на каждый чат: «/here видео» в одном чате — и
    видео из других чатов бот больше не берёт (жалоба 2026-10-20: «принимает
    на постинг видео не только в том чате, в котором я ему указал»)."""
    for g in _groups():
        topic = role_topic(g["id"], role)
        if topic:
            return g["id"], topic
    return None


def role_topic(chat_id, role):
    bound = _roles(chat_id).get(role)
    return bound["topic"] if bound else None


def group_topic(chat_id):
    """Тема видео (для совместимости: так звалась единственная тема)."""
    return role_topic(chat_id, "posts")


def set_roles(chat_id, roles, topic, name=""):
    """Привязать дела к теме. topic=None — отвязать эти дела."""
    groups = _groups()
    for g in groups:
        if g["id"] != str(chat_id):
            continue
        current = _roles(chat_id)
        g.pop("topic", None)
        g.pop("topic_name", None)
        for role in roles:
            if topic:
                current[role] = {"topic": topic, "name": name}
            else:
                current.pop(role, None)
        g["roles"] = current
    _write_groups(groups)


def set_group_topic(chat_id, topic, name=""):
    """Всё в одну тему (или отовсюду, topic=None)."""
    set_roles(chat_id, ROLES, topic, name)


def groups_text():
    """Для окна: «Команда: видео → «Постинг», ссылки → «Ссылки»»."""
    out = []
    for g in _groups():
        name = g.get("title") or g["id"]
        roles = _roles(g["id"])
        if roles:
            topics = {v["topic"] for v in roles.values()}
            if len(roles) == len(ROLES) and len(topics) == 1:
                one = next(iter(roles.values()))
                name += f" (только тема «{one['name'] or 'Общая'}»)"
            else:
                name += ": " + ", ".join(
                    f"{ROLE_TITLES[r]} → «{roles[r]['name'] or 'Общая'}»"
                    for r in ROLES if r in roles)
        out.append(name)
    return out


def destinations(role):
    """Куда слать неспрошенное этого дела: [(чат, тема)] по всем группам."""
    out = []
    for g in _groups():
        topic = role_topic(g["id"], role)
        if topic:
            out.append((g["id"], topic))
    return out


def post_stats(text):
    """Статистика — в темы статистики, если они привязаны. True — ушла."""
    if not text or not load_settings()["token"]:
        return False
    sent = False
    for chat, topic in destinations("stats"):
        sent = send(text, preview=False, chat_id=chat, thread=topic) or sent
    return sent


def day_stats(slot=""):
    """Итог дня в темы статистики. Один раз на день и слот (отметка в events)."""
    if not destinations("stats"):
        return ""
    import daystats

    mark = f"{dt.date.today().isoformat()} {slot}".strip()
    with jobs.connect() as con:
        if con.execute("SELECT 1 FROM events WHERE kind='stats_day' AND payload=?",
                       (mark,)).fetchone():
            return ""
    for chat, topic in destinations("stats"):
        send_shots(chat_id=chat, thread=topic)        # экраны статистики — альбомом
    if not post_stats(daystats.day_text()):
        return ""
    jobs.log_event("stats_day", mark)
    return "итог дня отправлен в тему статистики"


def _thread_for(target, thread=None):
    """В какую тему слать: явно названную, тему дела, иначе — откуда спросили."""
    if thread:
        return None if thread == GENERAL else thread
    role = getattr(_REPLY, "role", None)
    if str(target).startswith("-") and role:
        bound = role_topic(target, role)
        if bound == GENERAL:
            return None
        if bound:
            return bound
    if current_thread() and str(target) == str(current_chat()):
        return current_thread()
    return None


def _topic_name(msg):
    """Название темы — из служебного сообщения о её создании, если оно есть."""
    created = (msg.get("reply_to_message") or {}).get("forum_topic_created") or {}
    if created.get("name"):
        return created["name"]
    thread = msg.get("message_thread_id")
    return f"тема №{thread}" if thread else "Общая"


def _for_me(text, msg):
    """Текст из группы: (без «@бот», обращались ли к боту, адресовано ДРУГОМУ боту).

    «/status@Videopost23bot» — команда нам, «/status@другой_бот» — не нам.
    Упоминание «@бот» вырезаем: в подписи к видео оно стало бы описанием поста.
    """
    name = str(_me().get("username") or "").lower()
    addressed = False
    if text.startswith("/"):
        cmd, _, rest = text.partition(" ")
        if "@" in cmd:
            base, _, at = cmd.partition("@")
            if name and at.lower() != name:
                return text, False, True
            text = base + (" " + rest if rest else "")
        addressed = True
    if name and f"@{name}" in text.lower():
        text = re.sub(rf"@{re.escape(name)}\b", "", text, flags=re.I)
        text = re.sub(r"[ \t]{2,}", " ", text).strip()
        addressed = True
    reply = (msg.get("reply_to_message") or {}).get("from") or {}
    if name and str(reply.get("username") or "").lower() == name:
        addressed = True
    return text.strip(), addressed, False


GROUP_HELLO = ("Работаю в этом чате — как в личке:\n"
               "• ВИДЕО с подписью — опубликую (подпись = описание; сети и время — кнопками; "
               "«без ютуба» — пропустить сеть)\n"
               "• ССЫЛКУ — сохраню в подборку\n"
               "• /queue, /cancel 5, /status, /stop, /links\n"
               "• Темы: /here — всё в этой теме; /here видео, /here ссылки, "
               "/here статистика — одно дело сюда; /everywhere — во всех темах\n"
               "• /stats — статистика снимками экранов (/stats 1 — за день, /stats 2 — за два, "
               "/stats все — за всё время + плитки профилей с просмотрами)\n\n"
               "Публиковать может любой участник этого чата.")
PRIVACY_HINT = ("\n\n⚠ Сейчас я вижу в этом чате только команды и ответы на мои сообщения. "
                "Чтобы видел все видео и ссылки: сделай меня администратором чата — или в "
                "@BotFather: /setprivacy → выбери меня → Disable, потом убери и снова "
                "добавь меня в чат. А пока присылай видео ОТВЕТОМ на это сообщение.")


def _membership(update):
    """Бота добавили в чат или убрали из него."""
    chat = update.get("chat") or {}
    if chat.get("type") not in GROUP_TYPES:
        return
    chat_id = str(chat.get("id"))
    status = (update.get("new_chat_member") or {}).get("status", "")
    by = str((update.get("from") or {}).get("id", ""))
    owner = str(load_settings()["owner"])
    if status in ("left", "kicked"):
        drop_group(chat_id)
        return
    if status not in ("member", "administrator"):
        return
    title = chat.get("title", "")
    was = (update.get("old_chat_member") or {}).get("status", "")
    if was in ("member", "administrator", "restricted") and chat_id in allowed_groups():
        # Уже в чате — права поменяли (сделали админом). Второе приветствие
        # ни к чему, достаточно сказать, что теперь видно всё.
        if status == "administrator" and was != "administrator":
            send("Теперь я админ — вижу в чате видео и ссылки.", chat_id=chat_id)
        return
    if owner and by == owner:
        allow_group(chat_id, title)
        hint = "" if status == "administrator" or _me().get("can_read_all_group_messages") \
            else PRIVACY_HINT
        send(GROUP_HELLO + hint, chat_id=chat_id)
    elif owner:
        send(f"Меня добавили в чат «{title}», но не ты — там я молчу. Если это твой чат, "
             "напиши там любую команду, например /start, — и я начну в нём работать.",
             chat_id=owner)


def send(text, preview=True, chat_id=None, keyboard=False, thread=None):
    """Отправить ответ. Возвращает True, если Telegram принял ВСЁ.

    Адрес выбирается по очереди: явный `chat_id` -> тот, кого сейчас
    обслуживаем (`set_reply`) -> владелец. Последнее нужно для неспрошенных
    сообщений: вечерняя подборка и отчёты из окна приходят сами по себе, и
    чата, в который отвечать, у них нет.

    Ответ важен для подборки ссылок: помечать их отправленными можно только
    после подтверждения, иначе оборванная связь съедала бы список молча.

    `preview=False` гасит развёртку ссылок: в подборке из десяти адресов
    Telegram иначе пытается развернуть первый и растягивает сообщение на
    экран, а нужен список.
    """
    cfg = load_settings()
    target = chat_id or current_chat() or cfg["owner"]
    if not cfg["token"] or not target:
        return False
    ok = True
    chunks = [text[i:i + 3500] for i in range(0, len(text), 3500)] or [""]
    for n, chunk in enumerate(chunks):
        params = {
            "chat_id": target,
            "text": chunk,
            "disable_web_page_preview": "true" if not preview else "false",
        }
        topic = _thread_for(target, thread)
        if topic:
            params["message_thread_id"] = topic
        # Клавиатуру вешаем на ПОСЛЕДНИЙ кусок: длинная подборка режется на
        # части, и приклей её к первому — кнопки оказались бы в середине.
        # В группе — не вешаем: она вылезла бы у всех участников.
        if keyboard and n == len(chunks) - 1 and not (in_group() and str(target) == str(current_chat())):
            params["reply_markup"] = KEYBOARD
        res = _call("sendMessage", params, timeout=20)
        ok = ok and bool(res.get("ok"))
    return ok


def send_photo(image, caption="", chat_id=None, thread=None):
    """Картинка (JPEG или PNG) с подписью. True — Telegram принял.

    multipart вручную: сторонних библиотек в проекте нет, а urllib умеет
    только формы из пар «ключ=значение».
    """
    import secrets

    cfg = load_settings()
    target = chat_id or current_chat() or cfg["owner"]
    if not cfg["token"] or not target or not image:
        return False
    kind = "png" if image[:4] == b"\x89PNG" else "jpeg"
    boundary = "----pa" + secrets.token_hex(12)
    fields = [("chat_id", target), ("caption", caption[:1000])]
    topic = _thread_for(target, thread)
    if topic:
        fields.append(("message_thread_id", topic))     # тема группы
    head = "".join(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'
        for k, v in fields)
    body = (head.encode("utf-8")
            + (f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; '
               f'filename="screen.{kind}"\r\nContent-Type: image/{kind}\r\n\r\n').encode()
            + bytes(image) + f"\r\n--{boundary}--\r\n".encode())
    req = urllib.request.Request(
        f"{api_base()}/bot{cfg['token']}/sendPhoto", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return bool(json.loads(r.read().decode("utf-8")).get("ok"))
    except Exception:
        return False


def send_album(items, chat_id=None, thread=None):
    """Несколько снимков одним сообщением (до 10). items — [(байты, подпись)].
    True — Telegram принял. Подпись первого — подпись альбома."""
    import secrets

    cfg = load_settings()
    target = chat_id or current_chat() or cfg["owner"]
    items = [(img, cap) for img, cap in items if img][:10]
    if not cfg["token"] or not target or not items:
        return False
    if len(items) == 1:
        return send_photo(items[0][0], items[0][1], chat_id=target, thread=thread)
    boundary = "----pa" + secrets.token_hex(12)
    media = [{"type": "photo", "media": f"attach://p{i}", "caption": cap[:1000]}
             for i, (_, cap) in enumerate(items)]
    fields = [("chat_id", str(target)), ("media", json.dumps(media, ensure_ascii=False))]
    topic = _thread_for(target, thread)
    if topic:
        fields.append(("message_thread_id", str(topic)))
    body = "".join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'
                   for k, v in fields).encode("utf-8")
    for i, (img, _) in enumerate(items):
        kind = "png" if img[:4] == b"\x89PNG" else "jpeg"
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="p{i}"; '
                 f'filename="shot{i}.{kind}"\r\nContent-Type: image/{kind}\r\n\r\n').encode()
        body += bytes(img) + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{api_base()}/bot{cfg['token']}/sendMediaGroup", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return bool(json.loads(r.read().decode("utf-8")).get("ok"))
    except Exception:
        return False


def send_shots(chat_id=None, thread=None, fresh=False):
    """Последние снимки экранов статистики — альбомом. True — ушли.
    fresh — только сегодняшние (только что снятые)."""
    import accstats

    items, day = accstats.latest_shots()
    if not items or (fresh and day != dt.date.today().isoformat()):
        return False
    stamp = "" if day == dt.date.today().isoformat() else f" (снято {day[8:10]}.{day[5:7]})"
    pics = []
    for path, title in items:
        try:
            with open(path, "rb") as f:
                pics.append((f.read(), title + stamp))
        except OSError:
            pass
    return send_album(pics, chat_id=chat_id, thread=thread)


def alert(text, image=None):
    """Неспрошенное сообщение ВЛАДЕЛЬЦУ — о беде, которую агент не снял сам.

    Всегда владельцу, а не «тому, кто сейчас пишет»: бот открыт всем
    (`config.BOT_OPEN`), а снимок экрана личного телефона — только хозяину.
    Снимок не ушёл (нет связи, слишком большой) — хотя бы текстом.
    """
    cfg = load_settings()
    owner = cfg["owner"]
    if not cfg["token"] or not owner:
        return False
    if image and send_photo(image, text, chat_id=owner):
        return True
    return send(text, chat_id=owner)


# ------------------------------------------------- подборка понравившегося

def _plural(n, one, few, many):
    n = abs(int(n)) % 100
    if 11 <= n <= 14:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


def _link_block(row):
    """Одна ссылка в том виде, в каком её читают с телефона.

    Адрес последней строкой: так он виден целиком и по нему удобно попасть
    пальцем. Число лайков с пробелами («230 500») — подборка отвечает на
    вопрос «стоит ли смотреть», а слитные цифры отвечают на него хуже.
    """
    # Сердечко ставится только там, где число лайков и правда известно.
    # У присланного в чат его взять неоткуда, и «♥ 0» читалось бы как «ноль
    # лайков» — то есть ровно наоборот тому, что значит.
    parts = []
    if int(row["likes"] or 0) > 0:
        parts.append("♥ " + f"{int(row['likes']):,}".replace(",", " "))
    elif (row["session"] or "") == "телеграм":
        parts.append("прислано в чат")
    if row["author"]:
        parts.append(row["author"])
    if row["app"] and row["app"] != "tiktok":
        parts.append(row["app"])
    head = " · ".join(parts) or "ссылка"

    block = [head]
    caption = (row["caption"] or "").replace("\n", " ").strip()
    if caption:
        block.append(caption[:160])
    block.append(row["url"])
    return "\n".join(block)


def _send_links(rows, title, mark, to=None):
    """Отправить подборку пачками по 3500 знаков, не разрывая ссылки.

    Резать по знакам, как это делает `send`, здесь нельзя: разрыв посреди
    адреса превращает его в мусор, по которому уже не перейти. Поэтому
    сообщение набирается целыми абзацами и закрывается, когда следующий в
    него не влезает.

    Помечаем отправленным только после того, как Telegram принял ВСЕ части:
    оборванная на середине подборка должна прийти заново целиком.
    """
    if not rows:
        return False

    count = f"{len(rows)} {_plural(len(rows), 'ролик', 'ролика', 'роликов')}"
    parts, buf = [], [f"{title} — {count}"]
    for row in rows:
        piece = _link_block(row)
        if sum(len(x) + 2 for x in buf) + len(piece) > 3500:
            parts.append("\n\n".join(buf))
            buf = []
        buf.append(piece)
    if buf:
        parts.append("\n\n".join(buf))

    ok = True
    for chat, thread in (to or [(None, None)]):
        for part in parts:
            ok = send(part, preview=False, chat_id=chat, thread=thread) and ok
    if ok and mark:
        jobs.mark_links_sent([row["id"] for row in rows])
    return ok


def _cmd_links(text):
    """`/links` в переписке.

    Голая команда отдаёт только НОВОЕ и помечает его отправленным. С числом
    или со словом «все» — просто показывает последние и ничего не помечает:
    это взгляд в архив, он не должен съедать очередную подборку.
    """
    # Не `if " " in text`: «/links » с пробелом на конце даёт список из одного
    # куска, и обращение ко второму роняло обработчик. Клавиатура телефона
    # ставит такой пробел сама, автозаменой после команды.
    parts = text.split(maxsplit=1)
    arg = parts[1].strip().lower() if len(parts) > 1 else ""

    if not arg:
        rows = jobs.links(limit=200, unsent=True)
        if rows:
            _send_links(rows, "Понравилось", mark=True)
        elif jobs.links(limit=1):
            send("Новых ссылок нет. «/links 20» — показать последние.")
        else:
            porog = f"{config.LINK_MIN_LIKES:,}".replace(",", " ")
            send("Ссылок пока нет. Они появляются, когда агент лайкает "
                 f"ролик, набравший {porog} лайков и больше.")
        return

    limit = 50 if arg in ("все", "всё", "all") else 0
    if not limit:
        digits = re.sub(r"[^0-9]", "", arg)
        limit = max(1, min(200, int(digits))) if digits else 0
    if not limit:
        send("Так: /links — новое, /links 20 — последние 20, "
             "/links все — последние 50.")
        return

    rows = jobs.links(limit=limit)
    if not rows:
        send("Ссылок пока нет.")
        return
    _send_links(rows, "Последнее понравившееся", mark=False)


def digest(force=False):
    """Подборка новых ссылок. Возвращает строку для лога, "" — если не слал.

    Вызывается по часам из `scheduler.serve` и по команде `/links`. Ссылки,
    которые уже уходили, второй раз не приходят: отметка `sent` в базе.
    """
    rows = jobs.links(limit=200, unsent=True)
    if not rows:
        return ""
    if not force and len(rows) < max(1, config.LINKS_DIGEST_MIN):
        return ""
    day = dt.datetime.now().strftime("%d.%m")
    # Есть тема для ссылок — подборка туда (во все такие группы), а не в личку.
    if not _send_links(rows, f"Понравилось ({day})", mark=True,
                       to=destinations("links") or None):
        return ""
    return f"подборка ссылок отправлена: {len(rows)}"


def check():
    """Жив ли бот и кто он. Для кнопки «проверить» в окне."""
    cfg = load_settings()
    if not cfg["token"]:
        return False, "токен не задан"
    res = _call("getMe", timeout=20)
    if not res.get("ok"):
        why = _why_failed(res)
        if why in (MALFORMED, REJECTED):
            return False, why
        return False, f"токен не принят: {str(res.get('error') or res)[:80]}"
    name = res["result"].get("username") or res["result"].get("first_name")
    if res["result"].get("username") and _saved().get("username") != name:
        _remember(username=name)            # окну — показать, чей бот, без сети
    who = "открыт всем" if config.BOT_OPEN else f"только для {cfg['owner']}"
    if not cfg["owner"]:
        # Не поломка: отвечать он уже может. Некуда слать только неспрошенное —
        # вечернюю подборку и отчёты из окна.
        return True, f"@{name}, {who}; напиши ему — запомню, куда слать подборку"
    return True, f"@{name}, {who}; неспрошенное шлю в {cfg['owner']}"


DOWNLOAD = {"why": ""}      # почему не скачалось — в ответ человеку


def _download(file_id, dest_dir, size_mb=0):
    # Свой сервер отвечает на getFile, только когда САМ скачал файл у
    # Telegram: на сотнях мегабайт это минуты, и 30 с обрывали бы каждое
    # большое видео «не смог скачать».
    import tgserver

    wait = 30 + (min(3600, size_mb * 3) if max_download_mb() >= 2000 else 0)
    info = _call("getFile", {"file_id": file_id}, timeout=wait)
    DOWNLOAD["why"] = ""
    if not info.get("ok"):
        text = str(info.get("description") or info.get("error") or "")
        if "too big" in text.lower():
            # Сервер без --local: он, как облако, отдаёт до 20 МБ. Запоминаем —
            # следующее большое видео получит объяснение сразу, без скачивания.
            tgserver._STATE["local"] = False
            DOWNLOAD["why"] = ("сервер Bot API запущен без --local и, как облако, отдаёт "
                               "только до 20 МБ. Перезапусти его с --local — или впиши "
                               "api_id и api_hash, и программа поднимет свой")
        else:
            DOWNLOAD["why"] = text[:120]
        return None
    path = info["result"]["file_path"]
    token = load_settings()["token"]
    name = f"{int(time.time())}_{os.path.basename(path)}"
    dest = os.path.join(dest_dir, name)
    # Свой сервер (--local) отдаёт не адрес, а путь к уже скачанному файлу на
    # этом же ПК — просто переносим его к себе.
    if os.path.isabs(path) and os.path.isfile(path):
        try:
            shutil.move(path, dest)
            return dest
        except OSError:
            try:
                shutil.copyfile(path, dest)      # чужой сервер держит файл — копию
                return dest
            except OSError as e:
                DOWNLOAD["why"] = f"файл сервера не читается: {e}"
                return None
    if os.path.isabs(path) or re.match(r"^/|^[A-Za-z]:", path):
        # Путь на диске сервера, а у нас его нет: сервер на другом ПК или в
        # Docker. Иногда такие раздают файлы по HTTP — пробуем, иначе говорим.
        DOWNLOAD["why"] = (f"сервер Bot API положил файл у себя ({path[:60]}), а на этом ПК его "
                           "нет — сервер на другом компьютере или в Docker. Нужен сервер на "
                           "этом ПК (или раздача его папки по HTTP)")
    url = f"{api_base()}/file/bot{token}/{path.lstrip('/')}"
    # Не `urlretrieve`: у него нет таймаута вовсе. Зависшая отдача с той
    # стороны — и поток бота стоит навсегда, бот перестаёт отвечать на всё,
    # включая /stop. Здесь таймаут на каждое чтение и чистка обрывка.
    try:
        with urllib.request.urlopen(url, timeout=120) as src,                 open(dest, "wb") as out:
            shutil.copyfileobj(src, out, 1024 * 256)
    except Exception:
        try:
            os.remove(dest)
        except OSError:
            pass
        return None
    return dest


# --------------------------------------------------------- запуск работы

# Кто выполняет длинную работу. В окне это общая очередь на одно действие:
# телефон один, и публикация не должна начаться посреди сессии. Пусто —
# выполняем прямо здесь (так работает `main.py serve`).
SUBMIT = None


def set_submit(fn):
    global SUBMIT
    SUBMIT = fn


class Deferred(RuntimeError):
    """«Опубликую сам, когда телефон свободен» — так отвечает служба телефона."""


def leave_to_scheduler(title, work):
    """Очередь действий службы телефона (`main.py serve`): публиковать здесь, в
    потоке бота, нельзя — в это время по телефону может идти сессия из
    основного потока. Задача уже в очереди со временем «сейчас», цикл службы
    её подберёт."""
    raise Deferred(title)


def _run(title, work):
    # Работа уходит в ЧУЖОЙ поток (общая очередь окна), а «кому отвечать»
    # живёт в переменной потока — там его уже не будет. Поэтому чат
    # запоминается здесь и восстанавливается на той стороне: иначе отчёт о
    # публикации ушёл бы владельцу, а не тому, кто прислал видео.
    address = reply_address()

    def in_thread():
        reply_to(address)
        try:
            work()
        finally:
            set_reply(None)

    if SUBMIT is not None:
        try:
            SUBMIT(title, in_thread)
            return True
        except Deferred:
            send("Поставил в очередь — служба телефона опубликует, как только "
                 "телефон освободится (обычно в течение минуты).")
            return False
        except RuntimeError as e:
            # Занято сессией — и это нормально: обрывать её ради поста нельзя,
            # телефон один, а недосмотренная сессия выглядит подозрительнее
            # любой задержки. Задача остаётся в очереди, и сторож в окне
            # (webui.Dispatcher) публикует её, как только телефон освободится.
            busy = str(e).split(": ", 1)[-1].strip() or "другое действие"
            send(f"Телефон сейчас занят ({busy}) — не прерываю. "
                 "Опубликую сразу, как освободится.")
            return False
    in_thread()
    return True


# --------------------------------------------------------------- разбор

def _save_incoming(urls, note=""):
    """Положить присланные ссылки в ту же подборку, что и лайкнутое с телефона.

    Отдельной таблицы не заводим намеренно: вопрос у обеих один и тот же —
    «что посмотреть/переснять», и `/links` должен отвечать на него целиком.
    Отличить источник всегда можно по `session`: у телефона там номер сессии,
    здесь — «телеграм».

    Лайков у присланной ссылки нет и взяться им неоткуда (для этого пришлось
    бы лезть в сеть за страницей ролика), поэтому в базу идёт ноль, а подборка
    такие строки печатает без сердечка.
    """
    saved, dupes, lines = 0, 0, []
    for url in urls[:MAX_LINKS_PER_MESSAGE]:
        app = weblink.network_of(url)
        fresh = jobs.add_link(url, app or "ссылка", 0, caption=note,
                              session="телеграм")
        saved += 1 if fresh else 0
        dupes += 0 if fresh else 1
        lines.append(("+ " if fresh else "= ") + (app or "сеть не узнал"))

    left = len(urls) - MAX_LINKS_PER_MESSAGE
    reply = []
    if saved:
        reply.append(f"Сохранил {saved} {_plural(saved, 'ссылку', 'ссылки', 'ссылок')}: "
                     + ", ".join(sorted({l[2:] for l in lines if l.startswith('+')})))
    if dupes:
        reply.append(f"Уже было: {dupes}")
    if left > 0:
        reply.append(f"Остальные {left} не взял — больше {MAX_LINKS_PER_MESSAGE} "
                     "за раз не принимаю.")
    reply.append(f"Всего ждёт подборки: {jobs.links_waiting()}")
    send("\n".join(reply), preview=False)


def _handle(msg):
    cfg = load_settings()
    chat = msg.get("chat", {}) or {}
    chat_id = str(chat.get("id", ""))
    group = chat.get("type") in GROUP_TYPES
    sender = str((msg.get("from") or {}).get("id", "") or chat_id)
    thread = msg.get("message_thread_id") if msg.get("is_topic_message") else None

    # Отвечаем в ТОТ ЖЕ чат (и в ту же тему), откуда пришло. Ставится до
    # первого `send` и снимается в `poll_forever`, иначе следующее сообщение
    # от другого человека уехало бы предыдущему.
    set_reply(chat_id, thread=thread, group=group)

    # Первый написавший запоминается как адрес для НЕСПРОШЕННОГО (вечерняя
    # подборка, отчёты из окна) — им отвечать некуда. Это человек, а не
    # группа: тревоги со снимком телефона — только ему лично.
    if not cfg["owner"]:
        save_settings(cfg["token"], sender)
        cfg = load_settings()
    if group:
        # Группа стала супергруппой (включили темы и т.п.) — у чата новый
        # номер. Разрешение переезжает вместе с ним, иначе бот замолчал бы.
        moved = str(msg.get("migrate_to_chat_id") or "")
        if moved and chat_id in allowed_groups():
            allow_group(moved, chat.get("title", ""))
            drop_group(chat_id)
            return
        old_id = str(msg.get("migrate_from_chat_id") or "")
        if old_id and old_id in allowed_groups():
            allow_group(chat_id, chat.get("title", ""))
            drop_group(old_id)
            return
        # Только чаты владельца: бота может добавить к себе кто угодно.
        if chat_id not in allowed_groups():
            if sender != str(cfg["owner"]):
                return                            # чужой чат — молчим
            allow_group(chat_id, chat.get("title", ""))
    elif not config.BOT_OPEN and chat_id != str(cfg["owner"]):
        return                                    # чужие игнорируются молча

    text = (msg.get("text") or msg.get("caption") or "").strip()
    addressed = not group
    if group:
        text, addressed, other_bot = _for_me(text, msg)
        if other_bot:
            return                                # команда другому боту
        if text.startswith(("/here", "/everywhere")):
            _cmd_topic(text, chat, msg, sender, cfg)
            return

    video = msg.get("video") or msg.get("document") or msg.get("animation")
    role = _role_of(text, msg, video, chat_id, sender, addressed)
    if group:
        roles = _roles(chat_id)
        here = thread or GENERAL
        home = role_home(role) if role else None
        if home and (home[0] != str(chat_id) or home[1] != here):
            return                                # у этого дела свой чат и тема
        if not role and roles and len(roles) == len(ROLES) \
                and here not in {v["topic"] for v in roles.values()}:
            return                                # тема не наша вовсе — молчим
    _REPLY.role = role

    # Команды — только в ТЕКСТЕ. Подпись к видео — это описание поста:
    # «Ссылки на рецепт в профиле» или «Отмена встречи — разбор» должны уйти
    # в публикацию, а не выдать подборку и не отменять задачу.
    if not video and _command(text, addressed or text.startswith("/"), chat_id, sender):
        return

    # Ссылки принимаем до видео: сообщение со ссылкой — это не публикация,
    # а пополнение подборки. Разметка нужна потому, что в пересланном посте
    # адрес бывает спрятан за текстом и в самом тексте его нет.
    urls = weblink.find_urls(text, msg.get("entities") or msg.get("caption_entities"))
    if urls and not video:
        _save_incoming(urls, weblink.strip_urls(text))
        return

    if not video:
        # Фото вместо видео — частая промашка; в группе на обычную болтовню
        # и картинки не отвечаем — только если обратились.
        if addressed and (text or msg.get("photo")):
            send("Пришли видео файлом — опубликую, или ссылку — сохраню.",
                 keyboard=True)
        return

    if not _is_video(msg, video):
        # Документ, но не видео (PDF, архив, картинка файлом). Не качаем:
        # архив на гигабайт впустую занял бы бота на минуты.
        if addressed:
            send("Это не видео. Пришли видео — опубликую.")
        return
    if group and msg.get("animation") and not addressed:
        return                    # гифка-реакция в чате — не пост

    size_mb = (video.get("file_size") or 0) / 1024 / 1024
    if size_mb > max_download_mb():
        if max_download_mb() >= 2000:
            send(f"Файл {size_mb:.0f} МБ — больше 2 ГБ Telegram не отдаёт даже своему серверу.")
        else:
            send(f"Видео {size_mb:.0f} МБ, а через облако Telegram отдаёт боту только до "
                 f"{MAX_DOWNLOAD_MB} МБ. Чтобы бот брал видео до 2 ГБ, впиши api_id и "
                 "api_hash с my.telegram.org во вкладке «Бот» программы. А пока — "
                 "перетащи файл в окно программы (вкладка «Очередь»), там размер "
                 "не ограничен.")
        return

    import postspec

    # Подпись со служебными строками («куда:», «когда:») — старый шаблон,
    # для тех, кто к нему привык. Обычная подпись — это ПРОСТО описание:
    # уходит в пост как есть, а куда и когда — человек выбирает кнопками.
    spec = None
    if postspec.is_template(text):
        try:
            spec = postspec.parse(text)
        except postspec.SpecError as e:
            send(f"Не понял подпись: {e}.")
            return

    # Скачивание — не в потоке опроса: большой файл через свой сервер идёт
    # минутами, и всё это время бот не отвечал бы ни на что, даже на /stop.
    # Куда класть — запоминаем СЕЙЧАС: пока видео качается, телефон в окне
    # могут переключить, и оно уехало бы в очередь другого телефона.
    where = (config.QUEUE_DIR, getattr(jobs, "DEVICE", ""))
    _later(lambda: _take_video(video, text, spec, size_mb, where))


VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".3gp")


def _role_of(text, msg, video, chat_id, sender, addressed):
    """Какое это дело: posts, links, stats или None — общее (/status, /start)."""
    low = text.lower()
    if video:
        return "posts"
    if text.startswith(("/links", "/ссылки")) or text == BTN_LINKS \
            or (addressed and low.startswith("ссылки")):
        return "links"
    if text.startswith("/stats") or (addressed and low.startswith("статистика")):
        return "stats"
    if text.startswith(("/queue", "/cancel", "/template")) or text == BTN_POST \
            or (addressed and (low == "очередь" or low.startswith("отмена")
                               or low in ("шаблон", "/шаблон"))):
        return "posts"
    if f"{chat_id}:{sender}" in _ASK_TIME:
        return "posts"                            # ждём «своё время» — ответ про пост
    if weblink.find_urls(text, msg.get("entities") or msg.get("caption_entities")):
        return "links"
    return None


def _cmd_topic(text, chat, msg, sender, cfg):
    """/here — всё в этой теме; /here видео|ссылки|статистика — одно дело;
    /everywhere — во всех темах (или /everywhere ссылки — одно дело).

    Только владелец: иначе любой участник мог бы увести бота в свою тему.
    """
    chat_id = str(chat.get("id"))
    thread = msg.get("message_thread_id") if msg.get("is_topic_message") else None
    roles = _roles(chat_id)
    if sender != str(cfg["owner"]):
        here = thread or GENERAL
        if not roles or here in {v["topic"] for v in roles.values()}:
            send("Выбрать тему для бота может только его владелец.")
        return                                      # в чужой теме — молчим
    word = text.split(maxsplit=1)[1].strip().lower() if " " in text.strip() else ""
    picked = [r for r in ROLES if any(word.startswith(w) for w in ROLE_WORDS[r])] \
        if word else list(ROLES)
    if word and not picked:
        send("Не понял, какое дело. Так: /here видео, /here ссылки, /here статистика "
             "или просто /here — всё сюда.")
        return
    if text.startswith("/everywhere"):
        set_roles(chat_id, picked, None)
        what = "Работаю во всех темах этого чата." if not word else \
            f"{ROLE_TITLES[picked[0]].capitalize()} — снова в любой теме."
        send(what)
        return
    # Чат без тем — привязываем весь чат: дело живёт здесь, из других чатов
    # бот его больше не берёт.
    name = _topic_name(msg) if (thread or chat.get("is_forum")) else (chat.get("title") or "этот чат")
    for g in _groups():                           # дело переезжает сюда из другого чата
        if g["id"] != chat_id and any(role_topic(g["id"], r) for r in picked):
            set_roles(g["id"], picked, None)
    set_roles(chat_id, picked, thread or GENERAL, name)
    if not word:
        where = f"теме «{name}»" if (thread or chat.get("is_forum")) else "этом чате"
        send(f"Готово: работаю только в {where}. В других темах и чатах молчу, "
             "отчёты о публикациях — сюда же. Вернуть как было — /everywhere.")
        return
    jobs_here = {"posts": "Видео отсюда беру на постинг, черновики и отчёты о публикациях — сюда.",
                 "links": "Ссылки отсюда сохраняю, подборку присылаю сюда.",
                 "stats": "Статистику после каждой сессии и итог дня пишу сюда."}[picked[0]]
    others = [f"{ROLE_TITLES[r]} → «{v['name'] or 'Общая'}»"
              for r, v in _roles(chat_id).items() if r != picked[0]]
    place = f"тема «{name}»" if (thread or chat.get("is_forum")) else "этот чат"
    send(f"Готово: {place} — {ROLE_TITLES[picked[0]]}. {jobs_here} В других чатах и "
         "темах это дело больше не принимаю."
         + ("\nОстальное: " + ", ".join(others) if others else ""))


def _is_video(msg, video):
    if msg.get("video") or msg.get("animation"):
        return True
    mime = str(video.get("mime_type") or "").lower()
    name = str(video.get("file_name") or "").lower()
    return mime.startswith("video/") or name.endswith(VIDEO_EXT) or not (mime or name)


def _command(text, addressed, chat_id, sender):
    """Команда или кнопка. True — обработано.

    В группе слова без «/» («ссылки», «очередь», «отмена») — только когда
    обратились к боту: иначе «ссылки скинь» в разговоре выдавало бы всю
    подборку (и помечало её отправленной), а «отмена встречи» — отменяло.
    """
    low = text.lower()
    if text.startswith("/stop"):
        # Останавливаем то, что идёт сейчас, а не запрещаем будущее: режимов
        # в программе больше нет, всё остальное работает штатно.
        abort.request()
        send("Останавливаю текущее действие.")
        return True
    if (text.startswith("/start") or text.startswith("/help")) and in_group():
        send(GROUP_HELLO + ("" if _me().get("can_read_all_group_messages") else PRIVACY_HINT))
        return True
    if text.startswith("/start") or text.startswith("/help"):
        when = ", ".join(config.LINKS_DIGEST_TIMES) or "только по команде"
        send("Работаю.\n\n"
             "ВИДЕО с подписью — опубликую. Подпись = описание поста, "
             "как есть. Куда и когда — выберешь кнопками.\n"
             "/queue — что ждёт публикации, /cancel 5 — отменить пост #5\n\n"
             "ССЫЛКУ — сохраню в подборку. Любая соцсеть: TikTok, YouTube, "
             "Instagram, VK, X, Telegram, Pinterest, Reddit и прочие. "
             "Можно несколько сразу и с припиской — она станет заметкой.\n\n"
             f"/links — подборка (сама приходит: {when})\n"
             "/links 20 — последние 20, даже если уже присылал\n"
             "/status — что происходит\n"
             "/stats — статистика всех сетей снимками экранов: за 1 день, за 2 дня, за всё "
             "время или как обычно (выбор кнопками; сразу — /stats 1, /stats 2, /stats все). "
             "В альбоме и плитки профилей — под каждым видео его просмотры\n"
             "/stop — прервать текущее действие", keyboard=True)
        return True
    if text.startswith("/status"):
        send(
            f"В очереди: {len(jobs.pending())}\n"
            f"Постов сегодня: {jobs.count_today('done')}/{config.MAX_POSTS_PER_DAY}\n"
            f"Сессий сегодня: {jobs.sessions_today()}\n"
            f"Ссылок ждёт подборки: {jobs.links_waiting()}"
        )
        return True
    if text.startswith("/queue") or (addressed and (text == BTN_POST or low == "очередь")):
        # Публикацию кнопкой не заменить — нужен сам файл. Поэтому здесь
        # очередь и напоминание, как прислать видео.
        send(_queue_text() + "\n\nЧтобы опубликовать — пришли ВИДЕО с подписью "
             "(подпись = описание). Куда и когда — выберешь кнопками.", keyboard=True)
        return True
    if text.startswith("/template"):
        import postspec
        send(postspec.TEMPLATE_HELP)
        return True
    if text.startswith("/stats") or (addressed and low.startswith("статистика")):
        days = stats_days(text)
        if days is None:
            _ask_stats_period()
        else:
            _snap_stats(days or None)
        return True
    if text.startswith("/cancel"):
        _cmd_cancel(text)
        return True
    if text.startswith("/links"):
        _cmd_links(text)
        return True
    if text and _take_time_answer(chat_id, text, sender):
        return True
    if not addressed:
        return False
    # Кнопки клавиатуры и слова — как команды.
    if text == BTN_LINKS:
        # Именно "/links", а не текст кнопки: разбор берёт второе слово как
        # довесок, и «Ссылки» из подписи он принял бы за аргумент.
        _cmd_links("/links")
        return True
    if low in ("шаблон", "/шаблон"):
        import postspec
        send(postspec.TEMPLATE_HELP)
        return True
    if low.startswith("отмена"):
        _cmd_cancel(text)
        return True
    if low.startswith("ссылки"):
        _cmd_links(text)
        return True
    return False


# Период статистики (2026-10-22: «надо выбор, чтобы он мог показать стату за
# один день или за два дня»). 0 — как в приложениях: YouTube 28 дней,
# TikTok 7, Instagram 30.
STATS_PERIODS = {1: "за 1 день", 2: "за 2 дня", 0: "", -1: "за всё время"}


def stats_days(text):
    """Период из «/stats 2», «статистика за день»: 1, 2, 0 — обычный, None —
    не сказано (тогда спросить кнопками)."""
    parts = text.split(maxsplit=1)
    rest = parts[1].strip().lower() if len(parts) > 1 else ""
    if not rest:
        return None
    if re.search(r"вс[её]\s*врем|\bвсё\b|\bвсе\b|\bвесь\b|\ball\b|lifetime", rest):
        return -1                     # за всё время
    num = re.search(r"\d+", rest)
    if num:
        return int(num.group()) if int(num.group()) in (1, 2) else 0
    if re.search(r"\bдва\b|\bдвое\b|\bдвух\b|\btwo\b", rest):
        return 2
    if re.search(r"\bд(ень|ня)\b|сутки|вчера|сегодня|\bday\b", rest):
        return 1
    return 0


def _stats_markup():
    return json.dumps({"inline_keyboard": [
        [{"text": "За 1 день", "callback_data": "s:1"},
         {"text": "За 2 дня", "callback_data": "s:2"}],
        [{"text": "За всё время", "callback_data": "s:-1"}],
        [{"text": "Как обычно (7–30 дней)", "callback_data": "s:0"}]]}, ensure_ascii=False)


def _ask_stats_period():
    """/stats без периода — выбор кнопками."""
    target = current_chat() or load_settings()["owner"]
    params = {"chat_id": target, "text": "Статистику за какой период?",
              "reply_markup": _stats_markup()}
    thread = _thread_for(target)
    if thread:
        params["message_thread_id"] = thread
    _call("sendMessage", params, timeout=20)


def _snap_stats(days=None, say=None):
    """/stats — снять экраны статистики ВСЕХ сетей прямо сейчас и прислать
    альбомом сюда же (2026-10-18: «нужны именно тогда, когда я их запрошу»).
    days — 1 или 2 дня, None — обычный период приложений.

    Съёмка — действие с телефоном, поэтому через общую очередь действий.
    Телефон занят — просьба записывается, и сторож снимет сразу, как он
    освободится. Уже снимаем — второй раз не начинаем.
    """
    import accstats
    import runlog

    say = say or send
    address = reply_address()
    role = getattr(_REPLY, "role", None) or "stats"
    period = STATS_PERIODS.get(days or 0, "")
    what = "статистику со всех сетей" + (f" {period}" if period else "")

    def work():
        reply_to(address, role)
        try:
            log = runlog.Log("статистика по запросу")
            accstats.collect_all(log=log, days=days)
            if not send_shots(fresh=True):
                lines = [ln.strip() for ln in log.text().splitlines() if ln.strip()]
                send("Снимки статистики не получились: "
                     + (lines[-1] if len(lines) > 1 else "телефон не ответил")
                     + ". Телефон подключён и разблокирован?")
        finally:
            set_reply(None)

    # Живьём на Redmi 2026-10-22: за 1 день все три сети — 5 минут (выбор
    # дат в календарях), обычный снимок — около трёх.
    soon = "5 минут" if days else "3–4 минуты"
    if SUBMIT is None:
        say(f"Снимаю {what} — пришлю снимками через {soon}.")
        work()
        return
    try:
        SUBMIT("статистика по запросу", work)
        say(f"Снимаю {what} — пришлю снимками через {soon}.")
    except Deferred:
        accstats.request_now(address, days)
        say(f"Сниму {what}, как только служба телефона его освободит, — пришлю сюда.")
    except RuntimeError as e:
        busy = str(e).split(": ", 1)[-1].strip()
        if "статистик" in busy:
            say("Уже снимаю статистику — пришлю, как будет готово.")
        else:
            accstats.request_now(address, days)
            say(f"Телефон сейчас занят ({busy}) — сниму {what} сразу, как "
                "освободится, и пришлю сюда.")


def _stats_button(cq, data, chat_id, message_id, group, thread):
    """Кнопка периода под «Статистику за какой период?»."""
    _REPLY.role = "stats"
    home = role_home("stats")
    if home and group and (home[0] != chat_id or (thread or GENERAL) != home[1]):
        _call("answerCallbackQuery", {"callback_query_id": cq.get("id", ""),
                                      "text": "статистику я теперь присылаю в другой чат или тему — спроси там",
                                      "show_alert": "true"}, timeout=10)
        return
    _call("answerCallbackQuery", {"callback_query_id": cq.get("id", "")}, timeout=10)
    try:
        days = int(data.split(":", 1)[1]) or None
    except ValueError:
        days = None
    # Ответ — правкой того же сообщения: кнопки исчезают, второй раз не нажать.
    _snap_stats(days, say=lambda text: _edit(chat_id, message_id, text))


def _take_video(video, text, spec, size_mb=0, where=None):
    """Скачать видео и сделать черновик (или задачу по шаблону)."""
    import postspec

    queue_dir, device = where or (config.QUEUE_DIR, None)

    send("Качаю..." if size_mb < 50 else f"Качаю {size_mb:.0f} МБ — это займёт время...")
    path = _download(video["file_id"], queue_dir, size_mb)
    if not path:
        send("Не смог скачать файл" + (f": {DOWNLOAD['why']}." if DOWNLOAD["why"] else "."))
        return
    # То же видео, что уже ждёт или публикуется, — не ставим второй раз.
    fp = jobs.video_fingerprint(path)
    twin = jobs.duplicate_of(fp)
    if twin is not None and twin["status"] != "done":
        try:
            os.remove(path)
        except OSError:
            pass
        what = {"running": "публикуется прямо сейчас", "draft": "ждёт выбора времени"}.get(
            twin["status"], "уже в очереди")
        send(f"Это видео {what} — задача #{twin['id']}. Второй раз не ставлю.")
        return

    # Скачалось не то или не до конца: публиковать такое нельзя — агент
    # честно донесёт обрывок до редактора и застрянет там.
    import scheduler

    why = scheduler.looks_like_video(path)
    if why:
        try:
            os.remove(path)
        except OSError:
            pass
        send(f"Не беру: {why}")
        return

    if spec is None:
        # Черновик: публиковать его нельзя, пока не нажата кнопка времени.
        # Сети — все, кроме явно исключённых строкой «без ютуба» / «кроме
        # инсты»; сама эта строка в пост не уходит.
        excluded, caption = postspec.exclusions(text)
        targets = [t for t in _default_targets() if t not in excluded]
        if not targets:
            send("Все сети исключены — публиковать некуда. Убери строку «без …».")
            try:
                os.remove(path)
            except OSError:
                pass
            return
        extra = {}
        if "shorts" in targets and config.YT_TAGS and caption.strip():
            # Теги YouTube подбираем сразу — человек увидит их в черновике
            # до того, как нажмёт «Сейчас».
            import yttags
            extra["shorts_tags"] = yttags.suggest(caption)
            extra["shorts_tags_from"] = yttags.LAST_SOURCE
        job_id = jobs.add(path, caption=caption, targets=",".join(targets),
                          status="draft", chat=reply_address(), device=device,
                          captions=extra or None)
        _send_draft(job_id)
        return

    job_id = jobs.add(path, caption=spec["caption"], targets=",".join(spec["targets"]),
                      run_at=spec["when"].timestamp() if spec["when"] else None,
                      captions=spec["captions"], exact=spec["exact"],
                      chat=reply_address(), device=device)
    send(postspec.describe(spec, job_id) + f"\n\nОтменить: /cancel {job_id}")
    if spec["when"] is None:
        _publish_now(job_id)


# Видео качаются по одному в своём потоке — в порядке прихода (иначе два
# одинаковых, присланных подряд, разминулись бы мимо проверки на повтор).
# Вне опроса (тесты, `main.py`) — сразу, в том же потоке.
_WORK = {"queue": None, "thread": None}


def _later(fn):
    if not getattr(_REPLY, "polling", False):
        fn()
        return
    import queue

    address = reply_address()
    role = getattr(_REPLY, "role", None) or "posts"

    def job():
        reply_to(address, role)
        try:
            fn()
        except Exception as e:
            send(f"ошибка обработки видео: {type(e).__name__}: {e}")
        finally:
            set_reply(None)

    if _WORK["queue"] is None:
        _WORK["queue"] = queue.Queue()
    _WORK["queue"].put(job)
    worker = _WORK["thread"]
    if worker is None or not worker.is_alive():
        def loop():
            while True:
                task = _WORK["queue"].get()
                try:
                    task()
                except Exception:
                    pass

        _WORK["thread"] = threading.Thread(target=loop, daemon=True, name="tg-video")
        _WORK["thread"].start()


# --------------------------------------------------- пост кнопками (2026-10-11)
#
# «Какая-то странная хуйня, как-то проще можно?» — про шаблон «куда/когда/
# описание». Можно: подпись к видео — это описание, а сети и время выбираются
# кнопками под одним сообщением. Состояние черновика живёт в базе (сети — в
# `jobs.targets`, статус 'draft'), поэтому кнопки работают и после перезапуска.

TARGET_ORDER = ("tiktok", "reels", "shorts")
_ASK_TIME = {}          # «чат:человек» -> задача, для которой ждём «своё время» текстом
ASK_TIME_SEC = 15 * 60  # дольше не ждём: человек передумал и пишет уже о другом


def _defaults_path():
    return os.path.join(config.BASE, "bot_post.json")


def _default_targets():
    """Какие сети отмечены сразу: ВСЕ.

    Раньше отмечались те, что выбирали в прошлый раз, — и одна снятая галочка
    тихо переезжала на все следующие посты. Просьба 2026-10-14: «по дефолту
    постил во все соцсети, если только я явно не укажу, какую проигнорировать».
    Исключить — строкой «без ютуба» в подписи или галочкой под черновиком.
    """
    return list(TARGET_ORDER)


def _remember_targets(targets):
    """Больше ничего не запоминаем (см. `_default_targets`)."""


def _time_choices(now=None):
    """Кнопки времени: (подпись, код). Вечер — сегодня, если ещё не поздно."""
    now = now or dt.datetime.now()
    evening = "сегодня 19:00" if now.hour < 18 else "завтра 19:00"
    return [("▶️ Сейчас", "now"), ("⏱ Через час", "1h"),
            ("🌆 " + evening.capitalize(), "eve"), ("☀️ Завтра 12:00", "tom12"),
            ("🕐 Своё время", "ask")]


def _when_of(code, now=None):
    """Код кнопки -> (datetime или None, точное ли)."""
    now = now or dt.datetime.now()
    if code == "now":
        return None, False
    if code == "1h":
        return (now + dt.timedelta(hours=1)).replace(second=0, microsecond=0), True
    if code == "eve":
        day = now.date() if now.hour < 18 else now.date() + dt.timedelta(days=1)
        return dt.datetime.combine(day, dt.time(19, 0)), True
    if code == "tom12":
        return dt.datetime.combine(now.date() + dt.timedelta(days=1), dt.time(12, 0)), True
    raise ValueError(code)


def _draft_markup(job):
    import postspec

    picked = [t for t in job["targets"].split(",") if t]
    jid = job["id"]
    nets = [{"text": ("✅ " if t in picked else "⬜ ") + postspec.TITLES[t].split()[-1],
             "callback_data": f"t:{jid}:{t}"} for t in TARGET_ORDER]
    times = [{"text": label, "callback_data": f"w:{jid}:{code}"}
             for label, code in _time_choices()]
    rows = [nets, times[:2], times[2:4], [times[4],
            {"text": "✖️ Отмена", "callback_data": f"x:{jid}"}]]
    return json.dumps({"inline_keyboard": rows}, ensure_ascii=False)


def _draft_text(job):
    caption = (job["caption"] or "").strip()
    body = caption if caption else "(без описания — пришли видео с подписью, если нужно)"
    if len(body) > 900:
        body = body[:900] + "…"
    import postspec

    text = f"🎬 Видео #{job['id']} получил.\n\nОписание:\n{body}\n\n"
    picked = [t for t in job["targets"].split(",") if t]
    if "shorts" in picked and caption:
        title = postspec.fit(postspec.shorts_title(caption), "shorts")
        if title != caption:
            text += f"Название на YouTube: {title}\n\n"
    caps = jobs.captions_of(job)
    tags = caps.get("shorts_tags") if "shorts" in picked else None
    if tags:
        by = {"model": " (подобрал DeepSeek)",
              "hashtags": " (из хэштегов описания — модель не ответила)"}.get(caps.get("shorts_tags_from"), "")
        text += f"Теги YouTube{by}: " + ", ".join(tags) + "\n\n"
    skipped = [postspec.TITLES[t] for t in TARGET_ORDER if t not in picked]
    if skipped:
        text += "Не публикую в: " + ", ".join(skipped) + "\n\n"
    return text + "Проверь сети и нажми, когда публиковать 👇"


def _send_draft(job_id):
    job = jobs.by_id(job_id)
    cfg = load_settings()
    target = current_chat() or cfg["owner"]
    params = {"chat_id": target, "text": _draft_text(job), "reply_markup": _draft_markup(job)}
    thread = _thread_for(target)
    if thread:
        params["message_thread_id"] = thread
    _call("sendMessage", params, timeout=20)


def _edit(chat_id, message_id, text, markup=None):
    params = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if markup is not None:
        params["reply_markup"] = markup
    return _call("editMessageText", params, timeout=20)


def _scheduled_text(job, when):
    import postspec

    where = ", ".join(postspec.TITLES[t] for t in job["targets"].split(",") if t)
    if when is None:
        moment = "сейчас"
    else:
        days = (when.date() - dt.date.today()).days
        day = {0: "сегодня", 1: "завтра", 2: "послезавтра"}.get(days, f"{when:%d.%m}")
        moment = f"{day} в {when:%H:%M}"
    caption = (job["caption"] or "").strip() or "(без описания)"
    if len(caption) > 600:
        caption = caption[:600] + "…"
    text = (f"✅ Пост #{job['id']}: {where} — {moment}\n\n{caption}")
    if "shorts" in job["targets"]:
        title = postspec.fit(postspec.shorts_title(job["caption"] or ""), "shorts")
        if title != (job["caption"] or "").strip():
            text += f"\n\nНазвание на YouTube: {title or '(пусто)'}"
    if when is not None:
        text += "\n\nВыйдет точно в срок: если в это время идёт сессия, приостановлю её."
    return text


def _cancel_markup(job_id):
    return json.dumps({"inline_keyboard": [[{"text": "✖️ Отменить пост",
                                             "callback_data": f"x:{job_id}"}]]})


def _schedule(job_id, when, exact, chat_id=None, message_id=None):
    """Черновик -> очередь. Отвечает правкой того же сообщения, если оно есть."""
    jobs.schedule(job_id, when.timestamp() if when else None, exact)
    job = jobs.by_id(job_id)
    _remember_targets([t for t in job["targets"].split(",") if t])
    text = _scheduled_text(job, when)
    if message_id:
        _edit(chat_id, message_id, text, _cancel_markup(job_id))
    else:
        send(text)
    if when is None:
        _publish_now(job_id)


def _publish_now(job_id):
    def work():
        import scheduler
        report = scheduler.run_post_job(job_id=job_id)
        send(report or "готово")

    _run(f"публикация #{job_id}", work)


def _handle_callback(cq):
    """Нажатие кнопки под сообщением бота."""
    data = str(cq.get("data") or "")
    msg = cq.get("message") or {}
    chat = msg.get("chat") or {}
    chat_id = str(chat.get("id", ""))
    message_id = msg.get("message_id")
    group = chat.get("type") in GROUP_TYPES
    thread = msg.get("message_thread_id") if msg.get("is_topic_message") else None
    set_reply(chat_id, thread=thread, group=group)
    owner = str(load_settings()["owner"])
    if (chat_id not in allowed_groups()) if group else \
            (not config.BOT_OPEN and chat_id != owner):
        # Кнопки под старым сообщением в чате, откуда бота уже убрали.
        _call("answerCallbackQuery", {"callback_query_id": cq.get("id", ""),
                                      "text": "этот чат не подключён"}, timeout=10)
        return
    if data.startswith("s:"):
        _stats_button(cq, data, chat_id, message_id, group, thread)
        return
    home = role_home("posts")
    _REPLY.role = "posts"
    if home and group and (home[0] != chat_id or (thread or GENERAL) != home[1]):
        # Старый черновик в теме, где бот больше не работает: всплывашка, а
        # не сообщение — писать бот должен только в своей теме.
        _call("answerCallbackQuery", {"callback_query_id": cq.get("id", ""),
                                      "text": "видео я теперь принимаю в другом чате или теме — пришли туда",
                                      "show_alert": "true"}, timeout=10)
        return
    note = ""
    try:
        kind, jid, *rest = data.split(":")
        job = jobs.by_id(int(jid))
    except (ValueError, TypeError):
        job = None
        kind = ""
    if job is None:
        note = "этой задачи уже нет"
    elif kind == "x":
        if jobs.cancel(job["id"]) is not None:
            try:
                os.remove(job["video_path"])
            except OSError:
                pass
            _edit(chat_id, message_id, f"✖️ Пост #{job['id']} отменён, видео удалено.")
        else:
            note = "уже опубликован или отменён"
    elif job["status"] in ("cancelled", "done", "failed"):
        note = {"cancelled": "этот пост отменён", "done": "этот пост уже опубликован",
                "failed": "этот пост не удалось опубликовать"}[job["status"]]
    elif job["status"] != "draft":
        note = "время уже выбрано — отменить можно кнопкой «Отменить пост»"
    elif kind == "t":
        picked = [t for t in job["targets"].split(",") if t]
        target = rest[0] if rest else ""
        picked = [t for t in picked if t != target] if target in picked else picked + [target]
        picked = [t for t in TARGET_ORDER if t in picked]
        jobs.set_targets(job["id"], ",".join(picked))
        _edit(chat_id, message_id, _draft_text(jobs.by_id(job["id"])),
              _draft_markup(jobs.by_id(job["id"])))
    elif kind == "w":
        code = rest[0] if rest else ""
        if not [t for t in job["targets"].split(",") if t]:
            note = "отметь хотя бы одну сеть"
        elif code == "ask":
            who = str((cq.get("from") or {}).get("id", "") or chat_id)
            _ASK_TIME[f"{chat_id}:{who}"] = (job["id"], message_id, time.time())
            send("Напиши время одним сообщением: 18:30, завтра 9:00, 12.10 18:30 или пт 20:00"
                 + (" — ответом на это сообщение" if group else ""))
        else:
            when, exact = _when_of(code)
            _schedule(job["id"], when, exact, chat_id, message_id)
    _call("answerCallbackQuery", {"callback_query_id": cq.get("id", ""),
                                  "text": note}, timeout=10)


def _take_time_answer(chat_id, text, sender=None):
    """Ответ на «Своё время». True — сообщение было про время и обработано.

    Ждём ответа от того, кто нажал кнопку: в группе время пишет он, а не
    первый, кто что-то сказал в чат.
    """
    key = f"{chat_id}:{sender or chat_id}"
    waiting = _ASK_TIME.get(key)
    if not waiting:
        return False
    import postspec

    job_id, message_id, asked = waiting
    job = jobs.by_id(job_id)
    if job is None or job["status"] != "draft" or time.time() - asked > ASK_TIME_SEC:
        _ASK_TIME.pop(key, None)       # время уже выбрали кнопкой или забыли
        return False
    if weblink.find_urls(text, None):
        return False                   # не время, а ссылка — в подборку
    try:
        when, exact = postspec.parse_when(text)
    except postspec.SpecError as e:
        send(f"Не понял: {e}. Ещё раз — например 18:30 или завтра 9:00")
        return True
    _ASK_TIME.pop(key, None)
    _schedule(job_id, when, exact, chat_id, message_id)
    return True


def _queue_text():
    """Что ждёт публикации — номер, время, сети, начало описания."""
    import postspec

    rows = jobs.pending()
    head = (f"Опубликовано сегодня: {jobs.count_today('done')}/"
            f"{config.MAX_POSTS_PER_DAY} (с точным временем — сверх лимита)")
    if not rows:
        return head + "\nВ очереди пусто."
    lines = [head, f"В очереди: {len(rows)}"]
    now = time.time()
    for row in rows[:15]:
        when = ("сейчас" if (row["run_at"] or 0) <= now else
                dt.datetime.fromtimestamp(row["run_at"]).strftime("%d.%m %H:%M"))
        where = ", ".join(postspec.TITLES.get(t, t) for t in row["targets"].split(",") if t)
        first = (row["caption"] or "(без описания)").splitlines()[0][:60]
        lines.append(f"#{row['id']} · {when} · {where}\n   {first}")
    return "\n".join(lines)


def _cmd_cancel(text):
    """/cancel 5 — убрать задачу #5 из очереди вместе с файлом."""
    m = re.search(r"\d+", text)
    if not m:
        send("Какую отменить? Напиши номер: /cancel 5\n\n" + _queue_text())
        return
    row = jobs.cancel(int(m.group(0)))
    if row is None:
        send(f"Задачи #{m.group(0)} в очереди нет — уже вышла или отменена.")
        return
    try:
        os.remove(row["video_path"])
    except OSError:
        pass
    send(f"Задача #{row['id']} отменена, видео удалено.")


# Почему бот сейчас не принимает сообщения — для окна и журнала. Пусто —
# всё в порядке. Раньше при любой беде он молча повторял запрос раз в 5 с,
# и «бот перестал работать» (жалоба 2026-10-01) не объяснялось ничем.
_TROUBLE = {"text": ""}

CONFLICT = ("бота уже опрашивает другая копия PhoneAgent — на другом компьютере "
            "или в другом окне. Telegram отдаёт сообщения только одной; закрой "
            "лишнюю или убери токен на втором компьютере")
REJECTED = "Telegram не принимает токен (401) — вставь его заново во вкладке «Бот»"
MALFORMED = ("токен вставлен с ошибкой (Telegram его не узнаёт, 404) — скопируй "
             "его у @BotFather заново и вставь во вкладке «Бот»")
WEBHOOK = ("у бота включён webhook — Telegram отдаёт сообщения другому сервису, а не "
           "программе. Если бот только для PhoneAgent: открой в браузере "
           "api.telegram.org/bot<токен>/deleteWebhook — и бот заработает")
RETRY_SEC = 5            # пауза перед повтором после неудачного опроса
CONFLICT_GRACE_SEC = 75  # 409 короче этого — наш же прежний запрос, не другая копия


def trouble():
    return _TROUBLE["text"]


def _why_failed(res):
    """Человеческая причина неудачного getUpdates."""
    text = str(res.get("description") or res.get("error") or "")
    code = res.get("error_code")
    if "webhook" in text.lower():
        return WEBHOOK
    if code == 409 or "409" in text or "Conflict" in text:
        return CONFLICT
    if code == 401 or "401" in text or "Unauthorized" in text:
        return REJECTED
    if code == 404 or "404" in text or "Not Found" in text:
        return MALFORMED
    return "нет связи с Telegram: " + text[:80]


def poll_forever(generation=0):
    """Опрос Telegram. Не падает никогда: ошибка разбора — в журнал, дальше.

    `generation` — номер запуска. Сменили токен — `restart()` поднимает новый
    поток с новым номером, а этот, вернувшись из долгого запроса, видит, что
    устарел, и молча выходит, не трогая пришедшее.

    Почему не просто читать токен заново на каждом круге: номер прочитанного
    сообщения (`offset`) у каждого бота свой. Со старым номером новый бот
    отдавал «ничего нового» — и на новый токен не реагировал вовсе (жалоба
    2026-10-14). Новый поток начинает с нуля.
    """
    if not load_settings()["token"]:
        return
    # Свой сервер Bot API (видео до 2 ГБ), если вписаны api_id и api_hash.
    # Здесь, в потоке: сервер поднимается до 20 с, и окно ждать не должно.
    try:
        setup_server()
    except Exception as e:
        print(f"[бот] свой сервер: {type(e).__name__}: {e} — работаю через облако", flush=True)
    # Здесь, в потоке, а не при старте окна: без связи getMe ждёт 20 с.
    ok, info = check()
    print(f"[бот] {'запущен: ' + info if ok else info}", flush=True)
    if not ok and "не принят" in info:
        _TROUBLE["text"] = REJECTED
    _drop_stale_drafts()
    bind_shared()
    _REPLY.polling = True        # видео из этого потока качаются в своём (_later)
    said = ""
    conflict_since = None
    token = load_settings()["token"]
    offset = _offset_for(token)
    while True:
        if generation != _GENERATION["n"]:
            return                           # нас сменил поток с новым токеном
        now_token = load_settings()["token"]
        if now_token != token:
            # Токен поменяли в обход окна (руками в telegram.json).
            token, offset, said = now_token, _offset_for(now_token), ""
            _TROUBLE["text"] = ""
            if not token:
                return
            ok, info = check()
            print(f"[бот] новый токен: {'запущен: ' + info if ok else info}", flush=True)
        try:
            offset = _poll_once(offset, generation)
            said, conflict_since = "", None
        except _PollFailed as e:
            if generation != _GENERATION["n"]:
                return                       # нас уже сменили — молча уходим
            # «Другая копия» в первую минуту — обычно не другая копия, а наш
            # же прежний запрос: после перезапуска программы или смены токена
            # Telegram ещё до 50 с держит старый getUpdates и отвечает 409.
            # Говорим о конфликте, только если он держится дольше минуты
            # (жалоба 2026-10-16: сообщение висело сразу после запуска).
            if str(e) == CONFLICT:
                conflict_since = conflict_since or time.time()
                if time.time() - conflict_since < CONFLICT_GRACE_SEC:
                    time.sleep(RETRY_SEC)
                    continue
            _revive_server(str(e))
            _TROUBLE["text"] = str(e)
            if str(e) != said:               # в журнал — один раз на беду
                print(f"[бот] {e}", flush=True)
                said = str(e)
            time.sleep(RETRY_SEC)
        except Exception as e:               # поток бота не должен умирать
            print(f"[бот] сбой: {type(e).__name__}: {e}", flush=True)
            time.sleep(RETRY_SEC)


_REVIVED = {"at": 0.0}


def _revive_server(why):
    """Свой сервер пропал (его поднимала другая копия программы и закрылась,
    или он упал) — поднять снова или найти другой. Не чаще раза в 30 с."""
    import tgserver

    if "нет связи" not in why or not tgserver.url() or time.time() - _REVIVED["at"] < 30:
        return
    _REVIVED["at"] = time.time()
    if tgserver._alive(tgserver.url(), timeout=2.0):
        return
    print("[бот] сервер Telegram не отвечает — поднимаю заново", flush=True)
    tgserver._STATE.update(url="")
    try:
        setup_server()
    except Exception as e:
        print(f"[бот] сервер: {type(e).__name__}: {e}", flush=True)


def _drop_stale_drafts():
    """Видео прислали, а кнопку времени так и не нажали — через 2 дня убираем,
    чтобы файлы не копились в очереди."""
    try:
        for row in jobs.stale_drafts():
            jobs.cancel(row["id"])
            try:
                os.remove(row["video_path"])
            except OSError:
                pass
    except Exception:
        pass


class _PollFailed(Exception):
    pass


UPDATES = json.dumps(["message", "callback_query", "my_chat_member"])


# Сколько сообщений уже разобрано — общее для потоков опроса. При смене
# токена старый поток мог как раз разбирать пачку, а новый начинал с нуля и
# получал ту же пачку снова (Telegram считает сообщение прочитанным только
# на следующем getUpdates): человеку приходило два ответа на одно сообщение.
# Теперь новый поток ждёт, пока старый доразберёт, и продолжает с его места.
_OFFSET = {}                 # номер бота -> с какого сообщения читать
_HANDLING = threading.Lock()


# Тоже на диске: Telegram считает сообщение прочитанным только на СЛЕДУЮЩЕМ
# getUpdates. Закрыли программу сразу после ответа — при запуске та же пачка
# приходила снова, и на /stats бот отвечал дважды (живьём 2026-10-18).
def _offset_path():
    return os.path.join(config.BASE, "tg_offset.json")


def _offset_saved():
    try:
        with open(_offset_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _offset_store(bot, n):
    data = _offset_saved()
    if data.get(bot) == n:
        return
    data[bot] = n
    try:
        with open(_offset_path(), "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        pass


def _offset_for(token):
    with _HANDLING:
        bot = _bot_of(token)
        return _OFFSET.get(bot) or int(_offset_saved().get(bot) or 0)


def _poll_once(offset, generation=None):
    """Один круг getUpdates. Возвращает новый offset."""
    # Список обновлений — явно: Telegram помнит его от прошлого опроса, и
    # программа, когда-то попросившая только «message», лишила бы нас кнопок
    # и вступлений в чаты.
    asked_with = load_settings()["token"]
    res = _call("getUpdates", {"offset": offset, "timeout": 50,
                               "allowed_updates": UPDATES})
    # Пока ждали ответа, токен сменили: пришедшее — для старого бота, и
    # отвечать на него новым токеном нельзя.
    if generation is not None and generation != _GENERATION["n"]:
        return offset
    if not res.get("ok"):
        raise _PollFailed(_why_failed(res))
    if _TROUBLE["text"]:
        print("[бот] снова на связи", flush=True)
        _TROUBLE["text"] = ""
    with _HANDLING:
        if generation is not None and generation != _GENERATION["n"]:
            return offset                    # сменили, пока ждали замок
        if load_settings()["token"] != asked_with:
            return offset                    # пришло старому боту — не нам
        mark = _bot_of(asked_with)
        for upd in res.get("result", []):
            offset = upd["update_id"] + 1
            _OFFSET[mark] = offset
            _dispatch(upd)
            _offset_store(mark, offset)
    return offset


def _dispatch(upd):
    """Одно обновление: кнопка, вступление в чат или сообщение."""
    if upd.get("callback_query"):
        try:
            _handle_callback(upd["callback_query"])
        except Exception as e:
            send(f"ошибка кнопки: {type(e).__name__}: {e}")
            _call("answerCallbackQuery", {"callback_query_id":
                                          upd["callback_query"].get("id", "")}, timeout=10)
        finally:
            set_reply(None)
        return
    if upd.get("my_chat_member"):
        try:
            _membership(upd["my_chat_member"])
        except Exception as e:
            print(f"[бот] вступление в чат: {type(e).__name__}: {e}", flush=True)
        finally:
            set_reply(None)
        return
    msg = upd.get("message")
    if msg:
        try:
            _handle(msg)
        except Exception as e:
            send(f"ошибка обработки: {type(e).__name__}: {e}")
        finally:
            # Обязательно: иначе следующее сообщение от другого
            # человека ушло бы в чат предыдущего.
            set_reply(None)


_THREAD = None
_GENERATION = {"n": 0}


def stop():
    """Бросить опрос: поток уходит на ближайшей проверке. Нужен окну — у
    другого телефона другой бот, и старого опрашивать оно больше не должно."""
    global _THREAD
    _GENERATION["n"] += 1
    _TROUBLE["text"] = ""
    _THREAD = None


def restart():
    """Сменили токен — бросить старый опрос и начать новый с нуля."""
    stop()
    return start_background()


def start_background():
    """Поднять приёмник в фоне. Возвращает функцию отправки отчётов.

    Пишет в журнал, что поднялся и под каким именем: иначе «бот молчит» не
    отличить от «бот и не запускался».
    """
    global _THREAD
    if not load_settings()["token"]:
        return lambda text: None
    if _THREAD is None or not _THREAD.is_alive():
        _THREAD = threading.Thread(target=poll_forever, args=(_GENERATION["n"],),
                                   daemon=True)
        _THREAD.start()
    return send


def running():
    return _THREAD is not None and _THREAD.is_alive()
