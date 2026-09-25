# -*- coding: utf-8 -*-
"""Ссылки на ролики: найти в тексте, узнать соцсеть, привести к общему виду.

Нужно там, где ссылка приходит СНАРУЖИ — из переписки в Telegram, — а не
снимается с экрана телефона. У той, что с телефона, сеть известна заранее
(агент сам в ней сидит); у присланной в чат не известно ничего, кроме адреса.

Три вещи, которые здесь делаются, и почему именно они:

* **Найти адреса в тексте.** Человек присылает ссылку не отдельным
  сообщением, а с припиской («глянь, вот это в ролик») — и часто несколько
  сразу. Плюс Telegram умеет прятать адрес за текстом (`text_link`), тогда
  в самом тексте его нет вовсе, он лежит в `entities`.

* **Узнать соцсеть по домену.** Она пишется в колонку `app` рядом с адресом
  и потом видна в подборке. Список доменов, а не «всё, что не TikTok» —
  потому что у одной сети адресов несколько (`vm.tiktok.com`, `vt.tiktok.com`),
  и по одному домену их не свести.

* **Причесать адрес.** Соцсети вешают на ссылку метки того, кто и откуда её
  скопировал (`utm_*`, `igshid`, `is_from_webapp`, `si`). Один и тот же
  ролик из двух рук даёт два разных адреса, а в базе `url` объявлен UNIQUE —
  без чистки повтор не отсекается. Полезные параметры (`v` у YouTube,
  `t` у Telegram) при этом обязаны выжить, поэтому выбрасывается ЧЁРНЫЙ
  список меток, а не оставляется белый: неизвестный параметр скорее нужен.
"""
import re
import urllib.parse

# Имена лент берутся те же, что в recipes.json (`tiktok`, `shorts`, `reels`),
# чтобы колонка `app` значила одно и то же и у ссылок с телефона, и у
# присланных в чат. Остальные сети своих лент не имеют — там имя просто
# человеческое.
NETWORKS = [
    ("tiktok", ("tiktok.com", "vm.tiktok.com", "vt.tiktok.com", "douyin.com")),
    ("shorts", ("youtube.com", "youtu.be", "m.youtube.com", "music.youtube.com")),
    ("reels", ("instagram.com", "instagr.am", "ddinstagram.com")),
    ("vk", ("vk.com", "m.vk.com", "vk.ru", "vkvideo.ru", "vkontakte.ru")),
    ("x", ("twitter.com", "x.com", "t.co", "fxtwitter.com", "vxtwitter.com")),
    ("telegram", ("t.me", "telegram.me", "telegram.dog")),
    ("ok", ("ok.ru", "odnoklassniki.ru")),
    ("likee", ("likee.video", "l.likee.video", "like.video")),
    ("pinterest", ("pinterest.com", "pinterest.ru", "pin.it")),
    ("facebook", ("facebook.com", "fb.watch", "fb.com", "m.facebook.com")),
    ("threads", ("threads.net", "threads.com")),
    ("snapchat", ("snapchat.com",)),
    ("twitch", ("twitch.tv", "clips.twitch.tv")),
    ("reddit", ("reddit.com", "redd.it", "v.redd.it")),
    ("rutube", ("rutube.ru",)),
    ("dzen", ("dzen.ru", "zen.yandex.ru")),
    ("coub", ("coub.com",)),
    ("yappy", ("yappy.media",)),
    ("kwai", ("kwai.com", "kw.ai")),
    ("bilibili", ("bilibili.com", "b23.tv")),
    ("vimeo", ("vimeo.com",)),
]

# Метки «кто и откуда скопировал». Выбрасываются целиком; сравнение по
# началу имени, чтобы одним словом закрыть всё семейство utm_*.
JUNK_PARAMS = (
    "utm_", "igshid", "igsh", "is_from_webapp", "sender_device", "sender_web_id",
    "web_id", "_r", "_t", "_d", "checksum", "share_app_id", "share_item_id",
    "share_link_id", "tt_from", "source", "refer", "referrer", "ref_src",
    "ref_url", "fbclid", "gclid", "yclid", "si", "feature", "pp", "app",
    "trk", "trk_params", "from", "context", "share_id", "u_code", "timestamp",
    "share_times", "iid", "enter_from", "share_source",
)

# Метки, которые в общий список не поставить: у одной сети это мусор, у
# другой — смысл. `t` у X — метка копирования, а у YouTube то же `t` — с
# какой секунды смотреть; вычистишь всюду — потеряешь момент в ролике.
JUNK_BY_NETWORK = {
    "x": ("s", "t", "cxt"),
    "reels": ("img_index",),
    "facebook": ("mibextid", "rdid"),
}

# Адрес в тексте. Скобки и типографские кавычки в конец адреса не входят:
# человек пишет «(вот ссылка https://... )» и «„https://...“».
RE_URL = re.compile(
    r"""(?:https?://|www\.)[^\s<>"'«»„“”)\]}]+""", re.IGNORECASE)

# Адрес без «https://» и без «www.» — так их копируют с телефона.
#
# Граница слева — просмотр назад, а не съеденный символ: иначе открывающая
# скобка в «(vk.com/video-1_2)» попадала бы в совпадение и `strip_urls`
# оставлял бы в заметке осиротевшую «)». Запрещены слева `/`, `.`, `@` и
# буквы — так хвост уже найденного полного адреса не находится второй раз.
RE_BARE = re.compile(
    r"""(?<![\w/.@-])(?:[\w-]+\.)+[a-z]{2,}/[^\s<>"'«»„“”)\]}]+""",
    re.IGNORECASE)

TRAILING = ".,;:!?…'\"»«)]}"


def _host(url):
    try:
        host = urllib.parse.urlsplit(url).netloc.lower()
    except ValueError:
        return ""
    if "@" in host:                       # login@host — до собаки нам дела нет
        host = host.split("@", 1)[-1]
    return host.split(":")[0]


def network_of(url):
    """Имя соцсети по адресу. Пусто — сеть неизвестна, ссылка всё равно нужна.

    Сравнение по концу домена, чтобы `www.` и региональные поддомены
    (`ru.pinterest.com`) не заводили отдельных строк в списке.
    """
    host = _host(url)
    if not host:
        return ""
    for name, domains in NETWORKS:
        for domain in domains:
            if host == domain or host.endswith("." + domain):
                return name
    return ""


def normalize(url):
    """Причесать адрес: схема, нижний регистр домена, долой метки копирования."""
    url = url.strip().strip(TRAILING)
    if not url:
        return ""
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "https://" + url.lstrip("/")

    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return url

    extra = JUNK_BY_NETWORK.get(network_of(url), ())
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
             if not any(k.lower().startswith(junk) for junk in JUNK_PARAMS)
             and k.lower() not in extra]

    path = parts.path
    # Хвостовой слэш убираем, но только у пути с содержимым: у голого домена
    # он и есть весь путь.
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]

    return urllib.parse.urlunsplit((
        parts.scheme.lower(), _host(url), path,
        urllib.parse.urlencode(query), "",   # якорь всегда лишний
    ))


def find_urls(text, entities=None):
    """Все адреса из сообщения, причёсанные и без повторов.

    `entities` — разметка Telegram: там лежат адреса, спрятанные за текстом
    (`text_link`), которых в самом тексте нет. Без них ссылка из пересланного
    поста теряется молча.
    """
    found = []

    for item in entities or []:
        kind = item.get("type")
        if kind == "text_link" and item.get("url"):
            found.append(item["url"])

    text = text or ""
    found.extend(RE_URL.findall(text))
    for bare in RE_BARE.findall(text):
        # Голый адрес ищется отдельно и может пересечься с уже найденным
        # («…https://vk.com/x» даст и «vk.com/x») — повторы уберёт normalize.
        found.append(bare)

    out, seen = [], set()
    for url in found:
        clean = normalize(url)
        if not clean or "." not in _host(clean):
            continue
        if clean.lower() in seen:
            continue
        seen.add(clean.lower())
        out.append(clean)
    return out


def strip_urls(text):
    """Текст без адресов — из него получается заметка к ссылке."""
    text = RE_URL.sub(" ", text or "")
    text = RE_BARE.sub(" ", text)
    # От «(vk.com/x)» остаются пустые скобки — в заметке они выглядят опиской.
    text = re.sub(r"[(\[{«„]\s*[)\]}»“]", " ", text)
    return re.sub(r"\s{2,}", " ", text).strip(" \t\n-—:,;")
