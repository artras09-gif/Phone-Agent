# -*- coding: utf-8 -*-
"""Ежедневная пробная публикация — раннее предупреждение о поломках.

Раньше о том, что приложение обновилось и маршрут публикации сломался,
программа узнавала, только когда НАСТОЯЩИЙ пост не выходил. Теперь раз в
день, в тихое утреннее время, она проходит маршрут каждой сети до
финальной кнопки и НЕ нажимает её (сухой прогон, `config.DRY_RUN` только на
время проверки): пробный ролик, подпись «проверка маршрута», экран поста —
и приложение закрывается, ничего не сохранив.

Что получилось — в бот владельцу, только если есть о чём сказать:
  * маршрут сломался, но путь нашла модель (`postnav`) — значит, приложение
    поменялось, а программа уже приспособилась;
  * маршрут не прошёл — что именно, чтобы починить до настоящего поста.
Всё прошло как обычно — молчим: сообщение каждое утро стало бы шумом.

Порядок важнее проверки: идёт, только когда телефон свободен (сторож службы
— `webui.Dispatcher`, как ежедневная статистика) и по плану ничего не ждёт.
"""
import datetime as dt
import os
import re
import subprocess
import time

import abort
import config
import jobs

NAME = "route_check.mp4"
CAPTION = "проверка маршрута"


def _today_tries(now):
    start = time.mktime(now.date().timetuple())
    with jobs.connect() as con:
        return con.execute("SELECT COUNT(*) c FROM events WHERE kind='route_check' AND at>=?"
                           + jobs._mine()[0], (start,) + jobs._mine()[1]).fetchone()["c"]


def due(now=None):
    """Пора ли проверять: включено, есть бот (куда сказать), утреннее окно
    `ROUTE_CHECK_AT`–`ROUTE_CHECK_UNTIL`, сегодня ещё не пробовали."""
    if not config.ROUTE_CHECK:
        return False
    now = now or dt.datetime.now()
    start = dt.datetime.combine(now.date(), dt.time(*map(int, config.ROUTE_CHECK_AT.split(":"))))
    until = dt.datetime.combine(now.date(), dt.time(*map(int, config.ROUTE_CHECK_UNTIL.split(":"))))
    if not start <= now < until:
        return False
    try:
        import telegram_bot
        if not telegram_bot.load_settings()["token"]:
            return False
    except Exception:
        return False
    return _today_tries(now) == 0


def probe_clip():
    """Пробный ролик: 4,2 с цветной заставки со звуком (у чёрного кадра не было
    бы превью, и Instagram ждал бы его 20 с — `poster._wait_thumb`)."""
    path = os.path.join(os.path.dirname(config.QUEUE_DIR), "analytics", NAME)
    if os.path.exists(path) and os.path.getsize(path) > 10_000:
        return path
    import linkfetch
    ffmpeg = linkfetch.ffmpeg_tools()[0]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=s=1080x1920:r=30:d=4.2",
                    "-f", "lavfi", "-i", "sine=f=440:d=4.2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path],
                   check=True, timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return path


def targets():
    """Сети, в которые публикуем с этого телефона: маршрут есть и приложение
    стоит."""
    import accstats
    import poster
    recipes = poster.load_recipes()
    out = []
    for name, recipe in recipes.items():
        if not isinstance(recipe, dict) or not recipe.get("steps") or name.startswith("_"):
            continue
        try:
            if accstats._installed(recipe["package"]):
                out.append(name)
        except Exception:
            pass
    return out


def verdict(target, ok, report):
    """Строка про одну сеть: (что сказать, есть ли о чём сказать)."""
    import poster
    title = poster.load_recipes().get(target, {}).get("title", target)
    helped = [line.strip() for line in report.splitlines() if "путь:" in line and
              ("нашёлся" in line or "запомненному" in line or "жму по точке" in line)]
    if ok and not helped:
        return f"✓ {title}", False
    if ok:
        return f"✓ {title}: приложение поменялось, путь нашла модель и запомнила", True
    errors = re.findall(r"ОШИБКА: (.*)", report)
    why = (errors[-1] if errors else "маршрут не прошёл").split(" Дерево экрана")[0][:200]
    return f"✗ {title}: {why}", True


def run(log=None, send=None):
    """Пройти маршруты всех сетей без финальной кнопки. Возвращает текст."""
    import device
    import poster

    jobs.log_event("route_check", "попытка")
    say = log.append if log is not None else (lambda s: None)
    try:
        video = probe_clip()
    except Exception as e:
        say(f"пробная публикация: не сделал пробный ролик — {e}")
        return ""
    lines, worth = [], False
    was = config.DRY_RUN
    config.DRY_RUN = True                      # финальная кнопка НЕ нажимается
    try:
        for target in targets():
            if abort.requested():
                say("пробная публикация: остановлено")
                break
            try:
                ok, report = poster.post(video, CAPTION, target)
            except Exception as e:
                ok, report = False, f"ОШИБКА: {type(e).__name__}: {e}"
            line, matters = verdict(target, ok, report)
            lines.append(line)
            worth |= matters
            say(f"  {line}")
    finally:
        config.DRY_RUN = was
        try:
            device.go_home()
            device.lock()
        except Exception:
            pass
    text = ("🔎 Утренняя пробная публикация (без отправки):\n" + "\n".join(lines)) if lines else ""
    jobs.log_event("route_check", text or "сетей нет")
    if worth and text:
        try:
            if send is None:
                import telegram_bot
                send = telegram_bot.send
            send(text)
        except Exception:
            pass
    return text
