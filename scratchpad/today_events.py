"""Что агент делал сегодня — по журналам сессий в базе."""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import prefs   # noqa: E402

prefs.apply()

MARKS = ("истори", "Подел", "подел", "ОК", "Пропустить", "Закрыть",
         "выбрался", "выход", "escape", "не помогает", "застря", "окно")


def main():
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    rows = list(con.execute(
        "select id, datetime(at, 'unixepoch', 'localtime') as when_, kind, payload as text "
        "from events order by id desc limit 40"))
    print(f"записей в журнале: {len(rows)}\n")
    for r in rows:
        text = r["text"] or ""
        hits = [line.strip() for line in text.splitlines()
                if any(m in line for m in MARKS)]
        print(f"--- #{r['id']} {r['when_']} [{r['kind']}] строк {len(text.splitlines())}")
        for h in hits[:12]:
            print("     ", h[:120])
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

