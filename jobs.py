"""Очередь задач в SQLite.

Зачем БД, а не список в памяти: ПК перезагрузится, служба упадёт —
задачи останутся. Плюс видно историю и можно повторить упавшее.
"""
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
                ("rules", "TEXT DEFAULT ''"), ("device", "TEXT DEFAULT ''")],
    "jobs": [("device", "TEXT DEFAULT ''")],
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
        _SCHEMA_READY = True
    return con


def add(video_path, caption="", targets="tiktok", run_at=None):
    with connect() as con:
        cur = con.execute(
            "INSERT INTO jobs (video_path, caption, targets, run_at, created_at,"
            " device) VALUES (?,?,?,?,?,?)",
            (video_path, caption, targets, run_at or time.time(), time.time(),
             DEVICE),
        )
        return cur.lastrowid


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
    with connect() as con:
        con.execute(
            "INSERT OR IGNORE INTO content "
            "(session,app,frame,tema,category,screen_text,ad,lang,people,raw,at,"
            " caption,author,device) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
             DEVICE),
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


def set_verdict(frame, verdict, skipped):
    with connect() as con:
        con.execute("UPDATE content SET verdict=?, skipped=?, rules=? "
                    "WHERE frame=?",
                    (verdict, 1 if skipped else 0, RULES, frame))


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
