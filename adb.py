"""Тонкая обёртка над adb. Всё общение с телефоном идёт через этот модуль."""
import subprocess
import time

import config


class AdbError(RuntimeError):
    pass


# Какое устройство предпочитать, когда их несколько. Заполняется в `pin()`:
# телефон может быть виден и по USB, и по сети сразу, а adb в такой ситуации
# отказывается работать вовсе («more than one device»). Приоритет у кабеля.
_PREFERRED = None


def prefer(serial):
    global _PREFERRED
    _PREFERRED = serial


def _base():
    cmd = [config.ADB]
    serial = config.SERIAL or _PREFERRED
    if serial:
        cmd += ["-s", serial]
    return cmd


# Так adb отвечает, когда устройств несколько, а какое брать — не сказано.
MULTIPLE = "more than one device"


def raw(*args, timeout=60, check=True, binary=False, _healed=False):
    """Выполнить adb-команду. binary=True — вернуть байты (для screencap)."""
    try:
        p = subprocess.run(_base() + list(args), capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise AdbError(f"таймаут adb {' '.join(args)}") from e
    except FileNotFoundError as e:
        raise AdbError(
            f"adb.exe не найден (искал: {config.ADB}). "
            "Скачай platform-tools и укажи путь в переменной ADB_PATH."
        ) from e
    except OSError as e:
        # WinError 193 — по пути лежит папка или не-exe (частый случай:
        # в ADB_PATH указали каталог platform-tools вместо самого adb.exe).
        raise AdbError(
            f"не удалось запустить adb ({config.ADB}): {e}. "
            "В ADB_PATH должен быть путь к файлу adb.exe, а не к папке."
        ) from e

    if check and p.returncode != 0:
        err = p.stderr.decode("utf-8", "replace").strip()

        # «Устройств несколько, а какое — не сказано». Чинится на месте:
        # закрепляем телефон и повторяем команду один раз. Без этого одна
        # такая осечка отравляла ВСЮ сессию — телефон подключился уже после
        # старта процесса, закрепления не случилось, и каждая следующая
        # команда падала так же до самого конца сессии.
        if not _healed and MULTIPLE in err and pin():
            print(f"[adb] устройств несколько — закрепляю "
                  f"{config.SERIAL or _PREFERRED}", flush=True)
            return raw(*args, timeout=timeout, check=check, binary=binary,
                       _healed=True)

        raise AdbError(f"adb {' '.join(args)} -> {p.returncode}: {err}")

    return p.stdout if binary else p.stdout.decode("utf-8", "replace")


def quote(value):
    """Значение как один аргумент для shell на телефоне.

    Нужно всюду, где в команду подставляется имя файла: `rm -f /sdcard/мой
    ролик.mp4` телефон читает как три аргумента, файл остаётся лежать, а
    имя вида «x; rm -rf …» и вовсе выполнится как отдельная команда.
    Одинарные кавычки закрывают всё, кроме самой кавычки, — её склеиваем.
    """
    return "'" + str(value).replace("'", "'" + chr(92) + "''") + "'"


def shell(cmd, timeout=60, check=True):
    """adb shell '<cmd>'. Команда передаётся одной строкой."""
    return raw("shell", cmd, timeout=timeout, check=check)


def exec_out(cmd, timeout=60):
    """Как shell, но без порчи бинарных данных переводами строк."""
    return raw("exec-out", cmd, timeout=timeout, binary=True)


def devices():
    """[(serial, state), ...]"""
    out = raw("devices", check=False, timeout=30)
    res = []
    for line in out.splitlines()[1:]:
        if "\t" in line:
            serial, state = line.split("\t", 1)
            res.append((serial.strip(), state.strip()))
    return res


def connected():
    return any(state == "device" for _, state in devices())


def usb_devices():
    """Живые устройства на кабеле. Сетевые отличаются двоеточием в серийнике."""
    return [s for s, state in devices() if state == "device" and ":" not in s]


def pin():
    """Закрепить за процессом ОДИН конкретный телефон. True — есть за что.

    Закрепляем ВСЕГДА, даже когда телефон сейчас единственный. Раньше стояло
    «меньше двух — не трогаем», и это выходило боком: процесс, начавший
    работу с одним телефоном, оставался незакреплённым, а когда второй
    появлялся посреди сессии, каждый следующий вызов уходил без `-s` и adb
    отвечал «more than one device/emulator». Сессия при этом не падала — она
    доживала до конца, сыпля в журнал «сбой adb, продолжаю», то есть листала
    вслепую и ничего не делала.

    Приоритет у кабеля: он быстрее сети в разы и не зависит от туннеля.
    """
    alive = [s for s, state in devices() if state == "device"]
    if not alive:
        return False
    if config.SERIAL:
        return True                 # выбран явно — он главнее любых догадок
    if _PREFERRED in alive:
        return True                 # закреплённый на месте, не переигрываем
    # Закреплённый пропал (выдернули кабель, уснул) — выбираем заново.
    usb = [s for s in alive if ":" not in s]
    prefer(usb[0] if usb else alive[0])
    return True


def ensure(retries=3, delay=3):
    """Дождаться живого устройства, при необходимости передёрнуть сервер.

    Работаем по кабелю. Сеть — только запасной путь и только если он включён
    в config.REMOTE_ENABLED: тогда агент подтянет запомненный адрес, если
    телефона нет на USB.
    """
    for attempt in range(retries):
        if connected():
            pin()
            return True

        if config.REMOTE_ENABLED and not usb_devices():
            import remote

            address = remote.saved()
            if address:
                remote.connect(address, timeout=10)
                if connected():
                    pin()
                    return True

        if attempt:
            raw("kill-server", check=False, timeout=30)
            raw("start-server", check=False, timeout=30)
        time.sleep(delay)
    return connected()


def screen_size():
    """(ширина, высота) в пикселях, с учётом Override size."""
    out = shell("wm size")
    w = h = None
    for line in out.splitlines():
        if ":" in line:
            try:
                a, b = line.split(":")[1].strip().split("x")
                if "Override" in line or w is None:
                    w, h = int(a), int(b)
            except ValueError:
                continue
    if not w:
        raise AdbError(f"не разобрал wm size: {out!r}")
    return w, h


def current_app():
    """(package, activity) активного окна."""
    out = shell("dumpsys window")
    for line in out.splitlines():
        if "mCurrentFocus" in line or "mFocusedApp" in line:
            for token in line.replace("}", " ").split():
                if "/" in token and "." in token:
                    pkg, _, act = token.partition("/")
                    return pkg, act
    return None, None


def keyevent(name):
    shell(f"input keyevent {name}")


def tap(x, y):
    shell(f"input tap {int(x)} {int(y)}")


def swipe(x1, y1, x2, y2, ms=300):
    shell(f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(ms)}")


def screenshot(path):
    data = exec_out("screencap -p")
    with open(path, "wb") as f:
        f.write(data)
    return path
