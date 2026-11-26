"""Вшитые ffmpeg и yt-dlp раскладываются сами и работают без сети.

26.11 монтаж не начался: на ПК был ffmpeg без ffprobe, программа пошла
докачивать полный ffmpeg с gyan.dev, а связи в ту минуту не было. Теперь оба
инструмента вшиты в exe (`build_exe.pack_ffmpeg`, `pack_ytdlp`): ffmpeg —
архивом, при первом запуске его достаёт `bundled.unzip_ffmpeg`.

Проверяется без сборки и без сети: архив распаковывается и убирается, монтаж
берёт вшитый ffmpeg, а yt-dlp — вшитый, если своего ещё нет.
"""
import os
import socket
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402

tmp = tempfile.mkdtemp(prefix="pa_bundle_")
config.BASE = tmp


def no_net(*a, **k):
    raise OSError("сеть выключена для проверки")


socket.create_connection = no_net
socket.getaddrinfo = no_net

import bundled    # noqa: E402
import linkfetch  # noqa: E402

ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


tools = os.path.join(tmp, "tools")
os.makedirs(os.path.join(tools, "ffmpeg"))
os.makedirs(os.path.join(tools, "yt-dlp"))
with zipfile.ZipFile(os.path.join(tools, "ffmpeg", "ffmpeg.zip"), "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("ffmpeg.exe", b"MZ" + b"\0" * 5000)
with open(os.path.join(tools, "yt-dlp", "yt-dlp"), "wb") as f:
    f.write(b"PK-fake-yt-dlp")

print("1. Раскладка ffmpeg")
bundled.unzip_ffmpeg(tools)
exe = os.path.join(tools, "ffmpeg", "ffmpeg.exe")
say(os.path.getsize(exe) == 5002, "ffmpeg.exe достан из архива")
say(not os.path.exists(os.path.join(tools, "ffmpeg", "ffmpeg.zip")), "архив убран (36 МБ не лежат зря)")
bundled.unzip_ffmpeg(tools)
say(os.path.exists(exe), "второй запуск ничего не ломает")

print("2. Монтаж берёт вшитый ffmpeg, ffprobe не требует")
ff, fp = linkfetch.ffmpeg_tools()
say(ff == exe and fp is None, f"ffmpeg_tools: {ff!r}, {fp!r}")

print("3. yt-dlp без сети — вшитый")
path, updated = linkfetch.ensure("stable-py")
with open(path, "rb") as f:
    say(f.read() == b"PK-fake-yt-dlp" and not updated, "свой ещё не скачан — взят вшитый, сети не нужно")

print("ВСЁ ХОРОШО" if ok else "ЕСТЬ ОШИБКИ")
sys.exit(0 if ok else 1)
