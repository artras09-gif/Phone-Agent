"""Приём ссылок в Telegram: разбор адресов и что на них отвечает бот.

Ни сети, ни телефона: Telegram подменяется, база — списком в памяти. Так же
устроен `links_digest.py`, только там проверяется отправка подборки, а тут —
приём.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import telegram_bot as bot  # noqa: E402
import weblink  # noqa: E402

ok = True


def say(good, text):
    global ok
    ok &= good
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


# --- 1. какая сеть у какого адреса -----------------------------------
print("--- узнаём соцсеть ---")
CASES = [
    ("https://www.tiktok.com/@user/video/7412345678901234567", "tiktok"),
    ("https://vm.tiktok.com/ZMhcAbCdE/", "tiktok"),
    ("https://youtube.com/shorts/abc123", "shorts"),
    ("https://youtu.be/abc123", "shorts"),
    ("https://www.instagram.com/reel/CxYz12/", "reels"),
    ("https://vk.com/video-1_2", "vk"),
    ("https://vkvideo.ru/video-1_2", "vk"),
    ("https://x.com/u/status/1", "x"),
    ("https://twitter.com/u/status/1", "x"),
    ("https://t.me/channel/123", "telegram"),
    ("https://ok.ru/video/123", "ok"),
    ("https://pin.it/AbCd", "pinterest"),
    ("https://www.reddit.com/r/a/comments/b/c/", "reddit"),
    ("https://rutube.ru/video/abc/", "rutube"),
    ("https://dzen.ru/video/watch/abc", "dzen"),
    ("https://fb.watch/abc/", "facebook"),
    ("https://www.twitch.tv/user/clip/abc", "twitch"),
    ("https://b23.tv/abc", "bilibili"),
    ("https://example.org/clip.mp4", ""),
]
bad = [(u, weblink.network_of(u), want) for u, want in CASES
       if weblink.network_of(u) != want]
say(not bad, f"все {len(CASES)} адресов разобраны верно"
    + (f", кроме {bad}" if bad else ""))
say(weblink.network_of("https://ru.pinterest.com/pin/1") == "pinterest",
    "региональный поддомен — та же сеть")

# --- 2. чистка адреса ------------------------------------------------
print("\n--- метки копирования ---")
say(weblink.normalize("https://www.tiktok.com/@u/video/7?is_from_webapp=1&sender_device=pc")
    == "https://www.tiktok.com/@u/video/7", "у TikTok метки убраны")
say(weblink.normalize("https://x.com/u/status/1?s=20&t=abc")
    == "https://x.com/u/status/1", "у X убраны s и t")
say(weblink.normalize("https://youtu.be/abc?t=42") == "https://youtu.be/abc?t=42",
    "у YouTube t — это секунда, её оставляем")
say(weblink.normalize("https://www.youtube.com/watch?v=abc&si=xyz")
    == "https://www.youtube.com/watch?v=abc", "а si у YouTube — метка")
say(weblink.normalize("tiktok.com/@u/video/7").startswith("https://"),
    "адрес без схемы дополняется")
say(len(weblink.find_urls("https://vk.com/x?utm_source=a и https://vk.com/x/")) == 1,
    "один адрес в двух видах — одна ссылка")

# --- 3. поиск адресов в сообщении ------------------------------------
print("\n--- достаём из сообщения ---")
text = "глянь https://youtube.com/shorts/abc, и вот (vk.com/video-1_2) в ролик"
urls = weblink.find_urls(text)
say(len(urls) == 2, f"нашлись оба адреса: {urls}")
say(not urls[1].endswith(")"), "закрывающая скобка не приклеилась к адресу")
say(weblink.strip_urls(text) == "глянь , и вот () в ролик"
    or "глянь" in weblink.strip_urls(text), f"заметка: {weblink.strip_urls(text)!r}")

hidden = weblink.find_urls("смотри тут", [{"type": "text_link",
                                           "url": "https://t.me/c/1"}])
say(hidden == ["https://t.me/c/1"], "адрес, спрятанный за текстом, найден")

# --- 4. что делает бот -----------------------------------------------
print("\n--- ответ бота ---")
saved, sent = [], []


class FakeJobs:
    @staticmethod
    def add_link(url, app, likes, author="", caption="", session="", frame=""):
        if any(row[0] == url for row in saved):
            return False
        saved.append((url, app, caption, session))
        return True

    @staticmethod
    def links_waiting():
        return len(saved)


bot.jobs = FakeJobs
bot.send = lambda text, preview=True: sent.append(text) or True

bot._save_incoming(["https://www.tiktok.com/@u/video/7",
                    "https://youtube.com/shorts/abc"], "в ролик")
say(len(saved) == 2, f"сохранены обе: {[r[1] for r in saved]}")
say(saved[0][2] == "в ролик", "приписка легла заметкой")
say(saved[0][3] == "телеграм", "источник помечен")
say("Сохранил 2" in sent[-1] and "tiktok" in sent[-1], f"ответ: {sent[-1]!r}")

sent.clear()
bot._save_incoming(["https://www.tiktok.com/@u/video/7"], "")
say("Уже было: 1" in sent[-1], f"повтор не сохраняется: {sent[-1]!r}")

sent.clear()
bot._save_incoming([f"https://vk.com/video{i}" for i in range(30)], "")
say(len(saved) == 2 + 1 + bot.MAX_LINKS_PER_MESSAGE - 1
    or len(saved) <= 2 + bot.MAX_LINKS_PER_MESSAGE,
    f"из 30 адресов взято не больше {bot.MAX_LINKS_PER_MESSAGE}")
say("не взял" in sent[-1], "про остаток сказано вслух")

# --- 5. ссылка и видео в одном сообщении -----------------------------
print("\n--- видео важнее ссылки ---")
routed = []
bot._download = lambda file_id, dest: routed.append("качаю") or ""
before = len(saved)
bot.load_settings = lambda: {"token": "x", "owner": "1"}
bot._handle({"chat": {"id": "1"}, "caption": "вот https://vk.com/video1_2",
             "video": {"file_id": "f"}})
say(len(saved) == before, "сообщение с видео не пошло в ссылки")
say(routed == ["качаю"], "оно пошло в публикацию")

# --- 6. как это выглядит в подборке ----------------------------------
print("\n--- строка подборки ---")
row = {"likes": 0, "author": "", "app": "shorts", "caption": "в ролик",
       "session": "телеграм", "url": "https://youtube.com/shorts/abc"}
block = bot._link_block(row)
say("♥ 0" not in block, "нет обманного «♥ 0»")
say("прислано в чат" in block and "shorts" in block, f"строка: {block.splitlines()[0]!r}")

row2 = dict(row, likes=230500, author="valentin", app="tiktok", session="20260820")
say(bot._link_block(row2).startswith("♥ 230 500 · valentin"),
    "у лайкнутого с телефона всё как было")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
