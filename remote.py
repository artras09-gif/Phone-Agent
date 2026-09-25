"""Запасное подключение к телефону по сети. Основной способ — кабель.

Модуль спит, пока не поднят `config.REMOTE_ENABLED`: при воткнутом кабеле
он и не нужен, а кабель ещё и быстрее — кадр снимается за ~0.3 с против 1-3 с
по сети, и это на каждый ролик.

Когда включён, работает так: Android 11+ умеет «Беспроводную отладку», где
первое подключение авторизуется шестизначным кодом с экрана телефона. Чем-то
подтвердить его всё равно надо, иначе доступ получил бы любой сосед по сети.

Порядок:

1. На телефоне: Для разработчиков -> Беспроводная отладка -> включить ->
   «Подключение с помощью кода сопряжения». Там показаны адрес, порт и код.
2. `remote pair <адрес>:<порт> <код>` — сопряжение. Ключ ПК запоминается
   телефоном навсегда, второй раз код не понадобится.
3. Сразу после сопряжения агент фиксирует порт (`tcpip 5555`). Без этого
   порт у беспроводной отладки случайный и меняется при каждом включении,
   а значит, подключаться пришлось бы каждый раз вручную.
4. Дальше `adb connect <адрес туннеля>:5555` — и всё работает само.

Адрес берётся из туннеля (Tailscale и совместимые): телефон не обязан быть
в одной сети с ПК, трафик шифруется, а адрес `100.x` закреплён за
устройством и не меняется, в отличие от адреса по DHCP.

Про безопасность честно: `adb tcpip` открывает порт на всех интерфейсах
сразу, выбрать «только туннель» Android не даёт. Защита тут одна, зато
рабочая — ключ: чужой компьютер, подключившийся к порту, получит на телефоне
запрос «Разрешить отладку?» и без подтверждения ничего не сделает.
В недоверенной сети режим лучше выключать: `python main.py remote off`.

Что портит малину: **перезагрузка телефона** сбрасывает и режим 5555, и порт
беспроводной отладки. Сопряжение при этом сохраняется, так что достаточно
подсмотреть новый порт на экране телефона и выполнить `remote resume <порт>`.

Адрес запоминается в remote.txt, и adb.ensure() сам подключается к нему.
"""
import json
import os
import re
import shutil
import subprocess
import time

import adb
import config

ADDR_FILE = os.path.join(config.BASE, "remote.txt")
DEFAULT_PORT = 5555


def saved():
    """Запомненный адрес телефона или None."""
    try:
        with open(ADDR_FILE, encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def remember(address):
    try:
        with open(ADDR_FILE, "w", encoding="utf-8") as f:
            f.write(address)
    except OSError:
        pass


def forget():
    try:
        os.remove(ADDR_FILE)
    except OSError:
        pass


# ----------------------------------------------------------- ZeroTier

ZEROTIER_PATHS = (
    r"C:\Program Files (x86)\ZeroTier\One\zerotier-cli.bat",
    r"C:\Program Files\ZeroTier\One\zerotier-cli.bat",
)


def zerotier_cli():
    found = shutil.which("zerotier-cli")
    if found:
        return found
    return next((p for p in ZEROTIER_PATHS if os.path.exists(p)), None)


def zerotier_networks():
    """Сети ZeroTier, в которых состоит этот ПК: [(nwid, имя, статус, [ip/len])]."""
    cli = zerotier_cli()
    if not cli:
        return []
    try:
        out = subprocess.run([cli, "listnetworks"], capture_output=True, timeout=30)
        text = out.stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return []

    nets = []
    for line in text.splitlines():
        # 200 listnetworks <nwid> <name> <mac> <status> <type> <dev> <ips>
        # Первая такая строка — заголовок, в ней стоят literal-плейсхолдеры.
        parts = line.split()
        if len(parts) < 9 or parts[0] != "200" or parts[2].startswith("<"):
            continue
        ips = [ip for ip in parts[8].split(",") if "." in ip and ip != "-"]
        nets.append((parts[2], parts[3], parts[5], ips))
    return nets


def zerotier_subnet():
    """Наш адрес и подсеть в ZeroTier: (свой ip, [все адреса подсети]) или (None, [])."""
    for _, _, status, ips in zerotier_networks():
        if status != "OK" or not ips:
            continue
        addr = ips[0].partition("/")[0]
        octets = addr.split(".")
        if len(octets) != 4:
            continue
        # Сканируем /24 вокруг себя: у ZeroTier это типовой размер выдачи,
        # а перебирать /16 ради поиска одного телефона бессмысленно.
        base = ".".join(octets[:3])
        return addr, [f"{base}.{i}" for i in range(1, 255) if f"{base}.{i}" != addr]
    return None, []


def _port_open(host, port, timeout=0.4):
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def scan_subnet(hosts, port=None, workers=64):
    """Найти в подсети адреса с открытым портом adb. Возвращает список."""
    import threading

    port = port or DEFAULT_PORT
    found, lock = [], threading.Lock()
    queue_ = list(hosts)

    def worker():
        while True:
            with lock:
                if not queue_:
                    return
                host = queue_.pop()
            if _port_open(host, port):
                with lock:
                    found.append(host)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return sorted(found)


# ------------------------------------------------------------- туннель

def tailscale_exe():
    """Где лежит tailscale.exe. В PATH его кладут не всегда."""
    found = shutil.which("tailscale")
    if found:
        return found
    for path in (r"C:\Program Files\Tailscale\tailscale.exe",
                 r"C:\Program Files (x86)\Tailscale\tailscale.exe",
                 os.path.expanduser(r"~\AppData\Local\Tailscale\tailscale.exe")):
        if os.path.exists(path):
            return path
    return None


def tailscale_status():
    """Разобранный `tailscale status --json` или None, если туннеля нет."""
    exe = tailscale_exe()
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "status", "--json"], capture_output=True,
                             timeout=30)
        return json.loads(out.stdout.decode("utf-8", "replace"))
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def tunnel_peers(status=None):
    """Устройства в туннеле: [(имя, ip, онлайн, ос), ...]."""
    status = status if status is not None else tailscale_status()
    if not status:
        return []
    peers = []
    for peer in (status.get("Peer") or {}).values():
        ips = peer.get("TailscaleIPs") or []
        ipv4 = next((ip for ip in ips if ":" not in ip), ips[0] if ips else "")
        peers.append((
            (peer.get("HostName") or peer.get("DNSName", "").split(".")[0] or "?"),
            ipv4,
            bool(peer.get("Online")),
            (peer.get("OS") or "").lower(),
        ))
    return peers


def find_phone(name_hint=None, status=None):
    """Найти телефон среди устройств туннеля: (имя, ip) или (None, причина).

    Ищем по имени, если оно задано, иначе по признаку ОС: Android в туннеле
    обычно один. Оффлайновые устройства не предлагаем — подключение к ним
    всё равно не пройдёт.
    """
    peers = tunnel_peers(status)
    if not peers:
        return None, ("туннель не отвечает: проверь, что Tailscale запущен "
                      "и ты вошёл в аккаунт (tailscale up)")

    if name_hint:
        hits = [p for p in peers if name_hint.lower() in p[0].lower()]
        if not hits:
            names = ", ".join(p[0] for p in peers[:8]) or "пусто"
            return None, f"в туннеле нет устройства «{name_hint}». Есть: {names}"
    else:
        hits = [p for p in peers if p[3] == "android"]
        if not hits:
            names = ", ".join(f"{p[0]} ({p[3] or '?'})" for p in peers[:8]) or "пусто"
            return None, ("не нашёл Android в туннеле. Поставь на телефон "
                          f"Tailscale и войди тем же аккаунтом. Сейчас там: {names}")

    online = [p for p in hits if p[2]] or hits
    name, ip = online[0][0], online[0][1]
    if not ip:
        return None, f"у устройства «{name}» нет адреса в туннеле"
    return (name, ip), ""


def connect(address, timeout=20):
    """adb connect. Возвращает (успех, текст ответа)."""
    out = adb.raw("connect", address, check=False, timeout=timeout)
    ok = "connected to" in out.lower() and "cannot" not in out.lower()
    return ok, out.strip()


def disconnect(address=None):
    return adb.raw("disconnect", *( [address] if address else [] ),
                   check=False, timeout=15).strip()


def wireless_devices():
    """Подключённые по сети устройства: у них в серийнике есть ':'."""
    return [s for s, state in adb.devices() if ":" in s and state == "device"]


def any_device():
    """Любое живое устройство: серийник или None. USB тут не при чём."""
    return next((s for s, state in adb.devices() if state == "device"), None)


def fix_port(serial, port=DEFAULT_PORT):
    """Закрепить за телефоном постоянный порт.

    Порт беспроводной отладки случайный и меняется при каждом её включении.
    `tcpip` переводит adbd на фиксированный порт — и подключаться дальше
    можно без подглядывания в экран телефона. Команде всё равно, по какому
    каналу она пришла: работает и поверх самой беспроводной отладки.
    """
    adb.raw("-s", serial, "tcpip", str(port), check=False, timeout=30)
    time.sleep(2.5)


def mdns_candidates():
    """Телефоны, которые сами объявились в локальной сети (mDNS).

    Работает только когда ПК и телефон в одной сети: через туннель
    мультикаст не ходит. Нужно, чтобы не заставлять переписывать
    случайный порт с экрана вручную, когда телефон рядом.
    """
    out = adb.raw("mdns", "services", check=False, timeout=20)
    found = []
    for line in out.splitlines():
        m = re.search(r"(_adb[-\w]*\._tcp)\s+(\d+\.\d+\.\d+\.\d+):(\d+)", line)
        if m:
            found.append((m.group(1), f"{m.group(2)}:{m.group(3)}"))
    return found


def phone_host(name_hint=None):
    """Где искать телефон: (адрес без порта, подпись) или (None, причина).

    Порядок общий для любого туннеля: сначала запомненный адрес, потом
    автоопределение. Автоматика есть для Tailscale (читает его статус) и для
    ZeroTier (ищет в подсети открытый порт adb); для остальных адрес задаётся
    руками через `remote set`.
    """
    address = saved()
    if address:
        return address.split(":")[0], "запомненный адрес"

    found, _ = find_phone(name_hint)
    if found:
        return found[1], f"{found[0]} в Tailscale"

    own, hosts = zerotier_subnet()
    if own:
        live = scan_subnet(hosts)
        if live:
            return live[0], "найден в подсети ZeroTier"

    return None, ("не нашёл телефон в туннеле. Задай адрес руками:\n"
                  "    python main.py remote set 10.40.223.172")


def tunnel_address(name_hint=None, port=DEFAULT_PORT):
    """Адрес телефона с портом: (адрес, подпись) или (None, причина)."""
    address = saved()
    if address:
        return address, "запомненный адрес"

    host, why = phone_host(name_hint)
    return (f"{host}:{port}", why) if host else (None, why)


def setup(name_hint=None, port=DEFAULT_PORT):
    """Подключиться к телефону в туннеле и закрепить постоянный порт.

    Расчёт на то, что сопряжение уже сделано (`remote pair`) или телефон
    уже слушает порт. Возвращает (успех, текст).
    """
    address, label = tunnel_address(name_hint, port)
    if not address:
        return False, label

    ok = connect(address)[0]
    if ok:
        remember(address)
        return True, f"{address} ({label})"

    # Порт не открыт. Если телефон доступен другим путём (свежее сопряжение
    # по беспроводной отладке), закрепим порт через него.
    serial = any_device()
    if serial:
        fix_port(serial, port)
        for _ in range(3):
            ok = connect(address)[0]
            if ok:
                remember(address)
                return True, f"{address} ({label})"
            time.sleep(1.5)

    return False, (
        f"телефон найден в туннеле ({address}), но порт закрыт.\n"
        "Так бывает после перезагрузки телефона. Что делать:\n"
        "  1. На телефоне: Для разработчиков -> Беспроводная отладка -> включить\n"
        "  2. Посмотреть там «IP-адрес и порт», например 192.168.1.5:41234\n"
        "  3. python main.py remote resume 41234   (если сопряжение уже было)\n"
        "     или python main.py remote pair <адрес:порт> <код>  (первый раз)"
    )


def resume(port_hint, name_hint=None, port=DEFAULT_PORT):
    """Восстановить связь после перезагрузки телефона, зная текущий порт.

    Сопряжение переживает перезагрузку, а порт — нет. Подключаемся к тому,
    что телефон показывает сейчас, и сразу закрепляем постоянный порт.
    """
    ip, why = phone_host(name_hint)
    if not ip:
        return False, why

    ok, out = connect(f"{ip}:{port_hint}")
    if not ok:
        return False, (f"не подключился к {ip}:{port_hint}: {out}\n"
                       "Проверь, что беспроводная отладка включена, "
                       "и что порт взят с её экрана.")

    fix_port(f"{ip}:{port_hint}", port)
    for _ in range(3):
        ok, _ = connect(f"{ip}:{port}")
        if ok:
            remember(f"{ip}:{port}")
            return True, f"{ip}:{port} ({why})"
        time.sleep(1.5)

    # Порт закрепить не вышло, но текущее подключение живо — работать можно.
    remember(f"{ip}:{port_hint}")
    return True, (f"{ip}:{port_hint} ({why}, порт временный — "
                  "после перезагрузки телефона повторить)")


def pair(address, code):
    """Сопряжение по коду (Android 11+, «Беспроводная отладка»)."""
    out = adb.raw("pair", address, code, check=False, timeout=60).strip()
    return ("successfully" in out.lower() or "уже" in out.lower()), out


def pair_and_settle(pair_address, code, connect_port=None, name_hint=None,
                    port=DEFAULT_PORT):
    """Полный первый запуск без единого касания кабеля.

    Сопряжение -> подключение -> закрепление постоянного порта -> переезд
    на адрес туннеля. Возвращает (успех, список строк отчёта).
    """
    report = []
    ok, out = pair(pair_address, code)
    report.append(out)
    if not ok:
        return False, report

    ip = pair_address.split(":")[0]

    # Порт для работы отличается от порта сопряжения: он написан на том же
    # экране телефона, выше кода. В одной сети его можно узнать по mDNS.
    target = f"{ip}:{connect_port}" if connect_port else None
    if not target:
        for service, addr in mdns_candidates():
            if "connect" in service and addr.startswith(ip + ":"):
                target = addr
                report.append(f"порт найден по mDNS: {addr}")
                break
    if not target:
        report.append(
            "не знаю порт для подключения. Посмотри на экране «Беспроводная "
            "отладка» строку «IP-адрес и порт» и повтори команду с ним:\n"
            f"    python main.py remote pair {pair_address} {code} --port <порт>")
        return False, report

    ok, out = connect(target)
    report.append(out)
    if not ok:
        return False, report

    fix_port(target, port)
    report.append(f"порт закреплён за телефоном: {port}")

    # Переезжаем на постоянный порт по тому же адресу, который дал пользователь:
    # если это адрес в туннеле, то он и запомнится — там он не меняется.
    stable = f"{ip}:{port}"
    for _ in range(3):
        ok, _ = connect(stable)
        if ok:
            remember(stable)
            report.append(f"работаем по адресу: {stable}")
            return True, report
        time.sleep(1.5)

    # Порт закрепить не вышло — живём на временном, он до перезагрузки телефона.
    remember(target)
    report.append(f"работаем по временному адресу: {target}")
    report.append("после перезагрузки телефона повторить: remote resume <порт>")
    return True, report


def status():
    """Текстовая сводка для doctor и команды remote."""
    address = saved()
    live = wireless_devices()
    lines = []
    if address:
        lines.append(f"запомнен адрес: {address}"
                     + ("  (подключён)" if address in live else "  (не подключён)"))
    else:
        lines.append("подключение по сети не настроено")

    nets = zerotier_networks()
    if nets:
        for nwid, name, state, ips in nets:
            mark = "OK" if state == "OK" else state
            lines.append(f"ZeroTier: сеть {nwid} «{name}» — {mark}, "
                         f"наш адрес {', '.join(ips) or 'не выдан'}")
            if state == "ACCESS_DENIED":
                lines.append("   подтверди это устройство в Members на my.zerotier.com")
    elif zerotier_cli():
        lines.append("ZeroTier: установлен, но ни в одной сети "
                     "(zerotier-cli join <ID сети>)")

    if tailscale_exe():
        peers = tunnel_peers()
        if peers:
            lines.append(f"Tailscale: {len(peers)} устройств")
            for name, ip, online, os_name in peers[:6]:
                lines.append(f"   {'●' if online else '○'} {name} ({os_name or '?'}) {ip}")
    for serial in live:
        if serial != address:
            lines.append(f"подключён по сети: {serial}")
    usb = [s for s, st in adb.devices() if ":" not in s and st == "device"]
    if usb:
        lines.append(f"по USB: {', '.join(usb)}")
    return "\n".join(lines)
