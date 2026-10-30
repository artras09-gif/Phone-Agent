"""Окно-пульт и службы телефонов (2026-10-30: «нужно, чтобы телефоны работали
одновременно, а сейчас даже переключиться между ними не даёт, если у одного
из них сессия»).

Пульт поднимается настоящий, по HTTP, как его видит страница. Телефонов два,
подставных: `adb.devices` отдаёт A и B, а служба каждого — маленький HTTP-
сервер, который помнит «идёт ли сессия» и пишет свой журнал. Проверяем:
переключение посреди сессии, две сессии разом, журнал выбранного, видео —
в очередь выбранного, остановку одного, и что службы выходят сами, когда
окна не стало.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PORT = 8793
TMP = tempfile.mkdtemp(prefix="pa_hub_")
CONTROL = os.path.join(TMP, ".fleet")

STUB = os.path.join(TMP, "stub_worker.py")
with open(STUB, "w", encoding="utf-8", newline="\n") as f:
    f.write(r'''import http.server, json, os, sys, threading, time, urllib.parse
serial = os.environ["ANDROID_SERIAL"]
control = os.environ["HUB_TEST_CONTROL"]
state = {"busy": False, "action": "", "lines": [f"служба {serial} на связи"], "uploads": []}

class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send_json(self, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(200); self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        p = urllib.parse.urlparse(self.path); q = urllib.parse.parse_qs(p.query)
        since = int((q.get("since") or ["0"])[0])
        if p.path == "/api/brief":
            return self.send_json({"busy": state["busy"], "action": state["action"]})
        if p.path == "/api/log":
            return self.send_json({"total": len(state["lines"]), "lines": state["lines"][since:],
                                   "busy": state["busy"], "action": state["action"], "elapsed": 0})
        if p.path == "/api/state":
            return self.send_json({"phone": {"ok": True, "text": "телефон " + serial},
                                   "busy": state["busy"], "action": state["action"],
                                   "queue": [{"id": n, "name": u} for n, u in enumerate(state["uploads"])],
                                   "who": serial})
        self.send_response(404); self.end_headers()
    def do_POST(self):
        if self.headers.get("X-Token") != "tok-" + serial:
            return self.send_json({"ok": False, "error": "чужой токен"})
        n = int(self.headers.get("Content-Length") or 0); raw = self.rfile.read(n)
        p = urllib.parse.urlparse(self.path)
        if p.path == "/api/upload":
            state["uploads"].append(urllib.parse.parse_qs(p.query)["name"][0] + f":{len(raw)}")
            return self.send_json({"ok": True, "message": "в очереди " + serial})
        cmd = p.path.rsplit("/", 1)[-1]
        if cmd == "session":
            if state["busy"]:
                return self.send_json({"ok": False, "error": "сейчас идёт другое"})
            app = json.loads(raw or b"{}").get("app", "tiktok")
            state.update(busy=True, action="сессия " + app)
            state["lines"].append(f"{serial}: смотрю {app}")
            return self.send_json({"ok": True, "message": "смотрю ленту " + serial})
        if cmd == "cancel":
            state.update(busy=False, action="")
            return self.send_json({"ok": True})
        return self.send_json({"ok": True, "echo": cmd, "who": serial})

srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
os.makedirs(control, exist_ok=True)
tmp = os.path.join(control, serial + ".json.tmp")
with open(tmp, "w", encoding="utf-8") as f:
    json.dump({"serial": serial, "port": srv.server_address[1], "token": "tok-" + serial,
               "pid": os.getpid()}, f)
os.replace(tmp, os.path.join(control, serial + ".json"))
print(f"служба {serial} готова", flush=True)

def stopper():
    stop = os.path.join(control, serial + ".stop")
    def eof():
        while sys.stdin.buffer.read(1024): pass
        srv.shutdown()
    threading.Thread(target=eof, daemon=True).start()
    while not os.path.exists(stop): time.sleep(0.3)
    srv.shutdown()
threading.Thread(target=stopper, daemon=True).start()
srv.serve_forever()
print(f"служба {serial} вышла", flush=True)
''')

LAUNCH = f'''import os, sys
sys.path.insert(0, {ROOT!r})
import config, devices, fleet, adb, prefs, webui
config.BASE = {TMP!r}
config.LOG_DIR = os.path.join({TMP!r}, "logs")
config.DB_PATH = os.path.join({TMP!r}, "jobs.db")
config.STOP_FILE = os.path.join({TMP!r}, "STOP")
prefs.PATH = os.path.join({TMP!r}, "settings.json")
devices.ROOT = os.path.join({TMP!r}, "devices")
fleet.CONTROL = {CONTROL!r}
fleet.control_path = lambda s: os.path.join({CONTROL!r}, s + ".json")
fleet.stop_path = lambda s: os.path.join({CONTROL!r}, s + ".stop")
fleet.child_command = lambda serial: [sys.executable, {STUB!r}]
os.environ["HUB_TEST_CONTROL"] = {CONTROL!r}
adb.devices = lambda: [("PHONE_A", "device"), ("PHONE_B", "device")]
adb.raw = lambda *a, **k: ("Модель " + a[1][-1]) if a and a[0] == "-s" else ""
webui.HUB_EVERY = 0.5
webui.run(port={PORT}, open_browser=False)
'''

hub = subprocess.Popen([sys.executable, "-c", LAUNCH], cwd=ROOT,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, encoding="utf-8", errors="replace")
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = None
ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def post(cmd, body=None, raw=None, query=""):
    data = raw if raw is not None else json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(BASE + "/api/" + cmd + query, data=data,
                                 headers={"Content-Type": "application/json", "X-Token": TOKEN})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def wait(check, seconds=20):
    end = time.time() + seconds
    while time.time() < end:
        try:
            if check():
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


def phones():
    return {d["serial"]: d for d in get("/api/state").get("spaces", [])}


try:
    page = None
    for _ in range(60):
        try:
            with urllib.request.urlopen(BASE + "/", timeout=5) as r:
                page = r.read().decode("utf-8")
            break
        except Exception:
            time.sleep(0.5)
    if page is None:
        raise SystemExit("пульт не поднялся")
    TOKEN = page.split('const TOKEN = "', 1)[1].split('"', 1)[0]

    print("--- пульт сам поднимает службу каждому телефону ---")
    say(wait(lambda: all(d.get("ready") for d in phones().values()) and len(phones()) == 2),
        "обе службы на связи")
    s = get("/api/state")
    say(s.get("hub") and s["device"] == "PHONE_A" and s.get("who") == "PHONE_A",
        f"на главной первый телефон, состояние — от его службы: {s.get('device')}")

    print("\n--- сессия на A, переключение на B посреди неё, сессия на B ---")
    r = post("session", {"app": "tiktok"})
    say(r.get("ok") and "PHONE_A" in r.get("message", ""), f"кнопка ушла службе A: {r}")
    say(wait(lambda: phones()["PHONE_A"]["busy"]), "A занят")
    r = post("device", {"serial": "PHONE_B"})
    say(r.get("ok"), f"переключение посреди сессии на A — можно: {r.get('message')}")
    s = get("/api/state")
    say(s["device"] == "PHONE_B" and s.get("who") == "PHONE_B" and not s.get("busy"),
        "на главной B: свободен, хотя A смотрит ленту")
    say(s["spaces"][0]["busy"] and "сессия tiktok" in s["spaces"][0]["action"],
        f"в переключателе видно, что A занят: {s['spaces'][0].get('action')}")
    r = post("session", {"app": "reels"})
    say(r.get("ok") and "PHONE_B" in r.get("message", ""), "сессия на B запущена")
    say(wait(lambda: all(d["busy"] for d in phones().values())), "оба телефона работают одновременно")

    print("\n--- журнал и очередь — выбранного телефона ---")
    log = get("/api/log?since=0")
    say(log.get("device") == "PHONE_B" and any("PHONE_B: смотрю reels" in l for l in log["lines"])
        and not any("PHONE_A" in l for l in log["lines"]), f"журнал B без строк A: {log['lines']}")
    r = post("upload", raw=b"x" * 2048,
             query="?name=" + urllib.parse.quote("ролик.mp4") + "&targets=tiktok")
    s = get("/api/state")
    say(r.get("ok") and "PHONE_B" in r.get("message", "") and s["queue"] == [{"id": 0, "name": "ролик.mp4:2048"}],
        "видео ушло в очередь B целиком")
    r = post("taste", {"topic": "монтаж"})
    say(r.get("echo") == "taste" and r.get("who") == "PHONE_B", "остальные кнопки — тоже службе B")
    r = post("cancel")
    say(wait(lambda: not phones()["PHONE_B"]["busy"]) and phones()["PHONE_A"]["busy"],
        "«Остановить» остановил B, A продолжает")

    print("\n--- остановка одного телефона ---")
    r = post("fleet-stop", {"serial": "PHONE_B"})
    say(r.get("ok"), f"служба B остановлена: {r}")
    time.sleep(2.5)
    fl = {d["serial"]: d for d in get("/api/state")["fleet"]}
    say(not fl["PHONE_B"]["running"] and fl["PHONE_B"]["paused"] and fl["PHONE_A"]["running"],
        "остановленный руками сам не поднимается, A работает")
    r = post("session", {"app": "tiktok"})
    say(not r.get("ok") and "Флот" in r.get("error", ""), f"кнопка на остановленном — понятный отказ: {r.get('error')}")
    r = post("fleet-start", {"serial": "PHONE_B"})
    say(r.get("ok") and wait(lambda: phones()["PHONE_B"]["ready"]), "запустили снова — на связи")

    print("\n--- окна не стало — службы выходят сами ---")
    ports = []
    for name in ("PHONE_A", "PHONE_B"):
        with open(os.path.join(CONTROL, name + ".json"), encoding="utf-8") as f:
            ports.append(json.load(f)["port"])
    hub.kill()                                   # окно упало, а не закрылось
    hub.wait(10)

    def dead(port):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/brief", timeout=1)
            return False
        except Exception:
            return True
    say(wait(lambda: all(dead(p) for p in ports), 15), "обе службы вышли, телефонов без окна не осталось")
finally:
    if hub.poll() is None:
        hub.kill()

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
