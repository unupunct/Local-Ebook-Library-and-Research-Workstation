"""Reflowable-document reader (EPUB, HTML, Markdown, TXT, DOCX, RTF).

Content is sanitised (no scripts, styles, forms, remote resources) and shown in a
QTextBrowser, which cannot execute scripts. Positions are stored as chapter index +
character offset in the rendered text plus a quote with context, so annotations stay
meaningful when fonts or widths change.
"""
from __future__ import annotations

import html as html_mod
import posixpath
import re
import zipfile
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup, NavigableString
from PySide6.QtCore import QPoint, QUrl, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocument
from PySide6.QtWidgets import QTextBrowser, QTextEdit

from ..parsers.base import ParseError
from ..safecss import filter_css, filter_declarations
from ..parsers.epub import NS, EpubParser, check_zip_safety
from ..parsers.textdocs import read_text_file
from .theme import READER_THEMES

ALLOWED = {"p", "div", "span", "h1", "h2", "h3", "h4", "h5", "h6", "a", "img", "ul", "ol", "li", "blockquote", "pre", "code",
           "em", "strong", "b", "i", "u", "sub", "sup", "br", "hr", "table", "thead", "tbody", "tr", "td", "th", "dl", "dt", "dd", "small", "cite"}
DROP = {"script", "style", "iframe", "object", "embed", "form", "input", "button", "select", "textarea", "link", "meta", "svg", "math",
        "video", "audio", "canvas", "head", "title", "noscript", "applet", "base"}
KEEP_ATTRS = {"href", "src", "alt", "colspan", "rowspan", "id", "name", "class", "style"}
MAX_IMAGE = 8 * 1024 * 1024
PART_CHARS = 120_000


def _collect_css(soup, chapter_dir: str, zf: zipfile.ZipFile | None) -> str:
    """Gather <style> blocks and linked stylesheets (from inside the EPUB only), already reduced to a safe subset."""
    parts = []
    for st in soup.find_all("style"):
        parts.append(st.get_text())
    if zf is not None:
        for ln in soup.find_all("link"):
            rel = ln.get("rel") or []
            href = unquote(ln.get("href", ""))
            if "stylesheet" in [r.lower() for r in rel] and href and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", href):
                name = posixpath.normpath(posixpath.join(chapter_dir, href))
                try:
                    if name in zf.namelist() and zf.getinfo(name).file_size <= 500_000:
                        parts.append(zf.read(name).decode("utf-8", errors="replace"))
                except KeyError:
                    pass
    return filter_css(chr(10).join(parts))


def sanitize(raw: str | bytes, chapter_dir: str = "", zf: zipfile.ZipFile | None = None, images: dict[str, bytes] | None = None) -> str:
    soup = BeautifulSoup(raw, "lxml")
    css = _collect_css(soup, chapter_dir, zf)
    for t in soup.find_all(DROP):
        t.decompose()
    for el in soup.find_all(True):
        if el.name not in ALLOWED:
            el.unwrap()
            continue
        if "style" in el.attrs:
            safe = filter_declarations(el.attrs["style"])
            if safe:
                el.attrs["style"] = safe
            else:
                del el.attrs["style"]
        for a in list(el.attrs):
            if a not in KEEP_ATTRS:
                del el.attrs[a]
        if el.name == "a":
            href = el.get("href", "")
            if href and not href.startswith("#") and re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", href):
                el.attrs["data-external"] = href
                del el.attrs["href"]
        if el.name == "img":
            src = unquote(el.get("src", ""))
            if src.startswith("res:"):
                if images is None or src[4:] not in images:
                    el.decompose()
                continue
            name = posixpath.normpath(posixpath.join(chapter_dir, src)) if src and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", src) else ""
            if zf is not None and images is not None and name and name in zf.namelist():
                try:
                    if zf.getinfo(name).file_size <= MAX_IMAGE:
                        images[name] = zf.read(name)
                        el.attrs["src"] = "res:" + name
                    else:
                        el.decompose()
                except KeyError:
                    el.decompose()
            elif el.parent is not None:
                el.decompose()
    body = soup.body or soup
    return (f"<style>{css}</style>" if css else "") + body.decode_contents()


def text_to_html(text: str) -> str:
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    return "".join("<p>" + html_mod.escape(p.strip()).replace("\n", " ") + "</p>" for p in paras)


class Source:
    """A readable document made of chapters."""

    def __init__(self) -> None:
        self.titles: list[str] = []
        self.toc: list[tuple[str, int, int]] = []     # title, chapter index, level
        self._plain: dict[int, str] = {}

    def count(self) -> int:
        return len(self.titles)

    def chapter(self, i: int) -> tuple[str, dict[str, bytes]]:
        raise NotImplementedError

    def plain(self, i: int) -> str:
        if i not in self._plain:
            doc = QTextDocument()
            doc.setHtml(self.chapter(i)[0])
            self._plain[i] = doc.toPlainText()
        return self._plain[i]

    def href_target(self, href: str, current: int) -> tuple[int, str] | None:
        return None

    def close(self) -> None:
        pass


class EpubSource(Source):
    def __init__(self, path: str) -> None:
        super().__init__()
        try:
            self.zf = zipfile.ZipFile(path)
            check_zip_safety(self.zf)
            EpubParser._check_drm(self.zf)
            opf_path = EpubParser._opf_path(self.zf)
            opf = ET.fromstring(self.zf.read(opf_path))
        except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
            raise ParseError(f"Cannot open this EPUB: {exc}") from exc
        from urllib.parse import unquote as uq
        base = posixpath.dirname(opf_path)
        manifest = {}
        for it in opf.iterfind(".//opf:manifest/opf:item", NS):
            manifest[it.get("id", "")] = {"href": posixpath.normpath(posixpath.join(base, uq(it.get("href", "")))), "type": it.get("media-type", ""), "props": it.get("properties", "")}
        self.hrefs = [manifest[r.get("idref", "")]["href"] for r in opf.iterfind(".//opf:spine/opf:itemref", NS)
                      if r.get("idref", "") in manifest and "html" in manifest[r.get("idref", "")]["type"]]
        toc_map = EpubParser._toc(self.zf, opf, manifest, base)
        names = {}
        for title, href, _l in toc_map:
            names.setdefault(href.split("#")[0], title)
        self.titles = [names.get(h) or f"Section {i + 1}" for i, h in enumerate(self.hrefs)]
        idx = {h: i for i, h in enumerate(self.hrefs)}
        self.toc = [(t, idx[h.split("#")[0]], lvl) for t, h, lvl in toc_map if h.split("#")[0] in idx]
        if not self.hrefs:
            raise ParseError("This EPUB has no readable chapters.")

    def chapter(self, i: int):
        images: dict[str, bytes] = {}
        h = self.hrefs[i]
        return sanitize(self.zf.read(h), posixpath.dirname(h), self.zf, images), images

    def href_target(self, href: str, current: int):
        path, _, frag = href.partition("#")
        if not path:
            return current, frag
        full = posixpath.normpath(posixpath.join(posixpath.dirname(self.hrefs[current]), unquote(path)))
        return (self.hrefs.index(full), frag) if full in self.hrefs else None

    def close(self) -> None:
        self.zf.close()


class SingleSource(Source):
    def __init__(self, parts: list[tuple[str, str]], toc: list[tuple[str, int, int]] | None = None) -> None:
        super().__init__()
        self.parts = parts
        self.titles = [t for t, _ in parts]
        self.toc = toc or ([(t, i, 1) for i, (t, _h) in enumerate(parts)] if len(parts) > 1 else [])

    def chapter(self, i: int):
        return self.parts[i][1], {}


class MobiSource(Source):
    def __init__(self, path: str) -> None:
        super().__init__()
        from ..parsers.mobi import MobiBook
        self.book = MobiBook(Path(path))
        self.parts = self.book.chapters()
        self.titles = []
        for i, part in enumerate(self.parts):
            h = BeautifulSoup(part, "lxml").find(re.compile(r"^h[1-3]$"))
            self.titles.append(" ".join(h.get_text(" ").split()) if h else f"Section {i + 1}")
        self.toc = [(t, i, 1) for i, t in enumerate(self.titles)] if len(self.parts) > 1 else []

    def chapter(self, i: int):
        images: dict[str, bytes] = {}

        def swap(m):
            n = int(m.group(1))
            data = self.book.image(n)
            if data:
                images[f"mobi{n}"] = data
                return f'<img src="res:mobi{n}"/>'
            return ""
        html = re.sub(r'<img[^>]*?recindex="0*(\d+)"[^>]*>', swap, self.parts[i], flags=re.I)
        return sanitize(html, "", None, images), images


def load_source(path: str, fmt: str) -> Source:
    if fmt == "epub":
        return EpubSource(path)
    if fmt == "mobi":
        return MobiSource(path)
    p = Path(path)
    if fmt == "html":
        return SingleSource([("Document", sanitize(read_text_file(p)))])
    if fmt == "md":
        import markdown
        raw = re.sub(r"^---\s*\n.*?\n---\s*\n", "", read_text_file(p), flags=re.S)
        return SingleSource([("Document", sanitize(markdown.markdown(raw, extensions=["extra", "sane_lists", "fenced_code"])))])
    if fmt == "docx":
        import docx
        d = docx.Document(str(p))
        out = []
        for para in d.paragraphs:
            t = para.text.strip()
            if not t:
                continue
            st = (para.style.name if para.style is not None else "").lower()
            if st.startswith("heading") or st == "title":
                lvl = min(6, int(st[-1])) if st[-1:].isdigit() else 1
                out.append(f"<h{lvl}>{html_mod.escape(t)}</h{lvl}>")
            else:
                out.append(f"<p>{html_mod.escape(t)}</p>")
        for table in d.tables:
            rows = "".join("<tr>" + "".join(f"<td>{html_mod.escape(c.text)}</td>" for c in r.cells) + "</tr>" for r in table.rows)
            out.append(f"<table border='1' cellpadding='4'>{rows}</table>")
        return SingleSource([("Document", "".join(out))])
    if fmt == "rtf":
        from striprtf.striprtf import rtf_to_text
        text = rtf_to_text(read_text_file(p), errors="ignore")
        return _split_text(text)
    if fmt == "txt":
        return _split_text(read_text_file(p))
    raise ParseError(f"No reader for format '{fmt}'.", "unsupported")


def _split_text(text: str) -> SingleSource:
    text = text.replace("\r\n", "\n")
    if len(text) <= PART_CHARS:
        return SingleSource([("Document", text_to_html(text))])
    parts, pos, n = [], 0, 1
    while pos < len(text):
        end = min(len(text), pos + PART_CHARS)
        if end < len(text):
            cut = text.rfind("\n\n", pos + PART_CHARS // 2, end)
            end = cut if cut > pos else end
        parts.append((f"Part {n}", text_to_html(text[pos:end])))
        pos, n = end, n + 1
    return SingleSource(parts)


class ReflowView(QTextBrowser):
    selectionMade = Signal()
    linkRequested = Signal(str)
    positionChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setReadOnly(True)
        self.setAccessibleName("Reading view")
        self.anchorClicked.connect(self._anchor)
        self.cursorPositionChanged.connect(lambda: None)
        self.verticalScrollBar().valueChanged.connect(lambda *_: self.positionChanged.emit())
        self.prefs: dict = {}
        self.highlights: list[dict] = []
        self._flash: tuple[int, int] | None = None
        self._find_sel: tuple[int, int] | None = None
        self.paginated = False

    # ------------------------------------------------------------ appearance
    def apply_prefs(self, p: dict) -> None:
        self.prefs = p
        th = READER_THEMES.get(p.get("theme", "dark"), READER_THEMES["dark"])
        f = QFont(p.get("font_family", "Georgia"))
        f.setPointSizeF(float(p.get("font_size", 14)))
        self.document().setDefaultFont(f)
        self.setFont(f)
        css = (f"body {{ color: {th['fg']}; }} a {{ color: {th['link']}; }} h1,h2,h3 {{ margin-top: 0.8em; }} "
               f"blockquote {{ margin-left: 24px; font-style: italic; }} pre, code {{ font-family: Consolas, monospace; }}")
        self.document().setDefaultStyleSheet(css)
        self.setStyleSheet(f"QTextBrowser {{ background: {th['bg']}; color: {th['fg']}; border: none; selection-background-color: #3a6ea5; selection-color: #ffffff; }}")
        self.paginated = bool(p.get("paginated"))
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff if self.paginated else Qt.ScrollBarAsNeeded)
        self._margins()
        self._block_formats()

    def _margins(self) -> None:
        w = self.width()
        col = int(self.prefs.get("width", 760))
        m = int(self.prefs.get("margin", 24))
        side = max(m, (w - col) // 2) if col > 0 else m
        self.setViewportMargins(side, m, side, m)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._margins()

    def _block_formats(self) -> None:
        cur = QTextCursor(self.document())
        cur.select(QTextCursor.Document)
        bf = QTextBlockFormat()
        bf.setLineHeight(float(self.prefs.get("line_spacing", 150)), QTextBlockFormat.ProportionalHeight.value)
        bf.setBottomMargin(float(self.prefs.get("paragraph_spacing", 8)))
        cur.mergeBlockFormat(bf)

    # --------------------------------------------------------------- content
    def show_chapter(self, html: str, images: dict[str, bytes]) -> None:
        self.document().clear()
        self.highlights = []
        self._flash = self._find_sel = None
        for name, data in images.items():
            img = QImage.fromData(data)
            if not img.isNull():
                maxw = max(300, int(self.prefs.get("width", 760)) or 900)
                if img.width() > maxw:
                    img = img.scaledToWidth(maxw, Qt.SmoothTransformation)
                self.document().addResource(QTextDocument.ImageResource, QUrl("res:" + name), img)
        self.setHtml(f"<html><body>{html}</body></html>")
        self._block_formats()
        self.verticalScrollBar().setValue(0)

    def loadResource(self, type_, name):  # never fetch anything from disk or network
        from PySide6.QtCore import QVariant
        if type_ == QTextDocument.ImageResource and name.scheme() == "res":
            return self.document().resource(type_, name)
        return None

    def _anchor(self, url: QUrl) -> None:
        s = url.toString()
        if s:
            self.linkRequested.emit(s)

    # -------------------------------------------------------------- position
    def top_offset(self) -> int:
        return self.cursorForPosition(QPoint(self.viewport().width() // 2, 2)).position()

    def scroll_to_offset(self, off: int, margin_lines: int = 1) -> None:
        off = max(0, min(off, max(0, self.document().characterCount() - 1)))
        c = QTextCursor(self.document())
        c.setPosition(off)
        rect = self.cursorRect(c)
        vs = self.verticalScrollBar()
        vs.setValue(max(0, vs.value() + rect.top() - 20 * margin_lines))

    def progress_in_chapter(self) -> float:
        vs = self.verticalScrollBar()
        return vs.value() / vs.maximum() if vs.maximum() else 1.0

    def page_turn(self, direction: int) -> bool:
        """Scroll one viewport; return False when already at the edge."""
        vs = self.verticalScrollBar()
        step = max(40, self.viewport().height() - 40)
        if direction > 0 and vs.value() >= vs.maximum():
            return False
        if direction < 0 and vs.value() <= 0:
            return False
        vs.setValue(vs.value() + direction * step)
        return True

    def page_info(self) -> tuple[int, int]:
        vs = self.verticalScrollBar()
        step = max(40, self.viewport().height() - 40)
        return vs.value() // step + 1, (vs.maximum() + step - 1) // step + 1

    def wheelEvent(self, e) -> None:
        if self.paginated:
            self.page_turn(-1 if e.angleDelta().y() > 0 else 1)
            e.accept()
            return
        super().wheelEvent(e)

    # ------------------------------------------------------------- selection
    def selection_info(self) -> dict | None:
        c = self.textCursor()
        if not c.hasSelection():
            return None
        s, e = sorted((c.anchor(), c.position()))
        plain = self.document().toPlainText()
        text = plain[s:e]
        if not text.strip():
            return None
        return {"start": s, "end": e, "quote": text, "prefix": plain[max(0, s - 32):s], "suffix": plain[e:e + 32]}

    # -------------------------------------------------------------- overlays
    def set_highlights(self, items: list[dict]) -> list[int]:
        """Anchor annotations; returns ids that could not be anchored (reported, never moved blindly)."""
        self.highlights = []
        broken = []
        plain = self.document().toPlainText()
        for it in items:
            s, e, quote = it.get("start"), it.get("end"), it.get("quote", "")
            if s is not None and e is not None and plain[s:e] == quote and quote:
                self.highlights.append({"id": it["id"], "start": s, "end": e, "color": it["color"]})
                continue
            pos = _reanchor(plain, quote, it.get("prefix", ""), it.get("suffix", ""))
            if pos is not None:
                self.highlights.append({"id": it["id"], "start": pos, "end": pos + len(quote), "color": it["color"]})
            elif quote:
                broken.append(it["id"])
        self.refresh_overlays()
        return broken

    def flash(self, start: int, end: int) -> None:
        self._flash = (start, end)
        self.refresh_overlays()

    def mark_find(self, start: int, end: int) -> None:
        self._find_sel = (start, end)
        self.refresh_overlays()

    def refresh_overlays(self) -> None:
        sels = []
        for h in self.highlights:
            sels.append(self._sel(h["start"], h["end"], QColor(h["color"]), alpha=120))
        if self._find_sel:
            sels.append(self._sel(*self._find_sel, QColor("#ff9a2e"), alpha=170))
        if self._flash:
            sels.append(self._sel(*self._flash, QColor("#4da3ff"), alpha=130))
        self.setExtraSelections(sels)

    def _sel(self, s: int, e: int, color: QColor, alpha: int):
        sel = QTextEdit.ExtraSelection()
        c = QTextCursor(self.document())
        c.setPosition(s); c.setPosition(e, QTextCursor.KeepAnchor)
        sel.cursor = c
        col = QColor(color); col.setAlpha(alpha)
        fmt = QTextCharFormat(); fmt.setBackground(col)
        if self.prefs.get("theme") != "dark":
            fmt.setForeground(QColor("#000000"))
        sel.format = fmt
        return sel

    def highlight_at(self, pos: int) -> int | None:
        for h in self.highlights:
            if h["start"] <= pos < h["end"]:
                return h["id"]
        return None


def _reanchor(plain: str, quote: str, prefix: str, suffix: str) -> int | None:
    """Re-find a quote only if it identifies a single place (with context). Otherwise give up."""
    if not quote:
        return None
    full = prefix + quote + suffix
    i = plain.find(full)
    if i >= 0 and plain.find(full, i + 1) < 0:
        return i + len(prefix)
    if plain.count(quote) == 1:
        return plain.find(quote)
    return None


def locate_passage(plain: str, locator: dict, total_len_hint: int | None = None) -> tuple[int, int] | None:
    """Best-effort position of a search/clipping passage inside the rendered chapter text."""
    excerpt = " ".join((locator.get("excerpt") or "").replace("…", " ").split())
    terms = [t for t in locator.get("terms", []) if t]
    approx = int(locator.get("char_start", 0))
    lowered = plain.lower()
    cands: list[int] = []
    # 1) a long run of the excerpt (clippings are verbatim)
    frag = excerpt[:60].strip().lower()
    if len(frag) >= 20:
        norm = re.sub(r"\s+", " ", lowered)
        j = norm.find(frag)
        if j >= 0 and len(norm) == len(lowered):
            return j, j + len(frag)
    # 2) occurrences of the first matching term nearest the approximate offset
    for t in terms:
        for m in re.finditer(re.escape(t.lower()), lowered):
            cands.append(m.start())
            if len(cands) > 2000:
                break
        if cands:
            best = min(cands, key=lambda k: abs(k - approx))
            return best, best + len(t)
    return None
