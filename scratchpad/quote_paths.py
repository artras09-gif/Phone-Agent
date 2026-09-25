"""Путь к видео уходит на телефон В КАВЫЧКАХ — во всех местах.

Живьём 2026-08-30: ролик «проверка маршрута.mp4» не публиковался ни в TikTok,
ни в Shorts — «приложение не открылось». Причина была в `media_uri`:
`content call --arg` получал путь без кавычек, пробел делил его надвое, скан
не находил файл, адрес `content://` не возвращался, и всё сваливалось на
`file://`, который эти два приложения игнорируют.

Телефон здесь не нужен: `adb.shell` подменяется и запоминает команды.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import adb  # noqa: E402
import device  # noqa: E402

HARD = "/sdcard/Movies/проверка маршрута.mp4"
CALLS = []

ok = True


def check(name, cond, got=""):
    global ok
    ok = ok and bool(cond)
    print(("  OK  " if cond else " ПРОВАЛ ") + name + (f"\n        {got}" if got and not cond else ""))


def fake_shell(cmd, **kw):
    CALLS.append(cmd)
    # Ответ скана: адрес в медиатеке.
    if "scan_file" in cmd:
        return "Result: Bundle[{uri=content://media/external_primary/video/media/42}]"
    return ""


def main():
    adb.shell = fake_shell
    device.adb.shell = fake_shell
    device.human.pause = lambda *a, **k: None

    CALLS.clear()
    uri = device.media_uri(HARD)
    call = next((c for c in CALLS if "scan_file" in c), "")
    check("скан получил путь целиком, в кавычках",
          "'/sdcard/Movies/проверка маршрута.mp4'" in call, call)
    check("адрес разобран", uri and uri.endswith("/42"), str(uri))

    CALLS.clear()
    device.share_video(HARD, "com.zhiliaoapp.musically", caption="подпись")
    send = next((c for c in CALLS if "action.SEND" in c), "")
    check("STREAM в кавычках", "--eu android.intent.extra.STREAM '" in send, send)

    # Запасной путь `file://` — там пробелы бывают всегда.
    CALLS.clear()
    device.share_video(HARD, "com.google.android.youtube", use_content=False)
    send = next((c for c in CALLS if "action.SEND" in c), "")
    check("file:// тоже в кавычках",
          f"'file://{HARD}'" in send, send)

    CALLS.clear()
    device.remove_remote(HARD)
    check("удаление в кавычках",
          any(f"rm -f '{HARD}'" in c for c in CALLS), " | ".join(CALLS))

    # Опасное имя не должно исполниться как команда.
    CALLS.clear()
    device.remove_remote("/sdcard/Movies/x'; rm -rf /sdcard; echo '.mp4")
    joined = " ".join(CALLS)
    check("кавычка внутри имени обезврежена", "'\\''" in joined, joined)

    print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПРОВАЛЫ")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
