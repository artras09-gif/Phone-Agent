"""Скачать ролик по ссылке — yt-dlp, который сам себя чинит.

Нужен монтажёру (`montage.py`, просьба 2026-11-25): в тему «Монтаж» кидают
ссылку на Instagram, бот скачивает ролик и монтирует его по шаблону.

Почему yt-dlp и почему не pip: проект живёт на стандартной библиотеке, а
yt-dlp выходит готовыми файлами на GitHub — их программа берёт сама, сверяет
контрольную сумму из того же выпуска и кладёт в `montage/bin`.

Два вида одного и того же yt-dlp, и оба нужны:
  * `yt-dlp` — сам код на Python одним zip-файлом. Запускается нашим же
    Python (в сборке — самим PhoneAgent.exe: `PhoneAgent.exe ytdlp …`, см.
    `main.py`). Адреса сайтов берёт у Windows.
  * `yt-dlp.exe` — со своим сетевым движком curl_cffi, который умеет
    притворяться браузером. Но адреса он ищет сам, мимо Windows.
Живьём 25.11 на этом ПК: Windows ходит к DNS 1.1.1.1 шифрованно (DoH), а
обычные DNS-запросы сюда не проходят — и `yt-dlp.exe` падал на Instagram с
«Resolving timed out», а zip-версия той же сборки дошла до сайта. На другом
ПК бывает наоборот, поэтому пробуются оба.

Как чинится (что сработало — запоминается в `montage/fetch.json`):
  1. сначала способ, который сработал в прошлый раз;
  2. сбой «Instagram что-то поменял» — свериться с выпуском и обновиться,
     потом ещё раз;
  3. дальше — ночная сборка yt-dlp: исправления появляются в ней раньше;
  4. «нужен вход» (429, login required) — без cookies запасного аккаунта
     не пройти, это честно говорится человеку.
Вышла новая обычная сборка — возвращаемся к ней с ночной.

Причину сбоя определяют правила по тексту ошибки; незнакомый текст — модель
выбирает причину из того же короткого списка (`_ask_model`). Код скачивалки
модель не правит и ничего, кроме выбора из списка, не делает.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile

import config

HOME = os.path.join(config.BASE, "montage")
BIN = os.path.join(HOME, "bin")
STATE_FILE = os.path.join(HOME, "fetch.json")
# Cookies ЗАПАСНОГО аккаунта Instagram — тайна, как токен бота: лежит только
# здесь (папка montage/ в .gitignore), в журнал и в чат не печатается.
COOKIES = os.path.join(HOME, "cookies.txt")

RELEASES = {
    "stable": "https://github.com/yt-dlp/yt-dlp/releases/latest/download/",
    "nightly": "https://github.com/yt-dlp/yt-dlp-nightly-builds/releases/latest/download/",
}
FILES = {"py": "yt-dlp", "exe": "yt-dlp.exe"}
# Порядок проб, если ничего не запомнено: обычная сборка раньше ночной,
# zip раньше exe (см. выше про DNS).
METHODS = ("stable-py", "stable-exe", "nightly-py", "nightly-exe")
# Раз в сутки сверяемся с выпуском: Instagram ломает скачивалки раз в
# несколько недель, а исправление обычно выходит за день-два.
FRESH_SEC = 24 * 3600
# Проверка заранее, пока ссылок не присылали (`health`).
HEALTH_SEC = 24 * 3600
RUN_TIMEOUT = 15 * 60

# ffmpeg для склейки дорожек и монтажа, если его нет на ПК (на этом стоит
# через winget). Сборка gyan.dev — её и советует сайт ffmpeg для Windows;
# в «essentials» есть libfreetype, без которой не нарисовать заголовок.
FFMPEG_ZIP = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class FetchError(RuntimeError):
    """Не скачалось. `kind` — причина из `EXPLAIN`, `detail` — последняя
    строка ошибки как есть, `tried` — [(способ, причина, строка)]."""

    def __init__(self, kind, detail="", tried=None):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail
        self.tried = tried or []


# --------------------------------------------------------------- состояние

def _state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(state):
    os.makedirs(HOME, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_FILE)


def _update(**fields):
    state = _state()
    state.update(fields)
    _save(state)
    return state


# ------------------------------------------------------------ свой yt-dlp

def _get(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": "PhoneAgent"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _download_file(url, dest, timeout=600):
    """Скачать в файл кусками, вернуть sha256. Не в память: ffmpeg ~100 МБ."""
    req = urllib.request.Request(url, headers={"User-Agent": "PhoneAgent"})
    digest = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as out:
        while True:
            chunk = r.read(1024 * 256)
            if not chunk:
                break
            digest.update(chunk)
            out.write(chunk)
    return digest.hexdigest()


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 256), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tool_path(method):
    channel, kind = method.split("-")
    return os.path.join(BIN, channel, FILES[kind])


def ensure(method, refresh=False):
    """Путь к yt-dlp этого способа и обновился ли он сейчас: (путь, True|False).

    Раз в сутки (или по `refresh`) сверяет файл с контрольной суммой свежего
    выпуска и скачивает новый, если тот другой. Нет связи с GitHub, а файл
    уже есть — работаем им: лучше старая скачивалка, чем никакой."""
    channel, kind = method.split("-")
    path = tool_path(method)
    state = _state()
    tools = state.get("tools") or {}
    info = tools.get(method) or {}
    due = refresh or not os.path.exists(path) or \
        time.time() - float(info.get("checked") or 0) > FRESH_SEC
    if not due:
        return path, False
    try:
        sums = _get(RELEASES[channel] + "SHA2-256SUMS", 30).decode("utf-8", "replace")
        want = next((line.split()[0].lower() for line in sums.splitlines()
                     if line.split()[-1:] == [FILES[kind]]), "")
        if not want:
            raise FetchError("tool", f"в выпуске нет {FILES[kind]}")
        if os.path.exists(path) and _sha256(path) == want:
            info.update(checked=time.time(), sha=want)
            tools[method] = info
            _update(tools=tools)
            return path, False
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".new"
        got = _download_file(RELEASES[channel] + FILES[kind], tmp)
        if got != want:
            os.remove(tmp)
            raise FetchError("tool", f"{FILES[kind]} скачался битым: контрольная сумма не сошлась")
        os.replace(tmp, path)
    except FetchError:
        if os.path.exists(path):
            return path, False
        raise
    except (OSError, urllib.error.URLError, ValueError) as e:
        if os.path.exists(path):
            return path, False
        raise FetchError("tool", f"не скачал {FILES[kind]} с GitHub: {e}") from e
    info.update(checked=time.time(), sha=want, at=time.time())
    tools[method] = info
    fields = {"tools": tools}
    if channel == "stable" and state.get("good", "").startswith("nightly"):
        fields["good"] = ""       # вышла новая обычная сборка — снова с неё
    _update(**fields)
    return path, True


def version(method):
    """Версия yt-dlp этого способа или "" (без скачивания)."""
    path = tool_path(method)
    if not os.path.exists(path):
        return ""
    try:
        res = subprocess.run(_argv(method, path) + ["--version"], capture_output=True,
                             timeout=60, creationflags=NO_WINDOW, env=_env())
        return res.stdout.decode("utf-8", "replace").strip().splitlines()[-1]
    except (OSError, subprocess.SubprocessError, IndexError):
        return ""


def _argv(method, path):
    if method.endswith("-exe"):
        return [path]
    if getattr(sys, "frozen", False):
        # В сборке своего python.exe нет — код yt-dlp выполняет сам
        # PhoneAgent.exe (`main.py`, режим `ytdlp`).
        return [sys.executable, "ytdlp", path]
    return [sys.executable, path]


def _env():
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


# ------------------------------------------------------------------ ffmpeg

def ffmpeg_tools():
    """(ffmpeg, ffprobe). Нет на ПК — скачать сборку gyan.dev в montage/bin."""
    own = os.path.join(BIN, "ffmpeg")
    ff, fp = os.path.join(own, "ffmpeg.exe"), os.path.join(own, "ffprobe.exe")
    if os.path.exists(ff) and os.path.exists(fp):
        return ff, fp
    import stream

    found = stream.ffmpeg_exe()
    if found:
        near = os.path.join(os.path.dirname(found), "ffprobe.exe")
        probe = near if os.path.exists(near) else shutil.which("ffprobe")
        if probe:
            return found, probe
    os.makedirs(own, exist_ok=True)
    tmp = os.path.join(own, "ffmpeg.zip")
    try:
        want = _get(FFMPEG_ZIP + ".sha256", 30).decode("ascii", "replace").split()[0].lower()
        got = _download_file(FFMPEG_ZIP, tmp, timeout=1800)
        if got != want:
            raise FetchError("tool", "ffmpeg скачался битым: контрольная сумма не сошлась")
        with zipfile.ZipFile(tmp) as z:
            for name in z.namelist():
                base = os.path.basename(name)
                if base in ("ffmpeg.exe", "ffprobe.exe"):
                    with z.open(name) as src, open(os.path.join(own, base), "wb") as out:
                        shutil.copyfileobj(src, out)
    except FetchError:
        raise
    except (OSError, urllib.error.URLError, ValueError, zipfile.BadZipFile, IndexError) as e:
        raise FetchError("tool", f"ffmpeg не нашёл и не смог скачать: {e}") from e
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    if not (os.path.exists(ff) and os.path.exists(fp)):
        raise FetchError("tool", "в архиве ffmpeg не оказалось ffmpeg.exe и ffprobe.exe")
    return ff, fp


# ----------------------------------------------------------------- cookies

def has_cookies():
    return os.path.exists(COOKIES) and os.path.getsize(COOKIES) > 0


def save_cookies(raw):
    """Принять файл cookies.txt (формат Netscape). Возвращает текст для
    человека или бросает ValueError с понятной причиной. Содержимое никуда
    не печатается."""
    text = raw.decode("utf-8-sig", "replace") if isinstance(raw, bytes) else str(raw)
    rows = [line.split("\t") for line in text.splitlines()
            if line.strip() and (not line.startswith("#") or line.startswith("#HttpOnly_"))]
    rows = [r for r in rows if len(r) == 7]
    insta = [r for r in rows if "instagram.com" in r[0]]
    if not rows:
        raise ValueError("это не файл cookies.txt: нужен формат Netscape — так его "
                         "сохраняют расширения «Get cookies.txt LOCALLY» и «cookies.txt»")
    if not insta:
        raise ValueError("в файле нет cookies Instagram — сохрани их, когда открыт instagram.com")
    if not any(r[5] == "sessionid" and r[6].strip() for r in insta):
        raise ValueError("в файле нет входа в аккаунт (cookie sessionid) — сначала войди "
                         "в запасной аккаунт на instagram.com, потом сохрани cookies")
    if not text.lstrip().startswith("# Netscape HTTP Cookie File") and \
            not text.lstrip().startswith("# HTTP Cookie File"):
        text = "# Netscape HTTP Cookie File\n" + text
    os.makedirs(HOME, exist_ok=True)
    tmp = COOKIES + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, COOKIES)
    expires = [int(r[4]) for r in insta if r[5] == "sessionid" and r[4].isdigit()]
    when = ""
    if expires and expires[0] > 0:
        when = " Вход действует до " + time.strftime("%d.%m.%Y", time.localtime(expires[0])) + "."
    return "Cookies Instagram приняты." + when


# ------------------------------------------------------------- диагностика

# Порядок важен: «Requested content is not available, rate-limit reached or
# login required» — это вход, а не «ролика нет», поэтому вход проверяется
# раньше.
RULES = (
    ("login", r"HTTP Error 429|Too Many Requests|rate-?limit|login required|log ?in to|"
              r"Please log in|checkpoint|challenge_required|HTTP Error 401|"
              r"use --cookies|cookies-from-browser|account authentication"),
    ("unsupported", r"Unsupported URL|is not a valid URL"),
    ("gone", r"isn't available|is not available|no longer available|\bunavailable\b|"
             r"has been removed|This post is unavailable|Private video|HTTP Error 404|"
             r"does not exist"),
    ("network", r"Resolving timed out|timed out|getaddrinfo failed|Name or service not known|"
                r"name resolution|Connection (refused|reset|aborted)|Network is unreachable|"
                r"No route to host|Failed to resolve|RemoteDisconnected|"
                r"CERTIFICATE_VERIFY_FAILED|curl: \(\d+\)"),
    ("broken", r"Unable to extract|KeyError|Traceback|JSONDecodeError|No video formats found|"
               r"Unable to download JSON metadata|Unable to parse|IndexError|TypeError|"
               r"Please report this issue|unable to obtain|HTTP Error 403"),
)
KINDS = ("login", "gone", "network", "broken", "unsupported")

EXPLAIN = {
    "login": "Instagram не отдаёт ролик без входа в аккаунт",
    "login_stale": "Instagram не пустил даже с cookies — вход устарел или аккаунт "
                   "попросил подтверждение",
    "gone": "ролик недоступен: удалён, закрыт или виден только подписчикам",
    "network": "нет связи с сайтом",
    "broken": "скачивалка не справилась даже после обновления — сайт что-то поменял. "
              "Обычно yt-dlp чинят за день-два",
    "unsupported": "такую ссылку скачивалка не знает",
    "tool": "не смог подготовить скачивалку",
    "unknown": "непонятная ошибка скачивания",
}

COOKIES_HOWTO = (
    "Как дать мне вход: напиши здесь /login — на компьютере, где работает программа, "
    "откроется окно входа Instagram. Войди в ЗАПАСНОЙ аккаунт (не рабочий — его могут "
    "заблокировать), остальное я сделаю сам и окно закрою. Если cookies.txt уже есть — "
    "можно прислать его сюда файлом, сообщение я сразу удалю.")


def classify(text):
    """Причина по тексту ошибки или "" — правила не узнали."""
    for kind, pattern in RULES:
        if re.search(pattern, text or "", re.I):
            return kind
    return ""


DIAG_PROMPT = ("Программа скачивает ролик по ссылке через yt-dlp и получила ошибку:\n"
               "«{error}»\n\n"
               "Выбери ОДНУ причину и ответь одним словом из списка:\n"
               "login — сайт требует вход в аккаунт или ограничил запросы;\n"
               "gone — ролик удалён, закрыт или недоступен;\n"
               "network — нет связи, не находится адрес, обрыв соединения;\n"
               "broken — сайт поменялся, скачивалка не может разобрать страницу;\n"
               "unsupported — ссылка не на ролик или сайт не поддерживается.")


def _ask_model(text):
    """Незнакомая ошибка — пусть модель выберет причину из списка. Не вышло — "".
    Модель только называет причину: что делать дальше, решают правила."""
    try:
        import vision

        ok, _ = vision.available()
        if not ok:
            return ""
        answer = vision.ask_text(DIAG_PROMPT.format(error=text[-1500:]), 8, 0.0, 40)
    except Exception:
        return ""
    words = re.findall(r"[a-z]+", str(answer).lower())
    return next((w for w in words if w in KINDS), "")


def _last_line(text):
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    errors = [line for line in lines if line.startswith("ERROR")]
    return (errors or lines or [""])[-1][:300]


# ----------------------------------------------------------------- запуск

def _run(method, path, url, outdir, probe=False):
    """Один запуск yt-dlp. (код, stdout, stderr)."""
    ff = None
    try:
        ff = ffmpeg_tools()[0]
    except FetchError:
        pass
    args = _argv(method, path) + [
        "--no-progress", "--no-playlist", "--no-color",
        "--socket-timeout", "30", "--retries", "3", "--extractor-retries", "2",
    ]
    if ff:
        args += ["--ffmpeg-location", os.path.dirname(ff)]
    if has_cookies():
        args += ["--cookies", COOKIES]
    if probe:
        args += ["--simulate", "--print", "id"]
    else:
        args += ["-f", "bv*+ba/b", "--merge-output-format", "mp4",
                 "-o", os.path.join(outdir, "source.%(ext)s"),
                 "--write-info-json", "--no-write-playlist-metafiles",
                 "--print", "after_move:filepath"]
    args.append(url)
    try:
        res = subprocess.run(args, capture_output=True, timeout=RUN_TIMEOUT,
                             creationflags=NO_WINDOW, env=_env(), cwd=outdir)
    except subprocess.TimeoutExpired:
        return 1, "", "ERROR: timed out — yt-dlp не уложился в 15 минут"
    except OSError as e:
        return 1, "", f"ERROR: не запустился yt-dlp: {e}"
    return (res.returncode, res.stdout.decode("utf-8", "replace"),
            res.stderr.decode("utf-8", "replace"))


def _clean(outdir):
    for name in os.listdir(outdir):
        if name.startswith("source."):
            try:
                os.remove(os.path.join(outdir, name))
            except OSError:
                pass


def _result(outdir, out):
    """(путь к ролику, сведения из info.json) после удачного запуска."""
    path = next((line.strip() for line in reversed(out.splitlines())
                 if line.strip() and os.path.exists(line.strip())), "")
    if not path:
        found = [n for n in os.listdir(outdir)
                 if n.startswith("source.") and not n.endswith((".json", ".part", ".ytdl"))]
        path = os.path.join(outdir, found[0]) if found else ""
    info = {}
    try:
        with open(os.path.join(outdir, "source.info.json"), encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, ValueError):
        pass
    return path, info


def _order(state):
    good = state.get("good") if state.get("good") in METHODS else ""
    return ([good] if good else []) + [m for m in METHODS if m != good]


def fetch(url, outdir, say=None, probe=False):
    """Скачать ролик: (путь, info, заметки о починке). Не вышло — FetchError.

    `say(text)` — сказать человеку о починке по ходу («обновил yt-dlp»).
    `probe` — только проверить, что скачалось бы (для `health`)."""
    os.makedirs(outdir, exist_ok=True)
    state = _state()
    notes, tried, refreshed = [], [], set()
    relogged = False
    say = say or (lambda text: None)
    for method in _order(state):
        # Ночная сборка лечит только «сайт поменялся». От «нужен вход» и
        # обрыва сети она не спасёт — и качать её ради этого незачем.
        if method.startswith("nightly") and tried and \
                not any(k in ("broken", "unknown", "tool") for _, k, _ in tried):
            continue
        try:
            path, updated = ensure(method)
        except FetchError as e:
            tried.append((method, e.kind, e.detail))
            continue
        if updated:
            notes.append(f"скачалка обновлена ({method}, {version(method) or 'новая версия'})")
        for attempt in (1, 2):
            _clean(outdir)
            code, out, err = _run(method, path, url, outdir, probe)
            if code == 0 and (probe or _result(outdir, out)[0]):
                fields = {"good": method, "ok_url": url, "ok_at": time.time()}
                if probe:
                    fields.pop("ok_url")
                _update(**fields)
                if method != state.get("good") and state.get("good"):
                    notes.append(f"скачиваю теперь способом {method}")
                if probe:
                    return "", {}, notes
                video, info = _result(outdir, out)
                return video, info, notes
            line = _last_line(err or out)
            kind = classify(err + "\n" + out) or _ask_model(err or out) or "unknown"
            tried.append((method, kind, line))
            if kind in ("gone", "unsupported"):
                raise FetchError(kind, line, tried)
            channel = method.split("-")[0]
            if kind in ("broken", "unknown") and attempt == 1 and channel not in refreshed:
                refreshed.add(channel)
                try:
                    path, updated = ensure(method, refresh=True)
                except FetchError:
                    updated = False
                if updated:
                    note = f"обновил скачивалку ({method}, {version(method) or 'новая версия'})"
                    notes.append(note)
                    say(f"🔧 {note} — пробую ещё раз")
                    continue
            if kind == "login" and attempt == 1 and not relogged and "instagram.com" in url:
                # Вход устарел, а окно входа (`iglogin`) у нас уже было —
                # забираем из его профиля свежие cookies невидимо и сразу ещё раз.
                relogged = True
                try:
                    import iglogin

                    if iglogin.refresh():
                        notes.append("обновил вход в Instagram")
                        continue
                except Exception:
                    pass
            if kind == "login" and attempt == 1 and has_cookies() and not probe:
                time.sleep(60)          # мог быть короткий запрет на частые запросы
                continue
            break
    kinds = [k for _, k, _ in tried]
    final = next((k for k in ("login", "network", "broken", "unknown", "tool") if k in kinds),
                 "unknown")
    detail = next((line for _, k, line in reversed(tried) if k == final), "")
    raise FetchError(final, detail, tried)


def explain(error):
    """Объяснение для человека по FetchError — простыми словами."""
    kind = error.kind
    if kind == "login" and has_cookies():
        text = (EXPLAIN["login_stale"] + ". Напиши /login и войди в запасной аккаунт "
                "заново — или пришли свежий cookies.txt.")
    elif kind == "login":
        text = EXPLAIN["login"] + ".\n\n" + COOKIES_HOWTO
    else:
        text = EXPLAIN.get(kind, EXPLAIN["unknown"]) + "."
        if kind in ("broken", "unknown", "network", "tool"):
            text += " А пока можно прислать ролик сюда файлом — смонтирую так же."
    if error.detail:
        text += f"\n\n({error.detail[:200]})"
    return text


# ---------------------------------------------------------------- здоровье

def health_due():
    state = _state()
    return bool(state.get("ok_url")) and \
        time.time() - float(state.get("health_at") or 0) > HEALTH_SEC


def health(outdir, say=None):
    """Раз в сутки: проверить скачивание на последней удачной ссылке, пока
    новых не присылали. Чинит по той же цепочке. None — всё в порядке
    (или починилось), иначе FetchError. Заметки о починке — в `say`."""
    state = _update(health_at=time.time())
    try:
        _, _, notes = fetch(state["ok_url"], outdir, say=say, probe=True)
    except FetchError as e:
        if e.kind == "gone":
            _update(ok_url="")      # ролик удалили — проверять больше не на чем
            return None
        return e
    if notes and say:
        say("🔧 Скачивалка: " + "; ".join(notes) + ".")
    return None
