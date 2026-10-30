"""Registry of file formats and how well each is supported."""
from __future__ import annotations

from dataclasses import dataclass

FULL = "supported"
PLANNED = "planned"


@dataclass(frozen=True)
class FormatInfo:
    key: str
    label: str
    extensions: tuple[str, ...]
    support: str          # supported | planned
    notes: str = ""


FORMATS: list[FormatInfo] = [
    FormatInfo("pdf", "PDF", (".pdf",), FULL, "Page-level text, page rendering, optional OCR"),
    FormatInfo("epub", "EPUB", (".epub",), FULL, "Chapter-level text, covers, TOC (DRM-protected files are reported, not opened)"),
    FormatInfo("txt", "Plain text", (".txt", ".text"), FULL),
    FormatInfo("md", "Markdown", (".md", ".markdown"), FULL),
    FormatInfo("html", "HTML", (".html", ".htm", ".xhtml"), FULL),
    FormatInfo("docx", "Word (DOCX)", (".docx",), FULL, "Text and headings; legacy .doc is not supported"),
    FormatInfo("rtf", "RTF", (".rtf",), FULL, "Plain text via striprtf; formatting is dropped"),
    FormatInfo("mobi", "MOBI / AZW / AZW3", (".mobi", ".azw", ".azw3", ".prc"), FULL,
               "Unencrypted MOBI6/PalmDOC fully; AZW3/KF8 best-effort (text without chapters); DRM files are reported"),
    FormatInfo("djvu", "DjVu", (".djvu", ".djv"), PLANNED, "Needs an external DjVu library"),
]

_BY_EXT: dict[str, FormatInfo] = {e: f for f in FORMATS for e in f.extensions}


def format_for(filename: str) -> FormatInfo | None:
    dot = filename.rfind(".")
    if dot < 0:
        return None
    return _BY_EXT.get(filename[dot:].lower())


def supported_extensions() -> set[str]:
    return {e for f in FORMATS if f.support == FULL for e in f.extensions}


def planned_extensions() -> set[str]:
    return {e for f in FORMATS if f.support == PLANNED for e in f.extensions}
