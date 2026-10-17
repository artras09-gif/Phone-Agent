"""Статистика для Telegram: коротко после каждой сессии и итог дня.

Просьба 2026-10-16: в группе с темами «в третий чат постил бы статистику».
Не журнал сессии — его человек уже назвал «этой хуйнёй», когда он приходил в
бот (2026-10-16), — а цифры: сколько смотрел, лайкнул, сохранил, опубликовал.

Только чтение базы: сессия пишет итог строкой «итог: N видео, M лайков, …»
в `events`, посты лежат в `jobs`, ссылки — в `links`, темы роликов — в
`content`.
"""
import datetime as dt
import re
import time

import jobs

TITLES = {"tiktok": "TikTok", "reels": "Instagram", "shorts": "YouTube",
          "instagram": "Instagram", "youtube": "YouTube"}

RE_APP = re.compile(r"сессия в (\w+)")
RE_TOTAL = re.compile(r"итог:\s*(\d+)\s*видео,\s*(\d+)\s*лайков")
RE_LINKS = re.compile(r"(\d+)\s*ссылок сохранено")
RE_SKIPPED = re.compile(r"(\d+)\s*пролистано не по вкусу")
RE_TOOK = re.compile(r"длилась:\s*(\d+)\s*мин")
RE_COMMENTS = re.compile(r"заглянул в комментарии")


def _num(rx, text):
    m = rx.search(text or "")
    return int(m.group(1)) if m else 0


def parse(report):
    """Итог сессии из её отчёта: {app, videos, likes, links, skipped, minutes, comments}."""
    text = str(report or "")
    total = RE_TOTAL.search(text)
    m = RE_APP.search(text)
    app = m.group(1) if m else ""
    return {
        "app": app,
        "videos": int(total.group(1)) if total else 0,
        "likes": int(total.group(2)) if total else 0,
        "links": _num(RE_LINKS, text),
        "skipped": _num(RE_SKIPPED, text),
        "minutes": _num(RE_TOOK, text),
        "comments": len(RE_COMMENTS.findall(text)),
        "finished": bool(total),
    }


def _plural(n, one, few, many):
    n = abs(int(n)) % 100
    if 11 <= n <= 19:
        return many
    n %= 10
    return one if n == 1 else few if 2 <= n <= 4 else many


def _minutes(m):
    m = int(round(m))
    return f"{m // 60} ч {m % 60} мин" if m >= 60 else f"{m} мин"


def session_text(report, minutes=None):
    """Одна строка-две после сессии. Пусто — сессии по сути не было."""
    s = parse(report)
    if minutes is not None:
        s["minutes"] = round(minutes)
    title = TITLES.get(s["app"], s["app"] or "лента")
    if not s["finished"]:
        why = [ln.strip() for ln in str(report or "").splitlines() if ln.strip()]
        return f"📊 {title}: сессия не состоялась — {why[-1][:120] if why else 'без причины'}"
    head = f"📊 Сессия {title}" + (f" · {_minutes(s['minutes'])}" if s["minutes"] else "")
    parts = [f"роликов {s['videos']}", f"лайков {s['likes']}"]
    if s["links"]:
        parts.append(f"ссылок {s['links']}")
    if s["comments"]:
        parts.append(f"комментарии читал {s['comments']} раз")
    if s["skipped"]:
        parts.append(f"пролистано не по вкусу {s['skipped']}")
    return head + "\n" + " · ".join(parts)


def _day_bounds(day):
    start = time.mktime(dt.datetime.combine(day, dt.time()).timetuple())
    return start, start + 86400


def day_text(day=None):
    """Итог дня: аналитика аккаунтов (как в YouTube Studio) и работа агента."""
    import accstats

    accounts = accstats.report_text()
    agent = agent_text(day)
    return f"{accounts}\n\n{agent}" if accounts else agent


def agent_text(day=None):
    """Работа агента за день: сессии, ролики, лайки, ссылки, посты, о чём лента."""
    day = day or dt.date.today()
    start, end = _day_bounds(day)
    mine, dev = jobs._mine()
    with jobs.connect() as con:
        events = con.execute(
            "SELECT payload FROM events WHERE kind='session' AND at>=? AND at<?" + mine,
            (start, end) + dev).fetchall()
        posts = con.execute(
            "SELECT status, targets FROM jobs WHERE status IN ('done','failed') "
            "AND done_at>=? AND done_at<?" + mine, (start, end) + dev).fetchall()
        links = con.execute(
            "SELECT COUNT(*) c FROM links WHERE at>=? AND at<? AND session<>'телеграм'"
            + mine, (start, end) + dev).fetchone()["c"]
        sent_in = con.execute(
            "SELECT COUNT(*) c FROM links WHERE at>=? AND at<? AND session='телеграм'"
            + mine, (start, end) + dev).fetchone()["c"]
        cats = con.execute(
            "SELECT category, COUNT(*) c FROM content WHERE at>=? AND at<? "
            "AND category<>'' AND category<>'не разобрано'" + mine
            + " GROUP BY category ORDER BY c DESC LIMIT 4", (start, end) + dev).fetchall()
        seen = con.execute(
            "SELECT COUNT(*) c FROM content WHERE at>=? AND at<? AND category<>'' "
            "AND category<>'не разобрано'" + mine, (start, end) + dev).fetchone()["c"]

    sessions = [parse(e["payload"]) for e in events]
    done = [s for s in sessions if s["finished"]]
    lines = [f"📊 Работа агента — {day:%d.%m}"]
    if done:
        by_app = {}
        for s in done:
            name = TITLES.get(s["app"], s["app"] or "лента")
            by_app[name] = by_app.get(name, 0) + 1
        minutes = sum(s["minutes"] for s in done)
        apps = ", ".join(f"{k} {v}" for k, v in sorted(by_app.items(), key=lambda x: -x[1]))
        lines.append(f"Сессий: {len(done)}" + (f" · {_minutes(minutes)}" if minutes else "")
                     + f" ({apps})")
        videos = sum(s["videos"] for s in done)
        likes = sum(s["likes"] for s in done)
        lines.append(f"Роликов: {videos} · лайков: {likes} · ссылок сохранено: {links}")
    else:
        lines.append("Сессий не было.")
    failed = len(sessions) - len(done)
    if failed:
        lines.append(f"Не состоялось сессий: {failed}")

    ok = [p for p in posts if p["status"] == "done"]
    bad = [p for p in posts if p["status"] == "failed"]
    if ok or bad:
        nets = {}
        for p in ok:
            for t in (p["targets"] or "").split(","):
                if t:
                    nets[TITLES.get(t, t)] = nets.get(TITLES.get(t, t), 0) + 1
        where = ", ".join(f"{k} {v}" for k, v in nets.items())
        line = f"Постов: {len(ok)}" + (f" ({where})" if where else "")
        if bad:
            line += f" · не удалось: {len(bad)}"
        lines.append(line)
    else:
        lines.append("Постов не было.")
    if sent_in:
        lines.append(f"Ссылок прислали в чат: {sent_in}")
    if cats and seen:
        top = ", ".join(f"{r['category']} {r['c'] / seen:.0%}" for r in cats)
        lines.append(f"В ленте чаще всего: {top}")
    waiting = jobs.links_waiting()
    if waiting:
        lines.append(f"Ссылок ждёт подборки: {waiting}")
    return "\n".join(lines)
