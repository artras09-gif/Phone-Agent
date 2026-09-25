"""Бот открыт всем: каждый получает ответ В СВОЙ чат, неспрошенное — владельцу.

Сети нет: подменяется `_call`, поэтому проверяются именно ветки адресации,
а не Telegram. Боевой `telegram.json` не трогаем — путь уводится во временную
папку (однажды я уже затирал живые настройки).
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import jobs
import telegram_bot as tg

SENT = []          # (кому, текст)


def fake_call(method, params=None, timeout=70):
    if method == "sendMessage":
        SENT.append((str(params["chat_id"]), params["text"]))
        return {"ok": True}
    return {"ok": True, "result": {"username": "my_test_bot"}}


def msg(chat, text):
    return {"chat": {"id": chat}, "text": text}


def main():
    tmp = tempfile.mkdtemp(prefix="bot_open_")
    live = config.DB_PATH
    config.BASE = tmp                      # telegram.json уедет сюда
    # Именно `config.DB_PATH`: `jobs` берёт путь оттуда при каждом connect().
    # Промахнулся тут один раз — тестовая ссылка легла в БОЕВУЮ базу.
    config.DB_PATH = os.path.join(tmp, "jobs.db")
    assert config.DB_PATH != live
    config.BOT_OPEN = True
    tg._call = fake_call
    tg.save_settings("111:AAA", "")        # владельца ещё нет

    ok = True

    def check(name, cond):
        nonlocal ok
        ok = ok and cond
        print(("  OK  " if cond else " ПРОВАЛ ") + name)

    # 1. Первый написавший запоминается как адрес неспрошенного.
    tg._handle(msg(1001, "/status"))
    check("первый чат стал адресом для неспрошенного",
          tg.load_settings()["owner"] == "1001")
    check("ответ ушёл ему же", SENT and SENT[-1][0] == "1001")

    # 2. ЧУЖОЙ получает ответ, и в СВОЙ чат, а не владельцу.
    SENT.clear()
    tg.set_reply(None)
    tg._handle(msg(2002, "/status"))
    check("чужому ответили", bool(SENT))
    check("ответ ушёл в его чат, не владельцу",
          bool(SENT) and SENT[-1][0] == "2002")

    # 3. Ссылка от чужого сохраняется, и отчёт о ней — тоже ему.
    SENT.clear()
    tg.set_reply(None)
    tg._handle(msg(2002, "https://www.tiktok.com/@x/video/7300000000000000000"))
    check("ссылка от чужого сохранена", jobs.links_waiting() == 1)
    check("отчёт о ссылке ушёл ему", bool(SENT) and SENT[-1][0] == "2002")

    # 4. Неспрошенное (подборка по часам) уходит владельцу: чата нет.
    SENT.clear()
    tg.set_reply(None)
    tg.digest(force=True)
    check("вечерняя подборка ушла владельцу",
          bool(SENT) and all(w == "1001" for w, _ in SENT))

    # 5. Отчёт о длинной работе возвращается в чат заказчика ИЗ ДРУГОГО потока.
    SENT.clear()
    tg.set_reply(None)
    got = []

    def submit(title, work):
        import threading
        t = threading.Thread(target=work)
        t.start()
        t.join()

    tg.set_submit(submit)
    tg.set_reply("2002")
    tg._run("проверка", lambda: got.append(tg.send("готово")))
    tg.set_reply(None)
    tg.set_submit(None)
    check("отчёт из чужого потока ушёл заказчику",
          bool(SENT) and SENT[-1][0] == "2002")

    # 6. Закрытый режим по-прежнему работает: чужой молчком игнорируется.
    SENT.clear()
    tg.set_reply(None)
    config.BOT_OPEN = False
    tg._handle(msg(3003, "/status"))
    check("при BOT_OPEN=False чужой игнорируется", not SENT)
    config.BOT_OPEN = True

    print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПРОВАЛЫ")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
