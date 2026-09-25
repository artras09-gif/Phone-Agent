"""Три доработки «похожести на человека», проверенные числами.

  1. лайк больше не лежит всегда в одной точке ролика;
  2. понравившийся ролик изредка смотрится по второму кругу;
  3. время просмотра СВЯЗАНО между соседними роликами (дрейф внимания).

Третье — единственное, что нельзя проверить, глядя на одно значение: каждое
число и раньше было правдоподобным, неправдоподобной была их независимость.
Поэтому меряем автокорреляцию по лагу 1.
"""
import os
import statistics
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


def autocorr(xs):
    """Связь соседних значений. ~0 — независимы, >0 — идут полосами."""
    mean = statistics.fmean(xs)
    var = sum((x - mean) ** 2 for x in xs)
    if not var:
        return 0.0
    cov = sum((a - mean) * (b - mean) for a, b in zip(xs, xs[1:]))
    return cov / var


# --- 1. момент лайка --------------------------------------------------
print("--- 1. когда ставится лайк ---")
moments = [human.like_moment() for _ in range(20000)]
say(min(moments) > 0 and max(moments) < 1, "всегда внутри ролика")
buckets = [0] * 5
for m in moments:
    buckets[min(4, int(m * 5))] += 1
share = [b / len(moments) for b in buckets]
print("    по пятым долям ролика: " + "  ".join(f"{s:.0%}" for s in share))
say(min(share) > 0.04, "ранние лайки тоже случаются, не только в конце")
say(share[-1] < 0.45, "и в конце не скапливается большинство")
say(statistics.fmean(moments) > 0.5, f"смещено ко второй половине: {statistics.fmean(moments):.2f}")
say(len(set(round(m, 3) for m in moments)) > 900, "значения не повторяются")

# --- 2. второй круг ---------------------------------------------------
print("\n--- 2. пересмотр понравившегося ---")
n = 4000
got = sum(session._rewatch_loops("интересно") for _ in range(n)) / n
say(abs(got - config.REWATCH_PROBABILITY) < 0.02,
    f"на «интересно» {got:.0%} при заданных {config.REWATCH_PROBABILITY:.0%}")
say(sum(session._rewatch_loops("нейтрально") for _ in range(n)) == 0,
    "нейтральное не пересматривается никогда")
say(sum(session._rewatch_loops("мимо") for _ in range(n)) == 0,
    "и «мимо» тоже (иначе тянуло бы рекомендации)")

# --- 3. дрейф внимания ------------------------------------------------
print("\n--- 3. связь между соседними роликами ---")
SESSION = 120                      # роликов в сессии

was, now = [], []
for _ in range(400):
    # было: каждый ролик независимо
    was.append(autocorr([human.dwell() for _ in range(SESSION)]))
    # стало: то же самое, но помноженное на блуждающее настроение
    mood, row = human.drift(1.0), []
    for _ in range(SESSION):
        mood = human.drift(mood)
        row.append(human.dwell() * mood)
    now.append(autocorr(row))

before, after = statistics.fmean(was), statistics.fmean(now)
print(f"    было (независимо): {before:+.3f}")
print(f"    стало (с дрейфом): {after:+.3f}")
say(abs(before) < 0.05, "раньше связи не было вовсе — то есть машина")
say(after > before + 0.05, "теперь соседние ролики связаны")

# --- 4. дрейф не убегает и не залипает --------------------------------
print("\n--- 4. само настроение ---")
mood, walk = 1.0, []
for _ in range(200000):
    mood = human.drift(mood)
    walk.append(mood)
import inspect
bounds = inspect.signature(human.drift).parameters
lo = bounds["lo"].default
hi = bounds["hi"].default
say(lo <= min(walk) and max(walk) <= hi,
    f"держится в границах {lo}..{hi}: {min(walk):.2f}..{max(walk):.2f}")
# Именно СРЕДНЕЕ, а не медиана: у мультипликативного множителя медиана ниже
# единицы по построению, а важно, чтобы не поехал общий уровень просмотров.
say(abs(statistics.fmean(walk) - 1.0) < 0.03,
    f"общий уровень не поехал: среднее {statistics.fmean(walk):.3f}")
edge = sum(1 for v in walk if v < lo * 1.05 or v > hi * 0.95) / len(walk)
say(edge < 0.02, f"у границ почти не сидит: {edge:.2%}")
say(autocorr(walk) > 0.7, f"меняется плавно, а не скачет: {autocorr(walk):.2f}")

# один прогон целиком — посмотреть глазами
mood, row = human.drift(1.0), []
for _ in range(30):
    mood = human.drift(mood)
    row.append(mood)
print("    пример сессии: " + " ".join(f"{v:.2f}" for v in row[:15]))
print("                   " + " ".join(f"{v:.2f}" for v in row[15:]))

print("\nИТОГ:", "всё сошлось" if ok else "ЕСТЬ ПРОВАЛЫ")
sys.exit(0 if ok else 1)
