"""Notes: research notebook (topics), clippings and annotations; plus Reading History."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QInputDialog,
                               QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QSplitter, QTabWidget,
                               QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget, QMenu)

from ... import export
from ..common import button, confirm, hbox, human_duration, info, label, warn
from ..context import AppContext


def _write(parent, path: str, text: str) -> bool:
    try:
        with open(path, "w", encoding="utf-8-sig" if path.lower().endswith(".csv") else "utf-8", newline="") as f:
            f.write(text)
        return True
    except OSError as exc:
        warn(parent, "Export failed", str(exc))
        return False


def source_text(c: dict) -> str:
    return export.source_line(c).replace("*", "")


class TopicsTab(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.tid: int | None = None
        sp = QSplitter(self)
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0); outer.addWidget(sp)
        left = QWidget(); ll = QVBoxLayout(left); ll.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit(); self.search.setPlaceholderText("Search topics, notes and passages"); self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda *_: self.refresh())
        self.list = QListWidget(); self.list.setAccessibleName("Research topics")
        self.list.currentItemChanged.connect(self.load)
        ll.addWidget(self.search); ll.addWidget(self.list)
        ll.addLayout(hbox(button("New topic", self.new, True), button("Delete", self.delete, danger=True)))
        sp.addWidget(left)

        right = QWidget(); rl = QVBoxLayout(right); rl.setContentsMargins(0, 0, 0, 0)
        self.title = QLineEdit(); self.title.setPlaceholderText("Topic title"); self.title.setAccessibleName("Topic title")
        rl.addWidget(self.title)
        self.meta = label("", "muted", True)
        rl.addWidget(self.meta)
        es = QSplitter(Qt.Vertical)
        ed = QWidget(); el = QVBoxLayout(ed); el.setContentsMargins(0, 0, 0, 0)
        mode = QHBoxLayout()
        self.preview_btn = button("Preview", self.toggle_preview)
        mode.addWidget(label("Notes (Markdown; link other topics with [[Topic title]])", "muted")); mode.addStretch(1); mode.addWidget(self.preview_btn)
        el.addLayout(mode)
        self.editor = QPlainTextEdit(); self.editor.setAccessibleName("Topic notes")
        self.preview = QTextBrowser(); self.preview.setOpenLinks(False); self.preview.anchorClicked.connect(self._link); self.preview.hide()
        el.addWidget(self.editor); el.addWidget(self.preview)
        es.addWidget(ed)
        lower = QTabWidget()
        self.clips = QListWidget(); self.clips.itemDoubleClicked.connect(self.open_clip)
        cw = QWidget(); cl = QVBoxLayout(cw)
        cl.addWidget(self.clips)
        cl.addLayout(hbox(button("Add clipping…", self.attach_clip), button("Open source", self.open_clip), button("Detach", self.detach_clip), None))
        lower.addTab(cw, "Saved passages")
        lw = QWidget(); ll2 = QVBoxLayout(lw)
        self.books = QListWidget(); self.books.itemDoubleClicked.connect(lambda it: ctx.open_book(it.data(Qt.UserRole)))
        ll2.addWidget(self.books)
        ll2.addLayout(hbox(button("Link book…", self.link_book), button("Unlink", self.unlink_book), None))
        lower.addTab(lw, "Linked books")
        tw = QWidget(); tl = QVBoxLayout(tw)
        self.tags = QLineEdit(); self.tags.setPlaceholderText("tags, separated by ;"); self.tags.editingFinished.connect(self.save_tags)
        tl.addWidget(self.tags)
        tl.addWidget(label("Links to other topics", "muted")); self.links = QListWidget(); self.links.itemDoubleClicked.connect(self._goto_item)
        tl.addWidget(self.links)
        tl.addWidget(label("Referenced by (backlinks)", "muted")); self.back = QListWidget(); self.back.itemDoubleClicked.connect(self._goto_item)
        tl.addWidget(self.back)
        lower.addTab(tw, "Tags and links")
        es.addWidget(lower); es.setSizes([380, 260])
        rl.addWidget(es, 1)
        rl.addLayout(hbox(button("Save", self.save, True), button("Export Markdown…", lambda: self.export("md")),
                          button("Export HTML…", lambda: self.export("html")), None))
        sp.addWidget(right); sp.setSizes([280, 820])
        self._autosave = QTimer(singleShot=True, interval=1500)
        self._autosave.timeout.connect(lambda: self.save(quiet=True))
        self.editor.textChanged.connect(lambda: self._autosave.start() if self.tid else None)
        self.title.editingFinished.connect(lambda: self.save(quiet=True))
        ctx.notes_changed.connect(self.refresh_keep)
        self.refresh()

    def refresh_keep(self) -> None:
        cur = self.tid
        self.refresh(select=cur)

    def refresh(self, select: int | None = None) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for t in self.ctx.research.topics(self.search.text().strip()):
            it = QListWidgetItem(f"{t['title']}  ({t['n_clips']})"); it.setData(Qt.UserRole, t["id"]); self.list.addItem(it)
            if t["id"] == select:
                self.list.setCurrentItem(it)
        self.list.blockSignals(False)
        if select:
            self.load()

    def new(self) -> None:
        name, ok = QInputDialog.getText(self, "New topic", "Topic title:")
        if ok and name.strip():
            try:
                tid = self.ctx.research.create_topic(name)
            except ValueError as exc:
                warn(self, "Topic", str(exc)); return
            self.ctx.notes_changed.emit()
            self.select(tid)

    def select(self, tid: int) -> None:
        self.refresh(select=tid)

    def delete(self) -> None:
        if self.tid and confirm(self, "Delete topic", "Delete this topic page? Its saved passages (clippings) are kept in the Clippings tab.", "Delete topic"):
            self.ctx.research.delete_topic(self.tid); self.tid = None
            self.ctx.notes_changed.emit(); self.clear()

    def clear(self) -> None:
        self.title.clear(); self.editor.clear(); self.meta.clear(); self.clips.clear(); self.books.clear(); self.links.clear(); self.back.clear(); self.tags.clear()

    def load(self, *_a) -> None:
        it = self.list.currentItem()
        if not it:
            return
        self._autosave.stop()
        self.tid = None
        t = self.ctx.research.get_topic(it.data(Qt.UserRole))
        if not t:
            return
        self.title.setText(t["title"]); self.editor.setPlainText(t["body"])
        self.meta.setText(f"Created {t['created'][:16]} · modified {t['modified'][:16]}")
        self.clips.clear()
        for c in t["clippings"]:
            avail = "" if c["book_available"] or c["book_id"] is None else "  [source offline]"
            li = QListWidgetItem(f"“{c['text'][:140]}{'…' if len(c['text']) > 140 else ''}”\n    — {source_text(c)}{avail}")
            li.setData(Qt.UserRole, c["id"]); li.setToolTip(c["text"] + ("\n\nNote: " + c["note"] if c["note"] else "")); self.clips.addItem(li)
        self.books.clear()
        for b in t["books"]:
            li = QListWidgetItem(b["title"] + (f" — {b['authors']}" if b["authors"] else "") + ("" if b["available"] else "  [offline]")); li.setData(Qt.UserRole, b["id"]); self.books.addItem(li)
        self.links.clear(); self.back.clear()
        for x in t["links"]:
            li = QListWidgetItem(x["title"]); li.setData(Qt.UserRole, x["id"]); self.links.addItem(li)
        for x in t["backlinks"]:
            li = QListWidgetItem(x["title"]); li.setData(Qt.UserRole, x["id"]); self.back.addItem(li)
        self.tags.setText("; ".join(x["name"] for x in t["tags"]))
        self.tid = t["id"]
        if not self.preview.isHidden():
            self.render_preview()

    def save(self, quiet: bool = False) -> None:
        if not self.tid:
            return
        try:
            self.ctx.research.save_topic(self.tid, self.title.text(), self.editor.toPlainText())
        except ValueError as exc:
            warn(self, "Topic", str(exc)); return
        if not quiet:
            self.ctx.message.emit("Topic saved.")
        cur = self.tid
        self.list.blockSignals(True)
        it = self.list.currentItem()
        if it:
            it.setText(self.title.text())
        self.list.blockSignals(False)
        self.links.clear(); self.back.clear()
        t = self.ctx.research.get_topic(cur)
        for x in t["links"]:
            li = QListWidgetItem(x["title"]); li.setData(Qt.UserRole, x["id"]); self.links.addItem(li)
        for x in t["backlinks"]:
            li = QListWidgetItem(x["title"]); li.setData(Qt.UserRole, x["id"]); self.back.addItem(li)

    def save_tags(self) -> None:
        if not self.tid:
            return
        want = [self.ctx.tags.get_or_create_path(x) for x in self.tags.text().split(";") if x.strip()]
        have = {x["id"] for x in self.ctx.research.get_topic(self.tid)["tags"]}
        for t in set(want) - have:
            self.ctx.research.tag_topic(self.tid, t)
        for t in have - set(want):
            self.ctx.research.tag_topic(self.tid, t, False)
        self.ctx.tags_changed.emit()

    def _goto_item(self, it) -> None:
        self.select(it.data(Qt.UserRole))

    def _link(self, url) -> None:
        pass

    def toggle_preview(self) -> None:
        if self.preview.isHidden():
            self.render_preview(); self.preview.show(); self.editor.hide(); self.preview_btn.setText("Edit")
        else:
            self.preview.hide(); self.editor.show(); self.preview_btn.setText("Preview")

    def render_preview(self) -> None:
        self.preview.setMarkdown(self.editor.toPlainText())

    # ------------------------------------------------------------- relations
    def attach_clip(self) -> None:
        if not self.tid:
            return
        rows = self.ctx.research.clippings(limit=300)
        if not rows:
            info(self, "Clippings", "There are no clippings yet. Save passages from Research or the Reader first.")
            return
        names = [f"{r['id']}: {r['text'][:80]} — {r['book_title']}" for r in rows]
        pick, ok = QInputDialog.getItem(self, "Add clipping", "Choose a clipping:", names, 0, False)
        if ok:
            self.ctx.research.attach_clipping(self.tid, int(pick.split(":")[0])); self.ctx.notes_changed.emit()

    def detach_clip(self) -> None:
        it = self.clips.currentItem()
        if it and self.tid:
            self.ctx.research.detach_clipping(self.tid, it.data(Qt.UserRole)); self.ctx.notes_changed.emit()

    def open_clip(self, *_a) -> None:
        it = self.clips.currentItem()
        if it:
            open_clipping(self.ctx, self, it.data(Qt.UserRole))

    def link_book(self) -> None:
        if not self.tid:
            return
        from ...library import BookFilter
        rows = self.ctx.lib.query_books(BookFilter(), "title", False, 2000)
        names = [f"{r['title']} — {r['authors']}  #{r['id']}" for r in rows]
        pick, ok = QInputDialog.getItem(self, "Link book", "Choose a book:", names, 0, False)
        if ok:
            self.ctx.research.link_book(self.tid, int(pick.rsplit("#", 1)[1])); self.load()

    def unlink_book(self) -> None:
        it = self.books.currentItem()
        if it and self.tid:
            self.ctx.research.link_book(self.tid, it.data(Qt.UserRole), False); self.load()

    def export(self, kind: str) -> None:
        if not self.tid:
            return
        self.save(quiet=True)
        t = self.ctx.research.get_topic(self.tid)
        path, _ = QFileDialog.getSaveFileName(self, "Export topic", f"{t['title']}.{kind}", "Markdown (*.md)" if kind == "md" else "HTML (*.html)")
        if path:
            md = export.export_topic_md(self.ctx.db, self.tid)
            _write(self, path, md if kind == "md" else export.markdown_to_html(md, t["title"]))


def open_clipping(ctx: AppContext, parent, cid: int) -> None:
    c = ctx.research.get_clipping(cid)
    if not c:
        return
    if c["book_id"] is None:
        info(parent, "Source unavailable", f"The book record for this clipping was removed from the library.\n\nSource: {source_text(c)}\nOriginal path: {c['source_path']}")
        return
    if not c["book_available"]:
        info(parent, "Source offline", f"The source file is not available right now, but the clipping is safe.\n\nSource: {source_text(c)}\nFile: {c['source_path']}\n\n"
             "Reconnect the drive to open the book at this location.")
        return
    loc = dict(c["locator"])
    loc.setdefault("type", c["loc_type"]); loc.setdefault("num", c["loc_num"])
    loc["excerpt"] = c["text"]
    ctx.open_book(c["book_id"], loc)


class ClippingsTab(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.search = QLineEdit(); self.search.setPlaceholderText("Search clippings"); self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda *_: self.refresh())
        row.addWidget(self.search)
        lay.addLayout(row)
        sp = QSplitter()
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Passage", "Source", "Location", "Saved"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows); self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers); self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self.show_detail)
        self.table.cellDoubleClicked.connect(lambda *_: self.open())
        sp.addWidget(self.table)
        self.detail = QTextBrowser(); self.detail.setOpenLinks(False)
        sp.addWidget(self.detail); sp.setSizes([650, 400])
        lay.addWidget(sp, 1)
        lay.addLayout(hbox(button("Open source", self.open, True), button("Edit note…", self.edit_note), button("Add tag…", self.add_tag),
                           button("Link to another clipping…", self.link), button("Add to topic…", self.to_topic), button("Delete", self.delete, danger=True), None,
                           button("Export selected…", self.export)))
        ctx.notes_changed.connect(self.refresh)
        self.refresh()

    def ids(self) -> list[int]:
        return [self.table.item(i.row(), 0).data(Qt.UserRole) for i in self.table.selectionModel().selectedRows()]

    def refresh(self) -> None:
        rows = self.ctx.research.clippings(self.search.text().strip())
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            c = self.ctx.research.get_clipping(r["id"])
            vals = [r["text"].replace("\n", " ")[:160], f"{r['book_title']}" + (f" — {r['book_authors']}" if r["book_authors"] else ""), export.location_text(c), r["created"][:10]]
            for k, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if k == 0:
                    it.setData(Qt.UserRole, r["id"])
                self.table.setItem(i, k, it)

    def show_detail(self) -> None:
        ids = self.ids()
        if not ids:
            self.detail.clear(); return
        c = self.ctx.research.get_clipping(ids[0])
        import html
        links = ", ".join(str(x) for x in c["links"]) or "—"
        avail = "" if c["book_available"] or c["book_id"] is None else " <i>(source offline)</i>"
        self.detail.setHtml(
            f"<blockquote>{html.escape(c['text']).replace(chr(10), '<br>')}</blockquote>"
            f"<p><b>Source:</b> {html.escape(source_text(c))}{avail}<br><b>File:</b> {html.escape(c['source_path'] or '—')}<br><b>Saved:</b> {c['created']}</p>"
            f"<p><b>My note:</b> {html.escape(c['note']) or '—'}</p><p><b>Tags:</b> {html.escape(', '.join(t['name'] for t in c['tags'])) or '—'}<br>"
            f"<b>Topics:</b> {html.escape(', '.join(t['title'] for t in c['topics'])) or '—'}<br><b>Linked clippings:</b> {links}</p>")

    def open(self) -> None:
        ids = self.ids()
        if ids:
            open_clipping(self.ctx, self, ids[0])

    def edit_note(self) -> None:
        ids = self.ids()
        if not ids:
            return
        c = self.ctx.research.get_clipping(ids[0])
        text, ok = QInputDialog.getMultiLineText(self, "Note", "Personal note:", c["note"])
        if ok:
            self.ctx.research.update_clipping(ids[0], text); self.ctx.notes_changed.emit()

    def add_tag(self) -> None:
        ids = self.ids()
        if not ids:
            return
        name, ok = QInputDialog.getText(self, "Add tag", "Tag (use / for sub-tags):")
        if ok and name.strip():
            tid = self.ctx.tags.get_or_create_path(name)
            for i in ids:
                self.ctx.research.tag_clipping(i, tid)
            self.ctx.tags_changed.emit(); self.show_detail()

    def link(self) -> None:
        ids = self.ids()
        if not ids:
            return
        rows = [r for r in self.ctx.research.clippings(limit=300) if r["id"] != ids[0]]
        if not rows:
            return
        names = [f"{r['id']}: {r['text'][:80]}" for r in rows]
        pick, ok = QInputDialog.getItem(self, "Link clippings", "Link to:", names, 0, False)
        if ok:
            self.ctx.research.link_clippings(ids[0], int(pick.split(":")[0])); self.show_detail()

    def to_topic(self) -> None:
        ids = self.ids()
        topics = self.ctx.research.topics()
        if not ids or not topics:
            info(self, "Topics", "Create a topic first (Topics tab).")
            return
        names = [t["title"] for t in topics]
        pick, ok = QInputDialog.getItem(self, "Add to topic", "Topic:", names, 0, False)
        if ok:
            tid = self.ctx.research.topic_by_title(pick)
            for i in ids:
                self.ctx.research.attach_clipping(tid, i)
            self.ctx.notes_changed.emit()

    def delete(self) -> None:
        ids = self.ids()
        if ids and confirm(self, "Delete clippings", f"Delete {len(ids)} clipping(s)? This cannot be undone.", "Delete"):
            for i in ids:
                self.ctx.research.delete_clipping(i)
            self.ctx.notes_changed.emit()

    def export(self) -> None:
        ids = self.ids() or [self.table.item(r, 0).data(Qt.UserRole) for r in range(self.table.rowCount())]
        if not ids:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export clippings", "clippings.md", "Markdown (*.md);;HTML (*.html);;CSV (*.csv)")
        if not path:
            return
        if path.lower().endswith(".csv"):
            txt = export.export_clippings_csv(self.ctx.db, ids)
        else:
            md = export.export_clippings_md(self.ctx.db, ids)
            txt = export.markdown_to_html(md) if path.lower().endswith(".html") else md
        _write(self, path, txt)


class AnnotationsTab(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        lay = QVBoxLayout(self)
        self.search = QLineEdit(); self.search.setPlaceholderText("Search highlights, notes and bookmarks"); self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda *_: self.refresh())
        lay.addWidget(self.search)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Type", "Text / note", "Book", "Location", "Modified"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows); self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide(); self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.cellDoubleClicked.connect(lambda *_: self.open())
        lay.addWidget(self.table, 1)
        lay.addLayout(hbox(button("Open in reader", self.open, True), button("Delete", self.delete, danger=True), None))
        ctx.notes_changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        rows = self.ctx.reading.all_with_books(self.search.text().strip())
        self.table.setRowCount(len(rows))
        import json
        for i, r in enumerate(rows):
            loc = json.loads(r["locator"] or "{}")
            where = f"p. {loc.get('num')}" if loc.get("type") == "page" else (r["loc_label"] or (f"chapter {int(loc.get('num', 0)) + 1}" if loc.get("type") == "chapter" else ""))
            txt = (r["note"] + ("  —  " if r["note"] and r["selected_text"] else "") + r["selected_text"]).replace("\n", " ")[:200]
            vals = [("⚠ " if r["invalid"] else "") + r["type"], txt, r["title"] + ("" if r["available"] else " [offline]"), where, r["modified"][:16]]
            for k, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if k == 0:
                    it.setData(Qt.UserRole, (r["id"], r["book_id"], loc, r["available"]))
                    if r["invalid"]:
                        it.setToolTip("The source file changed after this annotation was made; its position may no longer be correct.")
                self.table.setItem(i, k, it)

    def open(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if rows:
            _id, bid, loc, avail = self.table.item(rows[0].row(), 0).data(Qt.UserRole)
            if not avail:
                info(self, "Source offline", "The book file is offline. The annotation text is shown here; reconnect the source to open it in the reader.")
                return
            self.ctx.open_book(bid, loc)

    def delete(self) -> None:
        for r in self.table.selectionModel().selectedRows():
            self.ctx.reading.delete(self.table.item(r.row(), 0).data(Qt.UserRole)[0])
        self.ctx.notes_changed.emit()


class NotesPage(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.setObjectName("page")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 10)
        lay.addWidget(label("Notes", "h1"))
        lay.addWidget(label("Your research notebook is stored in Atlas Library's own database, independent of the book files.", "muted"))
        self.tabs = QTabWidget()
        self.topics = TopicsTab(ctx); self.clips = ClippingsTab(ctx); self.anns = AnnotationsTab(ctx)
        self.tabs.addTab(self.topics, "Topics"); self.tabs.addTab(self.clips, "Clippings"); self.tabs.addTab(self.anns, "Highlights and annotations")
        lay.addWidget(self.tabs, 1)


class HistoryPage(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.setObjectName("page")
        self.ctx = ctx
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 10)
        lay.addWidget(label("Reading History", "h1"))
        self.stats = label("", "muted")
        lay.addWidget(self.stats)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Title", "Author", "Last read", "Progress", "Time spent"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows); self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide(); self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.cellDoubleClicked.connect(self.open)
        lay.addWidget(self.table, 1)
        lay.addLayout(hbox(button("Open selected", lambda: self.open(self.table.currentRow(), 0), True), None))
        ctx.library_changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        rows = self.ctx.reading.history()
        rs = self.ctx.reading.stats()
        self.stats.setText(f"{rs['finished']} finished · {rs['in_progress']} in progress · {human_duration(rs['seconds_total'])} total reading time"
                           if self.ctx.settings.reading_stats else f"{rs['finished']} finished · {rs['in_progress']} in progress")
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            vals = [r["title"] + ("" if r["available"] else "  [offline]"), r["authors"], (r["last_read"] or "")[:16], f"{r['percent']:.0f}%", human_duration(r["seconds"]) if r["seconds"] else ""]
            for k, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if k == 0:
                    it.setData(Qt.UserRole, (r["id"], r["available"]))
                self.table.setItem(i, k, it)

    def open(self, row: int, _col: int) -> None:
        if row < 0:
            return
        bid, avail = self.table.item(row, 0).data(Qt.UserRole)
        self.ctx.open_book(bid)
