"""Format adapter registry."""
from __future__ import annotations

from pathlib import Path

from ..formats import format_for
from .base import ParsedDocument, ParseError
from .epub import EpubParser
from .mobi import MobiParser
from .pdf import PdfParser
from .textdocs import DocxParser, HtmlParser, MarkdownParser, RtfParser, TxtParser

_PARSERS = {
    "pdf": PdfParser, "epub": EpubParser, "txt": TxtParser, "md": MarkdownParser,
    "html": HtmlParser, "docx": DocxParser, "rtf": RtfParser, "mobi": MobiParser,
}


def parser_for(fmt: str, **kwargs):
    cls = _PARSERS.get(fmt)
    if cls is None:
        raise ParseError(f"Format '{fmt}' is not supported yet.", "unsupported")
    return cls(**kwargs) if fmt == "pdf" else cls()


def parse_file(path: Path, fmt: str | None = None, min_chars: int = 25) -> ParsedDocument:
    if fmt is None:
        info = format_for(path.name)
        if info is None:
            raise ParseError("Unsupported file type.", "unsupported")
        fmt = info.key
    return parser_for(fmt, **({"min_chars": min_chars} if fmt == "pdf" else {})).parse(path)
