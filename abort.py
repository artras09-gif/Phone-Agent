"""Остановка того, что идёт прямо сейчас.

Отличается от стоп-крана: тот запрещает начинать новое, а это обрывает
текущее действие. Разница видна на живой сессии — стоп-кран она заметит
только на следующем круге, а «отвлёкся на 34 секунды» досидит до конца.

Поток убить нельзя, поэтому остановка кооперативная: код спрашивает
`requested()` в узких местах и спит через `sleep()`, который просыпается
досрочно. Главный выигрыш — в `human.pause`: через неё идут почти все паузы
агента, включая самые длинные.
"""
import threading
import time

_EVENT = threading.Event()


def request():
    """Попросить остановиться."""
    _EVENT.set()


def clear():
    """Снять просьбу. Зовётся перед началом нового действия."""
    _EVENT.clear()


def requested():
    return _EVENT.is_set()


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
