"""Маршруты публикации во всех трёх сетях — БЕЗ публикации.

`config.DRY_RUN` включается принудительно и проверяется assert'ом: финальная
кнопка не нажимается ни при каких условиях. Смысл прогона — убедиться, что
все шаги маршрута до неё находятся: приложения обновляются, и селекторы
устаревают молча.

Ролик генерится ffmpeg'ом, кладётся на телефон и удаляется после.
"""
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config    # noqa: E402
import device    # noqa: E402
import poster    # noqa: E402
import prefs     # noqa: E402

prefs.apply()

FFMPEG = r"C:\Users\Admin\Desktop\Agent\whisper\ffmpeg.exe"


def make_video(path, seconds=4):
    if not os.path.exists(FFMPEG):
        return False
    cmd = [FFMPEG, "-y", "-f", "lavfi", "-i",
           f"testsrc=size=720x1280:rate=30:duration={seconds}",
           "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
           "-shortest", path]
    run = subprocess.run(cmd, capture_output=True)
    return run.returncode == 0 and os.path.getsize(path) > 50_000


def main(targets):
    # Предохранитель: боевое значение запоминаем и возвращаем, но на время
    # прогона финальная кнопка отключена жёстко.
    was = config.DRY_RUN
    config.DRY_RUN = True
    assert config.DRY_RUN is True

    tmp = tempfile.mkdtemp(prefix="post_dry_")
    # Имя НАРОЧНО с пробелом и кириллицей: ровно на таком 2026-08-30
    # маршруты TikTok и Shorts падали с «приложение не открылось» —
    # `content call --arg` получал путь без кавычек.
    name = os.environ.get("PROBE_NAME", "проверка маршрута.mp4")
    video = os.path.join(tmp, name)
    if not make_video(video):
        print("ffmpeg не собрал ролик — проверять нечего")
        return 1
    print(f"ролик: {os.path.getsize(video) // 1024} КБ, сухой прогон ВКЛЮЧЁН\n")

    recipes = poster.load_recipes()
    device.ensure_awake()
    device.keep_awake(True)
    results = {}

    for target in targets:
        print("=" * 60)
        print("==", target)
        try:
            report = poster.post(video, "проверка маршрута, публикации нет",
                                 target, recipes=recipes)
            # `post` возвращает пару (получилось, отчёт). Раньше я смотрел на
            # слово «сухой» в тексте — оно есть в ШАПКЕ отчёта всегда, даже
            # когда маршрут упал на первом шаге, и стенд врал «прошёл».
            ok, text = report if isinstance(report, tuple) else (None, str(report))
            print(text[-1200:])
            results[target] = bool(ok) if ok is not None else False
        except Exception as e:
            print(f"  СБОЙ: {type(e).__name__}: {e}")
            results[target] = False
        time.sleep(2.0)

    device.keep_awake(False)
    config.DRY_RUN = was

    print("\n" + "=" * 60)
    for target, ok in results.items():
        print(f"  {target:8} {'маршрут прошёл до кнопки' if ok else 'НЕ ДОШЁЛ'}")
    print(f"\nDRY_RUN возвращён в {config.DRY_RUN}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["tiktok", "shorts", "reels"]))
