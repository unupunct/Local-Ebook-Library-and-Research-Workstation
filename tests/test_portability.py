"""Cross-platform behaviour: these tests run on Windows and Linux (the Linux logic is exercised with simulated mounts)."""
import ast
import io
import os
import sys
import tokenize
from pathlib import Path

import pytest

from atlas import paths, storage
from atlas.library import BookFilter, native_rel

SRC = Path(__file__).resolve().parent.parent / "atlas"

MOUNTINFO = r"""22 28 0:21 / /sys rw,nosuid shared:7 - sysfs sysfs rw
36 28 8:2 / / rw,relatime shared:1 - ext4 /dev/sda2 rw
40 36 8:17 / /media/user/My\040Books rw,nosuid shared:9 - exfat /dev/sdb1 rw
41 36 0:50 / /mnt/nas rw,relatime shared:10 - cifs //nas/share rw
42 36 0:51 /sub /mnt/nas2 rw,relatime shared:11 - nfs4 nas:/export rw
"""


def test_mountinfo_parsing_handles_escapes_and_fstypes():
    m = storage.parse_mountinfo(MOUNTINFO)
    assert ("/media/user/My Books", "/dev/sdb1", "exfat") in m
    assert ("/mnt/nas", "//nas/share", "cifs") in m and ("/mnt/nas2", "nas:/export", "nfs4") in m
    assert storage.parse_mountinfo("garbage\n\n- -") == []


def test_linux_volume_detection_and_remount(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "IS_WIN", False)
    monkeypatch.setattr(storage, "IS_LINUX", True)
    mounts = storage.parse_mountinfo(MOUNTINFO)
    monkeypatch.setattr(storage, "linux_mounts", lambda: mounts)
    monkeypatch.setattr(storage.os.path, "realpath", lambda p: str(p).replace("\\", "/"))
    monkeypatch.setattr(storage, "_linux_ids", lambda dev: ("1234-ABCD", "My Books") if dev == "/dev/sdb1" else ("ROOT-UUID", ""))
    assert storage.volume_info("/media/user/My Books/Library") == ("1234-ABCD", "My Books")
    assert storage.mount_relative("/media/user/My Books/Library/Fiction") == "Library/Fiction"
    assert storage.mount_relative("/media/user/My Books") == ""
    assert storage.is_network_path("/mnt/nas/books") and storage.kind_of("/mnt/nas/books") == "network"
    assert not storage.is_network_path("/media/user/My Books/x")
    # the same NAS share mounted at two places has one canonical identity
    assert storage.canonical_key("/mnt/nas/books") != storage.canonical_key("/media/user/My Books/books")
    # drive re-mounted elsewhere: the root is found again through the volume UUID
    new = [("/run/media/user/Books2", "/dev/sdb1", "exfat")] + [m for m in mounts if m[1] != "/dev/sdb1"]
    monkeypatch.setattr(storage, "linux_mounts", lambda: new)
    assert storage.volume_mountpoint("1234-ABCD") == "/run/media/user/Books2"
    cand = storage.relocate_candidate("/media/user/My Books/Library", "1234-ABCD", "Library")
    assert cand == os.path.join("/run/media/user/Books2", "Library")
    assert storage.relocate_candidate("/x", "1234-ABCD", None) is None            # unknown relative path: do not guess


def test_root_follows_a_remounted_volume(env, monkeypatch, tmp_path):
    """check_roots rebases a vanished root onto the new mount point (platform-independent logic)."""
    d = tmp_path / "old"
    d.mkdir()
    rid = env.lib.add_root(d)
    newmount = tmp_path / "newmount"
    (newmount / "Books").mkdir(parents=True)
    env.db.conn.execute("UPDATE library_roots SET path=?, volume_serial='UUID-1', mount_rel='Books', online=1 WHERE id=?",
                        (str(tmp_path / "gone" / "Books"), rid))
    monkeypatch.setattr(storage, "IS_WIN", False)
    monkeypatch.setattr(storage, "volume_mountpoint", lambda serial: str(newmount) if serial == "UUID-1" else None)
    ev = env.lib.check_roots()
    assert ev and ev[0]["event"] == "drive_letter_changed"
    assert env.lib.root(rid)["path"] == str(newmount / "Books") and env.lib.root(rid)["online"] == 1


def test_data_dir_follows_platform_conventions(monkeypatch, tmp_path):
    monkeypatch.delenv("ATLAS_DATA_DIR", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert paths.default_data_dir() == tmp_path / "xdg" / "AtlasLibrary"
    monkeypatch.delenv("XDG_DATA_HOME")
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    assert paths.default_data_dir() == tmp_path / "home" / ".local" / "share" / "AtlasLibrary"
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "lad"))
    assert paths.default_data_dir() == tmp_path / "lad" / "AtlasLibrary"
    monkeypatch.setenv("ATLAS_DATA_DIR", str(tmp_path / "custom"))
    assert paths.default_data_dir() == tmp_path / "custom"


def test_relative_paths_from_windows_databases_work_on_posix(monkeypatch):
    monkeypatch.setattr(os, "sep", "/")
    assert native_rel("Gardening\\Permaculture\\a.pdf") == "Gardening/Permaculture/a.pdf"
    monkeypatch.setattr(os, "sep", "\\")
    assert native_rel("Gardening\\a.pdf") == "Gardening\\a.pdf"


def test_folder_filter_accepts_either_separator(indexed):
    n = indexed.lib.count_books(BookFilter(folder="Gardening/Permaculture"))
    assert n == indexed.lib.count_books(BookFilter(folder="Gardening\\Permaculture")) == 2


def test_clipping_source_path_uses_native_separators(indexed):
    b = indexed.book("Gardening/Permaculture/food_forest.pdf")
    cid = indexed.research_clip = None
    from atlas.research import Research
    c = Research(indexed.db).create_clipping("text", b["id"], {"type": "page", "num": 1})
    path = Research(indexed.db).get_clipping(c)["source_path"]
    assert os.path.normpath(path) == os.path.normpath(b["path"])


# ----------------------------------------------------------- source compatibility
def test_sources_parse_for_python_310():
    for f in list(SRC.rglob("*.py")) + [SRC.parent / "run_atlas.py"]:
        ast.parse(f.read_text(encoding="utf-8"), filename=str(f), feature_version=(3, 10))


@pytest.mark.skipif(sys.version_info < (3, 12), reason="needs the 3.12 f-string tokenizer to inspect expressions")
def test_no_python312_only_fstring_syntax():
    """Backslashes or re-used quotes inside f-string expressions are legal on 3.12+ only; Debian 12 / Parrot 6 ship 3.11."""
    bad = []
    for f in SRC.rglob("*.py"):
        stack = []          # per open f-string: [quote, brace depth]
        for tok in tokenize.generate_tokens(io.StringIO(f.read_text(encoding="utf-8")).readline):
            name = tokenize.tok_name[tok.type]
            if name == "FSTRING_START":
                if stack and stack[-1][1] > 0 and tok.string[-1] == stack[-1][0]:
                    bad.append((f.name, tok.start[0], "re-used quote"))
                stack.append([tok.string[-1], 0])
            elif name == "FSTRING_END":
                stack.pop()
            elif stack:
                if name == "OP" and tok.string == "{":
                    stack[-1][1] += 1
                elif name == "OP" and tok.string == "}":
                    stack[-1][1] -= 1
                elif stack[-1][1] > 0 and name == "STRING":
                    if "\\" in tok.string:
                        bad.append((f.name, tok.start[0], "backslash in expression"))
                    if tok.string[-1] == stack[-1][0]:
                        bad.append((f.name, tok.start[0], "re-used quote"))
    assert not bad, bad
