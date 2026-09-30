"""Живьём: «Главная» -> лента силами session._ensure_feed (Instagram или YouTube).

Только навигация по вкладкам: ни лайков, ни комментариев, ни публикаций
(аккаунт личный). В общий прогон не входит — нужен телефон.

    python scratchpad\\ig_live_feed.py [reels|shorts]
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import adb      # noqa: E402
import device   # noqa: E402
import prefs    # noqa: E402
import runlog   # noqa: E402
import session  # noqa: E402

prefs.apply()
with open(os.path.join(os.path.dirname(HERE), "recipes.json"), encoding="utf-8") as f:
    CFG = json.load(f)["_feed_apps"][sys.argv[1] if len(sys.argv) > 1 else "reels"]
ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


def where():
    t = time.time()
    got = session._off_feed(CFG)
    return got, time.time() - t


device.wake()
device.unlock()
time.sleep(1.5)
try:
    print(f"--- лента {CFG['title']} по ссылке ---")
    device.open_uri(CFG["open_uri"], CFG["package"])
    device.wait_for_app(CFG["package"], timeout=25)
    time.sleep(4)
    got, spent = where()
    say(got == "", f"в ленте проверка молчит ({spent:.1f} с на попытку дерева){'' if got == '' else ': ' + repr(got)}")

    print("\n--- ушёл на «Главную» (как бывает после «назад») ---")
    w, h = adb.screen_size()
    adb.tap(int(w * 0.10), int(h * (0.925 if "youtube" in CFG["package"] else 0.918)))
    time.sleep(4)
    got, spent = where()
    say(got in ("Дом", "Главная"), f"проверка видит «{got}» ({spent:.1f} с)")

    print("\n--- возврат ---")
    log = runlog.Log(live=False)
    t = time.time()
    back = session._ensure_feed(CFG, log)
    print("    журнал: " + " | ".join(log.text().splitlines()[-2:]))
    say(back, f"_ensure_feed вернул в ленту за {time.time() - t:.1f} с")
    time.sleep(2)
    got, _ = where()
    say(got == "", "и проверка это подтверждает")
finally:
    device.go_home()
    device.lock()
    print("\nтелефон заблокирован")
print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
