"""Служба телефона не падает, когда окна уже нет (2026-10-30: при обновлении
служба печатала «служба телефона остановлена» в трубу ушедшего окна, падала с
OSError 22, и окно ошибки PyInstaller держало PhoneAgent.exe — новый файл не
вставал на место).

Настоящий дочерний процесс: печатает строку, окно (этот тест) закрывает трубу
журнала и stdin, служба печатает ещё — и должна выйти сама, с кодом 0, а
строки после ухода окна — оказаться в её файле журнала.
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TMP = tempfile.mkdtemp(prefix="pa_worker_out_")
LOG = os.path.join(TMP, "служба-X.log")

CHILD = f'''import sys
sys.path.insert(0, {ROOT!r})
import fleet
fleet.log_path = lambda serial: {LOG!r}
fleet.guard_output("X")
print("служба на связи", flush=True)
while sys.stdin.buffer.read(1024):        # окно живо, пока открыт stdin
    pass
for n in range(50):                       # окна нет — печатаем дальше
    print(f"после окна {{n}}", flush=True)
print("служба телефона остановлена", flush=True)
'''

ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
proc = subprocess.Popen([sys.executable, "-c", CHILD], stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, stdin=subprocess.PIPE, env=env)
first = proc.stdout.readline().decode("utf-8", "replace").strip()
say(first == "служба на связи", f"служба печатает в окно: {first!r}")
proc.stdout.close()                       # окно ушло: читать журнал некому
proc.stdin.close()
code = proc.wait(timeout=30)
say(code == 0, f"служба вышла сама, без падения (код {code})")
text = open(LOG, encoding="utf-8").read() if os.path.exists(LOG) else ""
say("служба телефона остановлена" in text and "после окна 49" in text,
    "строки после ухода окна — в её журнале")

print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)
