"""Library page: filters, browser, details/metadata editor, TOC."""
from __future__ import annotations

import json

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QListWidgetItem, QPushButton, QScrollArea, QSpinBox, QSplitter, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget, QToolButton, QButtonGroup)

from ...library import BookFilter, READING_STATUSES
from ...metadata_lookup import PROVIDERS
from ..book_browser import BookBrowser, CollectionList
from ..common import STATUS_TEXT, button, confirm, human_size, info, label, warn
from ..context import AppContext

SORTS = [("Title", "title"), ("Author", "author"), ("Date added", "added"), ("Publication year", "year"),
         ("Format", "format"), ("File size", "size"), ("Rating", "rating"), ("Last read", "last_read")]


class FilterPanel(QFrame):
    def __init__(self, ctx: AppContext, on_change) -> None:
        super().__init__()
        self.ctx, self.on_change = ctx, on_change
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.addWidget(label("Filters", "h2"))
        self.form = QFormLayout()
        self.form.setLabelAlignment(Qt.AlignLeft)
        lay.addLayout(self.form)
        self.text = QLineEdit(); self.text.setPlaceholderText("Title, author or filename"); self.text.setClearButtonEnabled(True)
        self.author = QComboBox(); self.tag = QComboBox(); self.genre = QComboBox(); self.fmt = QComboBox()
        self.lang = QComboBox(); self.status = QComboBox(); self.coll = QComboBox(); self.avail = QComboBox()
        self.y1 = QSpinBox(); self.y2 = QSpinBox()
        for s in (self.y1, self.y2):
            s.setRange(0, 2100); s.setSpecialValueText("any")
        self.rating = QComboBox()
        self.fav = QCheckBox("Favourites only")
        for lbl, w in (("Search", self.text), ("Author", self.author), ("Tag", self.tag), ("Genre", self.genre), ("Format", self.fmt),
                       ("Language", self.lang), ("Year from", self.y1), ("Year to", self.y2), ("Min. rating", self.rating),
                       ("Reading", self.status), ("Collection", self.coll), ("Availability", self.avail)):
            self.form.addRow(lbl, w)
            w.setAccessibleName(lbl)
        lay.addWidget(self.fav)
        lay.addWidget(button("Clear filters", self.clear))
        lay.addWidget(label("Collections — drag books here", "muted"))
        self.drop = CollectionList(); self.drop.setMaximumHeight(130); self.drop.setAccessibleName("Collections (drop books here)")
        self.drop.booksDropped.connect(self._dropped)
        self.drop.itemClicked.connect(lambda it: self.coll.setCurrentIndex(max(0, self.coll.findData(it.data(Qt.UserRole)))))
        lay.addWidget(self.drop)
        lay.addStretch(1)
        self._timer = QTimer(singleShot=True, interval=250)
        self._timer.timeout.connect(self.on_change)
        self.text.textChanged.connect(lambda *_: self._timer.start())
        for c in (self.author, self.tag, self.genre, self.fmt, self.lang, self.status, self.coll, self.avail, self.rating):
            c.currentIndexChanged.connect(lambda *_: self.on_change())
        for s in (self.y1, self.y2):
            s.valueChanged.connect(lambda *_: self._timer.start())
        self.fav.toggled.connect(lambda *_: self.on_change())
        self.populate()
        ctx.library_changed.connect(self.populate)
        ctx.tags_changed.connect(self.populate)

    def _dropped(self, cid: int, ids: list) -> None:
        self.ctx.lib.add_to_collection(cid, ids)
        self.ctx.message.emit(f"Added {len(ids)} book(s) to the collection.")
        self.populate()

    def populate(self) -> None:
        keep = self.to_filter()
        self._block(True)
        lib = self.ctx.lib

        def fill(cb: QComboBox, items, any_text="Any"):
            cur = cb.currentData()
            cb.clear(); cb.addItem(any_text, None)
            for label_, val in items:
                cb.addItem(label_, val)
            i = cb.findData(cur)
            cb.setCurrentIndex(max(i, 0))
        fill(self.author, [(a, a) for a in lib.distinct_values("author")])
        fill(self.genre, [(a, a) for a in lib.distinct_values("genre")])
        fill(self.fmt, [(a.upper(), a) for a in lib.distinct_values("format")])
        fill(self.lang, [(a, a) for a in lib.distinct_values("language")])
        fill(self.tag, [(self.ctx.tags.path(t["id"]), t["id"]) for t in sorted(self.ctx.tags.all(), key=lambda t: self.ctx.tags.path(t["id"]).lower())])
        fill(self.coll, [(c["name"], c["id"]) for c in lib.collections()])
        self.drop.clear()
        for c in lib.collections():
            it = QListWidgetItem(f"{c['name']}  ({c['n']})"); it.setData(Qt.UserRole, c["id"]); self.drop.addItem(it)
        fill(self.status, [(s.capitalize(), s) for s in READING_STATUSES])
        fill(self.avail, [("Available", True), ("Offline", False)])
        fill(self.rating, [("★" * i + " and up", i) for i in range(1, 6)])
        self._block(False)

    def _block(self, b: bool) -> None:
        for c in (self.author, self.tag, self.genre, self.fmt, self.lang, self.status, self.coll, self.avail, self.rating):
            c.blockSignals(b)

    def to_filter(self) -> BookFilter:
        return BookFilter(
            text=self.text.text().strip(), author=self.author.currentData() or "", tag_id=self.tag.currentData(),
            genre=self.genre.currentData() or "", format=self.fmt.currentData() or "", language=self.lang.currentData() or "",
            year_from=self.y1.value() or None, year_to=self.y2.value() or None, rating_min=self.rating.currentData() or 0,
            status=self.status.currentData() or "", collection_id=self.coll.currentData(), favorite=self.fav.isChecked(),
            available=self.avail.currentData())

    def clear(self) -> None:
        self._block(True)
        self.text.clear(); self.y1.setValue(0); self.y2.setValue(0); self.fav.setChecked(False)
        for c in (self.author, self.tag, self.genre, self.fmt, self.lang, self.status, self.coll, self.avail, self.rating):
            c.setCurrentIndex(0)
        self._block(False)
        self.on_change()


class DetailsPanel(QScrollArea):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.book_id: int | None = None
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMinimumWidth(300)
        w = QWidget()
        self.setWidget(w)
        lay = QVBoxLayout(w)
        self.cover = QLabel(); self.cover.setAlignment(Qt.AlignCenter)
        self.head = label("Select a book", "h2", True)
        self.state = label("", "muted", True)
        lay.addWidget(self.cover); lay.addWidget(self.head); lay.addWidget(self.state)
        self.form = QFormLayout()
        lay.addLayout(self.form)
        mk = lambda: QLineEdit()
        self.title, self.authors, self.publisher, self.year, self.isbn, self.language = mk(), mk(), mk(), mk(), mk(), mk()
        self.series, self.series_idx, self.genres, self.subjects = mk(), mk(), mk(), mk()
        self.authors.setPlaceholderText("Separate several authors with ;")
        self.genres.setPlaceholderText("; separated"); self.subjects.setPlaceholderText("; separated")
        self.status = QComboBox(); [self.status.addItem(s.capitalize(), s) for s in READING_STATUSES]
        self.rating = QComboBox(); [self.rating.addItem("★" * i if i else "No rating", i) for i in range(6)]
        self.desc = QPlainTextEdit(); self.desc.setMaximumHeight(90)
        self.notes = QPlainTextEdit(); self.notes.setMaximumHeight(90)
        self.tags = label("", wrap=True)
        for lbl, wd in (("Title", self.title), ("Authors", self.authors), ("Publisher", self.publisher), ("Year", self.year), ("ISBN", self.isbn),
                        ("Language", self.language), ("Series", self.series), ("Series #", self.series_idx), ("Genres", self.genres),
                        ("Subjects", self.subjects), ("Status", self.status), ("Rating", self.rating), ("Description", self.desc),
                        ("My notes", self.notes), ("Tags", self.tags)):
            self.form.addRow(lbl, wd)
            wd.setAccessibleName(lbl)
        row = QHBoxLayout()
        self.save_btn = button("Save changes", self.save, primary=True)
        self.lookup_btn = button("Look up online…", self.lookup, tip="Optional. Sends only the ISBN or title/author you see here to Open Library.")
        row.addWidget(self.save_btn); row.addWidget(self.lookup_btn)
        lay.addLayout(row)
        self.facts = label("", "muted", True)
        self.facts.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.facts)
        lay.addWidget(label("Table of contents", "h2"))
        self.toc = QTreeWidget(); self.toc.setHeaderHidden(True); self.toc.setMaximumHeight(200)
        self.toc.itemActivated.connect(self._toc_open); self.toc.itemDoubleClicked.connect(self._toc_open)
        lay.addWidget(self.toc)
        lay.addStretch(1)
        self.set_book(None)

    def set_book(self, book_id: int | None) -> None:
        self.book_id = book_id
        b = self.ctx.lib.get_book(book_id) if book_id else None
        self.widget().setEnabled(b is not None)
        if not b:
            self.head.setText("Select a book to see its details")
            self.cover.clear(); self.state.clear(); self.facts.clear(); self.toc.clear()
            return
        from ..common import CoverCache
        self.cover.setPixmap(CoverCache().get(b["cover_path"], b["title"], "; ".join(b["authors"]), (140, 210)))
        self.head.setText(b["title"])
        avail = "Source file available" if b["available"] else "Source file OFFLINE — metadata, notes and progress are still available"
        self.state.setText(avail + " · " + STATUS_TEXT.get(b["index_status"], b["index_status"]) + (f" — {b['index_detail']}" if b["index_detail"] else ""))
        self.title.setText(b["title"]); self.authors.setText("; ".join(b["authors"])); self.publisher.setText(b["publisher"])
        self.year.setText(str(b["year"] or "")); self.isbn.setText(b["isbn"]); self.language.setText(b["language"])
        self.series.setText(b["series"]); self.series_idx.setText("" if b["series_index"] is None else f"{b['series_index']:g}")
        self.genres.setText("; ".join(b["genres"])); self.subjects.setText("; ".join(b["subjects"]))
        self.status.setCurrentIndex(max(0, self.status.findData(b["reading_status"])))
        self.rating.setCurrentIndex(b["rating"])
        self.desc.setPlainText(b["description"]); self.notes.setPlainText(b["personal_notes"])
        self.tags.setText(", ".join(t["name"] for t in b["tags"]) or "—")
        self.facts.setText(f"{b['format'].upper()} · {human_size(b['size'])}" + (f" · {b['page_count']} pages" if b["page_count"] else "") +
                           f"\nFile: {b['original_filename']}\nSource: {b['root_label']}\nPath: {b['path']}\nAdded {b['import_date'][:10]}"
                           + (f" · indexed {b['last_indexed'][:10]}" if b["last_indexed"] else ""))
        self.toc.clear()
        try:
            for title, num, level in json.loads(b["toc_json"] or "[]"):
                it = QTreeWidgetItem([title]); it.setData(0, Qt.UserRole, (b["format"], num))
                self.toc.addTopLevelItem(it)
        except ValueError:
            pass
        self.lookup_btn.setVisible(self.ctx.settings.online_metadata_enabled)

    def _split(self, s: str) -> list[str]:
        return [x.strip() for x in s.split(";") if x.strip()]

    def save(self) -> None:
        if not self.book_id:
            return
        y = self.year.text().strip()
        si = self.series_idx.text().strip()
        try:
            self.ctx.lib.update_book(
                self.book_id, authors=self._split(self.authors.text()), genres=self._split(self.genres.text()),
                subjects=self._split(self.subjects.text()), title=self.title.text().strip() or "Untitled",
                publisher=self.publisher.text().strip(), year=int(y) if y else None, isbn=self.isbn.text().strip(),
                language=self.language.text().strip().lower(), series=self.series.text().strip(),
                series_index=float(si) if si else None, description=self.desc.toPlainText(),
                reading_status=self.status.currentData(), rating=self.rating.currentData(),
                personal_notes=self.notes.toPlainText())
        except ValueError:
            warn(self, "Invalid value", "Year must be a whole number and series number must be numeric.")
            return
        self.ctx.library_changed.emit()
        self.ctx.message.emit("Metadata saved.")

    def _toc_open(self, item) -> None:
        if self.book_id:
            fmt, num = item.data(0, Qt.UserRole)
            self.ctx.open_book(self.book_id, {"type": "page" if fmt == "pdf" else "chapter", "num": num})

    def lookup(self) -> None:
        if not self.book_id:
            return
        prov = next(iter(PROVIDERS.values()))
        isbn, title = self.isbn.text().strip(), self.title.text().strip()
        if not confirm(self, "Online lookup", f"This sends only '{isbn or title}' to {prov.name}. No book content is sent. Continue?", "Look up"):
            return

        def done(res):
            if not res:
                info(self, "No results", "Nothing was found. Metadata was not changed.")
                return
            r = res[0]
            if confirm(self, "Apply result?", f"Title: {r.title}\nAuthors: {', '.join(r.authors)}\nPublisher: {r.publisher}\nYear: {r.year or ''}\n\n"
                       "Apply to the empty fields only?", "Apply"):
                if not self.publisher.text() and r.publisher: self.publisher.setText(r.publisher)
                if not self.year.text() and r.year: self.year.setText(str(r.year))
                if not self.authors.text() and r.authors: self.authors.setText("; ".join(r.authors))
                if not self.isbn.text() and r.isbn: self.isbn.setText(r.isbn)
        self.ctx.run_async(lambda: prov.lookup(isbn=isbn, title=title), done, lambda e: warn(self, "Lookup failed", f"Could not reach the provider: {e}"))


class LibraryPage(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.setObjectName("page")
        self.ctx = ctx
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 8)
        top = QHBoxLayout()
        top.addWidget(label("Library", "h1"))
        top.addStretch(1)
        self.count = label("", "muted")
        top.addWidget(self.count)
        self.sort = QComboBox(); [self.sort.addItem(a, b) for a, b in SORTS]
        self.sort.setAccessibleName("Sort by")
        self.desc = QToolButton(); self.desc.setText("↓"); self.desc.setCheckable(True); self.desc.setToolTip("Descending order")
        top.addWidget(label("Sort:")); top.addWidget(self.sort); top.addWidget(self.desc)
        self.group = QButtonGroup(self)
        for i, (txt, mode, tip) in enumerate((("☰", "list", "Detailed list with covers"), ("≡", "compact", "Compact list"), ("▦", "grid", "Cover grid"))):
            b = QToolButton(); b.setText(txt); b.setCheckable(True); b.setToolTip(tip); b.setAccessibleName(tip)
            b.setChecked(mode == ctx.settings.library_view)
            b.clicked.connect(lambda _=False, m=mode: self.set_mode(m))
            self.group.addButton(b); top.addWidget(b)
        self.filter_btn = QToolButton(); self.filter_btn.setText("Filters"); self.filter_btn.setCheckable(True); self.filter_btn.setChecked(True)
        self.detail_btn = QToolButton(); self.detail_btn.setText("Details"); self.detail_btn.setCheckable(True); self.detail_btn.setChecked(True)
        top.addWidget(self.filter_btn); top.addWidget(self.detail_btn)
        lay.addLayout(top)
        self.filters = FilterPanel(ctx, self.apply)
        self.browser = BookBrowser(ctx)
        self.details = DetailsPanel(ctx)
        sp = QSplitter()
        sp.addWidget(self.filters); sp.addWidget(self.browser); sp.addWidget(self.details)
        sp.setStretchFactor(1, 1); sp.setSizes([230, 760, 320])
        lay.addWidget(sp, 1)
        self.filter_btn.toggled.connect(self.filters.setVisible); self.detail_btn.toggled.connect(self.details.setVisible)
        self.sort.currentIndexChanged.connect(lambda *_: self.apply())
        self.desc.toggled.connect(lambda *_: self.apply())
        self.browser.selection_changed.connect(lambda ids: self.details.set_book(ids[0] if len(ids) == 1 else None))
        self.browser.activated.connect(ctx.open_book)
        ctx.library_changed.connect(self._after_reload)
        self.apply()

    def set_mode(self, mode: str) -> None:
        self.ctx.settings.library_view = mode
        self.ctx.settings.save()
        self.browser.set_mode(mode)
        self._after_reload()

    def apply(self) -> None:
        self.browser.set_filter(self.filters.to_filter(), self.sort.currentData(), self.desc.isChecked())
        self._after_reload()

    def _after_reload(self) -> None:
        self.count.setText(f"{self.browser.total:,} books")
        ids = self.browser.selected_ids()
        if len(ids) == 1 and self.details.book_id == ids[0]:
            self.details.set_book(ids[0])

    def show_filter(self, flt: BookFilter) -> None:
        self.browser.set_filter(flt)
        self._after_reload()
