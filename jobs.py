"""Очередь задач в SQLite.

Зачем БД, а не список в памяти: ПК перезагрузится, служба упадёт —
задачи останутся. Плюс видно историю и можно повторить упавшее.
"""
import json
import os
import sqlite3
import time

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    video_path TEXT    NOT NULL,
    caption    TEXT    DEFAULT '',
    targets    TEXT    DEFAULT 'tiktok',
    run_at     REAL,
    status     TEXT    DEFAULT 'pending',
    attempts   INTEGER DEFAULT 0,
    result     TEXT    DEFAULT '',
    created_at REAL,
    done_at    REAL
);
CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    kind    TEXT,
    payload TEXT,
    at      REAL
);
CREATE TABLE IF NOT EXISTS links (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    url     TEXT UNIQUE,     -- один ролик — одна строка
    app     TEXT,
    likes   INTEGER,
    author  TEXT DEFAULT '',
    caption TEXT DEFAULT '',
    session TEXT DEFAULT '',
    frame   TEXT DEFAULT '',
    at      REAL,
    device  TEXT DEFAULT '',
    sent    REAL DEFAULT 0   -- когда ушла в подборку; 0 — ещё не отправляли
);
CREATE TABLE IF NOT EXISTS content (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session    TEXT,
    app        TEXT,
    frame      TEXT UNIQUE,     -- путь к кадру: он же защита от повторного разбора
    tema       TEXT,
    category   TEXT,
    screen_text TEXT,
    ad         INTEGER DEFAULT 0,
    lang       TEXT,
    people     INTEGER DEFAULT 0,
    raw        TEXT,
    at         REAL
);
"""


# Колонки, добавленные после первой версии схемы. CREATE TABLE IF NOT EXISTS
# существующую таблицу не трогает, поэтому старую базу надо дополнять руками.
MIGRATIONS = {
    "content": [("verdict", "TEXT DEFAULT ''"), ("skipped", "INTEGER DEFAULT 0"),
                ("caption", "TEXT DEFAULT ''"), ("author", "TEXT DEFAULT ''"),
                ("rules", "TEXT DEFAULT ''"), ("device", "TEXT DEFAULT ''"),
                # Размер кадра в КБ — пишется при разборе, потому что сам кадр
                # после разбора удаляется, а по размеру `stats` узнаёт пустые
                # (погашенный экран) кадры.
                ("frame_kb", "INTEGER DEFAULT 0"),
                # Совпала ли тема: 1 / 0, -1 — тему не проверяли. По этому
                # `interests.tuning` понимает, насколько лента уже про тему.
                ("on_topic", "INTEGER DEFAULT -1"),
                # На какую тему проверяли: сменили тему во «Вкусах» — старые
                # отметки «по теме» к новой не относятся.
                ("topic_key", "TEXT DEFAULT ''")],
    # captions — своё описание для отдельных сетей (JSON {сеть: текст});
    # exact — время названо явно: ради него сессия приостанавливается
    # (см. `postspec`, 2026-10-11).
    "jobs": [("device", "TEXT DEFAULT ''"), ("captions", "TEXT DEFAULT ''"),
             ("exact", "INTEGER DEFAULT 0"),
             # Отпечаток видео (размер + начало и конец файла): одно и то же
             # видео не ставится второй раз, пока прежняя задача ждёт или идёт
             # (живьём 2026-10-16: одно видео вышло в Instagram дважды).
             ("fingerprint", "TEXT DEFAULT ''"),
             # Откуда заказали из бота («чат» или «чат:тема»): отчёт о посте,
             # вышедшем по времени, идёт туда же — в группу, а не в личку.
             ("chat", "TEXT DEFAULT ''")],
    "events": [("device", "TEXT DEFAULT ''")],
    "links": [("sent", "REAL DEFAULT 0")],
}

# Чей это телефон. Пустая строка — «до разделения» и «телефон один»: такие
# строки видны из любого пространства, иначе переход на пространства стёр бы
# из окна всю прежнюю очередь и статистику.
DEVICE = ""


def _mine(alias=""):
    """Кусок WHERE и параметр к нему: строки этого телефона плюс общие."""
    col = f"{alias}.device" if alias else "device"
    return f" AND ({col}=? OR {col}='' OR {col} IS NULL)", (DEVICE,)

# По каким правилам вынесен вердикт. Нужно, потому что имена вердиктов
# переживают смену правил: «интересно» и «нейтрально» назывались так и в
# старой схеме со списками категорий, где значили другое и жили по другим
# вероятностям. Без метки отчёт молча смешивал бы эпохи.
#
# Версию поднимать и тогда, когда имена те же, но смысл поехал: в /4
# «нейтрально» разделилось надвое (тема без языка — фиксированные секунды,
# язык без темы — доля), и медиана по ступени стала другой величиной.
RULES = "тема+язык/7"


_SCHEMA_READY = False


def connect():
    """Соединение с базой. Схему проверяем один раз за запуск.

    Раньше CREATE TABLE и PRAGMA гонялись при каждом подключении, а в сессии
    их два на каждый ролик — это стоило заметной доли времени там, где важна
    каждая десятая секунды.
    """
    global _SCHEMA_READY

    con = sqlite3.connect(config.DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    if not _SCHEMA_READY:
        # WAL обязателен, когда телефонов несколько: у каждого свой ПРОЦЕСС,
        # и все они пишут в эту базу. В режиме `delete` пишущий запирает базу
        # целиком — сессия одного телефона вставала бы на публикации другого.
        # В WAL читатели и писатель не мешают друг другу.
        #
        # Режим — свойство самого файла, ставится один раз и переживает
        # перезапуск; повторный вызов ничего не стоит. `synchronous=NORMAL` —
        # обычная пара к WAL: сохранностью транзакций не рискуем, теряется
        # разве что последний коммит при отключении питания, а тут очередь
        # роликов, а не бухгалтерия.
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.DatabaseError:
            # Базу могли положить на сетевую шину, где WAL не работает.
            # Это не повод не запускаться: останемся на прежнем режиме.
            pass
        con.executescript(SCHEMA)
        for table, columns in MIGRATIONS.items():
            have = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
            for name, decl in columns:
                if name not in have:
                    con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        if con.execute("PRAGMA user_version").fetchone()[0] < CATEGORIES_VERSION:
            recategorize(con)
            con.execute(f"PRAGMA user_version={CATEGORIES_VERSION}")
            con.commit()
        _SCHEMA_READY = True
    return con


# Версия раскладки по категориям. Поднять — и при следующем запуске вся
# накопленная история переложится заново (`recategorize`), а не только новые
# кадры: иначе сводка за неделю ещё неделю показывала бы старую картину.
CATEGORIES_VERSION = 1


def recategorize(con):
    """Переложить уже разобранные кадры по нынешнему списку категорий.

    Модель при этом не нужна: в базе есть её описание ролика и, для слов вне
    списка, само слово («категория вне списка: природа»). Сколько переложено.
    """
    import vision

    moved = 0
    rows = con.execute("SELECT id, category, tema, raw FROM content").fetchall()
    for r in rows:
        said = r["category"] or ""
        raw = r["raw"] or ""
        if raw.startswith("категория вне списка:"):
            said = raw.split(":", 1)[1].strip() or said
        new = vision.categorize(said, r["tema"] or "")
        if new != r["category"]:
            con.execute("UPDATE content SET category=? WHERE id=?", (new, r["id"]))
            moved += 1
    return moved


def video_fingerprint(path):
    """Отпечаток видеофайла: размер + sha1 первого и последнего мегабайта.

    Весь файл не читаем — видео бывают по сотне мегабайт, а совпадение
    размера и обоих краёв у разных роликов не встречается.
    """
    import hashlib

    try:
        size = os.path.getsize(path)
        h = hashlib.sha1(str(size).encode())
        with open(path, "rb") as f:
            h.update(f.read(1 << 20))
            if size > 2 << 20:
                f.seek(-(1 << 20), os.SEEK_END)
                h.update(f.read(1 << 20))
        return h.hexdigest()
    except OSError:
        return ""


# Сколько после публикации то же видео в те же сети считается повтором.
DUPLICATE_DONE_HOURS = 6


def duplicate_of(fp, targets=None, now=None):
    """Задача с тем же видео, из-за которой новую ставить нельзя, или None.

    Повтор — если прежняя ещё ждёт, черновик или публикуется; или уже вышла
    в ЭТИ ЖЕ сети за последние DUPLICATE_DONE_HOURS часов. В другие сети то
    же видео ставить можно: сначала TikTok, потом Reels — обычное дело.
    """
    if not fp:
        return None
    now = now or time.time()
    wanted = {t for t in str(targets or "").split(",") if t}
    with connect() as con:
        rows = con.execute(
            "SELECT * FROM jobs WHERE fingerprint=? AND status IN "
            "('pending', 'draft', 'running', 'done') ORDER BY id DESC", (fp,)).fetchall()
    for row in rows:
        if row["status"] != "done":
            return row
        recent = (row["done_at"] or 0) > now - DUPLICATE_DONE_HOURS * 3600
        same = not wanted or wanted & {t for t in row["targets"].split(",") if t}
        if recent and same:
            return row
    return None


def set_status(job_id, status):
    with connect() as con:
        con.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))


def running():
    with connect() as con:
        where, args = _mine()
        return con.execute("SELECT * FROM jobs WHERE status='running'" + where, args).fetchall()


def interrupt_running():
    """Задачи, оставшиеся «публикуется» после обрыва программы, — в «сбой».

    Повторять их сами нельзя: пост мог уже выйти, и второй раз — дубль.
    """
    with connect() as con:
        where, args = _mine()
        cur = con.execute(
            "UPDATE jobs SET status='failed', result='прервано: программа закрылась "
            "посреди публикации — проверь, вышел ли пост', done_at=? "
            "WHERE status='running'" + where, (time.time(),) + args)
        return cur.rowcount


def add(video_path, caption="", targets="tiktok", run_at=None, captions=None,
        exact=False, status="pending", fingerprint="", chat=""):
    """Задача в очередь. status="draft" — черновик из бота: видео скачано, но
    сети и время человек ещё выбирает кнопками; публиковать его нельзя."""
    if not fingerprint and os.path.isfile(video_path):
        fingerprint = video_fingerprint(video_path)
    with connect() as con:
        cur = con.execute(
            "INSERT INTO jobs (video_path, caption, targets, run_at, created_at,"
            " device, captions, exact, status, fingerprint, chat)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (video_path, caption, targets, run_at or time.time(), time.time(),
             DEVICE, json.dumps(captions or {}, ensure_ascii=False),
             1 if exact else 0, status, fingerprint, chat or ""),
        )
        return cur.lastrowid


def set_targets(job_id, targets):
    with connect() as con:
        con.execute("UPDATE jobs SET targets=? WHERE id=?", (targets, job_id))


def schedule(job_id, run_at, exact):
    """Черновик -> в очередь: время выбрано."""
    with connect() as con:
        con.execute("UPDATE jobs SET run_at=?, exact=?, status='pending' "
                    "WHERE id=? AND status IN ('draft', 'pending')",
                    (run_at or time.time(), 1 if exact else 0, job_id))


def stale_drafts(days=2):
    """Черновики, про которые забыли: видео прислали, кнопку не нажали."""
    with connect() as con:
        return con.execute("SELECT * FROM jobs WHERE status='draft' AND created_at<?",
                           (time.time() - days * 86400,)).fetchall()


def captions_of(job):
    """Своё описание для отдельных сетей задачи: {сеть: текст}."""
    try:
        data = json.loads(job["captions"] or "{}")
    except (KeyError, IndexError, TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def cancel(job_id):
    """Отменить задачу из очереди. Возвращает строку задачи или None."""
    with connect() as con:
        row = con.execute("SELECT * FROM jobs WHERE id=? AND status IN ('pending', 'draft')",
                          (job_id,)).fetchone()
        if row is not None:
            con.execute("UPDATE jobs SET status='cancelled', done_at=? WHERE id=?",
                        (time.time(), job_id))
    return row


def reschedule(job_id, run_at):
    with connect() as con:
        con.execute("UPDATE jobs SET run_at=? WHERE id=?", (run_at, job_id))


def due(now=None):
    now = now or time.time()
    with connect() as con:
        where, args = _mine()
        return con.execute(
            "SELECT * FROM jobs WHERE status='pending' AND run_at<=?" + where
            + " ORDER BY run_at LIMIT 1", (now,) + args
        ).fetchone()


def by_id(job_id):
    with connect() as con:
        return con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()


def pending():
    with connect() as con:
        where, args = _mine()
        return con.execute(
            "SELECT * FROM jobs WHERE status='pending'" + where
            + " ORDER BY run_at", args
        ).fetchall()


def mark(job_id, status, result=""):
    with connect() as con:
        con.execute(
            "UPDATE jobs SET status=?, result=?, done_at=?, attempts=attempts+1 "
            "WHERE id=?",
            (status, result[-4000:], time.time(), job_id),
        )


def _day_start():
    """Полночь по местному времени.

    Раньше здесь было time.time() % 86400 — это полночь по UTC, то есть
    у нас день начинался в 03:00. Дневные лимиты из-за этого считались
    не за те сутки.
    """
    t = time.localtime()
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))


def count_today(status="done"):
    start = _day_start()
    with connect() as con:
        row = con.execute(
            "SELECT COUNT(*) c FROM jobs WHERE status=? AND done_at>=?"
            + _mine()[0], (status, start) + _mine()[1]
        ).fetchone()
        return row["c"]


def log_event(kind, payload=""):
    with connect() as con:
        con.execute(
            "INSERT INTO events (kind,payload,at,device) VALUES (?,?,?,?)",
            (kind, payload[-4000:], time.time(), DEVICE))


def add_content(session, app, frame, data):
    """Записать разбор кадра. Повторный разбор того же кадра игнорируется."""
    try:
        frame_kb = os.path.getsize(frame) // 1024
    except (OSError, TypeError):
        frame_kb = 0
    with connect() as con:
        con.execute(
            "INSERT OR IGNORE INTO content "
            "(session,app,frame,tema,category,screen_text,ad,lang,people,raw,at,"
            " caption,author,device,frame_kb) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (session, app, frame,
             str(data.get("тема", ""))[:500],
             str(data.get("категория", "другое"))[:60],
             str(data.get("текст", ""))[:1000],
             1 if data.get("реклама") else 0,
             str(data.get("язык", ""))[:20],
             0,   # колонка people осталась от старого формата ответа
             str(data.get("сырой", ""))[:1000],
             time.time(),
             str(data.get("описание", ""))[:500],
             str(data.get("автор", ""))[:80],
             DEVICE,
             frame_kb),
        )


def add_link(url, app, likes, author="", caption="", session="", frame=""):
    """Запомнить адрес ролика. True — записали впервые.

    `INSERT OR IGNORE` по уникальному `url`: один ролик встречается не по
    одному разу, а список должен оставаться списком роликов, а не журналом
    встреч.

    Адрес причёсывается ЗДЕСЬ, а не у вызывающего, и это не мелочь: сюда
    ссылки приходят из двух мест — с экрана телефона и из переписки, — и
    один и тот же ролик выглядит по-разному («…/ZTDfNosFW/» с телефона и
    «…/ZTDfNosFW» из чата, плюс метки копирования у присланного). Пока
    чистка стояла только на стороне бота, повтор пролезал мимо UNIQUE —
    поймано на первом же прогоне.

    Оговорка осталась прежняя: TikTok отдаёт КОРОТКУЮ ссылку, и на один и
    тот же ролик она бывает разной. Ловить это можно было бы только развернув
    адрес запросом в сеть, а сеть здесь ни при чём.
    """
    import weblink

    url = weblink.normalize(url) or url
    with connect() as con:
        cur = con.execute(
            "INSERT OR IGNORE INTO links "
            "(url,app,likes,author,caption,session,frame,at,device) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (url, app, int(likes or 0), str(author or "")[:80],
             str(caption or "")[:500], session, frame, time.time(), DEVICE),
        )
        return cur.rowcount > 0


def links(limit=50, unsent=False):
    """Сохранённые ссылки, свежие сверху.

    `unsent=True` — только те, что ещё не уходили в подборку. Отметка живёт
    в базе, а не в файле рядом: телефонов может быть несколько, у каждого
    свой процесс, и общая база — единственное место, где «уже отправлено»
    не разъедется между ними.
    """
    with connect() as con:
        where, args = _mine()
        if unsent:
            where += " AND (sent IS NULL OR sent=0)"
        return con.execute(
            "SELECT * FROM links WHERE 1=1" + where
            + " ORDER BY at DESC LIMIT ?", args + (limit,)
        ).fetchall()


def links_waiting():
    """Сколько ссылок ждёт отправки. Для /status и для решения «будить ли»."""
    with connect() as con:
        where, args = _mine()
        return con.execute(
            "SELECT COUNT(*) FROM links WHERE 1=1" + where
            + " AND (sent IS NULL OR sent=0)", args).fetchone()[0]


def mark_links_sent(ids):
    """Пометить отправленное. Отметка ставится ПОСЛЕ успешной отправки:
    лучше прислать ссылку дважды, чем потерять её из-за оборванной связи."""
    ids = [int(i) for i in ids]
    if not ids:
        return 0
    holes = ",".join("?" * len(ids))
    with connect() as con:
        cur = con.execute(
            f"UPDATE links SET sent=? WHERE id IN ({holes})",
            (time.time(), *ids))
        return cur.rowcount


def set_verdict(frame, verdict, skipped, on_topic=None, topic_key=""):
    topic = -1 if on_topic is None else (1 if on_topic else 0)
    with connect() as con:
        con.execute("UPDATE content SET verdict=?, skipped=?, rules=?, on_topic=?, "
                    "topic_key=? WHERE frame=?",
                    (verdict, 1 if skipped else 0, RULES, topic, topic_key, frame))


def recent_feed(app, limit=40, days=14, topic=None):
    """Последние разобранные ролики ленты: [(язык, по_теме)], новые первыми.

    Без рекламы и без кадров-сбоев — они не про то, что лента показывает.
    По_теме: True/False или None, если тему не проверяли — или проверяли на
    другую (`topic` задан и не совпал с той, что стояла тогда).
    """
    import vision

    since = time.time() - days * 86400
    mine, dev = _mine()
    with connect() as con:
        rows = con.execute(
            "SELECT lang, on_topic, topic_key FROM content WHERE app=? AND at>=? AND ad=0 "
            "AND category!=?" + mine + " ORDER BY at DESC LIMIT ?",
            (app, since, vision.NOT_PARSED) + dev + (int(limit),)).fetchall()
    def judged(r):
        if r["on_topic"] in (None, -1):
            return None
        if topic is not None and (r["topic_key"] or "") != topic:
            return None
        return bool(r["on_topic"])

    return [(r["lang"] or "", judged(r)) for r in rows]


def analyzed_frames():
    with connect() as con:
        return {r["frame"] for r in con.execute("SELECT frame FROM content")}


def content_stats(days=7):
    """Сводка: сколько чего съел аккаунт за N дней. Только этот телефон."""
    since = time.time() - days * 86400
    mine, dev = _mine()
    args = (since,) + dev
    with connect() as con:
        total = con.execute(
            "SELECT COUNT(*) c FROM content WHERE at>=?" + mine, args
        ).fetchone()["c"]
        cats = con.execute(
            "SELECT category, COUNT(*) c FROM content WHERE at>=?" + mine
            + " GROUP BY category ORDER BY c DESC", args
        ).fetchall()
        ads = con.execute(
            "SELECT COUNT(*) c FROM content WHERE at>=? AND ad=1" + mine, args
        ).fetchone()["c"]
        langs = con.execute(
            "SELECT lang, COUNT(*) c FROM content WHERE at>=? AND lang<>''" + mine
            + " GROUP BY lang ORDER BY c DESC LIMIT 5", args
        ).fetchall()
        recent = con.execute(
            "SELECT tema, category, at, verdict, skipped FROM content WHERE at>=?"
            + mine + " ORDER BY at DESC LIMIT 12", args
        ).fetchall()
        verdicts = con.execute(
            "SELECT verdict, COUNT(*) c, SUM(skipped) s FROM content "
            "WHERE at>=? AND verdict<>''" + mine
            + " GROUP BY verdict ORDER BY c DESC", args
        ).fetchall()
    return {"total": total, "cats": cats, "ads": ads, "langs": langs,
            "recent": recent, "verdicts": verdicts}


def sessions_today():
    start = _day_start()
    with connect() as con:
        row = con.execute(
            "SELECT COUNT(*) c FROM events WHERE kind='session' AND at>=?"
            + _mine()[0], (start,) + _mine()[1]
        ).fetchone()
        return row["c"]
