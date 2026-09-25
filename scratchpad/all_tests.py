"""Прогнать все проверки, которым не нужен телефон, — сколько угодно раз.

    python scratchpad\all_tests.py          один круг
    python scratchpad\all_tests.py 8        восемь кругов подряд

Круги нужны не для красоты: половина этого проекта — случайные величины
(разброс расписания, свайпы, моменты лайка, розыгрыш сессий). Проверка,
прошедшая один раз, могла просто попасть в удачный розыгрыш. Здесь считается
и то, сколько раз каждая проверка сошлась из скольких.

Тесты, которым нужен живой телефон (link_live, link_debug, adb_multi,
fleet_*), сюда не берутся: их место — на столе с воткнутым кабелем.
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)

# Каждый прогон — отдельный процесс: тесты подменяют друг другу модули
# (session.ui.dump, jobs.add_link), и в одном процессе они бы перемешались.
TESTS = [
    ("selftest.py", "логика без телефона: очередь, расписание, разбор"),
    ("scratchpad/links_digest.py", "подборка ссылок в Telegram"),
    ("scratchpad/link_save.py", "ссылки в TikTok: все ветки отказа"),
    ("scratchpad/link_points.py", "ссылки в Shorts и Reels по точкам"),
    ("scratchpad/links_all_nets.py", "ссылки во всех трёх сетях: селекторы"),
    ("scratchpad/quote_paths.py", "путь к видео уходит на телефон в кавычках"),
    ("scratchpad/never_publish.py", "НИЧЕГО не отправляет и не публикует само"),
    ("scratchpad/keys_ui.py", "ключи и адреса вписываются мышкой в окне"),
    ("scratchpad/key_only.py", "вставил один ключ — и всё работает"),
    ("scratchpad/liked_counter.py", "счётчик лайков у УЖЕ лайкнутого ролика"),
    ("scratchpad/bot_keyboard.py", "кнопки бота: ссылки и постинг"),
    ("scratchpad/bot_open.py", "бот открыт всем: ответ уходит написавшему"),
    ("scratchpad/link_probe_html.py", "разметка калибровки точек"),
    ("scratchpad/deep_watch.py", "залипание: досмотреть ролик целиком"),
    ("scratchpad/decide_frame.py", "решение по живому кадру, а не из потока"),
    ("scratchpad/habits_2.py", "чистка кадров, второй вид залипания, язык"),
    ("scratchpad/human_realism.py", "человеческие распределения жестов"),
    ("scratchpad/comments_daypart.py", "заход в комментарии и время суток"),
    ("scratchpad/links_incoming.py", "приём ссылок в чате: сеть, чистка, повторы"),
    ("scratchpad/vision_models.py", "список моделей: только те, что видят картинки"),
    ("scratchpad/escape_loop.py", "выход из тупика: выбор кнопки и запреты"),
    ("scratchpad/gate_test.py", "замки между процессами"),
    ("scratchpad/fleet_isolation.py", "разделение телефонов по пространствам"),
]

BAD_WORDS = ("НЕТ ", "ПЛОХО", "ЕСТЬ ОШИБКИ", "Traceback", "не сходится")


def run_one(path):
    """Вернуть (сошлось, вывод). Судим по коду возврата И по словам в выводе:
    часть тестов печатает «НЕТ», но выходит нулём."""
    started = time.time()
    try:
        res = subprocess.run([sys.executable, path], cwd=BASE, timeout=300,
                             capture_output=True, text=True,
                             encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return False, "не уложился в 5 минут", 300.0
    out = (res.stdout or "") + (res.stderr or "")
    good = res.returncode == 0 and not any(w in out for w in BAD_WORDS)
    return good, out, time.time() - started


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    score = {name: 0 for name, _ in TESTS}
    failures = []

    for circle in range(1, rounds + 1):
        print(f"\n=== круг {circle} из {rounds} " + "=" * 40)
        for name, about in TESTS:
            path = os.path.join(BASE, name.replace("/", os.sep))
            if not os.path.exists(path):
                print(f"  [нет файла] {name}")
                continue
            good, out, spent = run_one(path)
            score[name] += bool(good)
            print(f"  [{'ок ' if good else 'СБОЙ'}] {name:34} {spent:5.1f}с  {about}")
            if not good:
                tail = "\n".join(out.strip().splitlines()[-12:])
                failures.append((circle, name, tail))

    print("\n" + "=" * 60)
    print(f"ИТОГ за {rounds} " + ("круг" if rounds == 1 else "круга/кругов"))
    for name, _ in TESTS:
        got = score[name]
        mark = "ок " if got == rounds else "СБОЙ"
        print(f"  [{mark}] {name:34} {got}/{rounds}")

    if failures:
        print(f"\nСБОИ ({len(failures)}):")
        for circle, name, tail in failures[:5]:
            print(f"\n--- круг {circle}, {name}\n{tail}")
        return 1

    print("\nВСЕ ПРОВЕРКИ СОШЛИСЬ ВО ВСЕХ КРУГАХ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
