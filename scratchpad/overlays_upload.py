"""Перед публикацией соседние видеоприложения закрываются (force-stop), но НЕ
то, что ещё грузит прошлое видео (живьём 2026-10-22: YouTube через VPN грузил
дольше 15 минут, а следующая публикация убила бы его загрузку)."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import adb     # noqa: E402
import device  # noqa: E402

stopped = []
adb.shell = lambda cmd, **k: stopped.append(cmd.split()[-1]) or ""
uploading = {"com.google.android.youtube": (87, 100)}
device.upload_progress = lambda pkg: uploading.get(pkg)
device.close_overlays(["com.zhiliaoapp.musically", "com.google.android.youtube", "com.instagram.android"],
                      keep="com.zhiliaoapp.musically")
ok = stopped == ["com.instagram.android"]
print(f"  {'OK ' if ok else 'НЕТ'} закрыт только Instagram, YouTube грузит — не тронут: {stopped}")
print("\nИТОГ:", "всё сошлось" if ok else "ЕСТЬ ОШИБКИ")
sys.exit(0 if ok else 1)
