"""«По теме» и «лента» прямо в разборе кадра — без второго запроса. Без сети.

Замер 2026-10-01 на живых кадрах: «по теме» внутри разбора совпал с
отдельным судьёй 29 раз из 30, а экономит целый запрос на каждом ролике.
Проверяем:
  * облаку вопросы задаются, своей модели — нет;
  * мусорный ответ на них выбрасывается, и тогда работает старый путь;
  * с готовым «по теме» отдельный судья не зовётся вовсе;
  * «не лента» дважды подряд копится в сессии как повод проверить дерево.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config     # noqa: E402
import interests  # noqa: E402
import session    # noqa: E402
import ui         # noqa: E402
import vision     # noqa: E402

ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


prompts, answers = [], []


def fake_ask(png, prompt, **kw):
    prompts.append((prompt, kw))
    return answers.pop(0)


vision.ask = fake_ask

print("--- облако: вопросы в том же запросе ---")
vision.provider = lambda: vision.API
answers[:] = ['{"тема": "депутат выступает", "категория": "новости", "реклама": false, '
              '"язык": "ru", "по_теме": true, "лента": true}']
data = vision.describe_frame(b"png", topics="политика, история")
prompt = prompts[-1][0]
say("по_теме" in prompt and "политика, история" in prompt and "лента:" in prompt,
    "в вопросе есть темы пользователя и «лента»")
say('"по_теме": false, "лента": true}' in prompt, "пример ответа показывает новые поля")
say(data.get("по_теме") is True and data.get("лента") is True, "ответы разобраны")
say(prompts[-1][1].get("max_tokens", 0) >= 150, "запаса токенов хватает на лишние поля")

answers[:] = ['{"тема": "кот", "категория": "животные", "реклама": false, "язык": "ru", '
              '"по_теме": "да", "лента": "false"}']
data = vision.describe_frame(b"png", topics="животные")
say(data.get("по_теме") is True and data.get("лента") is False, "«да»/«false» строкой — тоже поняты")

answers[:] = ['{"тема": "кот", "категория": "животные", "реклама": false, "язык": "ru", '
              '"по_теме": "может быть", "лента": null}']
data = vision.describe_frame(b"png", topics="животные")
say("по_теме" not in data and "лента" not in data, "мусорный ответ выброшен — работает старый путь")

answers[:] = ['{"тема": "кот", "категория": "животные", "реклама": false, "язык": "ru", "лента": true}']
data = vision.describe_frame(b"png")
say("по_теме" not in prompts[-1][0] and data.get("лента") is True,
    "тем не задано — про тему не спрашиваем, про ленту спрашиваем")

print("\n--- своя модель: как раньше ---")
vision.provider = lambda: vision.LOCAL
answers[:] = ['{"тема": "кот", "категория": "животные", "реклама": false, "язык": "ru", '
              '"по_теме": true, "лента": true}']
data = vision.describe_frame(b"png", topics="животные")
say("по_теме" not in prompts[-1][0] and "по_теме" not in data and "лента" not in data,
    "3B не спрашиваем — и её выдумку не берём")
config.FRAME_QUESTIONS = False
vision.provider = lambda: vision.API
answers[:] = ['{"тема": "кот", "категория": "животные", "реклама": false, "язык": "ru"}']
vision.describe_frame(b"png", topics="животные")
say("по_теме" not in prompts[-1][0], "выключатель FRAME_QUESTIONS работает")
config.FRAME_QUESTIONS = True

print("\n--- решение: судья не нужен ---")
judged = []


def judge(theme, topic):
    judged.append(topic)
    return True


taste = {"тема": "политика, история", "язык": "ru"}
verdict, why, matched = interests.classify(
    {"тема": "кот", "язык": "en", "по_теме": False}, taste, expansions={}, judge=judge)
say(verdict == "мимо" and not judged, f"«мимо» без второго запроса: {why}")
verdict, why, matched = interests.classify(
    {"тема": "депутат", "язык": "ru", "по_теме": True}, taste, expansions={}, judge=judge)
say(verdict == "интересно" and not judged, f"«интересно» без второго запроса: {why}")
verdict, why, matched = interests.classify(
    {"тема": "депутат", "язык": "ru"}, taste, expansions={}, judge=judge)
say(judged, "ответа «по теме» нет — судья спрошен, как раньше")

print("\n--- фоновая проверка дерева ---")
seen = {}
ui.dump = lambda retries=1, tolerant=True, timeout=30: seen.update(timeout=timeout) or []
session._off_feed({"feed_no_tree": True}, timeout=session.FEED_PROBE_TIMEOUT)
say(seen.get("timeout", 0) >= 6, f"дерево ждём с запасом: {seen.get('timeout')} с "
                                  "(«Главная» Instagram снимается до 4.2 с)")
src = open(os.path.join(os.path.dirname(HERE), "session.py"), encoding="utf-8").read()
say("_PROBES.submit(_off_feed" in src and 'state.get("not_feed_streak", 0) >= 2' in src,
    "в сессии проверка уходит в фон, «не лента» дважды — повод проверить раньше")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
