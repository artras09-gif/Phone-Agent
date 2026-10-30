"""Публикация ждёт, пока видео догрузится (2026-10-17: «в ютубе проблема в том,
что он не дожидается, пока загрузится видос, и рано закрывает приложение, и в
других сетях то же самое»).

Раньше сразу после «Опубликовать» файл удалялся с телефона, а следующая сеть
начиналась с принудительного закрытия соседних приложений — загрузка
обрывалась. Телефон подменён: счётчик отправленных байт, уведомление с
прогрессом, экран — свои на каждый сценарий; часы — поддельные.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import abort    # noqa: E402
import config   # noqa: E402
import device   # noqa: E402
import poster   # noqa: E402
import runlog   # noqa: E402

ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


clock = [1000.0]
poster.time.time = lambda: clock[0]
abort.sleep = lambda s, step=0.2: clock.__setitem__(0, clock[0] + s)
abort.clear()
video = os.path.join(tempfile.mkdtemp(), "v.mp4")
open(video, "wb").write(b"\0" * (40 * 1024 * 1024))          # 40 МБ
MB = 1024 * 1024


def scenario(tx_rates=None, progress=None, screen=None, counters=True):
    """Прогнать ожидание: скорость отправки по секундам от старта."""
    start = clock[0]
    total = [0]

    def tx():
        if not counters:
            return None
        t = clock[0] - start
        rate = tx_rates(t) if tx_rates else 0
        total[0] += rate * config.UPLOAD_POLL_SEC
        return 10 ** 9 + int(total[0])

    device.tx_bytes = tx
    device.upload_progress = lambda pkg: progress(clock[0] - start) if progress else None
    poster._upload_on_screen = lambda pkg: screen(clock[0] - start) if screen else False
    log = runlog.Log("x", live=False)
    done = poster.wait_upload("com.app", video, log)
    return done, clock[0] - start, log.text()


print("--- YouTube: грузит 2 минуты на 400 КБ/с, потом тишина ---")
done, took, text = scenario(lambda t: 400 * 1024 if t < 120 else 2 * 1024)
say(done and 145 <= took <= 170, f"дождался конца загрузки: {took:.0f} с")
say("ушло" in text, text.splitlines()[-1].strip())

print("\n--- TikTok: 40 с пережимает (сеть молчит), потом грузит ---")
done, took, _ = scenario(lambda t: 0 if t < 40 else (500 * 1024 if t < 100 else 0))
say(done and took >= 125, f"тишину во время пережатия концом не счёл: {took:.0f} с")

print("\n--- сеть молчит, но в уведомлении прогресс ---")
done, took, _ = scenario(lambda t: 12 * 1024, progress=lambda t: (int(t), 100) if t < 200 else None)
say(done and took >= 225, f"пока в уведомлении проценты — ждёт: {took:.0f} с")

print("\n--- на экране «Публикация… 45 %» ---")
done, took, _ = scenario(lambda t: 12 * 1024, screen=lambda t: t < 150)
say(done and took >= 175, f"пока на экране прогресс — ждёт: {took:.0f} с")

print("\n--- счётчиков сети нет (закрыты прошивкой) ---")
done, took, _ = scenario(counters=False)
say(done and took >= 160, f"ждёт хотя бы по объёму (40 МБ ≈ 160 с): {took:.0f} с")

print("\n--- загрузка бесконечная ---")
done, took, text = scenario(lambda t: 300 * 1024)
say(not done and took >= config.UPLOAD_WAIT_MAX and took < config.UPLOAD_WAIT_MAX + 10,
    f"сдался через {took / 60:.0f} мин и сказал: {text.splitlines()[-1].strip()[:50]}")

print("\n--- живьём 2026-10-20: YouTube за 2 мин отправил 0,1 МБ — это не конец ---")
open(video, "wb").write(b"\0" * (100 * 1024 * 1024))          # 100 МБ
real_wait = poster.wait_upload


def run(rates, target, since_offset=0):
    start = clock[0]
    total = [0]

    def tx():
        t_ = clock[0] - start
        total[0] += rates(t_) * config.UPLOAD_POLL_SEC
        return 10 ** 9 + int(total[0])

    device.tx_bytes = tx
    device.upload_progress = lambda pkg: None
    poster._upload_on_screen = lambda pkg: False
    log = runlog.Log("x", live=False)
    done = real_wait("com.app", video, log, since=10 ** 9 - since_offset, target=target)
    return done, clock[0] - start, log.text()


done, took, text = run(lambda t: 1000 if t < 100 else 0, "shorts")
say(not done and took >= config.UPLOAD_WAIT_MAX and "не ушло" in text,
    f"не объявил «закончилась» на 0,1 МБ: ждал {took / 60:.0f} мин и предупредил")
done, took, _ = run(lambda t: 0, "reels", since_offset=30 * 1024 * 1024)
say(done and took < 120, f"Instagram загрузил заранее, пока набиралась подпись, — засчитано: {took:.0f} с")
done, took, _ = run(lambda t: 2 * 1024 * 1024 if t < 50 else 0, "shorts")
say(done and took < 150, f"YouTube отправил исходник — конец по тишине: {took:.0f} с")
open(video, "wb").write(b"\0" * (40 * 1024 * 1024))

print("\n--- остановили кнопкой ---")
device.tx_bytes = lambda: 10 ** 9
abort.request()
log = runlog.Log("x", live=False)
say(poster.wait_upload("com.app", video, log) is False and "остановили" in log.text(),
    "«Остановить» — не ждёт")
abort.clear()

print("\n--- в публикации: файл удаляется только после загрузки ---")
removed, stopped = [], []
for name, fn in {"unlock": lambda: True, "keep_awake": lambda on=True: None,
                 "close_overlays": lambda *a, **k: None,
                 "push_video": lambda path: "/sdcard/Movies/v.mp4",
                 "refresh_media": lambda remote: None,
                 "share_video": lambda *a, **k: None, "wait_for_app": lambda pkg, timeout=25: True,
                 "stop_app": lambda pkg: stopped.append(pkg),
                 "remove_remote": lambda path: removed.append(path),
                 "cleanup_pushed": lambda keep_sec=None: 0}.items():
    setattr(device, name, fn)
config.QUEUE_DIR = os.path.join(tempfile.mkdtemp(), "queue")
os.makedirs(config.QUEUE_DIR)
recipe = {"demo": {"package": "com.app", "title": "demo", "steps": [{"op": "sleep", "min": 0, "max": 0}]}}
poster.run_step = lambda step, ctx, log_path, i: "шаг"
config.DRY_RUN = False
order = []
poster.wait_upload = lambda pkg, path, log, **kw: order.append(("ждал", list(removed))) or True
good, report = poster.post(video, "x", "demo", recipes=recipe)
say(good and order == [("ждал", [])] and removed == ["/sdcard/Movies/v.mp4"],
    "сперва дождался загрузки, файл удалил потом")
say(not stopped, "приложение после публикации не закрывали")
removed.clear()
poster.wait_upload = lambda pkg, path, log, **kw: False
poster.post(video, "x", "demo", recipes=recipe)
pushed = open(os.path.join(os.path.dirname(config.QUEUE_DIR), "pushed.json"), encoding="utf-8").read()
say(not removed and "/sdcard/Movies/v.mp4" in pushed,
    "не дождался — файл оставлен и записан, его уберут через несколько часов")
config.DRY_RUN = True
waited = []
poster.wait_upload = lambda pkg, path, log, **kw: waited.append(1) or True
poster.post(video, "x", "demo", recipes=recipe)
say(not waited and stopped, "сухой прогон — не ждёт загрузки (её нет), приложение закрыто")
config.DRY_RUN = False

print("\n--- уборка своих старых файлов, чужие не трогаем ---")
import importlib  # noqa: E402
importlib.reload(device)
gone = []
device.remove_remote = lambda path: gone.append(path)
device.remember_pushed("/sdcard/Movies/old.mp4")
device.remember_pushed("/sdcard/Movies/fresh.mp4")
import json  # noqa: E402
data = json.load(open(device._pushed_path(), encoding="utf-8"))
data["/sdcard/Movies/old.mp4"] -= 4 * 3600
json.dump(data, open(device._pushed_path(), "w", encoding="utf-8"))
say(device.cleanup_pushed() == 1 and gone == ["/sdcard/Movies/old.mp4"],
    "убран только свой файл старше 3 часов; свежий (может ещё грузиться) — нет")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
