"""Установка зависимостей на новом ПК: python setup.py

Ставит то, что pip поставить не может: ADBKeyboard на телефон и (по желанию)
LM Studio для зрения. adb и scrcpy уже лежат в комплекте, их ставить некуда.

Ничего не делает молча: сначала показывает, что уже в порядке, а что нет,
и спрашивает перед каждой установкой. Запускать можно сколько угодно раз —
уже сделанные шаги пропускаются.
"""
import os
import shutil
import subprocess
import sys
import urllib.request

import adb
import config

ADB_KEYBOARD_URL = "https://github.com/senzhk/ADBKeyBoard/raw/master/ADBKeyboard.apk"
ADB_KEYBOARD_IME = "com.android.adbkeyboard/.AdbIME"
WINGET_LMSTUDIO = "ElementLabs.LMStudio"

OK, WARN, FAIL = "OK ", "[i]", "[!]"


def say(mark, text):
    print(f"{mark} {text}")


ASSUME_YES = "--yes" in sys.argv or "-y" in sys.argv


def assume_yes(on=True):
    """Отвечать «да» на все вопросы. Для запуска из скриптов и из меню."""
    global ASSUME_YES
    ASSUME_YES = on


def ask(question):
    """Спросить да/нет. Enter = да, потому что это установщик.

    С флагом --yes ничего не спрашивает: нужно для запуска из скриптов
    и там, где консоли нет вовсе (тогда input сразу упирается в EOF).
    """
    if ASSUME_YES:
        print(f"    {question} [Д/н]: да (--yes)")
        return True
    try:
        answer = input(f"    {question} [Д/н]: ").strip().lower()
    except EOFError:
        print("\n    (ввод недоступен — считаю, что нет; запусти с --yes)")
        return False
    return answer in ("", "д", "да", "y", "yes")


def winget_install(package, title):
    if not shutil.which("winget"):
        say(FAIL, f"{title}: winget не найден, поставь вручную")
        return False
    say(WARN, f"ставлю {title} через winget, это займёт пару минут...")
    code = subprocess.call([
        "winget", "install", "-e", "--id", package,
        "--accept-package-agreements", "--accept-source-agreements",
    ])
    if code == 0:
        say(OK, f"{title} установлен")
        return True
    say(FAIL, f"{title}: winget вернул код {code}")
    return False


# ------------------------------------------------------------------ шаги

def step_python():
    version = sys.version_info
    if version >= (3, 10):
        say(OK, f"Python {version.major}.{version.minor}.{version.micro}")
    else:
        say(FAIL, f"Python {version.major}.{version.minor} слишком старый, нужен 3.10+")
        return False

    missing = []
    for module in ("sqlite3", "tkinter"):
        try:
            __import__(module)
        except ImportError:
            missing.append(module)
    if missing:
        say(FAIL, f"в этой сборке Python нет: {', '.join(missing)}. "
                  "Поставь обычный установщик с python.org, не embeddable")
        return False
    say(OK, "sqlite3 и tkinter на месте")
    return True


def step_tools():
    ok = True
    if os.path.isfile(config.ADB):
        say(OK, f"adb: {config.ADB}")
    else:
        say(FAIL, f"adb не найден (искал {config.ADB}). "
                  "Папка tools должна лежать рядом с проектом")
        ok = False

    import mirror
    scrcpy = mirror.find_scrcpy()
    if scrcpy:
        say(OK, f"scrcpy: {scrcpy}")
    else:
        say(WARN, "scrcpy не найден — трансляции экрана не будет, агент работает")
    return ok


def step_phone():
    devices = adb.devices()
    if not devices:
        say(FAIL, "телефон не виден")
        print("    1. Воткни кабель")
        print("    2. Настройки -> О телефоне -> 7 тапов по «Номер сборки»")
        print("    3. Для разработчиков -> включить «Отладка по USB»")
        print("    4. Подтверди отпечаток в диалоге на экране телефона")
        if config.REMOTE_ENABLED:
            print("    Либо по сети: python main.py remote")
        return False
    for serial, state in devices:
        if state == "device":
            say(OK, f"телефон {serial}: {adb.shell('getprop ro.product.model').strip()}")
            return True
        say(FAIL, f"телефон {serial}: {state}"
                  + (" — подтверди доступ в диалоге на телефоне"
                     if state == "unauthorized" else ""))
    return False


def step_remote():
    """Запасное подключение по сети. По умолчанию выключено: работаем кабелем."""
    import remote

    if not config.REMOTE_ENABLED:
        say(OK, "работаем по кабелю (сеть выключена в config.REMOTE_ENABLED)")
        return True

    address = remote.saved()
    if not address:
        say(WARN, "адрес телефона в туннеле не задан")
        print("    Телефон и этот ПК должны быть в одной сети туннеля")
        print("    (ZeroTier, Tailscale, свой WireGuard — агенту всё равно).")
        print("    Дальше: python main.py remote set <адрес телефона>")
        print("    Первое подключение: python main.py remote pair <адрес>:<порт> <код>")
        return False

    if address in remote.wireless_devices():
        say(OK, f"телефон подключён по сети: {address}")
        return True

    say(WARN, f"адрес известен ({address}), пробую подключиться...")
    ok, out = remote.connect(address)
    if ok:
        say(OK, f"подключился: {address}")
        return True

    say(FAIL, f"не отвечает: {out.strip()[:120]}")
    print("    Проверь, что телефон в туннеле и что после его перезагрузки")
    print("    выполнена команда: python main.py remote resume <порт>")
    print("    Если это новый ПК — на телефоне появится запрос «Разрешить")
    print("    отладку?», его надо подтвердить: ключ у каждого ПК свой.")
    return False


def step_keyboard(phone_ready):
    """ADBKeyboard: без него не вводится русский текст в описания."""
    if not phone_ready:
        say(WARN, "ADBKeyboard: пропускаю, телефон не подключён")
        return False

    import device
    if device.has_adb_keyboard():
        say(OK, "ADBKeyboard уже установлен")
        return True

    # Частый случай: apk поставили руками на самом телефоне (с ПК MIUI не даёт).
    # Пакет есть, но система о клавиатуре не знает, пока её не включишь. Без
    # этой ветки команда снова уходила в adb install и упиралась в тот же запрет.
    if device.adb_keyboard_installed():
        adb.shell(f"ime enable {ADB_KEYBOARD_IME}", check=False)
        if device.has_adb_keyboard():
            say(OK, "ADBKeyboard уже стоял на телефоне — включил его")
            return True
        say(FAIL, "пакет есть, но включить клавиатуру не вышло")
        print("    Включи вручную: Настройки -> Язык и ввод -> Управление клавиатурами")
        return False

    say(WARN, "ADBKeyboard не установлен — русские описания вводиться не будут")
    if not ask("скачать и поставить сейчас?"):
        return False

    apk = os.path.join(config.BASE, "ADBKeyboard.apk")
    try:
        if not os.path.exists(apk):
            say(WARN, "качаю ADBKeyboard.apk...")
            # С таймаутом: без него установка молча висит на неотвечающем
            # зеркале, и человек не понимает, идёт что-то или нет.
            with urllib.request.urlopen(ADB_KEYBOARD_URL, timeout=60) as src,                     open(apk, "wb") as out:
                shutil.copyfileobj(src, out)
    except Exception as e:
        say(FAIL, f"не смог скачать: {str(e)[:120]}")
        return False

    say(WARN, "ставлю на телефон (подтверди установку на экране, если спросит)...")
    try:
        # check=True: причина отказа приходит в stderr, а он виден только
        # в тексте исключения. Без этого «Install canceled by user» теряется.
        adb.raw("install", "-r", apk, timeout=180)
    except adb.AdbError as e:
        text = str(e)
        if "USER_RESTRICTED" in text or "canceled by user" in text:
            say(FAIL, "телефон запретил установку по USB")
            # Запрет касается только установки, инициированной с ПК. Тот же
            # apk, открытый на самом телефоне, ставится как обычное
            # приложение — это и есть рабочий обходной путь, а не «либо».
            remote = "/sdcard/Download/ADBKeyboard.apk"
            try:
                adb.raw("push", apk, remote, timeout=120)
                say(OK, f"apk положен на телефон: {remote}")
            except adb.AdbError:
                print(f"    apk лежит на компьютере: {apk}")
            print("    Поставь его НА ТЕЛЕФОНЕ, с ПК это запрещено оболочкой:")
            print("      Проводник (Файлы) -> Download -> ADBKeyboard.apk -> Установить")
            print("      Разреши установку из этого источника, если спросит.")
            print("    Потом здесь: python main.py install-keyboard")
            print("    Через «Установка через USB» в настройках разработчика")
            print("    тоже можно, но на Xiaomi для неё нужны Mi-аккаунт и SIM.")
        else:
            say(FAIL, f"adb install: {text[:200]}")
        return False

    adb.shell(f"ime enable {ADB_KEYBOARD_IME}", check=False)
    say(OK, "ADBKeyboard установлен и включён")
    return True


def step_vision():
    """LM Studio — только для зрения, без него агент работает."""
    import vision

    ready, info = vision.available()
    if ready:
        say(OK, f"зрение готово, модель: {info}")
        return True

    say(WARN, f"зрение не готово: {info}")
    print("    Зрение нужно для команд vision/analyze/content и для решений")
    print("    «смотреть или листать» по теме ролика. Без него агент работает.")

    if not ask("настроить зрение?"):
        return False

    if not shutil.which("lms") and not os.path.exists(
            os.path.expanduser(r"~\.lmstudio\bin\lms.exe")):
        if not winget_install(WINGET_LMSTUDIO, "LM Studio"):
            return False

    print()
    print("    Дальше руками, это один раз:")
    print("      1. Открой LM Studio")
    print("      2. Найди и скачай модель Qwen2.5-VL-3B-Instruct-GGUF (~3 ГБ)")
    print("      3. Загрузи её кнопкой Load")
    print("      4. Developer -> Start Server (порт 1234)")
    print("      5. Проверь: python main.py vision")
    return False


def main():
    print("=" * 60)
    print("  PhoneAgent — установка зависимостей")
    print("=" * 60)

    print("\n--- Python ---")
    if not step_python():
        return 1

    print("\n--- adb и scrcpy ---")
    tools_ok = step_tools()

    print("\n--- подключение по сети ---")
    step_remote()

    print("\n--- телефон ---")
    phone_ok = step_phone() if tools_ok else False

    print("\n--- ADBKeyboard (русский текст) ---")
    step_keyboard(phone_ok)

    print("\n--- зрение (опционально) ---")
    step_vision()

    print("\n" + "=" * 60)
    if phone_ok:
        print("  Готово. Проверка целиком: python main.py doctor")
        print("  Первая сессия:            python main.py session tiktok --duration 120")
    else:
        print("  Телефон не подключён — вернись к этому шагу и запусти setup.py снова.")
    print("=" * 60)
    return 0 if phone_ok else 1


if __name__ == "__main__":
    sys.exit(main())
