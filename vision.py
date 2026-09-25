"""Визуальная модель. Две задачи: разбор контента и спасение.

Первая — понять, что за ролики скармливает лента аккаунту. Вторая — подсказка,
когда агент упёрся в незнакомый экран и селекторы не сработали.

Отвечать может кто угодно: сервер в своей сети (LM Studio на этом компьютере
или на соседнем) и любой OpenAI-совместимый сервис по API. Протокол один и
тот же, поэтому разница сведена к трём вещам — адрес, заголовок с ключом и
имя модели; всё остальное в этом файле общее. Кто отвечает — решает
`provider()`, а куда идти — `config.VISION_URL` / `config.API_URL`.

Модель НЕ управляет телефоном по умолчанию: она только смотрит и говорит.
Тапать по её подсказке разрешает config.VISION_MAY_TAP, и в маршруте
публикации это игнорируется всегда — цена ошибки там слишком высокая.

Только стандартная библиотека: HTTP руками через urllib, уменьшение картинки
через tkinter (в Tk 8.6 есть и чтение, и запись PNG).
"""
import base64
import contextlib
import hashlib
import http.client
import json
import os
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import abort
import config
import gate

LOCAL, API = "local", "api"


class VisionError(RuntimeError):
    """Зрение не ответило.

    `fatal` — беда не во времени и не в связи, а в настройках: неверный ключ,
    несуществующая модель, кончившаяся оплата. Такое не лечится ни повтором,
    ни подменой на локальную модель — об этом надо сказать человеку.
    """

    def __init__(self, message, fatal=False):
        super().__init__(message)
        self.fatal = fatal


# ------------------------------------------------------------ транспорт

def provider():
    """Кто сейчас отвечает: API (сервис в интернете) или LOCAL (своя сеть)."""
    return API if str(config.VISION_PROVIDER).strip().lower() in (
        "api", "qwen", "cloud", "dashscope", "облако") else LOCAL


def host(url):
    """Имя хоста из адреса — коротко, для журнала и окна."""
    try:
        return urllib.parse.urlparse(url).hostname or url
    except ValueError:
        return url


def where(kind=None):
    """Человеческое название источника — для журнала и окна.

    Называем именно адрес, а не «облако»: подключить можно что угодно, и в
    журнале должно быть видно, к кому именно агент ходил.
    """
    kind = kind or provider()
    if kind == API:
        return "по API · " + host(config.API_URL)
    name = host(config.VISION_URL)
    # Свой компьютер — самый частый случай, и «127.0.0.1» в журнале ничего
    # не добавляет. А вот сервер на соседней машине назвать стоит.
    return "LM Studio" if name in ("127.0.0.1", "localhost") \
        else f"LM Studio · {name}"


def _endpoint(kind):
    """Адрес и заголовки. Ключ подставляется только когда он задан.

    Пустой ключ — законный случай: у сервера в своей сети (LM Studio,
    vLLM, llama.cpp) авторизации обычно нет вовсе, а заголовок с пустым
    `Bearer` некоторые из них отвергают.
    """
    url = config.API_URL if kind == API else config.VISION_URL
    headers = {"Content-Type": "application/json"}
    key = (config.API_KEY if kind == API else "").strip()
    if key:
        # Заголовки HTTP — latin-1, и на кириллице в ключе urllib падает
        # UnicodeEncodeError посреди запроса. Ключ копируют мышкой, и лишний
        # русский символ или «умная» кавычка туда попадают легко: лучше
        # сказать об этом словами, чем показать обрыв стека.
        if not key.isascii():
            raise VisionError(
                "В ключе есть русские буквы или лишние символы — "
                "скопируй его заново.", fatal=True)
        headers["Authorization"] = "Bearer " + key
    return url.rstrip("/"), headers


def _proxied():
    """Настроен ли HTTP-прокси. При нём соединения держим не мы, а urllib.

    Пул ниже ходит напрямую через http.client и про прокси не знает. На этом
    компьютере прокси нет (VPN работает на уровне сети, а не как прокси), но
    комплект уезжает на чужие машины, и там он может быть.
    """
    if any(os.environ.get(n) for n in
           ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy",
            "ALL_PROXY", "all_proxy")):
        return True
    try:
        return bool(urllib.request.getproxies())
    except Exception:
        return False


class _Pool(threading.local):
    """Живые соединения — по одному на поток и на адрес.

    Зачем: TLS-рукопожатие с зарубежным сервисом стоит ~600 мс, а сам ответ
    на лёгкий запрос — 370 мс. То есть при новом соединении на каждый вызов
    дорога дороже работы, и на ролике (кадр + сверка темы) впустую уходило
    около 1.4 с. Замерено 2026-08-12: 1.05 с против 0.37 с на тех же запросах.

    Именно `threading.local`, а не общий пул: сессии в окне запускаются каждая
    в НОВОМ потоке, а одно `HTTPSConnection` двумя потоками сразу — это
    перемешанные ответы. Заодно это то, что нужно для нескольких телефонов
    разом: у каждого свой поток и своё соединение, они не ждут друг друга.
    """

    def __init__(self):
        self.conns = {}

    def get(self, url, timeout):
        """Соединение и признак «оно уже использовалось».

        Признак нужен вызывающему: обрыв на переиспользованном соединении —
        обычное дело (сервер закрыл его по простою), а тот же обрыв на
        свежесозданном означает настоящую поломку связи.
        """
        parts = urllib.parse.urlsplit(url)
        key = (parts.scheme, parts.hostname, parts.port)
        conn = self.conns.get(key)
        if conn is None:
            cls = http.client.HTTPSConnection if parts.scheme == "https" \
                else http.client.HTTPConnection
            conn = cls(parts.hostname, parts.port, timeout=timeout)
            self.conns[key] = conn
            return conn, False
        # Таймаут у каждого запроса свой (разогрев ждёт дольше разбора), а
        # соединение переиспользуется — переставляем и на живом сокете.
        conn.timeout = timeout
        if conn.sock is not None:
            try:
                conn.sock.settimeout(timeout)
            except OSError:
                self.drop(url)
                return self.get(url, timeout)
        return conn, True

    def drop(self, url):
        parts = urllib.parse.urlsplit(url)
        conn = self.conns.pop((parts.scheme, parts.hostname, parts.port), None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def close_all(self):
        for key in list(self.conns):
            conn = self.conns.pop(key)
            try:
                conn.close()
            except Exception:
                pass


_POOL = _Pool()


def close_connections():
    """Отпустить соединения этого потока. Звать не обязательно: простаивающее
    соединение закрывает сам сервер, а пул это переживает."""
    _POOL.close_all()


def _send(url, path, data, headers, timeout):
    """Один запрос по живому соединению. Возвращает (код, тело).

    Простаивающее keep-alive соединение сервер закрывает молча, и узнаём мы об
    этом только в момент отправки. Поэтому одна безусловная повторная попытка
    на свежем соединении — это не «повтор при ошибке», а нормальная часть
    работы с keep-alive, и она не должна съедать попытки из `API_RETRIES`.
    """
    parts = urllib.parse.urlsplit(url)
    full = (parts.path.rstrip("/") + path) or path
    for attempt in (0, 1):
        conn, reused = _POOL.get(url, timeout)
        try:
            conn.request("POST", full, data, headers)
            res = conn.getresponse()
            body = res.read()
            # Сервер вправе попрощаться после ответа — тогда соединение
            # держать нельзя, следующий запрос ушёл бы в закрытый сокет.
            if res.will_close or res.getheader("Connection", "").lower() == "close":
                _POOL.drop(url)
            return res.status, body.decode("utf-8", "replace")
        except (http.client.HTTPException, OSError) as e:
            _POOL.drop(url)
            # Свежее соединение и та же ошибка — дело не в простое, а в связи.
            if attempt or not reused:
                raise e
    raise VisionError("запрос не удался")


def _http_error(kind, code, body):
    """Ответ с кодом ошибки — во внятную фразу.

    Коды у облака говорящие, и путать их нельзя: при 401 бесполезно ждать и
    повторять, а при 429 — наоборот, только это и помогает.
    """
    short = body.strip()[:300]
    who = host(config.API_URL) if kind == API else "LM Studio"
    if kind != API:
        return VisionError(f"LM Studio ответил {code}: {short}")
    # Ключ рабочий, а модель аккаунту не выдана. Отдельно от неверного ключа
    # намеренно: поймано живьём, и час ушёл бы на проверку ключа, который в
    # порядке. Чинится не здесь, а в личном кабинете сервиса.
    if "Unpurchased" in body or "Access to model denied" in body \
            or "Access to app denied" in body:
        return VisionError(
            f"{who}: ключ принят, но эта модель аккаунту не выдана "
            f"({config.API_MODEL}). Подключи её в личном кабинете сервиса — "
            f"или выбери другую на вкладке «Настройки».", fatal=True)
    if code in (401, 403):
        return VisionError(
            f"{who} не принял ключ ({code}). Проверь его на вкладке "
            f"«Настройки». Ответ: {short}", fatal=True)
    if code == 429:
        return VisionError(f"{who}: слишком часто или кончилась квота (429): {short}")
    if code in (402, 404):
        return VisionError(
            f"{who} ответил {code} — проверь имя модели и оплату: {short}",
            fatal=True)
    return VisionError(f"{who} ответил {code}: {short}")


def _post(path, payload, timeout, kind=None):
    """Запрос к тому, кто сейчас за модель. Повторяет то, что стоит повторять.

    Локально повторять нечего: LM Studio либо запущен, либо нет. У облака
    между нами и моделью половина интернета, и 429/5xx/обрыв — обычное дело
    на ровном месте, поэтому там несколько попыток с паузой. Пауза
    прерываемая: кнопку «Остановить» нельзя заставить ждать сеть.
    """
    kind = kind or provider()
    base, headers = _endpoint(kind)
    url = base + path
    data = json.dumps(payload).encode("utf-8")
    attempts = (config.API_RETRIES + 1) if kind == API else 1

    last = None
    for attempt in range(attempts):
        if attempt:
            if abort.sleep(config.API_RETRY_PAUSE * attempt):
                break
        try:
            if _proxied():
                req = urllib.request.Request(url, data=data, headers=headers)
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return json.loads(r.read().decode("utf-8"))
            code, body = _send(base, path, data, headers, timeout)
            if code >= 400:
                last = _http_error(kind, code, body)
                # Ключ не тот и модели такой нет — от повтора не изменится.
                if code not in (429, 500, 502, 503, 504):
                    raise last
                continue
            return json.loads(body)
        except urllib.error.HTTPError as e:      # только путь через прокси
            body = e.read().decode("utf-8", "replace")
            last = _http_error(kind, e.code, body)
            if e.code not in (429, 500, 502, 503, 504):
                raise last from e
        except json.JSONDecodeError as e:
            # Ответ 200, но не JSON — так отвечает не тот адрес: страница
            # входа, заглушка провайдера, перепутанный `/v1`. Показать кусок
            # тела важнее, чем сказать «не удалось разобрать ответ».
            last = VisionError(
                f"{host(url)} ответил не по-JSON: {str(e)[:60]}. "
                "Проверь адрес сервиса на вкладке «Настройки».", fatal=True)
            raise last from e
        except (urllib.error.URLError, TimeoutError, OSError,
                http.client.HTTPException) as e:
            if kind == API:
                last = VisionError(
                    f"{host(config.API_URL)} не отвечает ({str(e)[:80]}). "
                    "Проверь связь; для зарубежных адресов — включён ли VPN.")
            else:
                last = VisionError(
                    f"LM Studio недоступен на {config.VISION_URL}. "
                    "Запусти его и включи сервер (Developer -> Start Server).")
                raise last from e
    raise last if last else VisionError("запрос не удался")


@contextlib.contextmanager
def _model_queue():
    """Очередь к модели, общая на все телефоны. Пусто, если делить нечего."""
    g = gate.vision()
    if isinstance(g, gate._Passthrough):
        yield
        return
    try:
        g.acquire(timeout=config.VISION_QUEUE_WAIT)
    except gate.Timeout as e:
        # Ждать дальше нечестнее, чем сказать правду: ролик на экране уже
        # сменился, и ответ про него всё равно опоздал бы. Наверху это
        # считается неразобранным кадром, и после трёх подряд сессия честно
        # уходит в слепой режим.
        raise VisionError(f"модель занята другим телефоном: {e}") from e
    try:
        yield
    finally:
        g.release()


def _completion(model, messages, max_tokens, temperature, timeout, kind=None):
    """Один запрос к модели. Единственное место, где собирается payload.

    `enable_thinking: false` — для облака обязательно. Модели Qwen с
    рассуждением включают его сами, а тогда ответ приходит только потоком, и
    обычный запрос падает с ошибкой. Рассуждение нам и не нужно: ролик висит
    на экране, пока модель думает, а ответ здесь — одна строка JSON.
    """
    kind = kind or provider()
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    if kind == API:
        payload["enable_thinking"] = False

    # Очередь к модели — когда телефонов несколько. При одном (или в облаке)
    # `gate.vision()` отдаёт заглушку и не стоит ничего. Пропускник
    # перевзятие внутри себя пропускает насквозь, поэтому вложенный вызов из
    # `ask` (перезагрузка и повторный вопрос) сам себя не запрёт.
    with _model_queue():
        try:
            return _post("/chat/completions", payload, timeout, kind)
        except VisionError as e:
            # Модель параметра не знает — повторим без него, чтобы не терять
            # запрос из-за необязательной подробности.
            if kind == API and "enable_thinking" in str(e):
                payload.pop("enable_thinking", None)
                return _post("/chat/completions", payload, timeout, kind)
            raise


def _timeout(kind=None):
    return config.API_TIMEOUT if (kind or provider()) == API \
        else config.VISION_TIMEOUT


# Как понять, что модель СМОТРИТ КАРТИНКИ, а не только читает текст.
#
# Спрашивать сам сервис — надёжнее всего, но список моделей у всех разный:
# OpenRouter рядом с именем отдаёт `architecture.input_modalities`, и тогда
# гадать не нужно вовсе; DashScope и LM Studio отдают голые имена. Поэтому
# два слоя: сначала метаданные (`_visual_by_meta`), и только если их нет —
# имя (`looks_visual`).
#
# Прежняя маска была `-vl|vision|llava|gemma-3|minicpm-v`, и мимо неё
# проходили gpt-4o, Claude, Gemini, Pixtral, InternVL — то есть у
# OpenRouter выпадающий список оставался почти пустым. Поймано охотой на
# баги: из 14 настоящих визуальных моделей маска узнавала 7.
VISION_HINT = re.compile(
    r"-vl\b|\bvl-|vision|llava|minicpm-v|gemma-3|gpt-4o|gpt-4\.1|gpt-5"
    r"|claude|gemini|pixtral|internvl|molmo|cogvlm|idefics|fuyu"
    r"|deepseek-vl|moondream|smolvlm|paligemma|glm-4v|yi-vl|step-1v"
    r"|ovis|janus|omni|multimodal|maverick|scout|qvq",
    re.I)

# Имена, которые попадают под маску, но картинок не видят. Проверяется
# первым: «qwen3-coder» не должен пролезать по слову «coder», а
# «gemini-embedding» — по слову «gemini».
NOT_VISION = re.compile(
    r"embedding|embed\b|whisper|\btts\b|dall-?e|rerank|moderation"
    r"|audio|realtime|guard|coder|instant|search-preview|image-gen",
    re.I)


def looks_visual(name):
    """Судим по имени — когда сервис ничего больше не сказал."""
    name = str(name or "")
    if NOT_VISION.search(name):
        return False
    return bool(VISION_HINT.search(name))


def _visual_by_meta(item):
    """Судим по метаданным. None — сервис о модальностях молчит.

    OpenRouter отдаёт `architecture.input_modalities: ["text","image"]`, в
    старых ответах — `architecture.modality: "text+image->text"`. Это точный
    ответ, и он бьёт любую догадку по имени.

    Смотреть надо В ДВУХ МЕСТАХ. DeepSeek кладёт `input_modalities` прямо в
    корень описания модели, без обёртки `architecture`, — и пока проверка
    заглядывала только внутрь неё, `deepseek-flash` со своим
    `["text","image"]` считался текстовым. Из-за этого я объявил, что у
    ключа нет зрения вовсе, хотя оно есть.
    """
    if not isinstance(item, dict):
        return None
    arch = item.get("architecture")
    places = [arch] if isinstance(arch, dict) else []
    places.append(item)

    for place in places:
        mods = place.get("input_modalities") or place.get("modalities")
        if isinstance(mods, (list, tuple)):
            return "image" in [str(m).lower() for m in mods]
        modality = place.get("modality")
        if isinstance(modality, str):
            return "image" in modality.split("->")[0].lower()
    return None

# Имя последней сработавшей модели. LM Studio показывает в /v1/models только
# то, что сейчас в памяти, а модель он выгружает сам после простоя. Без этой
# памяти второй вызов после паузы выглядел бы как «сервера нет».
_LAST_MODEL_FILE = os.path.join(config.BASE, ".vision_model")


def models(kind=None):
    """Список id моделей у сервера. [] если сервер жив, но пуст."""
    return [m.get("id", "") for m in models_detailed(kind) if m.get("id")]


def models_detailed(kind=None):
    """То же, но целиком: с метаданными, если сервис их отдаёт.

    Отдельно от `models()`, потому что по одному имени не всегда видно,
    смотрит ли модель картинки, а `architecture.input_modalities` — видно
    точно. Звать это чаще, чем при открытии настроек, незачем.
    """
    kind = kind or provider()
    base, headers = _endpoint(kind)
    req = urllib.request.Request(base + "/models", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))["data"]
            return [m for m in data if isinstance(m, dict)]
    except (urllib.error.URLError, TimeoutError, OSError, ValueError,
            KeyError, http.client.HTTPException) as e:
        if kind == API:
            raise VisionError(
                f"{host(config.API_URL)} не отдал список моделей: {str(e)[:80]}") from e
        raise VisionError(
            f"LM Studio недоступен на {config.VISION_URL}: {str(e)[:80]}"
        ) from e


# Если сервис список не отдал — показать хотя бы это. Пустой выпадающий
# список выглядит поломкой, а имя модели пользователь набирать не должен.
API_KNOWN = ("qwen-vl-plus", "qwen-vl-max", "qwen3-vl-plus")


# Цвета для проверки зрения. Берётся случайный: будь квадрат всегда красным,
# модель, отвечающая «красный» наугад, проходила бы проверку вечно.
PROBE_COLORS = {
    "красный": ((220, 20, 20), ("красн", "red", "алый")),
    "зелёный": ((20, 170, 60), ("зелён", "зелен", "green")),
    "синий": ((30, 60, 220), ("син", "голуб", "blue")),
    "жёлтый": ((240, 210, 30), ("жёлт", "желт", "yellow")),
}


def _solid_png(rgb, size=64):
    """Одноцветный квадрат PNG. Своими руками, потому что рисовать нечем:
    проект держится на одной стандартной библиотеке."""
    import struct
    import zlib

    def chunk(tag, data):
        body = tag + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    row = b"\x00" + bytes(rgb) * size          # 0 = строка без фильтра
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * size, 6))
            + chunk(b"IEND", b""))


def model_sees(model, url, key, timeout=30):
    """Смотрит ли модель картинки НА ДЕЛЕ. Возвращает (да/нет, что ответила).

    Проверка, а не догадка по имени: показываем цветной квадрат и спрашиваем
    цвет. Текстовая модель либо откажется принимать картинку, либо назовёт
    цвет наугад — а угадать один из четырёх вслепую шансов мало.

    Зачем: имя не гарантирует ничего. У больших сервисов сотни моделей с
    произвольными именами («stealth/space-bunny-alpha»), а выбранная по
    ошибке текстовая роняет зрение уже в ленте — на каждом кадре и молча.
    """
    word = random.choice(list(PROBE_COLORS))
    rgb, answers = PROBE_COLORS[word]
    image = "data:image/png;base64," + base64.b64encode(
        _solid_png(rgb)).decode("ascii")

    payload = json.dumps({
        "model": model,
        # Запас большой НАРОЧНО: рассуждающие модели тратят токены на
        # размышление, и при 16 (а то и 300) ответ обрывался на полуслове —
        # content приходил пустым, и модель выглядела слепой. Проверено на
        # deepseek-flash: 300 токенов уходили в рассуждение целиком.
        "max_tokens": 1500,
        "temperature": 0,
        "messages": [{"role": "user", "content": [
            {"type": "text",
             "text": "Какого цвета этот квадрат? Ответь ОДНИМ словом."},
            {"type": "image_url", "image_url": {"url": image}},
        ]}],
    }).encode("utf-8")

    req = urllib.request.Request(
        url.rstrip("/") + "/chat/completions", data=payload, headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "User-Agent": "PhoneAgent",
        })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            said = _answer_of(json.loads(r.read().decode("utf-8")))
    except urllib.error.HTTPError as e:
        # 400 тут — обычное дело: «эта модель не принимает изображения».
        return False, f"отказ {e.code}"
    except (urllib.error.URLError, TimeoutError, OSError, ValueError,
            KeyError, http.client.HTTPException) as e:
        return False, str(e)[:60]

    low = (said or "").lower()
    return any(a in low for a in answers), (said or "").strip()[:40]


def pick_visual(names, prefer="", url="", key="", tries=6, deadline=None,
                say=None):
    """Выбрать модель, которая ТОЧНО видит картинки. "" — не нашлось.

    Имена ни при чём: кандидаты проверяются живым запросом с цветным
    квадратом, и первый, кто назвал цвет верно, становится рабочим. Раньше
    здесь стояло предпочтение семейству Qwen-VL — от него отказались
    сознательно: сервисов много, имена у всех свои, и знакомое имя ничего
    не обещает.

    Уже выбранная модель проверяется первой: менять то, что работает,
    незачем. Без адреса и ключа проверять нечем — тогда отдаём первого
    кандидата как есть (так зовут стенды).
    """
    if not names:
        return ""
    order = ([prefer] if prefer and prefer in names else []) + \
            [n for n in names if n != prefer]
    if not url or not key:
        return order[0]

    for name in order[:max(1, tries)]:
        if deadline and time.time() > deadline:
            break
        good, said = model_sees(name, url, key)
        if say:
            say(f"{name}: {'видит картинку' if good else 'не видит'} ({said})")
        if good:
            return name
    return ""


def _list_at(url, key, timeout=20):
    """Список моделей по адресу. None — сервис не ответил или не принял ключ."""
    headers = {"User-Agent": "PhoneAgent"}
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url.rstrip("/") + "/models", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8")).get("data") or []
    except (urllib.error.URLError, TimeoutError, OSError, ValueError,
            http.client.HTTPException):
        return None
    return [m for m in data if isinstance(m, dict)]


def visual_of(items):
    """Из ответа сервиса — имена моделей, которые смотрят картинки."""
    out = []
    for item in items or []:
        name = item.get("id", "")
        said = _visual_by_meta(item)
        if name and (said is True or (said is None and looks_visual(name))):
            out.append(name)
    return out


def detect_service(key, timeout=20, tries=4, deadline=None, say=None):
    """Чей это ключ и какая модель у него видит картинки.

    Возвращает (id пресета, адрес, визуальные модели, рабочая модель).
    Рабочая модель пустая — значит ключ сервисом принят, а смотреть картинки
    у него нечем.

    ДОКАЗАТЕЛЬСТВОМ СЛУЖИТ ЗАПРОС, А НЕ СПИСОК. Первая версия верила списку
    моделей — и ошиблась на живом ключе: `/models` у OpenRouter **публичный**,
    он отдаёт 460 моделей вообще без ключа. То есть любой мусор «опознавался»
    как рабочий ключ OpenRouter, а настоящая проверка падала уже в ленте.

    Отсюда две опоры:
      * список, который БЕЗ ключа не отдаётся, сам по себе подтверждает ключ
        (так устроены DashScope и DeepSeek);
      * если список публичный, верить можно только удавшемуся запросу к
        модели — им же заодно проверяется и зрение (`model_sees`).
    """
    key = (key or "").strip()
    if not key:
        return None, "", [], ""

    order = [config.preset_for_key(key)]
    order += [p for p in config.API_PRESETS if p and p not in order]

    fallback = (None, "", [], "")
    for pid in order:
        _, url, _ = config.API_PRESETS.get(pid, ("", "", ""))
        if not url:
            continue
        items = _list_at(url, key, timeout)
        if items is None:
            continue                      # сервис не ответил или отверг ключ

        # Список отдают и без ключа? Тогда он ничего не доказывает.
        public = _list_at(url, "", timeout) is not None
        visual = visual_of(items)

        model = pick_visual(visual, "", url, key, tries, deadline, say)
        if model:
            return pid, url, visual, model
        if not public:
            # Ключ этот сервис принял (без него списка не дают), но зрения
            # у него не нашлось. Запоминаем и пробуем остальные — вдруг
            # ключ подойдёт кому-то ещё.
            if fallback[0] is None:
                fallback = (pid, url, visual, "")
    return fallback


def _cloud_models():
    """Визуальные модели облака: [{id, name, note}].

    Отбор двухслойный: сервис сказал про модальности — верим ему, молчит —
    судим по имени. Смешивать нельзя: у OpenRouter половина списка с
    метаданными, и модель, про которую там честно написано «только текст»,
    не должна пролезать по похожему имени.
    """
    ids = []
    try:
        for item in models_detailed(API):
            name = item.get("id", "")
            if not name:
                continue
            said = _visual_by_meta(item)
            if said is True or (said is None and looks_visual(name)):
                ids.append(name)
    except VisionError:
        pass
    # Запасные имена — ТОЛЬКО когда сервис не отдал вообще ничего. Иначе
    # они подмешиваются к чужому списку: у OpenRouter к 289 его моделям
    # добавлялись три имени DashScope, которых там нет, и выбрать их значило
    # получить «нет такой модели» уже в ленте.
    if not ids:
        ids = [name for name in API_KNOWN]
    current = (config.API_MODEL or "").strip()
    if current and current not in ids:
        ids.insert(0, current)
    return [{"id": m, "name": m, "size": 0, "loaded": False, "note": host(config.API_URL)}
            for m in ids]


def _loaded_visual_models():
    """Визуальные модели среди тех, что сервер показывает сам.

    Запасной путь для LM Studio без утилиты `lms` и для любого другого
    OpenAI-совместимого сервера в своей сети: `/v1/models` есть у всех.
    """
    try:
        items = models_detailed(LOCAL)
    except VisionError:
        return []
    out = []
    for item in items:
        name = item.get("id", "")
        said = _visual_by_meta(item)
        if not name or not (said is True or (said is None and looks_visual(name))):
            continue
        out.append({"id": name, "name": name, "size": 0, "loaded": True,
                    "note": "загружена в память"})
    return out


# Список моделей меняется редко, а стоит дорого: у LM Studio `lms ls` — это
# секунда на запуск процесса, у сервиса — запрос по сети. Окно же опрашивает
# состояние раз в 12 секунд, и без кэша каждый такой обход ходил в облако.
# Сбрасывается при любой правке настроек (`prefs.save` зовёт `forget_models`).
_MODELS = {"at": 0.0, "where": None, "list": []}
MODELS_TTL = 300.0


def forget_models():
    """Забыть список моделей: настройки поменялись, спрашивать надо заново."""
    _MODELS["where"] = None


def installed_models(fresh=False):
    """Модели, которые можно выбрать в окне: [{id, name, size, loaded, note}].

    Ответ кэшируется на `MODELS_TTL`; `fresh=True` спрашивает заново — так
    зовут сразу после того, как человек вставил ключ.
    """
    where = (provider(), config.API_URL, config.VISION_URL)
    if (not fresh and _MODELS["where"] == where
            and time.time() - _MODELS["at"] < MODELS_TTL):
        return _MODELS["list"]
    found = _installed_models_now()
    _MODELS.update(at=time.time(), where=where, list=found)
    return found


def _installed_models_now():
    """Спросить по-настоящему, без кэша.

    У облака это его собственный список, у LM Studio — всё скачанное.
    `/v1/models` локально показывает только то, что сейчас в памяти, — для
    выпадающего списка этого мало. `lms ls --json` знает всё скачанное и
    помечает визуальные признаком `vision`, по нему и отбираем: текстовой
    модели кадр не отдать.
    """
    if provider() == API:
        return _cloud_models()

    exe = lms_cli()
    if not exe:
        # Утилиты `lms` нет — спросим сам сервер. Список выйдет короче (в
        # памяти обычно одна модель), но пустой выпадающий список выглядит
        # поломкой, а человек не должен набирать имя модели руками.
        return _loaded_visual_models()
    import subprocess

    try:
        out = subprocess.run([exe, "ls", "--json"], capture_output=True,
                             text=True, encoding="utf-8", errors="replace",
                             timeout=30)
        items = json.loads(out.stdout or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        return []

    try:
        loaded = set(models())
    except VisionError:
        loaded = set()

    found = []
    for item in items:
        if not isinstance(item, dict) or not item.get("vision"):
            continue
        key = item.get("modelKey") or ""
        if not key:
            continue
        found.append({
            "id": key,
            "name": item.get("displayName") or key,
            "size": round((item.get("sizeBytes") or 0) / 1024 ** 3, 1),
            "loaded": key in loaded,
            "note": "",
        })
    return sorted(found, key=lambda m: m["name"].lower())


def warm_up(timeout=120):
    """Растолкать модель до начала работы.

    LM Studio выгружает модель по простою и грузит её обратно на первом
    запросе. Замерено: первое решение в сессии стоило 8.15 с против обычной
    секунды — ровно на это время первый ролик лишний раз висел на экране.

    У облака грузить нечего, но запрос всё равно полезен: он оплачивает
    рукопожатие TLS (первое соединение с dashscope стоило 2.8 с) и заодно
    проверяет ключ до того, как агент начнёт листать ленту.
    """
    kind = provider()
    ok, name = available()
    if not ok:
        return False
    # Именно с картинкой: от текстового запроса просыпается только языковая
    # часть, а картиночная догружается на первом кадре — и он один стоил
    # 8.15 с. Шлём крошечный PNG, чтобы этот счёт оплатить заранее.
    messages = [{"role": "user", "content": [
        {"type": "text", "text": "."},
        {"type": "image_url",
         "image_url": {"url": "data:image/png;base64," + _TINY_PNG}},
    ]}]
    if kind == API:
        # Заодно расталкиваем ffmpeg: первый его запуск стоил 1.35 с против
        # обычных 0.07 (файл ещё не в кэше, да и антивирус смотрит), и этот
        # счёт иначе оплатил бы первый ролик сессии.
        if config.API_JPEG:
            to_jpeg(base64.b64decode(_TINY_PNG))
        try:
            _completion(name, messages, 1, 0, min(timeout, config.API_TIMEOUT))
            return True
        except VisionError:
            return False

    payload = {
        "model": name,
        "messages": messages,
        "max_tokens": 1,
        "temperature": 0,
        "stream": False,
    }
    try:
        _post("/chat/completions", payload, timeout)
        return True
    except VisionError as e:
        # Модель выгружена по простою — сервер отвечает «No models loaded»
        # и сам её не поднимает. Поднимаем и пробуем ещё раз: ровно ради
        # этого случая разогрев и нужен.
        if "No models loaded" not in str(e) or not load_model(name):
            return False
    # Модель поднята — это и было главное; замер показал 6.3 с -> 2.1 с на
    # первом кадре. Второй запрос уже необязателен, его неудача ничего не
    # меняет.
    try:
        _post("/chat/completions", payload, timeout)
    except VisionError:
        pass
    return True


# Серый квадратик 16x16 — только чтобы разбудить картиночную часть модели.
_TINY_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAHElEQVR42mNkYPhfz0"
    "AEYBxVSF+FjIONjIyMAAsvBAV/nRWJAAAAAElFTkSuQmCC"
)


def _remember(model):
    try:
        with open(_LAST_MODEL_FILE, "w", encoding="utf-8") as f:
            f.write(model)
    except OSError:
        pass


def _recall():
    try:
        with open(_LAST_MODEL_FILE, encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


# Что показала последняя живая проверка ключа. Хранится отпечаток настроек,
# а не сам ключ: помнить надо «эта связка уже отвечала 401», а не секрет.
_VERDICT = {}


def _fingerprint():
    """Отпечаток связки адрес+ключ+модель. Ключ уходит в хеш, не в память."""
    key = (config.API_KEY or "").strip().encode("utf-8", "replace")
    return (config.API_URL.strip(),
            hashlib.sha256(key).hexdigest()[:16] if key else "",
            (config.API_MODEL or "").strip())


def verify(kind=None):
    """Живьём спросить сервис, работает ли связка ключ+адрес+модель.

    Зачем отдельно от `available()`: та нарочно не ходит по сети — её зовут
    раз в 12 секунд. Но когда человек ТОЛЬКО ЧТО вписал ключ, промолчать
    нельзя: неверный ключ иначе всплывёт через полчаса как «сессия шла
    слепой». Поэтому проверка тут одна и явная, по нажатию «Сохранить».

    Возвращает (ok, сообщение человеку). Исключений не бросает.
    """
    kind = kind or provider()
    if kind != API:
        try:
            items = models_detailed(LOCAL)
        except VisionError as e:
            return False, str(e)
        loaded = [m.get("id", "") for m in items]
        vis = visual_of(items)
        if vis:
            return True, f"сервер отвечает, визуальная модель: {vis[0]}"
        return True, f"сервер отвечает, моделей загружено: {len(loaded)}"

    if not (config.API_KEY or "").strip():
        return False, "ключ не вписан"

    try:
        loaded = models(API)
    except VisionError as e:
        text = str(e)
        # 401/403 — это не «связь подвела», это неверный ключ, и повтор не
        # поможет. Называем причину прямо, иначе человек ищет её в сети.
        if "401" in text or "Unauthorized" in text:
            return False, ("сервис не принял ключ (401) — он недействителен, "
                           "отозван или от другого сервиса")
        if "403" in text or "Forbidden" in text:
            return False, ("ключ принят, но доступ закрыт (403) — проверь "
                           "оплату и права ключа")
        return False, text

    # Отбор — общим правилом (метаданные, потом имя), а не маской по имени.
    # Пока здесь стоял только `VISION_HINT`, `deepseek-flash` со своим
    # честным `input_modalities: [text, image]` объявлялся невизуальным, и
    # окно писало «визуальных моделей у сервиса нет» — при выбранной и
    # работающей модели.
    try:
        vis = visual_of(models_detailed(API))
    except VisionError:
        vis = [m for m in loaded if looks_visual(m)]
    model = (config.API_MODEL or "").strip()
    if model and model not in loaded:
        got = ", ".join(vis[:3]) or "ни одной визуальной"
        return False, (f"ключ рабочий, но модели «{model}» у сервиса нет. "
                       f"Есть: {got}")
    if not vis:
        return False, "ключ рабочий, но визуальных моделей у сервиса нет"
    return True, (f"ключ рабочий, модель {model or vis[0]}, "
                  f"визуальных моделей у сервиса: {len(vis)}")


def check_and_remember(kind=None):
    """`verify()`, результат которого запомнится для `available()`."""
    ok, text = verify(kind)
    _VERDICT[_fingerprint()] = (ok, text)
    return ok, text


def available():
    """Готово ли зрение. Возвращает (bool, модель или причина отказа).

    Исключений не бросает: зовётся и из doctor, и из живой сессии.

    Сервис по API не опрашиваем: заданы адрес и модель — считаем готовым.
    Лишний запрос стоил бы полсекунды на каждой проверке (окно спрашивает раз
    в 12 секунд), а настоящую готовность всё равно покажет первый кадр.
    Ключ здесь не требуется: у сервера в своей сети его обычно нет.
    """
    if not config.VISION_ENABLED:
        return False, "выключено в config.VISION_ENABLED"

    if provider() == API:
        if not config.API_URL.strip():
            return False, "не задан адрес сервиса — вкладка «Настройки»"
        model = (config.API_MODEL or "").strip()
        if not model:
            return False, "не выбрана модель — вкладка «Настройки»"
        # По сети по-прежнему не ходим. Но если ровно эту связку уже
        # проверяли живьём и она не работает, врать «готово» нельзя:
        # именно так сессия и уходила в ленту слепой.
        seen = _VERDICT.get(_fingerprint())
        if seen and not seen[0]:
            return False, seen[1]
        return True, model

    if config.VISION_MODEL:
        return True, config.VISION_MODEL

    try:
        loaded = models()
    except VisionError as e:
        return False, str(e)

    for mid in loaded:
        if VISION_HINT.search(mid):
            _remember(mid)
            return True, mid

    # Сервер жив, но модели в памяти нет: отдадим имя прошлой — LM Studio
    # подгрузит её сам по первому запросу (JIT), это занимает несколько секунд.
    last = _recall()
    if last:
        return True, last

    if loaded:
        return False, (f"в памяти только {', '.join(loaded[:3])} — "
                       "загрузи визуальную модель (*-VL, gemma-3, minicpm-v)")
    return False, "в LM Studio не загружено ни одной модели"


# -------------------------------------------------------------- картинка

_TK_POOL = None
_TK_LOCK = threading.Lock()
_SHRINK_WARNED = False


def _tk_pool():
    """Один-единственный поток, в котором живёт весь Tk.

    Корень Tk намертво привязан к потоку, где создан, и из чужого молча
    ломается. А сессии в окне запускаются каждый раз в НОВОМ потоке —
    поэтому со второй сессии ужатие переставало работать, `shrink_png`
    возвращал исходник, и модель получала кадр вчетверо больше: решение
    вместо секунды занимало 3.2. Искали это долго, потому что осечка была
    беззвучной.
    """
    global _TK_POOL
    with _TK_LOCK:
        if _TK_POOL is None:
            import concurrent.futures

            _TK_POOL = concurrent.futures.ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="tk")
        return _TK_POOL


def shrink_png(data, factor=None):
    """Уменьшить PNG в factor раз. При любой осечке вернуть исходник.

    Кадр телефона — 1080x2400. Это больше тысячи визуальных токенов и
    несколько лишних секунд на каждый вызов; в половинном размере модель
    читает подписи не хуже. PIL в проекте нет, поэтому масштабируем
    средствами Tk: PhotoImage.subsample + запись обратно в PNG.

    Вся работа уходит в отдельный поток (см. `_tk_pool`) — иначе из второй
    сессии подряд ужатие тихо перестаёт работать.
    """
    factor = factor or config.VISION_SHRINK
    if factor <= 1:
        return data
    try:
        small = _tk_pool().submit(_shrink_here, data, factor).result(timeout=30)
    except Exception:
        small = data
    if len(small) >= len(data):
        global _SHRINK_WARNED
        if not _SHRINK_WARNED:
            _SHRINK_WARNED = True
            print("[внимание] кадр не ужимается — модель будет думать втрое "
                  "дольше. Проверь, что в сборке есть tkinter.", flush=True)
    return small


def _shrink_here(data, factor):
    """Само масштабирование. Зовётся только из потока `_tk_pool`."""
    try:
        import tkinter as tk

        root = _tk_root()
        img = tk.PhotoImage(master=root, data=base64.b64encode(data).decode("ascii"))
        small = img.subsample(factor, factor)

        try:
            # PhotoImage.data() появился только в Python 3.13.
            out = small.data(format="png")
            return out.encode("latin-1") if isinstance(out, str) else out
        except (AttributeError, tk.TclError):
            pass

        # Путь для старых версий: записать во временный файл и прочитать.
        import tempfile
        fd, tmp = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            small.write(tmp, format="png")
            with open(tmp, "rb") as f:
                return f.read()
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
    except Exception:
        return data


_ROOT = None


def _tk_root():
    """Одно скрытое окно на процесс: Tk не умеет работать без корня."""
    global _ROOT
    if _ROOT is None:
        import tkinter as tk
        _ROOT = tk.Tk()
        _ROOT.withdraw()
    return _ROOT


def frame_signature(data, factor=None):
    """Огрубленный отпечаток кадра: список яркостей по сетке.

    Нужен, чтобы отвечать на вопрос «лента вообще сдвинулась?». В живой ленте
    каждый свайп даёт совсем другой ролик, и соседние кадры расходятся сильно.
    Когда поверх ленты висит окно приложения («Подпишитесь на друзей»), свайп
    листает список ВНУТРИ окна, а ролик позади остаётся тот же — соседние
    кадры почти совпадают.

    Замерено на 98 настоящих сессиях: в живой ленте разница соседних кадров
    61.7 (медиана), в залипшей — 4.3.

    Считается в том же единственном потоке, что и ужатие: корень Tk привязан
    к своему потоку намертво (см. `_tk_pool`).
    """
    factor = factor or config.SIGNATURE_SHRINK
    try:
        return _tk_pool().submit(_signature_here, data, factor).result(timeout=20)
    except Exception:
        return None


def _signature_here(data, factor):
    """Само снятие отпечатка. Зовётся только из потока `_tk_pool`."""
    try:
        import tkinter as tk

        root = _tk_root()
        img = tk.PhotoImage(master=root, data=base64.b64encode(data).decode("ascii"))
        small = img.subsample(factor, factor)
        # Одним вызовом, а не пикселем за пикселем: каждый `.get()` — отдельный
        # поход в Tcl, и на сотне точек это дороже самого разбора кадра.
        rows = small.data(grayscale=True)
        out = []
        for row in rows:
            for cell in row.split():
                # Серый приходит как «#rrggbb» с одинаковыми составляющими.
                out.append(int(cell[1:3], 16) if cell.startswith("#") else 0)
        return out or None
    except Exception:
        return None


def frames_differ(a, b):
    """Насколько разошлись два отпечатка: 0 — одно и то же, 255 — всё иное.

    None (отпечаток не снялся) считаем «разошлись сильно»: молчащая проверка
    не должна сама останавливать сессию.
    """
    if not a or not b or len(a) != len(b):
        return 255.0
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def looks_blank(png_bytes, limit_kb=None):
    """Пустой ли кадр (погашенный экран). Судим по размеру PNG.

    Однотонный чёрный экран 1080x2400 сжимается в ~15 КБ, живой кадр весит
    700-2500 КБ. Проверка нужна потому, что `screen_state()` на MIUI умеет
    соврать «разблокирован» при выключенном экране: тогда агент час гоняет
    чёрные кадры, а модель на них честно выдумывает содержимое.
    """
    limit = (limit_kb or config.BLANK_FRAME_KB) * 1024
    return len(png_bytes) < limit


def _data_url(png_bytes):
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")


# Путь к ffmpeg ищется один раз: он один и тот же на весь запуск, а искать
# его на каждом кадре — лишняя работа в самом узком месте.
_FFMPEG = "?"
_JPEG_WARNED = False


def to_jpeg(png_bytes, quality=None):
    """PNG -> JPEG через ffmpeg. None, если не вышло (тогда шлём PNG).

    Нужно только для облака. Ужатый кадр в PNG весит 700-900 КБ, в base64 —
    больше мегабайта, и это уходит по сети на КАЖДЫЙ ролик. Тот же кадр в
    JPEG — 40-70 КБ, а модель по нему читает подписи так же.

    ffmpeg тут запускается разово на кадр (~60 мс) — на порядок дешевле, чем
    отправка лишнего мегабайта, тем более через VPN.
    """
    global _FFMPEG, _JPEG_WARNED
    if _FFMPEG == "?":
        import stream

        _FFMPEG = stream.ffmpeg_exe()
    if not _FFMPEG:
        if not _JPEG_WARNED:
            _JPEG_WARNED = True
            print("[внимание] ffmpeg не найден — кадры уходят в облако "
                  "картинкой PNG, это медленнее и дороже.", flush=True)
        return None

    import subprocess

    try:
        done = subprocess.run(
            [_FFMPEG, "-loglevel", "error", "-f", "image2pipe", "-i", "pipe:",
             "-f", "image2", "-vcodec", "mjpeg",
             "-q:v", str(quality or config.API_JPEG_QUALITY), "pipe:"],
            input=png_bytes, capture_output=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 and done.stdout else None


def _image_url(png_bytes, shrink, kind):
    """Кадр в том виде, в котором он уйдёт модели."""
    small = shrink_png(png_bytes, shrink)
    if kind == API and config.API_JPEG:
        jpg = to_jpeg(small)
        if jpg:
            return ("data:image/jpeg;base64,"
                    + base64.b64encode(jpg).decode("ascii"))
    return _data_url(small)


# ---------------------------------------------------------------- вызов

def lms_cli():
    """Путь к lms.exe — командной строке LM Studio, если она установлена."""
    import shutil

    found = shutil.which("lms")
    if found:
        return found
    path = os.path.expanduser(r"~\.lmstudio\bin\lms.exe")
    return path if os.path.exists(path) else None


def load_model(name, timeout=180):
    """Попросить LM Studio поднять модель в память. True, если получилось.

    Нужно потому, что LM Studio выгружает модель по простою, а на запрос к
    выгруженной отвечает «No models loaded» вместо того, чтобы подгрузить её
    самому. Без этого длинная сессия слепнет на середине.
    """
    if provider() == API:
        return False
    cli = lms_cli()
    if not cli:
        return False
    import subprocess

    try:
        done = subprocess.run([cli, "load", name, "--gpu", "max", "-y"],
                              capture_output=True, timeout=timeout)
        return done.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def looks_degenerate(answer):
    """Модель сорвалась в повтор одного символа и мелет чушь.

    Наблюдалось вживую: после нескольких часов работы qwen2.5-vl начинала
    отвечать «???????…» до упора в лимит токенов — на ЛЮБОЙ кадр, включая
    те, что часом раньше разбирались верно. Лечится только перезагрузкой
    модели, сама она не выправляется.

    Признак намеренно грубый: осмысленный ответ на четырёх языках всегда
    богаче двух разных символов на такой длине.
    """
    text = (answer or "").strip()
    return len(text) >= 20 and len(set(text)) <= 2


def reload_model(name, timeout=180):
    """Выгрузить и поднять модель заново. True, если получилось.

    Идёт через ту же очередь, что и запросы: `unload --all` вырывает модель
    из памяти у ВСЕХ, и сделать это, пока другой телефон ждёт ответа, значит
    подарить ему мусор вместо описания. При `VISION_MAX_PARALLEL = 1` слот
    один, поэтому перезагрузка получается исключительной сама собой.
    """
    if provider() == API:
        return False
    cli = lms_cli()
    if not cli:
        return False
    import subprocess

    try:
        with _model_queue():
            try:
                subprocess.run([cli, "unload", "--all"], capture_output=True,
                               timeout=60)
            except (OSError, subprocess.SubprocessError):
                pass
            return load_model(name, timeout)
    except VisionError:
        # Очередь не подошла. Перезагрузка — дело поправимое: вернём False,
        # и вызывающий просто переспросит модель как есть.
        return False


# Чтобы сорванная модель не приводила к перезагрузке на каждом кадре подряд.
_RELOADED_AT = 0.0
RELOAD_COOLDOWN = 120


def can_reload():
    """Есть ли кого перезагружать. У облака — нет, там нечем управлять."""
    return provider() == LOCAL and bool(lms_cli())


def _local_fallback():
    """Имя локальной модели, если она готова подменить облако. Иначе None.

    Проверка честная — спрашиваем LM Studio, что у него в памяти: обещать
    подмену и упереться в незапущенную программу хуже, чем сразу сказать,
    что зрения нет.
    """
    if not config.VISION_FALLBACK:
        return None
    try:
        loaded = models(LOCAL)
    except VisionError:
        return None
    for mid in loaded:
        if VISION_HINT.search(mid):
            return mid
    return None


# Модели, которым не хватило запаса токенов на рассуждение. Со второго
# запроса такой модели запас сразу увеличивается, чтобы не платить за
# повторный запрос на каждом кадре.
_NEEDS_ROOM = set()


def _thought_too_long(res):
    """Рассуждение съело весь запас, а ответ не начался."""
    try:
        choice = res["choices"][0]
        message = choice["message"]
    except (KeyError, IndexError, TypeError):
        return False
    if (message.get("content") or "").strip():
        return False
    thinking = (message.get("reasoning_content")
                or message.get("reasoning") or "").strip()
    return bool(thinking) and choice.get("finish_reason") == "length"


def _answer_of(res):
    """Текст ответа. У рассуждающих моделей он лежит не только в `content`.

    Поймано на `deepseek-flash`: это рассуждающая модель, и весь отведённый
    запас токенов уходит в `reasoning_content`, а `content` остаётся ПУСТЫМ
    (`finish_reason: length`). Пока мы читали только `content`, такая модель
    выглядела сломанной: ответ есть, а у нас пусто — и зрение молча падало
    бы на каждом кадре.
    """
    try:
        message = res["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as e:
        raise VisionError(f"неожиданный ответ модели: {str(res)[:200]}") from e

    said = (message.get("content") or "").strip()
    if said:
        return said
    # Рассуждение — не полноценный ответ, но лучше пустоты: по нему видно и
    # что модель поняла, и что кадр до неё дошёл.
    return (message.get("reasoning_content")
            or message.get("reasoning") or "").strip()


def ask(png_bytes, prompt, system=None, max_tokens=400, temperature=0.2,
        timeout=None, shrink=None):
    """Задать модели вопрос по картинке. Возвращает текст ответа."""
    kind = provider()
    ok, model = available()
    if not ok:
        raise VisionError(model)

    def build(for_kind):
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url",
             "image_url": {"url": _image_url(png_bytes, shrink, for_kind)}},
        ]})
        return messages

    if model in _NEEDS_ROOM:
        max_tokens *= 6
    wait = timeout or _timeout(kind)
    try:
        res = _completion(model, build(kind), max_tokens, temperature, wait, kind)
    except VisionError as e:
        if kind == API and not getattr(e, "fatal", False):
            # Облако отвалилось: упал VPN, оборвалась связь, ответило 429.
            # Доработаем на том, что под рукой — слепая сессия хуже медленной.
            # Неверный ключ сюда не попадает намеренно: подменить модель и
            # молчать значит спрятать поломку, которую надо чинить руками.
            local = _local_fallback()
            if not local:
                raise
            print(f"[зрение] сервис молчит ({str(e)[:60]}) — спрашиваю "
                  "LM Studio", flush=True)
            return _answer_of(_completion(local, build(LOCAL), max_tokens,
                                          temperature, config.VISION_TIMEOUT,
                                          LOCAL))
        # Модель выгрузилась по простою — поднимем её и попробуем ещё раз.
        if "No models loaded" not in str(e) or not load_model(model):
            raise
        res = _completion(model, build(kind), max_tokens, temperature, wait, kind)

    # Рассуждающая модель не уложилась в запас токенов: всё ушло в
    # размышление, а сам ответ не начался. Спрашиваем ещё раз, дав вшестеро
    # больше, и запоминаем эту модель — со второго кадра запас сразу большой.
    #
    # Поймано на `deepseek-flash`: кадр он разбирает верно («это страница
    # профиля, а не лента»), но JSON не успевает, и в базу ложился мусор.
    if _thought_too_long(res):
        _NEEDS_ROOM.add(model)
        res = _completion(model, build(kind), max_tokens * 6, temperature,
                          wait, kind)

    answer = _answer_of(res)

    # Ответ выродился — перезагружаем модель и спрашиваем ещё раз. Без этого
    # сессия молча доживает до конца на мусорных описаниях: вердикты
    # выносятся, ролики смотрятся, а в базе — строки из «?».
    global _RELOADED_AT
    if looks_degenerate(answer) and time.time() - _RELOADED_AT > RELOAD_COOLDOWN:
        _RELOADED_AT = time.time()
        # В облаке перезагружать нечего — просто спрашиваем ещё раз.
        if kind == API or reload_model(model):
            try:
                answer = _answer_of(_completion(model, build(kind), max_tokens,
                                                temperature, wait, kind))
            except VisionError:
                pass

    if kind == LOCAL:
        # Запрос прошёл — значит имя модели рабочее, запомним его для
        # следующего раза (после выгрузки по простою /v1/models будет пуст).
        _remember(model)
    return answer


def _json_from(text):
    """Вытащить JSON из ответа. Модели любят обрамлять его пояснениями."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    # Последняя попытка: вытащить поля по одному. Ответ мог оборваться на
    # лимите токенов — тогда JSON невалиден, но нужные значения в нём уже есть,
    # и терять их из-за незакрытой скобки жалко.
    salvaged = {}
    for key in ("тема", "категория", "язык"):
        m = re.search(rf'"{key}"\s*:\s*"([^"]*)"', text)
        if m:
            salvaged[key] = m.group(1)
    m = re.search(r'"реклама"\s*:\s*(true|false)', text)
    if m:
        salvaged["реклама"] = m.group(1) == "true"
    return salvaged or None


# --------------------------------------------------- разбор контента

CONTENT_SYSTEM = (
    "Ты разбираешь скриншоты коротких вертикальных видео (TikTok, Reels, Shorts). "
    "Отвечай ТОЛЬКО валидным JSON на русском языке, без пояснений и без markdown. "
    "Пиши только русскими буквами: иероглифы и латиница в описании недопустимы."
)

# «Интерфейс» в списке был ошибкой: поверх любого ролика в ленте нарисованы
# кнопки приложения, и модель клеила эту категорию настоящим видео, хотя тему
# описывала верно. Экраны без видео попадают в «другое» — для решения
# «смотреть или листать» этого достаточно.
# Язык ролика: модель называет его по-разному, а решение принимается по
# фиксированному набору. «другой» — всё, что не русский и не английский.
LANGUAGES = ("ru", "en", "es", "другой")

LANG_SYNONYMS = {
    "русский": "ru", "russian": "ru", "рус": "ru", "ru-ru": "ru",
    "английский": "en", "english": "en", "eng": "en", "en-us": "en",
    "испанский": "es", "spanish": "es", "español": "es", "espanol": "es",
    "исп": "es", "es-es": "es", "es-mx": "es", "испания": "es",
    "other": "другой", "иностранный": "другой",
}

# «Не смог определить» — это НЕ «чужой язык». Раньше пустое и «нет текста»
# сваливались в «другой», и ролик без надписей на кадре считался иноязычным:
# при заданном языке он молча уходил в «мимо». Теперь такие ответы дают пустой
# язык, а пустой язык `interests._lang_matches` не считает чужим.
LANG_UNKNOWN = {
    "", "-", "—", "нет", "none", "null", "n/a", "unknown", "неизвестно",
    "не определено", "не определён", "не определен", "непонятно",
    "нет текста", "без текста", "нет речи", "не слышно", "не указан",
}


def normalize_lang(value):
    """Привести язык к ru / en / другой. Пусто — значит не определён."""
    lang = str(value or "").strip().lower().strip(".!?")
    if lang in LANG_UNKNOWN:
        return ""
    lang = LANG_SYNONYMS.get(lang, lang)
    return lang if lang in LANGUAGES else "другой"


EXAMPLE_THEME = "парень готовит пасту на кухне и шутит в камеру"


def _same_as_example(theme, overlap=0.6):
    """Похоже ли описание на пример из промпта."""
    words = {w for w in theme.lower().split() if len(w) > 3}
    sample = {w for w in EXAMPLE_THEME.split() if len(w) > 3}
    if not words:
        return False
    return len(words & sample) / len(words | sample) >= overlap

# Модель упорно подбирает синонимы вместо предложенных слов — «кухня» вместо
# «еда», «фитнес» вместо «спорт». Раньше всё это падало в «другое» и теряло
# смысл: 16% кадров уходили в мусорную корзину на ровном месте.
SYNONYMS = {
    "кухня": "еда", "кулинария": "еда", "готовка": "еда", "рецепт": "еда",
    "рецепты": "еда", "напитки": "еда",
    "фитнес": "спорт", "спортивные игры": "спорт", "тренировка": "спорт",
    "футбол": "спорт", "тренировки": "спорт",
    "причёски": "красота", "прически": "красота", "одежда": "красота",
    "мода": "красота", "макияж": "красота", "стиль": "красота",
    "трансформация": "красота", "маникюр": "красота",
    "наука": "обучение", "наука и технологии": "обучение", "образование": "обучение",
    "технологии": "техника", "транспорт": "техника", "авто": "техника",
    "автомобили": "техника", "гаджеты": "техника",
    "свадьба": "отношения",
    "социальные сети": "другое", "друзья": "отношения", "семья": "отношения",
    "дети": "отношения", "любовь": "отношения",
    "танец": "танцы", "песня": "музыка", "песни": "музыка", "звук": "музыка",
    "питомцы": "животные", "кошки": "животные", "собаки": "животные",
    "юмористический": "юмор", "смешное": "юмор", "прикол": "юмор",
}

CATEGORIES = (
    "юмор", "музыка", "танцы", "еда", "спорт", "новости", "обучение", "игры",
    "животные", "красота", "техника", "отношения", "реклама", "другое",
)

# Мелкие модели охотно копируют шаблон вместо ответа («тема»: «о чём ролик»),
# поэтому поля объясняются словами, а формат показывается заполненным примером.
CONTENT_PROMPT = """Это кадр из ленты коротких видео. Поверх видео нарисован
интерфейс приложения: кнопки лайка и комментариев справа, подпись автора снизу.
Интерфейс описывать не надо — опиши САМО ВИДЕО под ним.

Поля ответа:
- тема: что происходит в видео, своими словами, до 8 слов
- категория: ровно одно слово из списка — юмор, музыка, танцы, еда, спорт,
  новости, обучение, игры, животные, красота, техника, отношения, реклама,
  другое. Выбирай по содержанию видео,
  а не по кнопкам приложения. Слово бери строго из списка, синонимы не нужны
- реклама: true, если это рекламная вставка
- язык: на каком языке НАДПИСИ на кадре и подпись автора снизу — ru, en, es
  или другой. Если надписей нет совсем или язык непонятен, напиши
  «неизвестно». Не угадывай по внешности людей и не пиши язык, которого
  не видишь

Пример правильного ответа:
{"тема": "{example}", "категория": "еда", "реклама": false, "язык": "ru"}

Опиши СВОЙ кадр так же. Не копируй пример."""


CAPTION_BLOCK = """
Под роликом автор написал:
---
{caption}
---
Подпись и картинка одинаково важны. Картинка показывает, что происходит,
подпись — о чём это и зачем снято; учитывай обе и не опирайся только на одну.
Если подпись противоречит картинке, опиши и то, и другое."""


def describe_frame(png_bytes, caption="", author="", music="", shrink=None):
    """Разбор кадра ленты. Подпись под роликом, если её удалось прочитать,
    идёт в тот же запрос: текст и картинка дополняют друг друга.

    topics — темы пользователя. Их сопоставляет сама модель, потому что
    поиск по словам тут бессилен: «политика» не встречается в описании
    «депутат обсуждает закон», хотя ролик именно про неё.

    Возвращает dict (с ключом 'сырой', если ответ не разобрался).
    """
    prompt = CONTENT_PROMPT.replace("{example}", EXAMPLE_THEME)
    extra = " ".join(x for x in (caption, f"автор {author}" if author else "",
                                 f"музыка {music}" if music else "") if x).strip()
    if extra:
        prompt += CAPTION_BLOCK.format(caption=extra[:500])

    # Ответ короткий намеренно: генерация текста — самая дорогая часть
    # запроса, а пока модель думает, неинтересный ролик висит на экране.
    text = ask(png_bytes, prompt, system=CONTENT_SYSTEM,
               max_tokens=120, temperature=0.1, shrink=shrink)
    # Перезагрузка внутри ask() не помогла (или её придержал таймаут) —
    # честно говорим «не разобрал» вместо строки из «?» в теме и в базе.
    if looks_degenerate(text):
        return {"тема": "", "категория": "другое", "текст": "",
                "реклама": False, "язык": "", "описание": caption,
                "сырой": "модель сорвалась в повтор символа"}
    data = _json_from(text)
    if data is None:
        data = {"тема": text[:200], "категория": "другое", "текст": "",
                "реклама": False, "язык": "", "сырой": text[:500]}
        data["описание"] = caption
        return data
    # Подпись храним как есть: по ней ищутся стоп-слова из interests.json,
    # и полагаться на пересказ модели тут нельзя.
    data["описание"] = caption
    data["автор"] = author

    # Модель регулярно выдумывает категории вне списка («сообщения», «жизнь»).
    # Сводка по свободным категориям бесполезна, поэтому приводим к списку.
    # Мелкая модель иногда переписывает пример из промпта вместо описания
    # кадра. Сравниваем по словам, а не дословно: она любит заменить одно
    # слово («мужчина» вместо «парень») и выдать всё остальное как есть.
    if _same_as_example(str(data.get("тема", ""))):
        data["тема"] = "не разобрал кадр"
        data["категория"] = "другое"
        data["сырой"] = "модель повторила пример из промпта"

    cat = str(data.get("категория", "")).strip().lower()
    cat = SYNONYMS.get(cat, cat)
    if cat not in CATEGORIES:
        data["сырой"] = f"категория вне списка: {cat}"
        cat = "другое"
    data["категория"] = cat
    data["язык"] = normalize_lang(data.get("язык"))
    data.setdefault("тема", "")
    return data


# --------------------------------------------------------- спасение

RESCUE_SYSTEM = (
    "Ты помогаешь автоматизации Android понять, что происходит на экране. "
    "Отвечай ТОЛЬКО валидным JSON на русском, без markdown."
)

RESCUE_PROMPT = """Экран телефона {w}x{h} пикселей. Автоматизация пыталась: {goal}
Шаг не удался — нужного элемента не нашлось.

Опиши экран и предложи ОДНО безопасное действие.

Верни строго такой JSON:
{{
  "экран": "что это за экран, одна фраза",
  "мешает": "что именно мешает продолжить: диалог, реклама, требование входа, другое",
  "действие": "одно из: tap, back, home, wait, none",
  "x": координата X для tap в пикселях (0 если не tap),
  "y": координата Y для tap в пикселях (0 если не tap),
  "почему": "коротко"
}}

Правила: никогда не предлагай кнопки публикации, оплаты, удаления и подтверждения
покупок. Если не уверен — действие "none"."""


def rescue(png_bytes, goal, screen_size):
    """Что делать на незнакомом экране. Решение выполняет вызывающий код."""
    w, h = screen_size
    text = ask(png_bytes, RESCUE_PROMPT.format(w=w, h=h, goal=goal),
               system=RESCUE_SYSTEM, max_tokens=300, temperature=0.1)
    data = _json_from(text) or {"экран": text[:200], "действие": "none",
                                "почему": "ответ не разобран"}

    action = str(data.get("действие", "none")).lower().strip()
    if action not in ("tap", "back", "home", "wait", "none"):
        action = "none"

    # Координаты вне экрана — верный признак, что модель их выдумала.
    if action == "tap":
        try:
            x, y = float(data.get("x", 0)), float(data.get("y", 0))
        except (TypeError, ValueError):
            x = y = 0
        if not (0 < x < w and 0 < y < h):
            action, data["почему"] = "none", "координаты вне экрана"
        data["x"], data["y"] = x, y

    data["действие"] = action
    return data


# ------------------------------------------------------------- кадры

def frames_dir(session_id):
    path = os.path.join(config.FRAMES_DIR, str(session_id))
    os.makedirs(path, exist_ok=True)
    return path


def save_frame(png_bytes, session_id, index):
    path = os.path.join(frames_dir(session_id), f"{index:03d}.png")
    with open(path, "wb") as f:
        f.write(png_bytes)
    return path


def new_session_id():
    return time.strftime("%Y%m%d-%H%M%S")


def tidy_up():
    """Убрать старые кадры и логи. Возвращает, сколько мегабайт освободилось.

    Раньше не убирал никто: кадр на ролик — это ~20 МБ за сессию, и за неделю
    ежедневной работы папка выросла до двух гигабайт. Разбор давно в базе, а
    сами картинки нужны только чтобы переразобрать недавнее.

    Зовётся в начале сессии: обход сотни папок стоит миллисекунды, а отдельного
    расписания для уборки заводить не хочется.
    """
    import shutil

    # Общая папка кадров осталась от времён до разделения по телефонам: новые
    # кадры туда уже не пишутся, но пара гигабайт старых лежала бы вечно —
    # убираем и её по тому же сроку.
    legacy = os.path.join(config.BASE, "frames")
    places = [(config.FRAMES_DIR, config.KEEP_FRAMES_DAYS),
              (config.LOG_DIR, config.KEEP_LOGS_DAYS)]
    if os.path.normcase(legacy) != os.path.normcase(config.FRAMES_DIR):
        places.append((legacy, config.KEEP_FRAMES_DAYS))

    freed = 0
    for folder, days in places:
        if not days or not os.path.isdir(folder):
            continue
        edge = time.time() - days * 86400
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            try:
                if os.path.getmtime(path) >= edge:
                    continue
                size = _weigh(path)
                shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
                freed += size
            except OSError:
                # Занятый или уже удалённый файл — не повод ронять сессию.
                continue

    freed += _trim_to_size([p for p, _ in places
                            if os.path.normcase(p) != os.path.normcase(config.LOG_DIR)],
                           getattr(config, "FRAMES_MAX_MB", 0))
    return freed / 1024 ** 2


def _trim_to_size(folders, limit_mb):
    """Держать кадры в пределах `limit_mb`, снося самые старые папки.

    Срока одного мало: за сутки плотной работы кадров набегает больше, чем за
    спокойную неделю, и папка успевает раздуться, ни разу не устарев.

    Считаем и режем по ВСЕМ папкам кадров разом (у каждого телефона своя):
    потолок общий, иначе три телефона держали бы тройной объём.
    """
    import shutil

    if not limit_mb:
        return 0
    limit = limit_mb * 1024 ** 2

    items = []
    for folder in folders:
        if not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            try:
                items.append((os.path.getmtime(path), _weigh(path), path))
            except OSError:
                continue

    total = sum(size for _, size, _ in items)
    if total <= limit:
        return 0

    freed = 0
    for _, size, path in sorted(items):          # от самых старых
        if total - freed <= limit:
            break
        try:
            shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
            freed += size
        except OSError:
            continue
    return freed


def _weigh(path):
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


# ------------------------------------------------- разворачивание тем

EXPAND_PROMPT = (
    "Тема: «{topic}». Перечисли через запятую 12-16 русских слов, которыми "
    "описывают короткие видео на эту тему. Нужны КОНКРЕТНЫЕ слова — кто в "
    "кадре и что показывают, а не отвлечённые понятия. Например для темы "
    "«политика»: депутат, президент, выборы, митинг, закон, чиновник, флаг. "
    "Только слова через запятую, без пояснений и без нумерации."
)


def expand_topic(topic, timeout=60):
    """Развернуть тему в набор слов: «политика» -> выборы, депутат, партия...

    Нужно потому, что тема — это понятие, а сопоставление идёт по словам:
    модель описывает ролик как «депутат обсуждает закон», и слова «политика»
    там нет. Сверять смысл прямо в разборе кадра 3B-модель не умеет
    (проверено дважды), а вот развернуть тему списком — вполне.

    Запрос текстовый, без картинки, и делается один раз на тему.
    """
    ok, model = available()
    if not ok:
        raise VisionError(model)

    res = _completion(model,
                      [{"role": "user",
                        "content": EXPAND_PROMPT.format(topic=topic)}],
                      120, 0.3, timeout)
    try:
        answer = res["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return []

    words = [w.strip(" .\"'\n").lower() for w in re.split(r"[,;\n]", answer)]
    return [w for w in words if 2 < len(w) < 20][:14]


# «Относится ли к теме» 3B понимает слишком широко: стройка и разговор
# двух людей уходили в «новости». Явное разрешение отвечать «нет» на
# косвенную связь убирает больше половины ложных «да» — на размеченном
# корпусе из трёх тем 88% -> 92-96% и вчетверо быстрее (0.06 с против 0.24).
JUDGE_PROMPT = ("Зритель смотрит только видео на тему «{topic}».\n"
                "Описание очередного видео: «{text}».\n"
                "Это видео действительно про «{topic}»? "
                "Если связь косвенная или её нет — отвечай нет.\n"
                "Ответь одним словом: да или нет.")


def judge_topic(description, topic, timeout=30):
    """Относится ли описанный ролик к теме. Текстовый вопрос, без картинки.

    Зачем отдельный вопрос, если есть описание: тема — это понятие, и поиск
    по словам её не ловит («политика» не встречается в «депутат обсуждает
    закон»). Просить модель сверить тему прямо при разборе кадра бесполезно —
    3B на картинке всегда выбирает что-то из списка и не умеет сказать «нет»
    (проверено трижды). А вот по готовому ТЕКСТУ она судит безошибочно:
    12 из 12 на контрольных примерах, и стоит это 0.08 секунды.
    """
    if not description or not topic:
        return False

    ok, model = available()
    if not ok:
        raise VisionError(model)

    res = _completion(model,
                      [{"role": "user",
                        "content": JUDGE_PROMPT.format(text=description[:300],
                                                       topic=topic)}],
                      4, 0.0, timeout)
    try:
        answer = res["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return False
    return str(answer).strip().lower().lstrip("«\"'").startswith("да")
