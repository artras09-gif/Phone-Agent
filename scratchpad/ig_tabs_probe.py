"""Чем «Главная» Instagram отличается от ленты Reels в дереве экрана.

Только смотрит и переключает нижние вкладки — ничего не лайкает, не пишет и
не публикует (аккаунт личный). Дампы кладутся в scratchpad/screens/ — эта
папка в .gitignore: там имена чужих аккаунтов из ленты.

    python scratchpad\\ig_tabs_probe.py
"""
import os
import sys
import time
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import adb     # noqa: E402
import device  # noqa: E402
import prefs   # noqa: E402
import ui      # noqa: E402

prefs.apply()
PKG = "com.instagram.android"
OUT = os.path.join(HERE, "screens")
os.makedirs(OUT, exist_ok=True)
MARKS = ("Видео Reels", "Reels video", "Reel by", "Ваша история", "Your story")


def raw_dump(name):
    """Сырой XML (с атрибутом selected) + сводка по нижней панели и шапке."""
    for _ in range(3):
        out = adb.shell("uiautomator dump /sdcard/pa_probe.xml 2>&1", timeout=30, check=False)
        if "dumped to" in out or "UI hierchary" in out:
            break
        time.sleep(1.5)
    else:
        print(f"  [{name}] дерево НЕ снялось: {out.strip()[:80]}")
        return
    xml = adb.exec_out("cat /sdcard/pa_probe.xml", timeout=30).decode("utf-8", "replace")
    with open(os.path.join(OUT, f"ig_{name}.xml"), "w", encoding="utf-8") as f:
        f.write(xml)
    nodes = list(ET.fromstring(xml).iter("node"))
    print(f"  [{name}] узлов: {len(nodes)}")
    h = 2400
    for n in nodes:
        a = n.attrib
        b = a.get("bounds", "")
        try:
            y = int(b.split("][")[1].split(",")[1].rstrip("]"))
        except (IndexError, ValueError):
            y = 0
        label = a.get("content-desc") or a.get("text") or ""
        rid = a.get("resource-id", "").split("/")[-1]
        low = y > h * 0.88 and a.get("clickable") == "true"      # нижняя панель
        top = y < h * 0.12 and (label or rid)                    # шапка
        if low or top or a.get("selected") == "true" and label:
            print(f"    {'низ ' if low else 'верх' if top else 'выбр'} "
                  f"sel={a.get('selected')} click={a.get('clickable')} "
                  f"desc={a.get('content-desc')!r} text={a.get('text')!r} id={rid} {b}")
    hits = [m for m in MARKS if m.lower() in xml.lower()]
    print(f"    отметки в дереве: {hits}")


device.wake()
device.unlock()
time.sleep(1.5)
print("--- открываю ленту Reels ссылкой ---")
device.open_uri("instagram://reels_home", PKG)
device.wait_for_app(PKG, timeout=25)
time.sleep(4)
raw_dump("reels")

tabs = ui.dump(retries=2, tolerant=True, timeout=15)
home = next((n for n in tabs if n.clickable and n.desc in ("Главная", "Home")), None)
print("\n--- на «Главную» ---", "вкладка найдена" if home else "вкладки «Главная» в дереве нет")
if home:
    ui.tap_node(home)
    time.sleep(3.5)
    raw_dump("home")
    reels = next((n for n in ui.dump(retries=2, tolerant=True, timeout=15)
                  if n.clickable and n.desc in ("Reels", "Клипы")), None)
    print("\n--- обратно в Reels ---", "вкладка найдена" if reels else "вкладки Reels нет")
    if reels:
        ui.tap_node(reels)
        time.sleep(3.5)
        raw_dump("reels2")

device.go_home()
device.lock()
print("\nготово, телефон заблокирован")
