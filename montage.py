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
  * заголовок и описание пишет модель (`vision`) по исходной подписи ролика,
    а без подписи — по кадру из него;
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
    size = t["size"]
    while True:
        lines = [line for words in parts
                 for line in _wrap(words, font, size, t["max_width"], glue)]
        if len(lines) <= t["max_lines"] or size <= t["min_size"]:
            break
        size -= 1
    if len(lines) > t["max_lines"] and len(parts) > 1:
        # Перенос модели не влез — переносим целиком по ширине.
        return layout_title(" ".join(w for words in parts for w in words), tpl)
    return size, lines


def fit_video(src_w, src_h, tpl):
    """Размер и место ролика в кадре: во всю ширину, но не выше `max_height`.
    Чётные числа — их требует yuv420p."""
    width, height = tpl["size"]
    box = tpl["video"]
    scale = min(width / src_w, box["max_height"] / src_h)
    w = max(2, int(src_w * scale) // 2 * 2)
    h = max(2, int(src_h * scale) // 2 * 2)
    x = (width - w) // 2
    y = int(round(box["center_y"] - h / 2)) // 2 * 2
    return w, h, x, y


def title_baselines(count, size, video_top, tpl):
    t = tpl["title"]
    pitch = round(size * t["line_height"])
    last = video_top - t["gap"]
    return [last - pitch * (count - 1 - i) for i in range(count)]


# -------------------------------------------------------------------- ffmpeg

def _run(cmd, timeout, cwd=None):
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=timeout, cwd=cwd,
                             creationflags=LOW_PRIORITY | NO_WINDOW)
    except subprocess.TimeoutExpired as e:
        raise MontageError(f"ffmpeg не уложился в {timeout // 60} мин") from e
    except OSError as e:
        raise MontageError(f"ffmpeg не запустился: {e}") from e
    return res


def probe(path, ffprobe):
    """Ширина и высота КАК ПОКАЗЫВАЕТСЯ (с поворотом и формой пикселя),
    длительность и есть ли звук."""
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


def frame_png(path, ffmpeg, meta):
    """Кадр из середины ролика — для модели, когда исходной подписи нет."""
    at = max(0.0, meta.get("duration", 0) * 0.4)
    res = _run([ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{at:.2f}", "-i", path,
                "-frames:v", "1", "-vf", "scale=720:-2", "-f", "image2pipe", "-c:v", "png", "-"], 120)
    return res.stdout if res.stdout[:4] == b"\x89PNG" else b""


def _color(value):
    return "0x" + str(value).lstrip("#").upper()


def render(src, title, tpl, out, tools, meta=None, cap_mb=None):
    """Смонтировать ролик по шаблону в `out` (mp4, H.264 + AAC).

    Всё, что рисуется, кладётся в рабочую папку рядом с `out` и зовётся
    относительными именами: в фильтрах ffmpeg двоеточие пути Windows («C:»)
    пришлось бы экранировать в два слоя, а текст заголовка идёт файлом
    (`textfile`, без разбора `%`), чтобы кавычки и запятые не ломали фильтр."""
    ffmpeg, ffprobe = tools
    meta = meta or probe(src, ffprobe)
    width, height = tpl["size"]
    fps = int(tpl.get("fps", 30))
    vw, vh, vx, vy = fit_video(meta["w"], meta["h"], tpl)
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

    accent = t.get("accent") == "first_line" and len(lines) > 1
    for i, (line, base) in enumerate(zip(lines, title_baselines(len(lines), size, vy, tpl))):
        color = t["accent_color"] if accent and i == 0 else t["color"]
        draw(line, t["font"], size, color, "(w-text_w)/2", base)
    for line, base in zip(badge.get("lines", []), badge.get("baselines", [])):
        draw(line, badge["font"], badge["size"], badge["color"], badge["x"], base)

    graph = [f"color=c={_color(tpl.get('background', '#000000'))}:s={width}x{height}:r={fps}[bg]",
             f"[0:v]scale={vw}:{vh}:flags=lanczos,setsar=1,fps={fps}[v]",
             f"[bg][v]overlay={vx}:{vy}:shortest=1[s0]"]
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
    res = _run(cmd, max(600, int(meta["duration"] * 30)), cwd=work)
    if res.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) < 1024:
        tail = res.stderr.decode("utf-8", "replace").strip().splitlines()[-3:]
        raise MontageError("ffmpeg не смонтировал: " + (" / ".join(tail) or f"код {res.returncode}"))
    shutil.rmtree(work, ignore_errors=True)
    return {"size": size, "lines": lines, "video": (vw, vh, vx, vy), "meta": meta}


# ------------------------------------------------------------------- тексты

PROMPT = """Ты редактор канала «{channel}» — {about}. Пишешь на языке: {language}.
Мы публикуем у себя ролик со своим оформлением: сверху на видео — заголовок, под постом — описание.

{source}
{user_title}
Нужно:
1. title — заголовок поверх видео. Коротко и по фактам: кто и что сделал. Не длиннее {title_chars} знаков, без эмодзи, кавычек, хэштегов и точки в конце. {title_rule} Примеры заголовков канала:
{examples}
2. caption — описание поста: {caption_style} Не длиннее {caption_chars} знаков.
Не выдумывай фактов, которых нет в подписи или на кадре. Без ссылок, @упоминаний и призывов подписаться.

Ответ — только JSON: {{"title": "...", "caption": "..."}}"""

EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")


def _clean_title(text, font):
    text = EMOJI.sub("", str(text or ""))
    text = re.sub(r"#\w+", "", text)
    text = text.replace("«", "").replace("»", "").replace('"', "").replace("“", "").replace("”", "")
    text = re.sub(r"\s*\|\s*", " | ", text)
    text = re.sub(r"\s+", " ", text).strip().strip("|").strip().rstrip(".").strip()
    text = text.upper()
    return "".join(ch for ch in text if ch in " |" or font.has(ch))


def _utf16(text):
    return len(text.encode("utf-16-le")) // 2


def _clean_caption(text, limit):
    """Описание без ссылок и @упоминаний, в пределах `limit` знаков UTF-16
    (так считает Threads — самый строгий из списка, 500)."""
    text = re.sub(r"https?://\S+", "", str(text or ""))
    text = re.sub(r"(?<!\w)@[\w.]+", "", text)
    text = re.sub(r"[ \t]+", " ", text).strip()
    if _utf16(text) <= limit:
        return text
    tags = " ".join(re.findall(r"#\w+", text)[:4])
    body = re.sub(r"#\w+", "", text).strip()
    room = limit - (_utf16(tags) + 2 if tags else 0)
    cut = body
    while _utf16(cut) > room:
        stop = max(cut.rfind(". ", 0, len(cut) - 1), cut.rfind("! ", 0, len(cut) - 1),
                   cut.rfind("? ", 0, len(cut) - 1))
        cut = cut[:stop + 1] if stop > 0 else cut[:room - 1].rstrip() + "…"
    return (cut.strip() + ("\n\n" + tags if tags else "")).strip()


def write_texts(tpl, source, author="", frame=b"", user_title=""):
    """(заголовок, описание) от модели. Свой заголовок человека не меняется.
    Модель не ответила за три попытки — MontageError с причиной."""
    import vision

    tx = tpl["texts"]
    font = Font.get(os.path.join(tpl["dir"], tpl["title"]["font"]))
    if source.strip():
        what = f"Исходная подпись автора ролика{f' ({author})' if author else ''}:\n«{source.strip()[:2500]}»"
    elif frame:
        what = "Подписи у ролика нет — суди по кадру из него (картинка)."
    else:
        raise MontageError("у ролика нет ни подписи, ни кадра — не из чего писать заголовок")
    prompt = PROMPT.format(
        channel=tpl.get("name", tpl["id"]), about=tx.get("about", ""),
        language=tx.get("language", "español"), source=what,
        user_title=(f"Заголовок уже дан человеком: «{user_title}». Оставь его как есть, "
                    f"напиши к нему описание.\n" if user_title else ""),
        title_chars=tx.get("title_chars", 70), title_rule=tx.get("title_rule", ""),
        examples="\n".join(f"- {e}" for e in tx.get("examples", [])),
        caption_style=tx.get("caption_style", "1–3 коротких предложения о том, что в ролике, "
                                              "и 3–5 хэштегов в конце."),
        caption_chars=tx.get("caption_chars", 450))
    why = ""
    for pause in TEXT_PAUSES:
        time.sleep(pause)
        try:
            if source.strip():
                answer = vision.ask_text(prompt, 600, 0.4, 120)
            else:
                answer = vision.ask(frame, prompt, max_tokens=600, temperature=0.4, timeout=120)
        except Exception as e:                       # VisionError, сеть, оплата (402)
            why = f"{type(e).__name__}: {e}"[:200]
            continue
        data = vision._json_from(answer or "") if answer else None
        if not isinstance(data, dict):
            why = "ответила не по форме"
            continue
        title = _clean_title(user_title or data.get("title"), font)
        caption = _clean_caption(data.get("caption"), int(tx.get("caption_chars", 450)))
        if title and caption:
            return title, caption
        why = "пустой заголовок или описание"
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
    jobs.montage_set(task_id, status="running", error="")
    work = os.path.join(WORK_DIR, f"task{task_id}")
    try:
        title, stock = _make(row, work, say)
    except linkfetch.FetchError as e:
        jobs.montage_set(task_id, status="failed", error=f"{e.kind}: {e.detail}"[:500],
                         done_at=time.time())
        say(f"❌ #{task_id}: не скачал. " + linkfetch.explain(e))
        return
    except MontageError as e:
        jobs.montage_set(task_id, status="failed", error=str(e)[:500], done_at=time.time())
        say(f"❌ #{task_id}: {e}")
        return
    except Exception as e:
        jobs.montage_set(task_id, status="failed", error=f"{type(e).__name__}: {e}"[:500],
                         done_at=time.time())
        say(f"❌ #{task_id}: ошибка монтажа: {type(e).__name__}: {e}")
        return
    finally:
        shutil.rmtree(work, ignore_errors=True)
    say(f"✅ #{task_id} в «{stock}»: {title.replace(' | ', ' ')}")


def _make(row, work, say):
    import linkfetch
    import scheduler
    import telegram_bot as tb

    stock = tb.shelf_home("stock")
    if not stock:
        raise MontageError("«Запасы» не назначены — некуда класть готовое. Назови тему "
                           "«Запасы» и положи в неё что-нибудь — или /here запасы в ней.")
    tpl = load_template(tb._saved().get("montage_template") or None)
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
    why = scheduler.looks_like_video(path)
    if why:
        raise MontageError(f"скачалось не видео: {why}")

    meta = probe(path, tools[1])
    frame = b"" if source.strip() else frame_png(path, tools[0], meta)
    title, caption = write_texts(tpl, source, author, frame, user_title)
    jobs.montage_set(row["id"], title=title, caption=caption)

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
    jobs.montage_set(row["id"], status="done", stock_msg=sent.get("message_id") or 0,
                     done_at=time.time())
    return title, stock[2]


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
