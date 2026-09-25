"""Живая проверка сохранения ссылок: сессия с ВРЕМЕННЫМИ вкусами.

Зачем временные: боевая тема — «политика», а лента сейчас про еду и котов,
поэтому настоящих лайков не будет вовсе, а ссылка сохраняется только ПОСЛЕ
лайка. Подменяем тему на то, что в ленте есть, и ставим лайк на каждое
совпадение — тогда ветка сохранения ссылки отработает по-настоящему.

Боевой interests.json НЕ трогаем: путь уводится во временный файл, а его
неизменность проверяется по хешу до и после (однажды я его уже затирал).
"""
import hashlib
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import jobs
import prefs
import runlog

prefs.apply()

LIVE = config.INTERESTS


def digest(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def main():
    args = sys.argv[1:]
    app = next((a for a in args if not a.isdigit()), "tiktok")
    seconds = next((int(a) for a in args if a.isdigit()), 150)
    before_hash = digest(LIVE)

    tmp = tempfile.mkdtemp(prefix="link_demo_")
    taste = os.path.join(tmp, "interests.json")
    with open(taste, "w", encoding="utf-8") as f:
        json.dump({
            "тема": "кулинария, еда, коты, животные",
            "язык": "ru",
            "поведение": {
                "секунд_на_взгляд": [1.5, 3.0],
                "секунд_на_интересное": [6.0, 9.0],
                "секунд_на_тему_без_языка": [3.0, 4.0],
                "секунд_на_язык_без_темы": [1.5, 3.0],
                "секунд_на_неинтересное": [0.0, 0.0],
                # Лайк на каждое совпадение: иначе ветка ссылки за две минуты
                # может не сработать ни разу, и проверять будет нечего.
                "лайк": 1.0,
            },
        }, f, ensure_ascii=False, indent=2)
    config.INTERESTS = taste
    assert config.INTERESTS != LIVE

    was = {r["url"] for r in jobs.links(limit=500)}
    print(f"ссылок в базе до прогона: {len(was)}")
    print(f"порог: от {config.LINK_MIN_LIKES:,} лайков".replace(",", " "))
    print(f"лента: {app}")
    print(f"сохранение ссылок: {'включено' if config.LINK_SAVE else 'ВЫКЛЮЧЕНО'}\n")

    import session

    config.LIVE_LOG = True
    runlog.set_sink(None)
    report = session.browse(app, seconds)
    if not config.LIVE_LOG:
        print(report)

    print("\n--- что легло в подборку ---")
    fresh = [r for r in jobs.links(limit=500) if r["url"] not in was]
    if not fresh:
        print("ничего: ни один лайкнутый ролик не набрал порога")
    for r in fresh:
        print(f"  ♥ {int(r['likes']):,}".replace(",", " "),
              "|", r["app"], "|", r["author"] or "?", "|", r["url"])

    after_hash = digest(LIVE)
    print("\nбоевой interests.json не тронут:", before_hash == after_hash)
    return 0 if before_hash == after_hash else 1


if __name__ == "__main__":
    sys.exit(main())
