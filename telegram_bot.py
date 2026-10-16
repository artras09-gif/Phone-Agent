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

def settings_path():
    return os.path.join(config.BASE, "telegram.json")


def load_settings():
    """Токен и владелец. Файл рядом с программой важнее переменных окружения:
    в собранном exe переменные задавать неудобно."""
    data = {"token": config.TELEGRAM_TOKEN, "owner": config.TELEGRAM_OWNER}
    try:
        with open(settings_path(), encoding="utf-8") as f:
            saved = json.load(f)
        data["token"] = str(saved.get("token") or data["token"]).strip()
        # Токен, сохранённый с мусором прежней версией, чистим и при чтении:
        # после обновления бот оживает сам, без повторной вставки.
        data["token"] = clean_token(data["token"]) or data["token"]
        data["owner"] = str(saved.get("owner") or data["owner"]).strip()
    except (OSError, json.JSONDecodeError):
        pass
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


def save_settings(token, owner, api_id=None, api_hash=None):
    """Сохранить. api_id / api_hash — None: оставить, что было (их вписывают
    отдельно от токена, и сохранение токена не должно их стирать)."""
    token = clean_token(token) if str(token or "").strip() else ""
    try:
        with open(settings_path(), encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except (OSError, json.JSONDecodeError):
        data = {}
    data.update(token=token, owner=str(owner).strip())
    if api_id is not None:
        data["api_id"] = re.sub(r"\D", "", str(api_id))
    if api_hash is not None:
        data["api_hash"] = re.sub(r"[^0-9a-fA-F]", "", str(api_hash)).lower()
    with open(settings_path(), "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return settings_path()


def _saved():
    try:
        with open(settings_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def api_keys():
    """(api_id, api_hash) с my.telegram.org — для своего сервера. Нет — ("", "")."""
    data = _saved()
    return str(data.get("api_id") or ""), str(data.get("api_hash") or "")


def api_base():
    """Куда ходит бот: свой сервер (файлы до 2 ГБ) или облако (до 20 МБ)."""
    import tgserver
    return tgserver.url() or API


def max_download_mb():
    import tgserver
    return 2000 if tgserver.url() else MAX_DOWNLOAD_MB


def _token_mark(token):
    import hashlib
    return hashlib.sha1(str(token).encode()).hexdigest()[:12]


def setup_server():
    """Включить свой сервер Bot API, если вписаны api_id и api_hash.

    Перед первой работой через свой сервер бота нужно один раз «выписать»
    из облака (logOut) — иначе Telegram не обещает доставлять ему сообщения.
    Отметка (не сам токен) хранится в telegram.json, чтобы не делать это
    на каждом запуске. Не вышло поднять сервер — бот остаётся в облаке, как
    раньше: из-за него ничего не ломается.
    """
    import tgserver

    cfg = load_settings()
    api_id, api_hash = api_keys()
    if not (api_id and api_hash and cfg["token"]):
        tgserver.stop()
        return ""
    local = tgserver.start(api_id, api_hash, token=cfg["token"])
    if not local:
        print(f"[бот] свой сервер Telegram не поднялся: {tgserver.why()} — работаю через облако",
              flush=True)
        return ""
    mark = _token_mark(cfg["token"])
    data = _saved()
    if data.get("cloud_logged_out") != mark:
        try:
            req = urllib.request.Request(f"{API}/bot{cfg['token']}/logOut", data=b"")
            with urllib.request.urlopen(req, timeout=20) as r:
                r.read()
        except urllib.error.HTTPError:
            pass                    # уже выписан — тоже годится
        except Exception as e:
            print(f"[бот] не смог выписать бота из облака ({e}) — пробую так", flush=True)
            data = None
        if data is not None:
            data["cloud_logged_out"] = mark
            with open(settings_path(), "w", encoding="utf-8", newline="\n") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
    print("[бот] свой сервер Telegram на этом ПК: видео до 2 ГБ", flush=True)
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


def _call(method, params=None, timeout=70):
    token = load_settings()["token"]
    url = f"{api_base()}/bot{token}/{method}"
    data = urllib.parse.urlencode(params or {}).encode()
    try:
        with urllib.request.urlopen(url, data=data, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
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


def current_chat():
    return getattr(_REPLY, "chat", None)


def reply_address():
    """Куда отвечать — строкой для задачи: «чат» или «чат:тема»."""
    chat = current_chat()
    if not chat:
        return ""
    thread = current_thread()
    return f"{chat}:{thread}" if thread else chat


def reply_to(address):
    """Обратно из строки задачи. Пустая — владельцу, как раньше.

    У групп Telegram отрицательные номера, у людей — положительные.
    """
    chat, _, thread = str(address or "").partition(":")
    set_reply(chat or None, thread=int(thread) if thread.isdigit() else None,
              group=chat.startswith("-"))


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


def allowed_groups():
    return {str(g.get("id")): g.get("title", "") for g in _saved().get("groups", [])
            if isinstance(g, dict) and g.get("id")}


def _set_groups(groups):
    data = _saved()
    data["groups"] = [{"id": k, "title": v} for k, v in groups.items()]
    with open(settings_path(), "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def allow_group(chat_id, title=""):
    groups = allowed_groups()
    if str(chat_id) not in groups:
        groups[str(chat_id)] = title
        _set_groups(groups)


def drop_group(chat_id):
    groups = allowed_groups()
    if groups.pop(str(chat_id), None) is not None:
        _set_groups(groups)


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
               "• /queue, /cancel 5, /status, /stop, /links\n\n"
               "Публиковать может любой участник этого чата.")
PRIVACY_HINT = ("\n\n⚠ Сейчас я вижу в этом чате только команды. Чтобы видел видео и ссылки: "
                "сделай меня администратором чата — или в @BotFather: /setprivacy → "
                "выбери меня → Disable, потом убери и снова добавь меня в чат.")


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


def send(text, preview=True, chat_id=None, keyboard=False):
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
        if current_thread() and str(target) == str(current_chat()):
            params["message_thread_id"] = current_thread()
        # Клавиатуру вешаем на ПОСЛЕДНИЙ кусок: длинная подборка режется на
        # части, и приклей её к первому — кнопки оказались бы в середине.
        # В группе — не вешаем: она вылезла бы у всех участников.
        if keyboard and n == len(chunks) - 1 and not (in_group() and str(target) == str(current_chat())):
            params["reply_markup"] = KEYBOARD
        res = _call("sendMessage", params, timeout=20)
        ok = ok and bool(res.get("ok"))
    return ok


def send_photo(image, caption="", chat_id=None):
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
    head = "".join(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'
        for k, v in (("chat_id", target), ("caption", caption[:1000])))
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


def _send_links(rows, title, mark):
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
    for part in parts:
        ok = send(part, preview=False) and ok
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
    if not _send_links(rows, f"Понравилось ({day})", mark=True):
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
    who = "открыт всем" if config.BOT_OPEN else f"только для {cfg['owner']}"
    if not cfg["owner"]:
        # Не поломка: отвечать он уже может. Некуда слать только неспрошенное —
        # вечернюю подборку и отчёты из окна.
        return True, f"@{name}, {who}; напиши ему — запомню, куда слать подборку"
    return True, f"@{name}, {who}; неспрошенное шлю в {cfg['owner']}"


def _download(file_id, dest_dir):
    info = _call("getFile", {"file_id": file_id}, timeout=30)
    if not info.get("ok"):
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
            return None
    url = f"{api_base()}/file/bot{token}/{path}"
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
        except RuntimeError:
            # Занято сессией — и это нормально: обрывать её ради поста нельзя,
            # телефон один, а недосмотренная сессия выглядит подозрительнее
            # любой задержки. Задача остаётся в очереди, и сторож в окне
            # (webui.Dispatcher) публикует её, как только телефон освободится.
            send("Сейчас идёт сессия — не прерываю её. "
                 "Опубликую сразу, как закончится.")
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

    if text.startswith("/stop"):
        # Останавливаем то, что идёт сейчас, а не запрещаем будущее: режимов
        # в программе больше нет, всё остальное работает штатно.
        abort.request()
        send("Останавливаю текущее действие.")
        return
    if text.startswith("/start") and group:
        send(GROUP_HELLO + ("" if _me().get("can_read_all_group_messages") else PRIVACY_HINT))
        return
    if text.startswith("/start"):
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
             "/stop — прервать текущее действие", keyboard=True)
        return
    if text.startswith("/status"):
        send(
            f"В очереди: {len(jobs.pending())}\n"
            f"Постов сегодня: {jobs.count_today('done')}/{config.MAX_POSTS_PER_DAY}\n"
            f"Сессий сегодня: {jobs.sessions_today()}\n"
            f"Ссылок ждёт подборки: {jobs.links_waiting()}"
        )
        return
    if text == BTN_LINKS:
        # Именно "/links", а не текст кнопки: разбор берёт второе слово как
        # довесок, и «Ссылки» из подписи он принял бы за аргумент.
        _cmd_links("/links")
        return
    if text == BTN_POST or text.startswith("/queue") or text.lower() == "очередь":
        # Публикацию кнопкой не заменить — нужен сам файл. Поэтому здесь
        # очередь и напоминание, как прислать видео.
        send(_queue_text() + "\n\nЧтобы опубликовать — пришли ВИДЕО с подписью "
             "(подпись = описание). Куда и когда — выберешь кнопками.", keyboard=True)
        return
    if text.startswith("/template") or text.lower() in ("шаблон", "/шаблон"):
        import postspec
        send(postspec.TEMPLATE_HELP)
        return
    if text.startswith("/cancel") or text.lower().startswith("отмена"):
        _cmd_cancel(text)
        return
    if text.startswith("/links") or text.lower().startswith("ссылки"):
        _cmd_links(text)
        return
    if text and not (msg.get("video") or msg.get("document") or msg.get("animation")) \
            and _take_time_answer(chat_id, text, sender):
        return

    # Ссылки принимаем до видео: сообщение со ссылкой — это не публикация,
    # а пополнение подборки. Разметка нужна потому, что в пересланном посте
    # адрес бывает спрятан за текстом и в самом тексте его нет.
    urls = weblink.find_urls(text, msg.get("entities") or msg.get("caption_entities"))
    if urls and not (msg.get("video") or msg.get("document") or msg.get("animation")):
        _save_incoming(urls, weblink.strip_urls(text))
        return

    video = msg.get("video") or msg.get("document") or msg.get("animation")
    if not video:
        # В группе на обычную болтовню не отвечаем — только если обратились.
        if text and addressed:
            send("Пришли видео файлом — опубликую, или ссылку — сохраню.",
                 keyboard=True)
        return

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

    send("Качаю...")
    path = _download(video["file_id"], config.QUEUE_DIR)
    if not path:
        send("Не смог скачать файл.")
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
        job_id = jobs.add(path, caption=caption, targets=",".join(targets),
                          status="draft", chat=reply_address())
        _send_draft(job_id)
        return

    job_id = jobs.add(path, caption=spec["caption"], targets=",".join(spec["targets"]),
                      run_at=spec["when"].timestamp() if spec["when"] else None,
                      captions=spec["captions"], exact=spec["exact"],
                      chat=reply_address())
    send(postspec.describe(spec, job_id) + f"\n\nОтменить: /cancel {job_id}")
    if spec["when"] is None:
        _publish_now(job_id)


# --------------------------------------------------- пост кнопками (2026-10-11)
#
# «Какая-то странная хуйня, как-то проще можно?» — про шаблон «куда/когда/
# описание». Можно: подпись к видео — это описание, а сети и время выбираются
# кнопками под одним сообщением. Состояние черновика живёт в базе (сети — в
# `jobs.targets`, статус 'draft'), поэтому кнопки работают и после перезапуска.

TARGET_ORDER = ("tiktok", "reels", "shorts")
_ASK_TIME = {}          # чат -> задача, для которой ждём «своё время» текстом


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
    skipped = [postspec.TITLES[t] for t in TARGET_ORDER if t not in picked]
    if skipped:
        text += "Не публикую в: " + ", ".join(skipped) + "\n\n"
    return text + "Проверь сети и нажми, когда публиковать 👇"


def _send_draft(job_id):
    job = jobs.by_id(job_id)
    cfg = load_settings()
    target = current_chat() or cfg["owner"]
    params = {"chat_id": target, "text": _draft_text(job), "reply_markup": _draft_markup(job)}
    if current_thread() and str(target) == str(current_chat()):
        params["message_thread_id"] = current_thread()
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
            _ASK_TIME[f"{chat_id}:{who}"] = (job["id"], message_id)
            send("Напиши время одним сообщением: 18:30, завтра 9:00, 12.10 18:30 или пт 20:00")
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

    job_id, message_id = waiting
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
RETRY_SEC = 5            # пауза перед повтором после неудачного опроса
CONFLICT_GRACE_SEC = 75  # 409 короче этого — наш же прежний запрос, не другая копия


def trouble():
    return _TROUBLE["text"]


def _why_failed(res):
    """Человеческая причина неудачного getUpdates."""
    text = str(res.get("description") or res.get("error") or "")
    code = res.get("error_code")
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
    offset = 0
    said = ""
    conflict_since = None
    token = load_settings()["token"]
    while True:
        if generation != _GENERATION["n"]:
            return                           # нас сменил поток с новым токеном
        now_token = load_settings()["token"]
        if now_token != token:
            # Токен поменяли в обход окна (руками в telegram.json).
            token, offset, said = now_token, 0, ""
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
            _TROUBLE["text"] = str(e)
            if str(e) != said:               # в журнал — один раз на беду
                print(f"[бот] {e}", flush=True)
                said = str(e)
            time.sleep(RETRY_SEC)
        except Exception as e:               # поток бота не должен умирать
            print(f"[бот] сбой: {type(e).__name__}: {e}", flush=True)
            time.sleep(RETRY_SEC)


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


def _poll_once(offset, generation=None):
    """Один круг getUpdates. Возвращает новый offset."""
    res = _call("getUpdates", {"offset": offset, "timeout": 50})
    # Пока ждали ответа, токен сменили: пришедшее — для старого бота, и
    # отвечать на него новым токеном нельзя.
    if generation is not None and generation != _GENERATION["n"]:
        return offset
    if not res.get("ok"):
        raise _PollFailed(_why_failed(res))
    if _TROUBLE["text"]:
        print("[бот] снова на связи", flush=True)
        _TROUBLE["text"] = ""
    for upd in res.get("result", []):
        offset = upd["update_id"] + 1
        if upd.get("callback_query"):
            try:
                _handle_callback(upd["callback_query"])
            except Exception as e:
                send(f"ошибка кнопки: {type(e).__name__}: {e}")
            finally:
                set_reply(None)
            continue
        if upd.get("my_chat_member"):
            try:
                _membership(upd["my_chat_member"])
            except Exception as e:
                print(f"[бот] вступление в чат: {type(e).__name__}: {e}", flush=True)
            finally:
                set_reply(None)
            continue
        msg = upd.get("message") or upd.get("channel_post")
        if msg:
            try:
                _handle(msg)
            except Exception as e:
                send(f"ошибка обработки: {type(e).__name__}: {e}")
            finally:
                # Обязательно: иначе следующее сообщение от другого
                # человека ушло бы в чат предыдущего.
                set_reply(None)
    return offset


_THREAD = None
_GENERATION = {"n": 0}


def restart():
    """Сменили токен — бросить старый опрос и начать новый с нуля."""
    global _THREAD
    _GENERATION["n"] += 1
    _TROUBLE["text"] = ""
    _THREAD = None
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
