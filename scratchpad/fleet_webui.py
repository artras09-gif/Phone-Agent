"""Сквозная проверка вкладки «Флот» по HTTP, как её видит страница.

Окно поднимается настоящее, но команду запуска службы подменяем на
подставную: настоящая повела бы живой телефон.
"""
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

PORT = 8791
STUB = os.path.join(HERE, "stub_serve2.py")

with open(STUB, "w", encoding="utf-8", newline="\n") as f:
    f.write(f'''import os, sys, time
sys.path.insert(0, {ROOT!r})
import fleet
serial = os.environ.get("ANDROID_SERIAL", "?")
should_stop = fleet.install_child_stopper(serial)
print(f"подставная служба {{serial}} на связи", flush=True)
while not should_stop():
    time.sleep(0.3)
print("выхожу", flush=True)
''')

LAUNCH = f'''import sys
sys.path.insert(0, {ROOT!r})
import fleet, webui
fleet.child_command = lambda serial: [sys.executable, {STUB!r}]
webui.run(port={PORT}, open_browser=False)
'''

srv = subprocess.Popen([sys.executable, "-c", LAUNCH], cwd=ROOT,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, encoding="utf-8", errors="replace")

BASE = f"http://127.0.0.1:{PORT}"
TOKEN = None
ok = True


def say(good, text):
    global ok
    ok &= good
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def post(cmd, body):
    req = urllib.request.Request(
        BASE + "/api/" + cmd, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Token": TOKEN})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))


try:
    # --- дождаться сервера и вытащить токен со страницы ---
    page = None
    for _ in range(60):
        try:
            with urllib.request.urlopen(BASE + "/", timeout=5) as r:
                page = r.read().decode("utf-8")
            break
        except Exception:
            time.sleep(0.5)
    if page is None:
        raise SystemExit("окно не поднялось")

    import re
    TOKEN = re.search(r'TOKEN\s*=\s*"([^"]+)"', page).group(1)
    print("окно поднялось, токен получен\n")

    print("--- страница ---")
    say('data-tab="fleet"' in page, "вкладка «Флот» есть в разметке")
    say('id="fleet-list"' in page, "список телефонов есть")

    print("\n--- состояние ---")
    st = get("/api/state")
    say("fleet" in st, "состояние отдаёт раздел fleet")
    rows = st.get("fleet", [])
    say(bool(rows), f"телефонов в списке: {len(rows)}")
    here = [d for d in rows if d.get("here")]
    say(len(here) == 1, "ровно один помечен как открытый в окне")

    print("\n--- открытый в окне телефон служба не забирает ---")
    res = post("fleet-start", {"serial": here[0]["serial"]})
    say(res.get("ok") is False, "отказ получен")
    say("окн" in (res.get("error") or "").lower(),
        f"причина названа: {res.get('error', '')[:60]}")

    print("\n--- служба для другого телефона ---")
    other = "СТЕНД-ФЛОТ"
    res = post("fleet-start", {"serial": other})
    say(res.get("ok") is True, f"запустилась: {res.get('message') or res.get('error')}")
    time.sleep(2)

    st = get("/api/state")
    row = next((d for d in st["fleet"] if d["serial"] == other), None)
    print("    строка состояния:", json.dumps(row, ensure_ascii=False))
    say(row is not None and row["running"], "в состоянии показана работающей")
    say(row is not None and row["managed"], "помечена как своя (запущена отсюда)")

    print("\n--- журнал службы ---")
    lg = get("/api/fleet?serial=" + urllib.parse.quote(other) + "&since=0")
    say(lg.get("total", 0) > 0, f"строк в журнале: {lg.get('total')}")
    say(any("на связи" in ln for ln in lg.get("lines", [])),
        "видно, что служба отчиталась")

    print("\n--- повторный запуск той же службы ---")
    res = post("fleet-start", {"serial": other})
    say(res.get("ok") is False, f"вторую не завели: {res.get('error', '')[:50]}")

    print("\n--- остановка ---")
    t0 = time.time()
    res = post("fleet-stop", {"serial": other})
    say(res.get("ok") is True, f"остановлена за {time.time() - t0:.1f} с")
    st = get("/api/state")
    row = next((d for d in st["fleet"] if d["serial"] == other), None)
    # Телефона нет на связи и служба его больше не ведёт — из списка он
    # уходит совсем, и это правильно: держать в окне строку про выдуманный
    # аппарат незачем. Настоящий телефон остаётся, потому что он в `known`.
    say(row is None or not row["running"],
        "в состоянии больше не работает" + (" (ушёл из списка)" if row is None else ""))

    print("\n--- настоящий телефон из списка не пропал ---")
    real = next((d for d in st["fleet"] if d["serial"] == here[0]["serial"]), None)
    say(real is not None, "открытый в окне телефон на месте")
    say(real is not None and not real["running"], "и службой не занят")

finally:
    srv.terminate()
    try:
        srv.wait(timeout=15)
    except subprocess.TimeoutExpired:
        srv.kill()
    try:
        os.remove(STUB)
    except OSError:
        pass

print("\nИТОГ:", "окно управляет флотом" if ok else "ЕСТЬ ПРОВАЛЫ")
sys.exit(0 if ok else 1)
