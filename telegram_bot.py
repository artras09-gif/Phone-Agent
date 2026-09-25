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
        data["owner"] = str(saved.get("owner") or data["owner"]).strip()
    except (OSError, json.JSONDecodeError):
        pass
    return data


def save_settings(token, owner):
    with open(settings_path(), "w", encoding="utf-8", newline="\n") as f:
        json.dump({"token": token.strip(), "owner": str(owner).strip()},
                  f, ensure_ascii=False, indent=2)
    return settings_path()


# --------------------------------------------------------------- разбор

def parse_targets(text):
    """(куда постить, текст без служебных слов).

    Ничего не нашли — значит во все сети: так просил пользователь.
    """
    found, cleaned = [], text
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
    url = f"{API}/bot{token}/{method}"
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


def set_reply(chat_id):
    """Кому уйдут следующие `send` в ЭТОМ потоке. None — снова владельцу."""
    _REPLY.chat = str(chat_id) if chat_id else None


def current_chat():
    return getattr(_REPLY, "chat", None)


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
        # Клавиатуру вешаем на ПОСЛЕДНИЙ кусок: длинная подборка режется на
        # части, и приклей её к первому — кнопки оказались бы в середине.
        if keyboard and n == len(chunks) - 1:
            params["reply_markup"] = KEYBOARD
        res = _call("sendMessage", params, timeout=20)
        ok = ok and bool(res.get("ok"))
    return ok


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
    url = f"{API}/file/bot{token}/{path}"
    name = f"{int(time.time())}_{os.path.basename(path)}"
    dest = os.path.join(dest_dir, name)
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
    chat = current_chat()

    def in_thread():
        set_reply(chat)
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
    chat_id = str(msg.get("chat", {}).get("id", ""))

    # Отвечаем в ТОТ ЖЕ чат, откуда пришло. Ставится до первого `send` и
    # снимается в `poll_forever`, иначе следующее сообщение от другого
    # человека уехало бы предыдущему.
    set_reply(chat_id)

    # Первый написавший запоминается как адрес для НЕСПРОШЕННОГО (вечерняя
    # подборка, отчёты из окна) — им отвечать некуда. Доступ это не даёт и не
    # ограничивает: при `config.BOT_OPEN` пишет кто угодно.
    if not cfg["owner"]:
        save_settings(cfg["token"], chat_id)
        cfg = load_settings()
    if not config.BOT_OPEN and chat_id != str(cfg["owner"]):
        return                                    # чужие игнорируются молча

    text = (msg.get("text") or msg.get("caption") or "").strip()

    if text.startswith("/stop"):
        # Останавливаем то, что идёт сейчас, а не запрещаем будущее: режимов
        # в программе больше нет, всё остальное работает штатно.
        abort.request()
        send("Останавливаю текущее действие.")
        return
    if text.startswith("/start"):
        when = ", ".join(config.LINKS_DIGEST_TIMES) or "только по команде"
        send("Работаю.\n\n"
             "ВИДЕО файлом с подписью — опубликую.\n"
             "Сети можно указать в подписи (tiktok, shorts, reels), "
             "не указаны — публикую во все.\n"
             "Время («в 18:30») ставит задачу на это время.\n\n"
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
    if text == BTN_POST:
        # Публикацию кнопкой не заменить — нужен сам файл. Поэтому здесь
        # состояние очереди и напоминание, как прислать видео.
        send("В очереди: %d\n"
             "Опубликовано сегодня: %d/%d\n\n"
             "Чтобы опубликовать — пришли ВИДЕО файлом.\n"
             "В подписи можно указать сети (tiktok, shorts, reels) и "
             "время («в 18:30»). Без них публикую сразу во все."
             % (len(jobs.pending()), jobs.count_today("done"),
                config.MAX_POSTS_PER_DAY), keyboard=True)
        return
    if text.startswith("/links") or text.lower().startswith("ссылки"):
        _cmd_links(text)
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
        if text:
            send("Пришли видео файлом — опубликую, или ссылку — сохраню.",
                 keyboard=True)
        return

    targets, text = parse_targets(text)
    when, caption = parse_when(text)

    send("Качаю...")
    path = _download(video["file_id"], config.QUEUE_DIR)
    if not path:
        send("Не смог скачать файл.")
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

    # Время не названо и расписание пустое — значит ждать нечего.
    now_post = when is None and not config.POST_TIMES
    job_id = jobs.add(path, caption=caption, targets=",".join(targets),
                      run_at=when.timestamp() if when else None)

    where = ", ".join(targets)
    if when is not None:
        send(f"Задача #{job_id}: {where}, опубликую в {when:%H:%M}.")
        return
    if not now_post:
        send(f"Задача #{job_id}: {where}, опубликую по расписанию "
             f"({', '.join(config.POST_TIMES)}).")
        return

    send(f"Задача #{job_id}: {where}, публикую сейчас.")

    def work():
        import scheduler
        report = scheduler.run_post_job(job_id=job_id)
        send(report or "готово")

    _run(f"публикация #{job_id}", work)


def poll_forever():
    if not load_settings()["token"]:
        return
    offset = 0
    while True:
        res = _call("getUpdates", {"offset": offset, "timeout": 50})
        if not res.get("ok"):
            time.sleep(5)
            continue
        for upd in res.get("result", []):
            offset = upd["update_id"] + 1
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


_THREAD = None


def start_background():
    """Поднять приёмник в фоне. Возвращает функцию отправки отчётов."""
    global _THREAD
    if not load_settings()["token"]:
        return lambda text: None
    if _THREAD is None or not _THREAD.is_alive():
        _THREAD = threading.Thread(target=poll_forever, daemon=True)
        _THREAD.start()
    return send


def running():
    return _THREAD is not None and _THREAD.is_alive()
