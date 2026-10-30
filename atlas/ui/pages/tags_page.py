"""Tags and categories: hierarchy management, auto-tag rules and suggestions."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QInputDialog, QLineEdit, QMenu, QSpinBox,
                               QSplitter, QTabWidget, QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget, QCheckBox, QHeaderView)

from ...library import BookFilter
from ...taxonomy import TagError
from ..book_browser import BookBrowser
from ..common import button, confirm, hbox, info, label, warn
from ..context import AppContext


class TagTree(QTreeWidget):
    def __init__(self, ctx: AppContext, on_drop) -> None:
        super().__init__()
        self.ctx, self.on_drop = ctx, on_drop
        self.setHeaderHidden(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setAccessibleName("Tag hierarchy")

    def dropEvent(self, e) -> None:
        item = self.currentItem()
        target = self.itemAt(e.position().toPoint())
        if item is None:
            return
        tid = item.data(0, Qt.UserRole)
        new_parent = target.data(0, Qt.UserRole) if target is not None else None
        e.ignore()
        self.on_drop(tid, new_parent)


class TagsPage(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.setObjectName("page")
        self.ctx = ctx
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 10)
        lay.addWidget(label("Tags and Categories", "h1"))
        lay.addWidget(label("Tags can be nested (drag a tag onto another to move it). Deleting a tag never deletes books.", "muted"))
        sp = QSplitter()
        left = QWidget(); ll = QVBoxLayout(left); ll.setContentsMargins(0, 0, 0, 0)
        self.tree = TagTree(ctx, self.move)
        self.tree.itemSelectionChanged.connect(self.on_select)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.menu)
        ll.addWidget(self.tree)
        ll.addLayout(hbox(button("New tag", lambda: self.new(False), True), button("New child", lambda: self.new(True)),
                          button("Rename", self.rename), button("Merge into…", self.merge), button("Delete", self.delete, danger=True)))
        sp.addWidget(left)
        tabs = QTabWidget()
        self.books = BookBrowser(ctx)
        self.books.activated.connect(ctx.open_book)
        bw = QWidget(); bl = QVBoxLayout(bw); self.sel_label = label("Select a tag to see its books (including books in child tags).", "muted")
        bl.addWidget(self.sel_label); bl.addWidget(self.books)
        bl.addLayout(hbox(button("Remove selected books from this tag", self.untag), None))
        tabs.addTab(bw, "Books")
        tabs.addTab(self._rules_tab(), "Auto-tag rules")
        sp.addWidget(tabs)
        sp.setSizes([320, 760])
        lay.addWidget(sp, 1)
        ctx.tags_changed.connect(self.refresh)
        self.refresh()

    # ------------------------------------------------------------- structure
    def refresh(self) -> None:
        sel = self.current_id()
        self.tree.blockSignals(True)
        self.tree.clear()

        def add(node, parent):
            it = QTreeWidgetItem([f"{node['name']}  ({node['count']})"])
            it.setData(0, Qt.UserRole, node["id"])
            (parent.addChild(it) if parent else self.tree.addTopLevelItem(it))
            for ch in node["children"]:
                add(ch, it)
            return it
        for n in self.ctx.tags.tree():
            add(n, None)
        self.tree.expandAll()
        if sel:
            it = self._find(sel)
            if it:
                self.tree.setCurrentItem(it)
        self.tree.blockSignals(False)
        self.refresh_rules()

    def _find(self, tid: int):
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            it = stack.pop()
            if it.data(0, Qt.UserRole) == tid:
                return it
            stack += [it.child(i) for i in range(it.childCount())]
        return None

    def current_id(self) -> int | None:
        it = self.tree.currentItem()
        return it.data(0, Qt.UserRole) if it else None

    def on_select(self) -> None:
        tid = self.current_id()
        if tid:
            self.sel_label.setText(self.ctx.tags.path(tid))
            self.books.set_filter(BookFilter(tag_id=tid))
        else:
            self.books.set_filter(BookFilter(book_ids=[]))

    def _guard(self, fn) -> None:
        try:
            fn()
        except TagError as exc:
            warn(self, "Tag", str(exc))
        self.ctx.tags_changed.emit(); self.ctx.library_changed.emit()

    def new(self, child: bool) -> None:
        parent = self.current_id() if child else None
        name, ok = QInputDialog.getText(self, "New tag", "Name (use / to create a path such as Gardening/Soil):")
        if ok and name.strip():
            def go():
                if "/" in name and not child:
                    self.ctx.tags.get_or_create_path(name)
                else:
                    self.ctx.tags.create(name, parent)
            self._guard(go)

    def rename(self) -> None:
        tid = self.current_id()
        if not tid:
            return
        cur = self.ctx.tags.path(tid).split(" / ")[-1]
        name, ok = QInputDialog.getText(self, "Rename tag", "New name:", text=cur)
        if ok:
            self._guard(lambda: self.ctx.tags.rename(tid, name))

    def merge(self) -> None:
        tid = self.current_id()
        if not tid:
            return
        others = [(self.ctx.tags.path(t["id"]), t["id"]) for t in self.ctx.tags.all() if t["id"] != tid]
        if not others:
            return
        names = [o[0] for o in others]
        pick, ok = QInputDialog.getItem(self, "Merge tag", f"Merge '{self.ctx.tags.path(tid)}' into:", names, 0, False)
        if ok and confirm(self, "Merge", f"All books tagged '{self.ctx.tags.path(tid)}' will get '{pick}' and the first tag is removed.", "Merge"):
            self._guard(lambda: self.ctx.tags.merge(tid, dict(others)[pick]))

    def delete(self) -> None:
        tid = self.current_id()
        if tid and confirm(self, "Delete tag", f"Delete '{self.ctx.tags.path(tid)}'? Books are not deleted; child tags move up one level.", "Delete tag"):
            self._guard(lambda: self.ctx.tags.delete(tid))

    def move(self, tid: int, new_parent: int | None) -> None:
        self._guard(lambda: self.ctx.tags.move(tid, new_parent))

    def untag(self) -> None:
        tid = self.current_id()
        ids = self.books.selected_ids()
        if tid and ids:
            self.ctx.tags.remove_from_books(tid, ids)
            self.ctx.tags_changed.emit(); self.ctx.library_changed.emit()

    def menu(self, pos) -> None:
        m = QMenu(self)
        m.addAction("New tag").triggered.connect(lambda: self.new(False))
        if self.current_id():
            m.addAction("New child tag").triggered.connect(lambda: self.new(True))
            m.addAction("Rename").triggered.connect(self.rename)
            m.addAction("Merge into…").triggered.connect(self.merge)
            m.addAction("Move to top level").triggered.connect(lambda: self.move(self.current_id(), None))
            m.addAction("Delete").triggered.connect(self.delete)
        m.exec(self.tree.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------ rules
    def _rules_tab(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w)
        lay.addWidget(label("Rules suggest tags from folder names, filenames, metadata or (with a minimum number of hits) extracted keywords. "
                            "Suggestions are only applied after you confirm, unless a rule is set to apply automatically.", "muted", True))
        row = QHBoxLayout()
        self.r_kind = QComboBox()
        for lbl, k in (("Folder name matches", "directory"), ("Filename matches", "filename"), ("Title/author/subject contains", "metadata"), ("Text keyword (needs several hits)", "keyword")):
            self.r_kind.addItem(lbl, k)
        self.r_pat = QLineEdit(); self.r_pat.setPlaceholderText("pattern, e.g. Permaculture or *soil*")
        self.r_tag = QComboBox()
        self.r_hits = QSpinBox(); self.r_hits.setRange(2, 100); self.r_hits.setValue(5); self.r_hits.setToolTip("Minimum keyword hits")
        self.r_auto = QCheckBox("Apply automatically")
        for x in (self.r_kind, self.r_pat, self.r_tag, self.r_hits, self.r_auto):
            row.addWidget(x)
        row.addWidget(button("Add rule", self.add_rule, True))
        lay.addLayout(row)
        self.rules = QTableWidget(0, 5)
        self.rules.setHorizontalHeaderLabels(["Type", "Pattern", "Tag", "Auto", "Min hits"])
        self.rules.setSelectionBehavior(QAbstractItemView.SelectRows); self.rules.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.rules.verticalHeader().hide(); self.rules.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.rules)
        lay.addLayout(hbox(button("Delete rule", self.del_rule), button("Evaluate rules now", self.evaluate, True), None))
        lay.addWidget(label("Suggestions awaiting your confirmation", "h2"))
        self.sugg = QTableWidget(0, 4)
        self.sugg.setHorizontalHeaderLabels(["Book", "Suggested tag", "Why", ""])
        self.sugg.setSelectionBehavior(QAbstractItemView.SelectRows); self.sugg.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.sugg.setEditTriggers(QAbstractItemView.NoEditTriggers); self.sugg.verticalHeader().hide()
        self.sugg.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        lay.addWidget(self.sugg)
        lay.addLayout(hbox(button("Accept selected", lambda: self.resolve(True), True), button("Reject selected", lambda: self.resolve(False)), None))
        return w

    def refresh_rules(self) -> None:
        self.r_tag.clear()
        for t in sorted(self.ctx.tags.all(), key=lambda t: self.ctx.tags.path(t["id"]).lower()):
            self.r_tag.addItem(self.ctx.tags.path(t["id"]), t["id"])
        rules = self.ctx.tags.rules()
        self.rules.setRowCount(len(rules))
        for i, r in enumerate(rules):
            vals = [r["kind"], r["pattern"], self.ctx.tags.path(r["tag_id"]), "yes" if r["auto_apply"] else "ask", str(r["min_keyword_hits"]) if r["kind"] == "keyword" else ""]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 0:
                    it.setData(Qt.UserRole, r["id"])
                self.rules.setItem(i, c, it)
        sg = self.ctx.tags.suggestions()
        self.sugg.setRowCount(len(sg))
        for i, s in enumerate(sg):
            for c, v in enumerate([s["title"], s["tag"], s["reason"]]):
                it = QTableWidgetItem(v)
                if c == 0:
                    it.setData(Qt.UserRole, (s["book_id"], s["tag_id"]))
                self.sugg.setItem(i, c, it)

    def add_rule(self) -> None:
        if self.r_tag.currentData() is None:
            warn(self, "Rule", "Create a tag first.")
            return
        self._guard(lambda: self.ctx.tags.add_rule(self.r_kind.currentData(), self.r_pat.text(), self.r_tag.currentData(), self.r_auto.isChecked(), self.r_hits.value()))
        self.r_pat.clear()

    def del_rule(self) -> None:
        rows = self.rules.selectionModel().selectedRows()
        for r in rows:
            self.ctx.tags.delete_rule(self.rules.item(r.row(), 0).data(Qt.UserRole))
        self.refresh_rules()

    def evaluate(self) -> None:
        self.ctx.run_async(lambda: self.ctx.tags.evaluate_rules(), lambda n: (self.ctx.tags_changed.emit(), self.ctx.library_changed.emit(),
                           info(self, "Rules evaluated", f"{n} new suggestion(s) or automatic tag(s). Review them below.")))

    def resolve(self, accept: bool) -> None:
        for r in self.sugg.selectionModel().selectedRows():
            b, t = self.sugg.item(r.row(), 0).data(Qt.UserRole)
            (self.ctx.tags.accept_suggestion if accept else self.ctx.tags.reject_suggestion)(b, t)
        self.ctx.tags_changed.emit(); self.ctx.library_changed.emit()
