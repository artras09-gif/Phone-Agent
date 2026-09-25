"""Расписание дня.

Ключевая идея: заданное время — это не точка, а центр окна. Публикация
ровно в 12:00:00 каждый день видна в логах платформы за неделю, поэтому
каждый запуск смещается на случайную величину, и число сессий тоже плавает.
"""
import datetime as dt
import os
import random
import time

import config
import jobs
import plan
import poster
import runlog
import session
# Наверху, а не внутри функции: `telegram_bot` тянет scheduler только лениво,
# из тела обработчика, так что кольца здесь не возникает.
import telegram_bot


# План, по которому служба работает прямо сейчас. Время внутри окна
# разыгрывается заново при каждом вызове `_plan_for_today`, поэтому показывать
# в интерфейсе свежий розыгрыш нельзя — там прыгали бы цифры, не совпадающие
# с тем, что служба реально собирается делать. Она публикует свой план сюда.
CURRENT = {"day": None, "items": [], "done": set()}


def _publish(day, items, done):
    CURRENT["day"], CURRENT["items"], CURRENT["done"] = day, items, done


def _plan_for_today(day=None):
    """Список (время, вид, параметр) на сегодня со случайным разбросом.

    Параметр сессии — пара (приложение, секунд); у публикации его нет.
    """
    today = day or dt.date.today()
    items = []

    for hhmm in config.POST_TIMES:
        h, m = map(int, hhmm.split(":"))
        base = dt.datetime.combine(today, dt.time(h, m))
        shift = random.uniform(-config.JITTER_MIN, config.JITTER_MIN)
        items.append((base + dt.timedelta(minutes=shift), "post", None))

    # Свои правила из plan.json заменяют часы из config: если человек
    # расписал день сам, подмешивать к нему случайные сессии незачем.
    rules = plan.load()
    if rules:
        for when, app, seconds in plan.for_date(today, rules):
            items.append((when, "session", (app, seconds)))
    else:
        # Число сессий в день тоже не должно быть одинаковым.
        times = list(config.SESSION_TIMES)
        random.shuffle(times)
        keep = times[:random.randint(max(1, len(times) - 2), len(times))]
        for hhmm in keep:
            h, m = map(int, hhmm.split(":"))
            base = dt.datetime.combine(today, dt.time(h, m))
            shift = random.uniform(-config.JITTER_MIN, config.JITTER_MIN)
            items.append((base + dt.timedelta(minutes=shift), "session",
                          (random.choice(config.FEED_APPS), None)))

    # Подборка ссылок в Telegram. Без разброса и БЕЗ пропуска задним числом
    # (см. `_already_past`): телефона это не касается, а письмо, отложенное
    # до завтра из-за того, что службу подняли в 21:05, — просто потерянное
    # письмо.
    for hhmm in config.LINKS_DIGEST_TIMES:
        h, m = map(int, hhmm.split(":"))
        items.append((dt.datetime.combine(today, dt.time(h, m)), "links", None))

    items.sort(key=lambda x: x[0])
    return items


def _already_past(items, now):
    """Пункты, время которых прошло ДО запуска службы, — считаем пропущенными.

    Иначе старт в середине дня выстреливал бы пачкой: цикл выполняет всё,
    у чего время наступило, и служба, поднятая в три часа ночи, тут же
    уходила в получасовую сессию, назначенную на полночь. Слот, который
    служба проспала, надо пропустить, а не догонять.
    """
    # Подборка ссылок — исключение: она ничего не делает с телефоном, и
    # повторить её не страшно (уже отправленное второй раз не приходит,
    # отметка `sent` в базе). Пропускать её задним числом значило бы молча
    # съедать письмо у того, кто поднял службу вечером.
    return {i for i, (when, kind, _) in enumerate(items)
            if when < now and kind != "links"}


def _plan_text(items):
    """План дня в человеческом виде — в уведомление и в команду serve."""
    lines = []
    for when, kind, arg in items:
        if kind == "links":
            lines.append(f"{when:%H:%M} — подборка ссылок в Telegram")
            continue
        if kind != "session":
            lines.append(f"{when:%H:%M} — публикация")
            continue
        app, seconds = arg
        length = f", {seconds / 60:.0f} мин" if seconds else ""
        lines.append(f"{when:%H:%M} — сессия {app}{length}")
    return "\n".join(lines) or "на сегодня ничего не запланировано"


VIDEO_EXT = (".mp4", ".mov", ".mkv", ".webm")

# Меньше этого видео не бывает даже у самого короткого ролика: секунда 720p
# весит сотни килобайт. Порог стоит затем, что в очередь попадало ЧТО УГОДНО —
# оборванная закачка, пустышка, случайный файл, — и агент честно нёс это на
# телефон и открывал редактор публикации. Ловим на входе, а не на экране.
MIN_VIDEO_BYTES = 64 * 1024

# Начало файла у mkv/webm. Записано числами, чтобы не зависеть от того,
# как редактор обойдётся с непечатаемыми байтами в литерале.
EBML = bytes((0x1A, 0x45, 0xDF, 0xA3))


def looks_like_video(path):
    """Похоже ли на видео. Возвращает причину отказа или None, если похоже."""
    if not path.lower().endswith(VIDEO_EXT):
        return "нужен видеофайл: mp4, mov, mkv или webm"
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return f"файл не читается: {e}"
    if size < MIN_VIDEO_BYTES:
        return f"файл слишком мал для видео ({size} байт) — похоже, битый"
    # Контейнер узнаём по началу файла: у mp4/mov это «ftyp» с четвёртого
    # байта, у mkv/webm — сигнатура EBML. Дешевле и надёжнее, чем верить
    # расширению: переименовать картинку в .mp4 может кто угодно.
    try:
        with open(path, "rb") as f:
            head = f.read(12)
    except OSError as e:
        return f"файл не читается: {e}"
    if head[4:8] == b"ftyp" or head[:4] == EBML:
        return None
    return "это не видео: файл не похож ни на mp4, ни на mkv"


def scan_watch_dir():
    """Видео, брошенные в watch/, попадают в очередь.

    Описание берётся из одноимённого .txt рядом с файлом.
    """
    added = []
    for name in sorted(os.listdir(config.WATCH_DIR)):
        src = os.path.join(config.WATCH_DIR, name)
        if not os.path.isfile(src) or not name.lower().endswith(VIDEO_EXT):
            continue

        why = looks_like_video(src)
        if why:
            # Не удаляем и не публикуем: перекладываем в сторону и говорим.
            # Тихо проглотить чужой файл хуже, чем оставить его на виду.
            bad = os.path.join(config.WATCH_DIR, "не-видео")
            os.makedirs(bad, exist_ok=True)
            try:
                os.replace(src, os.path.join(bad, name))
            except OSError:
                pass
            jobs.log_event("skip", f"{name}: {why}")
            runlog.Log(f"не беру «{name}»: {why}")
            continue

        caption = ""
        txt = os.path.splitext(src)[0] + ".txt"
        if os.path.exists(txt):
            with open(txt, encoding="utf-8") as f:
                caption = f.read().strip()

        dst = os.path.join(config.QUEUE_DIR, name)
        os.replace(src, dst)
        if os.path.exists(txt):
            os.remove(txt)

        job_id = jobs.add(dst, caption, targets="tiktok")
        added.append((job_id, name))
    return added


def run_post_job(notify=None, job_id=None):
    """Опубликовать задачу: указанную или первую подошедшую по времени.

    `job_id` нужен боту: он присылает конкретное видео и ждёт отчёт именно по
    нему, а не по тому, что случайно оказалось первым в очереди.
    """
    if jobs.count_today("done") >= config.MAX_POSTS_PER_DAY:
        return "дневной лимит постов исчерпан"

    job = jobs.by_id(job_id) if job_id is not None else jobs.due()
    if job is None:
        return f"задачи #{job_id} нет" if job_id is not None else "очередь пуста"

    results, ok_all = [], True
    for target in job["targets"].split(","):
        target = target.strip()
        if not target:
            continue
        ok, report = poster.post(job["video_path"], job["caption"], target)
        ok_all &= ok
        results.append(report)
        if notify:
            notify(report)

    jobs.mark(job["id"], "done" if ok_all else "failed", "\n\n".join(results))
    return "\n\n".join(results)


def run_session(app, seconds=None, notify=None, manual=False):
    """Одна сессия. `seconds` задан — значит её поставил человек в plan.json.

    Дневной лимит на такие не распространяется: он стоит против разгона
    случайного расписания, а расписанное руками — это уже решение. По той же
    причине его не считает `manual` — нажатие кнопки в окне. Раньше кнопка на
    исчерпанном лимите молча ничего не делала.
    """
    if not manual and seconds is None \
            and jobs.sessions_today() >= config.MAX_SESSIONS_PER_DAY:
        return "дневной лимит сессий исчерпан"
    report = session.browse(app, seconds)
    jobs.log_event("session", report)
    if notify:
        notify(report)
    return report


def _maybe_watch(watch, record, tag):
    """Окно трансляции на время одного действия, если попросили."""
    import contextlib

    if not watch:
        return contextlib.nullcontext()

    import mirror
    path = None
    if record:
        path = os.path.join(config.LOG_DIR,
                            f"{dt.datetime.now():%Y-%m-%d_%H-%M-%S}_{tag}.mp4")
    return mirror.watch(title=f"PhoneAgent — {tag}", record=path)


def serve(notify=None, watch=False, record=False, should_stop=None):
    """Главный цикл службы. Спит, просыпается по плану, работает.

    `should_stop` — необязательная проверка «пора выходить». Нужна там, где
    службу нельзя прибить Ctrl+C: в веб-интерфейсе она живёт фоновым потоком
    и выключается кнопкой. Проверяется часто, а не раз в полминуты, иначе
    выключатель отзывался бы с задержкой.
    """
    # Не `plan`: так называется модуль правил, и затенять его тут нельзя.
    schedule = _plan_for_today()
    schedule_day = dt.date.today()
    done = _already_past(schedule, dt.datetime.now())
    _publish(schedule_day, schedule, done)

    if notify:
        missed = f"\n(пропущено, время уже прошло: {len(done)})" if done else ""
        notify("План на сегодня:\n" + _plan_text(schedule) + missed)

    while True:
        if should_stop is not None and should_stop():
            return "служба остановлена"

        now = dt.datetime.now()

        if now.date() != schedule_day:
            schedule = _plan_for_today()
            schedule_day = now.date()
            done = _already_past(schedule, now)
            _publish(schedule_day, schedule, done)
            if notify:
                notify("План на сегодня:\n" + _plan_text(schedule))

        if config.stop_requested():
            # Спим кусками, а не одним махом: под стоп-краном служба проводит
            # почти всё время, и просьбу выйти надо замечать здесь так же
            # быстро, как в рабочем цикле ниже. Иначе остановка телефона
            # занимала полминуты вместо секунды.
            for _ in range(30):
                if should_stop is not None and should_stop():
                    return "служба остановлена"
                time.sleep(1)
            continue

        for i, (when, kind, arg) in enumerate(schedule):
            if i in done or now < when:
                continue
            done.add(i)

            # Подборка идёт мимо телефона: ни окна с экраном, ни записи,
            # ни ожидания, пока освободится устройство.
            if kind == "links":
                try:
                    said = telegram_bot.digest()
                    if said and notify:
                        notify(said)
                except Exception as e:
                    jobs.log_event("error", f"подборка ссылок: {e}")
                continue

            app, seconds = arg if kind == "session" else (None, None)
            try:
                with _maybe_watch(watch, record,
                                  "post" if kind == "post" else f"сессия {app}"):
                    if kind == "post":
                        scan_watch_dir()
                        run_post_job(notify)
                    else:
                        run_session(app, seconds, notify)
            except Exception as e:                     # служба не должна падать
                msg = f"сбой {kind}: {type(e).__name__}: {e}"
                jobs.log_event("error", msg)
                if notify:
                    notify(msg)

        # Очередь смотрим саму по себе, не только в часы POST_TIMES.
        # Иначе задача с назначенным временем («в 18:30» в подписи боту) не
        # публиковалась бы никогда: часов публикации может не быть вовсе,
        # и по умолчанию их нет.
        try:
            if jobs.due() is not None:
                run_post_job(notify)
        except Exception as e:
            msg = f"сбой публикации: {type(e).__name__}: {e}"
            jobs.log_event("error", msg)
            if notify:
                notify(msg)

        # Новые видео в watch/ подхватываем и вне расписания.
        try:
            for job_id, name in scan_watch_dir():
                if notify:
                    notify(f"принято в очередь #{job_id}: {name}")
        except OSError:
            pass

        # Спим кусками: выключатель в интерфейсе должен срабатывать сразу,
        # а не досиживать полминуты до конца паузы.
        for _ in range(30):
            if should_stop is not None and should_stop():
                return "служба остановлена"
            time.sleep(1)
