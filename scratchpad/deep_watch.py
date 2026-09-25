"""Проверка «залипания»: раз в 8-10 роликов один досматривается целиком.

Телефон не нужен — гоняем сам механизм счётчика, как `human.swipe_geometry`
проверяется отдельно от свайпов.
"""
import collections
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402
import human  # noqa: E402
import session  # noqa: E402

ok = True


def say(good, text):
    global ok
    ok &= good
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


# --- 1. сам интервал --------------------------------------------------
print("--- интервал между залипаниями ---")
gaps = [human.deep_watch_gap() for _ in range(20000)]
counts = collections.Counter(gaps)
low, high = config.DEEP_WATCH_EVERY
say(min(gaps) >= low and max(gaps) <= high,
    f"всё внутри границ {low}-{high}: {min(gaps)}..{max(gaps)}")
say(len(counts) == high - low + 1,
    f"используются все значения: {sorted(counts)}")
share = max(counts.values()) / len(gaps)
say(share < 0.40, f"нет перекоса: самое частое значение {share:.0%}")

print("\n--- выключение ---")
saved = config.DEEP_WATCH_EVERY
config.DEEP_WATCH_EVERY = (0, 0)
say(human.deep_watch_gap() == 0, "(0,0) выключает залипание")
config.DEEP_WATCH_EVERY = None
say(human.deep_watch_gap() == 0, "пустая настройка тоже")
config.DEEP_WATCH_EVERY = saved


def run(n, skip_every=None, allow_skipped=False):
    """Прогнать n роликов, вернуть номера, на которых залипли."""
    config.DEEP_WATCH_ON_SKIPPED = allow_skipped
    state = {}
    session._deep_watch_rearm(state, 0)
    fired = []
    for watched in range(1, n + 1):
        skip = bool(skip_every) and (watched % skip_every != 0)
        if session._deep_watch_due(state, watched):
            if not skip or allow_skipped:
                fired.append(watched)
                session._deep_watch_rearm(state, watched)
    return fired


# --- 2. обычная лента: срабатывает регулярно --------------------------
print("\n--- обычная лента (всё смотрим) ---")
fired = run(1000)
steps = [b - a for a, b in zip(fired, fired[1:])]
# Ожидание считаем ОТ НАСТРОЙКИ, а не числом из головы: интервал менялся
# (было 8-10, стало 25-35), и прежняя проверка «больше 90 раз на тысячу»
# начала падать не потому, что что-то сломалось.
_low, _high = config.DEEP_WATCH_EVERY
_expected = 1000 / ((_low + _high) / 2)
say(abs(len(fired) - _expected) < _expected * 0.25,
    f"за 1000 роликов залипаний: {len(fired)} (ожидалось около {_expected:.0f})")
say(min(steps) >= low and max(steps) <= high,
    f"шаг всегда {low}-{high}: {min(steps)}..{max(steps)}")
say(len(set(steps)) > 1, "шаг не постоянный (иначе виден с одного взгляда)")
say(fired[0] >= low, f"первое залипание не с порога: на {fired[0]}-м ролике")

# --- 3. первое залипание не всегда на одном и том же номере -----------
firsts = {run(60)[0] for _ in range(300)}
say(len(firsts) > 1, f"начало сессии тоже разное: {sorted(firsts)}")

# --- 4. строгие вкусы: почти всё листается ----------------------------
print("\n--- строгие вкусы (подходит каждый 5-й) ---")
fired = run(1000, skip_every=5)
say(len(fired) > 0, f"залипания случаются: {len(fired)}")
say(all(v % 5 == 0 for v in fired),
    "и только на подходящих роликах — лента не портится")
steps = [b - a for a, b in zip(fired, fired[1:])]
say(max(steps) <= high + 5,
    f"счётчик не сгорает впустую: наибольший разрыв {max(steps)} роликов")

# --- 5. разрешили залипать на неподходящем ----------------------------
print("\n--- то же, но с DEEP_WATCH_ON_SKIPPED = True ---")
fired = run(1000, skip_every=5, allow_skipped=True)
say(not all(v % 5 == 0 for v in fired),
    "залипает и на неподходящих (как и просили настройкой)")
config.DEEP_WATCH_ON_SKIPPED = False

# --- 6. предел просмотра ----------------------------------------------
print("\n--- предел на просмотр ---")
with_stream = session._deep_watch_seconds({"stream": object(), "frame_size": 900})
blind = session._deep_watch_seconds({"stream": None, "frame_size": 0})
say(with_stream == config.DEEP_WATCH_MAX_SEC,
    f"с потоком кадров: до {with_stream:.0f}с (обрыв поймает конец ролика)")
say(blind < with_stream,
    f"без потока скромнее: {blind:.0f}с (конец ловить нечем)")

print("\nИТОГ:", "залипание работает" if ok else "ЕСТЬ ПРОВАЛЫ")
sys.exit(0 if ok else 1)
