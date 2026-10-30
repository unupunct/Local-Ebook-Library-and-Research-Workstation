"""Optional OCR for scanned PDFs via an installed Tesseract. Never modifies the PDF.

OCR text is stored as separate segments (source='ocr') and cached in the OCR
directory, so normal re-indexing never touches it.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Callable

import pymupdf

from .library import Library
from .parsers.base import FITZ_LOCK, chunk_text, normalize_text
from .parsers.pdf import open_pdf

log = logging.getLogger(__name__)

COMMON_PATHS = [r"C:\Program Files\Tesseract-OCR\tesseract.exe", r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"]


class OcrUnavailable(Exception):
    pass


def find_tesseract(configured: str = "") -> str | None:
    if configured and os.path.isfile(configured):
        return configured
    found = shutil.which("tesseract")
    if found:
        return found
    return next((p for p in COMMON_PATHS if os.path.isfile(p)), None)


def available_languages(tesseract: str) -> list[str]:
    try:
        out = subprocess.run([tesseract, "--list-langs"], capture_output=True, text=True, timeout=20)
        return [ln.strip() for ln in out.stdout.splitlines()[1:] if ln.strip()]
    except (OSError, subprocess.SubprocessError):
        return []


def ocr_book(lib: Library, book_id: int, language: str = "eng", tesseract_path: str = "", ocr_dir: Path | None = None,
             min_chars: int = 25, progress: Callable[[int, int], None] | None = None,
             cancel: threading.Event | None = None) -> dict:
    tess = find_tesseract(tesseract_path)
    if not tess:
        raise OcrUnavailable("Tesseract OCR is not installed. Install it (e.g. from the UB Mannheim Windows build) "
                             "and set its path in Settings > OCR.")
    if language not in available_languages(tess):
        raise OcrUnavailable(f"The OCR language '{language}' is not installed for Tesseract.")
    book = lib.get_book(book_id)
    if book is None or book["format"] != "pdf":
        raise OcrUnavailable("OCR is only available for PDF files.")
    if not os.path.isfile(book["path"]):
        raise OcrUnavailable("The source file is offline, so OCR cannot run.")
    doc = open_pdf(Path(book["path"]))
    db = lib.db
    done = failed = skipped = 0
    try:
        n = doc.page_count
        have = {r[0]: r[1] for r in db.conn.execute(
            "SELECT loc_num, SUM(LENGTH(text)) FROM segments WHERE book_id=? AND source='text' AND loc_type='page' GROUP BY loc_num", (book_id,))}
        with db.tx() as c:
            c.execute("DELETE FROM segments WHERE book_id=? AND source='ocr'", (book_id,))
        out_dir = (ocr_dir or Path(tempfile.gettempdir())) / book["uuid"]
        out_dir.mkdir(parents=True, exist_ok=True)
        seq = 0
        for i in range(n):
            if cancel is not None and cancel.is_set():
                break
            if progress:
                progress(i + 1, n)
            if have.get(i + 1, 0) >= min_chars:
                skipped += 1
                continue
            with tempfile.TemporaryDirectory() as td:
                img = Path(td) / "p.png"
                with FITZ_LOCK:
                    doc[i].get_pixmap(matrix=pymupdf.Matrix(300 / 72, 300 / 72), alpha=False).save(str(img))
                try:
                    r = subprocess.run([tess, str(img), "stdout", "-l", language], capture_output=True, timeout=180,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                except (OSError, subprocess.SubprocessError) as exc:
                    log.warning("OCR failed on page %d: %s", i + 1, exc)
                    failed += 1
                    continue
            if r.returncode != 0:
                log.warning("Tesseract returned %s on page %d", r.returncode, i + 1)
                failed += 1
                continue
            text = normalize_text(r.stdout.decode("utf-8", errors="replace"))
            (out_dir / f"page-{i + 1}.txt").write_text(text, encoding="utf-8")
            rows = []
            for off, piece in chunk_text(text):
                rows.append((book_id, 10_000_000 + seq, "ocr", "page", i + 1, f"Page {i + 1}", off, piece))
                seq += 1
            if rows:
                with db.tx() as c:
                    c.executemany("INSERT INTO segments(book_id,seq,source,loc_type,loc_num,loc_label,char_start,text) VALUES(?,?,?,?,?,?,?,?)", rows)
            done += 1
    finally:
        with FITZ_LOCK:
            doc.close()
    if done:
        with db.tx() as c:
            c.execute("UPDATE books SET index_status='partial', index_detail=? WHERE id=?",
                      (f"OCR added text for {done} page(s)" + (f"; {failed} page(s) failed" if failed else ""), book_id))
    return {"pages_ocr": done, "pages_failed": failed, "pages_skipped": skipped}
