"""Отчёт о работе алгоритма: что решал, как быстро, где ошибался.

Отдельный модуль, а не ещё одна команда в main.py: тут только чтение базы
и арифметика, к телефону обращений нет.
"""
import re
import time

import config
import interests
import jobs
import vision

CYRILLIC = re.compile(r"[а-яёА-ЯЁ]")
FOREIGN = re.compile(r"[一-鿿぀-ヿ]")   # иероглифы и кана


def _rows(days):
    since = time.time() - days * 86400
    # Только этот телефон: у каждого своя лента и свои вкусы, и общая сводка
    # по двум аккаунтам сразу — каша, из которой ничего не следует.
    mine, dev = jobs._mine()
    with jobs.connect() as con:
        return con.execute(
            "SELECT session, app, frame, tema, category, screen_text, ad, lang, "
            "       people, raw, at, verdict, skipped, caption, rules "
            "FROM content WHERE at >= ?" + mine + " ORDER BY at",
            (since,) + dev
        ).fetchall()


def _bar(share, width=24):
    return "#" * max(0, round(share * width))


def _sessions(rows):
    """Сгруппировать кадры по сессиям: (id, кадров, секунд, интервал)."""
    out = {}
    for r in rows:
        out.setdefault(r["session"], []).append(r["at"])
    result = []
    for session_id, times in out.items():
        times.sort()
        span = times[-1] - times[0]
        gap = span / (len(times) - 1) if len(times) > 1 else 0
        result.append((session_id, len(times), span, gap))
    return sorted(result)


def _time_on_video(rows):
    """Сколько ролик провисел на экране: до следующего кадра той же сессии.

    Точной метки «свайпнул» в базе нет, а промежуток между соседними
    кадрами — это ровно время просмотра плюс постоянные накладные расходы
    (разбор и свайп). Для сравнения ступеней между собой этого достаточно.
    Последний кадр сессии не в счёт: за ним следующего нет.
    """
    by_session = {}
    for r in rows:
        by_session.setdefault(r["session"], []).append(r)
    out = {}
    for group in by_session.values():
        group.sort(key=lambda r: r["at"])
        for cur, nxt in zip(group, group[1:]):
            gap = nxt["at"] - cur["at"]
            if 0 < gap < 120:      # пауза длиннее — это «отвлёкся», не просмотр
                out[id(cur)] = gap
    return out


def report(days=7):
    rows = _rows(days)
    if not rows:
        return [f"За {days} дн. разобранных кадров нет."]

    taste = interests.load()
    total = len(rows)
    lines = ["=" * 64,
             f"  ОТЧЁТ ПО АЛГОРИТМУ · {total} кадров за {days} дн.",
             "=" * 64]

    # ---------------------------------------------------------- сессии
    sessions = _sessions(rows)
    spans = [s for _, n, s, _ in sessions if n > 1]
    gaps = [g for _, n, _, g in sessions if n > 1]
    lines += ["", f"СЕССИИ: {len(sessions)}"]
    lines.append(f"  роликов за сессию: медиана {_median([n for _, n, _, _ in sessions]):.0f}, "
                 f"всего {total}")
    if gaps:
        lines.append(f"  секунд на ролик:   медиана {_median(gaps):.1f}, "
                     f"минимум {min(gaps):.1f}, максимум {max(gaps):.1f}")
    if spans:
        lines.append(f"  длина сессии:      медиана {_median(spans) / 60:.1f} мин")

    # -------------------------------------------------------- решения
    # Только вердикты нынешних правил: имена ступеней повторяются между
    # версиями, и без метки старые кадры молча испортили бы медианы.
    decided = [r for r in rows if r["verdict"] and r["rules"] == jobs.RULES]
    outdated = sum(1 for r in rows if r["verdict"] and r["rules"] != jobs.RULES)
    lines += ["", f"РЕШЕНИЯ: {len(decided)} из {total} кадров "
                  f"(правила «{jobs.RULES}»)",
              f"  тема: {taste['тема'] or 'не задана'}   "
              f"язык: {taste['язык'] or 'не проверяется'}"]
    if outdated:
        lines.append(f"  {outdated} кадров вынесены по прежним правилам "
                     f"и в счёт не идут")
    if decided:
        # Главная проверка трёх ступеней — не доля пропусков, а ВРЕМЯ:
        # интересное должно держаться дольше нейтрального, нейтральное —
        # дольше пролистанного. Время на ролик считается как промежуток
        # до следующего кадра внутри той же сессии.
        held = _time_on_video(rows)
        tiers = ("интересно", "нейтрально", "мимо", "реклама")
        lines.append(f"  {'вердикт':12} {'кадров':>7} {'доля':>6} "
                     f"{'пролистано':>11} {'секунд (медиана)':>18}")
        seen = {}
        for verdict in tiers:
            group = [r for r in decided if r["verdict"] == verdict]
            if not group:
                continue
            skipped = sum(1 for r in group if r["skipped"])
            secs = [held[id(r)] for r in group if id(r) in held]
            seen[verdict] = _median(secs) if secs else 0.0
            lines.append(f"  {verdict:12} {len(group):7d} "
                         f"{len(group) / len(decided):5.0%} "
                         f"{skipped / len(group):10.0%} "
                         f"{seen[verdict]:17.1f}")

        order = [v for v in tiers if v in seen]
        if len(order) > 1:
            times = [seen[v] for v in order]
            ok = all(a > b for a, b in zip(times, times[1:]))
            lines.append("  ступени различимы по времени: "
                         + ("да" if ok else "НЕТ — смотрит одинаково"))

    # ------------------------------------------------------ категории
    cats = {}
    for r in rows:
        cats[r["category"]] = cats.get(r["category"], 0) + 1
    lines += ["", "КАТЕГОРИИ:"]
    for name, count in sorted(cats.items(), key=lambda x: -x[1]):
        lines.append(f"  {name:12} {count:4d}  {count / total:4.0%}  {_bar(count / total)}")
    unknown = cats.get("другое", 0) / total
    lines.append(f"  доля «другое»: {unknown:.0%}"
                 + ("  — модель часто не понимает, что на кадре" if unknown > 0.35 else ""))

    # ------------------------------------------------- качество разбора
    problems = {
        "ответ не разобран": sum(1 for r in rows if r["raw"] and "категория вне списка" not in r["raw"]),
        "категория вне списка": sum(1 for r in rows if r["raw"] and "категория вне списка" in r["raw"]),
        "пустая тема": sum(1 for r in rows if not (r["tema"] or "").strip()),
        "не по-русски": sum(1 for r in rows if FOREIGN.search(r["tema"] or "")),
        "без единой кириллицы": sum(1 for r in rows
                                    if (r["tema"] or "").strip()
                                    and not CYRILLIC.search(r["tema"])),
        "пересказ примера": sum(1 for r in rows
                                if vision._same_as_example(r["tema"] or "")),
    }
    lines += ["", "КАЧЕСТВО РАЗБОРА:"]
    for name, count in problems.items():
        lines.append(f"  {name:24} {count:4d}  {count / total:4.0%}")
    clean = total - sum(problems.values())
    lines.append(f"  {'без замечаний':24} {clean:4d}  {clean / total:4.0%}")

    # --------------------------------------------------- пустые кадры
    import os

    blank = 0
    for r in rows:
        try:
            if os.path.getsize(r["frame"]) < config.BLANK_FRAME_KB * 1024:
                blank += 1
        except OSError:
            pass
    if blank:
        lines += ["", "ПУСТЫЕ КАДРЫ (погашенный экран):",
                  f"  {blank} ({blank / total:.0%}) — решения по ним недостоверны"]

    # ------------------------------------------------------- застревания
    stuck = _stuck_streaks(rows)
    lines += ["", "ЗАСТРЕВАНИЯ (тема повторяется подряд):"]
    if stuck:
        for session_id, length, theme in stuck:
            lines.append(f"  {session_id}  {length} кадров подряд: {theme[:40]}")
    else:
        lines.append("  не было")

    # ------------------------------------------------------------ прочее
    ads = sum(1 for r in rows if r["ad"])
    langs = {}
    for r in rows:
        if r["lang"]:
            langs[r["lang"]] = langs.get(r["lang"], 0) + 1
    with_caption = sum(1 for r in rows if (r["caption"] or "").strip())
    lines += ["", "ПРОЧЕЕ:",
              f"  реклама: {ads} ({ads / total:.0%})",
              f"  языки: " + ", ".join(f"{k} {v}" for k, v in
                                       sorted(langs.items(), key=lambda x: -x[1])[:4]),
              f"  подпись из дерева прочиталась: {with_caption} ({with_caption / total:.0%})",
              "=" * 64]
    return lines


def _median(values):
    values = sorted(values)
    if not values:
        return 0
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


def _stuck_streaks(rows, min_length=3, overlap=0.5):
    """Серии подряд идущих кадров с почти одинаковой темой."""
    found, streak, prev_session = [], [], None
    for r in rows:
        words = {w for w in (r["tema"] or "").lower().split() if len(w) > 3}
        same = (r["session"] == prev_session and streak and words and streak[-1]
                and len(words & streak[-1]) / max(len(words), 1) >= overlap)
        if same:
            streak.append(words)
        else:
            if len(streak) >= min_length:
                found.append((prev_session, len(streak), " ".join(sorted(streak[0]))))
            streak = [words]
            prev_session = r["session"]
    if len(streak) >= min_length:
        found.append((prev_session, len(streak), " ".join(sorted(streak[0]))))
    return found
