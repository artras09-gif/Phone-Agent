"""Убрать кадры и логи за ПРОШЛЫЕ дни. Сегодняшние остаются.

Через Python, а не Remove-Item: на этой машине оболочка режет удаление по
маске (см. память про хук на Remove-Item).
"""
import datetime as dt
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402

TODAY = dt.date.today()


def roots():
    base = config.BASE
    out = [os.path.join(base, "frames"), os.path.join(base, "logs")]
    devices = os.path.join(base, "devices")
    if os.path.isdir(devices):
        for name in os.listdir(devices):
            for sub in ("frames", "logs"):
                out.append(os.path.join(devices, name, sub))
    return [p for p in out if os.path.isdir(p)]


def size_of(path):
    total = 0
    for folder, _, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(folder, name))
            except OSError:
                pass
    return total


def main():
    dry = "--yes" not in sys.argv
    removed, freed, kept = 0, 0, 0
    for root in roots():
        for name in sorted(os.listdir(root)):
            path = os.path.join(root, name)
            if not os.path.isdir(path):
                continue
            when = dt.date.fromtimestamp(os.path.getmtime(path))
            if when >= TODAY:
                kept += 1
                continue
            freed += size_of(path)
            removed += 1
            if not dry:
                shutil.rmtree(path, ignore_errors=True)
    verb = "нашлось" if dry else "удалено"
    print(f"{verb} папок за прошлые дни: {removed}, "
          f"это {freed / 1024 / 1024:.1f} МБ")
    print(f"сегодняшних оставлено: {kept}")
    if dry:
        print("\nэто был показ; для удаления — с флагом --yes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
