"""Storage abstraction for library roots (local, removable and - later - UNC/SMB).

Everything that touches a library root's file system goes through here so
that slow or missing sources never block the caller indefinitely.
"""
from __future__ import annotations

import ctypes
import os
import re
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
    return "network" if is_network_path(path) else "local"


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
        timeout = 10.0 if is_network_path(path) else 4.0
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
    if IS_LINUX:
        m = _mount_for(p)
        if m and m[2] in NETWORK_FS:      # the same share mounted twice is one library
            rel = os.path.relpath(os.path.realpath(p), m[0])
            return os.path.normcase(m[1].rstrip("/") + "/" + ("" if rel == "." else rel))
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


IS_WIN = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")
FILE_MANAGER = "Explorer" if IS_WIN else "file manager"
NETWORK_FS = {"cifs", "smb3", "smbfs", "nfs", "nfs4", "fuse.sshfs", "sshfs", "afs", "ncpfs", "9p", "fuse.rclone", "davfs", "fuse.davfs"}


# ------------------------------------------------------------- Linux mounts
def _unescape_mount(s: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), s)


def linux_mounts() -> list[tuple[str, str, str]]:
    """[(mountpoint, source, fstype)] from /proc/self/mountinfo (empty off Linux)."""
    try:
        with open("/proc/self/mountinfo", encoding="utf-8", errors="replace") as f:
            return parse_mountinfo(f.read())
    except OSError:
        return []


def parse_mountinfo(text: str) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        parts = line.split()
        if "-" not in parts[6:]:
            continue
        i = parts.index("-", 6)
        if len(parts) < i + 3:
            continue
        out.append((_unescape_mount(parts[4]), _unescape_mount(parts[i + 2]), parts[i + 1]))
    return out


def _mount_for(path: str) -> tuple[str, str, str] | None:
    """The mount holding ``path`` (longest matching mount point)."""
    try:
        real = os.path.realpath(path)
    except OSError:
        return None
    best = None
    for mp, src, fs in linux_mounts():
        if real == mp or real.startswith(mp.rstrip("/") + "/"):
            if best is None or len(mp) >= len(best[0]):
                best = (mp, src, fs)
    return best


def _linux_ids(device: str) -> tuple[str, str]:
    """(uuid, label) of a block device via /dev/disk/by-uuid and by-label."""
    uid = label = ""
    try:
        real = os.path.realpath(device)
        for kind in ("by-uuid", "by-label"):
            d = f"/dev/disk/{kind}"
            if not os.path.isdir(d):
                continue
            for name in os.listdir(d):
                if os.path.realpath(os.path.join(d, name)) == real:
                    if kind == "by-uuid":
                        uid = name
                    else:
                        label = _unescape_mount(name.replace("\\x20", " "))
    except OSError:
        pass
    return uid, label


def is_network_path(path: str) -> bool:
    if is_unc(path):
        return True
    if IS_WIN:
        drive = os.path.splitdrive(path)[0]
        return bool(drive) and bool(_wnet_connection(drive))
    if IS_LINUX:
        m = _mount_for(path)
        return bool(m and m[2] in NETWORK_FS)
    return False


def volume_info(path: str) -> tuple[str, str]:
    """(serial/UUID, label) of the volume holding ``path``; empty strings if unknown or a network path."""
    if is_unc(path):
        return "", ""
    if IS_WIN:
        drive = os.path.splitdrive(os.path.abspath(path))[0]
        return _volume_info_for_drive(drive) if drive else ("", "")
    if IS_LINUX:
        m = _mount_for(path)
        if m and m[1].startswith("/dev/"):
            return _linux_ids(m[1])
    return "", ""


def mount_relative(path: str) -> str | None:
    """Path of ``path`` relative to the root of its volume (used to find it again after a re-mount)."""
    if IS_WIN:
        rest = os.path.splitdrive(os.path.abspath(path))[1]
        return rest.lstrip("\\/")
    if IS_LINUX:
        m = _mount_for(path)
        if m:
            real = os.path.realpath(path)
            return os.path.relpath(real, m[0]).replace("\\", "/") if real != m[0] else ""
    return None


def volume_mountpoint(serial: str) -> str | None:
    """Current mount point of the volume with this UUID (Linux)."""
    if not serial or not IS_LINUX:
        return None
    best = None
    for mp, src, _fs in linux_mounts():
        if src.startswith("/dev/") and _linux_ids(src)[0] == serial:
            if best is None or len(mp) < len(best):   # prefer the shortest (non-bind) mount
                best = mp
    return best


def relocate_candidate(path: str, serial: str, mount_rel: str | None) -> str | None:
    """Where a root that vanished from ``path`` would be if its volume is mounted somewhere else now
    (new drive letter on Windows, new mount point on Linux)."""
    if not serial:
        return None
    if IS_WIN:
        drive = find_drive_by_serial(serial)
        return rebase_to_drive(path, drive) if drive else None
    mp = volume_mountpoint(serial)
    if mp is None or mount_rel is None:
        return None
    return os.path.join(mp, mount_rel) if mount_rel else mp


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
    """Return a drive letter ('E:') whose volume serial matches, if mounted (Windows)."""
    if not serial or not IS_WIN:
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
    """Show a file/folder in the platform file manager (Explorer, Finder, or the xdg default)."""
    import shutil
    import subprocess
    folder = path if os.path.isdir(path) else os.path.dirname(path)
    if IS_WIN:
        if select and os.path.isfile(path):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        else:
            subprocess.Popen(["explorer", os.path.normpath(folder)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path] if select and os.path.isfile(path) else ["open", folder])
    elif shutil.which("xdg-open"):
        subprocess.Popen(["xdg-open", folder], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
