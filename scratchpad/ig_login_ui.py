"""Вход в Instagram для монтажа — кнопкой в окне (без браузера и сети).

Просьба 2026-11-25: «запасной акк должно быть легко привязать в интерфейсе».
Проверяется: статус «входа нет / окно открыто / вход есть до …», кнопка
«Войти» открывает окно один раз и ждёт в фоне, готовый cookies.txt
принимается и проверяется, «Выйти» стирает вход и профиль браузера, а команды
выполняет само окно, а не служба телефона. Боевая папка montage/ не трогается.
"""
import os
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402

tmp = tempfile.mkdtemp(prefix="pa_iglogin_")
config.BASE = tmp

import iglogin    # noqa: E402
import linkfetch  # noqa: E402
import webui      # noqa: E402

ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


linkfetch.HOME = os.path.join(tmp, "montage")
linkfetch.COOKIES = os.path.join(linkfetch.HOME, "cookies.txt")
iglogin.PROFILE = os.path.join(linkfetch.HOME, "browser")
GOOD = ("# Netscape HTTP Cookie File\n"
        ".instagram.com\tTRUE\t/\tTRUE\t1893456000\tsessionid\tSECRET123\n"
        ".instagram.com\tTRUE\t/\tTRUE\t1893456000\tcsrftoken\tabc\n")
cmd = webui.COMMANDS

print("1. Команды — самого окна, не телефона")
say({"ig-login", "ig-cookies", "ig-logout"} <= webui.HUB_COMMANDS, "все три — в HUB_COMMANDS")
page = open(os.path.join(os.path.dirname(HERE), "webui.html"), encoding="utf-8").read()
say(all(x in page for x in ('id="ig-card"', 'id="b-ig-login"', 'id="b-ig-logout"', "renderIg(s)")),
    "карточка, кнопки и отрисовка — на странице")

print("2. Входа нет")
st = webui._ig_state()
say(not st["has"] and "входа нет" in st["text"], f"статус: {st['text']}")

print("3. «Войти» — окно открывается один раз, вход ждётся в фоне")
gate = threading.Event()
opened = []


def fake_login():
    opened.append(1)
    gate.wait(5)
    return linkfetch.save_cookies(GOOD)


iglogin.login = fake_login
iglogin.browser = lambda: r"C:\fake\msedge.exe"
r = cmd["ig-login"]({})
say(r["ok"] and "открыл" in r["message"], f"ответ: {r.get('message')}")
time.sleep(0.2)
say(webui._ig_state()["busy"] and "окно входа открыто" in webui._ig_state()["text"],
    "статус: окно открыто")
r2 = cmd["ig-login"]({})
say(r2["ok"] and "уже открыто" in r2["message"], "второе нажатие окно не дублирует")
say(cmd["ig-logout"]({})["ok"] is False, "пока окно открыто — «Выйти» не стирает")
gate.set()
for _ in range(50):
    if not webui.IGLOGIN["busy"]:
        break
    time.sleep(0.1)
st = webui._ig_state()
say(len(opened) == 1 and st["has"] and "действует до" in st["text"] and "2030" in st["text"],
    f"вход забран: {st['text']}")
say("SECRET123" not in str(st), "содержимое cookies наружу не отдаётся")

print("4. Нет браузера")
iglogin.browser = lambda: None
r = cmd["ig-login"]({})
say(r["ok"] is False and "cookies.txt" in r["error"], "честно говорит и предлагает файл")

print("5. Готовый cookies.txt")
r = cmd["ig-cookies"]({"text": "это не куки"})
say(r["ok"] is False and "Netscape" in r["error"], f"чужой файл — отказ: {r['error'][:50]}")
r = cmd["ig-cookies"]({"text": GOOD})
say(r["ok"] and "приняты" in r["message"], f"принят: {r['message']}")

print("6. «Выйти»")
os.makedirs(os.path.join(iglogin.PROFILE, "Default"), exist_ok=True)
r = cmd["ig-logout"]({})
say(r["ok"] and not os.path.exists(linkfetch.COOKIES) and not os.path.exists(iglogin.PROFILE),
    "вход и профиль браузера стёрты")
say(not webui._ig_state()["has"], "статус снова «входа нет»")

print("7. Просроченный вход")
cmd["ig-cookies"]({"text": GOOD.replace("1893456000", "1600000000")})
st = webui._ig_state()
say(not st["has"] and "устарел" in st["text"], f"статус: {st['text']}")

print("ВСЁ ХОРОШО" if ok else "ЕСТЬ ОШИБКИ")
sys.exit(0 if ok else 1)
