"""Проверка, что службы РАЗНЫХ телефонов не смешиваются.

Это главный риск всей затеи: программа держит «с каким телефоном работаем» в
глобальных переменных, и если хоть одна не переехала вместе с процессом,
видео уйдёт в чужой аккаунт. Проверяем не рассуждением, а запуском.

Второй телефон для этого не нужен: пространство заводится по серийнику, а не
по наличию связи, — ровно так же оно работает для отключённого телефона.

Отдельно проверяем ту ловушку, на которой всё едва не сломалось: в
settings.json лежит сохранённый серийник, и раньше он перебивал
ANDROID_SERIAL — все службы уехали бы на ОДИН телефон.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

CHILD = r'''
import json, os, sys
sys.path.insert(0, {root!r})
import config, prefs, devices, jobs, adb
import plan as plan_mod

# Ровно то, что делает main() на старте любой команды.
prefs.apply()
chosen = config.SERIAL or ""
devices.use(chosen)

print(json.dumps({{
    "serial":    config.SERIAL,
    "adb":       adb._base(),
    "watch":     config.WATCH_DIR,
    "queue":     config.QUEUE_DIR,
    "frames":    config.FRAMES_DIR,
    "interests": config.INTERESTS,
    "plan":      plan_mod.PATH,
    "job_device": jobs.DEVICE,
}}, ensure_ascii=False))
'''


def probe(serial):
    env = dict(os.environ)
    env["ANDROID_SERIAL"] = serial
    env["PYTHONIOENCODING"] = "utf-8"
    out = subprocess.run([sys.executable, "-c", CHILD.format(root=ROOT)],
                         capture_output=True, text=True, encoding="utf-8",
                         env=env, cwd=ROOT, timeout=120)
    if out.returncode != 0:
        print(out.stdout, out.stderr)
        raise SystemExit(f"дочерний процесс упал для {serial}")
    return json.loads(out.stdout.strip().splitlines()[-1])


# Настроек может не быть вовсе — так выглядит свежая установка на другом ПК:
# `settings.json` хранит серийник и ключ от облачного зрения и в комплект для
# передачи не едет. Проверка от этого не зависит: она про то, что каждый
# процесс живёт в своём пространстве, а не про содержимое настроек.
try:
    with open(os.path.join(ROOT, "settings.json"), encoding="utf-8") as f:
        saved = json.load(f)
    print(f"в settings.json сохранён телефон: {saved.get('serial')!r}")
    print("(именно он раньше перебивал ANDROID_SERIAL)\n")
except (OSError, json.JSONDecodeError):
    saved = {}
    print("settings.json нет — свежая установка, проверяем на пустых настройках\n")

A = "74eed241"                 # настоящий, он же сохранённый в настройках
B = "СТЕНД-второй"             # выдуманный: пространство должно завестись само

a, b = probe(A), probe(B)

ok = True


def check(what, got, want):
    global ok
    good = got == want
    ok &= good
    print(f"  {'OK ' if good else 'НЕТ'} {what}: {got}")
    if not good:
        print(f"      ожидалось: {want}")


print(f"--- служба телефона {A} ---")
check("серийник", a["serial"], A)
check("команда adb", a["adb"][-2:], ["-s", A])

print(f"\n--- служба телефона {B} ---")
check("серийник", b["serial"], B)
check("команда adb", b["adb"][-2:], ["-s", B])
check("метка в базе", b["job_device"], B)

print("\n--- пространства не пересекаются ---")
for key in ("watch", "queue", "frames", "interests", "plan"):
    same = a[key] == b[key]
    ok &= not same
    print(f"  {'НЕТ' if same else 'OK '} {key}")
    if same:
        print(f"      ОБА указывают на {a[key]}")
    else:
        print(f"      {os.path.relpath(a[key], ROOT)}")
        print(f"      {os.path.relpath(b[key], ROOT)}")

print("\nИТОГ:", "телефоны разведены" if ok else "ЕСТЬ СМЕШЕНИЕ — НЕ ЗАПУСКАТЬ")
sys.exit(0 if ok else 1)
