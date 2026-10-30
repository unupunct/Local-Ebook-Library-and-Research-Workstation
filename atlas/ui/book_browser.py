"""Reusable book browser: lazy model, list / compact / grid views, context menu, bulk actions."""
from __future__ import annotations

import os
from typing import Sequence

import json

from PySide6.QtCore import QAbstractTableModel, QItemSelectionModel, QMimeData, QModelIndex, QSize, Qt, Signal
from PySide6.QtGui import QAction, QColor, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QListWidget, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHeaderView,
                               QLabel, QLineEdit, QListView, QMenu, QSpinBox, QStackedWidget, QTableView, QVBoxLayout,
                               QWidget, QCheckBox)

from .. import storage
from ..library import BookFilter, READING_STATUSES
from .common import BADGES, CoverCache, STATUS_TEXT, badge_for, human_size, confirm, warn
from .context import AppContext

PAGE = 300
BOOKS_MIME = "application/x-atlas-books"
COLUMNS = [("Title", "title"), ("Author", "author"), ("Year", "year"), ("Format", "format"), ("Size", "size"),
           ("Rating", "rating"), ("Added", "added"), ("Last read", "last_read"), ("Status", None)]


class BookModel(QAbstractTableModel):
    def __init__(self, ctx: AppContext, covers: CoverCache) -> None:
        super().__init__()
        self.ctx, self.covers = ctx, covers
        self.flt = BookFilter()
        self.sort, self.desc = "title", False
        self.rows: list = []
        self.total = 0
        self.show_covers = True

    def reload(self) -> None:
        self.beginResetModel()
        self.rows = self.ctx.lib.query_books(self.flt, self.sort, self.desc, PAGE, 0)
        self.total = self.ctx.lib.count_books(self.flt)
        self.endResetModel()

    def canFetchMore(self, parent=QModelIndex()) -> bool:
        return not parent.isValid() and len(self.rows) < self.total

    def fetchMore(self, parent=QModelIndex()) -> None:
        more = self.ctx.lib.query_books(self.flt, self.sort, self.desc, PAGE, len(self.rows))
        if more:
            self.beginInsertRows(QModelIndex(), len(self.rows), len(self.rows) + len(more) - 1)
            self.rows.extend(more)
            self.endInsertRows()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def flags(self, index):
        return super().flags(index) | Qt.ItemIsDragEnabled

    def mimeTypes(self):
        return [BOOKS_MIME]

    def mimeData(self, indexes):
        ids = sorted({self.rows[i.row()]["id"] for i in indexes})
        md = QMimeData()
        md.setData(BOOKS_MIME, json.dumps(ids).encode())
        return md

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLUMNS[section][0]
        return None

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        r = self.rows[index.row()]
        col = index.column()
        if role == Qt.DisplayRole:
            if col == 0:
                return r["title"]
            if col == 1:
                return r["authors"]
            if col == 2:
                return str(r["year"]) if r["year"] else ""
            if col == 3:
                return r["format"].upper()
            if col == 4:
                return human_size(r["size"])
            if col == 5:
                return "★" * r["rating"] if r["rating"] else ""
            if col == 6:
                return (r["import_date"] or "")[:10]
            if col == 7:
                return (r["last_read"] or "")[:10]
            if col == 8:
                return ("" if r["available"] else "OFFLINE · ") + STATUS_TEXT.get(r["index_status"], r["index_status"])
        elif role == Qt.DecorationRole and col == 0 and self.show_covers:
            return self.covers.get(r["cover_path"], r["title"], r["authors"], (36, 54), badge_for(r["available"], r["index_status"]))
        elif role == Qt.ForegroundRole and not r["available"]:
            return QColor("#8b93a1")
        elif role == Qt.TextAlignmentRole and col in (2, 4):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        elif role == Qt.ToolTipRole:
            tip = f"{r['title']}\n{r['authors']}\n{r['rel_path']}"
            b = badge_for(r["available"], r["index_status"])
            return tip + (f"\n● {BADGES[b][1]}: {STATUS_TEXT.get(r['index_status'], r['index_status'])}" if b else "")
        elif role == Qt.UserRole:
            return r["id"]
        elif role == Qt.AccessibleTextRole:
            return f"{r['title']} by {r['authors'] or 'unknown author'}"
        return None


class GridModel(BookModel):
    """Single-column variant used by the cover grid (large covers)."""

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else 1

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if role == Qt.DecorationRole:
            r = self.rows[index.row()]
            return self.covers.get(r["cover_path"], r["title"], r["authors"], (120, 180), badge_for(r["available"], r["index_status"]))
        return super().data(index, role)


class CollectionList(QListWidget):
    """List of collections that accepts books dragged from a book browser."""
    booksDropped = Signal(int, list)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DropOnly)
        self.setDropIndicatorShown(True)

    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasFormat(BOOKS_MIME):
            e.acceptProposedAction()

    def dragMoveEvent(self, e) -> None:
        it = self.itemAt(e.position().toPoint())
        if e.mimeData().hasFormat(BOOKS_MIME) and it is not None:
            self.setCurrentItem(it) if False else None
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e) -> None:
        it = self.itemAt(e.position().toPoint())
        if it is None or not e.mimeData().hasFormat(BOOKS_MIME):
            e.ignore()
            return
        ids = json.loads(bytes(e.mimeData().data(BOOKS_MIME)).decode())
        e.acceptProposedAction()
        self.booksDropped.emit(it.data(Qt.UserRole), ids)


class BulkEditDialog(QDialog):
    def __init__(self, ctx: AppContext, n: int, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle(f"Edit {n} books")
        lay = QFormLayout(self)
        self.lang = QLineEdit(); self.lang.setPlaceholderText("leave empty to keep")
        self.publisher = QLineEdit(); self.publisher.setPlaceholderText("leave empty to keep")
        self.year = QLineEdit(); self.year.setPlaceholderText("leave empty to keep")
        self.genre = QLineEdit(); self.genre.setPlaceholderText("add genre(s), separated by ;")
        self.status = QComboBox(); self.status.addItem("(keep)", ""); [self.status.addItem(s.capitalize(), s) for s in READING_STATUSES]
        self.rating = QComboBox(); self.rating.addItem("(keep)", -1); [self.rating.addItem("★" * i if i else "No rating", i) for i in range(6)]
        self.tags = QComboBox(); self.tags.addItem("(none)", 0)
        for t in ctx.tags.all():
            self.tags.addItem(ctx.tags.path(t["id"]), t["id"])
        self.coll = QComboBox(); self.coll.addItem("(none)", 0)
        for c in ctx.lib.collections():
            self.coll.addItem(c["name"], c["id"])
        for label_, w in (("Language code", self.lang), ("Publisher", self.publisher), ("Year", self.year), ("Add genre", self.genre),
                          ("Reading status", self.status), ("Rating", self.rating), ("Add tag", self.tags), ("Add to collection", self.coll)):
            lay.addRow(label_, w)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addRow(bb)

    def apply(self, ids: Sequence[int]) -> None:
        f: dict = {}
        if self.lang.text().strip():
            f["language"] = self.lang.text().strip().lower()
        if self.publisher.text().strip():
            f["publisher"] = self.publisher.text().strip()
        if self.year.text().strip().isdigit():
            f["year"] = int(self.year.text())
        if self.status.currentData():
            f["reading_status"] = self.status.currentData()
        if self.rating.currentData() >= 0:
            f["rating"] = self.rating.currentData()
        genres = [g.strip() for g in self.genre.text().split(";") if g.strip()]
        self.ctx.lib.bulk_update(ids, add_tags=[self.tags.currentData()] if self.tags.currentData() else [],
                                 add_to_collection=self.coll.currentData() or None, add_genres=genres, **f)


class BookBrowser(QWidget):
    selection_changed = Signal(list)
    activated = Signal(int)

    def __init__(self, ctx: AppContext, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.covers = CoverCache((120, 180))
        self.model = BookModel(ctx, self.covers)
        self.gmodel = GridModel(ctx, self.covers)
        self.mode = ctx.settings.library_view
        self.stack = QStackedWidget()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setSortingEnabled(False)
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().sectionClicked.connect(self._header_clicked)
        self.table.setAccessibleName("Book list")
        self.grid = QListView()
        self.grid.setModel(self.gmodel)
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setUniformItemSizes(True)
        self.grid.setMovement(QListView.Static)
        self.grid.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.grid.setWordWrap(True)
        self.grid.setSpacing(10)
        self.grid.setGridSize(QSize(150, 250))
        self.grid.setIconSize(QSize(120, 180))
        self.grid.setAccessibleName("Book covers")
        self.grid.setTextElideMode(Qt.ElideRight)
        for v in (self.table, self.grid):
            v.setDragEnabled(True)
            v.setDragDropMode(QAbstractItemView.DragOnly)
        self.stack.addWidget(self.table)
        self.stack.addWidget(self.grid)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.stack)
        for v in (self.table, self.grid):
            v.setContextMenuPolicy(Qt.CustomContextMenu)
            v.customContextMenuRequested.connect(self._menu)
            v.doubleClicked.connect(lambda idx: self.activated.emit(self._active().rows[idx.row()]["id"]))
            v.selectionModel().selectionChanged.connect(lambda *_: self.selection_changed.emit(self.selected_ids()))
        for v in (self.table, self.grid):
            v.keyPressEvent = self._wrap_key(v)
        ctx.library_changed.connect(self.reload)
        self.set_mode(self.mode)

    def _wrap_key(self, view):
        orig = view.keyPressEvent

        def handler(e):
            if e.key() in (Qt.Key_Return, Qt.Key_Enter):
                ids = self.selected_ids()
                if ids:
                    self.activated.emit(ids[0])
                return
            orig(e)
        return handler

    # ----------------------------------------------------------------- state
    def set_mode(self, mode: str) -> None:
        self.mode = mode
        if mode == "grid":
            self.stack.setCurrentWidget(self.grid)
        else:
            self.model.show_covers = mode == "list"
            self.table.verticalHeader().setDefaultSectionSize(60 if mode == "list" else 24)
            self.table.setIconSize(QSize(36, 54))
            self.stack.setCurrentWidget(self.table)
        self.model.layoutChanged.emit()
        self.reload()
        if not getattr(self, "_widths_set", False):
            self._widths_set = True
            for i, w in enumerate((360, 200, 60, 70, 80, 80, 90, 90)):
                self.table.setColumnWidth(i, w)

    def _active(self) -> BookModel:
        return self.gmodel if self.mode == "grid" else self.model

    def set_filter(self, flt: BookFilter, sort: str | None = None, desc: bool | None = None) -> None:
        for m in (self.model, self.gmodel):
            m.flt = flt
            if sort:
                m.sort = sort
            if desc is not None:
                m.desc = desc
        self.reload()

    def reload(self) -> None:
        keep = self.selected_ids() if hasattr(self, "grid") else []
        self._active().reload()
        if keep:
            self.select_ids(keep)

    @property
    def total(self) -> int:
        return self._active().total

    def _header_clicked(self, section: int) -> None:
        key = COLUMNS[section][1]
        if not key:
            return
        desc = (not self.model.desc) if self.model.sort == key else key in ("added", "last_read", "rating", "size")
        self.set_filter(self.model.flt, key, desc)

    def selected_ids(self) -> list[int]:
        view = self.grid if self.mode == "grid" else self.table
        m = self._active()
        rows = sorted({i.row() for i in view.selectionModel().selectedIndexes()})
        return [m.rows[r]["id"] for r in rows if r < len(m.rows)]

    def select_ids(self, ids: Sequence[int]) -> None:
        want = set(ids)
        view = self.grid if self.mode == "grid" else self.table
        sm = view.selectionModel()
        m = self._active()
        for r, row in enumerate(m.rows):
            if row["id"] in want:
                sm.select(m.index(r, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)

    # ------------------------------------------------------------ context menu
    def _menu(self, pos) -> None:
        ids = self.selected_ids()
        if not ids:
            return
        m = QMenu(self)
        one = len(ids) == 1
        book = self.ctx.lib.get_book(ids[0]) if one else None
        a = m.addAction("Open in reader"); a.setEnabled(one and bool(book and book["available"]))
        a.triggered.connect(lambda: self.activated.emit(ids[0]))
        m.addSeparator()
        m.addAction("Show in Explorer").triggered.connect(self.show_in_explorer)
        m.addAction("Copy file path").triggered.connect(self.copy_path)
        m.addSeparator()
        m.addAction("Toggle favourite").triggered.connect(self.toggle_favorite)
        sub = m.addMenu("Add tag")
        for t in self.ctx.tags.all():
            sub.addAction(self.ctx.tags.path(t["id"])).triggered.connect(lambda _=False, tid=t["id"]: self.add_tag(tid))
        sub2 = m.addMenu("Add to collection")
        for c in self.ctx.lib.collections():
            sub2.addAction(c["name"]).triggered.connect(lambda _=False, cid=c["id"]: self.add_to_collection(cid))
        m.addAction("Edit metadata for selection…").triggered.connect(self.bulk_edit)
        m.addSeparator()
        m.addAction("Re-index").triggered.connect(self.reindex)
        m.addAction("Forget record (keeps the file)").triggered.connect(self.forget)
        m.exec(QGuiApplication.cursorPos())

    def _paths(self) -> list[str]:
        return [self.ctx.lib.book_path(i) for i in self.selected_ids()]

    def show_in_explorer(self) -> None:
        ids = self.selected_ids()
        if not ids:
            return
        p = self._paths()[0]
        if not os.path.exists(os.path.dirname(p)):
            warn(self, "Source offline", "The folder is not available. The drive or network share may be disconnected.")
            return
        storage.open_in_explorer(p)

    def copy_path(self) -> None:
        QGuiApplication.clipboard().setText("\n".join(self._paths()))
        self.ctx.message.emit("File path copied.")

    def toggle_favorite(self) -> None:
        ids = self.selected_ids()
        favs = [self.ctx.lib.get_book(i)["favorite"] for i in ids]
        self.ctx.lib.set_favorite(ids, not all(favs))
        self.ctx.library_changed.emit()

    def add_tag(self, tid: int) -> None:
        self.ctx.tags.add_to_books(tid, self.selected_ids())
        self.ctx.tags_changed.emit(); self.ctx.library_changed.emit()

    def add_to_collection(self, cid: int) -> None:
        self.ctx.lib.add_to_collection(cid, self.selected_ids())
        self.ctx.message.emit("Added to collection.")

    def bulk_edit(self) -> None:
        ids = self.selected_ids()
        if not ids:
            return
        d = BulkEditDialog(self.ctx, len(ids), self)
        if d.exec():
            d.apply(ids)
            self.ctx.library_changed.emit(); self.ctx.tags_changed.emit()

    def reindex(self) -> None:
        self.ctx.start_import("books", book_ids=self.selected_ids())

    def forget(self) -> None:
        ids = self.selected_ids()
        if confirm(self, "Forget books", f"Remove {len(ids)} book record(s) from the library? The files on disk are NOT touched. "
                   "Highlights and bookmarks for these books will be deleted; research clippings keep their text and source details.", "Forget records"):
            for i in ids:
                self.ctx.lib.delete_book_record(i)
            self.ctx.library_changed.emit()
