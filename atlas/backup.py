"""Database backup / restore and index integrity tools."""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from . import __version__
from .db import Database

log = logging.getLogger(__name__)

MANIFEST = "manifest.json"
DB_NAME = "atlas.db"
INDEX_NAME = "index.db"


class BackupError(Exception):
    pass


def _copy_db(src: sqlite3.Connection, schema: str, dest: Path) -> None:
    """Consistent online copy of one attached database using the SQLite backup API."""
    out = sqlite3.connect(dest)
    try:
        src.execute("PRAGMA wal_checkpoint(PASSIVE)")
        # sqlite3.Connection.backup supports attached schema via name=
        src.backup(out, name=schema)
    finally:
        out.close()


def create_backup(db: Database, backup_dir: Path, include_index: bool = False, keep: int = 10, settings_file: Path | None = None) -> Path:
    """Write ``atlas-backup-YYYYmmdd-HHMMSS.zip``. Contains metadata, tags, annotations, notes,
    saved searches, reading progress. The (rebuildable) search index is optional."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = backup_dir / f"atlas-backup-{stamp}.zip"
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        _copy_db(db.conn, "main", tdp / DB_NAME)
        if include_index:
            _copy_db(db.conn, "idx", tdp / INDEX_NAME)
        vcon = sqlite3.connect(tdp / DB_NAME)
        try:
            uv = vcon.execute("PRAGMA user_version").fetchone()[0]
        finally:
            vcon.close()
        manifest = {"app_version": __version__, "created": datetime.now().isoformat(timespec="seconds"),
                    "includes_index": include_index,
                    "main_user_version": uv}
        tmp = dest.with_suffix(".tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(MANIFEST, json.dumps(manifest, indent=2))
            z.write(tdp / DB_NAME, DB_NAME)
            if include_index:
                z.write(tdp / INDEX_NAME, INDEX_NAME)
            if settings_file and settings_file.exists():
                z.write(settings_file, "settings.json")
        os.replace(tmp, dest)
    _prune(backup_dir, keep)
    log.info("Backup written: %s", dest)
    return dest


def _prune(backup_dir: Path, keep: int) -> None:
    files = sorted(backup_dir.glob("atlas-backup-*.zip"))
    for f in files[:-keep] if keep > 0 else []:
        try:
            f.unlink()
        except OSError:
            pass


def list_backups(backup_dir: Path) -> list[Path]:
    return sorted(backup_dir.glob("atlas-backup-*.zip"), reverse=True)


def verify_backup(path: Path) -> dict:
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if MANIFEST not in names or DB_NAME not in names:
                raise BackupError("This file is not an Atlas Library backup.")
            manifest = json.loads(z.read(MANIFEST))
            with tempfile.TemporaryDirectory() as td:
                z.extract(DB_NAME, td)
                con = sqlite3.connect(Path(td) / DB_NAME)
                try:
                    res = con.execute("PRAGMA integrity_check").fetchone()[0]
                    if res != "ok":
                        raise BackupError(f"The backup database is damaged ({res}).")
                    ver = con.execute("PRAGMA user_version").fetchone()[0]
                finally:
                    con.close()
            from .migrations import MAIN_MIGRATIONS
            if ver > len(MAIN_MIGRATIONS):
                raise BackupError("This backup was made by a newer version of Atlas Library.")
            return manifest
    except zipfile.BadZipFile as exc:
        raise BackupError("The backup file is not a valid archive.") from exc


def restore_backup(path: Path, db_path: Path, index_path: Path) -> None:
    """Replace the live databases with the backup. The caller must close all connections first.

    The previous database is kept as ``*.before-restore`` so nothing is lost if the user restored the wrong file.
    If the backup has no index, the index is wiped and all books are re-queued for indexing.
    """
    manifest = verify_backup(path)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for f in (db_path, index_path):
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(f) + suffix)
            if p.exists():
                if suffix == "":
                    shutil.copy2(p, Path(str(f) + f".before-restore-{stamp}"))
                p.unlink()
    with zipfile.ZipFile(path) as z:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with z.open(DB_NAME) as src, open(db_path, "wb") as dst:
            shutil.copyfileobj(src, dst)
        if manifest.get("includes_index") and INDEX_NAME in z.namelist():
            with z.open(INDEX_NAME) as src, open(index_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
    if not manifest.get("includes_index"):
        con = sqlite3.connect(db_path)
        try:
            con.execute("UPDATE books SET index_status='pending', index_detail='Index must be rebuilt after restore' WHERE index_status IN ('indexed','partial','ocr_needed')")
            con.commit()
        finally:
            con.close()


# ------------------------------------------------------------------ integrity
def check_index(db: Database) -> dict:
    """Return findings; ``ok`` is True when nothing needs repair."""
    c = db.conn
    findings: dict = {"fts_ok": True, "fts_message": "", "orphan_segments": 0, "indexed_without_text": 0, "main_ok": True}
    try:
        c.execute("INSERT INTO segments_fts(segments_fts, rank) VALUES('integrity-check', 1)")
    except sqlite3.DatabaseError as exc:
        findings["fts_ok"] = False
        findings["fts_message"] = str(exc)
    findings["orphan_segments"] = c.execute(
        "SELECT COUNT(*) FROM segments WHERE book_id NOT IN (SELECT id FROM books)").fetchone()[0]
    meta_n = c.execute("SELECT COUNT(*) FROM seg_meta").fetchone()[0]
    seg_n = c.execute("SELECT COUNT(*) FROM segments").fetchone()[0]
    findings["meta_mismatch"] = abs(meta_n - seg_n)
    findings["indexed_without_text"] = c.execute(
        "SELECT COUNT(*) FROM books WHERE index_status IN ('indexed','partial') AND id NOT IN (SELECT DISTINCT book_id FROM segments)").fetchone()[0]
    findings["main_ok"] = c.execute("PRAGMA main.integrity_check").fetchone()[0] == "ok"
    findings["ok"] = findings["fts_ok"] and findings["main_ok"] and not findings["orphan_segments"] and not findings["indexed_without_text"] and not findings["meta_mismatch"]
    return findings


def repair_index(db: Database) -> dict:
    """Rebuild FTS from segments, drop orphans, and re-queue books whose text is missing."""
    with db.tx() as c:
        n_orphans = c.execute("DELETE FROM segments WHERE book_id NOT IN (SELECT id FROM books)").rowcount
        c.execute("INSERT INTO segments_fts(segments_fts) VALUES('rebuild')")
        c.execute("DELETE FROM seg_meta")
        c.execute("INSERT INTO seg_meta SELECT id, book_id, loc_type, loc_num, loc_label, char_start FROM segments")
        requeued = c.execute(
            "UPDATE books SET index_status='pending', index_detail='Re-queued by index repair' "
            "WHERE index_status IN ('indexed','partial') AND id NOT IN (SELECT DISTINCT book_id FROM segments)").rowcount
    return {"orphans_removed": n_orphans, "requeued": requeued}


def optimize(db: Database) -> None:
    db.conn.execute("INSERT INTO segments_fts(segments_fts) VALUES('optimize')")
    db.conn.execute("PRAGMA optimize")
