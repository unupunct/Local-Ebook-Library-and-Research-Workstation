"""Scanning of library roots and the background indexing pipeline (Qt-free).

``Importer.run`` is meant to be executed in a worker thread; progress is
reported through a plain callback and cancellation through a threading.Event.
"""
from __future__ import annotations

import fnmatch
import hashlib
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

from . import storage
from .covers import save_thumbnail
from .formats import format_for, planned_extensions
from .library import Library
from .parsers import parse_file
from .parsers.base import ParseError
from .settings import Settings

log = logging.getLogger(__name__)

SEG_BATCH = 400
FP_SAMPLE = 64 * 1024


@dataclass
class Progress:
    phase: str = "idle"           # discovering | indexing | done | cancelled
    discovered: int = 0
    new: int = 0
    changed: int = 0
    moved: int = 0
    missing: int = 0
    processed: int = 0
    skipped: int = 0
    failed: int = 0
    remaining: int = 0
    current: str = ""
    note: str = ""
    started: float = field(default_factory=time.time)

    def copy(self) -> "Progress":
        return Progress(**self.__dict__)


ProgressCB = Callable[[Progress], None]


def fingerprint(path: str, size: int | None = None) -> str:
    """Quick content fingerprint: size + sampled head/middle/tail bytes (SHA-1)."""
    size = os.path.getsize(path) if size is None else size
    h = hashlib.sha1(str(size).encode())
    with open(path, "rb") as f:
        if size <= 3 * FP_SAMPLE:
            h.update(f.read())
        else:
            h.update(f.read(FP_SAMPLE))
            f.seek(size // 2 - FP_SAMPLE // 2)
            h.update(f.read(FP_SAMPLE))
            f.seek(size - FP_SAMPLE)
            h.update(f.read(FP_SAMPLE))
    return "q1:" + h.hexdigest()


def full_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_hidden(entry: os.DirEntry) -> bool:
    if entry.name.startswith("."):
        return True
    try:
        attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
        return bool(attrs & 0x2)  # FILE_ATTRIBUTE_HIDDEN
    except OSError:
        return False


def walk_files(root: str, recursive: bool, exclude_dirs: list[str], exclude_patterns: list[str],
               skip_hidden: bool, cancel: threading.Event | None = None, attempts: int = 1,
               on_error: Callable[[str, OSError], None] | None = None) -> Iterator[tuple[str, os.stat_result | None]]:
    """Yield (path, stat) for candidate files; never follows directory symlinks.

    ``attempts`` > 1 retries a failing directory listing with backoff (flaky network shares);
    unreadable directories are reported through ``on_error`` and skipped."""
    stack = [root]
    ex_dirs = [d.lower() for d in exclude_dirs]
    while stack:
        if cancel is not None and cancel.is_set():
            return
        d = stack.pop()
        def _list(d=d):
            with os.scandir(d) as it:
                return list(it)
        try:
            entries = storage.with_retry(_list, attempts) if attempts > 1 else _list()
        except OSError as exc:
            log.warning("Cannot read directory %s: %s", d, exc)
            if on_error:
                on_error(d, exc)
            continue
        entries.sort(key=lambda e: e.name.lower(), reverse=True)
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    if not recursive:
                        continue
                    if any(fnmatch.fnmatch(e.name.lower(), x) for x in ex_dirs):
                        continue
                    if skip_hidden and _is_hidden(e):
                        continue
                    stack.append(e.path)
                elif e.is_file():
                    if any(fnmatch.fnmatch(e.name.lower(), p.lower()) for p in exclude_patterns):
                        continue
                    if skip_hidden and _is_hidden(e):
                        continue
                    yield e.path, e.stat()
            except OSError as exc:
                log.warning("Cannot inspect %s: %s", e.path, exc)


def guess_title(filename: str) -> str:
    stem = os.path.splitext(filename)[0]
    return re.sub(r"[_]+", " ", stem).strip() or filename


class Importer:
    def __init__(self, lib: Library, settings: Settings, progress_cb: ProgressCB | None = None,
                 cancel: threading.Event | None = None) -> None:
        self.lib = lib
        self.db = lib.db
        self.settings = settings
        self.cb = progress_cb or (lambda p: None)
        self.cancel = cancel or threading.Event()
        self.p = Progress()
        self._last_emit = 0.0
        self._plock = threading.Lock()

    # ------------------------------------------------------------- helpers
    def _emit(self, force: bool = False) -> None:
        now = time.time()
        if force or now - self._last_emit > 0.15:
            self._last_emit = now
            with self._plock:
                snap = self.p.copy()
            try:
                self.cb(snap)
            except Exception:  # a broken UI callback must not kill the import
                log.exception("progress callback failed")

    # ------------------------------------------------------------ top level
    def run(self, root_ids: list[int] | None = None, force: bool = False, index: bool = True) -> Progress:
        self.p = Progress(phase="discovering")
        log_id = self._log_start()
        try:
            self.lib.check_roots()
            roots = [r for r in self.lib.roots() if r["enabled"] and (root_ids is None or r["id"] in root_ids)]
            for r in roots:
                if self.cancel.is_set():
                    break
                if not r["online"]:
                    self.p.note = f"Source '{r['label']}' is offline and was skipped."
                    continue
                self.scan_root(r, force)
            if index and not self.cancel.is_set():
                self.process_pending(root_ids)
        finally:
            self.p.phase = "cancelled" if self.cancel.is_set() else "done"
            self._emit(True)
            self._log_end(log_id)
        return self.p

    def _log_start(self) -> int:
        with self.db.tx() as c:
            return c.execute("INSERT INTO scan_log(started) VALUES(datetime('now'))").lastrowid

    def _log_end(self, log_id: int) -> None:
        with self.db.tx() as c:
            c.execute("UPDATE scan_log SET finished=datetime('now'), discovered=?, processed=?, skipped=?, failed=?, note=? WHERE id=?",
                      (self.p.discovered, self.p.processed, self.p.skipped, self.p.failed, self.p.note, log_id))

    # ---------------------------------------------------------------- scan
    def scan_root(self, root: sqlite3.Row, force: bool = False) -> None:
        s = self.settings
        c = self.db.conn
        rid, rpath = root["id"], root["path"]
        existing = {r["rel_path"]: r for r in c.execute(
            "SELECT id, rel_path, size, mtime, available, index_status, fingerprint FROM books WHERE root_id=?", (rid,))}
        seen: set[int] = set()
        planned = planned_extensions()
        pending_writes = 0
        completed = True
        errors: list[str] = []
        attempts = 3 if root["kind"] == "network" else 1
        self.db.conn.execute("BEGIN IMMEDIATE")
        try:
            for path, st in walk_files(rpath, bool(root["recursive"]), s.exclude_dirs, s.exclude_patterns, s.skip_hidden, self.cancel,
                                       attempts, lambda d, e: errors.append(d)):
                if self.cancel.is_set():
                    completed = False
                    break
                if len(errors) >= 5:
                    break
                info = format_for(path)
                if info is None:
                    continue
                rel = os.path.relpath(path, rpath)
                self.p.discovered += 1
                self.p.current = rel
                try:
                    size, mtime = st.st_size, st.st_mtime
                    ex = existing.get(rel)
                    if ex is not None:
                        seen.add(ex["id"])
                        if not force and ex["size"] == size and abs(ex["mtime"] - mtime) < 0.01:
                            if not ex["available"]:
                                c.execute("UPDATE books SET available=1 WHERE id=?", (ex["id"],))
                            self.p.skipped += 1
                        else:
                            fp = fingerprint(path, size)
                            if not force and fp == ex["fingerprint"] and ex["size"] == size:
                                c.execute("UPDATE books SET mtime=?, available=1 WHERE id=?", (mtime, ex["id"]))
                                self.p.skipped += 1
                            else:
                                c.execute("UPDATE books SET size=?, mtime=?, fingerprint=?, available=1, index_status='pending',"
                                          " index_detail='File changed' WHERE id=?", (size, mtime, fp, ex["id"]))
                                self.p.changed += 1
                    else:
                        fp = fingerprint(path, size)
                        moved = None
                        for cand in c.execute(
                                "SELECT b.id, r.path AS rpath, b.rel_path FROM books b JOIN library_roots r ON r.id=b.root_id "
                                "WHERE b.fingerprint=? AND b.size=? AND b.format=?", (fp, size, info.key)).fetchall():
                            if not os.path.exists(os.path.join(cand["rpath"], cand["rel_path"])):
                                moved = cand
                                break
                        if moved:
                            c.execute("UPDATE books SET root_id=?, rel_path=?, original_filename=?, mtime=?, available=1 WHERE id=?",
                                      (rid, rel, os.path.basename(path), mtime, moved["id"]))
                            seen.add(moved["id"])
                            self.p.moved += 1
                        else:
                            status, detail = ("pending", "")
                            if info.support != "supported":
                                status, detail = "unsupported", f"{info.label} files are not supported yet."
                            cur = c.execute(
                                "INSERT INTO books(uuid,root_id,rel_path,original_filename,format,size,mtime,fingerprint,title,index_status,index_detail)"
                                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                (uuid.uuid4().hex, rid, rel, os.path.basename(path), info.key, size, mtime, fp,
                                 guess_title(os.path.basename(path)), status, detail))
                            seen.add(cur.lastrowid)
                            self.p.new += 1
                except OSError as exc:
                    log.warning("Cannot read %s: %s", path, exc)
                    self.p.failed += 1
                pending_writes += 1
                if pending_writes >= 200:
                    c.execute("COMMIT"); c.execute("BEGIN IMMEDIATE"); pending_writes = 0
                self._emit()
            if errors and (rpath in errors or len(errors) >= 5 or not storage.is_dir_available(rpath)):
                completed = False
                self.p.note = (f"Source '{root['label']}' became unreachable or unreadable during the scan "
                               f"({len(errors)} folders failed). Nothing was marked missing; rescan when it is back.")
                log.warning(self.p.note)
            elif errors:
                self.p.note = f"{len(errors)} folder(s) could not be read and were skipped (see log)."
            if completed and not self.cancel.is_set():
                gone_av = [v["id"] for v in existing.values() if v["id"] not in seen and v["available"]]
                for i in range(0, len(gone_av), 500):
                    ch = gone_av[i:i + 500]
                    c.execute(f"UPDATE books SET available=0 WHERE id IN ({','.join('?' * len(ch))})", ch)
                self.p.missing += len(gone_av)
                c.execute("UPDATE library_roots SET last_scan=datetime('now') WHERE id=?", (rid,))
            c.execute("COMMIT")
        except BaseException:
            if c.in_transaction:
                c.execute("ROLLBACK")
            raise
        self._emit(True)

    # --------------------------------------------------------------- index
    def process_pending(self, root_ids: list[int] | None = None, book_ids: list[int] | None = None) -> None:
        self.p.phase = "indexing"
        c = self.db.conn
        q = ("SELECT b.id FROM books b JOIN library_roots r ON r.id=b.root_id WHERE b.index_status='pending' "
             "AND b.available=1 AND r.enabled=1 AND r.online=1")
        params: list = []
        if root_ids:
            q += f" AND b.root_id IN ({','.join('?' * len(root_ids))})"
            params += list(root_ids)
        if book_ids:
            q += f" AND b.id IN ({','.join('?' * len(book_ids))})"
            params += list(book_ids)
        ids = [r[0] for r in c.execute(q + " ORDER BY b.id", params)]
        self.p.remaining = len(ids)
        self._emit(True)
        if not ids:
            return
        threads = max(1, int(self.settings.index_threads))
        if threads == 1:
            for bid in ids:
                if self.cancel.is_set():
                    break
                self._do_one(bid)
        else:
            with ThreadPoolExecutor(max_workers=threads, thread_name_prefix="index") as ex:
                futs = []
                for bid in ids:
                    futs.append(ex.submit(self._guarded, bid))
                for f in futs:
                    f.result()

    def _guarded(self, bid: int) -> None:
        if self.cancel.is_set():
            return
        self._do_one(bid)

    def _do_one(self, book_id: int) -> None:
        try:
            self.index_book(book_id)
        except Exception:
            log.exception("Unexpected failure indexing book %s", book_id)
            self._mark(book_id, "failed", "Unexpected error while indexing (see log).")
            self._count_failure("failed")
        with self._plock:
            self.p.processed += 1
            self.p.remaining = max(0, self.p.remaining - 1)
        self._emit()

    def _mark(self, book_id: int, status: str, detail: str) -> None:
        with self.db.tx() as c:
            c.execute("UPDATE books SET index_status=?, index_detail=?, last_indexed=datetime('now') WHERE id=?", (status, detail, book_id))

    def index_book(self, book_id: int) -> str:
        """Extract metadata + text for one book and replace its index records."""
        book = self.lib.get_book(book_id)
        if book is None:
            return "missing"
        path = Path(book["path"])
        with self._plock:
            self.p.current = book["original_filename"]
        if not path.is_file():
            with self.db.tx() as c:
                c.execute("UPDATE books SET available=0 WHERE id=?", (book_id,))
            return "offline"
        try:
            doc = parse_file(path, book["format"], self.settings.ocr_min_chars)
        except ParseError as exc:
            self._mark(book_id, exc.status, str(exc))
            self._count_failure(exc.status)
            return exc.status
        except OSError as exc:
            self._mark(book_id, "failed", f"Could not read the file: {exc}")
            self._count_failure("failed")
            return "failed"
        except Exception as exc:
            log.exception("Parser crashed on book %s", book_id)
            self._mark(book_id, "failed", f"The file could not be processed: {type(exc).__name__}")
            self._count_failure("failed")
            return "failed"

        # replace previous text (OCR text is kept separately and is not touched)
        with self.db.tx() as c:
            c.execute("DELETE FROM segments WHERE book_id=? AND source='text'", (book_id,))
        n = 0
        batch: list[tuple] = []
        try:
            for seg in doc.segments:
                batch.append((book_id, n, "text", seg.loc_type, seg.loc_num, seg.loc_label, seg.char_start, seg.text))
                n += 1
                if len(batch) >= SEG_BATCH:
                    self._write_segments(batch)
                    batch = []
                if self.cancel.is_set():
                    # leave book pending so it is processed again after restart
                    with self.db.tx() as c:
                        c.execute("DELETE FROM segments WHERE book_id=? AND source='text'", (book_id,))
                    return "cancelled"
            if batch:
                self._write_segments(batch)
        except ParseError as exc:
            self._mark(book_id, exc.status, str(exc))
            self._count_failure(exc.status)
            return exc.status
        except Exception as exc:
            log.exception("Text extraction failed for book %s", book_id)
            with self.db.tx() as c:
                c.execute("DELETE FROM segments WHERE book_id=? AND source='text'", (book_id,))
            self._mark(book_id, "failed", f"Text extraction failed: {type(exc).__name__}")
            self._count_failure("failed")
            return "failed"

        status, detail = self._status_for(book["format"], doc, n)
        self._apply_metadata(book, doc)
        if doc.cover and not book["cover_path"] and self.lib.covers_dir is not None:
            dest = self.lib.covers_dir / f"{book['uuid']}.jpg"
            if save_thumbnail(doc.cover, dest):
                with self.db.tx() as c:
                    c.execute("UPDATE books SET cover_path=? WHERE id=?", (str(dest), book_id))
        import json
        with self.db.tx() as c:
            c.execute("UPDATE books SET index_status=?, index_detail=?, last_indexed=datetime('now'), low_text_pages=?, toc_json=?,"
                      " page_count=COALESCE(?, page_count) WHERE id=?",
                      (status, detail, doc.low_text_pages, json.dumps([[t.title, t.loc_num, t.level] for t in doc.toc]),
                       doc.metadata.page_count, book_id))
        if status in ("failed", "ocr_needed"):
            self._count_failure(status)
        return status

    def _count_failure(self, status: str) -> None:
        with self._plock:
            self.p.failed += 1

    @staticmethod
    def _status_for(fmt: str, doc, n_segments: int) -> tuple[str, str]:
        if n_segments == 0:
            if fmt == "pdf" and doc.metadata.page_count:
                return "ocr_needed", "No extractable text: this looks like a scanned PDF. Run OCR to make it searchable."
            return "failed", "No text could be extracted from this file."
        if fmt == "pdf" and doc.low_text_pages:
            total = doc.metadata.page_count or 0
            if total and doc.low_text_pages >= total * 0.9:
                return "partial", f"{doc.low_text_pages} of {total} pages have little or no text (scanned?). OCR can add them."
            return "partial", f"{doc.low_text_pages} page(s) have little or no extractable text."
        if doc.warnings:
            return "partial", "; ".join(doc.warnings[:3])
        return "indexed", ""

    def _write_segments(self, batch: list[tuple]) -> None:
        with self.db.tx() as c:
            c.executemany("INSERT INTO segments(book_id,seq,source,loc_type,loc_num,loc_label,char_start,text) VALUES(?,?,?,?,?,?,?,?)", batch)

    def _apply_metadata(self, book: dict, doc) -> None:
        m = doc.metadata
        if book["metadata_edited"]:
            return
        fields: dict = {}
        if m.title and len(m.title.strip()) > 1:
            fields["title"] = m.title.strip()
        for k in ("publisher", "isbn", "language", "series", "description"):
            v = getattr(m, k)
            if v:
                fields[k] = v
        if m.year:
            fields["year"] = m.year
        if m.series_index is not None:
            fields["series_index"] = m.series_index
        with self.db.tx() as c:
            if fields:
                c.execute(f"UPDATE books SET {','.join(k + '=?' for k in fields)} WHERE id=?", list(fields.values()) + [book["id"]])
            if m.authors:
                self.lib.set_authors(book["id"], m.authors)
            if m.subjects:
                self.lib.set_terms(book["id"], "subject", m.subjects[:30])

    # ---------------------------------------------------------- convenience
    def reindex_books(self, book_ids: list[int]) -> Progress:
        with self.db.tx() as c:
            c.executemany("UPDATE books SET index_status='pending', index_detail='' WHERE id=?", [(i,) for i in book_ids])
        self.p = Progress(phase="indexing")
        self.process_pending(book_ids=list(book_ids))
        self.p.phase = "done"
        self._emit(True)
        return self.p

    def reindex_all(self) -> Progress:
        with self.db.tx() as c:
            c.execute("UPDATE books SET index_status='pending', index_detail='' WHERE index_status<>'unsupported'")
        self.p = Progress(phase="indexing")
        self.process_pending()
        self.p.phase = "done"
        self._emit(True)
        return self.p

    def retry_failed(self) -> Progress:
        with self.db.tx() as c:
            c.execute("UPDATE books SET index_status='pending', index_detail='' WHERE index_status IN ('failed','encrypted')")
        self.p = Progress(phase="indexing")
        self.process_pending()
        self.p.phase = "done"
        self._emit(True)
        return self.p

