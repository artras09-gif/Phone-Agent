"""Остановка того, что идёт прямо сейчас.

Отличается от стоп-крана: тот запрещает начинать новое, а это обрывает
текущее действие. Разница видна на живой сессии — стоп-кран она заметит
только на следующем круге, а «отвлёкся на 34 секунды» досидит до конца.

Поток убить нельзя, поэтому остановка кооперативная: код спрашивает
`requested()` в узких местах и спит через `sleep()`, который просыпается
досрочно. Главный выигрыш — в `human.pause`: через неё идут почти все паузы
агента, включая самые длинные.
"""
import contextlib
import threading
import time

_EVENT = threading.Event()
_LOCAL = threading.local()


class Stopped(BaseException):
    """Остановили посреди действия, внутри `guarded()`.

    От BaseException, а не Exception: по пути много `except Exception: pass`
    (снять окно, проверить ленту), и проглоченная остановка дала бы делу идти
    дальше — ровно то, на что жаловались."""


def request():
    """Попросить остановиться."""
    _EVENT.set()


def clear():
    """Снять просьбу. Зовётся перед началом нового действия."""
    _EVENT.clear()


def requested():
    return _EVENT.is_set()


@contextlib.contextmanager
def guarded():
    """Ограждение на ЭТОТ поток: после «Остановить» он больше не трогает
    телефон — следующая команда adb бросает `Stopped` (`adb.raw` зовёт
    `check`), а не выполняется.

    Зачем (жалоба 2026-11-25: «листание ленты не прекращается сразу, как я
    нажму стоп»): цикл ленты спрашивал остановку только в начале ролика, и
    после кнопки круг доигрывался — задуманный лайк, сохранение ссылки (семь
    касаний), заход в комментарии, «вышел и вернулся», свайп на следующий
    ролик. Расставить проверки перед каждым касанием — их десятки и новые
    появляются; закрыть сам вход к телефону — одно место.

    Только на свой поток: уборка после остановки (домой, блокировка) идёт уже
    вне ограждения, а монтаж и бот в соседних потоках его не замечают."""
    before = getattr(_LOCAL, "on", False)
    _LOCAL.on = True
    try:
        yield
    finally:
        _LOCAL.on = before


def guard():
    """Включить ограждение на этот поток без `with` — для длинного цикла,
    который снимает его сам (`unguard`) в своём `finally`."""
    _LOCAL.on = True


def unguard():
    """Снять ограждение с этого потока (уборка в `finally`)."""
    _LOCAL.on = False


def check():
    """Внутри `guarded()`: остановили — бросить `Stopped`."""
    if getattr(_LOCAL, "on", False) and _EVENT.is_set():
        raise Stopped()


_CALLER = {"thread": None, "jobs": None}
_CALLER_LOCK = threading.Lock()


def call(fn, *args, **kwargs):
    """Долгий вызов (вопрос модели), которого «Остановить» не ждёт.

    Внутри `guarded()` вызов идёт в отдельном ПОСТОЯННОМ потоке, а этот ждёт
    его кусками и на остановке сразу бросает `Stopped`; брошенный ответ
    доходит в фоне и выкидывается. Поток один на всю программу, а не новый
    на каждый вызов: соединение с облаком модели живёт в потоке (`vision._Pool`),
    и новый поток платил бы ~0.6 с за рукопожатие на каждом ролике.
    Вне ограждения — обычный вызов."""
    if not getattr(_LOCAL, "on", False):
        return fn(*args, **kwargs)
    check()
    import queue

    with _CALLER_LOCK:
        if _CALLER["thread"] is None or not _CALLER["thread"].is_alive():
            _CALLER["jobs"] = queue.Queue()

            def loop(jobs):
                while True:
                    task, box, done = jobs.get()
                    try:
                        box["result"] = task()
                    except BaseException as e:          # отдать тому, кто ждёт
                        box["error"] = e
                    done.set()

            _CALLER["thread"] = threading.Thread(target=loop, args=(_CALLER["jobs"],),
                                                 daemon=True, name="abort-call")
            _CALLER["thread"].start()
        jobs = _CALLER["jobs"]
    box, done = {}, threading.Event()
    jobs.put((lambda: fn(*args, **kwargs), box, done))
    while not done.wait(0.1):
        if _EVENT.is_set():
            raise Stopped()
    if "error" in box:
        raise box["error"]
    return box.get("result")


def sleep(seconds, step=0.2):
    """Пауза, которую можно прервать. True — прервали, False — доспали.

    Спим кусками, а не одним `time.sleep`: иначе кнопка «Остановить» ждала бы
    конца паузы, а они бывают по сорок секунд.
    """
    if seconds <= 0:
        return _EVENT.is_set()
    if _EVENT.wait(0):
        return True
    deadline = time.time() + seconds
    while True:
        left = deadline - time.time()
        if left <= 0:
            return False
        if _EVENT.wait(min(step, left)):
            return True
