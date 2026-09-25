"""Пространство телефона: у каждого своё, и они не смешиваются.

Когда телефон один, разделять нечего — так программа и была устроена. Но
телефонов бывает несколько, и это разные аккаунты: своя очередь видео, своё
расписание, свои вкусы, своя сводка. Складывать их в общую кучу нельзя —
получится каша, в которой не понять, кому что показывали и что куда ушло.

Поэтому у каждого телефона своя папка `devices/<серийник>/` с `plan.json`,
`interests.json`, `watch/`, `queue/` и кадрами, а строки в общей базе помечены
серийником. Переключение пространства — это `use(serial)`: он переставляет
пути в `config` и модулях, как это делает `prefs.apply` для остальных
настроек. Работает по-прежнему один телефон за раз (телефон водит `Runner`,
и слот у него один) — переключение меняет не «кто сейчас работает», а «чьё
хозяйство мы открыли».

Имя телефона (`Redmi Note 10`) спрашивается у самого устройства и
запоминается: серийник вроде `74eed241` ни о чём не говорит, а выбирать
пространство по нему пришлось бы наугад.
"""
import json
import os
import re

import adb
import config

ROOT = os.path.join(config.BASE, "devices")
NAMES = os.path.join(ROOT, "имена.json")

# Что переезжает в пространство телефона из общей папки при первом запуске.
# Настройки, нажитые до разделения, принадлежат тому телефону, с которым
# работали, — терять их нельзя.
INHERITED = ("plan.json", "interests.json")


def slug(serial):
    """Серийник в имя папки. По сети он вида `192.168.1.5:5555`.

    Из одних точек имя не делаем: серийник приходит и от adb, и из запроса
    страницы, а `..` увёл бы папку телефона на уровень выше — в корень
    проекта, поверх общих файлов.
    """
    name = re.sub(r"[^\w.-]", "_", str(serial or "")).strip(".-")
    return name or "общий"


def space(serial):
    """Папка телефона. Создаётся при первом обращении.

    Наследство из общей папки достаётся ТОЛЬКО самому первому пространству —
    это переезд со старого устройства программы, где телефон был один. Второй
    телефон должен начинаться с чистого листа, иначе «разделение» сведётся к
    тому, что у всех одинаковое расписание.
    """
    path = os.path.join(ROOT, slug(serial))
    first = not os.path.isdir(ROOT) or not os.listdir(ROOT)
    fresh = not os.path.isdir(path)
    os.makedirs(path, exist_ok=True)
    # Пространство есть — значит телефон известен, даже если имя пока не
    # спросили: без этой записи он не появится в списке, пока не подключён.
    if fresh and serial:
        remember(serial, name_of(serial))
    if fresh and first:
        for name in INHERITED:
            old = os.path.join(config.BASE, name)
            if os.path.exists(old) and not os.path.exists(os.path.join(path, name)):
                try:
                    with open(old, "rb") as src, \
                            open(os.path.join(path, name), "wb") as dst:
                        dst.write(src.read())
                except OSError:
                    pass
    return path


def _names():
    try:
        with open(NAMES, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def remember(serial, name):
    """Запомнить человеческое имя телефона. Молча, без падений."""
    if not serial or not name:
        return
    data = _names()
    if data.get(serial) == name:
        return
    data[serial] = name
    os.makedirs(ROOT, exist_ok=True)
    try:
        with open(NAMES, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def name_of(serial):
    return _names().get(serial) or serial


def known(live=None):
    """Все телефоны: и подключённые сейчас, и те, у кого есть пространство.

    Отключённый телефон из окна не исчезает: его очередь и расписание никуда
    не делись, и открыть их надо уметь без кабеля под рукой.
    """
    if live is None:
        try:
            live = [s for s, state in adb.devices() if state == "device"]
        except Exception:
            live = []

    order, seen = [], set()
    for serial in live:
        order.append(serial)
        seen.add(serial)
    for folder in sorted(os.listdir(ROOT)) if os.path.isdir(ROOT) else []:
        for serial, _ in _names().items():
            if slug(serial) == folder and serial not in seen:
                order.append(serial)
                seen.add(serial)

    return [{"serial": s,
             "name": name_of(s),
             "live": s in live,
             "link": "по сети" if ":" in s else "кабель"}
            for s in order]


def use(serial):
    """Открыть пространство телефона: пути, серийник, база.

    Всё, что дальше пишет и читает программа, начинает относиться к нему.
    """
    import jobs                 # локально: модули друг друга не тянут
    import plan as plan_mod

    serial = str(serial or "").strip()
    path = space(serial)

    config.SERIAL = serial or None
    if serial:
        adb.prefer(serial)

    config.WATCH_DIR = os.path.join(path, "watch")
    config.QUEUE_DIR = os.path.join(path, "queue")
    config.FRAMES_DIR = os.path.join(path, "frames")
    config.INTERESTS = os.path.join(path, "interests.json")
    plan_mod.PATH = os.path.join(path, "plan.json")
    jobs.DEVICE = serial

    for folder in (config.WATCH_DIR, config.QUEUE_DIR, config.FRAMES_DIR):
        os.makedirs(folder, exist_ok=True)
    # `topics_cache.json` намеренно остаётся общим: это словарь «тема ->
    # слова», он про темы, а не про телефоны, и разворачивать его заново
    # для каждого — лишние запросы к модели.
    return path
