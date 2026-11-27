"""Монтажёр: ролик по ссылке (или файлом) → оформление по шаблону → «Запасы».

Просьба 2026-11-25: «кидаю в чат ссылку на видео из инсты — он накладывает
заготовленный шаблон, рендерит и отправляет в запасы сразу с подходящим
описанием». Шаблон пока один — Rusorama (`templates/rusorama`), снят с двух
примеров человека: чёрный кадр 9:16, сверху заголовок капсом шрифтом Rubik
Bold (первая строка жёлтая), в середине ролик во всю ширину, снизу глобус и
подпись канала.

Как устроено:
  * тема «Монтаж» — полка бота, как «Запасы» (`telegram_bot.SHELVES`):
    назначается по названию темы, `/here монтаж` или в окне. Ссылка там —
    заказ монтажа (текст рядом со ссылкой — свой заголовок), видео файлом —
    тоже заказ (его подпись — исходное описание), «/login» — окно входа в
    Instagram на компьютере (`iglogin`), файл cookies.txt — тот же вход
    готовым файлом (`linkfetch.save_cookies`).
  * скачивание — `linkfetch.fetch` (yt-dlp, сам чинится);
  * заголовок и описание пишет модель (`vision`) в два шага: разбор
    раскадровки всего ролика, потом текст по разбору и исходной подписи;
    под «✅ готово» — кнопки «другой заголовок» и «другой текст» (перемонтаж);
  * рендер — ffmpeg по `template.json`. Перенос строк заголовка считается по
    ширинам букв из самого шрифта (`Font`) — без сторонних библиотек;
  * готовое — `sendVideo` в «Запасы» и сразу запись в `stock`: свои сообщения
    бот не видит, и без записи после публикации не убрал бы видео в архив;
  * работает в своём потоке, по одному ролику и с низким приоритетом:
    телефон для монтажа не нужен, и очередь публикаций его не ждёт.
"""
import json
import os
import queue
import re
import shutil
import struct
import subprocess
import sys
import threading
import time

import config
import jobs

WORK_DIR = os.path.join(config.BASE, "montage")
TEMPLATE_FILE = "template.json"
DEFAULT_TEMPLATE = "rusorama"
MAX_LINKS = 5                     # ссылок из одного сообщения
# Telegram через облако принимает от бота файлы до 50 МБ; свой сервер — до
# 2 ГБ. Под облако ужимаем с запасом.
CLOUD_UPLOAD_MB = 48
LOW_PRIORITY = 0x00004000 if os.name == "nt" else 0       # BELOW_NORMAL
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
TEXT_PAUSES = (0, 45, 120)        # повторы вопроса модели, секунды
REDO_WORDS = re.compile(r"^(заново|ещё раз|еще раз|снова|again)$", re.I)


class MontageError(RuntimeError):
    """Монтаж не вышел — текст для человека."""


# ----------------------------------------------------------------- шрифт

class Font:
    """Ширины букв из файла TTF — чтобы перенести заголовок по строкам.

    Читаются только таблицы head, hhea, hmtx, cmap и OS/2. Кернинг не
    учитывается: ширина выходит чуть больше настоящей, и строка, которая
    «влезла» по этому счёту, влезает и на экране. Центрирует строку сам
    ffmpeg по её настоящей ширине (`text_w`)."""

    _cache = {}

    @classmethod
    def get(cls, path):
        font = cls._cache.get(path)
        if font is None:
            font = cls._cache[path] = cls(path)
        return font

    def __init__(self, path):
        with open(path, "rb") as f:
            data = f.read()
        self.path = path
        count = struct.unpack(">H", data[4:6])[0]
        tables = {}
        for i in range(count):
            tag, _, offset, _ = struct.unpack(">4sIII", data[12 + 16 * i:28 + 16 * i])
            tables[tag.decode("latin-1")] = offset
        head, hhea, hmtx = tables["head"], tables["hhea"], tables["hmtx"]
        self.upm = struct.unpack(">H", data[head + 18:head + 20])[0]
        self.ascent, self.descent = struct.unpack(">hh", data[hhea + 4:hhea + 8])
        metrics = struct.unpack(">H", data[hhea + 34:hhea + 36])[0]
        self.advances = [struct.unpack(">H", data[hmtx + 4 * i:hmtx + 4 * i + 2])[0]
                         for i in range(metrics)]
        self.cap = 0
        os2 = tables.get("OS/2")
        if os2 is not None and struct.unpack(">H", data[os2:os2 + 2])[0] >= 2:
            self.cap = struct.unpack(">h", data[os2 + 88:os2 + 90])[0]
        self.cmap = self._cmap(data, tables["cmap"])

    @staticmethod
    def _cmap(data, base):
        count = struct.unpack(">H", data[base + 2:base + 4])[0]
        subtables = {}
        for i in range(count):
            platform, encoding, offset = struct.unpack(
                ">HHI", data[base + 4 + 8 * i:base + 12 + 8 * i])
            subtables[(platform, encoding)] = base + offset
        for key in ((3, 10), (0, 4), (3, 1), (0, 3)):
            off = subtables.get(key)
            if off is None:
                continue
            fmt = struct.unpack(">H", data[off:off + 2])[0]
            if fmt == 12:
                groups = struct.unpack(">I", data[off + 12:off + 16])[0]
                table = {}
                for g in range(groups):
                    start, end, glyph = struct.unpack(
                        ">III", data[off + 16 + 12 * g:off + 28 + 12 * g])
                    for code in range(start, end + 1):
                        table[code] = glyph + code - start
                return table
            if fmt == 4:
                seg2 = struct.unpack(">H", data[off + 6:off + 8])[0]
                ends = off + 14
                starts = ends + seg2 + 2
                deltas = starts + seg2
                ranges = deltas + seg2
                table = {}
                for i in range(seg2 // 2):
                    end = struct.unpack(">H", data[ends + 2 * i:ends + 2 * i + 2])[0]
                    start = struct.unpack(">H", data[starts + 2 * i:starts + 2 * i + 2])[0]
                    delta = struct.unpack(">h", data[deltas + 2 * i:deltas + 2 * i + 2])[0]
                    roff = struct.unpack(">H", data[ranges + 2 * i:ranges + 2 * i + 2])[0]
                    for code in range(start, end + 1):
                        if code == 0xFFFF:
                            continue
                        if roff == 0:
                            glyph = (code + delta) & 0xFFFF
                        else:
                            at = ranges + 2 * i + roff + 2 * (code - start)
                            glyph = struct.unpack(">H", data[at:at + 2])[0]
                            glyph = (glyph + delta) & 0xFFFF if glyph else 0
                        if glyph:
                            table[code] = glyph
                return table
        return {}

    def has(self, char):
        return ord(char) in self.cmap

    def width(self, text, size):
        total = 0
        for char in text:
            glyph = self.cmap.get(ord(char), 0)
            total += self.advances[min(glyph, len(self.advances) - 1)]
        return total * size / self.upm


# --------------------------------------------------------------- шаблоны

def _template_roots():
    """Где искать шаблоны: свои (рядом с данными программы) раньше встроенных."""
    here = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    roots, seen = [], set()
    for root in (os.path.join(config.BASE, "templates"), os.path.join(here, "templates")):
        key = os.path.normcase(os.path.abspath(root))
        if key not in seen and os.path.isdir(root):
            seen.add(key)
            roots.append(root)
    return roots


def templates():
    """Имена шаблонов (папки с template.json)."""
    names = []
    for root in _template_roots():
        for name in sorted(os.listdir(root)):
            if name not in names and os.path.exists(os.path.join(root, name, TEMPLATE_FILE)):
                names.append(name)
    return names


def load_template(name=None):
    names = templates()
    if not names:
        raise MontageError("нет ни одного шаблона монтажа (папка templates)")
    name = name or (DEFAULT_TEMPLATE if DEFAULT_TEMPLATE in names else names[0])
    for root in _template_roots():
        folder = os.path.join(root, name)
        path = os.path.join(folder, TEMPLATE_FILE)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                tpl = json.load(f)
            tpl["dir"], tpl["id"] = folder, name
            return tpl
    raise MontageError(f"нет шаблона «{name}». Есть: {', '.join(names)}")


# ---------------------------------------------------------------- раскладка

def _wrap(words, font, size, width, glue):
    """Жадный перенос; предлог в конце строки уходит на следующую
    («…PERSONALMENTE A / VLADIMIR PUTIN» → «…PERSONALMENTE / A VLADIMIR PUTIN»,
    как на примере человека)."""
    lines, cur = [], []
    for word in words:
        if cur and font.width(" ".join(cur + [word]), size) > width:
            carry = []
            while len(cur) > 1 and cur[-1].lower().strip(",.:;") in glue:
                carry.insert(0, cur.pop())
            lines.append(" ".join(cur))
            cur = carry + [word]
        else:
            cur.append(word)
    if cur:
        lines.append(" ".join(cur))
    return lines


def layout_title(title, tpl):
    """(кегль, строки) заголовка: самый крупный кегль, при котором строк не
    больше `max_lines`; не вышло и на `min_size` — сколько получится.

    «|» в заголовке — перенос по смыслу от модели (на примерах человек рвёт
    строку не по ширине, а между «кто что сделал» и продолжением). Каждая
    часть при этом переносится по ширине сама, если не влезла."""
    t = tpl["title"]
    font = Font.get(os.path.join(tpl["dir"], t["font"]))
    parts = [p.split() for p in title.split("|") if p.strip()]
    glue = {w.lower() for w in t.get("glue", [])}
    # `prefer_lines` (с 26.11, крупный заголовок): сначала две ровные строки
    # чуть мельче, и только если не влезли и на `min_size` — до `max_lines`
    # крупнее. Иначе при кегле 64 вторая часть рвалась и внизу висел хвост
    # «LA SALA».
    for limit in [n for n in (t.get("prefer_lines"), t["max_lines"]) if n]:
        size = t["size"]
        while True:
            lines = [line for words in parts
                     for line in _wrap(words, font, size, t["max_width"], glue)]
            if len(lines) <= limit or size <= t["min_size"]:
                break
            size -= 1
        if len(lines) <= limit:
            break
    if len(lines) > t["max_lines"] and len(parts) > 1:
        # Перенос модели не влез — переносим целиком по ширине.
        return layout_title(" ".join(w for words in parts for w in words), tpl)
    return size, lines


def hook_lines(title, size, lines, tpl):
    """Сколько строк занимает крючок — часть заголовка до «|»: она вся
    акцентным цветом. При кегле 64 (с 26.11) крючок бывает и в две строки,
    и жёлтой была бы только первая половина фразы. Перенос модели не влез
    и заголовок переложен целиком по ширине — акцент, как раньше, одна строка."""
    parts = [p.split() for p in title.split("|") if p.strip()]
    if len(parts) < 2:
        return 1
    t = tpl["title"]
    font = Font.get(os.path.join(tpl["dir"], t["font"]))
    first = _wrap(parts[0], font, size, t["max_width"], {w.lower() for w in t.get("glue", [])})
    return len(first) if len(first) < len(lines) and lines[:len(first)] == first else 1


def fit_video(src_w, src_h, tpl):
    """Размер и место ролика в кадре: (w, h, x, y). Чётные числа — их требует
    yuv420p.

    Во всю ширину кадра («видео во всю ширину от экрана до экрана», просьба
    2026-11-25): прежде ролик вписывался целиком в `max_height`, и вертикальный
    исходник с чёрными полями выходил узкой полоской посередине. Невысокий
    ролик стоит по центру `center_y`, высокий — по центру кадра, а заголовок и
    подпись канала ложатся на него поверх плашки (`render`).
    `fill_width: false` в шаблоне — по-старому, целиком в `max_height`."""
    width, height = tpl["size"]
    box = tpl["video"]
    if box.get("fill_width", True):
        scale = width / src_w
    else:
        scale = min(width / src_w, box["max_height"] / src_h)
    w = max(2, int(round(src_w * scale)) // 2 * 2)
    h = max(2, int(round(src_h * scale)) // 2 * 2)
    x = (width - w) // 2
    if h <= box["max_height"]:
        y = int(round(box["center_y"] - h / 2)) // 2 * 2
    else:
        y = int(round((height - h) / 2)) // 2 * 2
    return w, h, x, y


def title_top(vh, vy, tpl):
    """Верх ролика, от которого отсчитывается заголовок. Высокий ролик
    (выше `max_height`) заходит под заголовок — тогда заголовок стоит там же,
    где стоял бы над роликом наибольшей высоты: место у него постоянное."""
    box = tpl["video"]
    if vh <= box["max_height"]:
        return vy
    return int(round(box["center_y"] - box["max_height"] / 2))


# ------------------------------------------------ настоящий кадр в исходнике
#
# Исходники канала — чаще всего чужие вертикальные ролики: чёрный кадр 9:16,
# сверху их собственный текст («When Putin's FSO Met Indian Security…»), в
# середине сама съёмка (живьём 25.11). Если вписывать такой ролик целиком,
# съёмка выходит узкой полоской, а чужой текст остаётся. Поэтому сначала
# находим полосу съёмки и обрезаем по ней: поля и текст на них уходят.
#
# Как отличить съёмку от текста на чёрном: строка съёмки занята картинкой
# почти во всю ширину и меняется от кадра к кадру; строка текста — редкие
# буквы на чёрном и стоит на месте. Без сторонних библиотек: несколько
# маленьких серых кадров сырыми байтами из ffmpeg.

PROBE_W = 128              # ширина серых кадров для разбора
DARK = 28                  # ярче этого — уже не чёрное поле
ROW_SURE = 0.9             # так плотно занята картинкой — съёмка, даже если стоит
MOVE = 0.5                 # средняя разница яркости между кадрами: строка «живая»
BAR_MAX = 20               # самая яркая точка чёрного поля (сжатие даёт шум)


def _gray_frames(path, ffmpeg, meta, count=7):
    """Несколько серых кадров PROBE_W x sh из разных мест ролика: (sh, [bytes])."""
    sh = max(8, int(round(PROBE_W * meta["h"] / meta["w"])) // 2 * 2)
    duration = meta.get("duration") or 0
    frames = []
    for i in range(count):
        at = duration * (i + 0.5) / count if duration > 1 else 0
        res = _run([ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{at:.2f}", "-i", path,
                    "-frames:v", "1", "-vf",
                    f"scale={meta['w']}:{meta['h']},setsar=1,scale={PROBE_W}:{sh},format=gray",
                    "-f", "rawvideo", "-"], 120, step="поиск полей ролика")
        if len(res.stdout) == PROBE_W * sh:
            frames.append(res.stdout)
    return sh, frames


def _longest_run(flags, gap=2):
    """Самый длинный отрезок True, разрывы до `gap` подряд — не разрыв: (начало, конец)."""
    best, start, misses = (0, -1), None, 0
    for i, on in enumerate(list(flags) + [False] * (gap + 1)):
        if on:
            start = i if start is None else start
            misses = 0
        elif start is not None:
            misses += 1
            if misses > gap:
                end = i - misses
                if end - start > best[1] - best[0]:
                    best = (start, end)
                start, misses = None, 0
    return best


def content_box(frames, sw, sh):
    """Полоса съёмки по серым кадрам: (x1, y1, x2, y2) в клетках PROBE_W x sh
    (концы включительно) или None — не нашлась или это весь кадр."""
    if not frames:
        return None
    n = len(frames)
    rows, moves = [0.0] * sh, [0.0] * sh
    for k, fr in enumerate(frames):
        for y in range(sh):
            line = fr[y * sw:(y + 1) * sw]
            lit = 0
            for x in range(sw):
                if line[x] > DARK:
                    lit += 1
            rows[y] += lit / sw
            if k:
                prev = frames[k - 1][y * sw:(y + 1) * sw]
                moves[y] += sum(abs(a - b) for a, b in zip(line, prev)) / sw
    rows = [r / n for r in rows]
    moves = [m / max(1, n - 1) for m in moves]
    # Главный признак — движение: строки чужого текста на поле неподвижны
    # (разница между кадрами ровно 0), строки съёмки меняются. По одной
    # «занятости» не различить: на пробе с ночной съёмкой (тёмные костюмы на
    # чёрном, как на примере 25.11) буквы занимали строку на 50–70 %, а сама
    # съёмка — на треть. Плотно занятая строка (ярко, во всю ширину) —
    # съёмка, даже если стоит на месте.
    flags = [r >= ROW_SURE or (n > 1 and m >= MOVE) for r, m in zip(rows, moves)]
    y1, y2 = _longest_run(flags, gap=4)
    if y2 < y1 or (y2 - y1 + 1) < sh * 0.15:
        return None
    # Поля слева и справа — чистый чёрный по всей высоте полосы во всех
    # кадрах. Тёмная съёмка тоже бывает «пустой» по занятости, но хоть
    # где-то в столбце светлее поля — поэтому меряем самую яркую точку.
    peak = [max(fr[y * sw + x] for fr in frames for y in range(y1, y2 + 1)) for x in range(sw)]
    x1, x2 = _longest_run([p > BAR_MAX for p in peak], gap=2)
    # Поля по бокам у настоящих исходников симметричны (ролик по центру).
    # Чёрное только с одной стороны — это тёмный угол самой съёмки (ночь),
    # резать нельзя: ширину оставляем всю.
    if x2 < x1 or abs(x1 - (sw - 1 - x2)) > sw * 0.06:
        x1, x2 = 0, sw - 1
    if (y2 - y1 + 1) >= sh * 0.97 and (x2 - x1 + 1) >= sw * 0.97:
        return None
    return x1, y1, x2, y2


def detect_content(path, ffmpeg, meta):
    """Где в исходнике сама съёмка: (w, h, x, y) в пикселях показа для
    фильтра crop — или None (обрезать нечего / не понять). По краю — на
    одну клетку внутрь: на границе поля и съёмки бывает серая полоса."""
    sh, frames = _gray_frames(path, ffmpeg, meta)
    box = content_box(frames, PROBE_W, sh)
    if box is None:
        return None
    x1, y1, x2, y2 = box
    fx, fy = meta["w"] / PROBE_W, meta["h"] / sh
    inset_x = 1 if x1 > 0 or x2 < PROBE_W - 1 else 0
    left = int((x1 + inset_x) * fx)
    right = int((x2 + 1 - inset_x) * fx)
    top = int((y1 + 1) * fy)
    bottom = int(y2 * fy)
    w, h = (right - left) // 2 * 2, (bottom - top) // 2 * 2
    if w < 16 or h < 16:
        return None
    return w, h, left // 2 * 2, top // 2 * 2


def title_baselines(count, size, video_top, tpl):
    t = tpl["title"]
    pitch = round(size * t["line_height"])
    last = video_top - t["gap"]
    return [last - pitch * (count - 1 - i) for i in range(count)]


# -------------------------------------------------------------------- ffmpeg

def _run(cmd, timeout, cwd=None, step=""):
    # stdin закрыт всегда (и `-nostdin`): ffmpeg без него читает «клавиши» из
    # унаследованного stdin, а у службы телефона это труба от окна. 27.11
    # монтаж падал «ffmpeg не уложился в 5 мин» и на лёгких роликах.
    if (cmd and len(cmd) > 1 and "-nostdin" not in cmd
            and "ffmpeg" in os.path.basename(str(cmd[0])).lower()):
        cmd = [cmd[0], "-nostdin"] + list(cmd[1:])
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=timeout, cwd=cwd,
                             stdin=subprocess.DEVNULL,
                             creationflags=LOW_PRIORITY | NO_WINDOW)
    except subprocess.TimeoutExpired as e:
        said = (e.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-2:]
        what = f" ({step})" if step else ""
        tail = f": {' / '.join(said)}" if said else ""
        limit = f"{timeout // 60} мин" if timeout >= 60 else f"{timeout} с"
        raise MontageError(f"ffmpeg завис{what} — не уложился в {limit}{tail}") from e
    except OSError as e:
        raise MontageError(f"ffmpeg не запустился: {e}") from e
    return res


def _probe_ffmpeg(path, ffmpeg):
    """То же, что `probe`, но по выводу самого ffmpeg — когда ffprobe нет.

    26.11 монтаж на этом ПК не начался: ffmpeg был, ffprobe — нет, программа
    пошла качать полный ffmpeg (100 МБ), а связи в ту минуту не было. Всё, что
    нужно монтажу (размер, поворот, форма пикселя, длина, звук), ffmpeg и так
    печатает в шапке `ffmpeg -i`."""
    res = _run([ffmpeg, "-hide_banner", "-nostdin", "-i", path], 120)
    text = res.stderr.decode("utf-8", "replace")
    video = re.search(r"Stream #\S+.*?: Video: .*", text)
    if not video:
        raise MontageError("в файле нет видео")
    size = re.search(r"\b(\d{2,5})x(\d{2,5})\b", video.group(0))
    if not size:
        raise MontageError("не понял размер кадра ролика")
    w, h = int(size.group(1)), int(size.group(2))
    sar = re.search(r"\[SAR (\d+):(\d+)", video.group(0))
    if sar and int(sar.group(1)) and int(sar.group(2)):
        w = round(w * int(sar.group(1)) / int(sar.group(2)))
    turn = re.search(r"rotation of (-?[\d.]+) degrees|rotate\s*:\s*(-?\d+)", text)
    rotation = int(float(turn.group(1) or turn.group(2))) if turn else 0
    if abs(rotation) % 180 == 90:
        w, h = h, w
    dur = re.search(r"Duration: (\d+):(\d+):([\d.]+)", text)
    duration = (int(dur.group(1)) * 3600 + int(dur.group(2)) * 60 + float(dur.group(3))) if dur else 0.0
    return {"w": w, "h": h, "duration": duration, "audio": bool(re.search(r"Stream #\S+.*?: Audio:", text))}


def probe(path, ffprobe, ffmpeg=None):
    """Ширина и высота КАК ПОКАЗЫВАЕТСЯ (с поворотом и формой пикселя),
    длительность и есть ли звук. Нет ffprobe — по выводу ffmpeg."""
    if not ffprobe or not os.path.exists(ffprobe):
        if not ffmpeg:
            raise MontageError("нет ни ffprobe, ни ffmpeg — не понять, что за ролик")
        return _probe_ffmpeg(path, ffmpeg)
    res = _run([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", path], 120)
    try:
        data = json.loads(res.stdout.decode("utf-8", "replace") or "{}")
    except ValueError:
        data = {}
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video:
        raise MontageError("в файле нет видео")
    w, h = int(video.get("width") or 0), int(video.get("height") or 0)
    sar = str(video.get("sample_aspect_ratio") or "1:1")
    m = re.fullmatch(r"(\d+):(\d+)", sar)
    if m and int(m.group(1)) and int(m.group(2)):
        w = round(w * int(m.group(1)) / int(m.group(2)))
    rotation = 0
    for side in video.get("side_data_list") or []:
        if "rotation" in side:
            rotation = int(float(side["rotation"]))
    rotation = rotation or int(float((video.get("tags") or {}).get("rotate") or 0))
    if abs(rotation) % 180 == 90:
        w, h = h, w
    duration = float((data.get("format") or {}).get("duration") or video.get("duration") or 0)
    if not w or not h:
        raise MontageError("не понял размер кадра ролика")
    return {"w": w, "h": h, "duration": duration,
            "audio": any(s.get("codec_type") == "audio" for s in streams)}


BOARD_FRAMES = 16                 # кадров в раскадровке, сеткой 4x4


def storyboard(path, ffmpeg, meta):
    """Раскадровка всего ролика одной картинкой: 16 кадров по порядку, 4x4.

    Прежде модели давали один кадр из середины, да ещё ужатый до 240 точек
    (27.11, ролик про ВВС четырёх стран: по нему модель написала «Си Цзиньпин
    и самолёт, который не то, чем кажется»). По раскадровке в полный размер
    она поняла ролик верно. Кадры — равномерно по длине: смена сцен на
    динамичной нарезке даёт сотню склеек и ничего не выбирает.
    Не вышло (ролик короче сетки) — один кадр из середины."""
    duration = meta.get("duration") or 0
    # Кадры — по одному, быстрым переходом к месту (`-ss` до `-i`), а не
    # фильтром fps по всему ролику: тот декодировал ролик целиком, и 27.11
    # раскадровка падала «ffmpeg не уложился в 5 мин» — и монтаж вместе с
    # ней. Так время не зависит ни от длины, ни от тяжести исходника.
    try:
        board = _board_by_frames(path, ffmpeg, duration) if duration > 1 else b""
    except MontageError:
        board = b""
    if board:
        return board
    # Не вышло — один кадр из середины; не вышел и он — пусто: текст тогда
    # пишется по подписи, а монтаж из-за раскадровки не падает.
    at = max(0.0, duration * 0.4)
    try:
        res = _run([ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{at:.2f}", "-i", path,
                    "-frames:v", "1", "-vf", "scale=720:-2", "-f", "image2pipe", "-c:v", "png", "-"],
                   BOARD_FRAME_SEC, step="кадр для модели")
    except MontageError:
        return b""
    return res.stdout if res.stdout[:4] == b"\x89PNG" else b""


BOARD_FRAME_SEC = 40              # на один кадр раскадровки
BOARD_TOTAL_SEC = 180             # на всю раскадровку


def _board_by_frames(path, ffmpeg, duration):
    """Сетка 4x4 из кадров, снятых по одному. b"" — набралось меньше четырёх."""
    import tempfile

    tmp = tempfile.mkdtemp(prefix="pa_board_")
    try:
        got, deadline = 0, time.time() + BOARD_TOTAL_SEC
        for i in range(BOARD_FRAMES):
            if time.time() > deadline:
                break
            at = duration * (i + 0.5) / BOARD_FRAMES
            name = os.path.join(tmp, f"f{got:02d}.png")
            try:
                _run([ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{at:.2f}", "-i", path,
                      "-frames:v", "1", "-an", "-vf", "scale=360:-2,setsar=1", "-y", name],
                     BOARD_FRAME_SEC, step="кадр раскадровки")
            except MontageError:
                continue
            if os.path.exists(name) and os.path.getsize(name) > 0:
                got += 1
        if got < 4:
            return b""
        rows = (got + 3) // 4
        for k in range(got, rows * 4):        # добить ряд последним кадром
            shutil.copyfile(os.path.join(tmp, f"f{got - 1:02d}.png"), os.path.join(tmp, f"f{k:02d}.png"))
        res = _run([ffmpeg, "-hide_banner", "-loglevel", "error", "-framerate", "1",
                    "-i", os.path.join(tmp, "f%02d.png"), "-vf", f"tile=4x{rows}", "-frames:v", "1",
                    "-f", "image2pipe", "-c:v", "png", "-"], BOARD_FRAME_SEC, step="сетка раскадровки")
        return res.stdout if res.stdout[:4] == b"\x89PNG" else b""
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _color(value):
    return "0x" + str(value).lstrip("#").upper()


def render(src, title, tpl, out, tools, meta=None, cap_mb=None):
    """Смонтировать ролик по шаблону в `out` (mp4, H.264 + AAC).

    Всё, что рисуется, кладётся в рабочую папку рядом с `out` и зовётся
    относительными именами: в фильтрах ffmpeg двоеточие пути Windows («C:»)
    пришлось бы экранировать в два слоя, а текст заголовка идёт файлом
    (`textfile`, без разбора `%`), чтобы кавычки и запятые не ломали фильтр."""
    ffmpeg, ffprobe = tools
    meta = meta or probe(src, ffprobe, ffmpeg)
    width, height = tpl["size"]
    fps = int(tpl.get("fps", 30))
    try:
        crop = detect_content(src, ffmpeg, meta) if tpl["video"].get("crop_bars", True) else None
    except MontageError:
        crop = None                 # поля не нашлись — монтируем без обрезки, а не падаем
    cw, ch = (crop[0], crop[1]) if crop else (meta["w"], meta["h"])
    vw, vh, vx, vy = fit_video(cw, ch, tpl)
    size, lines = layout_title(title, tpl)
    work = out + ".parts"
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    t, badge = tpl["title"], tpl.get("badge") or {}
    fonts = {}

    def font_file(name):
        if name not in fonts:
            fonts[name] = f"f{len(fonts)}.ttf"
            shutil.copyfile(os.path.join(tpl["dir"], name), os.path.join(work, fonts[name]))
        return fonts[name]

    texts = []

    def draw(text, font, fsize, color, x, y):
        name = f"t{len(texts)}.txt"
        with open(os.path.join(work, name), "w", encoding="utf-8", newline="") as f:
            f.write(text)
        texts.append(f"drawtext=fontfile={font_file(font)}:textfile={name}:expansion=none:"
                     f"fontsize={fsize}:fontcolor={_color(color)}:x={x}:y={y}:y_align=baseline")

    accent = hook_lines(title, size, lines, tpl) if (t.get("accent") == "first_line"
                                                     and len(lines) > 1) else 0
    bases = title_baselines(len(lines), size, title_top(vh, vy, tpl), tpl)
    for i, (line, base) in enumerate(zip(lines, bases)):
        color = t["accent_color"] if i < accent else t["color"]
        draw(line, t["font"], size, color, "(w-text_w)/2", base)
    for line, base in zip(badge.get("lines", []), badge.get("baselines", [])):
        draw(line, badge["font"], badge["size"], badge["color"], badge["x"], base)

    # Плашки: ролик во всю ширину бывает выше места между заголовком и
    # подписью канала. Тогда верх кадра до низа заголовка и низ от подписи
    # канала закрываются цветом фона: текст читается, а чужой текст вверху и
    # внизу исходника (его заголовок, ник, субтитры) уходит под плашку. Узкая
    # полоса только под строками выглядела бы зеброй и чужой текст выше
    # заголовка оставляла бы на виду (проверено на пробном ролике 25.11).
    plates = []
    pad = round(size * 0.6)
    regions = [(0, bases[-1] + round(size * 0.3) + pad)] if bases else []
    if badge:
        tops = [base - badge["size"] for base in badge.get("baselines", [])]
        bottoms = [base + round(badge["size"] * 0.3) for base in badge.get("baselines", [])]
        if badge.get("image"):
            half = badge["image_size"] // 2
            tops.append(badge["image_center_y"] - half)
            bottoms.append(badge["image_center_y"] + half)
        if tops:
            bpad = round(badge["size"] * 0.8)
            regions.append((min(tops) - bpad, height))
    for top, bottom in regions:
        top, bottom = max(0, top), min(height, bottom)
        if bottom > top and top < vy + vh and bottom > vy:      # ролик под текстом
            plates.append(f"drawbox=x=0:y={top}:w={width}:h={bottom - top}:"
                          f"color={_color(tpl.get('background', '#000000'))}:t=fill")

    src_chain = (f"scale={meta['w']}:{meta['h']}:flags=lanczos,setsar=1,"
                 f"crop={crop[0]}:{crop[1]}:{crop[2]}:{crop[3]}," if crop else "")
    graph = [f"color=c={_color(tpl.get('background', '#000000'))}:s={width}x{height}:r={fps}[bg]",
             f"[0:v]{src_chain}scale={vw}:{vh}:flags=lanczos,setsar=1,fps={fps}[v]",
             f"[bg][v]overlay={vx}:{vy}:shortest=1" + "".join("," + p for p in plates) + "[s0]"]
    inputs = ["-i", src]
    last = "s0"
    if badge.get("image"):
        shutil.copyfile(os.path.join(tpl["dir"], badge["image"]), os.path.join(work, "badge.png"))
        inputs += ["-loop", "1", "-framerate", str(fps), "-i", "badge.png"]
        side = badge["image_size"]
        graph += [f"[1:v]scale={side}:{side}:flags=lanczos[b]",
                  f"[s0][b]overlay={badge['image_x']}:{badge['image_center_y'] - side // 2}"
                  f":shortest=1[s1]"]
        last = "s1"
    graph.append(f"[{last}]" + ",".join(texts or ["null"]) + "[out]")
    # Граф — аргументом, а не файлом: `-filter_complex_script` в ffmpeg 8
    # убрали, а `-/filter_complex` нет в старых. Оболочки между нами и ffmpeg
    # нет, так что экранировать нечего.

    rate = []
    if cap_mb and meta["duration"] > 0:
        # Под облако Telegram (50 МБ): потолок битрейта по длине ролика.
        kbps = int(cap_mb * 8 * 1024 * 0.92 / meta["duration"]) - 160
        if kbps < 6000:
            rate = ["-maxrate", f"{max(kbps, 600)}k", "-bufsize", f"{max(kbps, 600) * 2}k"]
    cmd = ([ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y"] + inputs
           # Главы и метаданные исходника не переносим: ffmpeg иначе кладёт
           # главы отдельной текстовой дорожкой (живьём 25.11 на образце mp4),
           # а в публикацию не должно уйти ничего из чужого файла.
           + ["-filter_complex", ";".join(graph), "-map", "[out]", "-map", "0:a:0?",
              "-map_chapters", "-1", "-map_metadata", "-1", "-dn", "-sn",
              "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
              "-profile:v", "high", "-r", str(fps)] + rate
           + ["-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-shortest",
              os.path.abspath(out)])
    res = _run(cmd, max(600, int(meta["duration"] * 30)), cwd=work, step="рендер")
    if res.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) < 1024:
        tail = res.stderr.decode("utf-8", "replace").strip().splitlines()[-3:]
        raise MontageError("ffmpeg не смонтировал: " + (" / ".join(tail) or f"код {res.returncode}"))
    shutil.rmtree(work, ignore_errors=True)
    return {"size": size, "lines": lines, "video": (vw, vh, vx, vy), "meta": meta,
            "crop": crop, "plates": len(plates)}


# ------------------------------------------------------------------- тексты

# Тексты пишутся в два шага (27.11): сначала модель разбирает раскадровку
# всего ролика («что это, кто, в чём идея»), потом по разбору и подписи
# автора пишет заголовок и описание. В один шаг по кадру она описывала
# картинку («Си Цзиньпин и самолёт»), а не смысл ролика.
LOOK = """Перед тобой раскадровка ВСЕГО ролика: кадры по порядку, слева направо и сверху вниз.
{source}
Разбери ролик целиком (не по одному кадру) и ответь по-русски, только JSON:
{{"kind": "новость | речь | нарезка/сравнение | юмор | другое",
  "people": ["кого узнал и по чему"],
  "on_screen": "текст на кадрах, если есть (ники и водяные знаки авторов не нужны)",
  "story": "что происходит от начала до конца, 2–3 фразы",
  "theme": "о чём ролик одной фразой — его идея, а не перечень кадров",
  "striking": "самое цепляющее в ролике",
  "sure": "high | low — уверен ли, что понял ролик",
  "unclear": "что осталось непонятным, или пусто"}}"""

WRITE = """Ты редактор канала «{channel}». Пишешь на языке: {language}.
О канале: {about}
Мы публикуем у себя чужой ролик со своим оформлением: сверху на видео — заголовок, под постом — описание.

Что в ролике:
{facts}
{user_title}{rejected}
{taste}{rules}
Нужно:
1. title — заголовок поверх видео. Не констатация факта («X сделал Y», «X и Y на одном экране»), а образ или обобщение, которое схватывает идею ролика: игра слов, метафора, контраст, неожиданный угол, чуть с юмором — чтобы хотелось досмотреть. Интрига конкретная: не «не то, чем кажется» и не «никто не ожидал». По смыслу — правда, без выдуманных подробностей. Строго не длиннее {title_chars} знаков, обычными буквами (капсом на видео сделаем сами), без эмодзи, кавычек, хэштегов и точки в конце. {title_rule} Знак | — только место переноса строки на видео: без него заголовок читается правильной фразой. Примеры стиля канала (стиль, не темы):
{examples}
Сначала придумай 5 вариантов (variants), потом выбери лучший (title): самый образный, точный по смыслу и короткий.
{emoji}2. caption — описание поста: {caption_style} Не длиннее {caption_chars} знаков, без хэштегов. Примеры:
{caption_examples}
3. hashtags — {hashtags}
4. title_ru и caption_ru — перевод title и caption на русский (для проверки, в пост не идёт).
Без ссылок, @упоминаний и призывов подписаться.

Ответ — только JSON: {{"variants": ["..."], "title": "...", "title_emoji": "...", "caption": "...", "hashtags": ["#..."], "title_ru": "...", "caption_ru": "..."}}"""

# Переделка кнопкой под «✅ готово» (27.11): что не зашло и что оставить.
REDO_TEXT = {
    "all": "Прошлый вариант человеку не зашёл — напиши заметно по-другому: другой образ, другая "
           "мысль, другое построение фразы. Прошлый: «{title}» / «{caption}»\n",
    "title": "Описание уже есть, его не меняем: «{caption}». Нужен только новый заголовок к нему "
             "(caption верни пустым). Прошлый заголовок человеку не зашёл: «{title}» — придумай с "
             "нуля, а не замени в нём слово: другой образ и другое построение.\n",
    "caption": "Прошлое описание человеку не зашло — напиши заметно по-другому: другая мысль, другой "
               "крючок, другое начало и по-другому построенный вопрос в конце. Прошлое: «{caption}»\n",
}

CAPTION_STYLE = ("не пересказ ролика, а мысль вокруг него: наблюдение, обобщение, ирония, "
                 "второй смысл. 2–3 коротких предложения живым языком.")
MAX_VARIANTS = 3                  # другие заголовки — для старых кнопок «1. …» под «готово»

EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")


def _clean_title(text, font, upper=True):
    """Заголовок без эмодзи, кавычек, хэштегов и точки. На видео — капсом
    (`upper`), в тексте поста — обычными буквами, как написала модель."""
    text = EMOJI.sub("", str(text or ""))
    text = re.sub(r"#\w+", "", text)
    text = text.replace("«", "").replace("»", "").replace('"', "").replace("“", "").replace("”", "")
    text = re.sub(r"\s*\|\s*", " | ", text)
    text = re.sub(r"\s+", " ", text).strip().strip("|").strip().rstrip(".").strip()
    if upper:
        text = text.upper()
    return "".join(ch for ch in text if ch in " |" or font.has(ch))


def _utf16(text):
    return len(text.encode("utf-16-le")) // 2


MAX_TAGS = 5


def _clean_caption(text, limit, head="", max_tags=MAX_TAGS):
    """Текст поста: заголовок, описание и хэштеги — через пустую строку
    (просьба 2026-11-26: «пустыми строками отделить заголовок, описание и
    хэштеги, а он всё в одну кучу»). Хэштеги вынимаются из ответа модели, где
    бы она их ни поставила: хвост из хэштегов уходит вниз целиком, а тег
    посреди фразы («la #OTAN responde») остаётся в ней словом. `max_tags`=0 —
    без хэштегов совсем. Без ссылок и @упоминаний, в пределах `limit` знаков
    UTF-16 (так считает Threads — самый строгий из списка, 500); не влезает —
    укорачивается описание, заголовок и хэштеги остаются."""
    text = re.sub(r"https?://\S+", "", str(text or ""))
    # С пробелом перед ником: «la firma @autor, especialista» → «la firma, especialista».
    text = re.sub(r"[ \t]*(?<!\w)@[\w.]+", "", text)
    tags, seen = [], set()
    for tag in re.findall(r"#\w+", text):
        if tag.lower() not in seen:
            seen.add(tag.lower())
            tags.append(tag)
    tags = " ".join(tags[:max_tags])
    tail = re.search(r"(?:\s*#\w+[,;]?)+[\s.]*$", text)
    body = text[:tail.start()] if tail else text
    body = re.sub(r"#(\w+)", r"\1", body)
    body = "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in body.splitlines())
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    head = str(head or "").replace(" | ", " ").strip()
    room = limit - sum(_utf16(p) + 2 for p in (head, tags) if p)
    # Вопрос в конце поста (27.11: пост кончается вопросом к читателю) при
    # обрезке остаётся — сокращается то, что перед ним.
    ask, glue = "", ""
    if _utf16(body) > room and body.endswith("?") and "¿" in body:
        start = mark = body.rfind("¿")
        while start > 0 and (body[start - 1] in " \t\n" or EMOJI.match(body[start - 1])):
            start -= 1
        if 0 < start and _utf16(body[start:].strip()) < room // 2:
            glue = "\n" if "\n" in body[start:mark] else " "
            ask, body = body[start:].strip(), body[:start].rstrip()
            room -= _utf16(ask) + 1
    cut = body if room > 1 else ""
    while _utf16(cut) > room:
        ends = [m.end() for m in re.finditer(r"[.!?…](?=\s)", cut[:-1])]
        if ends:
            cut = cut[:ends[-1]]
            continue
        cut = cut[:room - 1]
        while _utf16(cut) > room - 1:              # эмодзи в UTF-16 — два знака
            cut = cut[:-1]
        cut = cut.rstrip() + "…"
    cut = cut.strip()
    if ask:
        cut = f"{cut}{glue}{ask}" if cut else ask
    return "\n\n".join(p for p in (head, cut, tags) if p)


def _model_json(ask, pauses):
    """Ответ модели JSON-объектом, с повторами через `pauses`: (dict, "") или
    (None, почему не вышло)."""
    import vision

    why = ""
    for pause in pauses:
        time.sleep(pause)
        try:
            answer = ask()
        except Exception as e:                       # VisionError, сеть, оплата (402)
            why = f"{type(e).__name__}: {e}"[:200]
            continue
        data = vision._json_from(answer or "") if answer else None
        if isinstance(data, dict):
            return data, ""
        why = "ответила не по форме"
    return None, why


def _plain(text):
    """Заголовок одной строкой: без знака переноса «|»."""
    return " ".join(re.sub(r"\s*\|\s*", " ", str(text or "")).split())


def _avoided(text, words):
    """Запрещённые обороты шаблона (`texts.avoid`), найденные в тексте."""
    return [w for w in words
            if re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", text, re.I)]


# ---------------------------------------------------------- память шаблона
#
# Просьба 2026-11-27: у каждого шаблона свой контекст описаний — о чём канал,
# что человек принял и что отверг. Принятое — «✅ Одобрить», текст, который
# вышел в публикацию, и заголовок, выбранный старой кнопкой; отвергнутое — то,
# что переделали кнопками «Заголовок», «Описание», «Всё заново». Правила канала
# (шаблон) память не трогает. Хранится до MEMORY_KEEP записей каждого
# вида, старые выпадают (скользящее окно — выбор человека: без выжимки);
# модель видит последние MEMORY_SHOW. Файл — в данных программы, а не в
# шаблоне: встроенный шаблон лежит внутри exe, и память общая на все телефоны.
MEMORY_DIR = os.path.join(config.BASE, "montage_memory")
MEMORY_KEEP = 30
MEMORY_SHOW = 5
_MEMORY_LOCK = threading.Lock()


def _memory_path(tpl_id):
    return os.path.join(MEMORY_DIR, re.sub(r"[^\w.-]", "_", str(tpl_id)) + ".json")


def memory(tpl_id):
    """{"about": "...", "accepted": [...], "rejected": [...]}; запись —
    {"title", "caption", "theme", "at"}, старые первыми."""
    try:
        with open(_memory_path(tpl_id), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    out = {"about": str(data.get("about") or "").strip()}
    for kind in ("accepted", "rejected"):
        items = data.get(kind) if isinstance(data.get(kind), list) else []
        out[kind] = [e for e in items if isinstance(e, dict) and e.get("title")]
    return out


def _memory_save(tpl_id, mem):
    os.makedirs(MEMORY_DIR, exist_ok=True)
    path = _memory_path(tpl_id)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(mem, f, ensure_ascii=False, indent=1)
    os.replace(path + ".tmp", path)


def _post_parts(title, caption):
    """Заголовок обычными буквами и описание из текста поста (заголовок /
    описание / хэштеги через пустую строку). Первый абзац — заголовок, только
    если он и есть заголовок: в публикации его могли переписать или убрать."""
    parts = [p.strip() for p in str(caption or "").split("\n\n") if p.strip()]
    while parts and re.fullmatch(r"(#\w+[\s,;]*)+", parts[-1]):
        parts.pop()
    plain = _plain(title)
    if parts and _key(parts[0]) == _key(title):
        return parts[0], "\n".join(parts[1:])
    # Заголовок с видео — капсом; в память — обычными буквами (с эмодзи впереди
    # заглавная — первая буква, а не эмодзи).
    if plain.isupper():
        low = plain.lower()
        at = next((i for i, ch in enumerate(low) if ch.isalpha()), 0)
        plain = low[:at] + low[at:at + 1].upper() + low[at + 1:]
    return plain, "\n".join(parts)


def _key(title):
    """Заголовок для сравнения: без «|», эмодзи и регистра («👰 PUTIN ENTRE
    NOVIAS» в посте и «PUTIN | ENTRE NOVIAS» на видео — один заголовок)."""
    return _plain(EMOJI.sub("", str(title or ""))).lower()


def _head_rest(title, caption):
    """Первый абзац поста, если это заголовок, и всё остальное — описание с
    хэштегами, как есть."""
    caption = str(caption or "").strip()
    head, _, rest = caption.partition("\n\n")
    if _key(head) == _key(title):
        return head, rest.strip()
    return "", caption


def remember(tpl_id, kind, title, caption="", theme="", what=""):
    """Записать принятое (`kind`="accepted") или отвергнутое ("rejected").
    `what`="caption" — не зашло только описание (заголовок к нему был хорош).

    Та же запись в другом списке убирается: последнее слово человека. «Та
    же» — тот же заголовок и то же описание (или у одной из них описания нет):
    отвергнутое описание под заголовком, который потом приняли с другим
    описанием, остаётся в отвергнутом."""
    if not tpl_id or not _key(title):
        return
    head, body = _post_parts(title, caption)
    entry = {"title": head, "caption": body[:600], "theme": str(theme or "")[:200],
             "at": time.strftime("%Y-%m-%d")}
    if what:
        entry["what"] = what
    key, text = _key(head), " ".join(entry["caption"].split())

    def same(e):
        other = " ".join(str(e.get("caption") or "").split())
        return _key(e["title"]) == key and (not text or not other or other == text)

    with _MEMORY_LOCK:
        mem = memory(tpl_id)
        twins = [e for e in mem[kind] if same(e)]
        if twins and not entry["caption"]:            # тот же заголовок без описания —
            entry["caption"] = twins[-1].get("caption", "")   # описание прежней записи
        for other in ("accepted", "rejected"):
            mem[other] = [e for e in mem[other] if not same(e)]
        mem[kind] = (mem[kind] + [entry])[-MEMORY_KEEP:]
        _memory_save(tpl_id, mem)


def set_about(tpl_id, about):
    """«О чём канал» из окна. Совпадает с описанием в шаблоне — не храним:
    правка шаблона тогда дойдёт и сюда."""
    about = str(about or "").strip()[:1500]
    try:
        if about == str(load_template(tpl_id)["texts"].get("about") or "").strip():
            about = ""
    except (MontageError, OSError, ValueError, KeyError):
        pass
    with _MEMORY_LOCK:
        mem = memory(tpl_id)
        if mem["about"] != about:
            mem["about"] = about
            _memory_save(tpl_id, mem)


def forget(tpl_id):
    """«Очистить память»: принятое и отвергнутое — прочь, «О чём канал» остаётся."""
    with _MEMORY_LOCK:
        mem = memory(tpl_id)
        mem["accepted"], mem["rejected"] = [], []
        _memory_save(tpl_id, mem)


def _taste(mem):
    """Память шаблона — в запрос модели: последние принятые и отвергнутые."""
    def line(e):
        text = f"- «{e['title']}»"
        if e.get("caption"):
            caption = " ".join(str(e["caption"]).split())[:300]
            text = (f"- описание к заголовку «{e['title']}»: {caption}" if e.get("what") == "caption"
                    else f"{text} — {caption}")
        if e.get("theme"):
            text += f" (ролик: {e['theme']})"
        return text

    lines = []
    good, bad = mem["accepted"][-MEMORY_SHOW:], mem["rejected"][-MEMORY_SHOW:]
    if good:
        lines.append("Что человек принял раньше — ориентир по вкусу и подходу (не копируй "
                     "их фразы и образы, ролик сейчас другой):")
        lines += [line(e) for e in reversed(good)]
    if bad:
        lines.append("Что человек отверг — так не писать: ни эти образы, ни то же построение "
                     "фразы с другими словами:")
        lines += [line(e) for e in reversed(bad)]
    return "\n".join(lines) + "\n" if lines else ""


def on_published(file, caption):
    """Видео монтажа вышло в публикацию — его текст принят. Пост мог быть
    поправлен перед публикацией: запоминается то, что вышло."""
    try:
        row = jobs.montage_by_file(file)
        if row and row["tpl"]:
            remember(row["tpl"], "accepted", row["title"], caption or row["caption"],
                     row["theme"])
    except Exception as e:                         # публикацию это не портит
        print(f"[монтаж] память шаблона: {type(e).__name__}: {e}", flush=True)


def write_texts(tpl, source, author="", board=b"", user_title="", rejected=None, redo="all",
                facts=None, keep=""):
    """Заголовок и текст поста от модели — словарь: title (на видео, капсом),
    caption (заголовок, описание и хэштеги через пустую строку), variants
    (другие заголовки), title_ru и caption_ru (перевод для человека), theme и
    facts (что модель знала о ролике — для переделки без скачивания).

    Шаг 1 — разбор раскадровки `board` (что за ролик и в чём его идея), шаг 2 —
    текст по разбору и подписи автора. Модель не уверена, что поняла ролик,
    а ни подписи, ни своего заголовка нет — MontageError с просьбой подсказать:
    выдуманный заголовок хуже вопроса. `facts` — готовый разбор и подпись из
    прошлого монтажа: тогда шаг 1 не нужен.

    `rejected` — (заголовок, текст поста), которые человек отверг кнопкой, и
    `redo` — что именно: "all" — всё, "title" — только заголовок (описание
    `keep` остаётся), "caption" — только описание (заголовок — `user_title`,
    первая строка поста `keep` остаётся)."""
    import postspec
    import vision

    tx = tpl["texts"]
    font = Font.get(os.path.join(tpl["dir"], tpl["title"]["font"]))
    source = source.strip()
    look = None
    if facts is None:
        known = bool(source or user_title)
        if not known and not board:
            raise MontageError("у ролика нет ни подписи, ни кадра — не из чего писать заголовок")
        if board:
            hint = (f"Подпись автора ролика: «{source[:1500]}»" if source else "Подписи у ролика нет.")
            look, why = _model_json(lambda: vision.ask(
                board, LOOK.format(source=hint), max_tokens=900, temperature=0.2, timeout=180,
                shrink=1), (0,) if known else TEXT_PAUSES)
            if look is None and not known:
                raise MontageError(f"модель не разобрала ролик ({why})")
        if look and not known and str(look.get("sure", "")).strip().lower() == "low":
            unclear = str(look.get("unclear") or "").strip()
            raise MontageError("по кадрам не понял, о чём ролик" + (f" ({unclear})" if unclear else "")
                               + ", а подписи у него нет. Пришли ссылку ещё раз и рядом свой "
                               "заголовок — или видео файлом с парой слов в подписи: по ним напишу.")
        lines = []
        if look:
            lines.append("Разбор по кадрам: " + json.dumps(
                {k: v for k, v in look.items() if k not in ("sure", "unclear")}, ensure_ascii=False))
        if source:
            lines.append(f"Подпись автора ролика{f' ({author})' if author else ''}: «{source[:2500]}»")
        facts = "\n".join(lines)
    redo = redo if redo in REDO_TEXT else "all"
    if rejected:
        old_head, old_body = _post_parts(*rejected)
        rejected = REDO_TEXT[redo].format(title=old_head, caption=" ".join(old_body.split()))
    tags_max = int(tx.get("hashtags", MAX_TAGS))
    emoji = bool(tx.get("title_emoji"))
    rules = tx.get("rules") or ""
    rules = ("\n".join(rules) if isinstance(rules, list) else str(rules)).strip()
    mem = memory(tpl["id"])
    prompt = WRITE.format(
        channel=tpl.get("name", tpl["id"]), about=mem["about"] or tx.get("about", ""),
        taste=_taste(mem), rules=(f"Правила канала:\n{rules}\n" if rules else ""),
        language=tx.get("language", "español"), facts=facts,
        user_title=(f"Заголовок уже выбран: «{user_title}». Оставь его как есть "
                    f"(title — он же, variants — пустой список), напиши к нему описание.\n"
                    if user_title else ""),
        rejected=rejected or "",
        title_chars=tx.get("title_chars", 70), title_rule=tx.get("title_rule", ""),
        examples="\n".join(f"- {e}" for e in tx.get("examples", [])),
        emoji=("   title_emoji — один уместный тематический эмодзи к заголовку: он встанет перед "
               "заголовком в тексте поста (на видео его не будет); не подобрать — пусто.\n"
               if emoji else "   title_emoji — пусто.\n"),
        caption_style=tx.get("caption_style", CAPTION_STYLE),
        caption_examples="\n".join(f"- {e}" for e in tx.get("caption_examples", [])) or "-",
        caption_chars=tx.get("caption_chars", 450),
        hashtags=(f"{min(3, tags_max)}–{tags_max} хэштегов по теме." if tags_max
                  else "пустой список: хэштегов в посте у канала нет."))
    limit = postspec.LIMITS["threads"]
    extra, why = "", ""
    for attempt in (1, 2):
        data, why = _model_json(lambda: vision.ask_text(prompt + extra, 1500, 0.8, 120),
                                TEXT_PAUSES)
        if data is None:
            break
        raw = user_title or str(data.get("title") or "")
        title = _clean_title(raw, font)
        body = keep if redo == "title" else str(data.get("caption") or "").strip()
        if not title or not body:
            why = "пустой заголовок или описание"
            continue
        sign = str(data.get("title_emoji") or "").strip() if emoji else ""
        sign = sign if len(sign) <= 8 and re.fullmatch(f"(?:{EMOJI.pattern})+", sign) else ""
        if redo == "caption" and keep:
            head = keep                               # первая строка поста — как была
        else:
            head = _clean_title(raw, font, upper=bool(tx.get("post_title_upper")))
            head = f"{sign} {head}" if sign and head else head
        tags = [] if redo == "title" else data.get("hashtags") or []
        tags = tags.split() if isinstance(tags, str) else tags
        tags = ["#" + re.sub(r"\W", "", str(t)) for t in tags if re.sub(r"\W", "", str(t))]
        tags = tags[:tags_max]
        # Обороты, которых в канале быть не должно («en el video», «es una
        # señal»), и описание длиннее, чем влезет в пост (обрезка съела бы
        # вопрос в конце): один раз просим переписать, второй раз — берём как есть.
        wrong = []
        found = _avoided(raw if redo == "title" else f"{raw}\n{body}", tx.get("avoid", []))
        if found:
            wrong.append("были обороты, которых быть не должно: "
                         + ", ".join(f"«{w}»" for w in found) + ". Перепиши без них")
        most = int(tx.get("title_chars", 70))
        if not user_title and _utf16(_plain(title)) > most:
            wrong.append(f"заголовок «{_plain(raw)}» длиннее {most} знаков. Короче")
        room = limit - _utf16(head.replace(" | ", " ")) - 2 - (_utf16(" ".join(tags)) + 2 if tags else 0)
        if redo != "title" and _utf16(body) > room:
            wrong.append(f"описание длиннее, чем помещается в пост: {_utf16(body)} знаков из "
                         f"{room}. Сократи, вопрос в конце оставь")
        if wrong and attempt == 1:
            extra = "\n\nВ прошлом ответе " + "; ".join(wrong) + "."
            continue
        caption = _clean_caption(body + "\n" + " ".join(tags), limit, head=head, max_tags=tags_max)
        variants, seen = [], {_plain(raw).lower()}
        for v in ([] if user_title else data.get("variants") or []):
            v = _clean_title(v, font, upper=False)
            if v and _plain(v).lower() not in seen and _utf16(v) <= tx.get("title_chars", 70) + 10:
                seen.add(_plain(v).lower())
                variants.append(v)
        return {"title": title, "caption": caption, "variants": variants[:MAX_VARIANTS],
                "title_ru": _plain(data.get("title_ru")), "caption_ru": _plain(data.get("caption_ru")),
                "theme": str((look or {}).get("theme") or "")[:200], "facts": facts}
    raise MontageError(f"модель не написала заголовок и описание ({why})")


# --------------------------------------------------------------- очередь

_QUEUE = queue.Queue()
_THREAD = {"t": None, "resumed": False}
_LOCK = threading.Lock()


def start():
    """Поднять поток монтажа (один на процесс). Незаконченное после
    перезапуска программы подхватывается заново."""
    with _LOCK:
        worker = _THREAD["t"]
        if worker is not None and worker.is_alive():
            return
        _THREAD["t"] = threading.Thread(target=_loop, daemon=True, name="montage")
        _THREAD["t"].start()


def _home():
    import telegram_bot as tb

    return tb.shelf_home("montage")


def _loop():
    home = _home()
    if home and not _THREAD["resumed"]:
        _THREAD["resumed"] = True
        for row in jobs.montage_unfinished(home[0]):
            _QUEUE.put(row["id"])
    while True:
        try:
            task = _QUEUE.get(timeout=600)
        except queue.Empty:
            _check_health()
            continue
        try:
            process(task)
        except Exception as e:                       # поток не должен умирать
            print(f"[монтаж] #{task}: {type(e).__name__}: {e}", flush=True)


def _say_to(chat, thread):
    import telegram_bot as tb

    topic = tb.GENERAL if not thread or thread == tb.GENERAL else int(thread)

    def say(text):
        tb.send(text, preview=False, chat_id=chat, thread=topic)
    return say


def _check_health():
    import linkfetch

    home = _home()
    if not home or not linkfetch.health_due():
        return
    say = _say_to(home[0], home[1])
    error = linkfetch.health(os.path.join(WORK_DIR, "health"), say=say)
    if error is not None:
        say("⚠ Проверка скачивания: " + linkfetch.explain(error))


def enqueue(url="", file_id="", note="", chat="", thread="", msg=0):
    task = jobs.montage_add(url=url, file_id=file_id, note=note, chat=str(chat),
                            thread=str(thread or ""), msg=msg)
    _QUEUE.put(task)
    start()
    return task


def process(task_id):
    """Один заказ от начала до «Запасов». Ошибка — в чат, не наружу."""
    import linkfetch
    import telegram_bot as tb

    row = jobs.montage_get(task_id)
    if not row or row["status"] == "done":
        return
    say = _say_to(row["chat"], row["thread"])
    # Перемонтаж кнопкой: прежнее видео лежит в запасах, пока не встанет новое.
    # Не вышло — заказ остаётся готовым (старое видео на месте), и кнопки
    # снова под ошибкой: можно попробовать ещё раз.
    redo = bool(row["stock_msg"])
    jobs.montage_set(task_id, status="running", error="")
    work = os.path.join(WORK_DIR, f"task{task_id}")
    try:
        title, stock, texts, note = _make(row, work, say)
    except Exception as e:
        if isinstance(e, linkfetch.FetchError):
            error, text = f"{e.kind}: {e.detail}", "не скачал. " + linkfetch.explain(e)
        elif isinstance(e, MontageError):
            error = text = str(e)
        else:
            error = f"{type(e).__name__}: {e}"
            text = "ошибка монтажа: " + error
        jobs.montage_set(task_id, status="done" if redo else "failed", error=error[:500],
                         done_at=time.time(), redo="")
        if redo:
            _say_buttons(row, f"❌ #{task_id}: не переделал — {text}\nВ запасах всё осталось "
                         "как было.", _buttons(task_id))
        else:
            say(f"❌ #{task_id}: {text}")
        return
    finally:
        shutil.rmtree(work, ignore_errors=True)
    _say_buttons(row, done_text(task_id, stock, title, texts, note), _buttons(task_id))


def done_text(task_id, stock, title, texts, note=""):
    """«✅ готово»: заголовок на видео и перевод на русский — пост на
    испанском, а проверить его надо сразу."""
    lines = [f"✅ #{task_id} в «{stock}»: {_plain(title)}"]
    if note:
        lines.append(note)
    ru = [s for s in (texts.get("title_ru"), texts.get("caption_ru")) if s]
    if ru:
        lines += ["", "По-русски:"] + ru
    return "\n".join(lines)


# Кнопки под «✅ готово» (просьба 2026-11-27: «одобрить кнопкой либо
# перегенерировать — заголовок, описание или полностью»). Код — в
# callback_data: «m:7:ok», «m:7:t», «m:7:c», «m:7» — всё заново. «m:7:2» —
# кнопки другого заголовка под прежними «готово»: они работают и дальше.
REDO_SAY = {
    "title": "пишу новый заголовок и перемонтирую — описание оставлю, прежнее видео в запасах "
             "заменю.",
    "caption": "пишу новое описание — поменяю подпись у видео в запасах, ролик тот же.",
    "caption_slow": "пишу новое описание и перемонтирую с тем же заголовком — прежнее видео в "
                    "запасах заменю.",
    "all": "перемонтирую с другим заголовком и описанием — прежнее видео в запасах заменю.",
}


def _buttons(task_id):
    def button(text, code=""):
        return {"text": text, "callback_data": f"m:{task_id}{code}"}

    rows = [[button("✅ Одобрить", ":ok")],
            [button("🔄 Заголовок", ":t"), button("🔄 Описание", ":c")],
            [button("🔄 Всё заново")]]
    return json.dumps({"inline_keyboard": rows}, ensure_ascii=False)


def _stored_variants(row):
    try:
        return [v for v in json.loads(row["variants"] or "[]") if isinstance(v, str)]
    except ValueError:
        return []


def _say_buttons(row, text, markup):
    import telegram_bot as tb

    topic = tb.GENERAL if not row["thread"] or row["thread"] == tb.GENERAL else int(row["thread"])
    tb.send(text, preview=False, chat_id=row["chat"], thread=topic, markup=markup)


def on_button(data, chat_id, message_id):
    """Кнопка под «✅ готово». «Одобрить» — заголовок и описание в принятое
    памяти шаблона; «Заголовок», «Описание», «Всё заново» — не зашедшее в
    отвергнутое и переделка: видео в запасах заменяется новым, а новое
    описание встаёт подписью к тому же видео. Возвращает всплывающую подсказку."""
    import telegram_bot as tb

    try:
        parts = data.split(":")
        task_id = int(parts[1])
        code = parts[2] if len(parts) > 2 else ""
    except (IndexError, ValueError):
        return "не понял кнопку"
    if code not in ("", "ok", "t", "c") and not code.isdigit():
        return "не понял кнопку"
    row = jobs.montage_get(task_id)
    if not row or str(row["chat"]) != str(chat_id):
        return "этого заказа уже нет"
    if row["status"] in ("queued", "running"):
        return "уже переделываю — пришлю, как будет готово"
    if row["status"] != "done" or not row["stock_msg"]:
        return "этот ролик не смонтирован — пришли ссылку ещё раз"
    title = ""
    if code.isdigit():
        variants = _stored_variants(row)
        if not 0 < int(code) <= len(variants):
            return "этого варианта уже нет"
        title = variants[int(code) - 1]
    redo = {"t": "title", "c": "caption"}.get(code, "all")
    # Память шаблона: одобренное — принято; заменённый заголовок, описание
    # или весь текст — отвергнуты; выбранный кнопкой заголовок — принят.
    if row["tpl"]:
        try:
            if code == "ok":
                remember(row["tpl"], "accepted", row["title"], row["caption"], row["theme"])
            elif title or redo == "title":
                old = _post_parts(row["title"], row["caption"])[0]       # без описания
                remember(row["tpl"], "rejected", old, theme=row["theme"])
                if title:
                    remember(row["tpl"], "accepted", title, theme=row["theme"])
            else:
                remember(row["tpl"], "rejected", row["title"], row["caption"], row["theme"],
                         what="caption" if redo == "caption" else "")
        except OSError as e:
            print(f"[монтаж] память шаблона: {e}", flush=True)
    # Кнопки убираем: второе нажатие не закажет переделку дважды, а
    # одобренное больше не переделать.
    tb._call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id,
                                        "reply_markup": json.dumps({"inline_keyboard": []})},
             timeout=20)
    if code == "ok":
        return "одобрено — запомнил как удачное"
    jobs.montage_set(task_id, status="queued", pick=title, redo="" if title else redo, error="")
    if title:
        text = f"перемонтирую с заголовком «{_plain(title)}» — прежнее видео в запасах заменю."
    else:
        text = REDO_SAY["caption_slow" if redo == "caption" and not row["facts"] else redo]
    _say_to(row["chat"], row["thread"])(f"🔄 #{task_id}: {text}")
    _QUEUE.put(task_id)
    start()
    return "пишу новое описание" if redo == "caption" and not title else "перемонтирую"


def _recaption(row, stock, tpl):
    """«🔄 Описание»: новое описание — подписью к тому же видео в запасах, без
    скачивания и перемонтажа. Первая строка поста (заголовок) — как была."""
    import telegram_bot as tb

    head, _ = _head_rest(row["title"], row["caption"])
    texts = write_texts(tpl, "", user_title=row["title"], rejected=(row["title"], row["caption"]),
                        redo="caption", facts=row["facts"], keep=head)
    res = tb._call("editMessageCaption", {"chat_id": stock[0], "message_id": row["stock_msg"],
                                          "caption": texts["caption"]}, timeout=30)
    if not res.get("ok"):
        raise MontageError(f"подпись у видео в «{stock[2]}» не поменял (Telegram: {tb._why(res)}) — "
                           "может, его уже опубликовали или удалили")
    jobs.montage_set(row["id"], status="done", done_at=time.time(), caption=texts["caption"],
                     pick="", redo="")
    return row["title"], stock[2], texts, "Описание у видео в запасах заменил — ролик тот же."


def _make(row, work, say):
    import linkfetch
    import scheduler
    import telegram_bot as tb

    stock = tb.shelf_home("stock")
    if not stock:
        raise MontageError("«Запасы» не назначены — некуда класть готовое. Назови тему "
                           "«Запасы» и положи в неё что-нибудь — или /here запасы в ней.")
    # Тот же выбор, что видно в окне: выбранный шаблон удалили — основной.
    tpl = load_template(tb.montage_templates()["montage_template"] or None)
    # Переделка кнопкой: что именно. Новое описание при сохранённом разборе
    # ролика — без скачивания и перемонтажа, подписью к тому же видео.
    redo = (row["redo"] or "all") if row["stock_msg"] and not row["pick"] else ""
    if redo == "caption" and row["facts"]:
        return _recaption(row, stock, tpl)
    tools = linkfetch.ffmpeg_tools()
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work, exist_ok=True)

    user_title = ""
    if row["url"]:
        path, info, notes = linkfetch.fetch(row["url"], work, say=say)
        if notes:
            say("🔧 " + "; ".join(notes) + ".")
        source = str(info.get("description") or info.get("title") or "")
        author = str(info.get("uploader") or info.get("channel") or "")
        user_title = row["note"].strip()
        if REDO_WORDS.match(user_title):
            user_title = ""
    else:
        path = tb._download(row["file_id"], work)
        if not path:
            raise MontageError("не смог взять видео из Telegram"
                               + (f": {tb.DOWNLOAD['why']}" if tb.DOWNLOAD.get("why") else ""))
        source, author = row["note"], ""
    # Заголовок, выбранный кнопкой под «✅ готово», — как свой заголовок
    # человека; переделка — модели показываем, что не зашло.
    # «Заголовок» — свой заголовок человека тоже меняем, «Описание» — заголовок
    # прежний; разбор ролика берём из прошлого раза, если он есть.
    user_title = row["pick"] or {"title": "", "caption": row["title"]}.get(redo, user_title)
    rejected = (row["title"], row["caption"]) if redo else None
    head, rest = _head_rest(row["title"], row["caption"])
    keep = {"title": rest, "caption": head}.get(redo, "")
    facts = row["facts"] if redo in ("title", "caption") and row["facts"] else None
    why = scheduler.looks_like_video(path)
    if why:
        raise MontageError(f"скачалось не видео: {why}")

    meta = probe(path, tools[1], tools[0])
    board = b"" if facts else storyboard(path, tools[0], meta)
    texts = write_texts(tpl, source, author, board, user_title, rejected, redo=redo or "all",
                        facts=facts, keep=keep)
    title, caption = texts["title"], texts["caption"]

    out = os.path.join(work, "montage.mp4")
    cap = None if tb.max_download_mb() >= 2000 else CLOUD_UPLOAD_MB
    render(path, title, tpl, out, tools, meta, cap_mb=cap)
    size_mb = os.path.getsize(out) / 1024 / 1024
    if cap and size_mb > 49.5:
        raise MontageError(f"готовое видео {size_mb:.0f} МБ, а через облако Telegram бот "
                           "отправляет до 50 МБ. Впиши api_id и api_hash во вкладке «Бот» — "
                           "свой сервер примет до 2 ГБ.")

    width, height = tpl["size"]
    res = tb.send_video(out, caption, chat_id=stock[0], thread=stock[1], width=width,
                        height=height, duration=int(meta["duration"]))
    if not res.get("ok"):
        raise MontageError(f"Telegram не принял видео в «{stock[2]}»: {tb._why(res)}")
    sent = res.get("result") or {}
    video = sent.get("video") or sent.get("document") or {}
    # Свои сообщения бот не получает, поэтому `_remember_stock` это видео не
    # увидит — записываем сами, иначе после публикации оно не уйдёт в архив.
    jobs.stock_add(video.get("file_unique_id", ""), stock[0],
                   "" if stock[1] == tb.GENERAL else stock[1], sent.get("message_id"),
                   str((sent.get("from") or {}).get("id", "")), sent.get("date"))
    note = ""
    if row["stock_msg"]:
        # Перемонтаж: новое видео встало — прежнее из запасов убираем.
        res = tb._call("deleteMessage", {"chat_id": stock[0], "message_id": row["stock_msg"]},
                       timeout=30)
        if res.get("ok") or "not found" in tb._why(res).lower():
            jobs.stock_drop(stock[0], row["stock_msg"])
            note = "Прежнее видео из запасов убрал." if res.get("ok") else ""
        else:
            note = (f"Прежнее видео из «{stock[2]}» удалить не смог (Telegram: {tb._why(res)}) — "
                    "удали вручную.")
    jobs.montage_set(row["id"], status="done", stock_msg=sent.get("message_id") or 0,
                     done_at=time.time(), title=title, caption=caption, pick="",
                     variants=json.dumps(texts["variants"], ensure_ascii=False),
                     tpl=tpl["id"], theme=texts.get("theme") or row["theme"] or "",
                     out_file=video.get("file_unique_id", ""), redo="",
                     facts=texts.get("facts") or "")
    return title, stock[2], texts, note


# -------------------------------------------------------- сообщения из темы

def _cookies_doc(msg):
    doc = msg.get("document") or {}
    name = str(doc.get("file_name") or "").lower()
    return doc if name.endswith(".txt") and ("cookie" in name or "instagram" in name) else None


LOGIN_WORDS = re.compile(r"^(/login|/вход|вход|войти|логин)$", re.I)
_LOGIN = {"busy": False}


def _start_login(say):
    """«/login»: окно входа Instagram на компьютере (`iglogin`), ждём в фоне —
    бот тем временем отвечает остальным."""
    import iglogin

    if _LOGIN["busy"]:
        say("Окно входа уже открыто на компьютере — войди в нём.")
        return
    if not iglogin.browser():
        say("Не нашёл на компьютере ни Edge, ни Chrome — окно входа открыть нечем. "
            "Пришли cookies.txt файлом.")
        return
    _LOGIN["busy"] = True

    def run():
        try:
            say("Открыл на компьютере, где работает программа, окно входа Instagram. "
                "Войди в ЗАПАСНОЙ аккаунт — дальше я сам (жду до 10 минут).")
            say("✅ " + iglogin.login() + " Окно закрыл — можно кидать ссылки.")
        except (iglogin.LoginError, ValueError) as e:
            say(f"Вход не забрал: {e}.")
        except Exception as e:
            say(f"Вход не забрал: {type(e).__name__}: {e}.")
        finally:
            _LOGIN["busy"] = False

    threading.Thread(target=run, daemon=True, name="ig-login").start()


def on_message(msg, text, chat_id, thread, sender, owner):
    """Сообщение в теме «Монтаж». Остальное в теме — разговор, бот молчит."""
    import linkfetch
    import telegram_bot as tb
    import weblink

    topic = thread or tb.GENERAL
    say = _say_to(chat_id, topic)
    if LOGIN_WORDS.match(text.strip()):
        if str(sender) == str(owner):
            _start_login(say)
        return
    doc = _cookies_doc(msg)
    if doc:
        if str(sender) != str(owner):
            return
        tmp = os.path.join(WORK_DIR, "incoming")
        os.makedirs(tmp, exist_ok=True)
        path = tb._download(doc["file_id"], tmp)
        # В сообщении — вход в аккаунт: убираем из чата сразу, что бы ни вышло.
        tb._call("deleteMessage", {"chat_id": chat_id, "message_id": msg.get("message_id")},
                 timeout=20)
        if not path:
            say("Не смог взять файл cookies из Telegram — пришли ещё раз.")
            return
        try:
            with open(path, "rb") as f:
                raw = f.read()
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        try:
            say(linkfetch.save_cookies(raw) + " Сообщение с ними удалил.")
        except ValueError as e:
            say(f"Не принял cookies: {e}. Сообщение удалил.")
        return

    urls = weblink.find_urls(text, msg.get("entities") or msg.get("caption_entities"))
    video = msg.get("video") or msg.get("document") or msg.get("animation")
    if urls:
        note = weblink.strip_urls(text).strip()
        for url in urls[:MAX_LINKS]:
            twin = jobs.montage_twin(url)
            if twin and not REDO_WORDS.match(note):
                state = {"done": "уже смонтирован и лежит в запасах",
                         "running": "монтируется прямо сейчас",
                         "queued": "уже в очереди"}[twin["status"]]
                say(f"Этот ролик {state} — #{twin['id']}. Смонтировать ещё раз — пришли "
                    "ссылку со словом «заново».")
                continue
            task = enqueue(url=url, note=note, chat=chat_id, thread=topic,
                           msg=msg.get("message_id"))
            say(f"🎬 Взял в монтаж: #{task}. Готовое положу в запасы.")
        if len(urls) > MAX_LINKS:
            say(f"Больше {MAX_LINKS} ссылок за раз не беру — остальные пришли следующим сообщением.")
        return
    if video and tb._is_video(msg, video):
        task = enqueue(file_id=video["file_id"], note=text, chat=chat_id, thread=topic,
                       msg=msg.get("message_id"))
        say(f"🎬 Взял в монтаж: #{task}. Готовое положу в запасы.")
