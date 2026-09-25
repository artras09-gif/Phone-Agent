"""Пропускник к общему ресурсу — на несколько ПРОЦЕССОВ сразу.

Зачем. Когда телефон один, делить нечего. Когда их три, каждый живёт своим
процессом (см. `fleet`), а модель зрения у них ОДНА на всех: локальная 3B в
LM Studio держит в памяти один экземпляр и обслуживает запросы по очереди.
Три процесса, ломящиеся в неё одновременно, не ускоряют ничего — они лишь
превращают предсказуемые 1.3 с на кадр в непредсказуемые 4 с, потому что
каждый ждёт своей очереди уже ВНУТРИ сервера, где мы этого не видим и не
можем ни отменить, ни измерить.

Поэтому очередь делаем своей: сколько процессов пропускать к модели разом —
решает `config.VISION_MAX_PARALLEL`. Ноль означает «не ограничивать» и
ставится для облака, где параллельные запросы обслуживаются по-настоящему.

Как устроено. Слот — это файл, запертый средствами ОС (`flock` на POSIX,
`msvcrt.locking` на Windows). Такой замок снимается операционной системой,
когда процесс умирает, — а значит, упавшая сессия НЕ оставляет после себя
навсегда занятый слот. Это главное свойство: сторожа с проверкой «а жив ли
владелец» пришлось бы писать самому, и на Windows он был бы опасен —
`os.kill(pid, 0)` там не спрашивает процесс о здоровье, а завершает его.

Замок перевзятия (`_depth`) нужен на случай, когда обращение к модели
случается внутри другого такого же обращения: `vision.ask` при вырожденном
ответе перезагружает модель и переспрашивает. Без счётчика процесс встал бы
в очередь сам за собой и не дождался бы никогда.
"""
import os
import threading
import time

try:                        # POSIX
    import fcntl
    _WINDOWS = False
except ImportError:         # Windows
    import msvcrt
    _WINDOWS = True


class Timeout(RuntimeError):
    """Не дождались очереди. Отдельный тип: это не поломка ресурса."""


def _try_lock(fd):
    """Запереть файл, не дожидаясь. True — заперли."""
    try:
        if _WINDOWS:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fd):
    try:
        if _WINDOWS:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


class Gate:
    """Пропускник на `slots` мест. `slots <= 0` — не ограничивать."""

    def __init__(self, name, slots, root):
        self.name = name
        self.slots = int(slots or 0)
        self.root = root
        self._local = threading.local()

    # --- внутреннее ------------------------------------------------------

    def _depth(self):
        return getattr(self._local, "depth", 0)

    def _slot_path(self, i):
        return os.path.join(self.root, f"{self.name}.{i}.slot")

    def _grab(self):
        """Попытаться занять любой свободный слот. Вернуть fd или None."""
        for i in range(self.slots):
            path = self._slot_path(i)
            try:
                fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
            except OSError:
                continue
            if _try_lock(fd):
                return fd
            os.close(fd)
        return None

    # --- наружное --------------------------------------------------------

    def acquire(self, timeout=None, poll=0.05):
        """Дождаться места. `timeout=None` — ждать сколько угодно."""
        if self.slots <= 0:
            self._local.depth = self._depth() + 1
            return True
        if self._depth():                       # уже держим — проходим насквозь
            self._local.depth = self._depth() + 1
            return True

        try:
            os.makedirs(self.root, exist_ok=True)
        except OSError:
            # Не смогли даже завести папку — пропускаем без очереди.
            # Остаться без зрения хуже, чем разделить модель на троих.
            self._local.depth = self._depth() + 1
            return True

        deadline = None if timeout is None else time.time() + timeout
        while True:
            fd = self._grab()
            if fd is not None:
                self._local.fd = fd
                self._local.depth = 1
                return True
            if deadline is not None and time.time() >= deadline:
                raise Timeout(f"очередь к «{self.name}» не подошла за {timeout:.0f} с")
            time.sleep(poll)

    def release(self):
        depth = self._depth()
        if depth <= 0:
            return
        self._local.depth = depth - 1
        if depth > 1:
            return
        fd = getattr(self._local, "fd", None)
        if fd is not None:
            _unlock(fd)
            try:
                os.close(fd)
            except OSError:
                pass
            self._local.fd = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False


class _Passthrough:
    """Заглушка на случай «ограничивать нечего»: тот же вид, ничего не делает."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


PASS = _Passthrough()

_GATES = {}
_GATES_LOCK = threading.Lock()


def vision():
    """Пропускник к модели зрения. Настройка читается каждый раз.

    Читать её на месте, а не запоминать при первом вызове, важно: провайдера
    переключают мышкой в окне, и после перехода на облако ограничение должно
    сняться без перезапуска.
    """
    import config

    slots = getattr(config, "VISION_MAX_PARALLEL", 0)
    if not slots or slots <= 0:
        return PASS

    root = os.path.join(config.BASE, ".gates")
    key = ("vision", slots, root)
    with _GATES_LOCK:
        found = _GATES.get(key)
        if found is None:
            found = Gate("vision", slots, root)
            _GATES[key] = found
    return found
