"""Подборка ссылок в Telegram — без телефона и без сети.

Telegram подменяется: `_call` записывает отправленное в список. Проверяем
то, из-за чего эта штука может тихо испортиться:

* новое уходит один раз и помечается отправленным;
* оборванная отправка НЕ помечает — иначе список пропал бы молча;
* `/links 20` показывает архив и ничего не помечает;
* длинная подборка режется по целым ссылкам, а не посреди адреса;
* порог `LINKS_DIGEST_MIN` не пускает подборку из одной ссылки, а `/links`
  отдаёт её же по требованию.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="links-"), "test.db")

import jobs
import telegram_bot as tg

SENT = []
FAIL = {"on": False}


def fake_call(method, params=None, timeout=70):
    if method != "sendMessage":
        return {"ok": True, "result": {}}
    if FAIL["on"]:
        return {"ok": False, "error": "связь оборвалась"}
    SENT.append(params["text"])
    return {"ok": True, "result": {}}


tg._call = fake_call
tg.load_settings = lambda: {"token": "тест", "owner": "1"}


def add(url, likes, author="автор", caption="описание"):
    return jobs.add_link(url, "tiktok", likes, author=author, caption=caption)


def check(name, got, want):
    mark = "ок " if got == want else "ПЛОХО"
    print(f"  [{mark}] {name}: {got!r}" + ("" if got == want else f" != {want!r}"))
    return got == want


def main():
    ok = True
    print(f"база: {config.DB_PATH}\n")

    # Адреса сверяются БЕЗ косой черты на конце: `jobs.add_link` её
    # срезает при чистке (иначе одна и та же ссылка с телефона и из
    # чата считалась бы двумя). Проверять надо то, что в базе.
    print("1. новое уходит один раз")
    for i in range(3):
        add(f"https://www.tiktok.com/t/ZT{i}/", 100_000 + i)
    SENT.clear()
    said = tg.digest()
    ok &= check("отчёт", bool(said), True)
    ok &= check("сообщений", len(SENT), 1)
    ok &= check("все три ссылки внутри",
                all(f"ZT{i}" in SENT[0] for i in range(3)), True)
    ok &= check("осталось ждать", jobs.links_waiting(), 0)
    SENT.clear()
    ok &= check("второй раз молчит", tg.digest(), "")
    ok &= check("сообщений", len(SENT), 0)

    print("\n2. оборванная отправка не помечает")
    add("https://www.tiktok.com/t/BREAK/", 200_000)
    FAIL["on"] = True
    ok &= check("отчёт пуст", tg.digest(), "")
    FAIL["on"] = False
    ok &= check("ссылка осталась в очереди", jobs.links_waiting(), 1)
    SENT.clear()
    ok &= check("после связи ушла", bool(tg.digest()), True)
    ok &= check("очередь пуста", jobs.links_waiting(), 0)

    print("\n3. /links 20 — архив, без отметок")
    SENT.clear()
    tg._cmd_links("/links 20")
    ok &= check("сообщений", len(SENT), 1)
    ok &= check("это архив", "Последнее понравившееся" in SENT[0], True)
    ok &= check("очередь не тронута", jobs.links_waiting(), 0)

    print("\n4. длинная подборка режется по целым ссылкам")
    for i in range(80):
        add(f"https://www.tiktok.com/t/LONG{i:03}/", 500_000 + i,
            caption="описание подлиннее, чтобы набрать объём " * 2)
    SENT.clear()
    tg.digest()
    ok &= check("сообщений больше одного", len(SENT) > 1, True)
    ok &= check("длина каждого в пределах", max(len(x) for x in SENT) <= 3600, True)
    whole = "\n".join(SENT)
    ok &= check("все адреса целы",
                all(f"LONG{i:03}" in whole for i in range(80)), True)
    ok &= check("ни один адрес не разорван",
                all(part.count("https://") == part.count("tiktok.com/t/")
                    for part in SENT), True)

    print("\n5. порог LINKS_DIGEST_MIN")
    config.LINKS_DIGEST_MIN = 3
    add("https://www.tiktok.com/t/ONE/", 700_000)
    SENT.clear()
    ok &= check("одна ссылка ждёт", tg.digest(), "")
    ok &= check("сообщений", len(SENT), 0)
    tg._cmd_links("/links")
    ok &= check("по команде пришла", len(SENT), 1)
    ok &= check("и помечена", jobs.links_waiting(), 0)
    config.LINKS_DIGEST_MIN = 1

    print("\n6. кривые команды не роняют бота")
    add("https://www.tiktok.com/t/EDGE/", 900_000)
    for cmd in ("/links ", "/links@бот", "/links сколько-нибудь", "ссылки",
                "/links 0", "/links 999999", "/links все"):
        SENT.clear()
        try:
            tg._cmd_links(cmd)
            said = bool(SENT)
        except Exception as e:
            said = f"УПАЛО: {type(e).__name__}: {e}"
        ok &= check(f"{cmd!r} отвечает", said, True)

    print("\n7. когда ссылок нет вовсе")
    SENT.clear()
    tg._cmd_links("/links")
    ok &= check("ответ есть", len(SENT), 1)
    ok &= check("объясняет, откуда они берутся",
                "лайк" in SENT[0].lower() or "Новых ссылок нет" in SENT[0], True)

    print("\n8. вид подборки")
    print("  " + "\n  ".join(SENT[-1].splitlines()[:6]))

    print("\nВСЁ ХОРОШО" if ok else "\nЕСТЬ ОШИБКИ")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
