"""План сессий: свои правила вместо жёстких часов в config.

Правило — это «в такие-то дни недели, в случайный момент такого-то окна,
смотреть столько-то минут». Точное время специально не задаётся: запуск
ровно в 21:00:00 каждый вечер виден в логах платформы за неделю.

Правил можно завести сколько угодно, лимит сессий в день на них не
распространяется — их поставил человек, значит так и надо.

Файл `plan.json` правится руками, перезапускать ничего не нужно: служба
перечитывает план на каждую смену суток.
"""
import datetime as dt
import json
import os
import random

import config

PATH = os.path.join(config.BASE, "plan.json")

DAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")

# Латиница — не для красоты: в cmd кириллица в аргументах местами приезжает
# битой (см. историю с CP866), и «mon-fri» всегда сработает.
LATIN = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

ALIASES = {
    "каждый день": range(7), "ежедневно": range(7), "все": range(7),
    "*": range(7), "daily": range(7), "everyday": range(7),
    "будни": range(5), "рабочие": range(5), "weekdays": range(5),
    "выходные": range(5, 7), "weekend": range(5, 7),
}


def _day_index(name):
    if name in DAYS:
        return DAYS.index(name)
    if name in LATIN:
        return LATIN[name]
    return None

NOTE = [
    "Каждое правило — это «в такие-то дни, в случайное время окна, столько минут».",
    "окно: «21:00-22:00» — начало выпадает случайно внутри окна.",
    "окно можно задать одним временем — «21:00», тогда начало точное.",
    "минут: число или пара [20, 40] — тогда длительность тоже случайная.",
    "дни: «пн-пт», «пн,ср,сб», «каждый день», «будни», «выходные».",
    "вкл: false — правило лежит, но не срабатывает.",
    "Окно через полночь («23:00-01:00») считается внутри одних суток:",
    "сессия встанет либо поздно вечером, либо ночью того же дня.",
]


# ------------------------------------------------------------ разбор

def parse_days(text):
    """«пн-пт», «пн,сб», «будни» -> множество номеров дней (пн = 0)."""
    text = str(text).strip().lower()
    if text in ALIASES:
        return set(ALIASES[text])

    out = set()
    for part in text.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = (_day_index(x) for x in part.split("-", 1))
            if a is None or b is None:
                raise ValueError(f"не понял дни «{part}»")
            # Диапазон через воскресенье («сб-вт») тоже осмысленный.
            out |= {d % 7 for d in range(a, b + 1 if b >= a else b + 8)}
        else:
            index = _day_index(part)
            if index is None:
                raise ValueError(f"не понял день «{part}»")
            out.add(index)
    if not out:
        raise ValueError("дни не заданы")
    return out


def days_text(days):
    """Обратно в человеческий вид, для показа."""
    days = sorted(days)
    if len(days) == 7:
        return "каждый день"
    if days == list(range(5)):
        return "будни"
    if days == [5, 6]:
        return "выходные"
    return ",".join(DAYS[d] for d in days)


def parse_window(text):
    """«21:00-22:00» или «21:00» -> (начало, конец) как минуты от полуночи."""
    text = str(text).strip().replace(" ", "")
    parts = text.split("-", 1)
    times = []
    for part in parts:
        try:
            h, m = part.split(":")
            h, m = int(h), int(m)
        except ValueError:
            raise ValueError(f"не понял время «{part}», нужно ЧЧ:ММ") from None
        if not (0 <= h < 24 and 0 <= m < 60):
            raise ValueError(f"такого времени не бывает: «{part}»")
        times.append(h * 60 + m)
    return (times[0], times[0]) if len(times) == 1 else (times[0], times[1])


def parse_minutes(value):
    """Число, пара или «20-40» -> (мин, макс) в минутах.

    Строку с дефисом принимаем нарочно: именно так диапазон выглядит в окне и
    в подсказке к полю, а раньше он до файла не доходил — `float("20-40")`
    падал, и правило молча не добавлялось.
    """
    if isinstance(value, str) and "-" in value:
        value = value.replace(" ", "").split("-", 1)
    if isinstance(value, (list, tuple)):
        if len(value) != 2:
            raise ValueError("минут: нужно число или пара [от, до]")
        try:
            low, high = float(value[0]), float(value[1])
        except ValueError:
            raise ValueError(f"не понял минуты «{'-'.join(map(str, value))}»") from None
    else:
        try:
            low = high = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"не понял минуты «{value}»") from None
    if low <= 0 or high < low:
        raise ValueError("минут: должно быть больше нуля, и «от» не больше «до»")
    return low, high


# ------------------------------------------------------------ файл

def load():
    """Список правил. Файла нет или он битый — пустой план."""
    try:
        with open(PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    rules = data.get("сессии") if isinstance(data, dict) else data
    return [r for r in (rules or []) if isinstance(r, dict)]


def save(rules):
    with open(PATH, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"_как_пользоваться": NOTE, "сессии": rules},
                  f, ensure_ascii=False, indent=2)
    return PATH


def add(window, minutes, days="каждый день", app=None):
    """Добавить правило. Разбор строгий: кривое правило не должно осесть в файле."""
    parse_window(window)
    low, high = parse_minutes(minutes)
    parse_days(days)

    # В файл кладём разобранное значение, а не то, что набрали: там оно должно
    # выглядеть ровно так, как обещает «_как_пользоваться» — число или пара.
    minutes = low if low == high else [low, high]

    app = app or config.FEED_APPS[0]
    rules = load()
    rules.append({"окно": window, "минут": minutes, "дни": days,
                  "что": app, "вкл": True})
    save(rules)
    return rules


def remove(index):
    rules = load()
    if not 1 <= index <= len(rules):
        raise ValueError(f"нет правила №{index}, всего их {len(rules)}")
    dropped = rules.pop(index - 1)
    save(rules)
    return dropped


def enable(index, on=True):
    rules = load()
    if not 1 <= index <= len(rules):
        raise ValueError(f"нет правила №{index}, всего их {len(rules)}")
    rules[index - 1]["вкл"] = bool(on)
    save(rules)
    return rules[index - 1]


# ------------------------------------------------------------ план дня

def _today_rules(day, rules):
    """Правила, работающие в этот день: (начало, длина окна, мин, макс, лента)."""
    out = []
    for rule in rules:
        if not rule.get("вкл", True):
            continue
        try:
            start, end = parse_window(rule.get("окно", ""))
            low, high = parse_minutes(rule.get("минут", 0))
            days = parse_days(rule.get("дни", "каждый день"))
        except ValueError:
            continue                     # кривое правило просто не срабатывает
        if day.weekday() not in days:
            continue
        span = (end - start) % (24 * 60)
        out.append((start, span, low, high, rule.get("что") or config.FEED_APPS[0]))
    return out


def _overlapping(entries):
    """Правила с пересекающимися окнами — в одну кучку (по времени начала)."""
    groups = []
    for entry in sorted(entries, key=lambda e: e[0]):
        start, span = entry[0], entry[1]
        if groups and start <= groups[-1][1]:
            groups[-1][0].append(entry)
            groups[-1][1] = max(groups[-1][1], start + span)
        else:
            groups.append([[entry], start + span])
    return [g[0] for g in groups]


def _pieces(seconds):
    """Разрезать время одной ленты на 1-3 куска не короче PLAN_MIX_MIN_CHUNK."""
    least = config.PLAN_MIX_MIN_CHUNK_MIN * 60
    most = max(1, min(config.PLAN_MIX_MAX_CHUNKS, int(seconds // least)))
    n = random.randint(1, most)
    weights = [random.random() + 0.3 for _ in range(n)]
    rest = seconds - n * least
    return [least + rest * wt / sum(weights) for wt in weights]


def _interleave(parts):
    """Куски разных лент вперемешку, одна лента дважды подряд — только если
    других уже не осталось. Соседние куски одной ленты склеиваются."""
    pools = {}
    for app, seconds in parts:
        pools.setdefault(app, []).append(seconds)
    order, last = [], None
    while any(pools.values()):
        left = [a for a, p in pools.items() if p]
        pick = [a for a in left if a != last] or left
        app = random.choices(pick, weights=[len(pools[a]) for a in pick])[0]
        seconds = pools[app].pop(random.randrange(len(pools[app])))
        if order and order[-1][0] == app:
            order[-1] = (app, order[-1][1] + seconds)
        else:
            order.append((app, seconds))
        last = app
    return order


def _mix(day, group):
    """Окна разных лент пересекаются — один блок, ленты вперемешку.

    Просьба пользователя 2026-09-30: если в одном диапазоне стоят сессии в
    разных соцсетях, комбинировать их случайно. Каждая лента получает своё
    время из своего правила, режется на куски, и куски идут вперемешку, с
    короткой паузой на «переключился в другое приложение». Начало блока —
    случайное внутри общего окна, как у одиночного правила.
    """
    parts = []
    for _start, _span, low, high, app in group:
        parts += [(app, s) for s in _pieces(random.uniform(low, high) * 60)]
    order = _interleave(parts)

    gap_lo, gap_hi = config.PLAN_MIX_GAP_SEC
    total_min = (sum(s for _, s in order) + gap_hi * (len(order) - 1)) / 60
    first = min(e[0] for e in group)
    last_end = max(e[0] + e[1] for e in group)
    room = max(0.0, (last_end - first) - total_min)
    # Блок длиннее окна — начало всё равно не ровно в начале окна.
    at = first + (random.uniform(0, room) if room > 0
                  else random.uniform(0, min(10, last_end - first)))
    when = dt.datetime.combine(day, dt.time()) + dt.timedelta(minutes=at % (24 * 60))
    out = []
    for app, seconds in order:
        out.append((when, app, seconds))
        when += dt.timedelta(seconds=seconds + random.uniform(gap_lo, gap_hi))
    return out


def for_date(day=None, rules=None):
    """Сессии на дату: список (когда, приложение, секунд), по времени.

    Время начала разыгрывается заново на каждый день — в этом весь смысл
    окна. Окно через полночь укладывается в те же сутки: сессия встанет
    либо поздним вечером, либо ночью того же календарного дня.

    Правила РАЗНЫХ лент с пересекающимися окнами смешиваются в один блок
    (`_mix`, выключатель `PLAN_MIX`): «TikTok 21-23 на 40 минут» и «Reels
    21-23 на 30 минут» дают, например, Reels 12 мин -> TikTok 25 -> Reels 18
    -> TikTok 15, каждый день в новом порядке.
    """
    day = day or dt.date.today()
    entries = _today_rules(day, rules if rules is not None else load())
    out = []
    for group in _overlapping(entries):
        if config.PLAN_MIX and len({e[4] for e in group}) > 1:
            out += _mix(day, group)
            continue
        for start, span, low, high, app in group:
            at = (start + random.uniform(0, span)) % (24 * 60)
            when = dt.datetime.combine(day, dt.time()) + dt.timedelta(minutes=at)
            out.append((when, app, random.uniform(low, high) * 60))

    out.sort(key=lambda x: x[0])
    return out


def describe(day=None):
    """Правила и во что они складываются сегодня — для команды plan."""
    rules = load()
    lines = [f"файл: {PATH}"]
    if not rules:
        lines.append("правил нет — сессии идут по SESSION_TIMES из config.py")
        return "\n".join(lines)

    lines.append("")
    for i, rule in enumerate(rules, start=1):
        mark = " " if rule.get("вкл", True) else "×"
        try:
            low, high = parse_minutes(rule.get("минут", 0))
            length = (f"{low:g} мин" if low == high
                      else f"{low:g}-{high:g} мин")
            days = days_text(parse_days(rule.get("дни", "каждый день")))
            body = (f"{rule.get('окно', ''):<13} {length:<11} {days:<12} "
                    f"{rule.get('что', '')}")
        except ValueError as e:
            body = f"{rule.get('окно', '')} — ОШИБКА: {e}"
        lines.append(f" {mark}{i:2d}. {body}")

    day = day or dt.date.today()
    today = for_date(day, rules)
    lines += ["", f"на {day:%d.%m} ({days_text({day.weekday()})}) выпало:"]
    if not today:
        lines.append("  ничего — сегодня ни одно правило не работает")
    for when, app, seconds in today:
        lines.append(f"  {when:%H:%M} — {app}, {seconds / 60:.0f} мин")
    return "\n".join(lines)
