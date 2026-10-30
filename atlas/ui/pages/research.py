"""Research Mode: library-wide full-text search with filters, passage navigation and clipping."""
from __future__ import annotations

import html
import json
from datetime import datetime, timedelta

from PySide6.QtCore import QModelIndex, QSize, Qt
from PySide6.QtGui import QAbstractTextDocumentLayout, QPalette, QTextDocument
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
                               QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu,
                               QPushButton, QSpinBox, QSplitter, QStyle, QStyledItemDelegate, QTabWidget, QToolButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QDialogButtonBox, QPlainTextEdit)

from ...export import export_search_csv, export_search_md, markdown_to_html
from ...library import BookFilter
from ...search import QuerySyntaxError
from ..common import button, confirm, hbox, info, label, warn
from ..context import AppContext

HELP = """<h3>Search syntax</h3>
<table cellpadding=4>
<tr><td><code>soil compost</code></td><td>both words (implicit AND)</td></tr>
<tr><td><code>"passive solar"</code></td><td>exact phrase</td></tr>
<tr><td><code>tomato OR basil</code></td><td>either word (OR must be capitalised)</td></tr>
<tr><td><code>mulch NOT clay</code> or <code>mulch -clay</code></td><td>exclude a word</td></tr>
<tr><td><code>(tomato OR basil) AND "companion planting"</code></td><td>grouping with parentheses</td></tr>
<tr><td><code>compost*</code></td><td>prefix: compost, composting, compostable…</td></tr>
</table>
<p>Searching is case-insensitive and ignores diacritics (<i>grădină</i> = <i>gradina</i>).
This is keyword search, not natural-language search: it finds the words you type, not synonyms.
Lower-case <code>and</code>/<code>or</code>/<code>not</code> are searched as ordinary words.</p>"""


class HtmlDelegate(QStyledItemDelegate):
    """Renders rich-text rows (stored in Qt.UserRole+1) without creating widgets."""

    def _doc(self, option, index) -> QTextDocument:
        doc = QTextDocument()
        doc.setDefaultFont(option.font)
        vw = option.widget.viewport().width() if option.widget is not None else option.rect.width()
        w = max((option.rect.width() if option.rect.width() > 50 else vw - 40) - 8, 200)
        doc.setTextWidth(w)
        doc.setHtml(index.data(Qt.UserRole + 1) or "")
        return doc

    def sizeHint(self, option, index) -> QSize:
        doc = self._doc(option, index)
        return QSize(int(doc.idealWidth()), int(doc.size().height()) + 8)

    def paint(self, painter, option, index) -> None:
        painter.save()
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight().color().darker(160))
        doc = self._doc(option, index)
        painter.translate(option.rect.left() + 4, option.rect.top() + 4)
        ctx = QAbstractTextDocumentLayout.PaintContext()
        ctx.palette.setColor(QPalette.Text, option.palette.text().color())
        doc.documentLayout().draw(painter, ctx)
        painter.restore()


class ResearchPage(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.setObjectName("page")
        self.ctx = ctx
        self.response = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 8)
        head = QHBoxLayout()
        head.addWidget(label("Research", "h1"))
        head.addStretch(1)
        self.summary = label("", "muted")
        head.addWidget(self.summary)
        lay.addLayout(head)

        row = QHBoxLayout()
        self.query = QLineEdit(); self.query.setPlaceholderText('Search all indexed books — e.g.  "companion planting" tomato NOT blight')
        self.query.setClearButtonEnabled(True); self.query.setAccessibleName("Research query")
        self.query.returnPressed.connect(self.run)
        row.addWidget(self.query, 1)
        row.addWidget(button("Search", self.run, True))
        row.addWidget(button("Syntax help", self.show_help))
        lay.addLayout(row)
        self.error = label("", wrap=True)
        self.error.setStyleSheet("color:#e0a030")
        self.error.hide()
        lay.addWidget(self.error)

        # filters
        fr = QHBoxLayout()
        self.f_tag = QComboBox(); self.f_author = QComboBox(); self.f_genre = QComboBox(); self.f_fmt = QComboBox()
        self.f_lang = QComboBox(); self.f_coll = QComboBox(); self.f_root = QComboBox(); self.f_added = QComboBox(); self.f_idx = QComboBox()
        self.f_y1 = QSpinBox(); self.f_y2 = QSpinBox()
        for s in (self.f_y1, self.f_y2):
            s.setRange(0, 2100); s.setSpecialValueText("any")
        self.f_folder = QLineEdit(); self.f_folder.setPlaceholderText("Folder (relative to source)")
        self.filter_items = [("Tag", self.f_tag), ("Author", self.f_author), ("Genre", self.f_genre), ("Format", self.f_fmt),
                             ("Language", self.f_lang), ("Collection", self.f_coll), ("Source", self.f_root), ("Added", self.f_added), ("Indexed", self.f_idx)]
        self.book_scope: list[int] | None = None
        self.scope_label = label("", "muted")
        self.filter_row = QWidget(); fl = QHBoxLayout(self.filter_row); fl.setContentsMargins(0, 0, 0, 0)
        for t, w in self.filter_items:
            w.setAccessibleName(t); w.setToolTip(t)
            fl.addWidget(w)
        fl.addWidget(label("Year")); fl.addWidget(self.f_y1); fl.addWidget(label("–")); fl.addWidget(self.f_y2)
        fl.addWidget(self.f_folder, 1)
        lay.addWidget(self.filter_row)
        opt = QHBoxLayout()
        self.group = QCheckBox("Group by book"); self.group.setChecked(ctx.settings.group_by_book)
        self.sort = QComboBox(); [self.sort.addItem(a, b) for a, b in (("Relevance", "relevance"), ("Title", "title"), ("Author", "author"), ("Year (newest)", "year"), ("Date added", "added"))]
        self.limit = QSpinBox(); self.limit.setRange(10, 5000); self.limit.setValue(ctx.settings.result_limit); self.limit.setSuffix(" max")
        self.per_book = QSpinBox(); self.per_book.setRange(1, 50); self.per_book.setValue(ctx.settings.passages_per_book); self.per_book.setSuffix(" per book")
        self.sem_cb = QCheckBox("Also search by meaning")
        self.sem_cb.setToolTip("Optional local semantic search; needs a model configured in Settings > Semantic search")
        for w in (self.group, label("Sort:"), self.sort, self.limit, self.per_book, self.sem_cb, self.scope_label):
            opt.addWidget(w)
        opt.addStretch(1)
        opt.addWidget(button("Clear filters", self.clear_filters))
        lay.addLayout(opt)

        sp = QSplitter()
        self.tree = QTreeWidget(); self.tree.setHeaderHidden(True); self.tree.setItemDelegate(HtmlDelegate(self.tree))
        self.tree.setUniformRowHeights(False); self.tree.setWordWrap(True); self.tree.setAccessibleName("Search results")
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.itemDoubleClicked.connect(lambda *_: self.open_selected())
        self.tree.itemActivated.connect(lambda *_: self.open_selected())
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.menu)
        sp.addWidget(self.tree)
        side = QTabWidget()
        self.saved = QListWidget(); self.hist = QListWidget()
        self.saved.itemDoubleClicked.connect(self.run_saved); self.hist.itemDoubleClicked.connect(self.run_history)
        sv = QWidget(); svl = QVBoxLayout(sv); svl.addWidget(self.saved)
        svl.addLayout(hbox(button("Delete", self.delete_saved), None))
        hv = QWidget(); hvl = QVBoxLayout(hv); hvl.addWidget(self.hist)
        hvl.addLayout(hbox(button("Clear history", self.clear_history), None))
        side.addTab(sv, "Saved searches"); side.addTab(hv, "History")
        sp.addWidget(side)
        sp.setStretchFactor(0, 1); sp.setSizes([900, 260])
        lay.addWidget(sp, 1)

        bar = QHBoxLayout()
        self.b_prev = button("◀ Previous passage", lambda: self.step(-1), tip="Alt+Left")
        self.b_next = button("Next passage ▶", lambda: self.step(1), tip="Alt+Right")
        bar.addWidget(self.b_prev); bar.addWidget(self.b_next)
        bar.addWidget(button("Open passage", self.open_selected, True))
        bar.addWidget(button("Save as clipping…", self.clip_selected))
        bar.addStretch(1)
        bar.addWidget(button("Save search…", self.save_search))
        bar.addWidget(button("Export results…", self.export))
        lay.addLayout(bar)
        self.b_prev.setShortcut("Alt+Left"); self.b_next.setShortcut("Alt+Right")

        ctx.library_changed.connect(self.populate_filters)
        ctx.tags_changed.connect(self.populate_filters)
        self.update_semantic_state()
        self.populate_filters()
        self.refresh_side()
        self._restore_state()

    def _save_state(self, q: str, flt: BookFilter) -> None:
        try:
            self.ctx.db.set_state("research_state", json.dumps({
                "query": q, "filters": flt.to_dict(), "sort": self.sort.currentData(), "group": self.group.isChecked()}))
        except Exception:
            pass

    def _restore_state(self) -> None:
        try:
            st = json.loads(self.ctx.db.get_state("research_state") or "{}")
        except ValueError:
            return
        if not st:
            return
        self.query.setText(st.get("query", ""))
        self.apply_filter_dict(st.get("filters", {}))
        i = self.sort.findData(st.get("sort"))
        if i >= 0:
            self.sort.setCurrentIndex(i)
        self.group.setChecked(bool(st.get("group", True)))
        if st.get("query"):
            self.summary.setText("Last search restored — press Search to run it again.")

    def update_semantic_state(self) -> None:
        st = self.ctx.semantic_status()
        ok = st.available
        self.sem_cb.setEnabled(ok)
        if not ok:
            self.sem_cb.setChecked(False)
            self.sem_cb.setToolTip("Semantic search is not available: " + st.message)

    # ----------------------------------------------------------------- filters
    def populate_filters(self) -> None:
        lib = self.ctx.lib

        def fill(cb: QComboBox, title: str, items):
            cur = cb.currentData()
            cb.blockSignals(True)
            cb.clear(); cb.addItem(f"{title}: any", None)
            for l, v in items:
                cb.addItem(l, v)
            cb.setCurrentIndex(max(0, cb.findData(cur)))
            cb.blockSignals(False)
        fill(self.f_tag, "Tag", [(self.ctx.tags.path(t["id"]), t["id"]) for t in sorted(self.ctx.tags.all(), key=lambda t: self.ctx.tags.path(t["id"]).lower())])
        fill(self.f_author, "Author", [(a, a) for a in lib.distinct_values("author")])
        fill(self.f_genre, "Genre", [(a, a) for a in lib.distinct_values("genre")])
        fill(self.f_fmt, "Format", [(a.upper(), a) for a in lib.distinct_values("format")])
        fill(self.f_lang, "Language", [(a, a) for a in lib.distinct_values("language")])
        fill(self.f_coll, "Collection", [(c["name"], c["id"]) for c in lib.collections()])
        fill(self.f_root, "Source", [(r["label"], r["id"]) for r in lib.roots()])
        fill(self.f_added, "Added", [("last 7 days", 7), ("last 30 days", 30), ("last year", 365)])
        fill(self.f_idx, "Indexed", [("fully indexed", "indexed"), ("partly (needs OCR?)", "partial")])

    def clear_filters(self) -> None:
        for _t, w in self.filter_items:
            w.setCurrentIndex(0)
        self.f_y1.setValue(0); self.f_y2.setValue(0); self.f_folder.clear()
        self.set_scope(None, "")

    def set_scope(self, book_ids: list[int] | None, text: str) -> None:
        self.book_scope = book_ids
        self.scope_label.setText(f"Searching only in: {text}" if book_ids else "")

    def current_filter(self) -> BookFilter:
        f = BookFilter(tag_id=self.f_tag.currentData(), author=self.f_author.currentData() or "", genre=self.f_genre.currentData() or "",
                       format=self.f_fmt.currentData() or "", language=self.f_lang.currentData() or "",
                       collection_id=self.f_coll.currentData(), root_id=self.f_root.currentData(),
                       year_from=self.f_y1.value() or None, year_to=self.f_y2.value() or None,
                       index_status=self.f_idx.currentData() or "", folder=self.f_folder.text().strip(), book_ids=self.book_scope)
        days = self.f_added.currentData()
        if days:
            f.added_since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        return f

    def apply_filter_dict(self, d: dict) -> None:
        f = BookFilter.from_dict(d)
        for cb, val in ((self.f_tag, f.tag_id), (self.f_author, f.author), (self.f_genre, f.genre), (self.f_fmt, f.format),
                        (self.f_lang, f.language), (self.f_coll, f.collection_id), (self.f_root, f.root_id), (self.f_idx, f.index_status)):
            cb.setCurrentIndex(max(0, cb.findData(val or None)))
        self.f_y1.setValue(f.year_from or 0); self.f_y2.setValue(f.year_to or 0); self.f_folder.setText(f.folder)
        self.set_scope(list(f.book_ids) if f.book_ids else None, "selected books" if f.book_ids else "")

    # ------------------------------------------------------------------ search
    def search_for(self, text: str) -> None:
        self.query.setText(text)
        self.run()

    def run(self) -> None:
        q = self.query.text().strip()
        self.error.hide()
        if not q:
            self.error.setText("Type something to search for."); self.error.show()
            return
        s = self.ctx.settings
        flt, grouped, sort, limit, per = self.current_filter(), self.group.isChecked(), self.sort.currentData(), self.limit.value(), self.per_book.value()
        s.group_by_book, s.result_limit, s.passages_per_book = grouped, limit, per
        self.summary.setText("Searching…")
        self._save_state(q, flt)

        want_sem = self.sem_cb.isChecked()

        def work():
            resp = self.ctx.engine.search(q, flt, sort, limit, per, grouped, True, s.history_limit)
            related = []
            if want_sem:
                svc = self.ctx.semantic_service()
                if svc is not None:
                    ids = self.ctx.lib.book_ids(flt) if flt.to_dict() else None
                    related = self.ctx.engine.related_passages(svc.search(q, 30, ids))
            return resp, related

        def done(res):
            resp, related = res
            self.response = resp
            self.show_results(resp)
            if related:
                self.show_related(related, resp.parsed.terms)
            elif want_sem:
                self.summary.setText(self.summary.text() + " · no related passages by meaning (is the semantic index built?)")
            self.refresh_side()

        def fail(exc):
            self.summary.setText("")
            self.sem_cb.setChecked(False) if want_sem and not isinstance(exc, QuerySyntaxError) else None
            if isinstance(exc, QuerySyntaxError):
                self.error.setText("Search syntax problem: " + exc.message); self.error.show()
            else:
                self.error.setText(f"Search failed: {exc}"); self.error.show()
        self.ctx.run_async(work, done, fail)

    def show_results(self, resp) -> None:
        self.tree.setUpdatesEnabled(False)
        self.tree.clear()
        terms = resp.parsed.terms
        if resp.parsed.warnings:
            self.error.setText(" ".join(resp.parsed.warnings)); self.error.show()
        books = {b.book_id: b for b in resp.books}
        if self.group.isChecked():
            for b in resp.books:
                top = QTreeWidgetItem(self.tree)
                top.setData(0, Qt.UserRole, ("book", b.book_id))
                top.setData(0, Qt.UserRole + 1, self._book_html(b, True))
                for p in b.passages:
                    self._passage_item(top, p, b, terms, False)
                top.setExpanded(True)
        else:
            for p in resp.passages:
                b = books.get(p.book_id)
                self._passage_item(self.tree, p, b, terms, True)
        self.tree.setUpdatesEnabled(True)
        self.tree.scheduleDelayedItemsLayout()
        extra = " (showing the best matches only — refine the query or raise the limit)" if resp.truncated else ""
        self.summary.setText(f"{resp.total_passages:,} passages in {resp.total_books:,} books · {resp.elapsed_ms:.0f} ms{extra}")
        if not resp.books:
            self.summary.setText("No matches. Check spelling, try fewer words or a prefix such as compost*. Offline books are searched too.")
        first = self.tree.topLevelItem(0)
        if first is not None:
            (first.child(0) if first.childCount() else first)
            self.tree.setCurrentItem(first.child(0) if first.childCount() else first)

    def show_related(self, books, terms) -> None:
        top = QTreeWidgetItem(self.tree)
        top.setData(0, Qt.UserRole, ("group",))
        top.setData(0, Qt.UserRole + 1, "<b style='font-size:11pt;color:#79b0e8'>Related passages by meaning</b> "
                    "<span style='color:#8b93a1'>(local semantic search — may not contain your exact words)</span>")
        for b in books:
            bi = QTreeWidgetItem(top)
            bi.setData(0, Qt.UserRole, ("book", b.book_id))
            bi.setData(0, Qt.UserRole + 1, self._book_html(b, True))
            for p in b.passages:
                self._passage_item(bi, p, b, terms, False)
            bi.setExpanded(True)
        top.setExpanded(True)

    def _book_html(self, b, header: bool) -> str:
        tags = f" · <span style='color:#8b93a1'>{html.escape(', '.join(b.tags[:6]))}</span>" if b.tags else ""
        off = " · <span style='color:#e0a030'>source offline</span>" if not b.available else ""
        return (f"<b style='font-size:11pt'>{html.escape(b.title)}</b> <span style='color:#8b93a1'>{html.escape(b.authors or '')}"
                f"{' · ' + str(b.year) if b.year else ''} · {b.fmt.upper()}</span>{tags} · {b.match_count} match{'es' if b.match_count != 1 else ''}{off}")

    def _passage_item(self, parent, p, b, terms, with_book: bool) -> None:
        it = QTreeWidgetItem(parent)
        loc = html.escape(p.location_text)
        head = ""
        if with_book and b is not None:
            head = self._book_html(b, False) + "<br>"
        it.setData(0, Qt.UserRole + 1, f"{head}<span style='color:#8b93a1'>{loc}</span>&nbsp; {p.excerpt_html.replace('<b>', '<b style=\"background:#6b5a14;color:#fff\">')}")
        it.setData(0, Qt.UserRole, ("passage", p, p.locator(terms), p.book_id))

    # ---------------------------------------------------------------- actions
    def _current_passage(self):
        it = self.tree.currentItem()
        if it is None:
            return None
        d = it.data(0, Qt.UserRole)
        if d and d[0] == "passage":
            return d
        if d and d[0] == "book":
            return ("book", None, None, d[1])
        return None

    def open_selected(self) -> None:
        d = self._current_passage()
        if not d:
            return
        _, p, loc, bid = d
        book = self.ctx.lib.get_book(bid)
        if book and not book["available"]:
            info(self, "Source offline", f"'{book['title']}' is not available right now (drive disconnected or file moved).\n\n"
                 "The passage text below is from the saved index; reconnect the source to open the book.\n\n" + (p.excerpt_plain if p else ""))
            return
        self.ctx.open_book(bid, loc or {})

    def step(self, delta: int) -> None:
        items = []
        for i in range(self.tree.topLevelItemCount()):
            t = self.tree.topLevelItem(i)
            d = t.data(0, Qt.UserRole)
            if d and d[0] == "passage":
                items.append(t)
            for j in range(t.childCount()):
                items.append(t.child(j))
        if not items:
            return
        cur = self.tree.currentItem()
        idx = items.index(cur) if cur in items else (-1 if delta > 0 else len(items))
        idx = max(0, min(len(items) - 1, idx + delta))
        self.tree.setCurrentItem(items[idx])
        self.tree.scrollToItem(items[idx])

    def clip_selected(self) -> None:
        d = self._current_passage()
        if not d or d[1] is None:
            info(self, "Clipping", "Select a passage first.")
            return
        _, p, loc, bid = d
        ClipDialog.make_clipping(self.ctx, self, p.excerpt_plain.strip("… "), bid, {"type": p.loc_type, "num": p.loc_num, "label": p.loc_label, "char_start": p.char_start})

    def menu(self, pos) -> None:
        d = self._current_passage()
        m = QMenu(self)
        if d:
            m.addAction("Open passage").triggered.connect(self.open_selected)
            if d[1] is not None:
                m.addAction("Save as clipping…").triggered.connect(self.clip_selected)
                m.addAction("Search only in this book").triggered.connect(lambda: self._scope_to(d[3]))
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def _scope_to(self, book_id: int) -> None:
        b = self.ctx.lib.get_book(book_id)
        self.set_scope([book_id], b["title"] if b else "book")
        self.run()

    def show_help(self) -> None:
        d = QDialog(self); d.setWindowTitle("Search syntax")
        l = QVBoxLayout(d); lb = QLabel(HELP); lb.setTextFormat(Qt.RichText); lb.setWordWrap(True); l.addWidget(lb)
        bb = QDialogButtonBox(QDialogButtonBox.Close); bb.rejected.connect(d.reject); l.addWidget(bb)
        d.resize(560, 360); d.exec()

    # ----------------------------------------------------------- saved / history
    def refresh_side(self) -> None:
        self.saved.clear(); self.hist.clear()
        for s in self.ctx.engine.saved_searches():
            it = QListWidgetItem(f"{s['name']}   —   {s['query']}"); it.setData(Qt.UserRole, s); self.saved.addItem(it)
        for h in self.ctx.engine.history(50):
            it = QListWidgetItem(f"{h['query']}   ({h['result_count']})"); it.setData(Qt.UserRole, h); self.hist.addItem(it)

    def run_saved(self, it) -> None:
        s = it.data(Qt.UserRole)
        self.query.setText(s["query"]); self.apply_filter_dict(json.loads(s["filters"] or "{}")); self.run()

    def run_history(self, it) -> None:
        h = it.data(Qt.UserRole)
        self.query.setText(h["query"]); self.apply_filter_dict(json.loads(h["filters"] or "{}")); self.run()

    def save_search(self) -> None:
        q = self.query.text().strip()
        if not q:
            return
        name, ok = QInputDialog.getText(self, "Save search", "Name for this search:", text=q[:40])
        if ok and name.strip():
            self.ctx.engine.save_search(name, q, self.current_filter()); self.refresh_side()

    def delete_saved(self) -> None:
        it = self.saved.currentItem()
        if it:
            self.ctx.engine.delete_saved(it.data(Qt.UserRole)["id"]); self.refresh_side()

    def clear_history(self) -> None:
        self.ctx.engine.clear_history(); self.refresh_side()

    def export(self) -> None:
        if not self.response or not self.response.passages:
            info(self, "Export", "Run a search first.")
            return
        path, flt = QFileDialog.getSaveFileName(self, "Export results", "search-results.md", "Markdown (*.md);;HTML (*.html);;CSV (*.csv)")
        if not path:
            return
        try:
            if path.lower().endswith(".csv"):
                txt = export_search_csv(self.response)
            elif path.lower().endswith(".html"):
                txt = markdown_to_html(export_search_md(self.response))
            else:
                txt = export_search_md(self.response)
            with open(path, "w", encoding="utf-8-sig" if path.lower().endswith(".csv") else "utf-8", newline="") as f:
                f.write(txt)
        except OSError as exc:
            warn(self, "Export failed", str(exc))


class ClipDialog(QDialog):
    """Create a research clipping: text, note, tags and topic."""

    def __init__(self, ctx: AppContext, text: str, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("Save research clipping")
        lay = QVBoxLayout(self)
        lay.addWidget(label("Verbatim text (as in the source)", "h2"))
        self.text = QPlainTextEdit(text); self.text.setMinimumHeight(110); lay.addWidget(self.text)
        lay.addWidget(label("My note (kept separate from the quotation)", "h2"))
        self.note = QPlainTextEdit(); self.note.setMaximumHeight(80); lay.addWidget(self.note)
        form = QFormLayout()
        self.topic = QComboBox(); self.topic.setEditable(True)
        self.topic.addItem("(none)", None)
        for t in ctx.research.topics():
            self.topic.addItem(t["title"], t["id"])
        self.topic.setToolTip("Pick an existing topic or type a new topic name")
        self.tags = QLineEdit(); self.tags.setPlaceholderText("tags, separated by ; (use / for sub-tags)")
        form.addRow("Add to topic", self.topic); form.addRow("Tags", self.tags)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel); bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb); self.resize(560, 420)

    def topic_id(self) -> int | None:
        name = self.topic.currentText().strip()
        if not name or name == "(none)":
            return None
        tid = self.ctx.research.topic_by_title(name)
        return tid if tid else self.ctx.research.create_topic(name)

    def tag_ids(self) -> list[int]:
        return [self.ctx.tags.get_or_create_path(t) for t in self.tags.text().split(";") if t.strip()]

    @staticmethod
    def make_clipping(ctx: AppContext, parent, text: str, book_id: int | None, locator: dict) -> int | None:
        d = ClipDialog(ctx, text, parent)
        if not d.exec():
            return None
        try:
            cid = ctx.research.create_clipping(d.text.toPlainText(), book_id, locator, d.note.toPlainText(), d.tag_ids(), d.topic_id())
        except (ValueError, KeyError) as exc:
            warn(parent, "Clipping", str(exc)); return None
        ctx.notes_changed.emit(); ctx.tags_changed.emit()
        ctx.message.emit("Clipping saved with its source reference.")
        return cid
