"""MOBI / PRC / AZW reader written from the public format description (no GPL code involved).

Supported: unencrypted PalmDOC-compressed MOBI (version 6) files - text, chapters (page breaks), metadata (EXTH),
cover and inline images. KF8/AZW3 files (MOBI version 8) are read best-effort: the text is extracted but chapter
structure is not reconstructed, so such books are reported as "partly indexed".
Not supported: DRM-protected files (reported as encrypted) and Huffman/CDIC compression.
"""
from __future__ import annotations

import re
import struct
from pathlib import Path
from typing import Iterator

from bs4 import BeautifulSoup

from .base import (EncryptedError, Metadata, ParsedDocument, ParseError, Segment, TocEntry, chunk_text,
                   clean_isbn, extract_year, normalize_text)
from .epub import html_to_text

MAX_FILE = 300 * 1024 * 1024
MAX_TEXT = 200 * 1024 * 1024


def palmdoc_decompress(data: bytes) -> bytes:
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        c = data[i]
        i += 1
        if c == 0 or 9 <= c <= 0x7F:
            out.append(c)
        elif 1 <= c <= 8:
            out += data[i:i + c]
            i += c
        elif c >= 0xC0:
            out.append(32)
            out.append(c ^ 0x80)
        else:  # 0x80..0xBF: length/distance pair
            if i >= n:
                break
            c = (c << 8) | data[i]
            i += 1
            dist = (c >> 3) & 0x7FF
            length = (c & 7) + 3
            if dist == 0 or dist > len(out):
                raise ParseError("Damaged MOBI text (bad back-reference).")
            for _ in range(length):
                out.append(out[-dist])
        if len(out) > MAX_TEXT:
            raise ParseError("MOBI text is unreasonably large; refusing to expand it.")
    return bytes(out)


def _trailing_entry(data: bytes, size: int) -> int:
    bitpos, result = 0, 0
    if size <= 0:
        return 0
    while True:
        v = data[size - 1]
        result |= (v & 0x7F) << bitpos
        bitpos += 7
        size -= 1
        if (v & 0x80) or bitpos >= 28 or size == 0:
            return result


def _trim_record(rec: bytes, flags: int) -> bytes:
    num = 0
    f = flags >> 1
    while f:
        if f & 1:
            num += _trailing_entry(rec, len(rec) - num)
        f >>= 1
    if flags & 1 and len(rec) - num - 1 >= 0:
        num += (rec[len(rec) - num - 1] & 3) + 1
    return rec[:max(0, len(rec) - num)]


class MobiBook:
    def __init__(self, path: Path) -> None:
        try:
            if path.stat().st_size > MAX_FILE:
                raise ParseError("MOBI file is too large to process.")
            self.data = path.read_bytes()
        except OSError as exc:
            raise ParseError(f"Cannot read file: {exc}") from exc
        d = self.data
        if len(d) < 80 or d[60:68] not in (b"BOOKMOBI", b"TEXtREAd"):
            raise ParseError("Not a MOBI/PalmDOC file (or an unsupported Kindle format such as Topaz).")
        n_rec = struct.unpack(">H", d[76:78])[0]
        if n_rec < 2 or 78 + 8 * n_rec > len(d):
            raise ParseError("Damaged MOBI file (bad record table).")
        offs = [struct.unpack(">I", d[78 + 8 * i:82 + 8 * i])[0] for i in range(n_rec)] + [len(d)]
        if any(offs[i] > offs[i + 1] for i in range(n_rec)) or offs[n_rec - 1] > len(d):
            raise ParseError("Damaged MOBI file (bad record offsets).")
        self.offs, self.n_rec = offs, n_rec
        r0 = d[offs[0]:offs[1]]
        self.compression, _, self.text_len, self.n_text, self.rec_size, enc = struct.unpack(">HHIHHH", r0[:14])
        if enc not in (0,):
            raise EncryptedError("This MOBI/AZW file is DRM-protected and cannot be read or indexed.")
        if self.compression not in (1, 2):
            raise ParseError("This MOBI uses Huffman/CDIC compression, which is not supported.", "unsupported")
        self.version, self.codepage, self.first_image, self.extra_flags = 0, 1252, 0xFFFFFFFF, 0
        self.title, self.exth = "", {}
        if r0[16:20] == b"MOBI":
            hlen = struct.unpack(">I", r0[20:24])[0]
            self.codepage = struct.unpack(">I", r0[28:32])[0]
            self.version = struct.unpack(">I", r0[36:40])[0]
            name_off, name_len = struct.unpack(">II", r0[84:92])
            if hlen >= 116:
                self.first_image = struct.unpack(">I", r0[108:112])[0]
            exth_flags = struct.unpack(">I", r0[128:132])[0] if hlen >= 116 else 0
            if hlen >= 228:
                self.extra_flags = struct.unpack(">H", r0[242:244])[0]
            self.title = r0[name_off:name_off + name_len].decode(self._enc(), errors="replace").strip()
            if exth_flags & 0x40:
                self.exth = self._parse_exth(r0, 16 + hlen)

    def _enc(self) -> str:
        return "utf-8" if self.codepage == 65001 else "cp1252"

    def _parse_exth(self, r0: bytes, pos: int) -> dict[int, list[bytes]]:
        out: dict[int, list[bytes]] = {}
        if r0[pos:pos + 4] != b"EXTH":
            return out
        count = struct.unpack(">I", r0[pos + 8:pos + 12])[0]
        p = pos + 12
        for _ in range(min(count, 500)):
            if p + 8 > len(r0):
                break
            t, ln = struct.unpack(">II", r0[p:p + 8])
            if ln < 8:
                break
            out.setdefault(t, []).append(r0[p + 8:p + ln])
            p += ln
        return out

    def exth_text(self, key: int) -> str:
        v = self.exth.get(key)
        return v[0].decode(self._enc(), errors="replace").strip() if v else ""

    def html(self) -> str:
        parts = []
        for i in range(1, self.n_text + 1):
            if i >= self.n_rec:
                break
            rec = self.data[self.offs[i]:self.offs[i + 1]]
            rec = _trim_record(rec, self.extra_flags) if self.extra_flags else rec
            parts.append(palmdoc_decompress(rec) if self.compression == 2 else rec)
        raw = b"".join(parts)
        return raw.decode(self._enc(), errors="replace")

    def image(self, index: int) -> bytes | None:
        """Image number ``index`` (1-based, as in <img recindex>)."""
        if self.first_image == 0xFFFFFFFF:
            return None
        rec = self.first_image + index - 1
        if rec <= 0 or rec >= self.n_rec:
            return None
        return self.data[self.offs[rec]:self.offs[rec + 1]]

    def cover(self) -> bytes | None:
        off = self.exth.get(201)
        if off and len(off[0]) == 4:
            n = struct.unpack(">I", off[0])[0]
            return self.image(n + 1)
        return self.image(1)

    def chapters(self) -> list[str]:
        """HTML parts split at page breaks (empty parts removed)."""
        html = self.html()
        parts = re.split(r"<mbp:pagebreak\s*/?>", html, flags=re.I)
        parts = [p for p in parts if re.sub(r"<[^>]+>|\s|&nbsp;", "", p)]
        return parts or [html]


class MobiParser:
    def parse(self, path: Path) -> ParsedDocument:
        book = MobiBook(path)
        meta = Metadata(
            title=book.exth_text(503) or book.title,
            authors=[a.decode(book._enc(), errors="replace").strip() for a in book.exth.get(100, []) if a.strip()],
            publisher=book.exth_text(101), language=(book.exth_text(524).split("-")[0].lower()),
            isbn=clean_isbn(book.exth_text(104)), year=extract_year(book.exth_text(106)),
            description=BeautifulSoup(book.exth_text(103), "lxml").get_text(" ").strip(),
            subjects=[s.decode(book._enc(), errors="replace").strip() for s in book.exth.get(105, [])][:20])
        parts = book.chapters()
        pd = ParsedDocument(metadata=meta, segments=iter(()), cover=book.cover())
        if book.version >= 8:
            pd.warnings.append("AZW3/KF8 file: text was extracted but chapter structure is not available")
        titles: list[str] = []
        for i, part in enumerate(parts):
            soup = BeautifulSoup(part, "lxml")
            h = soup.find(re.compile(r"^h[1-3]$"))
            titles.append(" ".join(h.get_text(" ").split()) if h else f"Section {i + 1}")
        pd.toc = [TocEntry(t, i, 1) for i, t in enumerate(titles)] if len(parts) > 1 else []
        pd.segments = self._segments(parts, titles)
        return pd

    @staticmethod
    def _segments(parts: list[str], titles: list[str]) -> Iterator[Segment]:
        for i, part in enumerate(parts):
            text, _h = html_to_text(part)
            for off, piece in chunk_text(text):
                yield Segment("chapter", i, piece, titles[i], off)
