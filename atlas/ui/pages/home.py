"""Home: continue reading, recent activity, statistics and status."""
from __future__ import annotations

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QScrollArea, QVBoxLayout,
                               QWidget, QFrame, QProgressBar)

from ...settings import HOME_SECTIONS
from ..common import CoverCache, card, human_duration, human_size, label
from ..context import AppContext

TITLES = {
    "continue_reading": "Continue reading", "recently_added": "Recently added", "recently_read": "Recently read",
    "favorites": "Favourites", "recently_annotated": "Recently annotated", "popular_tags": "Popular tags",
    "recent_notes": "Recent research notes", "statistics": "Library statistics", "indexing_status": "Indexing status",
    "offline_roots": "Offline or unavailable sources",
}


class BookList(QListWidget):
    def __init__(self, ctx: AppContext, covers: CoverCache, rows, sub=None) -> None:
        super().__init__()
        self.setIconSize(QSize(34, 50))
        self.setFrameShape(QFrame.NoFrame)
        self.setMaximumHeight(60 * min(max(len(rows), 1), 5) + 6)
        self.setSelectionMode(QListWidget.SingleSelection)
        for r in rows:
            it = QListWidgetItem(QIcon(covers.get(r["cover_path"], r["title"], r["authors"], (34, 50))),
                                 r["title"] + ("" if not sub else f"\n{sub(r)}"))
            it.setData(Qt.UserRole, r["id"])
            if not r["available"]:
                it.setToolTip("Source file offline")
            self.addItem(it)
        self.itemActivated.connect(lambda it: ctx.open_book(it.data(Qt.UserRole)))
        self.itemDoubleClicked.connect(lambda it: ctx.open_book(it.data(Qt.UserRole)))


class HomePage(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.setObjectName("page")
        self.ctx = ctx
        self.covers = CoverCache()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 18, 24, 12)
        outer.addWidget(label("Home", "h1"))
        self.empty = label("", "muted", wrap=True)
        outer.addWidget(self.empty)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.NoFrame)
        self.body = QWidget()
        self.grid = QGridLayout(self.body)
        self.grid.setSpacing(14)
        sc.setWidget(self.body)
        outer.addWidget(sc, 1)
        for sig in (ctx.library_changed, ctx.notes_changed, ctx.tags_changed, ctx.roots_changed):
            sig.connect(self.refresh)
        ctx.progress.connect(self._progress)
        self._last_progress = 0.0
        self.refresh()

    def _progress(self, p) -> None:
        import time
        if time.time() - self._last_progress > 1.0 or p.phase in ("done", "cancelled"):
            self._last_progress = time.time()
            self.refresh()

    def refresh(self) -> None:
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        ctx, lib = self.ctx, self.ctx.lib
        st = lib.stats()
        if st["books"] == 0:
            self.empty.setText("Your library is empty. Open Import and Indexing to add a folder of books, "
                               "or use Settings to run the setup again.")
            self.empty.show()
        else:
            self.empty.hide()
        cards = []
        for key in ctx.settings.home_sections:
            if key not in HOME_SECTIONS:
                continue
            w = self._section(key, st)
            if w is not None:
                cards.append(w)
        for i, w in enumerate(cards):
            self.grid.addWidget(w, i // 2, i % 2)
        self.grid.setRowStretch(len(cards) // 2 + 1, 1)
        self.grid.setColumnStretch(0, 1)
        self.grid.setColumnStretch(1, 1)

    def _section(self, key: str, st: dict):
        ctx, lib = self.ctx, self.ctx.lib
        f, lay = card(TITLES[key])
        from ...library import BookFilter
        if key == "continue_reading":
            rows = ctx.reading.continue_reading(5)
            if not rows:
                lay.addWidget(label("Nothing in progress yet.", "muted"))
            else:
                lay.addWidget(BookList(ctx, self.covers, rows, lambda r: f"{r['percent']:.0f}% read"))
        elif key == "recently_added":
            rows = lib.query_books(BookFilter(), "added", True, 5)
            lay.addWidget(BookList(ctx, self.covers, rows, lambda r: r["authors"])) if rows else lay.addWidget(label("No books yet.", "muted"))
        elif key == "recently_read":
            rows = lib.query_books(BookFilter(), "last_read", True, 20)
            rows = [r for r in rows if r["last_read"]][:5]
            lay.addWidget(BookList(ctx, self.covers, rows, lambda r: (r["last_read"] or "")[:16])) if rows else lay.addWidget(label("Nothing read yet.", "muted"))
        elif key == "favorites":
            rows = lib.query_books(BookFilter(favorite=True), "title", False, 5)
            lay.addWidget(BookList(ctx, self.covers, rows, lambda r: r["authors"])) if rows else lay.addWidget(label("Mark books as favourites from the Library.", "muted", True))
        elif key == "recently_annotated":
            rows = ctx.reading.recent(5)
            if not rows:
                lay.addWidget(label("No highlights or notes yet.", "muted"))
            for r in rows:
                txt = (r["note"] or r["selected_text"] or r["type"]).strip().replace("\n", " ")[:90]
                lb = label(f"<b>{_esc(r['title'])}</b><br>{r['type']}: {_esc(txt)}", wrap=True)
                lb.setTextFormat(Qt.RichText)
                lay.addWidget(lb)
        elif key == "popular_tags":
            tags = ctx.tags.popular(12)
            if not tags:
                lay.addWidget(label("Tag your books to see them here.", "muted"))
            else:
                lb = label("  ·  ".join(f"{_esc(t['name'])} ({t['n']})" for t in tags), wrap=True)
                lay.addWidget(lb)
        elif key == "recent_notes":
            rows = ctx.research.recent_topics(5)
            if not rows:
                lay.addWidget(label("No research topics yet. Create one in Notes.", "muted", True))
            for r in rows:
                lay.addWidget(label(f"{r['title']}  —  {r['modified'][:10]}"))
        elif key == "statistics":
            rs = ctx.reading.stats()
            lines = [f"{st['books']:,} books · {human_size(st['bytes'])}", f"{st['authors']:,} authors · {st['tags']:,} tags",
                     f"{st['finished']:,} finished · {rs['in_progress']:,} in progress",
                     f"{st['annotations']:,} annotations · {st['clippings']:,} clippings · {st['topics']:,} topics"]
            if ctx.settings.reading_stats:
                lines.append(f"Reading time: {human_duration(rs['seconds_30d'])} in the last 30 days, {human_duration(rs['seconds_total'])} total")
            for ln in lines:
                lay.addWidget(label(ln))
        elif key == "indexing_status":
            p = ctx.last_progress
            if ctx.importing and p:
                lay.addWidget(label(f"{'Scanning' if p.phase == 'discovering' else 'Indexing'}: {p.processed:,} done, {p.remaining:,} remaining"))
                bar = QProgressBar(); total = p.processed + p.remaining
                bar.setRange(0, max(total, 1)); bar.setValue(p.processed)
                lay.addWidget(bar)
            else:
                lay.addWidget(label(f"{st['indexed']:,} indexed · {st['pending']:,} waiting · {st['failed']:,} need attention"))
                lay.addWidget(label(f"{st['segments']:,} searchable text passages", "muted"))
        elif key == "offline_roots":
            off = [r for r in lib.roots() if r["enabled"] and not r["online"]]
            if not off:
                lay.addWidget(label("All library sources are available.", "muted"))
            for r in off:
                lb = label(f"⚠ {_esc(r['label'])} — {_esc(r['path'])}<br>Reconnect the drive; your notes and reading progress stay available.", wrap=True)
                lb.setTextFormat(Qt.RichText)
                lay.addWidget(lb)
        else:
            return None
        return f


def _esc(s: str) -> str:
    import html
    return html.escape(s)
