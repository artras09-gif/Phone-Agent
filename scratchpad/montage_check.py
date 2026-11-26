"""Монтажёр (2026-11-25) — без телефона и без сети.

Ссылка в теме «Монтаж» → скачивание (yt-dlp, сам чинится) → заголовок и
описание от модели → рендер по шаблону Rusorama → видео в «Запасы» и запись
в `stock`, чтобы после публикации оно ушло в архив.

Проверяется:
  * шрифт и раскладка заголовка — как на примерах человека;
  * место ролика в кадре, чистка заголовка и описания;
  * цепочка починки скачивания на подставных запусках yt-dlp;
  * приём cookies.txt;
  * маршрут сообщений бота: в теме «Монтаж» ссылка — заказ монтажа, а не
    подборка ссылок; «/here монтаж», тема по названию, окно;
  * путь заказа до «Запасов» и честная ошибка без них;
  * отправка видео файлом кусками;
  * настоящий рендер, если на ПК есть ffmpeg.
"""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import config  # noqa: E402

tmp = tempfile.mkdtemp(prefix="pa_montage_")
config.BASE = tmp
config.SERIAL = ""
config.DB_PATH = os.path.join(tmp, "jobs.db")
config.QUEUE_DIR = os.path.join(tmp, "queue")

import jobs          # noqa: E402
import linkfetch     # noqa: E402
import montage       # noqa: E402
import telegram_bot  # noqa: E402

for mod in (linkfetch,):
    mod.HOME = os.path.join(tmp, "montage")
    mod.BIN = os.path.join(mod.HOME, "bin")
    mod.STATE_FILE = os.path.join(mod.HOME, "fetch.json")
    mod.COOKIES = os.path.join(mod.HOME, "cookies.txt")
montage.WORK_DIR = os.path.join(tmp, "montage")
montage.TEXT_PAUSES = (0, 0, 0)

ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


tpl = montage.load_template()
say(tpl["id"] == "rusorama" and os.path.isdir(tpl["dir"]), "шаблон Rusorama находится")

print("1. Шрифт и заголовок — как на примерах")
font = montage.Font.get(os.path.join(tpl["dir"], tpl["title"]["font"]))
say(all(font.has(c) for c in "ÁÉÍÓÚÑÜ¿¡áéíóúñü|"), "в Rubik Bold есть все буквы испанского")
w = font.width("KIM JONG-UN RECIBE PERSONALMENTE", 40)
say(780 < w < 820, f"ширина строки примера при кегле 40 — {w:.0f} px (на примере ~800)")
# Логика переноса — на прежних мерках примеров (40/33, две строки): числа
# сверены с ними. Нынешний шаблон крупнее — ниже, отдельно.
big = tpl
tpl = copy.deepcopy(big)
tpl["title"].update(size=40, min_size=33, max_width=864, max_lines=2)
size, lines = montage.layout_title("KIM JONG-UN RECIBE PERSONALMENTE A VLADIMIR PUTIN", tpl)
say(lines == ["KIM JONG-UN RECIBE PERSONALMENTE", "A VLADIMIR PUTIN"] and size == 40,
    f"предлог «A» не висит в конце строки: {lines} @ {size}")
size, lines = montage.layout_title(
    "UN GRUPO DE MODELOS VESTIDAS DE NOVIA | RODEÓ A PUTIN PARA TOMARSE UNA SELFIE CON ÉL", tpl)
say(lines == ["UN GRUPO DE MODELOS VESTIDAS DE NOVIA",
              "RODEÓ A PUTIN PARA TOMARSE UNA SELFIE CON ÉL"] and size == 33,
    f"перенос «|» от модели соблюдён, кегль ужат до 33, как на примере: {size}")
size, lines = montage.layout_title("PUTIN | Y XI JINPING FIRMAN UN ACUERDO HISTÓRICO SOBRE "
                                   "ENERGÍA, COMERCIO Y SEGURIDAD EN MOSCÚ", tpl)
say(lines[0] != "PUTIN" and size == 33,
    f"неудачный перенос модели не ломает раскладку: {lines}")
say(all(font.width(line, size) <= tpl["title"]["max_width"] for line in lines),
    "ни одна строка не шире 864 px")
tpl = big
size, lines = montage.layout_title("KIM JONG-UN ROMPE EL PROTOCOLO | Y RECIBE A PUTIN EN PERSONA", tpl)
say(size >= 52 and len(lines) <= 3 and all(font.width(x, size) <= 960 for x in lines),
    f"шаблон сейчас: заголовок в 45 знаков — крупно, до трёх строк: {lines} @ {size}")
say(montage.hook_lines("KIM JONG-UN ROMPE EL PROTOCOLO | Y RECIBE A PUTIN EN PERSONA", size, lines, tpl) == 2,
    "крючок в две строки — жёлтые обе")
say(montage.hook_lines("KIM ROMPE EL PROTOCOLO | Y RECIBE A PUTIN", 64, ["KIM ROMPE EL PROTOCOLO", "Y RECIBE A PUTIN"],
                       tpl) == 1 and montage.hook_lines("SIN CORTE", 64, ["SIN CORTE"], tpl) == 1,
    "крючок в строку — жёлтая одна; без «|» — как раньше")
size2, lines2 = montage.layout_title("SILENCIO INCÓMODO | Y ERDOGAN ABANDONA LA SALA", tpl)
say(lines2 == ["SILENCIO INCÓMODO", "Y ERDOGAN ABANDONA LA SALA"] and 52 <= size2 < 64,
    f"влезает в две строки чуть мельче — две, без хвоста «LA SALA»: {lines2} @ {size2}")
size2, lines2 = montage.layout_title("PUTIN RECIBE PATATAS | DEL HUERTO", tpl)
say(size2 == 64 and len(lines2) == 2, f"короткий — полным кеглем 64: {lines2} @ {size2}")
bases = montage.title_baselines(len(lines), size, montage.title_top(1920, 0, tpl), tpl)
say(bases[0] - size > 0, f"три строки над высоким роликом не уходят за верх кадра: {bases}")

print("2. Ролик в кадре")
say(montage.fit_video(1280, 720, tpl) == (1080, 608, 0, 682), "16:9 — во всю ширину, по центру 986")
vw, vh, vx, vy = montage.fit_video(720, 1280, tpl)
say((vw, vh, vx, vy) == (1080, 1920, 0, 0),
    f"вертикальный — тоже во всю ширину, «от экрана до экрана»: {vw}x{vh} в {vx},{vy}")
say(montage.title_top(vh, vy, tpl) == 986 - 430, "высокий ролик: заголовок на постоянном месте")
say(montage.title_top(608, 682, tpl) == 682, "невысокий: заголовок над роликом")
base = montage.title_baselines(2, 40, 682, tpl)
say(base[1] == 582 and base[0] == 582 - round(40 * 1.22), f"заголовок в 100 px над роликом: {base}")

print("2а. Настоящий кадр в чужом вертикальном ролике")
# Серые кадры 128x228, как их даёт ffmpeg: чёрное поле (0), сверху чужой
# текст — неподвижные буквы, в середине съёмка — меняется от кадра к кадру.
import random  # noqa: E402
SW, SH = 128, 228


def frames_like(text_rows, shot_rows, shot_cols=(0, SW), dark=False, count=5):
    out = []
    for k in range(count):
        rnd = random.Random(k)
        fr = bytearray(SW * SH)
        for y in text_rows:
            for x in range(20, 108):
                fr[y * SW + x] = 255 if (x * 7 + y) % 3 else 0        # буквы — на месте
        for y in range(*shot_rows):
            for x in range(*shot_cols):
                top = 70 if dark else 250
                fr[y * SW + x] = rnd.randrange(0, top)                 # съёмка — живая
        out.append(bytes(fr))
    return out


box = montage.content_box(frames_like(range(60, 72), (78, 150)), SW, SH)
say(box is not None and box[1] == 78 and box[3] == 149 and box[0] == 0 and box[2] == SW - 1,
    f"поля и чужой текст сверху — съёмка найдена, текст не захвачен: {box}")
box = montage.content_box(frames_like(range(66, 76), (76, 150), dark=True), SW, SH)
say(box is not None and box[1] >= 76 and box[3] == 149,
    f"тёмная съёмка и текст вплотную над ней — текст не захвачен: {box}")
box = montage.content_box(frames_like(range(40, 50), (55, 170), shot_cols=(21, 107)), SW, SH)
say(box is not None and (box[0], box[2]) == (21, 106), f"поля по бокам — обрезаны: {box}")
box = montage.content_box(frames_like(range(40, 50), (55, 170), shot_cols=(0, 70)), SW, SH)
say(box is not None and (box[0], box[2]) == (0, SW - 1),
    f"чёрное только с одного боку (ночь) — ширину не режем: {box}")
say(montage.content_box(frames_like([], (0, SH)), SW, SH) is None, "съёмка на весь кадр — резать нечего")
say(montage.content_box(frames_like(range(60, 72), (0, 0)), SW, SH) is None,
    "одни неподвижные буквы — съёмки не нашёл, не режу")

print("3. Чистка текстов")
say(montage._clean_title("«Putin» llega a Pekín 🇨🇳. #news", font) == "PUTIN LLEGA A PEKÍN",
    "заголовок: без кавычек, эмодзи, хэштегов и точки, капсом")
long_caption = ("Primera frase del vídeo. " * 40) + "Más en https://x.com/a @autor #rusia #putin #historia"
cap = montage._clean_caption(long_caption, 450)
say(montage._utf16(cap) <= 450 and "https" not in cap and "@autor" not in cap and "#rusia" in cap,
    f"описание: ≤450 знаков UTF-16, без ссылок и @, хэштеги сохранены ({montage._utf16(cap)})")
say(montage._clean_title("Kim rompe el protocolo | y recibe a Putin", font, upper=False)
    == "Kim rompe el protocolo | y recibe a Putin", "для текста поста заголовок — обычными буквами")
post = montage._clean_caption("Kim recibió a Putin en el aeropuerto. La #OTAN responde con dureza. "
                              "#rusia #putin #corea #RUSIA", 500, head="Kim rompe el protocolo | y recibe a Putin")
say(post == ("Kim rompe el protocolo y recibe a Putin\n\n"
             "Kim recibió a Putin en el aeropuerto. La OTAN responde con dureza.\n\n"
             "#OTAN #rusia #putin #corea"),
    f"текст поста: заголовок / описание / хэштеги через пустую строку, без повторов:\n{post}")
post = montage._clean_caption(long_caption, 500, head="Un titular de prueba")
parts = post.split("\n\n")
say(montage._utf16(post) <= 500 and parts[0] == "Un titular de prueba" and parts[-1].startswith("#rusia"),
    f"длинное описание укорочено, заголовок и хэштеги на месте ({montage._utf16(post)} знаков)")
post = montage._clean_caption("😀" * 600 + " #a", 500, head="T")
say(montage._utf16(post) <= 500 and post.endswith("#a"), "описание из одних эмодзи — режется без зацикливания")

print("4. Скачивание: причины по тексту ошибки")
cases = {
    "ERROR: [Instagram] X: Unable to download webpage: HTTP Error 429: Too Many Requests": "login",
    "ERROR: [Instagram] X: Requested content is not available, rate-limit reached or login required": "login",
    "ERROR: [Instagram] X: Unable to download webpage: Failed to perform, curl: (28) Resolving timed out": "network",
    "ERROR: Unsupported URL: https://example.com/": "unsupported",
    "ERROR: [Instagram] X: Unable to extract shared data; please report this issue": "broken",
    "ERROR: [youtube] X: Private video. Sign in if you've been granted access": "gone",
    # живьём 25.11: этот текст уходил в «непонятные», и цепочка зря качала ночную
    "ERROR: [youtube] BaW_jenozKc: This video is unavailable": "gone",
}
for text, want in cases.items():
    say(linkfetch.classify(text) == want, f"{want:11} ← {text[:60]}…")


class Fake:
    """Подставной yt-dlp: по способу — что ответить."""

    def __init__(self, answers):
        self.answers = answers          # способ -> список (код, out, err) по очереди
        self.calls = []
        self.refreshed = []

    def ensure(self, method, refresh=False):
        if refresh:
            self.refreshed.append(method)
        return f"/bin/{method}", bool(refresh and method in self.updates)

    def run(self, method, path, url, outdir, probe=False):
        self.calls.append(method)
        answer = self.answers.get(method, [(1, "", "ERROR: no answer")])
        code, out, err = answer.pop(0) if len(answer) > 1 else answer[0]
        if code == 0 and not probe:
            video = os.path.join(outdir, "source.mp4")
            with open(video, "wb") as f:
                f.write(b"x" * 100)
            with open(os.path.join(outdir, "source.info.json"), "w", encoding="utf-8") as f:
                json.dump({"description": "Kim recibe a Putin", "uploader": "autor"}, f)
            out = video + "\n"
        return code, out, err


def chain(answers, updates=(), url="https://www.instagram.com/reel/AAA/"):
    fake = Fake(answers)
    fake.updates = set(updates)
    linkfetch.ensure, linkfetch._run = fake.ensure, fake.run
    linkfetch.version = lambda method: "2026.11.23"
    linkfetch._ask_model = lambda text: ""
    linkfetch.time.sleep = lambda s: None
    try:
        result = linkfetch.fetch(url, os.path.join(tmp, "dl"), say=lambda t: None)
        return fake, result, None
    except linkfetch.FetchError as e:
        return fake, None, e


DNS = (1, "", "ERROR: [Instagram] X: Unable to download webpage: curl: (28) Resolving timed out")
OKAY = (0, "", "")
fake, result, err = chain({"stable-py": [DNS], "stable-exe": [OKAY]})
say(result and fake.calls == ["stable-py", "stable-exe"], f"нет связи у одного способа — другой: {fake.calls}")
say(linkfetch._state().get("good") == "stable-exe", "сработавший способ запомнен")
fake, result, err = chain({"stable-exe": [OKAY]})
say(fake.calls == ["stable-exe"], "следующий раз — сразу запомненным способом")

linkfetch._update(good="")
BROKEN = (1, "", "ERROR: [Instagram] X: Unable to extract video url; please report this issue")
fake, result, err = chain({"stable-py": [BROKEN, OKAY]}, updates={"stable-py"})
say(result and fake.calls == ["stable-py", "stable-py"] and fake.refreshed == ["stable-py"]
    and any("обновил" in n for n in result[2]),
    f"«сайт поменялся» — обновиться и ещё раз: {fake.calls}, заметки {result and result[2]}")

linkfetch._update(good="")
fake, result, err = chain({"stable-py": [BROKEN], "stable-exe": [BROKEN], "nightly-py": [OKAY]})
say(result and fake.calls[-1] == "nightly-py", f"обновление не помогло — ночная сборка: {fake.calls}")

linkfetch._update(good="")
LOGIN = (1, "", "ERROR: [Instagram] X: Unable to download webpage: HTTP Error 429: Too Many Requests")
fake, result, err = chain({"stable-py": [LOGIN], "stable-exe": [LOGIN]})
say(err and err.kind == "login" and not any(c.startswith("nightly") for c in fake.calls),
    f"«нужен вход» — ночную не качаем зря, причина login: {fake.calls}")
text = linkfetch.explain(err)
say("/login" in text and "ЗАПАСНОЙ" in text, "человеку сказано, как дать вход запасного аккаунта (/login)")

import iglogin as _ig  # noqa: E402
_ig.refresh = lambda timeout=40: True
fake, result, err = chain({"stable-py": [LOGIN, OKAY]})
say(result and fake.calls == ["stable-py", "stable-py"] and "обновил вход в Instagram" in result[2],
    f"вход устарел, а окно входа было — свежие cookies из него и сразу ещё раз: {fake.calls}")
_ig.refresh = lambda timeout=40: False

GONE = (1, "", "ERROR: [Instagram] X: This content isn't available")
fake, result, err = chain({"stable-py": [GONE]})
say(err and err.kind == "gone" and fake.calls == ["stable-py"], "ролик удалён — сразу честный ответ")

print("5. Cookies")
good = ("# Netscape HTTP Cookie File\n"
        ".instagram.com\tTRUE\t/\tTRUE\t1900000000\tsessionid\tabc123\n"
        ".instagram.com\tTRUE\t/\tTRUE\t1900000000\tcsrftoken\tz\n").encode()
say("приняты" in linkfetch.save_cookies(good) and linkfetch.has_cookies(), "файл с sessionid принят")
for bad, why in ((b"just text", "не тот формат"),
                 (good.replace(b"sessionid", b"ds_user"), "без входа (sessionid)"),
                 (good.replace(b"instagram", b"example"), "не Instagram")):
    try:
        linkfetch.save_cookies(bad)
        say(False, f"отбит: {why}")
    except ValueError:
        say(True, f"отбит: {why}")

print("5б. Вход окном браузера (/login), без файла cookies")
import iglogin  # noqa: E402
iglogin.PROFILE = os.path.join(tmp, "montage", "browser")
devtools = [
    {"domain": ".instagram.com", "name": "csrftoken", "value": "c", "path": "/", "secure": True,
     "httpOnly": False, "expires": 1900000000.5},
    {"domain": ".instagram.com", "name": "sessionid", "value": "s", "path": "/", "secure": True,
     "httpOnly": True, "expires": 1900000000},
]
text = iglogin.to_netscape(devtools)
say("#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t1900000000\tsessionid\ts" in text,
    "cookies из DevTools → строка cookies.txt, как её читает yt-dlp")
say("приняты" in linkfetch.save_cookies(text), "такой файл проходит проверку входа")


class Proc:
    def __init__(self):
        self.code = None

    def poll(self):
        return self.code


polls = {"n": 0}


def cookies_after(n):
    def get(port):
        polls["n"] += 1
        return devtools if polls["n"] >= n else devtools[:1]
    return get


proc = Proc()
closed = []
iglogin._launch = lambda url, headless=False: (proc, 1)
iglogin._close = lambda p, port: closed.append(port)
iglogin.instagram_cookies = cookies_after(3)
iglogin.time.sleep = lambda s: None
msg_text = iglogin.login(timeout=60, poll=0)
say("приняты" in msg_text and polls["n"] >= 3 and closed, "ждём, пока человек войдёт; вошёл — записали и закрыли окно")
proc.code = 0
polls["n"] = 0
try:
    iglogin.login(timeout=60, poll=0)
    say(False, "закрытое до входа окно — ошибка")
except iglogin.LoginError as e:
    say("закрыли" in str(e), f"окно закрыли до входа — честно: {e}")

print("6. Бот: тема «Монтаж»")
GROUP, MONT, STOCK = "-1001", 7, 9
with open(os.path.join(tmp, "telegram.json"), "w", encoding="utf-8") as f:
    json.dump({"token": "1:T", "owner": "42", "groups": [{"id": GROUP, "title": "Rusorama"}]}, f)
sent, api = [], []


def fake_call(method, params=None, timeout=70, _retry=True):
    api.append((method, params))
    if method == "getMe":
        return {"ok": True, "result": {"id": 99, "username": "testbot",
                                       "can_read_all_group_messages": True}}
    if method == "getFile":
        return {"ok": False, "description": "нет"}
    return {"ok": True, "result": {}}


telegram_bot._call = fake_call
telegram_bot.send = lambda text, *a, **k: sent.append(text) or True
telegram_bot._ME.clear()
started = []
montage.start = lambda: started.append(1)


def msg(text="", thread=MONT, **extra):
    m = {"message_id": 100 + len(api), "chat": {"id": int(GROUP), "type": "supergroup",
                                               "is_forum": True},
         "from": {"id": 42}, "text": text, "is_topic_message": True,
         "message_thread_id": thread}
    m.update(extra)
    return m


telegram_bot._handle(msg(thread=MONT, text="", forum_topic_created={"name": "Монтаж"}))
say(telegram_bot.shelf_home("montage") and telegram_bot.shelf_home("montage")[1] == MONT,
    "тема «Монтаж» стала монтажом по названию")
say(sent and "шаблону «Rusorama»" in sent[-1] and "Запасы не назначены" in sent[-1],
    "бот сказал, что умеет, и что запасов нет")
telegram_bot._handle(msg(thread=STOCK, text="/here запасы"))
say(telegram_bot.shelf_home("stock") and telegram_bot.shelf_home("stock")[1] == STOCK, "запасы назначены")

sent.clear()
url = "https://www.instagram.com/reel/DAbc123/"
telegram_bot._handle(msg(text=f"{url} PUTIN EN PEKÍN", entities=[]))
row = jobs.montage_get(1)
say(row and row["url"] == url.rstrip("/") and row["note"] == "PUTIN EN PEKÍN"
    and row["thread"] == str(MONT),
    "ссылка в теме монтажа — заказ монтажа, текст рядом — свой заголовок")
say(jobs.links_waiting() == 0, "в подборку ссылок она НЕ попала")
say(started and any("Взял в монтаж: #1" in s for s in sent), "заказ принят, поток монтажа поднят")
sent.clear()
telegram_bot._handle(msg(text=url))
say(any("уже в очереди" in s for s in sent) and jobs.montage_get(2) is None, "та же ссылка второй раз не берётся")
telegram_bot._handle(msg(text=url + " заново"))
say(jobs.montage_get(2) is not None, "со словом «заново» — берётся")
telegram_bot._handle(msg(thread=3, text="https://www.instagram.com/reel/OTHER/"))
say(jobs.montage_get(3) is None and jobs.links_waiting() == 1, "в другой теме ссылка идёт в подборку, как раньше")

api.clear()
cookie_msg = msg(text="", document={"file_id": "F1", "file_name": "www.instagram.com_cookies.txt",
                                    "mime_type": "text/plain"})
saved = []
telegram_bot._download = lambda file_id, dest, size_mb=0: (
    open(os.path.join(dest, "c.txt"), "wb").write(good) and os.path.join(dest, "c.txt"))
linkfetch.save_cookies = lambda raw: saved.append(raw) or "Cookies Instagram приняты."
telegram_bot._handle(cookie_msg)
say(saved == [good] and any(m == "deleteMessage" for m, _ in api),
    "cookies.txt от владельца принят, сообщение с ним удалено из чата")
saved.clear()
cookie_msg["from"] = {"id": 555}
telegram_bot._handle(cookie_msg)
say(not saved, "cookies от чужого участника не принимаются")

logins = []
iglogin.browser = lambda: "edge.exe"
iglogin.login = lambda: logins.append(1) or "Cookies Instagram приняты."
sent.clear()
telegram_bot._handle(msg(text="/login"))
for t in threading.enumerate():
    if t.name == "ig-login":
        t.join(5)
say(logins and any("окно входа" in s for s in sent) and any(s.startswith("✅") for s in sent),
    "/login от владельца — окно входа на компьютере, итог в теме")
logins.clear()
other_login = msg(text="/login")
other_login["from"] = {"id": 555}
telegram_bot._handle(other_login)
say(not logins, "/login от чужого участника — молчим")

setup = telegram_bot.setup()
say("montage" in setup["order"] and setup["jobs"]["montage"]["topic"] == str(MONT),
    "в окне видно дело «Монтаж» и его тема")

print("7. Заказ до «Запасов»")
renders, uploads = [], []
linkfetch.ffmpeg_tools = lambda: ("ffmpeg", "ffprobe")
linkfetch.fetch = lambda url, outdir, say=None, probe=False: (
    os.path.join(outdir, "source.mp4"), {"description": "Kim Jong-un recibe a Putin", "uploader": "a"},
    ["обновил скачивалку (stable-py, 2026.11.23)"])
montage.probe = lambda path, ffprobe, ffmpeg=None: {"w": 1280, "h": 720, "duration": 12.0, "audio": True}
montage.frame_png = lambda *a: b""
import scheduler  # noqa: E402
scheduler.looks_like_video = lambda path: None
texts = {}


def fake_texts(tpl_, source, author="", frame=b"", user_title=""):
    texts.update(source=source, user_title=user_title)
    return (user_title or "KIM JONG-UN RECIBE | A PUTIN"), "Kim recibe a Putin. #rusia"


montage.write_texts = fake_texts


def fake_render(src, title, tpl_, out, tools, meta=None, cap_mb=None):
    renders.append((title, cap_mb))
    with open(out, "wb") as f:
        f.write(b"v" * 2048)


montage.render = fake_render
telegram_bot.max_download_mb = lambda: 2000


def fake_send_video(path, caption="", chat_id=None, thread=None, **k):
    uploads.append((chat_id, thread, caption))
    return {"ok": True, "result": {"message_id": 555, "date": 1764000000, "from": {"id": 99},
                                   "video": {"file_unique_id": "UNIQ"}}}


telegram_bot.send_video = fake_send_video
sent.clear()
montage.process(1)
row = jobs.montage_get(1)
say(row["status"] == "done" and row["stock_msg"] == 555, "заказ #1 выполнен")
say(texts["user_title"] == "PUTIN EN PEKÍN" and texts["source"].startswith("Kim"),
    "модели дана исходная подпись, свой заголовок человека сохранён")
say(uploads == [(GROUP, STOCK, "Kim recibe a Putin. #rusia")], f"видео ушло в тему запасов: {uploads}")
found = jobs.stock_find("UNIQ")
say(found and found[0]["msg"] == 555 and found[0]["thread"] == str(STOCK),
    "видео записано в stock — после публикации уйдёт в архив")
say(any("обновил скачивалку" in s for s in sent) and any(s.startswith("✅ #1") for s in sent),
    "в теме монтажа — заметка о починке и «✅ #1»")
say(renders[-1][1] is None, "свой сервер Bot API — без потолка размера")

print("7б. Видео файлом из Telegram — главный путь, пока ссылки не готовы")
uploads.clear()
texts.clear()
sent.clear()
video_msg = msg(text="", caption="Putin llega a Pekín para reunirse con Xi Jinping",
                video={"file_id": "VID1", "file_unique_id": "U1", "mime_type": "video/mp4",
                       "file_size": 5 * 1024 * 1024})
video_msg.pop("text")
telegram_bot._handle(video_msg)
file_task = max(r["id"] for r in jobs.connect().execute("SELECT id FROM montage").fetchall())
row = jobs.montage_get(file_task)
say(row["file_id"] == "VID1" and row["url"] == "" and row["note"].startswith("Putin llega"),
    "видео в теме монтажа — заказ, подпись — исходное описание")
say(not jobs.pending() and not jobs.connect().execute("SELECT 1 FROM jobs").fetchall(),
    "черновика публикации из него НЕ сделано — сначала монтаж")
got = []


def fake_download(file_id, dest, size_mb=0):
    got.append(file_id)
    path = os.path.join(dest, "in.mp4")
    with open(path, "wb") as f:
        f.write(b"v" * 4096)
    return path


telegram_bot._download = fake_download
telegram_bot.max_download_mb = lambda: 2000
montage.process(file_task)
say(got == ["VID1"] and jobs.montage_get(file_task)["status"] == "done",
    "видео взято из Telegram и смонтировано")
say(texts.get("source", "").startswith("Putin llega") and not texts.get("user_title"),
    "заголовок и описание модель пишет по подписи видео")
say(uploads and uploads[-1][:2] == (GROUP, STOCK), "готовое — в «Запасы»")

telegram_bot.max_download_mb = lambda: 20
montage.process(2)
say(renders[-1][1] == montage.CLOUD_UPLOAD_MB, "через облако Telegram — рендер под 48 МБ")

telegram_bot.set_shelf(GROUP, "stock", None)
sent.clear()
tid = jobs.montage_add(url="https://www.instagram.com/reel/NEW/", chat=GROUP, thread=str(MONT))
montage.process(tid)
say(jobs.montage_get(tid)["status"] == "failed" and any("Запасы» не назначены" in s for s in sent),
    "без запасов — честная ошибка, а не тишина")

print("8. Отправка видео файлом")
import importlib  # noqa: E402
tb = importlib.reload(telegram_bot)
tb.load_settings = lambda: {"token": "1:T", "owner": "42"}
tb.api_base = lambda: "http://api"
video = os.path.join(tmp, "v.mp4")
with open(video, "wb") as f:
    f.write(os.urandom(3 * 1024 * 1024 + 17))
captured = {}


class Resp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b'{"ok": true, "result": {"message_id": 1}}'


def fake_open(req, timeout=None):
    body = b"".join(req.data)
    captured.update(length=int(req.headers["Content-length"]), body=body, url=req.full_url)
    return Resp()


tb.urllib.request.urlopen = fake_open
res = tb.send_video(video, "descripción", chat_id=GROUP, thread=STOCK, width=1080, height=1920,
                    duration=12)
body = captured.get("body", b"")
say(res.get("ok") and captured["length"] == len(body), "Content-Length совпадает с телом")
say(b'name="message_thread_id"\r\n\r\n9' in body and "descripción".encode() in body
    and b'name="supports_streaming"' in body and captured["url"].endswith("/sendVideo"),
    "в запросе тема, описание, потоковое видео")

print("9. Настоящий рендер")
importlib.reload(montage)
import stream  # noqa: E402
ff = stream.ffmpeg_exe()
if not ff:
    print("  — ffmpeg на ПК нет, пропускаю")
else:
    fp = os.path.join(os.path.dirname(ff), "ffprobe.exe")
    fp = fp if os.path.exists(fp) else shutil.which("ffprobe")
if ff:
    src = os.path.join(tmp, "src.mp4")
    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=25:d=2",
                    "-f", "lavfi", "-i", "sine=f=440:d=2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", src], check=True)
    out = os.path.join(tmp, "out.mp4")
    # ffprobe не обязателен: 26.11 его не было, и монтаж не начался вовсе.
    info = montage.render(src, "KIM JONG-UN RECIBE PERSONALMENTE | A VLADIMIR PUTIN", tpl, out, (ff, fp))
    meta = montage.probe(out, fp, ff)
    say(montage.probe(out, None, ff) == montage.probe(out, None, ff) and
        montage.probe(out, None, ff)["w"] == 1080, "сведения о ролике и без ffprobe — по выводу ffmpeg")
    turned = os.path.join(tmp, "turned.mp4")
    subprocess.run([ff, "-v", "error", "-y", "-display_rotation", "90", "-i", src, "-c", "copy",
                    turned], check=False)
    if os.path.exists(turned):
        got = montage.probe(turned, None, ff)
        say((got["w"], got["h"]) == (360, 640), f"повёрнутый ролик — размер с поворотом: {got}")
    wide_px = os.path.join(tmp, "sar.mp4")
    subprocess.run([ff, "-v", "error", "-y", "-i", src, "-vf", "setsar=4/3", "-c:v", "libx264",
                    "-an", wide_px], check=True)
    got = montage.probe(wide_px, None, ff)
    say((got["w"], got["h"]) == (853, 360) and not got["audio"],
        f"широкий пиксель и ролик без звука: {got}")
    say(meta["w"] == 1080 and meta["h"] == 1920 and meta["audio"] and 1.8 < meta["duration"] < 2.3,
        f"вышло 1080x1920 со звуком, {meta['duration']:.1f} с")
    say(not os.path.exists(out + ".parts"), "рабочая папка рендера убрана")
    if fp:
        kinds = subprocess.run([fp, "-v", "error", "-show_entries", "stream=codec_type", "-of",
                                "csv=p=0", out], capture_output=True, text=True).stdout.split()
        say(sorted(kinds) == ["audio", "video"], f"в файле только видео и звук, без глав исходника: {kinds}")

shutil.rmtree(tmp, ignore_errors=True)
print("ВСЁ ХОРОШО" if ok else "ЕСТЬ ОШИБКИ")
sys.exit(0 if ok else 1)
