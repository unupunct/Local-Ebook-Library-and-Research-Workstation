"""Reader page: PDF and reflowable readers, panels, annotations, position saving."""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QKeySequence, QShortcut
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMenu, QProgressBar, QPushButton, QSlider, QSpinBox,
                               QSplitter, QStackedWidget, QTabWidget, QToolButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget, QFontComboBox, QDialogButtonBox)

from ...parsers.base import ParseError
from ..common import button, confirm, hbox, info, label, warn
from ..context import AppContext
from ..pdf_view import PdfView
from ..text_reader import ReflowView, load_source, locate_passage
from .research import ClipDialog

log = logging.getLogger(__name__)
COLORS = [("Yellow", "#f2c94c"), ("Green", "#6fcf97"), ("Blue", "#56ccf2"), ("Pink", "#f78fb3")]


class PrefsDialog(QDialog):
    def __init__(self, ctx: AppContext, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Reading preferences")
        s = ctx.settings
        f = QFormLayout(self)
        self.font = QFontComboBox(); self.font.setCurrentFont(__import__("PySide6.QtGui", fromlist=["QFont"]).QFont(s.reader_font_family))
        self.size = QSpinBox(); self.size.setRange(8, 40); self.size.setValue(s.reader_font_size)
        self.line = QSpinBox(); self.line.setRange(100, 300); self.line.setSuffix(" %"); self.line.setValue(s.reader_line_spacing)
        self.para = QSpinBox(); self.para.setRange(0, 60); self.para.setSuffix(" px"); self.para.setValue(s.reader_paragraph_spacing)
        self.width = QSpinBox(); self.width.setRange(0, 2000); self.width.setSingleStep(20); self.width.setSpecialValueText("full width"); self.width.setValue(s.reader_width)
        self.margin = QSpinBox(); self.margin.setRange(0, 200); self.margin.setValue(s.reader_margin)
        self.theme = QComboBox(); [self.theme.addItem(t.capitalize(), t) for t in ("light", "sepia", "dark")]
        self.theme.setCurrentIndex(self.theme.findData(s.reader_theme))
        self.pag = QCheckBox("Paginated (turn pages instead of scrolling)"); self.pag.setChecked(s.reader_paginated)
        for lbl, w in (("Font", self.font), ("Font size (pt)", self.size), ("Line spacing", self.line), ("Paragraph spacing", self.para),
                       ("Reading width (px)", self.width), ("Side margin (px)", self.margin), ("Theme", self.theme)):
            f.addRow(lbl, w)
        f.addRow(self.pag)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel); bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        f.addRow(bb)

    def apply(self, s) -> None:
        s.reader_font_family = self.font.currentFont().family(); s.reader_font_size = self.size.value()
        s.reader_line_spacing = self.line.value(); s.reader_paragraph_spacing = self.para.value()
        s.reader_width = self.width.value(); s.reader_margin = self.margin.value()
        s.reader_theme = self.theme.currentData(); s.reader_paginated = self.pag.isChecked()
        s.save()


def prefs_dict(s) -> dict:
    return {"font_family": s.reader_font_family, "font_size": s.reader_font_size, "line_spacing": s.reader_line_spacing,
            "paragraph_spacing": s.reader_paragraph_spacing, "width": s.reader_width, "margin": s.reader_margin,
            "theme": s.reader_theme, "paginated": s.reader_paginated}


class ReaderPage(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.setObjectName("page")
        self.ctx = ctx
        self.book: dict | None = None
        self.kind = ""          # pdf | reflow | ""
        self.source = None
        self.chapter = 0
        self.session_start: datetime | None = None
        self.session_t0 = 0.0
        self._pending_loc: dict | None = None
        self.focus_mode = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        # ----------------------------------------------------------- toolbar
        self.toolbar = QWidget()
        tb = QHBoxLayout(self.toolbar)
        tb.setContentsMargins(10, 6, 10, 6)
        self.title = label("No book open", "h2")
        tb.addWidget(self.title, 1)

        def tool(text, tip, slot, checkable=False, shortcut=None):
            b = QToolButton(); b.setText(text); b.setToolTip(tip + (f"  ({shortcut})" if shortcut else "")); b.setAccessibleName(tip)
            b.setCheckable(checkable)
            if checkable:
                b.toggled.connect(slot)
            else:
                b.clicked.connect(lambda _=False: slot())
            if shortcut:
                sc = QShortcut(QKeySequence(shortcut), self); sc.setContext(Qt.WidgetWithChildrenShortcut)
                sc.activated.connect(b.toggle if checkable else slot)
            tb.addWidget(b)
            return b
        self.b_panel = tool("☰ Contents", "Show or hide the side panel", self.toggle_panel, True, "Ctrl+T"); self.b_panel.setChecked(True)
        self.b_prev = tool("◀", "Previous page / chapter", lambda: self.step(-1), shortcut="Alt+Left")
        self.page_label = QLabel(""); tb.addWidget(self.page_label)
        self.b_next = tool("▶", "Next page / chapter", lambda: self.step(1), shortcut="Alt+Right")
        self.goto_edit = QLineEdit(); self.goto_edit.setFixedWidth(60); self.goto_edit.setPlaceholderText("Go to"); self.goto_edit.setToolTip("Page number (PDF) or chapter number")
        self.goto_edit.returnPressed.connect(self.goto_typed); tb.addWidget(self.goto_edit)
        self.pdf_tools = QWidget(); pt = QHBoxLayout(self.pdf_tools); pt.setContentsMargins(0, 0, 0, 0)
        for text, tip, fn in (("−", "Zoom out", lambda: self.pdf.set_zoom(self.pdf.zoom / 1.15)), ("+", "Zoom in", lambda: self.pdf.set_zoom(self.pdf.zoom * 1.15)),
                              ("Fit width", "Fit page width", lambda: self.pdf.fit_width()), ("Fit page", "Fit whole page", lambda: self.pdf.fit_page())):
            b = QToolButton(); b.setText(text); b.setToolTip(tip); b.setAccessibleName(tip); b.clicked.connect(lambda _=False, f=fn: f()); pt.addWidget(b)
        self.zoom_label = QLabel("100%"); pt.addWidget(self.zoom_label)
        self.b_mode = QToolButton(); self.b_mode.setText("Single page"); self.b_mode.setCheckable(True); self.b_mode.setToolTip("Toggle single-page / continuous scrolling")
        self.b_mode.toggled.connect(lambda on: (self.pdf.set_mode("single" if on else "continuous"), self.b_mode.setText("Continuous" if on else "Single page")))
        pt.addWidget(self.b_mode)
        self.b_invert = QToolButton(); self.b_invert.setText("Night"); self.b_invert.setCheckable(True); self.b_invert.setToolTip("Invert PDF colours")
        self.b_invert.toggled.connect(lambda on: (setattr(self.pdf, "invert", on), self.pdf.viewport().update())); pt.addWidget(self.b_invert)
        tb.addWidget(self.pdf_tools)
        self.b_prefs = tool("Aa", "Reading preferences (font, spacing, theme)", self.open_prefs)
        self.b_theme = tool("◐", "Cycle reader theme", self.cycle_theme)
        self.b_find = tool("Find", "Search in this book", self.show_find, shortcut="Ctrl+F")
        self.hl_btn = QToolButton(); self.hl_btn.setText("Highlight ▾"); self.hl_btn.setToolTip("Highlight the selected text")
        self.hl_btn.setPopupMode(QToolButton.MenuButtonPopup)
        m = QMenu(self.hl_btn)
        for name, col in COLORS:
            m.addAction(name).triggered.connect(lambda _=False, c=col: self.highlight(c))
        self.hl_btn.setMenu(m); self.hl_btn.clicked.connect(lambda: self.highlight(COLORS[0][1])); tb.addWidget(self.hl_btn)
        self.b_note = tool("Note", "Highlight with a note", self.add_note)
        self.b_bm = tool("🔖", "Add bookmark here", self.add_bookmark, shortcut="Ctrl+D")
        self.b_clip = tool("Clip", "Save selection as research clipping", self.clip, shortcut="Ctrl+K")
        self.b_lib = tool("Search all books", "Search the whole library for the selected text", self.search_library)
        self.b_full = tool("⛶", "Full screen", self.toggle_fullscreen, shortcut="F11")
        self.b_focus = tool("Focus", "Distraction-free reading (hide toolbar and panels)", self.toggle_focus, shortcut="Ctrl+Shift+F")
        root.addWidget(self.toolbar)

        # --------------------------------------------------------- find bar
        self.find_bar = QWidget(); fb = QHBoxLayout(self.find_bar); fb.setContentsMargins(10, 2, 10, 2)
        self.find_edit = QLineEdit(); self.find_edit.setPlaceholderText("Find in this book…"); self.find_edit.returnPressed.connect(lambda: self.find(False))
        fb.addWidget(self.find_edit, 1)
        fb.addWidget(button("Previous", lambda: self.find(True))); fb.addWidget(button("Next", lambda: self.find(False), True))
        self.find_status = label("", "muted"); fb.addWidget(self.find_status)
        fb.addWidget(button("Close", self.hide_find))
        self.find_bar.hide(); root.addWidget(self.find_bar)
        QShortcut(QKeySequence("Escape"), self, activated=self._escape).setContext(Qt.WidgetWithChildrenShortcut)

        # ------------------------------------------------------------ body
        self.split = QSplitter()
        self.panel = QTabWidget()
        self.toc = QTreeWidget(); self.toc.setHeaderHidden(True); self.toc.itemActivated.connect(self.toc_open); self.toc.itemClicked.connect(self.toc_open)
        self.anns = QListWidget(); self.anns.itemDoubleClicked.connect(self.ann_open)
        self.anns.setContextMenuPolicy(Qt.CustomContextMenu); self.anns.customContextMenuRequested.connect(self.ann_menu)
        self.panel.addTab(self.toc, "Contents"); self.panel.addTab(self.anns, "Bookmarks and notes")
        self.panel.setMinimumWidth(220)
        self.split.addWidget(self.panel)
        self.stack = QStackedWidget()
        self.pdf = PdfView(); self.reflow = ReflowView(); self.empty = QWidget()
        el = QVBoxLayout(self.empty); self.empty_msg = label("Open a book from the Library, Research or Home.", "muted", True); self.empty_msg.setAlignment(Qt.AlignCenter); el.addWidget(self.empty_msg)
        for w in (self.empty, self.pdf, self.reflow):
            self.stack.addWidget(w)
        self.split.addWidget(self.stack)
        self.split.setStretchFactor(1, 1); self.split.setSizes([260, 1000])
        root.addWidget(self.split, 1)
        # ------------------------------------------------------- status bar
        self.statusbar = QWidget(); sb = QHBoxLayout(self.statusbar); sb.setContentsMargins(10, 3, 10, 3)
        self.status = label("", "muted"); sb.addWidget(self.status, 1)
        self.bar = QProgressBar(); self.bar.setRange(0, 1000); self.bar.setFixedWidth(220); self.bar.setTextVisible(False); sb.addWidget(self.bar)
        self.pct = label("0%", "muted"); sb.addWidget(self.pct)
        root.addWidget(self.statusbar)

        self.pdf.pageChanged.connect(self._pdf_page)
        self.pdf.zoomChanged.connect(lambda z: self.zoom_label.setText(f"{z * 100:.0f}%"))
        self.pdf.highlightClicked.connect(self.edit_annotation)
        self.reflow.linkRequested.connect(self.on_link)
        self.reflow.positionChanged.connect(self._reflow_moved)
        self.reflow.mousePressEvent = self._reflow_click(self.reflow.mousePressEvent)
        self._save_timer = QTimer(self, interval=5000)
        self._save_timer.timeout.connect(self.save_position)
        ctx.open_book_requested.connect(self.open_book)
        ctx.notes_changed.connect(self.refresh_annotations)
        self._set_kind("")

    # ------------------------------------------------------------------ open
    def _set_kind(self, kind: str) -> None:
        self.kind = kind
        self.stack.setCurrentWidget({"pdf": self.pdf, "reflow": self.reflow}.get(kind, self.empty))
        self.pdf_tools.setVisible(kind == "pdf")
        self.b_prefs.setVisible(kind == "reflow"); self.b_theme.setVisible(kind == "reflow")
        for w in (self.b_find, self.hl_btn, self.b_note, self.b_bm, self.b_clip, self.b_lib, self.b_prev, self.b_next, self.goto_edit):
            w.setEnabled(kind != "")

    def open_book(self, book_id: int, locator: dict | None = None) -> None:
        self.save_position()
        self._end_session()
        book = self.ctx.lib.get_book(book_id)
        if book is None:
            warn(self, "Book not found", "This book is no longer in the library.")
            return
        self.book = book
        self.title.setText(book["title"])
        self.panel.setTabText(1, "Bookmarks and notes")
        self.refresh_annotations()
        self.hide_find()
        self.ctx.goto_page_requested.emit("reader")
        if not os.path.isfile(book["path"]):
            self._offline(book)
            return
        loc = locator or {}
        try:
            if book["format"] == "pdf":
                self._open_pdf(book, loc)
            else:
                self._open_reflow(book, loc)
        except (ParseError, ValueError, OSError) as exc:
            log.warning("Cannot open %s: %s", book["id"], exc)
            self._failed(book, str(exc))
            return
        except Exception as exc:
            log.exception("Reader failed for book %s", book["id"])
            self._failed(book, f"{type(exc).__name__}: {exc}")
            return
        self.session_start = datetime.now(); self.session_t0 = time.time()
        self._save_timer.start()
        self.ctx.db.set_state("last_book", str(book_id))

    def _offline(self, book: dict) -> None:
        self._set_kind("")
        self.empty_msg.setText(f"“{book['title']}” cannot be opened because its file is offline.\n\n{book['path']}\n\n"
                               "The drive may be disconnected, or the file was moved or deleted. Your bookmarks, notes and reading progress "
                               "are still available in the panel on the left. Reconnect the drive and use Import → Check availability.")
        self.status.setText("Source file offline")
        self.toc.clear()
        self.b_clip.setEnabled(False)

    def _failed(self, book: dict, msg: str) -> None:
        self._set_kind("")
        self.empty_msg.setText(f"“{book['title']}” could not be opened.\n\n{msg}")
        self.status.setText("Could not open file")

    def _open_pdf(self, book: dict, loc: dict) -> None:
        self.pdf.open(book["path"])
        self._set_kind("pdf")
        prog = self.ctx.reading.get_progress(book["id"])
        self.toc.clear()
        stack: list[tuple[int, QTreeWidgetItem]] = []
        for lvl, title, page in self.pdf.toc():
            it = QTreeWidgetItem([title]); it.setData(0, Qt.UserRole, page)
            while stack and stack[-1][0] >= lvl:
                stack.pop()
            (stack[-1][1].addChild(it) if stack else self.toc.addTopLevelItem(it))
            stack.append((lvl, it))
        self.toc.expandToDepth(0)
        if prog and prog.get("zoom"):
            self.pdf.zoom = prog["zoom"]; self.pdf._relayout(); self.zoom_label.setText(f"{self.pdf.zoom * 100:.0f}%")
        else:
            QTimer.singleShot(0, self.pdf.fit_width)
        self.apply_pdf_highlights()
        if loc.get("type") == "page" or (loc.get("num") and book["format"] == "pdf"):
            page = int(loc.get("num") or 1)
            QTimer.singleShot(60, lambda: self._pdf_goto_result(page, loc))
        elif prog and prog["locator"].get("type") == "page":
            QTimer.singleShot(60, lambda: self.pdf.goto_page(int(prog["locator"]["num"]), float(prog["locator"].get("frac", 0))))
        self._pdf_page(self.pdf.cur)

    def _pdf_goto_result(self, page: int, loc: dict) -> None:
        self.pdf.goto_page(page, float(loc.get("frac", 0)))
        terms = [t for t in loc.get("terms", []) if len(t) > 1][:4]
        if terms:
            hits: dict[int, list] = {}
            for t in terms:
                for r in self.pdf.search_page(page, t):
                    hits.setdefault(page, []).append(r)
            if hits:
                self.pdf.show_hits(hits)
                first = hits[page][0]
                self.pdf.goto_page(page, max(0.0, (first[1] - 80) / self.pdf.sizes[page - 1][1]))

    def _open_reflow(self, book: dict, loc: dict) -> None:
        if self.source is not None:
            self.source.close()
        self.source = load_source(book["path"], book["format"])
        self._set_kind("reflow")
        self.reflow.apply_prefs(prefs_dict(self.ctx.settings))
        self.toc.clear()
        entries = self.source.toc or [(t, i, 1) for i, t in enumerate(self.source.titles)] if self.source.count() > 1 else self.source.toc
        stack: list[tuple[int, QTreeWidgetItem]] = []
        for title, ch, lvl in entries:
            it = QTreeWidgetItem([title]); it.setData(0, Qt.UserRole, ch)
            while stack and stack[-1][0] >= lvl:
                stack.pop()
            (stack[-1][1].addChild(it) if stack else self.toc.addTopLevelItem(it))
            stack.append((lvl, it))
        self.toc.expandToDepth(1)
        prog = self.ctx.reading.get_progress(book["id"])
        if loc.get("type") == "chapter" or (loc.get("num") is not None and "excerpt" in loc):
            self.load_chapter(int(loc.get("num", 0)), loc=loc)
        elif prog and prog["locator"].get("type") == "chapter":
            pl = prog["locator"]
            self.load_chapter(int(pl.get("num", 0)), offset=pl.get("offset"))
        else:
            self.load_chapter(0)

    def load_chapter(self, ch: int, offset: int | None = None, loc: dict | None = None) -> None:
        ch = max(0, min(self.source.count() - 1, ch))
        html, images = self.source.chapter(ch)
        self.chapter = ch
        self.reflow.show_chapter(html, images)
        self.reflow.apply_prefs(prefs_dict(self.ctx.settings))
        self.apply_reflow_highlights()
        self._update_reflow_status()
        if loc:
            plain = self.reflow.document().toPlainText()
            span = None
            if loc.get("start") is not None and plain[loc["start"]:loc["end"]] == loc.get("quote"):
                span = (loc["start"], loc["end"])
            else:
                span = locate_passage(plain, loc)
            if span:
                QTimer.singleShot(50, lambda: (self.reflow.scroll_to_offset(span[0], 3), self.reflow.flash(*span)))
            elif loc.get("offset") is not None:
                QTimer.singleShot(50, lambda: self.reflow.scroll_to_offset(int(loc["offset"])))
        elif offset is not None:
            QTimer.singleShot(50, lambda: self.reflow.scroll_to_offset(int(offset), 0))

    # ------------------------------------------------------------- navigation
    def step(self, d: int) -> None:
        if self.kind == "pdf":
            self.pdf.goto_page(self.pdf.cur + d)
        elif self.kind == "reflow":
            if self.reflow.paginated and self.reflow.page_turn(d):
                self._update_reflow_status(); return
            nxt = self.chapter + d
            if 0 <= nxt < self.source.count():
                self.load_chapter(nxt)
                if d < 0 and self.reflow.paginated:
                    self.reflow.verticalScrollBar().setValue(self.reflow.verticalScrollBar().maximum())

    def goto_typed(self) -> None:
        t = self.goto_edit.text().strip()
        if not t.isdigit():
            return
        n = int(t)
        if self.kind == "pdf":
            self.pdf.goto_page(n)
        elif self.kind == "reflow":
            self.load_chapter(n - 1)
        self.goto_edit.clear()

    def toc_open(self, item, _col=0) -> None:
        v = item.data(0, Qt.UserRole)
        if v is None:
            return
        if self.kind == "pdf":
            self.pdf.goto_page(int(v))
        elif self.kind == "reflow":
            self.load_chapter(int(v))

    def on_link(self, href: str) -> None:
        if href.startswith("#") or not href or href.startswith("res:"):
            return
        tgt = self.source.href_target(href, self.chapter) if self.source else None
        if tgt:
            ch, frag = tgt
            if ch != self.chapter:
                self.load_chapter(ch)
            if frag:
                self.reflow.scrollToAnchor(frag)
        else:
            self.ctx.message.emit("External links are not opened from the reader.")

    def _pdf_page(self, page: int) -> None:
        n = self.pdf.n or 1
        self.page_label.setText(f"Page {page} / {n}")
        frac = ((page - 1) + self.pdf.page_fraction()) / n
        self._set_progress(frac * 100)
        self.status.setText(os.path.basename(self.book["path"]) if self.book else "")

    def _reflow_moved(self) -> None:
        if self.kind == "reflow":
            self._update_reflow_status()

    def _update_reflow_status(self) -> None:
        if not self.source:
            return
        n = self.source.count()
        if self.reflow.paginated:
            a, b = self.reflow.page_info()
            self.page_label.setText(f"{self.source.titles[self.chapter][:28]} · page {a}/{b}")
        else:
            self.page_label.setText(f"Chapter {self.chapter + 1} / {n}" if n > 1 else "")
        self._set_progress((self.chapter + self.reflow.progress_in_chapter()) / n * 100)
        self.status.setText(self.source.titles[self.chapter] if n > 1 else "")

    def _set_progress(self, pct: float) -> None:
        self.bar.setValue(int(pct * 10)); self.pct.setText(f"{pct:.0f}%")

    # --------------------------------------------------------------- position
    def current_locator(self) -> tuple[dict, float, float | None] | None:
        if not self.book:
            return None
        if self.kind == "pdf":
            n = max(1, self.pdf.n)
            return {"type": "page", "num": self.pdf.cur, "frac": round(self.pdf.page_fraction(), 4)}, ((self.pdf.cur - 1) + self.pdf.page_fraction()) / n * 100, self.pdf.zoom
        if self.kind == "reflow":
            n = self.source.count()
            return ({"type": "chapter", "num": self.chapter, "offset": self.reflow.top_offset(), "label": self.source.titles[self.chapter]},
                    (self.chapter + self.reflow.progress_in_chapter()) / n * 100, None)
        return None

    def save_position(self) -> None:
        if not self.ctx.settings.autosave_position and self.sender() is self._save_timer:
            return
        cur = self.current_locator()
        if cur and self.book:
            loc, pct, zoom = cur
            try:
                self.ctx.reading.save_progress(self.book["id"], loc, pct, zoom)
            except Exception:
                log.exception("Could not save reading position")

    def _end_session(self) -> None:
        if self.session_start and self.book:
            self.ctx.reading.record_session(self.book["id"], self.session_start, int(time.time() - self.session_t0))
        self.session_start = None

    def shutdown(self) -> None:
        self.save_position(); self._end_session()
        self.pdf.close_doc()
        if self.source:
            self.source.close()

    # ------------------------------------------------------------ annotations
    def selection(self):
        """Returns list of parts [(locator, text)] for the current selection."""
        if self.kind == "pdf":
            parts = self.pdf.selection_parts()
            return [({"type": "page", "num": pg, "rects": rects, "quote": text, "label": f"Page {pg}"}, text) for pg, text, rects in parts if text.strip()]
        if self.kind == "reflow":
            s = self.reflow.selection_info()
            if s:
                s = dict(s, type="chapter", num=self.chapter, label=self.source.titles[self.chapter])
                return [(s, s["quote"])]
        return []

    def highlight(self, color: str, note: str = "", type_: str = "highlight") -> None:
        parts = self.selection()
        if not parts:
            info(self, "Highlight", "Select some text first.")
            return
        for loc, text in parts:
            self.ctx.reading.add(self.book["id"], type_, loc, text, note, color, loc.get("label", ""))
        self._after_annotation()

    def add_note(self) -> None:
        parts = self.selection()
        if not parts:
            info(self, "Note", "Select the text you want to attach a note to.")
            return
        text, ok = QInputDialog.getMultiLineText(self, "Add note", "Your note:")
        if ok:
            self.highlight(COLORS[2][1], text, "note")

    def add_bookmark(self) -> None:
        cur = self.current_locator()
        if not cur or not self.book:
            return
        loc = dict(cur[0])
        label_ = f"Page {loc['num']}" if loc["type"] == "page" else self.source.titles[self.chapter]
        self.ctx.reading.add(self.book["id"], "bookmark", loc, "", "", "#f2c94c", label_)
        self._after_annotation()
        self.ctx.message.emit("Bookmark added.")

    def _after_annotation(self) -> None:
        if self.kind == "pdf":
            self.pdf.clear_selection()
        elif self.kind == "reflow":
            c = self.reflow.textCursor(); c.clearSelection(); self.reflow.setTextCursor(c)
        self.ctx.notes_changed.emit()
        self.apply_highlights()

    def apply_highlights(self) -> None:
        if self.kind == "pdf":
            self.apply_pdf_highlights()
        elif self.kind == "reflow":
            self.apply_reflow_highlights()

    def apply_pdf_highlights(self) -> None:
        items = []
        for a in self.ctx.reading.for_book(self.book["id"]):
            loc = a["locator"]
            if a["type"] in ("highlight", "note") and loc.get("rects"):
                items.append((int(loc["num"]), loc["rects"], a["color"], a["id"]))
        self.pdf.set_highlights(items)

    def apply_reflow_highlights(self) -> None:
        items = []
        for a in self.ctx.reading.for_book(self.book["id"]):
            loc = a["locator"]
            if a["type"] in ("highlight", "note") and loc.get("type") == "chapter" and loc.get("num") == self.chapter:
                items.append({"id": a["id"], "start": loc.get("start"), "end": loc.get("end"), "quote": loc.get("quote", ""),
                              "prefix": loc.get("prefix", ""), "suffix": loc.get("suffix", ""), "color": a["color"]})
        broken = self.reflow.set_highlights(items)
        if broken:
            self.ctx.reading.mark_invalid(broken)
            self.ctx.message.emit(f"{len(broken)} annotation(s) in this chapter could not be placed exactly (the file may have changed). They are marked ⚠ in the list.")
            self.refresh_annotations()

    def refresh_annotations(self) -> None:
        self.anns.clear()
        if not self.book:
            return
        for a in self.ctx.reading.for_book(self.book["id"]):
            loc = a["locator"]
            where = f"p. {loc.get('num')}" if loc.get("type") == "page" else (a["loc_label"] or "")
            icon = {"bookmark": "🔖", "highlight": "▍", "note": "✎"}[a["type"]]
            text = (a["selected_text"] or "").replace("\n", " ")
            txt = f"{'⚠ ' if a['invalid'] else ''}{icon} {where}"
            if text:
                txt += f"\n   “{text[:110]}{'…' if len(text) > 110 else ''}”"
            if a["note"]:
                txt += f"\n   Note: {a['note'][:110]}"
            it = QListWidgetItem(txt); it.setData(Qt.UserRole, a)
            if a["type"] != "bookmark":
                from PySide6.QtGui import QColor
                it.setForeground(QColor(a["color"]))
            if a["invalid"]:
                it.setToolTip("The source file changed after this was saved; the position may be wrong.")
            self.anns.addItem(it)

    def ann_open(self, item) -> None:
        a = item.data(Qt.UserRole)
        loc = a["locator"]
        if self.kind == "pdf" and loc.get("type") == "page":
            self.pdf.goto_page(int(loc["num"]), max(0.0, (loc["rects"][0][1] - 80) / self.pdf.sizes[int(loc["num"]) - 1][1]) if loc.get("rects") else 0)
        elif self.kind == "reflow" and loc.get("type") == "chapter":
            if loc["num"] != self.chapter:
                self.load_chapter(int(loc["num"]))
            span = next(((h["start"], h["end"]) for h in self.reflow.highlights if h["id"] == a["id"]), None)
            if span:
                self.reflow.scroll_to_offset(span[0], 3); self.reflow.flash(*span)
            elif loc.get("offset") is not None:
                self.reflow.scroll_to_offset(int(loc["offset"]))
        elif self.kind == "":
            info(self, "Source offline", "Open the book (reconnect the drive) to jump to this position.")

    def ann_menu(self, pos) -> None:
        it = self.anns.itemAt(pos)
        if not it:
            return
        a = it.data(Qt.UserRole)
        m = QMenu(self)
        m.addAction("Go to").triggered.connect(lambda: self.ann_open(it))
        m.addAction("Edit note…").triggered.connect(lambda: self.edit_annotation(a["id"]))
        if a["selected_text"]:
            m.addAction("Save as research clipping…").triggered.connect(lambda: self._clip_annotation(a))
        m.addAction("Delete").triggered.connect(lambda: (self.ctx.reading.delete(a["id"]), self.ctx.notes_changed.emit(), self.apply_highlights()))
        m.exec(self.anns.viewport().mapToGlobal(pos))

    def edit_annotation(self, ann_id: int) -> None:
        a = next((x for x in self.ctx.reading.for_book(self.book["id"]) if x["id"] == ann_id), None)
        if not a:
            return
        text, ok = QInputDialog.getMultiLineText(self, "Annotation", "Note (empty to keep only the highlight):", a["note"])
        if ok:
            self.ctx.reading.update(ann_id, note=text); self.ctx.notes_changed.emit()

    def _clip_annotation(self, a: dict) -> None:
        loc = dict(a["locator"]); loc.setdefault("label", a["loc_label"])
        ClipDialog.make_clipping(self.ctx, self, a["selected_text"], self.book["id"], loc)

    # ------------------------------------------------------- research bridge
    def clip(self) -> None:
        parts = self.selection()
        if not parts:
            info(self, "Clipping", "Select the passage you want to save, then press Clip.")
            return
        text = "\n".join(t for _l, t in parts)
        loc = dict(parts[0][0])
        loc.pop("rects", None)
        if ClipDialog.make_clipping(self.ctx, self, text, self.book["id"], loc) and self.ctx.settings:
            for l, t in parts:
                self.ctx.reading.add(self.book["id"], "highlight", l, t, "", COLORS[1][1], l.get("label", ""))
            self._after_annotation()

    def search_library(self) -> None:
        parts = self.selection()
        if not parts:
            info(self, "Search", "Select some words first.")
            return
        words = " ".join(parts[0][1].split()[:12])
        self.ctx.search_requested.emit(f'"{words}"' if len(words.split()) > 1 else words)

    # --------------------------------------------------------------- find
    def show_find(self) -> None:
        if self.kind:
            self.find_bar.show(); self.find_edit.setFocus(); self.find_edit.selectAll()

    def hide_find(self) -> None:
        self.find_bar.hide()
        self.pdf.hits = {}; self.pdf._hit_list = []; self.pdf._find_text = None; self.pdf.viewport().update()
        self.reflow._find_sel = None; self.reflow.refresh_overlays()

    def _escape(self) -> None:
        if self.find_bar.isVisible():
            self.hide_find()
        elif self.focus_mode:
            self.toggle_focus()
        elif self.window().isFullScreen():
            self.toggle_fullscreen()

    def find(self, backwards: bool) -> None:
        t = self.find_edit.text().strip()
        if not t:
            return
        if self.kind == "pdf":
            ok = self.pdf.find(t, backwards)
            self.find_status.setText(self.pdf.hit_status() if ok else "no matches")
        elif self.kind == "reflow":
            self._find_reflow(t, backwards)

    def _find_reflow(self, text: str, backwards: bool) -> None:
        from PySide6.QtGui import QTextCursor, QTextDocument
        flags = QTextDocument.FindBackward if backwards else QTextDocument.FindFlag(0)
        v = self.reflow
        n = self.source.count()
        order = [self.chapter] + [(self.chapter + (-k if backwards else k)) % n for k in range(1, n)]
        for k, ch in enumerate(order):
            if ch != self.chapter:
                if text.lower() not in self.source.plain(ch).lower():
                    continue
                self.load_chapter(ch)
                c = v.textCursor(); c.movePosition(QTextCursor.End if backwards else QTextCursor.Start); v.setTextCursor(c)
            if v.find(text, flags):
                c = v.textCursor()
                v.mark_find(c.selectionStart(), c.selectionEnd())
                v.ensureCursorVisible()
                self.find_status.setText(f"chapter {self.chapter + 1}")
                return
            if k == 0 and n == 1:
                c = v.textCursor(); c.movePosition(QTextCursor.End if backwards else QTextCursor.Start); v.setTextCursor(c)
                if v.find(text, flags):
                    c = v.textCursor(); v.mark_find(c.selectionStart(), c.selectionEnd()); v.ensureCursorVisible()
                    self.find_status.setText("wrapped"); return
        self.find_status.setText("no matches")

    # ----------------------------------------------------------- appearance
    def open_prefs(self) -> None:
        d = PrefsDialog(self.ctx, self)
        if d.exec():
            d.apply(self.ctx.settings)
            self.apply_prefs()

    def apply_prefs(self) -> None:
        if self.kind == "reflow":
            off = self.reflow.top_offset()
            self.reflow.apply_prefs(prefs_dict(self.ctx.settings))
            self.reflow.refresh_overlays()
            self.reflow.scroll_to_offset(off, 0)
            self._update_reflow_status()

    def cycle_theme(self) -> None:
        order = ["light", "sepia", "dark"]
        s = self.ctx.settings
        s.reader_theme = order[(order.index(s.reader_theme) + 1) % 3]
        s.save()
        self.apply_prefs()

    def toggle_panel(self, on: bool) -> None:
        if not hasattr(self, "panel"):
            return
        self.panel.setVisible(on)

    def toggle_fullscreen(self) -> None:
        w = self.window()
        w.showNormal() if w.isFullScreen() else w.showFullScreen()

    def toggle_focus(self) -> None:
        self.focus_mode = not self.focus_mode
        for w in (self.toolbar, self.statusbar):
            w.setVisible(not self.focus_mode)
        self.panel.setVisible(not self.focus_mode and self.b_panel.isChecked())
        self.ctx.goto_page_requested.emit("focus:on" if self.focus_mode else "focus:off")

    def _reflow_click(self, orig):
        def handler(e):
            orig(e)
            pos = self.reflow.cursorForPosition(e.position().toPoint()).position()
            aid = self.reflow.highlight_at(pos)
            if aid is not None and not self.reflow.textCursor().hasSelection():
                self.edit_annotation(aid)
        return handler

    # key handling for paginated reflow
    def keyPressEvent(self, e) -> None:
        if self.kind == "reflow" and self.reflow.paginated and e.key() in (Qt.Key_Right, Qt.Key_Space, Qt.Key_PageDown):
            self.step(1); return
        if self.kind == "reflow" and self.reflow.paginated and e.key() in (Qt.Key_Left, Qt.Key_PageUp):
            self.step(-1); return
        super().keyPressEvent(e)
