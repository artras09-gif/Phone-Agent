"""Трансляция экрана телефона на ПК.

Основной путь — scrcpy: настоящий H.264-поток с задержкой 30-60 мс.
Запасной — встроенный просмотрщик на tkinter (медленно, ~1-2 кадра в секунду,
зато работает вообще без сторонних программ).
"""
import base64
import os
import shutil
import subprocess
import threading
import time

import adb
import config


def find_scrcpy():
    """Ищем scrcpy.exe: переменная окружения -> PATH -> папка tools."""
    explicit = os.environ.get("SCRCPY_PATH")
    if explicit and os.path.exists(explicit):
        return explicit

    found = shutil.which("scrcpy")
    if found:
        return found

    # tools рядом с проектом (переносимый комплект), потом tools в профиле
    for tools in (os.path.join(os.path.dirname(config.BASE), "tools"),
                  os.path.join(config.BASE, "tools"),
                  os.path.join(os.path.expanduser("~"), "tools")):
        if not os.path.isdir(tools):
            continue
        for entry in sorted(os.listdir(tools), reverse=True):   # свежая версия первой
            candidate = os.path.join(tools, entry, "scrcpy.exe")
            if os.path.exists(candidate):
                return candidate
    return None


class Mirror:
    """Окно трансляции. Умеет работать как менеджер контекста.

    По умолчанию управление мышью отключено: пока агент работает, случайный
    клик по окну сломает ему маршрут.
    """

    def __init__(self, title="PhoneAgent", control=False, record=None,
                 max_size=800, always_on_top=True):
        self.title = title
        self.control = control
        self.record = record
        self.max_size = max_size
        self.always_on_top = always_on_top
        self.proc = None

    def start(self):
        exe = find_scrcpy()
        if not exe:
            raise RuntimeError(
                "scrcpy не найден. Скачай отсюда:\n"
                "  https://github.com/Genymobile/scrcpy/releases\n"
                "распакуй в %USERPROFILE%\\tools\\ или задай SCRCPY_PATH.\n"
                "Либо запусти встроенный просмотрщик: python main.py mirror --simple"
            )

        cmd = [
            exe,
            "--window-title", self.title,
            "--max-size", str(self.max_size),
            "--no-audio",              # звук нам не нужен и он усложняет запуск
        ]
        if self.control:
            # scrcpy отказывается стартовать с --stay-awake при --no-control:
            # «Cannot request to stay awake if control is disabled».
            # Без управления экран всё равно держит device.keep_awake().
            cmd.append("--stay-awake")
        else:
            cmd.append("--no-control")
        if self.always_on_top:
            cmd.append("--always-on-top")
        if self.record:
            os.makedirs(os.path.dirname(self.record) or ".", exist_ok=True)
            cmd += ["--record", self.record]
        if config.SERIAL:
            cmd += ["-s", config.SERIAL]

        # Показываем scrcpy на наш adb, иначе он поднимет свой сервер другой
        # версии и передёрнет уже работающее подключение.
        env = dict(os.environ, ADB=config.ADB)

        # stderr держим у себя: если scrcpy откажется стартовать, причину
        # надо показать, а не проглотить.
        self.proc = subprocess.Popen(
            cmd, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        time.sleep(1.5)
        if self.proc.poll() is not None:
            err = (self.proc.stderr.read() or b"").decode("utf-8", "replace").strip()
            raise RuntimeError(
                "scrcpy завершился сразу"
                + (f":\n  {err.splitlines()[-1]}" if err else " — проверь подключение телефона")
            )
        return self

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        if self.alive():
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
        return False


def watch(**kwargs):
    """Трансляция на время работы блока. Если scrcpy нет — просто предупредим,
    ломать из-за этого публикацию не стоит."""

    class _Safe:
        def __init__(self):
            self.m = None

        def __enter__(self):
            try:
                self.m = Mirror(**kwargs).start()
            except RuntimeError as e:
                print(f"[трансляция недоступна] {e}")
            return self.m

        def __exit__(self, *exc):
            if self.m:
                self.m.stop()
            return False

    return _Safe()


# ------------------------------------------------ запасной просмотрщик

def simple_viewer(fps=2, scale=2):
    """Просмотрщик на tkinter без единой сторонней библиотеки.

    Медленный: каждый кадр — это отдельный screencap с PNG-сжатием на телефоне.
    Нужен на случай, когда scrcpy недоступен.
    """
    import tkinter as tk

    root = tk.Tk()
    root.title("PhoneAgent — простая трансляция")
    root.attributes("-topmost", True)

    label = tk.Label(root, bg="black")
    label.pack()
    status = tk.Label(root, text="подключение...", anchor="w")
    status.pack(fill="x")

    state = {"running": True, "img": None, "frame": None, "n": 0, "err": ""}

    def grab():
        while state["running"]:
            t0 = time.time()
            try:
                state["frame"] = adb.exec_out("screencap -p", timeout=20)
                state["n"] += 1
                state["err"] = ""
            except adb.AdbError as e:
                state["err"] = str(e)[:60]
            time.sleep(max(0, 1.0 / fps - (time.time() - t0)))

    def draw():
        if not state["running"]:
            return
        frame = state["frame"]
        if frame:
            try:
                img = tk.PhotoImage(data=base64.b64encode(frame).decode("ascii"))
                if scale > 1:
                    img = img.subsample(scale, scale)
                label.configure(image=img)
                state["img"] = img          # иначе картинку соберёт сборщик мусора
            except tk.TclError as e:
                state["err"] = f"кадр не отрисован: {e}"
        status.configure(
            text=state["err"] or f"кадр {state['n']}  ·  ~{fps} к/с  ·  только просмотр"
        )
        root.after(int(1000 / fps), draw)

    def on_close():
        state["running"] = False
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    threading.Thread(target=grab, daemon=True).start()
    root.after(300, draw)
    root.mainloop()
