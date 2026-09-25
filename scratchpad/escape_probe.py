"""Отладка выхода из тупика: снять экран в файл и гонять по нему промпт.

Зачем: живой прогон `main.py escape` стоит минуту и каждый раз показывает
модели РАЗНЫЙ экран — сравнивать формулировки промпта на нём невозможно.
Здесь экран (кадр + дерево) один раз кладётся на диск, а дальше вопрос
задаётся сколько угодно раз, телефон при этом не трогается вообще.

    python scratchpad/escape_probe.py --save search    снять то, что на экране
    python scratchpad/escape_probe.py --ask search     спросить модель
    python scratchpad/escape_probe.py --ask search -n 5   пять раз подряд
    python scratchpad/escape_probe.py --list

Ни одного тапа не делает — только смотрит.
"""
import argparse
import os
import sys
import time
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import adb  # noqa: E402
import escape  # noqa: E402
import ui  # noqa: E402
import vision  # noqa: E402

SHOTS = os.path.join(HERE, "screens")


def save(name):
    os.makedirs(SHOTS, exist_ok=True)
    png = adb.exec_out("screencap -p", timeout=25)
    adb.shell("uiautomator dump /sdcard/pa_probe.xml", timeout=30, check=False)
    xml = adb.exec_out("cat /sdcard/pa_probe.xml", timeout=30)
    with open(os.path.join(SHOTS, name + ".png"), "wb") as f:
        f.write(png)
    with open(os.path.join(SHOTS, name + ".xml"), "wb") as f:
        f.write(xml)
    print(f"снято: {name} (кадр {len(png) // 1024} КБ, дерево {len(xml) // 1024} КБ)")


def load(name):
    with open(os.path.join(SHOTS, name + ".png"), "rb") as f:
        png = f.read()
    with open(os.path.join(SHOTS, name + ".xml"), "rb") as f:
        xml = f.read().decode("utf-8", "replace")
    nodes = [ui.Node(el.attrib) for el in ET.fromstring(xml).iter("node")]
    return png, nodes


def ask(name, times, w, h, shrink=None, allow_done=False):
    png, nodes = load(name)
    menu = escape._menu(nodes, w, h)
    texts = escape._texts(nodes)

    print(f"--- {name}: {len(nodes)} узлов, {len(menu)} кнопок ---")
    print("надписи:", ", ".join(texts) or "(нет)")
    print(escape._describe_menu(menu, w, h))
    print()

    prompt = escape.build_prompt(menu, [], w, h, allow_done=allow_done)
    hits = {}
    for i in range(times):
        t0 = time.time()
        raw = vision.ask(png, prompt, system=escape.SYSTEM, max_tokens=250,
                         temperature=0.1, shrink=shrink)
        data = escape.parse(raw, menu)
        took = time.time() - t0
        action, button = data["действие"], data["кнопка"]
        target = (" «" + escape._label_of(menu[button - 1]) + "»") if button else ""
        hits[action + target] = hits.get(action + target, 0) + 1
        print(f"[{i + 1}] {took:4.1f}с  {action}{target}"
              f"   экран: {str(data.get('экран', ''))[:40]}"
              f"   почему: {str(data.get('почему', ''))[:50]}")
        if action == "none" and "непонятн" in str(data.get("почему", "")):
            print("     сырой ответ:", raw[:200].replace("\n", " "))
    if times > 1:
        print("\nразброс:", ", ".join(f"{k} x{v}" for k, v in
                                      sorted(hits.items(), key=lambda kv: -kv[1])))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--save", metavar="ИМЯ")
    p.add_argument("--ask", metavar="ИМЯ")
    p.add_argument("-n", type=int, default=1, help="сколько раз спросить")
    p.add_argument("--shrink", type=int, default=None,
                   help="во сколько раз ужать кадр (по умолчанию как в config)")
    p.add_argument("--done", action="store_true",
                   help="разрешить ответ done (ленты с деревом: shorts, reels)")
    p.add_argument("--list", action="store_true")
    args = p.parse_args()

    if args.list:
        names = sorted(f[:-4] for f in os.listdir(SHOTS) if f.endswith(".xml")) \
            if os.path.isdir(SHOTS) else []
        print("\n".join(names) or "(пусто)")
        return 0
    if args.save:
        save(args.save)
        return 0
    if args.ask:
        ask(args.ask, args.n, 1080, 2400, args.shrink, args.done)
        return 0
    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
