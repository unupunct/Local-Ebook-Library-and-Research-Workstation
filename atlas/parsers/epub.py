"""EPUB adapter: spine-ordered chapters, TOC, metadata, cover, DRM detection.

Uses zipfile + BeautifulSoup directly (EbookLib is used for OPF metadata where it
helps) so we can enforce size limits and never extract to disk.
"""
from __future__ import annotations

import posixpath
import re
import zipfile
from pathlib import Path
from typing import Iterator
from urllib.parse import unquote
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

from .base import (EncryptedError, Metadata, ParsedDocument, ParseError, Segment, TocEntry,
                   chunk_text, clean_isbn, extract_year, normalize_text)

MAX_TOTAL_UNCOMPRESSED = 2 * 1024**3
MAX_ENTRY = 200 * 1024 * 1024
MAX_RATIO = 1000

NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
    "ncx": "http://www.daisy.org/z3986/2005/ncx/",
    "x": "http://www.w3.org/1999/xhtml",
}
FONT_OBFUSCATION = {"http://www.idpf.org/2008/embedding", "http://ansrv.com/ns/fontobfuscation"}


def check_zip_safety(zf: zipfile.ZipFile) -> None:
    total = 0
    for info in zf.infolist():
        total += info.file_size
        if info.file_size > MAX_ENTRY:
            raise ParseError("Archive contains an oversized entry; refusing to read it.")
        if info.compress_size and info.file_size / max(info.compress_size, 1) > MAX_RATIO and info.file_size > 10_000_000:
            raise ParseError("Archive has a suspicious compression ratio; refusing to read it.")
        name = info.filename.replace("\\", "/")
        if name.startswith("/") or ".." in name.split("/"):
            raise ParseError("Archive contains an unsafe path; refusing to read it.")
    if total > MAX_TOTAL_UNCOMPRESSED:
        raise ParseError("Archive is too large when decompressed; refusing to read it.")


def _read(zf: zipfile.ZipFile, name: str) -> bytes:
    return zf.read(name)


def html_to_text(raw: bytes | str) -> tuple[str, str]:
    """Return (plain text with paragraph breaks, first heading)."""
    soup = BeautifulSoup(raw, "lxml")
    for t in soup(["script", "style", "head", "noscript", "svg", "math"]):
        t.decompose()
    heading = ""
    h = soup.find(re.compile(r"^h[1-3]$"))
    if h:
        heading = " ".join(h.get_text(" ").split())
    for br in soup.find_all("br"):
        br.replace_with("\n")
    blocks = []
    for el in soup.find_all(["p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "tr", "pre"]):
        if el.find(["p", "div", "li", "blockquote", "tr", "pre"]):
            continue  # only leaf blocks
        t = el.get_text(" ")
        if t.strip():
            blocks.append(t)
    text = "\n\n".join(blocks) if blocks else (soup.body or soup).get_text("\n")
    return normalize_text(text), heading


class EpubParser:
    def parse(self, path: Path) -> ParsedDocument:
        try:
            zf = zipfile.ZipFile(str(path))
        except (zipfile.BadZipFile, OSError) as exc:
            raise ParseError(f"Not a valid EPUB archive: {exc}") from exc
        try:
            check_zip_safety(zf)
            opf_path = self._opf_path(zf)
            self._check_drm(zf)
            opf = ET.fromstring(_read(zf, opf_path))
        except ParseError:
            zf.close()
            raise
        except (KeyError, ET.ParseError) as exc:
            zf.close()
            raise ParseError(f"Damaged EPUB structure: {exc}") from exc

        base = posixpath.dirname(opf_path)
        manifest: dict[str, dict] = {}
        for it in opf.iterfind(".//opf:manifest/opf:item", NS):
            href = unquote(it.get("href", ""))
            manifest[it.get("id", "")] = {
                "href": posixpath.normpath(posixpath.join(base, href)) if href else "",
                "type": it.get("media-type", ""), "props": it.get("properties", "")}
        spine = [manifest[r.get("idref", "")] for r in opf.iterfind(".//opf:spine/opf:itemref", NS)
                 if r.get("idref", "") in manifest]
        spine = [m for m in spine if "html" in m["type"] or m["href"].lower().endswith((".xhtml", ".html", ".htm"))]
        if not spine:
            zf.close()
            raise ParseError("EPUB has no readable chapters.")

        meta = self._metadata(opf)
        toc_map = self._toc(zf, opf, manifest, base)
        cover = self._cover(zf, opf, manifest)
        toc = []
        order = {m["href"]: i for i, m in enumerate(spine)}
        for title, href, level in toc_map:
            key = href.split("#")[0]
            if key in order:
                toc.append(TocEntry(title, order[key], level))
        pd = ParsedDocument(metadata=meta, segments=iter(()), toc=toc, cover=cover)
        pd.segments = self._chapters(zf, spine, toc_map, pd)
        return pd

    # ----------------------------------------------------------------
    @staticmethod
    def _opf_path(zf: zipfile.ZipFile) -> str:
        c = ET.fromstring(_read(zf, "META-INF/container.xml"))
        rf = c.find(".//c:rootfile", NS)
        if rf is None or not rf.get("full-path"):
            raise ParseError("EPUB container has no package document.")
        return rf.get("full-path")

    @staticmethod
    def _check_drm(zf: zipfile.ZipFile) -> None:
        if "META-INF/encryption.xml" not in zf.namelist():
            return
        try:
            root = ET.fromstring(_read(zf, "META-INF/encryption.xml"))
        except ET.ParseError:
            return
        for m in root.iter("{http://www.w3.org/2001/04/xmlenc#}EncryptionMethod"):
            if m.get("Algorithm") not in FONT_OBFUSCATION:
                raise EncryptedError("This EPUB is DRM-protected and cannot be read or indexed.")

    @staticmethod
    def _metadata(opf) -> Metadata:
        def texts(tag: str) -> list[str]:
            return [(e.text or "").strip() for e in opf.iterfind(f".//dc:{tag}", NS) if (e.text or "").strip()]
        m = Metadata()
        t = texts("title")
        m.title = t[0] if t else ""
        m.authors = texts("creator")
        pub = texts("publisher")
        m.publisher = pub[0] if pub else ""
        d = texts("date")
        m.year = extract_year(d[0]) if d else None
        lang = texts("language")
        m.language = lang[0].split("-")[0].lower() if lang else ""
        m.subjects = texts("subject")
        desc = texts("description")
        m.description = BeautifulSoup(desc[0], "lxml").get_text(" ").strip() if desc else ""
        for ident in texts("identifier"):
            isbn = clean_isbn(ident.replace("urn:isbn:", ""))
            if isbn:
                m.isbn = isbn
                break
        for meta in opf.iterfind(".//opf:meta", NS):
            if meta.get("name") == "calibre:series":
                m.series = meta.get("content", "")
            elif meta.get("name") == "calibre:series_index":
                try:
                    m.series_index = float(meta.get("content", ""))
                except ValueError:
                    pass
        return m

    @staticmethod
    def _toc(zf, opf, manifest, base) -> list[tuple[str, str, int]]:
        out: list[tuple[str, str, int]] = []
        try:
            nav = next((m for m in manifest.values() if "nav" in m["props"].split()), None)
            ncx = next((m for m in manifest.values() if m["type"] == "application/x-dtbncx+xml"), None)
            if nav:
                soup = BeautifulSoup(_read(zf, nav["href"]), "lxml")
                navel = soup.find("nav", attrs={"epub:type": "toc"}) or soup.find("nav")
                if navel:
                    nb = posixpath.dirname(nav["href"])

                    def walk(ol, level):
                        for li in ol.find_all("li", recursive=False):
                            a = li.find("a", recursive=False) or li.find("a")
                            if a and a.get("href"):
                                href = posixpath.normpath(posixpath.join(nb, unquote(a["href"])))
                                out.append((" ".join(a.get_text(" ").split()), href, level))
                            sub = li.find("ol", recursive=False)
                            if sub:
                                walk(sub, level + 1)
                    top = navel.find("ol")
                    if top:
                        walk(top, 1)
            if not out and ncx:
                root = ET.fromstring(_read(zf, ncx["href"]))
                nb = posixpath.dirname(ncx["href"])

                def walk_np(el, level):
                    for np in el.findall("ncx:navPoint", NS):
                        lab = np.find("ncx:navLabel/ncx:text", NS)
                        cont = np.find("ncx:content", NS)
                        if lab is not None and cont is not None:
                            href = posixpath.normpath(posixpath.join(nb, unquote(cont.get("src", ""))))
                            out.append((" ".join((lab.text or "").split()), href, level))
                        walk_np(np, level + 1)
                nm = root.find("ncx:navMap", NS)
                if nm is not None:
                    walk_np(nm, 1)
        except Exception:
            return out
        return out

    @staticmethod
    def _cover(zf, opf, manifest) -> bytes | None:
        try:
            cid = None
            for meta in opf.iterfind(".//opf:meta", NS):
                if meta.get("name") == "cover":
                    cid = meta.get("content")
            item = manifest.get(cid) if cid else None
            if not item:
                item = next((m for m in manifest.values() if "cover-image" in m["props"].split()), None)
            if not item:
                item = next((m for k, m in manifest.items() if "cover" in k.lower() and m["type"].startswith("image/")), None)
            if item:
                return _read(zf, item["href"])
        except Exception:
            return None
        return None

    def _chapters(self, zf, spine, toc_map, pd: ParsedDocument) -> Iterator[Segment]:
        titles: dict[str, str] = {}
        for title, href, _lvl in toc_map:
            titles.setdefault(href.split("#")[0], title)
        try:
            for i, item in enumerate(spine):
                try:
                    raw = _read(zf, item["href"])
                except KeyError:
                    pd.warnings.append(f"missing chapter file {item['href']}")
                    continue
                text, heading = html_to_text(raw)
                if not text:
                    continue
                label = titles.get(item["href"]) or heading or f"Section {i + 1}"
                for off, piece in chunk_text(text):
                    yield Segment("chapter", i, piece, label, off)
        finally:
            zf.close()
