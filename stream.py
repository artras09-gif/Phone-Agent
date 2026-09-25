"""Кадры из видеопотока телефона вместо screencap.

Зачем: `screencap -p` заставляет телефон кодировать PNG всего экрана —
0.5-1.6 секунды на кадр, и это самая дорогая часть цикла. `screenrecord`
отдаёт готовый H.264 непрерывно, там кадр стоит доли миллисекунды.

Как устроено. Фоновый поток пишет короткие куски видео в память, а когда
нужен кадр, накопленный кусок разово прогоняется через ffmpeg — офлайн
декодирование идёт в десятки раз быстрее реального времени, поэтому это
дешевле съёмки экрана.

Почему куски короткие, а не один длинный поток: `screenrecord` отдаёт
заголовок H.264 и опорный кадр РОВНО ОДИН РАЗ, в самом начале. Дальше идут
только разностные кадры — за 12 секунд 172 разностных и ни одного опорного.
Стоит началу выпасть из буфера, и декодировать становится нечего. Поэтому
запись перезапускается каждые несколько секунд: каждая начинается со своего
заголовка и декодируется сама по себе.

Почему не «живой» конвейер screenrecord | ffmpeg: ffmpeg на нём молчит,
сколько ему ни настраивай probesize и буферы (проверено). Разовый прогон
короткого куска работает предсказуемо.

Ограничения честно:
  * нужен ffmpeg (в комплекте его нет, ищется в PATH и рядом с проектом);
  * `screenrecord` живёт максимум 180 секунд, поток перезапускается сам;
  * пока экран погашен, поток пустой — это ловится как обычный пустой кадр;
  * кодирование видео греет телефон сильнее, чем редкие снимки.
"""
import os
import shutil
import subprocess
import threading
import time

import config

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
IEND = b"IEND\xaeB`\x82"
# Маркеры PNG нужны для нарезки вывода ffmpeg на отдельные кадры.

FFMPEG_PATHS = (
    r"C:\Users\Admin\Desktop\Agent\whisper\ffmpeg.exe",
    os.path.join(config.BASE, "ffmpeg.exe"),
    os.path.join(os.path.dirname(config.BASE), "tools", "ffmpeg.exe"),
)


def ffmpeg_exe():
    """Где взять ffmpeg. Без него поток не работает — вернём None."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    return next((p for p in FFMPEG_PATHS if os.path.exists(p)), None)


class Stream:
    """Живой поток кадров с телефона. Использовать как менеджер контекста."""

    def __init__(self, size=None, bitrate=None, seconds=None):
        self.size = size or config.STREAM_SIZE
        self.bitrate = bitrate or config.STREAM_BITRATE
        # Длина одного куска. Короткий — чаще перезапуск, но и декодировать
        # меньше; длинный — дороже декодирование каждого кадра.
        self.seconds = seconds or config.STREAM_CHUNK_SEC
        self.ffmpeg = ffmpeg_exe()
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._proc = None
        self._last_png = None       # последний удачный кадр — на время перезапуска
        self._last_at = 0.0

    # ---------------------------------------------------------- запуск

    def start(self):
        if not self.ffmpeg:
            raise RuntimeError(
                "ffmpeg не найден — поток кадров недоступен. Положи ffmpeg.exe "
                "рядом с проектом или добавь в PATH, либо выключи "
                "config.FRAME_SOURCE = 'screencap'.")
        self._stop.clear()
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()
        return self

    def _pump(self):
        """Читать H.264 в буфер, перезапуская запись по истечении лимита."""
        while not self._stop.is_set():
            cmd = [config.ADB]
            if config.SERIAL:
                cmd += ["-s", config.SERIAL]
            cmd += ["exec-out",
                    f"screenrecord --output-format=h264 --size {self.size} "
                    f"--bit-rate {self.bitrate} --time-limit {self.seconds} -"]
            try:
                self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                              stderr=subprocess.DEVNULL)
            except OSError:
                return

            fresh = bytearray()
            while not self._stop.is_set():
                chunk = self._proc.stdout.read1(65536)
                if not chunk:
                    break
                fresh += chunk
                # Подменяем буфер целиком, а не дописываем: кусок должен
                # начинаться с заголовка, иначе ffmpeg его не разберёт.
                with self._lock:
                    self._buffer = fresh

            self._proc.kill()

    # ---------------------------------------------------------- кадры

    def frame(self, timeout=6.0, max_age=1.0):
        """Последний кадр в PNG или None, если поток ещё не разогнался.

        Пока запись перезапускается, свежего куска нет. Отдать предыдущий
        кадр можно, но только совсем молодой: агент вызывает это сразу после
        свайпа, и кадр постарше показал бы предыдущий ролик — решение
        принялось бы не по тому видео.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            chunk = self._tail()
            if chunk:
                png = self._decode(chunk)
                if png:
                    self._last_png, self._last_at = png, time.time()
                    return png
            elif (self._last_png is not None
                    and time.time() - self._last_at < max_age):
                return self._last_png
            time.sleep(0.15)
        return None

    def _tail(self):
        """Текущий кусок, если в нём уже есть что декодировать.

        Кусок всегда начинается с заголовка H.264 — так устроен перезапуск
        записи. Ждём, пока наберётся хоть один кадр, иначе ffmpeg вернёт
        пустоту, а мы зря потратим запуск процесса.
        """
        with self._lock:
            data = bytes(self._buffer)
        return data if len(data) > config.STREAM_MIN_KB * 1024 else None

    def _decode(self, chunk):
        """Последний кадр куска H.264 в PNG."""
        try:
            done = subprocess.run(
                [self.ffmpeg, "-loglevel", "error", "-framerate", "30",
                 "-f", "h264", "-i", "pipe:0", "-vf", "fps=2",
                 "-f", "image2pipe", "-vcodec", "png", "pipe:1"],
                input=chunk, capture_output=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return None

        out = done.stdout
        last = out.rfind(PNG_MAGIC)
        if last < 0:
            return None
        end = out.find(IEND, last)
        return out[last:end + len(IEND)] if end > 0 else out[last:]

    # ---------------------------------------------------------- останов

    def stop(self):
        """Погасить поток и, главное, добить запись на самом телефоне.

        Убить процесс adb на своей стороне мало: `screenrecord` на телефоне
        доживает до своего лимита и всё это время грузит устройство и канал.
        Из-за такого хвоста соседние команды adb проседали с 0.3 до 9 секунд.

        Совпадение по точному имени, а не по строке запуска: иначе под раздачу
        попадает системный `com.miui.screenrecorder`, который трогать нельзя
        и не нужно.
        """
        self._stop.set()
        if self._proc:
            self._proc.kill()
        if self._thread:
            self._thread.join(timeout=3)

        cmd = [config.ADB]
        if config.SERIAL:
            cmd += ["-s", config.SERIAL]
        try:
            subprocess.run(cmd + ["shell", "pkill -x screenrecord"],
                           capture_output=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            pass

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
        return False
