"""Collections (virtual shelves), duplicates inspector, metadata review."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QInputDialog, QListWidget, QListWidgetItem, QSplitter, QTabWidget,
                               QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QHeaderView)

from ...library import BookFilter
from ..book_browser import BookBrowser, CollectionList
from ..common import button, confirm, hbox, human_size, info, label, warn
from ..context import AppContext


class CollectionsPage(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.setObjectName("page")
        self.ctx = ctx
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 10)
        lay.addWidget(label("Collections", "h1"))
        lay.addWidget(label("Collections group books without touching the folders on disk. A book can be in several collections.", "muted"))
        tabs = QTabWidget()
        lay.addWidget(tabs, 1)

        w = QWidget(); wl = QVBoxLayout(w)
        sp = QSplitter()
        left = QWidget(); ll = QVBoxLayout(left); ll.setContentsMargins(0, 0, 0, 0)
        self.list = CollectionList(); self.list.setAccessibleName("Collections (drop books here)")
        self.list.currentItemChanged.connect(self.on_select)
        self.list.booksDropped.connect(self.on_drop)
        ll.addWidget(self.list)
        ll.addLayout(hbox(button("New", self.new, True), button("Rename", self.rename), button("Delete", self.delete, danger=True)))
        sp.addWidget(left)
        right = QWidget(); rl = QVBoxLayout(right); rl.setContentsMargins(0, 0, 0, 0)
        self.browser = BookBrowser(ctx)
        self.browser.activated.connect(ctx.open_book)
        rl.addWidget(self.browser)
        rl.addLayout(hbox(button("Remove selected from collection", self.remove_books), None))
        sp.addWidget(right); sp.setSizes([260, 800])
        wl.addWidget(sp)
        tabs.addTab(w, "Collections")

        d = QWidget(); dl = QVBoxLayout(d)
        dl.addWidget(label("Books with identical content, or the same title and author. Nothing is ever deleted automatically — "
                           "use this list to decide what you want to do in Windows Explorer.", "muted", True))
        self.dups = QTreeWidget(); self.dups.setHeaderLabels(["Book", "Format", "Size", "Path"])
        self.dups.header().setSectionResizeMode(3, QHeaderView.Stretch)
        dl.addWidget(self.dups)
        dl.addLayout(hbox(button("Find duplicates", self.find_dups, True), button("Show selected in Explorer", self.dup_explorer), None))
        tabs.addTab(d, "Duplicates")

        r = QWidget(); rl2 = QVBoxLayout(r)
        rl2.addWidget(label("Records with missing or suspicious metadata. Edit them in the Library details panel. "
                            "Atlas never fills in authors, years or ISBNs on its own.", "muted", True))
        self.review = QTableWidget(0, 3)
        self.review.setHorizontalHeaderLabels(["Title", "Author", "Issues"])
        self.review.setEditTriggers(QAbstractItemView.NoEditTriggers); self.review.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.review.verticalHeader().hide(); self.review.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.review.cellDoubleClicked.connect(lambda row, _c: ctx.goto_page_requested.emit(f"library:{self.review.item(row, 0).data(Qt.UserRole)}"))
        rl2.addWidget(self.review)
        from ...filename_meta import PATTERNS
        self.pattern = QComboBox(); [self.pattern.addItem(p) for p in PATTERNS]
        self.pattern.setToolTip("How the file names of the selected books are structured")
        rl2.addLayout(hbox(button("Refresh review list", self.load_review, True), label("File name looks like:"), self.pattern,
                           button("Fill from file names (preview)…", self.fill_from_names,
                                  tip="Shows what would be filled in; only empty fields are changed, and only after you confirm"), None))
        tabs.addTab(r, "Metadata review")

        ctx.library_changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        cur = self.current_id()
        self.list.blockSignals(True)
        self.list.clear()
        for c in self.ctx.lib.collections():
            it = QListWidgetItem(f"{c['name']}  ({c['n']})"); it.setData(Qt.UserRole, c["id"]); self.list.addItem(it)
            if c["id"] == cur:
                self.list.setCurrentItem(it)
        self.list.blockSignals(False)
        self.on_select()

    def on_drop(self, cid: int, ids: list) -> None:
        self.ctx.lib.add_to_collection(cid, ids)
        self.ctx.message.emit(f"Added {len(ids)} book(s) to the collection.")
        self.ctx.library_changed.emit()

    def current_id(self) -> int | None:
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it else None

    def on_select(self, *_a) -> None:
        cid = self.current_id()
        self.browser.set_filter(BookFilter(collection_id=cid) if cid else BookFilter(book_ids=[]))

    def new(self) -> None:
        name, ok = QInputDialog.getText(self, "New collection", "Collection name:")
        if ok:
            try:
                self.ctx.lib.create_collection(name)
            except ValueError as exc:
                warn(self, "Collection", str(exc)); return
            self.refresh()

    def rename(self) -> None:
        cid = self.current_id()
        if not cid:
            return
        name, ok = QInputDialog.getText(self, "Rename collection", "New name:")
        if ok:
            try:
                self.ctx.lib.rename_collection(cid, name)
            except ValueError as exc:
                warn(self, "Collection", str(exc))
            self.refresh()

    def delete(self) -> None:
        cid = self.current_id()
        if cid and confirm(self, "Delete collection", "Delete this collection? The books themselves are not affected.", "Delete"):
            self.ctx.lib.delete_collection(cid); self.refresh()

    def remove_books(self) -> None:
        cid, ids = self.current_id(), self.browser.selected_ids()
        if cid and ids:
            self.ctx.lib.remove_from_collection(cid, ids); self.refresh()

    # --------------------------------------------------------------- duplicates
    def find_dups(self) -> None:
        def done(groups):
            self.dups.clear()
            for g in groups:
                top = QTreeWidgetItem([("Identical content" if g["kind"] == "identical" else "Same title and author — different files") + f" ({len(g['book_ids'])})"])
                self.dups.addTopLevelItem(top)
                for bid in g["book_ids"]:
                    b = self.ctx.lib.get_book(bid)
                    it = QTreeWidgetItem([b["title"], b["format"].upper(), human_size(b["size"]), b["path"]])
                    it.setData(0, Qt.UserRole, b["path"]); top.addChild(it)
                top.setExpanded(True)
            if not groups:
                info(self, "Duplicates", "No duplicates found.")
        self.ctx.run_async(self.ctx.lib.duplicate_groups, done)

    def dup_explorer(self) -> None:
        from ... import storage
        it = self.dups.currentItem()
        if it and it.data(0, Qt.UserRole):
            storage.open_in_explorer(it.data(0, Qt.UserRole))

    # ------------------------------------------------------------------ review
    def fill_from_names(self) -> None:
        from ...filename_meta import apply_suggestion, suggest
        rows = self.review.selectionModel().selectedRows() or [self.review.model().index(i, 0) for i in range(self.review.rowCount())]
        ids = [self.review.item(r.row(), 0).data(Qt.UserRole) for r in rows]
        pattern = self.pattern.currentText()
        preview = []
        for bid in ids:
            b = self.ctx.lib.get_book(bid)
            s = suggest(b["original_filename"], pattern) if b else None
            if not s:
                continue
            parts = []
            if s.title: parts.append(f"title: {s.title}")
            if s.author and not b["authors"]: parts.append(f"author: {s.author}")
            if s.year and not b["year"]: parts.append(f"year: {s.year}")
            if parts:
                preview.append(f"{b['original_filename']}  →  " + ", ".join(parts))
        if not preview:
            info(self, "Fill from file names", f"No file name matched the pattern '{pattern}', or there was nothing to fill in.")
            return
        shown = "\n".join(preview[:15]) + (f"\n… and {len(preview) - 15} more" if len(preview) > 15 else "")
        if not confirm(self, "Fill from file names", f"{len(preview)} book(s) would be updated (empty fields only):\n\n{shown}", "Apply"):
            return
        n = sum(1 for bid in ids if apply_suggestion(self.ctx.lib, bid, pattern))
        self.ctx.library_changed.emit()
        self.load_review()
        info(self, "Done", f"Updated {n} book(s).")

    def load_review(self) -> None:
        def done(rows):
            self.review.setRowCount(len(rows))
            for i, r in enumerate(rows):
                for c, v in enumerate([r["title"], r["authors"], ", ".join(r["issues"])]):
                    it = QTableWidgetItem(v)
                    if c == 0:
                        it.setData(Qt.UserRole, r["id"])
                    self.review.setItem(i, c, it)
        self.ctx.run_async(self.ctx.lib.review_candidates, done)
