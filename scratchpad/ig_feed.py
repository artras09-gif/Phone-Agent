"""Instagram: «Главная» против ленты Reels — без телефона.

Живой замер 2026-09-30: в ленте Reels дерево НЕ снимается (играет видео), на
«Главной» снимается за 3-4 с, и там у вкладки «Дом» selected=true. Раньше
признаком ленты были отметки «Видео Reels» в дереве, которого в ленте нет, —
и агент, нажав с «Главной» вкладку Reels, считал, что туда не попал.

Дерево ниже — нижняя панель настоящей «Главной» (подписи и места те же).
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import bundled  # noqa: E402
import device   # noqa: E402
import human    # noqa: E402
import runlog   # noqa: E402
import session  # noqa: E402
import ui       # noqa: E402

ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


def node(desc, x0, selected=False, y0=2138, clickable=True):
    return ui.Node({"text": "", "content-desc": desc, "resource-id": "",
                    "class": "android.widget.FrameLayout", "package": "com.instagram.android",
                    "clickable": "true" if clickable else "false", "enabled": "true",
                    "selected": "true" if selected else "false",
                    "bounds": f"[{x0},{y0}][{x0 + 216},2177]"})


def nav(selected_desc):
    tabs = ["Дом", "Reels", "Сообщение", "Поиск и интересное", "Профиль"]
    return [node("Instagram", 380, y0=120, clickable=False)] + \
        [node(d, i * 216, selected=(d == selected_desc)) for i, d in enumerate(tabs)] + \
        [node("", 0, y0=2300, clickable=False)]          # панель системы ниже


with open(os.path.join(os.path.dirname(HERE), "recipes.json"), encoding="utf-8") as f:
    CFG = json.load(f)["_feed_apps"]["reels"]

screens, taps, links = [], [], []
ui.dump = lambda *a, **kw: screens.pop(0) if screens else []
ui.tap_node = lambda n: taps.append(n.desc)
device.open_uri = lambda uri, pkg=None: links.append(uri)
human.pause = lambda lo, hi: None
session.time.sleep = lambda s: None
session.abort.sleep = lambda s, step=0.2: False     # ожидание ленты — без настоящих секунд


def run(*trees):
    del taps[:], links[:]
    screens[:] = list(trees)
    log = runlog.Log(live=False)
    return session._ensure_feed(CFG, log), log.text()


print("--- рецепт ---")
say(CFG.get("feed_no_tree") is True, "в ленте Reels дерева нет — пустое дерево = лента")
say(CFG.get("feed_selected"), "признак ленты — выбранная вкладка Reels")
say(CFG.get("feed_check_every"), f"проверка «не на Главной ли» раз в {CFG.get('feed_check_every')} роликов")

print("\n--- _ensure_feed ---")
got, text = run([])
say(got and not taps, "дерево не снялось (лента играет) — в ленте, ничего не жмём")
got, text = run(nav("Reels"))
say(got and not taps, "вкладка Reels выбрана — в ленте, ничего не жмём")
got, text = run(nav("Дом"), [])
say(got and taps == ["Reels"], f"с «Главной»: нажата Reels, и это признано успехом: {taps}")
say("«Дом»" in text, "в журнале сказано, где были")
# Вкладка не сработала: пока не открыта ссылка, экран всё время «Главная».
plain_dump = ui.dump
ui.dump = lambda *a, **kw: [] if links else nav("Дом")
real_time = session.time.time
clock = [0.0]
session.time.time = lambda: clock.__setitem__(0, clock[0] + 1.0) or clock[0]
got, text = run()
ui.dump, session.time.time = plain_dump, real_time
say(got and links == ["instagram://reels_home"], f"вкладка не помогла — прямая ссылка: {links}")
say("прямой ссылке" in text, "и это сказано в журнале")

print("\n--- проверка по ходу сессии ---")
screens[:] = [nav("Дом")]
say(session._off_feed(CFG) == "Дом", "на «Главной» -> «Дом»")
screens[:] = [[]]
say(session._off_feed(CFG) == "", "дерево не снялось -> в ленте")
screens[:] = [nav("Reels")]
say(session._off_feed(CFG) == "", "Reels выбрана -> в ленте")
screens[:] = [nav("Профиль")]
say(session._off_feed(CFG) == "Профиль", "в профиле -> «Профиль»")

print("\n--- новые ключи рецепта доезжают до старой установки ---")
tmp = tempfile.mkdtemp(prefix="pa_merge_")
old = json.loads(json.dumps({"_feed_apps": {"reels": {
    k: v for k, v in CFG.items() if k not in ("feed_no_tree", "feed_selected", "feed_check_every")}}}))
old["_feed_apps"]["reels"]["open_uri"] = "instagram://моя_правка"
packed = os.path.join(tmp, "packed.json")
mine = os.path.join(tmp, "recipes.json")
with open(packed, "w", encoding="utf-8") as f:
    json.dump({"_feed_apps": {"reels": CFG}}, f, ensure_ascii=False)
with open(mine, "w", encoding="utf-8") as f:
    json.dump(old, f, ensure_ascii=False)
added = bundled.merge_new_keys(packed, mine)
with open(mine, encoding="utf-8") as f:
    merged = json.load(f)["_feed_apps"]["reels"]
say(added == 3 and merged.get("feed_no_tree") is True, f"дописано ключей: {added}")
say(merged["open_uri"] == "instagram://моя_правка", "правка человека не затёрта")
say(bundled.merge_new_keys(packed, mine) == 0, "второй раз дописывать нечего — файл не трогаем")
shutil.rmtree(tmp, ignore_errors=True)

print("\n--- признак selected в дереве ---")
n = nav("Дом")[1]
say(n.selected and ui.find([n], desc="Дом", selected=True), "selected читается и ищется")
say(not ui.find([n], desc="Дом", selected=False), "и отличается от невыбранной")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
