"""Окно приложения вместо меню в консоли.

Почему браузер, а не tkinter: страницу можно сделать симпатичной, не заводя
первую зависимость из PyPI — сервер собран на стандартном `http.server`,
внешний вид описан обычным CSS. Чтобы это не выглядело сайтом, страница
открывается в оконном режиме браузера (`--app=...`): ни вкладок, ни адресной
строки — обычное окно, которое живёт в панели задач само по себе.

Что важно знать про устройство:

* Длинные действия (сессия, публикация) идут в фоновом потоке, по одному
  за раз — телефон один, и двум хозяевам его не поделить. За этим следит
  `Runner`; расписание и очередь только ставят в эту очередь работу, сами
  телефон не занимая (`Keeper`, `Dispatcher`).
* Строки прогона попадают на страницу через `runlog.set_sink`: тот же самый
  журнал, что печатается в консоли, дублируется в окно.
* Состояние телефона опрашивается по расписанию и кэшируется. Страница
  спрашивает раз в секунду, а дёргать adb с такой частотой нельзя — во время
  сессии каждый лишний вызов отнимает время у самой сессии.
"""
import abort
import datetime as dt
import http.server
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser

import adb
import config
import device
import devices
import fleet
import gate
import interests
import jobs
import plan
import prefs
import runlog
import scheduler
import stats
import vision

HOST = "127.0.0.1"
PORT = 8765

# Токен от чужих вкладок. Сервер слушает только localhost, но любая открытая
# в браузере страница тоже может послать запрос на 127.0.0.1 — а тут кнопки,
# которые двигают живой телефон. Токен выдаётся самой странице при загрузке,
# и без него изменяющие запросы не принимаются.
TOKEN = secrets.token_urlsafe(24)


def _base_dir():
    """Каталог с ресурсами: рядом с кодом, а в сборке — во временной распаковке."""
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------- действия

class Runner:
    """Одно длинное действие за раз плюс его журнал для страницы."""

    LIMIT = 4000        # строк в памяти; сессия бывает часовой

    def __init__(self):
        self._lock = threading.Lock()
        self._thread = None
        self.title = ""
        self.lines = []
        self.started_at = 0.0
        self.on_done = []      # кого разбудить, когда телефон освободился

    def busy(self):
        thread = self._thread
        return thread is not None and thread.is_alive()

    def log(self, line):
        with self._lock:
            # Отчёты приходят и целыми кусками с переводами строк: на странице
            # это должны быть отдельные строки, иначе разъедется вёрстка.
            self.lines.extend(str(line).split("\n"))
            if len(self.lines) > self.LIMIT:
                del self.lines[:len(self.lines) - self.LIMIT]

    def tail(self, since):
        """Строки, которых страница ещё не видела, и сколько их всего."""
        with self._lock:
            total = len(self.lines)
            since = max(0, min(int(since), total))
            return total, self.lines[since:]

    def start(self, title, fn):
        # Проверку и захват держим под одним замком. Претендентов на телефон
        # четверо — кнопки, расписание, сторож очереди и бот, — и раздельные
        # `busy()` и `start()` позволяли двоим проскочить одновременно: оба
        # видели «свободно» до того, как первый успел занять место.
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError(f"сейчас идёт другое действие: {self.title}")
            self.lines = []
            self.title = title
            self.started_at = time.time()
            self._thread = threading.Thread(target=self._wrap(fn), daemon=True)
            self._thread.start()
        abort.clear()          # прошлая остановка не должна гасить новое

    def _wrap(self, fn):
        """Обёртка вокруг работы: журнал, перехват падений, побудка ждущих."""

        def wrapper():
            previous = runlog.set_sink(self.log)
            try:
                fn()
            except Exception as e:                  # поток не должен падать молча
                self.log(f"ОШИБКА: {type(e).__name__}: {e}")
            finally:
                runlog.set_sink(previous)
                # Кто-то мог ждать освободившийся телефон — например,
                # отложенная публикация из очереди. Будим сразу, а не через
                # десять секунд опроса: «сразу после сессии» должно значить
                # именно сразу.
                for hook in list(self.on_done):
                    try:
                        hook()
                    except Exception:
                        pass

        return wrapper

    def cancel(self):
        """Оборвать то, что идёт. Поток не убиваем — просим его закончить.

        Паузы агента спят через `abort.sleep`, поэтому просьба замечается
        сразу, а не после «отвлёкся на сорок секунд».
        """
        if not self.busy():
            return False
        abort.request()
        self.log("остановка по кнопке...")
        return True


class Keeper:
    """Расписание, у которого нет выключателя.

    Раньше служба сама была длинным действием и занимала единственный слот
    Runner целиком: включил — и кнопки «смотреть ленту» и «опубликовать»
    заблокированы до вечера. Поэтому её и приходилось выключать руками.

    Теперь она не работает, а **сторожит**: раз в полминуты смотрит, не
    подошло ли время очередного пункта плана, и если телефон свободен —
    ставит это действие в ту же общую очередь, что и кнопки. Пока правила в
    расписании есть, выключать её незачем: сама она телефон не занимает.

    Пункт помечается сделанным ДО запуска — иначе прерванная кнопкой
    «Остановить» сессия начиналась бы заново через полминуты.
    """

    EVERY = 30.0

    def __init__(self, runner):
        self.runner = runner
        self._wake = threading.Event()
        self.day = None
        self.schedule = []
        self.done = set()

    def rules(self):
        """Сколько включённых правил в расписании. Ноль — сторожить нечего."""
        return sum(1 for r in plan.load() if r.get("вкл", True))

    def live(self):
        return bool(self.rules())

    def reload(self):
        """Расписание поменяли — разыграть день заново."""
        self.day = None
        self._wake.set()

    def start(self):
        self.runner.on_done.append(self._wake.set)
        self._wake.set()        # первый круг сразу, а не через полминуты
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            self._wake.wait(self.EVERY)
            self._wake.clear()
            try:
                self._tick()
            except Exception as e:
                # Сторож не имеет права падать: без него расписание мертво.
                self.runner.log(f"расписание: {type(e).__name__}: {e}")

    def _tick(self):
        now = dt.datetime.now()
        if now.date() != self.day:
            self.day = now.date()
            self.schedule = scheduler._plan_for_today(self.day)
            # Пункты, время которых прошло до запуска окна, догонять не надо:
            # открыв программу вечером, не хочется получить пачку сессий
            # за весь день сразу.
            self.done = scheduler._already_past(self.schedule, now)
            scheduler._publish(self.day, self.schedule, self.done)

        if config.stop_requested() or self.runner.busy():
            return

        for i, (when, kind, arg) in enumerate(self.schedule):
            if i in self.done or now < when:
                continue
            self.done.add(i)
            scheduler._publish(self.day, self.schedule, self.done)
            if kind == "post":
                self.runner.start("публикация по расписанию", lambda: (
                    scheduler.scan_watch_dir(), scheduler.run_post_job(_tell)))
            else:
                app, seconds = arg
                self.runner.start(
                    f"сессия {app}",
                    lambda a=app, sec=seconds: scheduler.run_session(
                        a, sec, notify=_tell))
            return                      # одно действие за раз, остальное подождёт


# ---------------------------------------------------------- состояние

class Dispatcher:
    """Очередь публикуется сама — без кнопки и без включённой службы.

    Раньше видео, присланное боту, могло осесть в очереди навсегда: бот
    публикует сразу, но если в этот момент шла сессия, задача оставалась
    ждать, а ждать её было некому. Теперь ждёт этот сторож: телефон
    освободился, время задачи пришло — публикуем.
    """

    EVERY = 10.0

    def __init__(self, runner):
        self.runner = runner
        self._wake = threading.Event()

    def start(self):
        # Сессия кончилась — проверяем очередь тут же, не досыпая свои
        # десять секунд. Видео, присланное во время сессии, публикуется
        # сразу по её окончании.
        self.runner.on_done.append(self._wake.set)
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            self._wake.wait(self.EVERY)
            self._wake.clear()
            if self.runner.busy():
                continue
            try:
                scheduler.scan_watch_dir()          # видео, брошенные в watch/
                if jobs.due() is None:
                    continue
                self.runner.start("публикация из очереди",
                                  lambda: scheduler.run_post_job(notify=_tell))
            except Exception:
                # Сторож не имеет права падать: он единственный, кто
                # разгребает очередь в простое.
                pass


class Probe:
    """Состояние телефона, зрения и клавиатуры — с кэшем.

    Опрос стоит доли секунды, но страница обновляется раз в секунду, а во
    время сессии любой лишний вызов adb крадёт время у неё же. Поэтому
    опрашиваем раз в REFRESH секунд и никогда — пока идёт действие.
    """

    REFRESH = 12.0

    def __init__(self, runner):
        self.runner = runner
        self.data = {
            "phone": {"ok": False, "text": "проверяю..."},
            "vision": {"ok": False, "text": "проверяю..."},
            "keyboard": {"ok": False, "text": "проверяю..."},
            "devices": [],
            # Список моделей меняется редко, а `lms ls` стоит секунду —
            # обновляем его вместе с остальным опросом, а не на каждый запрос.
            "models": [],
        }
        self.checked_at = 0.0

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def refresh_soon(self):
        """Сбросить кэш: следующий круг опросит заново."""
        self.checked_at = 0.0

    def _loop(self):
        while True:
            if not self.runner.busy() and time.time() - self.checked_at >= self.REFRESH:
                try:
                    self._refresh()
                except Exception as e:
                    self.data["phone"] = {"ok": False,
                                          "text": f"сбой опроса: {type(e).__name__}"}
                self.checked_at = time.time()
            time.sleep(1.0)

    def _refresh(self):
        # Список телефонов для выпадающего списка. Имя модели спрашиваем у
        # каждого: серийник «74eed241» человеку ничего не говорит.
        found = []
        for serial, state in adb.devices():
            name = ""
            if state == "device":
                try:
                    name = adb.raw("-s", serial, "shell", "getprop",
                                   "ro.product.model", check=False,
                                   timeout=10).strip()
                except adb.AdbError:
                    name = ""
            found.append({"serial": serial, "state": state,
                          "name": name or serial,
                          "link": "по сети" if ":" in serial else "кабель"})
            if name:
                devices.remember(serial, name)
        self.data["devices"] = found

        live = [d["serial"] for d in found if d["state"] == "device"]
        self.data["live"] = live
        if not live:
            self.data["phone"] = {"ok": False, "text": "не подключён"}
            self.data["keyboard"] = {"ok": False, "text": "телефона нет"}
        else:
            # Выбранный телефон не трогаем: пространство открыто именно его,
            # и опрос не имеет права увести работу на соседний.
            if not config.SERIAL:
                adb.prefer(live[0] if len(live) > 1 else None)
            model = adb.shell("getprop ro.product.model", check=False).strip()
            release = adb.shell("getprop ro.build.version.release", check=False).strip()
            screen = device.screen_state()
            by_cable = any(":" not in s for s in live)
            self.data["phone"] = {
                "ok": True,
                "text": f"{model or 'телефон'} · Android {release or '?'}",
                "screen": screen,
                "link": "кабель" if by_cable else "по сети",
            }
            keyboard = device.has_adb_keyboard()
            if keyboard:
                self.data["keyboard"] = {"ok": True, "text": "включена"}
            elif device.adb_keyboard_installed():
                self.data["keyboard"] = {"ok": False,
                                         "text": "установлена, но выключена"}
            else:
                self.data["keyboard"] = {"ok": False, "text": "нет — русский текст не введётся"}

        vis_ok, vis_info = vision.available()
        self.data["vision"] = {
            "ok": bool(vis_ok),
            "text": f"{vis_info} · {vision.where()}" if vis_ok else vis_info,
            "where": vision.where(),
        }
        try:
            self.data["models"] = vision.installed_models()
        except Exception:
            self.data["models"] = []


RUNNER = Runner()
KEEPER = Keeper(RUNNER)
PROBE = Probe(RUNNER)
DISPATCHER = Dispatcher(RUNNER)


def _start_bot():
    """Поднять Telegram-приёмник и отдать ему общую очередь действий.

    Через `set_submit` публикация из бота встаёт в тот же `Runner`, что и
    кнопки: телефон один, и пост не должен начаться посреди сессии.

    Читатель у Telegram ровно один: `getUpdates` отдаёт второму 409, и тогда
    ломаются оба — сообщения начинают доставаться случайному. Поэтому бота
    берём под тот же замок, что и службы телефонов. Окно в этой очереди
    первое по праву: оно и есть место, куда человек смотрит.
    """
    import telegram_bot

    try:
        fleet.telegram_gate().acquire(timeout=0.5)
    except gate.Timeout:
        print("Telegram-бота уже ведёт служба телефона — окно его не поднимает")
        return

    telegram_bot.set_submit(RUNNER.start)
    telegram_bot.start_background()


def _bot_state():
    import telegram_bot

    cfg = telegram_bot.load_settings()
    return {"token": bool(cfg["token"]), "owner": cfg["owner"],
            "running": telegram_bot.running()}


def _tell(text):
    """Сказать в Telegram, если бот настроен. Молча промолчать, если нет.

    Нужно отложенной публикации: видео прислали во время сессии, опубликовал
    его сторож спустя полчаса — и без этой строки человек не узнал бы, что
    оно вообще вышло.
    """
    try:
        import telegram_bot

        if telegram_bot.load_settings()["token"]:
            telegram_bot.send(str(text))
    except Exception:
        pass


def _chosen():
    """Выбранное мышкой — для страницы. Ключ наружу не уходит.

    Страница сидит на localhost и под токеном, но показывать ключ ей всё
    равно незачем: он нужен только тому коду, который делает запрос.
    """
    data = dict(prefs.load())
    api = vision.provider() == vision.API
    data["has_key"] = bool(data["api_key"] or config.API_KEY.strip())
    data["api_key"] = ""
    data["vision_provider"] = vision.provider()
    # Списков на странице по одному — того, кто сейчас отвечает. Показываем
    # то, что реально в силе: не выбрали своё — значит работает то, что
    # прописано в config.py, и выделенным должно быть именно оно.
    data["model"] = (config.API_MODEL if api else data["vision_model"])
    data["url"] = config.API_URL if api else config.VISION_URL
    data["presets"] = [{"id": k, "title": v[0], "url": v[1], "model": v[2]}
                       for k, v in config.API_PRESETS.items()]
    return data


def _queue():
    rows = []
    for r in jobs.pending():
        rows.append({
            "id": r["id"],
            "name": os.path.basename(r["video_path"]),
            "caption": r["caption"] or "",
            "targets": r["targets"],
            "when": time.strftime("%d.%m %H:%M", time.localtime(r["run_at"])),
        })
    return rows


_DRAW = {"day": None, "items": []}


def _today_plan():
    """Расписание на сегодня в человеческом виде.

    Служба работает по своему розыгрышу времени — если она поднята, берём
    план прямо у неё. Иначе разыгрываем сами, но **один раз в сутки**:
    `plan.for_date` каждый вызов выдаёт новое время внутри окна, а страница
    спрашивает состояние ежесекундно — цифры прыгали бы на глазах.
    """
    today = dt.date.today()

    if scheduler.CURRENT["day"] == today and scheduler.CURRENT["items"]:
        items = [(when, arg[0] if kind == "session" else None,
                  arg[1] if kind == "session" else None, kind)
                 for when, kind, arg in scheduler.CURRENT["items"]]
    else:
        if _DRAW["day"] != today:
            try:
                _DRAW["items"] = [(when, app, seconds, "session")
                                  for when, app, seconds in plan.for_date(today)]
            except Exception:
                _DRAW["items"] = []
            _DRAW["day"] = today
        items = _DRAW["items"]

    now = dt.datetime.now()
    lines, upcoming = [], ""
    for when, app, seconds, kind in items:
        if kind != "session":
            text = f"{when:%H:%M} — публикация"
        else:
            length = f", {seconds / 60:.0f} мин" if seconds else ""
            text = f"{when:%H:%M} — {app}{length}"
        past = when < now
        lines.append(text + (" · прошло" if past else ""))
        if not past and not upcoming:
            upcoming = f"{when:%H:%M}"
    return lines, upcoming


def _apps():
    """Ленты из recipes.json: что вообще можно смотреть.

    Список берётся из файла, а не из кода: добавил туда маршрут — приложение
    само появилось в окне и в расписании.
    """
    try:
        with open(config.RECIPES, encoding="utf-8") as f:
            feeds = json.load(f).get("_feed_apps", {})
    except (OSError, json.JSONDecodeError):
        feeds = {}

    out = []
    for name in config.FEED_APPS:
        cfg = feeds.get(name)
        if isinstance(cfg, dict) and cfg.get("package"):
            out.append({"id": name, "title": cfg.get("title") or name})
    return out or [{"id": "tiktok", "title": "TikTok"}]


def _minutes_text(value):
    """«30» или «20-40» — в файле это может быть число, а может быть пара."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return f"{float(value[0]):g}-{float(value[1]):g}"
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return str(value)


def _plan_state():
    rules = []
    for i, rule in enumerate(plan.load(), start=1):
        rules.append({
            "n": i,
            "window": rule.get("окно", ""),
            "minutes": _minutes_text(rule.get("минут", "")),
            "days": rule.get("дни", ""),
            "app": rule.get("что") or config.FEED_APPS[0],
            "on": bool(rule.get("вкл", True)),
        })
    lines, upcoming = _today_plan()
    return {"rules": rules, "today": lines, "next": upcoming,
            "live": KEEPER.live()}


def _fleet_state():
    """Кто из телефонов сейчас работает своей службой.

    Список строится по ВСЕМ известным телефонам, а не по тем, кого запустило
    это окно: службы переживают закрытие окна (у них своё расписание), и
    новое окно должно их видеть, а не заводить вторую поверх.
    """
    rows = devices.known(PROBE.data.get("live", []))
    seen = {d["serial"] for d in rows}

    # Телефоны, чьи службы мы ведём, но которых нет в списке известных.
    # Так бывает буднично: кабель выдернули посреди работы — телефон исчез
    # из `devices.known`, а служба осталась. Пропади он из списка, его нельзя
    # было бы даже остановить из окна.
    for serial, svc in list(fleet.FLEET.services.items()):
        if serial and serial not in seen and svc.running():
            rows.append({"serial": serial, "name": devices.name_of(serial),
                         "live": False, "link": "нет связи"})
            seen.add(serial)

    out = []
    for d in rows:
        st = fleet.FLEET.service(d["serial"]).state()
        st["live"] = d["live"]
        st["link"] = d["link"]
        st["here"] = (d["serial"] == (config.SERIAL or ""))
        out.append(st)
    return out


def state(since=0):
    # Журнал отдаётся и здесь — на случай, если страница спросит состояние
    # раньше первого запроса за строками. Основной путь всё равно /api/log.
    total, lines = RUNNER.tail(since)
    taste = interests.load()
    running = RUNNER.busy()

    return {
        "phone": PROBE.data["phone"],
        "vision": PROBE.data["vision"],
        "keyboard": PROBE.data["keyboard"],
        "serving": KEEPER.live(),
        "spaces": devices.known(PROBE.data.get("live", [])),
        "device": config.SERIAL or "",
        "busy": running,
        "action": RUNNER.title if running else "",
        "elapsed": int(time.time() - RUNNER.started_at) if running else 0,
        "log": {"total": total, "lines": lines},
        "today": {
            "sessions": jobs.sessions_today(),
            "max_sessions": config.MAX_SESSIONS_PER_DAY,
            "posts": jobs.count_today("done"),
            "max_posts": config.MAX_POSTS_PER_DAY,
        },
        "queue": _queue(),
        "plan": _plan_state(),
        "fleet": _fleet_state(),
        "taste": {"topic": taste.get("тема", ""), "lang": taste.get("язык", "")},
        "apps": _apps(),
        "bot": _bot_state(),
        "models": PROBE.data.get("models", []),
        "devices": PROBE.data.get("devices", []),
        "chosen": _chosen(),
        "post_times": list(config.POST_TIMES),
        "targets": [a["id"] for a in _apps()],
    }


# ------------------------------------------------------------- команды

def _session(app, minutes):
    seconds = float(minutes) * 60 if minutes else None
    RUNNER.start(f"сессия {app}",
                 lambda: scheduler.run_session(app, seconds, notify=None,
                                               manual=True))


def _post_queue():
    RUNNER.start("публикация из очереди", lambda: scheduler.run_post_job())


def _save_upload(name, data):
    """Положить присланный файл в watch/ и сразу забрать в очередь."""
    name = os.path.basename(name or "").strip() or "video.mp4"
    # Имя приходит из браузера: чистим всё, что может увести запись из папки.
    name = re.sub(r"[^\w\s.()\[\]-]", "_", name, flags=re.UNICODE)
    # Длинное имя роняло запись с невнятной ошибкой файловой системы, да ещё
    # и с полным путём в тексте. Режем сами, оставляя расширение.
    stem, ext = os.path.splitext(name)
    name = stem[:80] + ext[:8]
    if not name.lower().endswith(scheduler.VIDEO_EXT):
        raise ValueError("нужен видеофайл: mp4, mov, mkv или webm")

    path = os.path.join(config.WATCH_DIR, name)
    stem, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(path):
        path, n = f"{stem} ({n}){ext}", n + 1

    with open(path, "wb") as f:
        f.write(data)

    # Проверяем то, что реально легло на диск: браузер отдаёт байты как есть,
    # и оборванная передача выглядит нормальным файлом с правильным именем.
    why = scheduler.looks_like_video(path)
    if why:
        os.remove(path)
        raise ValueError(why)
    return os.path.basename(path)


COMMANDS = {}


def command(name):
    def wrap(fn):
        COMMANDS[name] = fn
        return fn
    return wrap


def session_blocked(app):
    """Почему сессия не начнётся. None — можно запускать.

    Заведено после того, как кнопка «Смотреть ленту» перестала работать
    после переноса и НИЧЕГО об этом не сказала: команда отвечала «ок»,
    сессия умирала через секунду, а причина оставалась в живом журнале,
    куда никто не смотрит. Теперь каждый отказ называется вслух.
    """
    if RUNNER.busy():
        return f"сейчас идёт другое: {RUNNER.title}"
    # Этот телефон может вести отдельная служба (см. fleet). Тогда кнопка в
    # окне — второй ведущий у одного аппарата: два маршрута перемешаются на
    # одном экране, и разобрать потом, кто куда нажал, будет нечем.
    if fleet.busy_elsewhere(config.SERIAL or ""):
        return ("этот телефон уже ведёт своя служба — останови её "
                "во «Флоте» или открой другой телефон")
    if config.stop_requested():
        return "взведён стоп-кран — сними его, тогда пойдёт"
    if not adb.connected():
        return ("телефона не видно: кабель, «Отладка по USB» и подтверждение "
                "отпечатка на экране телефона")
    # Закрепить телефон ДО начала работы. Окно живёт часами, и телефон в нём
    # часто появляется уже после запуска: при старте его не было, закрепления
    # не случилось, а подключённый позже второй аппарат ломал каждую команду
    # («more than one device»). Стоит один вызов `adb devices`.
    adb.pin()
    try:
        import session as session_mod

        session_mod._feed_config(app)
    except Exception as e:
        return f"лента «{app}» не описана в recipes.json ({str(e)[:60]})"
    return None


@command("session")
def _cmd_session(body):
    app = body.get("app") or config.FEED_APPS[0]
    why = session_blocked(app)
    if why:
        return {"ok": False, "error": why}

    # Дневной лимит кнопку не держит: он стоит против разгона случайного
    # расписания, а нажатие руками — это уже решение (то же правило, что и
    # для правил, расставленных вручную). Но сказать об этом надо.
    over = jobs.sessions_today() >= config.MAX_SESSIONS_PER_DAY
    _session(app, body.get("minutes"))
    name = next((a["title"] for a in _apps() if a["id"] == app), app)
    return {"ok": True,
            "message": (f"дневной лимит уже выбран — иду по нажатию: {name}"
                        if over else f"смотрю ленту: {name}")}


@command("post-queue")
def _cmd_post_queue(body):
    _post_queue()
    return {"ok": True}


@command("device")
def _cmd_device(body):
    """Открыть пространство другого телефона.

    Переключать посреди работы нельзя: действие уже водит тот телефон, и
    смена путей на ходу увела бы очередь и кадры в чужую папку.
    """
    serial = str(body.get("serial", "")).strip()
    if RUNNER.busy():
        return {"ok": False,
                "error": f"сейчас идёт «{RUNNER.title}» — сначала дождись или останови"}

    devices.use(serial)
    prefs.save(serial=serial)
    _plan_changed()
    PROBE.refresh_soon()
    return {"ok": True, "message": f"открыл телефон: {devices.name_of(serial)}"}


@command("fleet-start")
def _cmd_fleet_start(body):
    """Поднять службу телефона: свой процесс, своё расписание.

    Телефон, открытый в этом окне, отдавать службе нельзя: окно водит его
    само (кнопки, сторож очереди, расписание внутри окна), и вдвоём они
    подерутся за экран.
    """
    serial = str(body.get("serial", "")).strip()
    if not serial:
        return {"ok": False, "error": "не сказано, какой телефон"}
    if serial == (config.SERIAL or ""):
        if RUNNER.busy():
            return {"ok": False, "error": f"здесь идёт «{RUNNER.title}»"}
        return {"ok": False,
                "error": "этот телефон открыт в окне и работает отсюда. "
                         "Служба нужна для ОСТАЛЬНЫХ телефонов — открой "
                         "другой телефон, а этому нажми запуск"}
    # Две проверки, а не одна: замок ловит службу, поднятую откуда угодно,
    # а `alive` — нашу собственную. Вторую замок не видит, пока служба не
    # дошла до захвата телефона, и без неё окно молча заводило бы дубль.
    if fleet.busy_elsewhere(serial) or fleet.FLEET.service(serial).alive():
        return {"ok": False, "error": "служба этого телефона уже работает"}

    if not fleet.FLEET.start(serial):
        svc = fleet.FLEET.service(serial)
        return {"ok": False, "error": svc.stopped_reason or "не запустилась"}
    return {"ok": True, "message": f"служба запущена: {devices.name_of(serial)}"}


@command("fleet-stop")
def _cmd_fleet_stop(body):
    serial = str(body.get("serial", "")).strip()
    if not serial:
        return {"ok": False, "error": "не сказано, какой телефон"}
    name = devices.name_of(serial)
    if fleet.FLEET.stop(serial):
        return {"ok": True, "message": f"служба остановлена: {name}"}
    return {"ok": False,
            "error": f"{name}: за {fleet.STOP_GRACE:.0f} с не вышла — "
                     "смотри её журнал"}


@command("fleet-stop-all")
def _cmd_fleet_stop_all(body):
    """Останавливаем параллельно: по очереди вышло бы N × 45 секунд."""
    busy = [d["serial"] for d in _fleet_state() if d["running"]]
    if not busy:
        return {"ok": True, "message": "работающих служб нет"}
    fleet.FLEET.stop_all()
    left = [devices.name_of(d["serial"]) for d in _fleet_state() if d["running"]]
    if left:
        return {"ok": False, "error": "не остановились: " + ", ".join(left)}
    return {"ok": True, "message": f"остановлено служб: {len(busy)}"}


@command("cancel")
def _cmd_cancel(body):
    """Остановить текущее действие.

    Расписание при этом не трогаем: пункт уже помечен сделанным, второй раз
    он не выстрелит, а следующий по плану — это уже другая сессия.
    """
    if not RUNNER.cancel():
        return {"ok": False, "error": "сейчас ничего не идёт"}
    return {"ok": True, "message": "останавливаю"}


@command("job-add")
def _cmd_job_add(body):
    path = (body.get("path") or "").strip('" ')
    if not os.path.exists(path):
        raise ValueError(f"файла нет: {path}")
    job_id = jobs.add(os.path.abspath(path), body.get("caption", ""),
                      body.get("target") or "tiktok")
    return {"ok": True, "message": f"задача #{job_id} добавлена"}


@command("caption-last")
def _cmd_caption_last(body):
    """Подписать самую свежую задачу в очереди.

    Описание на странице вводится до перетаскивания файла — иначе пришлось бы
    городить форму поверх зоны загрузки. Файл прилетает отдельным запросом,
    поэтому подпись доклеивается к последней созданной задаче.
    """
    text = (body.get("caption") or "").strip()
    if not text:
        return {"ok": True}
    with jobs.connect() as con:
        row = con.execute(
            "SELECT id FROM jobs WHERE status='pending'" + jobs._mine()[0]
            + " ORDER BY id DESC LIMIT 1", jobs._mine()[1]
        ).fetchone()
        if row is None:
            return {"ok": False, "error": "очередь пуста, подписывать нечего"}
        con.execute("UPDATE jobs SET caption=? WHERE id=?", (text, row["id"]))
    return {"ok": True, "message": "описание сохранено"}


@command("job-remove")
def _cmd_job_remove(body):
    with jobs.connect() as con:
        con.execute("DELETE FROM jobs WHERE id=? AND status='pending'",
                    (int(body["id"]),))
    return {"ok": True}


def _plan_changed():
    """Правило поменялось — и нарисованный план, и сторож устарели."""
    _DRAW["day"] = None
    KEEPER.reload()


@command("plan-add")
def _cmd_plan_add(body):
    plan.add(body.get("window", ""), body.get("minutes", ""),
             body.get("days") or "каждый день",
             app=body.get("app") or None)
    _plan_changed()
    return {"ok": True, "message": "правило добавлено"}


@command("plan-toggle")
def _cmd_plan_toggle(body):
    plan.enable(int(body["n"]), bool(body.get("on")))
    _plan_changed()
    return {"ok": True}


@command("plan-remove")
def _cmd_plan_remove(body):
    plan.remove(int(body["n"]))
    _plan_changed()
    return {"ok": True}


@command("taste")
def _cmd_taste(body):
    interests.set_choice(topic=body.get("topic", ""), lang=body.get("lang", ""))
    return {"ok": True}


@command("settings")
def _cmd_settings(body):
    """Выбор зрения, адреса, модели и телефона. Пусто = как в config.py."""
    changes = {}
    if "serial" in body:
        changes["serial"] = body["serial"]

    # Списки моделей и адреса у своего сервера и у сервиса по API разные,
    # поэтому выбор пишется в своё поле: переключение туда-обратно не должно
    # стирать чужой.
    where = str(body.get("vision_provider", "")).strip().lower()
    if where in (vision.LOCAL, vision.API):
        changes["vision_provider"] = where
    else:
        where = vision.provider()

    # Ключ приходит только когда его правда меняли: страница его не
    # показывает, и пустое поле значит «оставь как было», а не «сотри».
    # Стереть можно — для этого есть отдельный знак.
    key = str(body.get("api_key", "")).strip()
    switched = False
    if key:
        changes["api_key"] = "" if key == "-" else key
        # Вставили ключ, а «где считать» осталось на своём сервере. Раньше
        # ключ просто ложился в файл и не использовался ничем: `_endpoint`
        # подставляет его только на API. Человек видел «сохранено» и ровно
        # никакой разницы. Ключ вписывают затем, чтобы считать по API, —
        # переключаем сами и говорим об этом вслух.
        if key != "-" and where != vision.API:
            changes["vision_provider"] = where = vision.API
            switched = True

        # Ключ от одного сервиса, а адрес стоит от другого — верим ключу:
        # его вставили только что, он и есть свежее намерение. Иначе ключ
        # OpenRouter уходил бы стучаться в DashScope и получал 401, причём
        # с виду «ключ неверный», хотя неверен адрес.
        # Свой адрес, набранный руками, не трогаем: его в списке нет, и
        # подменять его угадкой нельзя.
        if key != "-":
            want = config.preset_for_key(key)
            now = config.preset_for_url(config.API_URL)
            if now and now != want:
                _, url, model = config.API_PRESETS[want]
                changes["api_url"] = url
                changes["api_model"] = model

    # Поля может не быть вовсе — так приходит переключение «где считать».
    # Тогда прежний выбор не трогаем. А при самопереключении по ключу их
    # брать НЕЛЬЗЯ: на странице в этот момент показаны адрес и модель
    # своего сервера, и они уехали бы в поля сервиса — зрение ушло бы
    # стучаться ключом в localhost.
    api = where == vision.API
    if not switched:
        if "vision_model" in body:
            changes["api_model" if api else "vision_model"] = body["vision_model"]
        if "url" in body:
            changes["api_url" if api else "vision_url"] = _clean_url(body["url"])

    data = prefs.save(**changes)
    # `prefs.save` сбросил кэш списка моделей, `refresh_soon` — кэш опроса:
    # ближайший круг спросит сервис заново, и выпадающий список сам
    # наполнится моделями нового ключа.
    #
    # Спрашивать прямо здесь НЕЛЬЗЯ, хотя соблазн был: обработчик запроса
    # ушёл бы в сеть на пятнадцать секунд таймаута, окно на это время
    # замирает, а стенды настроек (`key_only`, `keys_ui`) начинают ломиться
    # к настоящему сервису — они и поймали это первыми.
    PROBE.refresh_soon()

    # Ключ только что вписали — спрашиваем сервис прямо сейчас. Молчание
    # тут стоит дороже полсекунды ожидания: неверный ключ иначе всплывает
    # посреди сессии и выглядит как «зрение само отвалилось».
    if api and key and key != "-":
        good, said = vision.check_and_remember()
        if not good:
            # Сервис не принял ключ — но виноват может быть АДРЕС, а не ключ.
            # Угадка по началу строки врёт: OpenRouter выдаёт и обычные
            # `sk-…`, такой ключ уходил в DashScope, получал 401, и окно
            # объявляло рабочий ключ недействительным (поймано на живом
            # ключе). Поэтому спрашиваем сами сервисы, кто его признаёт.
            pid, url, visual = vision.detect_service(key)
            if pid:
                _, _, default_model = config.API_PRESETS[pid]
                model = vision.pick_visual(visual, default_model)
                data = prefs.save(api_url=url, api_model=model)
                PROBE.refresh_soon()
                good, said = vision.check_and_remember()
                if good:
                    title = config.API_PRESETS[pid][0]
                    return {"ok": True,
                            "message": f"ключ оказался от «{title}» — "
                                       f"переключил адрес, модель {model}, "
                                       f"видят картинки: {len(visual)}"}
            return {"ok": False, "error": "ключ сохранён, но " + said}
        head = "переключил на «по API», " if switched else ""
        return {"ok": True, "message": head + said}

    ok, model = vision.available()
    if not ok:
        model = "выбирается сама"
    phone = data["serial"] or "выбирается сам"
    return {"ok": True,
            "message": f"зрение: {vision.where()}, модель: {model}, "
                       f"телефон: {phone}"}


def _clean_url(value):
    """Адрес сервера в том виде, который нужен коду.

    Люди копируют адрес из документации как придётся: с `/chat/completions`
    на конце, без схемы, с лишней косой чертой. Просить дописать `/v1`
    руками — верный способ получить «сервис не отвечает» на ровном месте.
    """
    url = str(value or "").strip().rstrip("/")
    if not url:
        return ""
    # Пробел внутри — это не адрес, а фраза. Сказать об этом сразу честнее,
    # чем сохранить и получить невнятную сетевую ошибку на первом же кадре.
    if any(ch.isspace() for ch in url):
        raise ValueError(f"адрес не похож на адрес: «{url}»")
    if "://" not in url:
        url = "http://" + url
    for tail in ("/chat/completions", "/completions", "/models"):
        if url.endswith(tail):
            url = url[:-len(tail)]
    return url.rstrip("/")


@command("keyboard")
def _cmd_keyboard(body):
    import setup as setup_mod

    setup_mod.assume_yes()
    ok = setup_mod.step_keyboard(adb.connected())
    PROBE.refresh_soon()
    return {"ok": ok, "message": "клавиатура включена" if ok
            else "не вышло — поставь apk на телефоне, см. README"}


@command("telegram")
def _cmd_telegram(body):
    """Сохранить настройки бота и поднять его."""
    import telegram_bot

    if "token" in body or "owner" in body:
        current = telegram_bot.load_settings()
        telegram_bot.save_settings(body.get("token", current["token"]),
                                   body.get("owner", current["owner"]))
    ok, info = telegram_bot.check()
    if ok or telegram_bot.load_settings()["token"]:
        _start_bot()
    return {"ok": ok, "message" if ok else "error": info}


@command("stats")
def _cmd_stats(body):
    # report() отдаёт СПИСОК строк, а не текст: без склейки страница печатала
    # его через запятую, одной кашей.
    lines = stats.report(int(body.get("days", 7)))
    if not isinstance(lines, str):
        lines = "\n".join(lines)
    return {"ok": True, "text": lines}


# --------------------------------------------------------------- сервер

class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "PhoneAgent"

    def log_message(self, *args):
        pass                     # свой лог не нужен, окно и так перед глазами

    # ----- вспомогательное

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if not isinstance(body, bytes):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass                 # окно закрыли на полуслове — это не ошибка

    def _json(self, code, data):
        self._send(code, json.dumps(data, ensure_ascii=False))

    def _host_ok(self):
        """Защита от подмены имени: пускаем только по адресу localhost.

        Без этой проверки чужой домен, указывающий на 127.0.0.1, смог бы
        обращаться к серверу как к своему.
        """
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    # ----- запросы

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, "нет", "text/plain; charset=utf-8")

        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)

        if parsed.path in ("/", "/index.html"):
            try:
                with open(os.path.join(_base_dir(), "webui.html"),
                          encoding="utf-8") as f:
                    page = f.read()
            except OSError as e:
                return self._send(500, f"нет webui.html: {e}",
                                  "text/plain; charset=utf-8")
            page = page.replace("{{TOKEN}}", TOKEN)
            return self._send(200, page, "text/html; charset=utf-8")

        if parsed.path == "/api/state":
            since = int((query.get("since") or ["0"])[0])
            return self._json(200, state(since))

        if parsed.path == "/api/log":
            # Только строки прогона. Отдельно от состояния, потому что за
            # журналом страница ходит втрое чаще: иначе строка о ролике
            # появлялась уже после того, как агент его пролистнул.
            since = int((query.get("since") or ["0"])[0])
            total, lines = RUNNER.tail(since)
            running = RUNNER.busy()
            return self._json(200, {
                "total": total, "lines": lines, "busy": running,
                "action": RUNNER.title if running else "",
                "elapsed": int(time.time() - RUNNER.started_at) if running else 0,
            })

        if parsed.path == "/api/fleet":
            # Журнал ОДНОЙ службы. Отдельным запросом, а не внутри состояния:
            # строк много, а смотрят их по одному телефону за раз — тому,
            # что раскрыт на странице.
            serial = (query.get("serial") or [""])[0]
            since = int((query.get("since") or ["0"])[0])
            svc = fleet.FLEET.service(serial)
            total, lines = svc.tail(since)
            return self._json(200, dict(svc.state(), total=total, lines=lines))

        return self._send(404, "нет такой страницы", "text/plain; charset=utf-8")

    def do_POST(self):
        if not self._host_ok():
            return self._send(403, "нет", "text/plain; charset=utf-8")
        if self.headers.get("X-Token") != TOKEN:
            return self._json(403, {"ok": False, "error": "чужой запрос"})

        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""

        if parsed.path == "/api/upload":
            try:
                name = _save_upload((query.get("name") or [""])[0], raw)
            except (ValueError, OSError) as e:
                return self._json(400, {"ok": False, "error": str(e)})
            added = scheduler.scan_watch_dir()
            return self._json(200, {"ok": True,
                                    "message": f"{name} — в очереди"
                                    if added else f"{name} принят"})

        name = parsed.path.rsplit("/", 1)[-1]
        handler = COMMANDS.get(name)
        if handler is None:
            return self._json(404, {"ok": False, "error": f"нет команды «{name}»"})

        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._json(400, {"ok": False, "error": "тело не разобралось"})

        try:
            return self._json(200, handler(body) or {"ok": True})
        except Exception as e:
            # Ошибка команды — обычное дело (кривое правило, занятый телефон),
            # и человек должен прочитать её в окне, а не в консоли.
            return self._json(200, {"ok": False, "error": f"{e}"})


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# ------------------------------------------------------------- запуск окна

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def open_window(url):
    """Открыть страницу отдельным окном, без вкладок и адресной строки.

    `--app` понимают Chrome и Edge, а они на Windows есть почти всегда. Если
    не нашлись — обычная вкладка в браузере по умолчанию: выглядит хуже,
    работает так же.
    """
    profile = os.path.join(config.BASE, ".uiprofile")
    for exe in CHROME_PATHS:
        if not os.path.exists(exe):
            continue
        try:
            subprocess.Popen([exe, f"--app={url}",
                              f"--user-data-dir={profile}",
                              "--window-size=1180,860"],
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return os.path.basename(exe)
        except OSError:
            continue

    webbrowser.open(url)
    return "браузер по умолчанию"


def run(port=PORT, open_browser=True):
    prefs.apply()         # выбранные мышкой зрение и телефон
    # Открываем пространство телефона: очередь, расписание и вкусы у каждого
    # свои. Не выбран — берём первый живой, а нет и его — общее пространство,
    # чтобы окно поднялось и без телефона.
    chosen = config.SERIAL or ""
    if not chosen:
        live = [s for s, state in adb.devices() if state == "device"]
        chosen = live[0] if live else ""
    devices.use(chosen)
    # Стоп-крана в окне больше нет, а файл от прошлых версий мог остаться и
    # молча блокировал бы службу. Снимаем на старте.
    config.set_stop(False)
    PROBE.start()
    DISPATCHER.start()      # очередь разгребается сама
    KEEPER.start()          # расписание тоже: выключателя у него больше нет
    _start_bot()          # токен не задан — поднимать нечего, выйдет сразу

    httpd = Server((HOST, port), Handler)
    url = f"http://{HOST}:{port}/"

    print(f"PhoneAgent: {url}")
    if open_browser:
        print(f"окно: {open_window(url)}")
    print("Закрой окно и нажми Ctrl+C здесь, чтобы остановить.")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nостановлено")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    run()
