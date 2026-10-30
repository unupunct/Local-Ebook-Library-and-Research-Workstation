"""Storage abstraction for library roots (local, removable and - later - UNC/SMB).

Everything that touches a library root's file system goes through here so
that slow or missing sources never block the caller indefinitely.
"""
from __future__ import annotations

import ctypes
import os
import string
import sys
import threading
from pathlib import Path
from typing import Callable, TypeVar

T = TypeVar("T")


def is_unc(path: str) -> bool:
    return path.startswith("\\\\") or path.startswith("//")


def normalize(path: str | Path) -> str:
    """Absolute, normalised path string (keeps UNC paths intact)."""
    p = str(path)
    if is_unc(p):
        return os.path.normpath(p)
    return os.path.normpath(os.path.abspath(p))


def path_key(path: str | Path) -> str:
    """Case-insensitive comparison key (Windows semantics)."""
    return os.path.normcase(normalize(path))


def kind_of(path: str) -> str:
    return "network" if is_unc(path) else "local"


def with_timeout(fn: Callable[[], T], timeout: float, default: T) -> T:
    """Run a possibly-blocking file system call without hanging the caller."""
    result: list[T] = []

    def run() -> None:
        try:
            result.append(fn())
        except OSError:
            result.append(default)

    t = threading.Thread(target=run, daemon=True, name="fs-probe")
    t.start()
    t.join(timeout)
    return result[0] if result else default


def is_dir_available(path: str, timeout: float | None = None) -> bool:
    """Directory check that never blocks longer than ``timeout`` (network shares get a longer default)."""
    if timeout is None:
        timeout = 10.0 if (is_unc(path) or _is_mapped_network(path)) else 4.0
    return with_timeout(lambda: os.path.isdir(path), timeout, False)


def _wnet_connection(drive: str) -> str:
    """UNC path behind a mapped network drive letter ('Z:'), or ''."""
    if sys.platform != "win32":
        return ""
    try:
        from ctypes import wintypes
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        mpr = ctypes.WinDLL("mpr")
        mpr.WNetGetConnectionW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        rc = mpr.WNetGetConnectionW(drive, buf, ctypes.byref(size))
        return buf.value if rc == 0 else ""
    except Exception:  # pragma: no cover - API unavailable
        return ""


def _is_mapped_network(path: str) -> bool:
    drive = os.path.splitdrive(path)[0]
    return bool(drive) and not is_unc(path) and bool(_wnet_connection(drive))


def canonical_key(path: str | Path) -> str:
    """Identity of a folder independent of how it is reached: mapped drives become their UNC path,
    junctions/symlinks are resolved, case and separators are normalised."""
    p = normalize(path)
    drive, rest = os.path.splitdrive(p)
    if drive and not is_unc(p):
        unc = _wnet_connection(drive)
        if unc:
            p = unc.rstrip("\\") + rest
    try:
        if os.path.exists(p):
            p = os.path.realpath(p)
    except OSError:
        pass
    return os.path.normcase(os.path.normpath(p))


def with_retry(fn: Callable[[], T], attempts: int = 3, delay: float = 0.5) -> T:
    """Retry a transient OSError (slow or flaky network share) with growing delays."""
    import time
    last: OSError | None = None
    for i in range(attempts):
        try:
            return fn()
        except OSError as exc:
            last = exc
            if i < attempts - 1:
                time.sleep(delay * (2 ** i))
    assert last is not None
    raise last


def volume_info(path: str) -> tuple[str, str]:
    """(serial, label) of the volume holding ``path``; empty strings if unknown."""
    if sys.platform != "win32" or is_unc(path):
        return "", ""
    drive = os.path.splitdrive(os.path.abspath(path))[0]
    if not drive:
        return "", ""
    return _volume_info_for_drive(drive)


def _volume_info_for_drive(drive: str) -> tuple[str, str]:
    try:
        label = ctypes.create_unicode_buffer(261)
        serial = ctypes.c_uint32(0)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(  # type: ignore[attr-defined]
            ctypes.c_wchar_p(drive + "\\"), label, 261, ctypes.byref(serial), None, None, None, 0)
        if not ok:
            return "", ""
        return f"{serial.value:08X}", label.value
    except Exception:  # pragma: no cover - non-windows / API failure
        return "", ""


def find_drive_by_serial(serial: str) -> str | None:
    """Return a drive letter ('E:') whose volume serial matches, if mounted."""
    if not serial or sys.platform != "win32":
        return None
    for letter in string.ascii_uppercase:
        drive = f"{letter}:"
        if not os.path.exists(drive + "\\"):
            continue
        s, _ = _volume_info_for_drive(drive)
        if s == serial:
            return drive
    return None


def rebase_to_drive(path: str, drive: str) -> str:
    """Replace the drive letter of ``path`` with ``drive``."""
    rest = os.path.splitdrive(path)[1]
    return drive + rest


def open_in_explorer(path: str, select: bool = True) -> None:
    import subprocess
    if sys.platform != "win32":
        return
    if select and os.path.isfile(path):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        subprocess.Popen(["explorer", os.path.normpath(path if os.path.isdir(path) else os.path.dirname(path))])
