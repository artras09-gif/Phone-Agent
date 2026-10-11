# -*- coding: utf-8 -*-
"""Прочесать собранный exe: нет ли внутри ключей, токенов и чужих файлов."""
import json
import marshal
import os
import re
import tempfile

from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

PROJ = r"C:\Users\Admin\Desktop\PhoneAgent"
EXE = os.path.join(os.path.expanduser("~"), "Desktop", "PhoneAgent.exe")

secrets = []
for name, fields in (("settings.json", ("api_key",)), ("telegram.json", ("token",))):
    try:
        with open(os.path.join(PROJ, name), encoding="utf-8") as f:
            d = json.load(f)
        secrets += [str(d[k]) for k in fields if len(str(d.get(k) or "")) > 10]
    except (OSError, ValueError):
        pass
print(f"живых секретов на машине для сверки: {len(secrets)}")

pat = re.compile(rb"sk-[A-Za-z0-9_-]{20,}|[0-9]{8,10}:AA[A-Za-z0-9_-]{30,}")
car = CArchiveReader(EXE)
names = list(car.toc.keys())
blobs = {}
for n in names:
    try:
        blobs[n] = car.extract(n)
    except Exception:
        pass

pyz_name = next((n for n in names if n.upper().startswith("PYZ")), None)
mods = 0
if pyz_name:
    tmp = os.path.join(tempfile.gettempdir(), "pa_pyz.bin")
    with open(tmp, "wb") as f:
        f.write(car.extract(pyz_name))
    z = ZlibArchiveReader(tmp)
    for m in z.toc:
        try:
            code = z.extract(m)
            blobs["модуль " + m] = code if isinstance(code, bytes) else marshal.dumps(code)
            mods += 1
        except Exception:
            pass

print(f"записей в exe: {len(names)}, модулей Python: {mods}")
files = sorted(n for n in names if n.lower().endswith(
    (".json", ".db", ".txt", ".html", ".apk", ".png", ".ico", ".log")))
print("файлы данных внутри:", files)
bad = [n for n in names if os.path.basename(n) in
       ("settings.json", "telegram.json", "jobs.db", "pin.txt", "remote.txt")]
print("файлы с тайнами внутри:", bad or "НЕТ")

leak = 0
for n, b in blobs.items():
    if not isinstance(b, (bytes, bytearray)):
        continue
    for s in secrets:
        if s.encode() in b:
            print("  !!! живой секрет в", n)
            leak += 1
    for m in pat.findall(b):
        if b"0123456789abcdef" not in m:
            print("  подозрительно в", n, m[:14])
            leak += 1
print("ключ и токен внутри exe:", "НАЙДЕНЫ" if leak else "нет")

# Пути этой машины, зашитые в модули (скомпилированный код хранит строки как есть)
paths = {}
for n, b in blobs.items():
    if isinstance(b, (bytes, bytearray)) and n.startswith("модуль ") and b"Users\\Admin" in b:
        paths[n] = b.count(b"Users\\Admin")
print("модули со строкой 'Users\\Admin':", paths or "нет")
