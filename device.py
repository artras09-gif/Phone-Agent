"""Операции с телефоном: разблокировка, ввод текста, заливка файлов."""
import base64
import os
import posixpath
import re
import time

import adb
import config
import human

ADB_KEYBOARD = "com.android.adbkeyboard/.AdbIME"


# --------------------------------------------------------------- экран

def screen_state():
    """'unlocked' | 'locked' | 'off' | 'unknown'.

    Пробуем три источника подряд, потому что вывод dumpsys заметно
    отличается между версиями Android и оболочками производителей.
    """
    out = adb.shell("dumpsys nfc", check=False)
    m = re.search(r"mScreenState=(\S+)", out)
    if m:
        state = m.group(1).upper()
        if state.startswith("OFF"):
            return "off"
        return "unlocked" if "UNLOCKED" in state else "locked"

    out = adb.shell("dumpsys window", check=False)
    m = re.search(r"mDreamingLockscreen=(true|false)", out)
    if m:
        awake = "mAwake=true" in out or "mWakefulness=Awake" in adb.shell("dumpsys power", check=False)
        if not awake:
            return "off"
        return "locked" if m.group(1) == "true" else "unlocked"

    out = adb.shell("dumpsys power", check=False)
    if "mWakefulness=Asleep" in out or "mWakefulness=Dozing" in out:
        return "off"
    return "unknown"


def read_pin():
    if not os.path.exists(config.PIN_FILE):
        return None
    with open(config.PIN_FILE, encoding="utf-8") as f:
        pin = f.read().strip()
    return pin or None


def wake():
    if screen_state() == "off":
        adb.keyevent("KEYCODE_WAKEUP")
        human.pause(0.6, 1.2)


def ensure_awake(attempts=3):
    """Убедиться, что экран реально горит, а не «числится включённым».

    Проверяем не опросом состояния, а снимком: на MIUI `screen_state()`
    умеет вернуть «unlocked» при выключенном экране, и тогда агент работает
    вслепую по чёрным кадрам, а модель на них выдумывает содержимое.
    """
    import vision

    for _ in range(attempts):
        try:
            png = adb.exec_out("screencap -p", timeout=20)
        except adb.AdbError:
            return False
        if not vision.looks_blank(png):
            return True

        adb.keyevent("KEYCODE_WAKEUP")
        human.pause(0.6, 1.2)
        w, h = adb.screen_size()
        adb.swipe(w * 0.5, h * 0.80, w * 0.5, h * 0.25, 250)
        human.pause(0.8, 1.4)
    return False


def unlock():
    """Разбудить и разблокировать. PIN в модель/логи не попадает никогда."""
    wake()
    if screen_state() == "unlocked":
        return True

    w, h = adb.screen_size()
    adb.swipe(w * 0.5, h * 0.80, w * 0.5, h * 0.25, 250)
    human.pause(0.7, 1.3)

    if screen_state() == "unlocked":
        return True

    pin = read_pin()
    if not pin:
        return screen_state() == "unlocked"

    adb.shell(f"input text {pin}")
    human.pause(0.3, 0.7)
    adb.keyevent("KEYCODE_ENTER")
    human.pause(1.0, 1.8)
    return screen_state() == "unlocked"


def lock():
    if screen_state() != "off":
        adb.keyevent("KEYCODE_POWER")


def keep_awake(on=True):
    """Не давать экрану гаснуть во время работы. В конце обязательно вернуть."""
    value = config.SCREEN_TIMEOUT_MS if on else 60_000
    adb.shell(f"settings put system screen_off_timeout {value}", check=False)


def go_home():
    adb.keyevent("KEYCODE_HOME")
    human.pause(0.8, 1.6)


def back():
    adb.keyevent("KEYCODE_BACK")
    human.pause(0.5, 1.1)


def keyboard_shown():
    """Показана ли сейчас экранная клавиатура.

    Берём `mInputShown` системной службы. У самой IME рядом лежит
    `mIsInputViewShown`, но оно остаётся true и на экране без единого
    поля ввода — по нему решать нельзя.
    """
    out = adb.shell("dumpsys input_method", check=False)
    for line in out.splitlines():
        if "mInputShown=" in line:
            return "mInputShown=true" in line
    return False


def hide_keyboard():
    """Убрать клавиатуру, не трогая навигацию.

    Голое «назад» опасно: при спрятанной клавиатуре оно уходит на
    предыдущий экран. На публикации в TikTok это выбрасывало из формы
    описания обратно в редактор, и финальная кнопка потом не находилась.
    ADBKeyboard почти не рисует панель, так что прятать обычно нечего.
    """
    if not keyboard_shown():
        return False
    for _ in range(2):
        back()
        if not keyboard_shown():
            return True
    return False


# ---------------------------------------------------------- ввод текста

def has_adb_keyboard():
    """Готова ли клавиатура к работе, то есть включена в системе."""
    return "adbkeyboard" in adb.shell("ime list -s", check=False).lower()


def adb_keyboard_installed():
    """Установлен ли пакет — отдельный вопрос от «включён».

    MIUI не даёт ставить apk с ПК (`INSTALL_FAILED_USER_RESTRICTED`), поэтому
    человек ставит его руками через Проводник. Пакет после этого есть, а в
    списке клавиатур его нет: `ime enable` никто не вызывал.
    """
    out = adb.shell("pm list packages com.android.adbkeyboard", check=False)
    return "com.android.adbkeyboard" in out


def type_text(text):
    """Ввод текста. Кириллица и эмодзи — только через ADBKeyboard.

    Штатный `input text` умеет исключительно ASCII: русский текст
    он молча проглотит и не введёт ничего.
    """
    human.typing_delay()

    if has_adb_keyboard():
        previous = adb.shell("settings get secure default_input_method", check=False).strip()
        adb.shell(f"ime set {ADB_KEYBOARD}", check=False)
        human.pause(0.4, 0.9)
        payload = base64.b64encode(text.encode("utf-8")).decode("ascii")
        adb.shell(f"am broadcast -a ADB_INPUT_B64 --es msg {payload}", check=False)
        human.pause(0.5, 1.0)
        if previous and "adbkeyboard" not in previous.lower():
            adb.shell(f"ime set {previous}", check=False)
        return True

    if not text.isascii():
        raise RuntimeError(
            "Для не-ASCII текста нужен ADBKeyboard. Поставь его командой:\n"
            "  python main.py install-keyboard"
        )

    # input text не понимает пробелы и часть символов — экранируем. Перевод
    # строки убираем совсем: набрать его всё равно нельзя, а в команде он
    # разрывал её на две — вторая половина выполнилась бы как своя команда.
    safe = re.sub(r"\s+", " ", text)
    safe = safe.replace("%", "%%").replace(" ", "%s")
    for ch in "()<>|;&*\\~\"'`$":
        safe = safe.replace(ch, "\\" + ch)
    adb.shell(f"input text {safe}")
    return True


# --------------------------------------------------------------- файлы

def push_video(local_path):
    """Залить видео и заставить галерею его увидеть."""
    if not os.path.exists(local_path):
        raise FileNotFoundError(local_path)

    name = os.path.basename(local_path)
    remote = posixpath.join(config.REMOTE_DIR, name)

    adb.raw("push", local_path, remote, timeout=600)
    adb.shell(
        "am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE "
        f"-d {adb.quote('file://' + remote)}",
        check=False,
    )
    time.sleep(2.0)
    return remote


def remove_remote(remote_path):
    adb.shell(f"rm -f {adb.quote(remote_path)}", check=False)
    adb.shell(
        "am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE "
        f"-d {adb.quote('file://' + remote_path)}",
        check=False,
    )


def media_uri(remote_path):
    """Адрес файла в медиатеке (`content://`) или None.

    `file://` современные приложения молча игнорируют: Instagram по такой
    ссылке просто открывал главную ленту, будто видео и не присылали.

    Старая рассылка `MEDIA_SCANNER_SCAN_FILE` на Android 12 уже ничего не
    делает — файл в медиатеку не попадал вовсе. Рабочий способ: вызвать у
    медиатеки метод `scan_file`, он и сканирует, и сразу возвращает адрес.
    Свой запрос по `_data` не годится: том называется `external_primary`,
    а не `external`, и поиск промахивался.
    """
    # Кавычки обязательны: «мой ролик.mp4» без них приезжает на телефон
    # ДВУМЯ аргументами, скан промахивается, адрес не возвращается — и
    # публикация сваливается на `file://`, по которому TikTok и YouTube не
    # открываются вовсе. В журнале это выглядело как «приложение не
    # открылось», то есть на имя файла не указывало ничем.
    out = adb.shell(
        "content call --uri content://media/external --method scan_file "
        f"--arg {adb.quote(remote_path)}", check=False)
    match = re.search(r"(content://[^\s\]}]+)", out or "")
    return match.group(1) if match else None


def share_video(remote_path, package, caption="", component=None,
                use_content=True):
    """Открыть приложение сразу на экране публикации через системный Intent.

    Это пропускает запуск, кнопку «+» и выбор файла в галерее —
    четыре экрана превращаются в одну команду.

    `component` — точная активность приёма. Нужна там, где у приложения
    несколько точек входа: MIUI показывает свой выбор («Лента», «История»,
    «Директ»), и агент застревает на нём.

    `use_content` — каким адресом отдавать файл. Единого рабочего вида нет,
    проверено живьём: Instagram по `file://` открывает главную ленту и делает
    вид, что ничего не прислали, а YouTube по `content://` не открывается
    вовсе. Поэтому вызывающий код пробует второй вид, если первый не сработал.
    """
    uri = (media_uri(remote_path) if use_content else None) \
        or f"file://{remote_path}"
    cmd = (
        "am start -a android.intent.action.SEND "
        "-t video/* "
        # Тоже в кавычках: у `content://` пробелов не бывает, но запасной
        # `file://` — это путь, и в нём они бывают запросто.
        f"--eu android.intent.extra.STREAM {adb.quote(uri)} "
        "--grant-read-uri-permission "
        + (f"-n {component}" if component else f"-p {package}")
    )
    if caption:
        safe = caption.replace('"', '\\"')
        cmd += f' --es android.intent.extra.TEXT "{safe}"'
    adb.shell(cmd, check=False)
    human.pause(2.5, 4.0)


def open_uri(uri, package=None):
    """Открыть ссылку внутри приложения — прямой путь к нужной ленте.

    Надёжнее, чем искать кнопку в дереве: `instagram://reels_home` и
    `youtube.com/shorts` открывают ленту сразу, без промаха по вкладкам.
    Пакет указываем явно, чтобы система не спрашивала, чем открыть.
    """
    cmd = f'am start -a android.intent.action.VIEW -d "{uri}"'
    if package:
        cmd += f" -p {package}"
    adb.shell(cmd, check=False)
    human.pause(1.5, 2.5)


def open_app(package):
    adb.shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1", check=False)
    human.pause(2.0, 3.5)


def stop_app(package):
    adb.shell(f"am force-stop {package}", check=False)


def close_overlays(packages, keep=None):
    """Прибить соседние видеоприложения — они висят поверх «картинкой в картинке».

    Поймано на публикации: после сессии в Shorts YouTube остался плавающим
    окошком в правом нижнем углу и **перехватывал тапы** по кнопке «Далее».
    В логе это выглядело как «нажал, но экран не сменился», а на скриншоте
    было видно чужое окно поверх нашего.
    """
    for package in packages:
        if package and package != keep:
            adb.shell(f"am force-stop {package}", check=False)


def wait_for_app(package, timeout=25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        pkg, _ = adb.current_app()
        if pkg == package:
            return True
        time.sleep(1.0)
    return False
