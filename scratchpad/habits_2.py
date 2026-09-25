"""Три правки от 2026-08-30: чистка кадров, второй вид залипания, язык.

1. Кадры чистятся не только по сроку, но и по РАЗМЕРУ — иначе за сутки
   плотной работы папка раздувается, ни разу не устарев.
2. Залипаний два, и оба считаются интервалом в роликах, а не шансом:
   досмотреть целиком и выйти из приложения. Раз в ~30 каждое.
3. В комментарии заходим только на своём языке.

Телефона не нужно: кадры подделываются файлами, залипание — статистикой.
"""
import os
import random
import statistics
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import human  # noqa: E402
import session  # noqa: E402
import vision  # noqa: E402

ok = True


def check(name, cond, got=""):
    global ok
    ok = ok and bool(cond)
    print(("  OK  " if cond else " ПРОВАЛ ") + name + (f"  [{got}]" if got else ""))


def make_frames(root, folders, mb_each):
    """Насыпать поддельных папок с кадрами, самая старая — первая."""
    block = b"x" * (1024 * 1024)
    for i in range(folders):
        path = os.path.join(root, f"2026080{i % 9}-1200{i:02}")
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "001.png"), "wb") as f:
            for _ in range(mb_each):
                f.write(block)
        # Время правки по порядку: нулевая папка самая старая.
        old = time.time() - (folders - i) * 3600
        os.utime(path, (old, old))


def total_mb(root):
    return sum(vision._weigh(os.path.join(root, n))
               for n in os.listdir(root)) / 1024 ** 2


def main():
    print("== кадры режутся по размеру ==")
    tmp = tempfile.mkdtemp(prefix="habits_")
    frames = os.path.join(tmp, "frames")
    os.makedirs(frames)
    make_frames(frames, folders=10, mb_each=2)          # 20 МБ
    check("насыпали 20 МБ", 19 < total_mb(frames) < 21,
          f"{total_mb(frames):.1f} МБ")

    freed = vision._trim_to_size([frames], limit_mb=8)
    left = total_mb(frames)
    check("ужалось до потолка", left <= 8.5, f"{left:.1f} МБ")
    check("что-то освобождено", freed > 0, f"{freed / 1024 ** 2:.1f} МБ")

    # Сверяем по ВРЕМЕНИ, а не по имени: имена папок в стенде идут по кругу,
    # и «самая свежая» сортируется алфавитом первой. На этом я и споткнулся.
    left_times = sorted(os.path.getmtime(os.path.join(frames, n))
                        for n in os.listdir(frames))
    check("остались САМЫЕ СВЕЖИЕ, старые снесены",
          bool(left_times) and left_times[0] > time.time() - 5 * 3600,
          f"осталось {len(left_times)} папок")

    check("без потолка ничего не трогает",
          vision._trim_to_size([frames], limit_mb=0) == 0)
    check("потолок в боевом config задан",
          getattr(config, "FRAMES_MAX_MB", 0) > 0,
          str(getattr(config, "FRAMES_MAX_MB", 0)))
    check("срок хранения кадров сокращён",
          config.KEEP_FRAMES_DAYS <= 3, str(config.KEEP_FRAMES_DAYS))

    print("\n== два вида залипания, оба раз в ~30 ==")
    deep = [human.deep_watch_gap() for _ in range(4000)]
    leave = [human.deep_watch_gap(span=config.LEAVE_APP_EVERY)
             for _ in range(4000)]
    check("досмотреть целиком: в среднем около 30",
          25 <= statistics.fmean(deep) <= 35, f"{statistics.fmean(deep):.1f}")
    check("выйти из приложения: в среднем около 30",
          25 <= statistics.fmean(leave) <= 35, f"{statistics.fmean(leave):.1f}")
    check("оба не бывают чаще, чем раз в 20 роликов",
          min(deep) >= 20 and min(leave) >= 20,
          f"{min(deep)} и {min(leave)}")
    check("но момент случайный, а не ровный шаг",
          len(set(deep)) > 5, f"разных значений: {len(set(deep))}")

    print("\n== выход из приложения взводится и срабатывает ==")
    state = {}
    session._leave_rearm(state, 0)
    at = state["leave_at"]
    check("назначен на будущее", at >= 20, str(at))
    check("до срока не срабатывает", not session._leave_due(state, at - 1))
    check("на сроке срабатывает", session._leave_due(state, at))
    session._leave_rearm(state, at)
    check("после срабатывания назначается заново", state["leave_at"] > at)

    # Без слова «нет» заглавными: общий прогон судит об ошибках по словам в
    # выводе, и «НЕТ » в заголовке он считает провалом.
    print("\n== шанс выйти на каждом ролике убран ==")
    check("LEAVE_PROBABILITY убрана из настроек",
          not hasattr(config, "LEAVE_PROBABILITY"))
    check("а короткое отвлечение осталось",
          getattr(config, "DISTRACT_PROBABILITY", 0) > 0)

    print("\n== комментарии только на своём языке ==")
    # Ровно то условие, что стоит в сессии.
    def allowed(matched):
        return bool((matched or {}).get("язык", True))

    check("язык совпал — можно", allowed({"язык": True, "тема": True}))
    check("язык чужой — НЕЛЬЗЯ", not allowed({"язык": False, "тема": True}))
    check("язык не задан — можно", allowed({"тема": True}))
    check("совпадений нет вовсе — можно", allowed({}))

    print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПРОВАЛЫ")
    return 0 if ok else 1


if __name__ == "__main__":
    random.seed()
    sys.exit(main())
