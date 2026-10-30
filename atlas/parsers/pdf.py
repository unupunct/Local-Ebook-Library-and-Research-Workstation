"""PDF adapter (PyMuPDF): page-level text, TOC, cover, encryption detection."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator

import pymupdf

from .base import (FITZ_LOCK, EncryptedError, Metadata, ParsedDocument, ParseError,
                   Segment, TocEntry, chunk_text, extract_year, normalize_text, clean_isbn)

log = logging.getLogger(__name__)
MIN_CHARS_DEFAULT = 25


def _split_authors(value: str) -> list[str]:
    import re
    parts = re.split(r"\s*(?:;|&|\band\b|\n)\s*", value or "")
    return [p.strip() for p in parts if p.strip()]


def open_pdf(path: Path):
    try:
        with FITZ_LOCK:
            doc = pymupdf.open(str(path))
    except Exception as exc:
        raise ParseError(f"Cannot open PDF: {exc}") from exc
    if doc.needs_pass:
        doc.close()
        raise EncryptedError()
    return doc


class PdfParser:
    def __init__(self, min_chars: int = MIN_CHARS_DEFAULT) -> None:
        self.min_chars = min_chars

    def parse(self, path: Path) -> ParsedDocument:
        doc = open_pdf(path)
        try:
            with FITZ_LOCK:
                md = doc.metadata or {}
                n = doc.page_count
                meta = Metadata(
                    title=(md.get("title") or "").strip(),
                    authors=_split_authors(md.get("author") or ""),
                    publisher=(md.get("producer") or "").strip() if False else "",
                    year=extract_year(md.get("creationDate", "") or ""),
                    description=(md.get("subject") or "").strip(),
                    subjects=[k.strip() for k in (md.get("keywords") or "").replace(";", ",").split(",") if k.strip()][:20],
                    page_count=n,
                )
                # embedded creation dates are tool dates, not publication dates
                meta.year = None
                toc = [TocEntry(t.strip(), max(1, p), lvl) for lvl, t, p in doc.get_toc(simple=True) if t.strip()]
                cover = self._cover(doc)
        except ParseError:
            doc.close()
            raise
        except Exception as exc:
            doc.close()
            raise ParseError(f"Damaged PDF: {exc}") from exc
        pd = ParsedDocument(metadata=meta, segments=iter(()), toc=toc, cover=cover)
        pd.segments = self._pages(doc, n, pd)
        return pd

    def _cover(self, doc) -> bytes | None:
        try:
            if doc.page_count == 0:
                return None
            pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(0.6, 0.6), alpha=False)
            return pix.tobytes("png")
        except Exception:
            return None

    def _pages(self, doc, n: int, pd: ParsedDocument) -> Iterator[Segment]:
        low = 0
        try:
            for i in range(n):
                try:
                    with FITZ_LOCK:
                        text = doc[i].get_text("text")
                except Exception as exc:
                    pd.warnings.append(f"page {i + 1}: {exc}")
                    low += 1
                    continue
                text = normalize_text(text)
                if len(text) < self.min_chars:
                    low += 1
                    pd.low_text_pages = low
                    if not text:
                        continue
                pd.low_text_pages = low
                for off, piece in chunk_text(text):
                    yield Segment("page", i + 1, piece, f"Page {i + 1}", off)
        finally:
            pd.low_text_pages = low
            with FITZ_LOCK:
                doc.close()
