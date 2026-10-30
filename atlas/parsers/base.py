"""Parser contract shared by all format adapters."""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Protocol

# Global lock for MuPDF: it is not thread safe.
FITZ_LOCK = threading.RLock()

CHUNK_TARGET = 3000
MAX_SEGMENT = 5000


class ParseError(Exception):
    """Unreadable / corrupt file. ``status`` becomes the book's index_status."""

    def __init__(self, message: str, status: str = "failed") -> None:
        super().__init__(message)
        self.status = status


class EncryptedError(ParseError):
    def __init__(self, message: str = "The file is encrypted or password protected.") -> None:
        super().__init__(message, "encrypted")


@dataclass
class Segment:
    loc_type: str          # page | chapter | section | text
    loc_num: int           # page (1-based) / chapter index (0-based) / chunk number
    text: str
    loc_label: str = ""
    char_start: int = 0


@dataclass
class TocEntry:
    title: str
    loc_num: int           # page (1-based) or chapter index (0-based)
    level: int = 1


@dataclass
class Metadata:
    title: str = ""
    authors: list[str] = field(default_factory=list)
    publisher: str = ""
    year: int | None = None
    isbn: str = ""
    language: str = ""
    series: str = ""
    series_index: float | None = None
    subjects: list[str] = field(default_factory=list)
    description: str = ""
    page_count: int | None = None


@dataclass
class ParsedDocument:
    metadata: Metadata
    segments: Iterator[Segment]
    toc: list[TocEntry] = field(default_factory=list)
    cover: bytes | None = None
    low_text_pages: int = 0
    warnings: list[str] = field(default_factory=list)
    # PDF only: filled in as segments are consumed
    stats: dict = field(default_factory=dict)


class Parser(Protocol):
    def parse(self, path: Path) -> ParsedDocument: ...


_WS = re.compile(r"[ \t ​]+")
_NL = re.compile(r"\n{3,}")


def normalize_text(text: str) -> str:
    """Collapse runs of whitespace but keep paragraph breaks."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = _WS.sub(" ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    return _NL.sub("\n\n", text).strip()


def chunk_text(text: str, target: int = CHUNK_TARGET) -> Iterator[tuple[int, str]]:
    """Split text at paragraph/sentence boundaries into (char_start, chunk)."""
    n = len(text)
    pos = 0
    while pos < n:
        end = min(pos + target, n)
        if end < n:
            cut = text.rfind("\n", pos + target // 2, end)
            if cut < 0:
                cut = max(text.rfind(". ", pos + target // 2, end), text.rfind(" ", pos + target // 2, end))
            if cut > pos:
                end = cut + 1
        piece = text[pos:end]
        if piece.strip():
            yield pos, piece.strip()
        pos = end


def extract_year(value: str) -> int | None:
    m = re.search(r"(1[5-9]\d\d|20\d\d)", value or "")
    return int(m.group(1)) if m else None


def clean_isbn(value: str) -> str:
    digits = re.sub(r"[^0-9Xx]", "", value or "")
    return digits.upper() if len(digits) in (10, 13) else ""
