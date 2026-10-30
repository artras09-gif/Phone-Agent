"""Несколько телефонов сразу: по процессу на каждый.

Зачем именно процессы, а не потоки. Программа складывает «с каким телефоном
мы работаем» в глобальные переменные: `config.SERIAL`, четыре пути в
`config`, `plan.PATH`, `jobs.DEVICE`, `adb._PREFERRED`, приёмник журнала в
`runlog`. `devices.use()` их переставляет — и именно поэтому окно запрещало
менять телефон, пока идёт работа. Расставить два телефона по потокам одного
процесса значит перевести ВСЁ это на контекстные переменные, до последней
строки: одна пропущенная — и видео уходит в чужой аккаунт.

Процесс решает это по построению. У каждого свои глобальные переменные, свой
`abort`, свой планировщик; `adb -s <серийник>` для того и сделан, чтобы
разговаривать с нужным телефоном; папки `devices/<серийник>/` уже разложены.
Надзирателю остаётся запускать, следить и останавливать.

Что всё-таки общее и потому требует уговора:
  * `jobs.db` — переведена в WAL, иначе писатель запирал бы базу целиком;
  * модель зрения — очередь через `gate.vision()`;
  * сам телефон — замок на устройство, чтобы двое не повели один телефон.

Остановка кооперативная. Убивать процесс нельзя: он может держать телефон
посреди публикации, на экране редактора. Поэтому надзиратель кладёт файл, а
служба его замечает, просит текущее действие прерваться (`abort`) и выходит
сама.

С 2026-10-30 так работает КАЖДЫЙ телефон, и тот, что открыт в окне, тоже
(«телефоны должны работать одновременно, и переключаться между ними, пока
на одном сессия»). Служба телефона — это окно без окна (`webui.run_worker`):
те же кнопки, расписание, сторож очереди, бот и статистика, свой HTTP на
свободном порту. Окно (`webui.run`) стало пультом: само ни одним телефоном не
правит, поднимает службу каждому подключённому и пересылает запросы страницы
службе выбранного. Порт и токен служба кладёт в `.fleet/<телефон>.json`.
Окно закрыли — у службы закрылся stdin, и она выходит сама, штатно.
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import config
import devices
import gate

# Управляющие файлы. Отдельной папкой, а не в `devices/<серийник>/`: там
# хозяйство пользователя (очередь, расписание, вкусы), и служебному мусору
# среди него не место.
CONTROL = os.path.join(config.BASE, ".fleet")

LINES_KEPT = 400        # строк журнала на телефон в памяти надзирателя

# Сколько ждать, пока служба выйдет сама, прежде чем прибить.
STOP_GRACE = 45.0

# Перезапуск упавшей службы. Растущая пауза нужна против «падает сразу»:
# без неё сломанная настройка крутила бы запуск в цикле сотни раз в минуту.
RESTART_DELAYS = (5, 15, 60, 300)


def _slug(serial):
    return devices.slug(serial)


def stop_path(serial):
    return os.path.join(CONTROL, f"{_slug(serial)}.stop")


def log_path(serial):
    return os.path.join(config.LOG_DIR, f"служба-{_slug(serial)}.log")


def control_path(serial):
    """Где служба телефона оставляет свой порт и токен для окна."""
    return os.path.join(CONTROL, f"{_slug(serial)}.json")


def write_control(serial, port, token):
    """Вызывается В СЛУЖБЕ, когда её HTTP поднялся. Через временный файл:
    окно не должно прочитать половину записи."""
    os.makedirs(CONTROL, exist_ok=True)
    path = control_path(serial)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"serial": serial, "port": int(port), "token": token,
                   "pid": os.getpid()}, f)
    os.replace(tmp, path)


def read_control(serial):
    try:
        with open(control_path(serial), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) and data.get("port") else None
    except (OSError, ValueError):
        return None


def drop_control(serial):
    try:
        os.remove(control_path(serial))
    except OSError:
        pass


def device_gate(serial):
    """Замок на телефон: его держит работающая служба.

    Нужен затем, что запустить службу можно и из окна, и из консоли. Двое,
    ведущие один телефон, — это два маршрута публикации, перемешанные на
    одном экране; поймать такое по логам почти невозможно.
    """
    return gate.Gate(f"device.{_slug(serial)}", 1,
                     os.path.join(config.BASE, ".gates"))


def telegram_gate(bot=""):
    """Замок на Telegram-бота: читатель у него ровно один.

    `bot` — номер бота (часть токена до двоеточия): у каждого телефона может
    быть свой бот, и замок у каждого свой. Пусто — прежний общий замок.

    `getUpdates` устроен так, что второй читатель того же токена получает
    409 Conflict, и тогда ломаются ОБА: сообщения начинают доставаться
    случайному. Поэтому бота поднимает та служба, которая успела занять слот,
    а остальные работают без него. Слот держится до конца жизни процесса —
    отпускать его некому и незачем.
    """
    name = f"telegram.{bot}" if bot else "telegram"
    # `Held`, а не `Gate`: окно отпускает этот замок из другого потока, чем
    # брало (см. gate.Held) — с `Gate` он не отпускался вовсе.
    return gate.Held(name, os.path.join(config.BASE, ".gates"))


def bot_busy(bot):
    """Опрашивает ли этого бота какой-нибудь процесс программы прямо сейчас."""
    g = telegram_gate(bot)
    try:
        g.acquire(timeout=0.01)
    except gate.Timeout:
        return True
    g.release()
    return False


def busy_elsewhere(serial):
    """Уже ведёт ли этот телефон кто-то другой."""
    g = device_gate(serial)
    try:
        g.acquire(timeout=0.01)
    except gate.Timeout:
        return True
    g.release()
    return False


# ------------------------------------------------------- сторона службы

def install_child_stopper(serial, watch_stdin=False):
    """Вызывается В САМОЙ СЛУЖБЕ. Возвращает проверку «пора выходить».

    Заодно поднимает сторожа: он замечает просьбу и обрывает текущее
    действие через `abort`, иначе выхода пришлось бы ждать до конца сессии,
    а она бывает получасовой.

    `watch_stdin` — службу запустило окно: оно держит её stdin открытым, и
    конец stdin значит «окна больше нет» (закрыли, упало, сняли в диспетчере
    задач). Службы без окна не остаются: им некому показать журнал, и
    остановить их было бы нечем.
    """
    import abort

    flag = {"stop": False}
    path = stop_path(serial)

    # Файл мог остаться от прошлого раза — тогда служба вышла бы сразу.
    try:
        os.remove(path)
    except OSError:
        pass

    def stop(why):
        if not flag["stop"]:
            flag["stop"] = True
            print(f"{why} — заканчиваю", flush=True)
            abort.request()

    def watch():
        while not flag["stop"]:
            if os.path.exists(path):
                stop("попросили остановиться")
                return
            time.sleep(0.5)

    def orphan():
        stream = getattr(sys.stdin, "buffer", None)
        if stream is None:
            return
        try:
            while stream.read(1024):
                pass
        except (OSError, ValueError):
            pass
        stop("окна программы больше нет")

    threading.Thread(target=watch, daemon=True).start()
    if watch_stdin and sys.stdin is not None:
        threading.Thread(target=orphan, daemon=True).start()
    return lambda: flag["stop"]


def child_command(serial):
    """Чем запускать службу для телефона: окно без окна (`main.py worker`)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "worker"]
    return [sys.executable, os.path.join(config.BASE, "main.py"), "worker"]


# ---------------------------------------------------- сторона надзирателя

class Service:
    """Одна служба: процесс, его журнал и состояние."""

    def __init__(self, serial):
        self.serial = serial
        self.name = devices.name_of(serial)
        self.proc = None
        self.lines = []
        self.started_at = 0.0
        self.stopped_reason = ""
        self.restarts = 0
        self.want_running = False
        # Остановили руками («Остановить» во «Флоте») — окно не поднимает
        # такую службу само, пока её не запустят кнопкой.
        self.paused = False
        self._lock = threading.Lock()

    # --- журнал ---

    def log(self, line):
        with self._lock:
            self.lines.extend(str(line).rstrip("\n").split("\n"))
            if len(self.lines) > LINES_KEPT:
                del self.lines[:len(self.lines) - LINES_KEPT]

    def tail(self, since=0):
        with self._lock:
            total = len(self.lines)
            since = max(0, min(int(since), total))
            return total, self.lines[since:]

    # --- состояние ---

    def alive(self):
        """Наш ли процесс жив. Чужой службы это не касается — см. `running`."""
        return self.proc is not None and self.proc.poll() is None

    def running(self):
        """Работает ли служба вообще — хоть наша, хоть чужая.

        Чужая появляется буднично: окно закрыли, а службы остались работать
        (они и должны — у них своё расписание). Новое окно про них не знает,
        но замок на телефон занят, и это видно.
        """
        return self.alive() or busy_elsewhere(self.serial)

    def state(self):
        mine = self.alive()
        return {
            "serial": self.serial,
            "name": self.name,
            "running": self.running(),
            "managed": mine,        # False = работает, но запущена не нами
            "ready": self.ready() is not None,
            "paused": self.paused,
            "pid": self.proc.pid if mine else 0,
            "uptime": int(time.time() - self.started_at) if mine else 0,
            "restarts": self.restarts,
            "reason": self.stopped_reason,
        }

    # --- связь со службой ---

    def ready(self):
        """Порт и токен службы, если её HTTP уже отвечает за НАШ процесс, иначе
        None. Файл от прошлой, упавшей службы не годится: порт там чужой."""
        if not self.alive():
            # Служба, поднятая не нами (консолью), держит замок телефона и
            # тоже оставляет свой порт — с ней пульт говорит так же.
            return read_control(self.serial) if busy_elsewhere(self.serial) else None
        data = read_control(self.serial)
        # По pid не сверить: в собранном exe служба — дочерний процесс
        # загрузчика, и pid у неё другой. Но перед запуском файл стёрт
        # (`start`), так что свежий файл — от нашей службы.
        try:
            fresh = os.path.getmtime(control_path(self.serial)) >= self.started_at - 1
        except OSError:
            fresh = False
        return data if data and fresh else None

    def call(self, path, body=None, raw=None, timeout=10.0):
        """Запрос к службе: GET без тела, POST с JSON-телом `body` или сырыми
        байтами `raw`. Возвращает разобранный JSON. Не готова или не ответила —
        RuntimeError с понятной причиной."""
        where = self.ready()
        if where is None:
            raise RuntimeError("служба телефона ещё не поднялась")
        url = f"http://127.0.0.1:{where['port']}{path}"
        data = None
        headers = {"X-Token": str(where.get("token", ""))}
        if raw is not None:
            data = raw
            headers["Content-Type"] = "application/octet-stream"
        elif body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode("utf-8") or "{}")
            except ValueError:
                raise RuntimeError(f"служба телефона ответила {e.code}")
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise RuntimeError(f"служба телефона не отвечает: {e}")

    # --- запуск и остановка ---

    def start(self):
        if self.alive():
            return False
        if busy_elsewhere(self.serial):
            self.stopped_reason = "этот телефон уже ведёт другая служба"
            self.log(f"ОШИБКА: {self.stopped_reason}")
            return False

        env = dict(os.environ)
        env["ANDROID_SERIAL"] = self.serial
        # Служба печатает по-русски, а её вывод мы читаем трубой. Без этого
        # на Windows дочерний процесс возьмёт кодировку консоли, и в журнале
        # окажется каша вместо букв.
        env["PYTHONIOENCODING"] = "utf-8"
        # Без этого вывод службы копится в её собственном буфере и доходит до
        # нас пачками по несколько килобайт: в окне надзирателя минутами
        # пусто, а потом всё разом. У живого журнала весь смысл в «сразу».
        env["PYTHONUNBUFFERED"] = "1"
        env["PHONEAGENT_MANAGED"] = "1"

        try:
            os.makedirs(CONTROL, exist_ok=True)
            os.remove(stop_path(self.serial))
        except OSError:
            pass
        drop_control(self.serial)          # порт прошлой службы уже чужой

        creation = 0
        if os.name == "nt":
            # Своя группа процессов: иначе Ctrl+C в консоли надзирателя
            # разложил бы разом все службы, включая ту, что сейчас
            # посреди публикации.
            creation = subprocess.CREATE_NEW_PROCESS_GROUP
            creation |= getattr(subprocess, "CREATE_NO_WINDOW", 0)

        try:
            # stdin — труба, в которую мы никогда не пишем: пока окно живо, она
            # открыта, а не стало окна — закрылась, и служба выходит сама
            # (`install_child_stopper(watch_stdin=True)`).
            self.proc = subprocess.Popen(
                child_command(self.serial),
                cwd=config.BASE, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, stdin=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
                bufsize=1, creationflags=creation,
            )
        except OSError as e:
            self.stopped_reason = f"не удалось запустить: {e}"
            self.log(f"ОШИБКА: {self.stopped_reason}")
            return False

        self.started_at = time.time()
        self.stopped_reason = ""
        self.want_running = True
        self.log(f"служба запущена (pid {self.proc.pid})")
        threading.Thread(target=self._pump, daemon=True).start()
        return True

    def _pump(self):
        """Читать вывод службы: в память для окна и в файл для разбора потом."""
        proc = self.proc
        path = log_path(self.serial)
        try:
            os.makedirs(config.LOG_DIR, exist_ok=True)
            sink = open(path, "a", encoding="utf-8", newline="\n")
        except OSError:
            sink = None
        try:
            for line in proc.stdout:
                self.log(line)
                if sink is not None:
                    sink.write(line if line.endswith("\n") else line + "\n")
                    sink.flush()
        except (ValueError, OSError):
            pass
        finally:
            if sink is not None:
                sink.close()

    def stop(self, timeout=STOP_GRACE):
        """Попросить службу выйти. Ждём, потому что она может держать телефон.

        Работает и для ЧУЖОЙ службы — той, что осталась от прошлого окна.
        Просьба передаётся файлом, а не сигналом, ровно затем, чтобы её
        услышал любой процесс, а не только наш ребёнок. Признак ухода тогда
        другой: не «наш процесс кончился», а «замок на телефон отпущен».
        """
        self.want_running = False
        mine = self.alive()
        if not mine and not busy_elsewhere(self.serial):
            return True

        try:
            os.makedirs(CONTROL, exist_ok=True)
            with open(stop_path(self.serial), "w", encoding="utf-8") as f:
                f.write("остановлено надзирателем\n")
        except OSError as e:
            self.log(f"не смог попросить об остановке: {e}")
            return False

        self.log("прошу службу закончить..." if mine
                 else "прошу закончить службу, запущенную не отсюда...")
        deadline = time.time() + timeout
        while time.time() < deadline:
            gone = (not self.alive()) if mine else (not busy_elsewhere(self.serial))
            if gone:
                self.log("служба вышла сама")
                self._cleanup()
                return True
            time.sleep(0.3)

        if not mine:
            # Чужой процесс убивать нечем — ручки на него у нас нет, а искать
            # его по списку процессов и стрелять по совпадению имени опаснее,
            # чем оставить работать: под руку попадёт соседняя служба.
            self.log(f"за {timeout:.0f} с не отпустила телефон; "
                     "останови её там, где запускала")
            return False

        # Не вышла: значит застряла там, где просьбу не проверяют.
        self.log(f"за {timeout:.0f} с не вышла — снимаю принудительно")
        try:
            self.proc.kill()
            self.proc.wait(timeout=10)
        except Exception:
            pass
        self._cleanup()
        return False

    def _cleanup(self):
        try:
            os.remove(stop_path(self.serial))
        except OSError:
            pass
        drop_control(self.serial)


class Fleet:
    """Все службы разом: запуск, присмотр, остановка."""

    WATCH_EVERY = 3.0

    def __init__(self):
        self.services = {}
        self._lock = threading.Lock()
        self._watching = False
        self._failures = {}

    def service(self, serial):
        with self._lock:
            found = self.services.get(serial)
            if found is None:
                found = Service(serial)
                self.services[serial] = found
            return found

    def start(self, serial):
        svc = self.service(serial)
        svc.paused = False
        ok = svc.start()
        if ok:
            self._failures[serial] = 0
            self._watch()
        return ok

    def ensure(self, serial):
        """Окно видит подключённый телефон — у него должна работать служба.
        Не трогаем остановленную руками, работающую и ту, что упала и ждёт
        перезапуска (её ведёт присмотр). True — запустили сейчас."""
        svc = self.service(serial)
        if svc.paused or svc.alive() or svc.want_running or busy_elsewhere(serial):
            return False
        return self.start(serial)

    def stop(self, serial, timeout=STOP_GRACE, pause=True):
        """`pause` — остановили руками: сама служба больше не поднимется.
        При закрытии окна не ставим: в следующий раз все поднимутся заново."""
        svc = self.service(serial)
        if pause:
            svc.paused = True
        return svc.stop(timeout)

    def stop_all(self, timeout=STOP_GRACE, pause=False):
        """Останавливаем ПАРАЛЛЕЛЬНО: последовательно вышло бы N × 45 с.
        `pause` — кнопкой «Остановить все»; при закрытии окна — нет."""
        threads = []
        for serial in list(self.services):
            t = threading.Thread(target=self.stop, args=(serial, timeout, pause),
                                 daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join(timeout + 15)

    def running(self):
        return [s for s in self.services.values() if s.alive()]

    def state(self):
        with self._lock:
            items = list(self.services.values())
        return [s.state() for s in items]

    # --- присмотр ---

    def _watch(self):
        if self._watching:
            return
        self._watching = True
        threading.Thread(target=self._watch_loop, daemon=True).start()

    def _watch_loop(self):
        # Когда перезапускать каждую — по отметке времени, а не сном на месте.
        # Спать прямо здесь нельзя: пауза растёт до пяти минут, и всё это
        # время цикл не смотрел бы на ОСТАЛЬНЫЕ службы. Одна упавшая
        # задерживала бы перезапуск соседних.
        due = {}
        while True:
            time.sleep(self.WATCH_EVERY)
            now = time.time()
            for serial, svc in list(self.services.items()):
                if svc.alive():
                    self._failures[serial] = 0
                    due.pop(serial, None)
                    continue
                if not svc.want_running:
                    due.pop(serial, None)
                    continue

                when = due.get(serial)
                if when is None:
                    code = svc.proc.poll() if svc.proc else None
                    n = self._failures.get(serial, 0)
                    delay = RESTART_DELAYS[min(n, len(RESTART_DELAYS) - 1)]
                    self._failures[serial] = n + 1
                    svc.log(f"служба завершилась (код {code}), "
                            f"перезапуск через {delay} с")
                    due[serial] = now + delay
                    continue

                if now >= when:
                    due.pop(serial, None)
                    svc.restarts += 1
                    svc.start()


FLEET = Fleet()
