"""Достать вшитые инструменты (adb, scrcpy, свой сервер Telegram) из прежнего exe.

    python tools_from_release.py PhoneAgent.exe env/tools

Сборка на GitHub идёт на чистой Windows-машине: ни ~/tools, ни env/tools там
нет, а без adb `build_exe.py` не собирает вовсе. Скачивать заново — значит
получить другие версии, а свой сервер Telegram Bot API собран из исходников и
скачать его негде. Поэтому берём ровно те же файлы из прошлого релиза: exe
PyInstaller'а — это архив (CArchive) в хвосте файла, а инструменты лежат в нём
под `tools/` (см. `build_exe.py`).
"""
import os
import struct
import sys
import zlib

MAGIC = b"MEI\014\013\012\013\016"
COOKIE = struct.Struct("!8sIIii64s")      # magic, длина пакета, TOC, длина TOC, python, libpython
ENTRY = struct.Struct("!IIIIBc")          # длина записи, смещение, размер, распакованный, сжато, тип


def entries(path):
    with open(path, "rb") as f:
        data = f.read()
    at = data.rfind(MAGIC)
    if at < 0:
        raise SystemExit(f"{path}: это не exe PyInstaller'а (нет метки архива)")
    magic, length, toc, toc_len, _py, _lib = COOKIE.unpack_from(data, at)
    start = at + COOKIE.size - length
    pos, end = start + toc, start + toc + toc_len
    while pos < end:
        size, offset, stored, full, flag, kind = ENTRY.unpack_from(data, pos)
        name = data[pos + ENTRY.size:pos + size].rstrip(b"\0").decode("utf-8")
        blob = data[start + offset:start + offset + stored]
        yield name, kind, (zlib.decompress(blob) if flag else blob), full
        pos += size


def main():
    # Консоль Windows на машине сборки — cp1252: «КБ» в ней роняло скрипт.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    exe, dest = sys.argv[1], sys.argv[2]
    got = 0
    for name, _kind, blob, full in entries(exe):
        norm = name.replace("\\", "/")
        if not norm.startswith("tools/"):
            continue
        if len(blob) != full:
            raise SystemExit(f"{name}: размер не сошёлся ({len(blob)} вместо {full})")
        rel = norm[len("tools/"):]
        if not rel or ".." in rel.split("/") or os.path.isabs(rel):
            raise SystemExit(f"странный путь в архиве: {name}")
        target = os.path.join(dest, *rel.split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as f:
            f.write(blob)
        got += 1
        print(f"  {rel}  ({len(blob) // 1024} КБ)")
    if not got:
        raise SystemExit("в прежнем exe нет папки tools/")
    print(f"инструментов достано: {got}")


if __name__ == "__main__":
    main()
