"""Эталоны путей к статистике — без телефона.

Когда модель находит дорогу к статистике (`statnav.py`), рядом с путём
сохраняются деревья экранов, на которых она жала: `analytics/routes/<сеть>/
<версия приложения>/шагN.json` + `route.json`. А если запомненный путь
оборвался — `обрыв_шагN.json`, экран, на котором он оборвался.

Эта проверка проходит по всем эталонам, какие есть:
  * в папке проекта `scratchpad/golden/` (сюда кладутся эталоны, присланные
    с других компьютеров — скопировать папку `routes` целиком);
  * в данных программы на этом ПК (`%LOCALAPPDATA%\\PhoneAgent\\...\\routes`).

и проверяет, что каждый шаг пути узнаётся на своём экране (`statnav.match`).
Сломалось после правки `match` — видно сразу. Обрывы печатаются отдельно:
это список того, что приложения поменяли.

Эталонов ещё нет — проверяется сам механизм на учебном примере.
"""
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import statnav  # noqa: E402

ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


def roots():
    out = [os.path.join(HERE, "golden")]
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        out += glob.glob(os.path.join(local, "PhoneAgent", "analytics", "routes"))
        out += glob.glob(os.path.join(local, "PhoneAgent", "devices", "*", "analytics", "routes"))
    return [r for r in out if os.path.isdir(r)]


def check_route(folder):
    with open(os.path.join(folder, "route.json"), encoding="utf-8") as f:
        route = json.load(f)
    found = 0
    for i, step in enumerate(route.get("steps", []), 1):
        if not step.get("tree"):
            continue
        path = os.path.join(folder, step["tree"] + ".json")
        if not os.path.isfile(path):
            say(False, f"{folder}: нет экрана {step['tree']}")
            continue
        with open(path, encoding="utf-8") as f:
            shot = json.load(f)
        nodes = statnav.tree_from_json(shot["nodes"])
        node = statnav.match(nodes, step, shot["w"], shot["h"])
        say(node is not None, f"{route.get('net')} {route.get('ver')}: шаг {i} "
                              f"«{step.get('label') or step.get('rid') or 'значок'}» узнаётся")
        found += 1
    return found


def breaks(folder):
    for path in sorted(glob.glob(os.path.join(folder, "обрыв_шаг*.json"))):
        with open(path, encoding="utf-8") as f:
            shot = json.load(f)
        labels = [n.get("text") or n.get("content-desc") for n in shot["nodes"]
                  if n.get("clickable") == "true" and (n.get("text") or n.get("content-desc"))]
        print(f"  обрыв: {os.path.relpath(path, os.path.dirname(folder))} — на экране: "
              + ", ".join(labels[:10]))


total = 0
for root in roots():
    for route in sorted(glob.glob(os.path.join(root, "*", "*", "route.json"))):
        total += check_route(os.path.dirname(route))
    for folder in sorted(glob.glob(os.path.join(root, "*", "*"))):
        breaks(folder)

if not total:
    print("Эталонов пока нет — проверяю механизм на учебном примере")
    import tempfile
    import ui
    tmp = tempfile.mkdtemp(prefix="pa_golden_")

    def node(label, y):
        return ui.Node({"text": label, "clickable": "true", "enabled": "true",
                        "class": "android.widget.Button", "bounds": f"[100,{y}][900,{y + 120}]"})

    screen = [node("Меню", 600), node("Архив", 800)]
    sig = statnav.signature(screen[0], 1080, 2400)
    sig["tree"] = "шаг1"
    os.makedirs(os.path.join(tmp, "reels", "1.0"))
    statnav._keep_tree(os.path.join(tmp, "reels", "1.0"), "шаг1", screen, 1080, 2400)
    with open(os.path.join(tmp, "reels", "1.0", "route.json"), "w", encoding="utf-8") as f:
        json.dump({"net": "reels", "ver": "1.0", "steps": [sig]}, f, ensure_ascii=False)
    say(check_route(os.path.join(tmp, "reels", "1.0")) == 1, "учебный эталон сохранён и узнаётся")

print("ВСЁ ХОРОШО" if ok else "ЕСТЬ ОШИБКИ")
sys.exit(0 if ok else 1)
