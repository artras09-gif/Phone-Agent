"""Сведения о процессах и ярлык в «Пуске» — напрямую функциями Windows (ctypes).

Не Windows или функция недоступна — None / False, без исключений.
"""
# Зачем: 29.10 Касперский счёл PhoneAgent.exe трояном (UDS:Trojan.Win32.PyPower.ox)
# и лечил ПК с перезагрузкой — Python-программа скрыто запускала PowerShell. С
# 30.10 программа не запускает ни PowerShell, ни cmd: всё, за чем ходили к ним,
# Windows отдаёт сама — командную строку процесса, путь к его exe, ярлык.
# Описание здесь, а не в строке документации: та попадает в exe.
import ctypes
import os

if os.name == "nt":
    from ctypes import wintypes

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
COMMAND_LINE_INFORMATION = 60        # ProcessCommandLineInformation, Windows 8.1+
CLSCTX_INPROC_SERVER = 1
CLSID_SHELL_LINK = "{00021401-0000-0000-C000-000000000046}"
IID_SHELL_LINK_W = "{000214F9-0000-0000-C000-000000000046}"
IID_PERSIST_FILE = "{0000010B-0000-0000-C000-000000000046}"

# Номера методов в таблицах COM-интерфейсов (порядок из ShObjIdl.h / ObjIdl.h).
RELEASE = 2
QUERY_INTERFACE = 0
LINK_SET_DESCRIPTION = 7
LINK_SET_WORKING_DIRECTORY = 9
LINK_SET_ICON_LOCATION = 17
LINK_SET_PATH = 20
FILE_SAVE = 6


def _kernel():
    # Своя копия kernel32: прототипы, заданные здесь, не трогают ctypes.windll,
    # которым пользуются другие модули.
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k.CloseHandle.argtypes = (wintypes.HANDLE,)
    k.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD,
                                             wintypes.LPWSTR,
                                             ctypes.POINTER(wintypes.DWORD))
    return k


def _open(pid):
    """(kernel32, дескриптор процесса только на чтение сведений) или (None, None)."""
    if os.name != "nt":
        return None, None
    try:
        k = _kernel()
        handle = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    except (OSError, AttributeError, ValueError):
        return None, None
    return (k, handle) if handle else (None, None)


def command_line(pid):
    """Командная строка процесса или None (нет такого, нет прав, не Windows)."""
    k, handle = _open(pid)
    if not handle:
        return None
    try:
        nt = ctypes.WinDLL("ntdll")
        query = nt.NtQueryInformationProcess
        query.restype = ctypes.c_long
        query.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                          wintypes.ULONG, ctypes.POINTER(wintypes.ULONG))
        size = wintypes.ULONG(0)
        query(handle, COMMAND_LINE_INFORMATION, None, 0, ctypes.byref(size))
        if not size.value:
            return None
        buf = ctypes.create_string_buffer(size.value)
        if query(handle, COMMAND_LINE_INFORMATION, buf, size, ctypes.byref(size)) != 0:
            return None

        class UnicodeString(ctypes.Structure):
            _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT),
                        ("Buffer", ctypes.c_void_p)]

        text = UnicodeString.from_buffer(buf)
        return ctypes.wstring_at(text.Buffer, text.Length // 2) if text.Buffer else ""
    except (OSError, AttributeError, ValueError):
        return None
    finally:
        k.CloseHandle(handle)


def image_path(pid):
    """Полный путь к exe процесса или None."""
    k, handle = _open(pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if not k.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return None
        return buf.value
    except (OSError, AttributeError):
        return None
    finally:
        k.CloseHandle(handle)


def _method(obj, index, *argtypes):
    """Метод COM-объекта по номеру в его таблице: вызов — (obj, *args) -> HRESULT."""
    table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(table[index])


def make_shortcut(lnk, target, workdir="", icon="", description=""):
    """Ярлык .lnk на target через IShellLinkW — как это делает сама Windows.
    icon — путь к файлу со значком (берётся первый). True — ярлык записан."""
    if os.name != "nt":
        return False
    try:
        ole = ctypes.WinDLL("ole32")
    except OSError:
        return False

    def guid(text):
        out = (ctypes.c_byte * 16)()
        return out if ole.CLSIDFromString(ctypes.c_wchar_p(text), out) == 0 else None

    started = ole.CoInitialize(None) in (0, 1)        # S_OK, S_FALSE
    link, file = ctypes.c_void_p(), ctypes.c_void_p()
    try:
        clsid, iid_link, iid_file = (guid(CLSID_SHELL_LINK), guid(IID_SHELL_LINK_W),
                                     guid(IID_PERSIST_FILE))
        if not (clsid and iid_link and iid_file):
            return False
        if ole.CoCreateInstance(clsid, None, CLSCTX_INPROC_SERVER, iid_link,
                                ctypes.byref(link)) != 0 or not link:
            return False
        text = ctypes.c_wchar_p
        steps = [(LINK_SET_PATH, (text,), (target,))]
        if workdir:
            steps.append((LINK_SET_WORKING_DIRECTORY, (text,), (workdir,)))
        if icon:
            steps.append((LINK_SET_ICON_LOCATION, (text, ctypes.c_int), (icon, 0)))
        if description:
            steps.append((LINK_SET_DESCRIPTION, (text,), (description,)))
        for index, types, args in steps:
            if _method(link, index, *types)(link, *args) != 0:
                return False
        if _method(link, QUERY_INTERFACE, ctypes.c_void_p, ctypes.c_void_p)(
                link, iid_file, ctypes.byref(file)) != 0 or not file:
            return False
        if _method(file, FILE_SAVE, text, ctypes.c_int)(file, lnk, 1) != 0:
            return False
        return os.path.exists(lnk)
    except (OSError, AttributeError, ValueError):
        return False
    finally:
        for obj in (file, link):
            if obj:
                _method(obj, RELEASE)(obj)
        if started:
            ole.CoUninitialize()
