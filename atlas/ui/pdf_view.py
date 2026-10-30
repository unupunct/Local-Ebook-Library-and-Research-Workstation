"""PDF page viewer (PyMuPDF): continuous or single-page, zoom, selection, highlights, search hits.

All MuPDF calls happen under FITZ_LOCK. Page rendering runs on a thread pool;
the UI thread only paints finished images.
"""
from __future__ import annotations

import logging
from collections import OrderedDict

import pymupdf
from PySide6.QtCore import QObject, QPointF, QRect, QRectF, QRunnable, QThreadPool, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QAbstractScrollArea

from ..parsers.base import FITZ_LOCK

log = logging.getLogger(__name__)
GAP = 12
DPI_FACTOR = 96 / 72
CACHE_PAGES = 30


class _Signals(QObject):
    rendered = Signal(int, int, QImage)


class _RenderTask(QRunnable):
    def __init__(self, doc, page: int, scale: float, key: int, sig: _Signals) -> None:
        super().__init__()
        self.doc, self.page, self.scale, self.key, self.sig = doc, page, scale, key, sig

    def run(self) -> None:
        try:
            with FITZ_LOCK:
                pix = self.doc[self.page].get_pixmap(matrix=pymupdf.Matrix(self.scale, self.scale), alpha=False)
                img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
            self.sig.rendered.emit(self.page, self.key, img)
        except Exception:  # document closed while rendering, etc.
            log.debug("render failed for page %s", self.page, exc_info=True)


class PdfView(QAbstractScrollArea):
    pageChanged = Signal(int)
    selectionChanged = Signal()
    zoomChanged = Signal(float)
    highlightClicked = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.doc = None
        self.n = 0
        self.sizes: list[tuple[float, float]] = []   # page size in points
        self.zoom = 1.0
        self.mode = "continuous"
        self.cur = 1
        self.bg = QColor("#2b2d31")
        self.page_bg = QColor("white")
        self.invert = False
        self._tops: list[int] = []
        self._cache: OrderedDict[tuple[int, int], QImage] = OrderedDict()
        self._pending: set[tuple[int, int]] = set()
        self._words: dict[int, list] = {}
        self._sel: tuple[tuple[int, int], tuple[int, int]] | None = None
        self._press: tuple[int, int] | None = None
        self._dragging = False
        self.highlights: dict[int, list[tuple[QRectF, QColor, int]]] = {}
        self.hits: dict[int, list[QRectF]] = {}
        self._hit_list: list[tuple[int, QRectF]] = []
        self._hit_i = -1
        self._sig = _Signals()
        self._sig.rendered.connect(self._on_rendered)
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(1)
        self.viewport().setCursor(Qt.IBeamCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName("PDF page view")
        self.verticalScrollBar().valueChanged.connect(self._scrolled)
        self.horizontalScrollBar().valueChanged.connect(lambda *_: self.viewport().update())

    # ---------------------------------------------------------------- loading
    def open(self, path: str) -> None:
        self.close_doc()
        with FITZ_LOCK:
            doc = pymupdf.open(path)
            if doc.needs_pass:
                doc.close()
                raise ValueError("This PDF is password protected.")
            self.doc = doc
            self.n = doc.page_count
            self.sizes = []
            for i in range(self.n):
                r = doc[i].rect
                self.sizes.append((r.width, r.height))
        self.cur = 1
        self._relayout()

    def close_doc(self) -> None:
        self._pool.clear()
        self._pool.waitForDone(3000)
        with FITZ_LOCK:
            if self.doc is not None:
                self.doc.close()
        self.doc = None
        self.n = 0
        self._cache.clear(); self._pending.clear(); self._words.clear()
        self.highlights.clear(); self.hits.clear(); self._sel = None; self._hit_list = []

    def toc(self) -> list[tuple[int, str, int]]:
        with FITZ_LOCK:
            return [(l, t, p) for l, t, p in self.doc.get_toc(simple=True)] if self.doc else []

    # ----------------------------------------------------------------- layout
    @property
    def scale(self) -> float:
        return self.zoom * DPI_FACTOR

    def _page_px(self, i: int) -> tuple[int, int]:
        w, h = self.sizes[i]
        return int(w * self.scale), int(h * self.scale)

    def _relayout(self) -> None:
        if not self.n:
            return
        if self.mode == "continuous":
            y = GAP
            self._tops = []
            for i in range(self.n):
                self._tops.append(y)
                y += self._page_px(i)[1] + GAP
            total_h = y
            total_w = max(self._page_px(i)[0] for i in range(self.n)) + 2 * GAP
        else:
            self._tops = [GAP] * self.n
            w, h = self._page_px(self.cur - 1)
            total_h, total_w = h + 2 * GAP, w + 2 * GAP
        vp = self.viewport().size()
        vs, hs = self.verticalScrollBar(), self.horizontalScrollBar()
        vs.setRange(0, max(0, total_h - vp.height())); vs.setPageStep(vp.height()); vs.setSingleStep(40)
        hs.setRange(0, max(0, total_w - vp.width())); hs.setPageStep(vp.width()); hs.setSingleStep(40)
        self._total = (total_w, total_h)
        self.viewport().update()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        keep = self.cur
        self._relayout()
        if self.mode == "continuous":
            self.goto_page(keep)

    def _x_offset(self, i: int) -> int:
        w = self._page_px(i)[0]
        vw = self.viewport().width()
        return (vw - w) // 2 - self.horizontalScrollBar().value() if w + 2 * GAP <= vw else GAP - self.horizontalScrollBar().value()

    def _page_rect(self, i: int) -> QRect:
        w, h = self._page_px(i)
        return QRect(self._x_offset(i), self._tops[i] - self.verticalScrollBar().value(), w, h)

    def _visible_pages(self) -> list[int]:
        if not self.n:
            return []
        if self.mode == "single":
            return [self.cur - 1]
        vh = self.viewport().height()
        out = []
        for i in range(self.n):
            r = self._page_rect(i)
            if r.bottom() >= 0 and r.top() <= vh:
                out.append(i)
            elif r.top() > vh:
                break
        return out

    # ------------------------------------------------------------------ paint
    def paintEvent(self, e) -> None:
        p = QPainter(self.viewport())
        p.fillRect(self.viewport().rect(), self.bg)
        for i in self._visible_pages():
            r = self._page_rect(i)
            img = self._image(i)
            if img is not None:
                p.drawImage(r, img)
                if self.invert:
                    p.setCompositionMode(QPainter.CompositionMode_Difference)
                    p.fillRect(r, QColor("white"))
                    p.setCompositionMode(QPainter.CompositionMode_SourceOver)
            else:
                p.fillRect(r, self.page_bg if not self.invert else QColor("#111"))
                p.setPen(QColor("#888"))
                p.drawText(r, Qt.AlignCenter, f"Rendering page {i + 1}…")
            self._paint_overlays(p, i, r)
        p.end()

    def _to_px(self, rect: QRectF, page_rect: QRect) -> QRectF:
        s = self.scale
        return QRectF(page_rect.left() + rect.left() * s, page_rect.top() + rect.top() * s, rect.width() * s, rect.height() * s)

    def _paint_overlays(self, p: QPainter, i: int, r: QRect) -> None:
        for rect, color, _aid in self.highlights.get(i + 1, []):
            c = QColor(color); c.setAlpha(110)
            p.fillRect(self._to_px(rect, r), c)
        for rect in self.hits.get(i + 1, []):
            p.fillRect(self._to_px(rect, r), QColor(255, 140, 0, 120))
        if self._hit_list and 0 <= self._hit_i < len(self._hit_list):
            pg, rect = self._hit_list[self._hit_i]
            if pg == i + 1:
                p.setPen(QPen(QColor("#ff5500"), 2)); p.setBrush(Qt.NoBrush)
                p.drawRect(self._to_px(rect, r))
        for pg, rect in self._selection_rects():
            if pg == i + 1:
                p.fillRect(self._to_px(rect, r), QColor(60, 130, 230, 110))

    # ---------------------------------------------------------------- render
    def _image(self, i: int) -> QImage | None:
        key = (i, int(self.scale * 100))
        img = self._cache.get(key)
        if img is not None:
            self._cache.move_to_end(key)
            return img
        if key not in self._pending and self.doc is not None:
            self._pending.add(key)
            self._pool.start(_RenderTask(self.doc, i, self.scale, key[1], self._sig))
        # show a stale render at another zoom while the new one is produced
        for (pi, k), im in self._cache.items():
            if pi == i:
                return im
        return None

    def _on_rendered(self, page: int, key: int, img: QImage) -> None:
        k = (page, key)
        self._pending.discard(k)
        if self.doc is None:
            return
        self._cache[k] = img
        while len(self._cache) > CACHE_PAGES:
            self._cache.popitem(last=False)
        self.viewport().update()

    # -------------------------------------------------------------- navigation
    def _scrolled(self, _v: int) -> None:
        self.viewport().update()
        if self.mode != "continuous" or not self.n:
            return
        mid = self.verticalScrollBar().value() + self.viewport().height() // 3
        page = 1
        for i, t in enumerate(self._tops):
            if t <= mid:
                page = i + 1
            else:
                break
        if page != self.cur:
            self.cur = page
            self.pageChanged.emit(page)

    def goto_page(self, n: int, frac: float = 0.0) -> None:
        if not self.n:
            return
        n = max(1, min(self.n, n))
        if self.mode == "single":
            if n != self.cur:
                self.cur = n
                self._relayout()
            self.verticalScrollBar().setValue(int(self._page_px(n - 1)[1] * frac))
            self.pageChanged.emit(n)
        else:
            self.cur = n
            self.verticalScrollBar().setValue(self._tops[n - 1] - GAP + int(self._page_px(n - 1)[1] * frac))
            self.pageChanged.emit(n)
        self.viewport().update()

    def page_fraction(self) -> float:
        if not self.n or self.mode != "continuous":
            return 0.0
        i = self.cur - 1
        off = self.verticalScrollBar().value() - (self._tops[i] - GAP)
        return max(0.0, min(1.0, off / max(1, self._page_px(i)[1])))

    def set_mode(self, mode: str) -> None:
        keep = self.cur
        self.mode = mode
        self._relayout()
        self.goto_page(keep)

    def set_zoom(self, z: float, anchor_page: bool = True) -> None:
        z = max(0.25, min(6.0, z))
        if abs(z - self.zoom) < 1e-3:
            return
        keep, frac = self.cur, self.page_fraction()
        self.zoom = z
        self._relayout()
        self.goto_page(keep, frac)
        self.zoomChanged.emit(z)

    def fit_width(self) -> None:
        if self.n:
            w = self.sizes[self.cur - 1][0]
            self.set_zoom((self.viewport().width() - 2 * GAP - 2) / (w * DPI_FACTOR))

    def fit_page(self) -> None:
        if self.n:
            w, h = self.sizes[self.cur - 1]
            self.set_zoom(min((self.viewport().width() - 2 * GAP) / (w * DPI_FACTOR), (self.viewport().height() - 2 * GAP) / (h * DPI_FACTOR)))

    def wheelEvent(self, e) -> None:
        if e.modifiers() & Qt.ControlModifier:
            self.set_zoom(self.zoom * (1.1 if e.angleDelta().y() > 0 else 1 / 1.1))
            e.accept()
            return
        if self.mode == "single":
            vs = self.verticalScrollBar()
            d = e.angleDelta().y()
            if d < 0 and vs.value() >= vs.maximum() and self.cur < self.n:
                self.goto_page(self.cur + 1); return
            if d > 0 and vs.value() <= vs.minimum() and self.cur > 1:
                self.goto_page(self.cur - 1); self.verticalScrollBar().setValue(self.verticalScrollBar().maximum()); return
        super().wheelEvent(e)

    def keyPressEvent(self, e) -> None:
        k = e.key()
        vs = self.verticalScrollBar()
        if k in (Qt.Key_PageDown, Qt.Key_Space):
            if self.mode == "single" and vs.value() >= vs.maximum():
                self.goto_page(self.cur + 1)
            else:
                vs.setValue(vs.value() + vs.pageStep() - 40)
        elif k == Qt.Key_PageUp:
            if self.mode == "single" and vs.value() <= vs.minimum():
                self.goto_page(self.cur - 1); vs.setValue(vs.maximum())
            else:
                vs.setValue(vs.value() - vs.pageStep() + 40)
        elif k == Qt.Key_Home:
            self.goto_page(1)
        elif k == Qt.Key_End:
            self.goto_page(self.n)
        elif k in (Qt.Key_Plus, Qt.Key_Equal) and e.modifiers() & Qt.ControlModifier:
            self.set_zoom(self.zoom * 1.15)
        elif k == Qt.Key_Minus and e.modifiers() & Qt.ControlModifier:
            self.set_zoom(self.zoom / 1.15)
        elif k == Qt.Key_C and e.modifiers() & Qt.ControlModifier:
            self.copy_selection()
        elif k == Qt.Key_Down:
            vs.setValue(vs.value() + 60)
        elif k == Qt.Key_Up:
            vs.setValue(vs.value() - 60)
        elif k == Qt.Key_Right and self.mode == "single":
            self.goto_page(self.cur + 1)
        elif k == Qt.Key_Left and self.mode == "single":
            self.goto_page(self.cur - 1)
        else:
            super().keyPressEvent(e)

    # ---------------------------------------------------------------- selection
    def page_words(self, page: int) -> list:
        w = self._words.get(page)
        if w is None:
            with FITZ_LOCK:
                w = self.doc[page - 1].get_text("words") if self.doc else []
            self._words[page] = w
        return w

    def _locate(self, pos) -> tuple[int, QPointF] | None:
        for i in self._visible_pages():
            r = self._page_rect(i)
            if r.adjusted(0, -GAP // 2, 0, GAP // 2).contains(pos):
                return i + 1, QPointF((pos.x() - r.left()) / self.scale, (pos.y() - r.top()) / self.scale)
        return None

    def _nearest_word(self, page: int, pt: QPointF) -> int | None:
        words = self.page_words(page)
        if not words:
            return None
        best, bd = None, 1e18
        for idx, w in enumerate(words):
            x0, y0, x1, y1 = w[:4]
            dx = max(x0 - pt.x(), 0, pt.x() - x1)
            dy = max(y0 - pt.y(), 0, pt.y() - y1)
            d = dy * dy * 4 + dx * dx
            if d < bd:
                best, bd = idx, d
        return best

    def mousePressEvent(self, e) -> None:
        self.setFocus()
        if e.button() != Qt.LeftButton:
            return
        loc = self._locate(e.position().toPoint())
        self._dragging = False
        if loc is None:
            self._press = None
            self._sel = None
            self.viewport().update(); self.selectionChanged.emit()
            return
        page, pt = loc
        for rect, _c, aid in self.highlights.get(page, []):
            if rect.contains(pt):
                self._click_ann = aid
                break
        else:
            self._click_ann = None
        idx = self._nearest_word(page, pt)
        self._press = (page, idx) if idx is not None else None
        self._press_pos = e.position().toPoint()

    def mouseMoveEvent(self, e) -> None:
        if self._press is None or not (e.buttons() & Qt.LeftButton):
            return
        if not self._dragging and (e.position().toPoint() - self._press_pos).manhattanLength() < 4:
            return
        self._dragging = True
        loc = self._locate(e.position().toPoint())
        if loc is None:
            return
        page, pt = loc
        idx = self._nearest_word(page, pt)
        if idx is not None:
            self._sel = (self._press, (page, idx))
            self.viewport().update()

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            if not self._dragging:
                if getattr(self, "_click_ann", None):
                    self.highlightClicked.emit(self._click_ann)
                self._sel = None
                self.viewport().update()
            self.selectionChanged.emit()
            self._dragging = False

    def mouseDoubleClickEvent(self, e) -> None:
        loc = self._locate(e.position().toPoint())
        if loc:
            idx = self._nearest_word(*loc)
            if idx is not None:
                self._sel = ((loc[0], idx), (loc[0], idx))
                self.viewport().update(); self.selectionChanged.emit()

    def _ordered_selection(self):
        if not self._sel:
            return None
        a, b = self._sel
        return (a, b) if a <= b else (b, a)

    def selection_segments(self) -> list[tuple[int, list]]:
        """[(page, [word tuples])] in reading order."""
        o = self._ordered_selection()
        if not o:
            return []
        (p1, i1), (p2, i2) = o
        out = []
        for pg in range(p1, p2 + 1):
            words = self.page_words(pg)
            lo = i1 if pg == p1 else 0
            hi = i2 if pg == p2 else len(words) - 1
            if words:
                out.append((pg, words[lo:hi + 1]))
        return out

    def _selection_rects(self) -> list[tuple[int, QRectF]]:
        out = []
        for pg, words in self.selection_segments():
            out += [(pg, r) for r in merge_word_rects(words)]
        return out

    def selection_rects_by_page(self) -> dict[int, list[list[float]]]:
        d: dict[int, list[list[float]]] = {}
        for pg, words in self.selection_segments():
            d[pg] = [[r.left(), r.top(), r.right(), r.bottom()] for r in merge_word_rects(words)]
        return d

    def selected_text(self) -> str:
        parts = []
        for _pg, words in self.selection_segments():
            lines, last = [], None
            for w in words:
                key = (w[5], w[6])
                if key != last:
                    lines.append([])
                    last = key
                lines[-1].append(w[4])
            parts.append("\n".join(" ".join(l) for l in lines))
        return "\n".join(parts).strip()

    def selection_parts(self) -> list[tuple[int, str, list[list[float]]]]:
        """[(page, text, rects)] - one entry per page touched by the selection."""
        out = []
        for pg, words in self.selection_segments():
            lines, last = [], None
            for w in words:
                key = (w[5], w[6])
                if key != last:
                    lines.append([]); last = key
                lines[-1].append(w[4])
            rects = [[r.left(), r.top(), r.right(), r.bottom()] for r in merge_word_rects(words)]
            out.append((pg, chr(10).join(" ".join(l) for l in lines), rects))
        return out

    def selection_first_page(self) -> int:
        o = self._ordered_selection()
        return o[0][0] if o else self.cur

    def clear_selection(self) -> None:
        self._sel = None
        self.viewport().update()
        self.selectionChanged.emit()

    def copy_selection(self) -> None:
        t = self.selected_text()
        if t:
            from PySide6.QtGui import QGuiApplication
            QGuiApplication.clipboard().setText(t)

    # --------------------------------------------------------- highlights / find
    def set_highlights(self, items: list[tuple[int, list[list[float]], str, int]]) -> None:
        """items: (page, rects, colour, annotation id)."""
        self.highlights = {}
        for page, rects, color, aid in items:
            for r in rects:
                self.highlights.setdefault(page, []).append((QRectF(r[0], r[1], r[2] - r[0], r[3] - r[1]), QColor(color), aid))
        self.viewport().update()

    def show_hits(self, hits: dict[int, list[list[float]]]) -> None:
        self.hits = {pg: [QRectF(r[0], r[1], r[2] - r[0], r[3] - r[1]) for r in rs] for pg, rs in hits.items()}
        self._hit_list = [(pg, r) for pg in sorted(self.hits) for r in self.hits[pg]]
        self._hit_i = 0 if self._hit_list else -1
        self.viewport().update()

    def search_page(self, page: int, text: str) -> list[list[float]]:
        with FITZ_LOCK:
            rects = self.doc[page - 1].search_for(text) if self.doc else []
        return [[r.x0, r.y0, r.x1, r.y1] for r in rects]

    def find(self, text: str, backwards: bool = False) -> bool:
        """Find next/previous occurrence of text starting from the current hit or page."""
        if not text or not self.n:
            return False
        if not self._hit_list or getattr(self, "_find_text", None) != text:
            self._find_text = text
            self.hits = {}
            for i in range(self.n):
                pg = (self.cur - 1 + i) % self.n + 1
                r = self.search_page(pg, text)
                if r:
                    self.hits[pg] = [QRectF(x[0], x[1], x[2] - x[0], x[3] - x[1]) for x in r]
            self._hit_list = [(pg, r) for pg in sorted(self.hits) for r in self.hits[pg]]
            start = next((k for k, (pg, _r) in enumerate(self._hit_list) if pg >= self.cur), 0)
            self._hit_i = start if self._hit_list else -1
        elif self._hit_list:
            self._hit_i = (self._hit_i + (-1 if backwards else 1)) % len(self._hit_list)
        if not self._hit_list:
            self.viewport().update()
            return False
        pg, rect = self._hit_list[self._hit_i]
        self.goto_page(pg, max(0.0, (rect.top() - 60) / self.sizes[pg - 1][1]))
        return True

    def hit_status(self) -> str:
        return f"{self._hit_i + 1} of {len(self._hit_list)}" if self._hit_list else "no matches"


def merge_word_rects(words: list) -> list[QRectF]:
    rects: list[QRectF] = []
    last_key = None
    for w in words:
        key = (w[5], w[6])
        r = QRectF(w[0], w[1], w[2] - w[0], w[3] - w[1])
        if rects and key == last_key and r.left() - rects[-1].right() < 6:
            rects[-1] = rects[-1].united(r)
        else:
            rects.append(r)
        last_key = key
    return rects
