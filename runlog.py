"""Журнал прогона, который видно сразу.

Раньше строки копились в списке и печатались одной пачкой в конце: сессия
идёт три минуты, и всё это время на экране пусто — непонятно, работает
агент или завис. Класс ведёт себя как обычный список (код, который его
наполняет, не поменялся ни строкой), но каждую строку сразу отдаёт на экран.

Список при этом сохраняется целиком: `serve` кладёт готовый текст в базу
через `jobs.log_event`, и ему нужен весь прогон разом, а не по строчке.
"""
import config

# Необязательный второй приёмник строк. Веб-интерфейс подписывается сюда и
# показывает прогон в окне, пока тот идёт. Консоль при этом продолжает
# работать как раньше: sink добавляется к печати, а не заменяет её.
SINK = None


def set_sink(fn):
    """Подписать приёмник (или снять, передав None). Возвращает прежний."""
    global SINK
    previous, SINK = SINK, fn
    return previous


class Log(list):
    """Список строк, печатающий то, что в него кладут."""

    def __init__(self, first=None, live=None):
        super().__init__()
        self.live = config.LIVE_LOG if live is None else live
        if first is not None:
            self.append(first)

    def append(self, line):
        super().append(line)

        sink = SINK
        if sink is not None:
            # Сбой приёмника не должен ронять прогон: журнал — дело
            # второстепенное, а сессия идёт на живом телефоне.
            try:
                sink(line)
            except Exception:
                pass

        if not self.live:
            return
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            # Консоль не в UTF-8, а модель прислала иероглифы. Ронять из-за
            # этого сессию нельзя: строка нужна на экране, пусть и с «?».
            print(line.encode("ascii", "replace").decode(), flush=True)

    def text(self):
        return "\n".join(self)
