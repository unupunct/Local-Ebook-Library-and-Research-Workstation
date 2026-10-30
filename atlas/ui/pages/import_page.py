"""Import and Library Sources: roots, scanning progress, failures, index maintenance."""
from __future__ import annotations

import os
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QFileDialog, QFormLayout, QGridLayout, QGroupBox,
                               QHBoxLayout, QHeaderView, QLabel, QProgressBar, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget, QPlainTextEdit, QDialog, QDialogButtonBox, QTextBrowser)

from ... import backup as backup_mod, ocr as ocr_mod, storage
from ...formats import FORMATS
from ..common import STATUS_TEXT, button, confirm, hbox, human_size, info, label, warn
from ..context import AppContext


class SourcesPanel(QWidget):
    """List of library roots with add / remove / rescan actions."""

    def __init__(self, ctx: AppContext, compact: bool = False) -> None:
        super().__init__()
        self.ctx = ctx
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Source", "Path", "Status", "Books", "Last scan", "Subfolders"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setAccessibleName("Library sources")
        lay.addWidget(self.table)
        row = [button("Add folder or drive…", self.add, True, "Choose a folder, drive or network share to import"),
               button("Remove", self.remove, tip="Stops scanning this source. Book records are kept."),
               button("Rescan selected", lambda: self.rescan(True)), button("Rescan all", lambda: self.rescan(False)),
               button("Check availability", self.check, tip="Re-check drives, follow drive-letter changes and reconnect books"), None]
        lay.addLayout(hbox(*row))
        ctx.roots_changed.connect(self.refresh)
        ctx.library_changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        lib = self.ctx.lib
        roots = lib.roots()
        self.table.setRowCount(len(roots))
        for i, r in enumerate(roots):
            n = lib.db.conn.execute("SELECT COUNT(*) FROM books WHERE root_id=?", (r["id"],)).fetchone()[0]
            st = "Disabled" if not r["enabled"] else ("Online" if r["online"] else "OFFLINE")
            if r["kind"] == "network":
                st += " (network)"
            vals = [r["label"], r["path"], st, f"{n:,}", (r["last_scan"] or "never")[:16], "yes" if r["recursive"] else "no"]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 0:
                    it.setData(Qt.UserRole, r["id"])
                if not r["online"] and r["enabled"] and c == 2:
                    it.setForeground(Qt.red)
                self.table.setItem(i, c, it)
        self.table.resizeColumnToContents(0)

    def selected_root(self) -> int | None:
        rows = self.table.selectionModel().selectedRows()
        return self.table.item(rows[0].row(), 0).data(Qt.UserRole) if rows else None

    def add(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose a folder or drive containing books")
        if not path:
            return
        try:
            rid = self.ctx.lib.add_root(path)
        except ValueError as exc:
            warn(self, "Cannot add this source", str(exc))
            return
        self.ctx.roots_changed.emit()
        if confirm(self, "Scan now?", f"Added '{path}'. Scan and index it now? You can also do this later.", "Scan now"):
            self.ctx.start_import("scan", [rid])

    def remove(self) -> None:
        rid = self.selected_root()
        if rid is None:
            return
        if confirm(self, "Remove source", "Stop using this source? Its books stay in your library as offline records "
                   "(notes, annotations and progress are kept). Files on disk are never touched.", "Remove source"):
            self.ctx.lib.remove_root(rid)
            self.ctx.roots_changed.emit(); self.ctx.library_changed.emit()

    def rescan(self, selected: bool) -> None:
        rid = self.selected_root() if selected else None
        if selected and rid is None:
            info(self, "Select a source", "Select a source in the list first.")
            return
        self.ctx.start_import("scan", [rid] if rid else None, force=False)

    def check(self) -> None:
        def work():
            return self.ctx.lib.check_roots()

        def done(events):
            msg = []
            for e in events:
                if e["event"] == "drive_letter_changed":
                    msg.append(f"Drive letter changed: {e['old']} → {e['new']} (followed automatically)")
                elif e["event"] == "reconnected":
                    msg.append("A source came back online; its books were reconnected.")
                elif e["event"] == "went_offline":
                    msg.append("A source is offline; its books are marked offline.")
            self.ctx.roots_changed.emit(); self.ctx.library_changed.emit()
            info(self, "Availability", "\n".join(msg) or "Nothing changed.")
        self.ctx.run_async(work, done)


class FailureDialog(QDialog):
    def __init__(self, parent, rows) -> None:
        super().__init__(parent)
        self.setWindowTitle("Details")
        lay = QVBoxLayout(self)
        t = QTextBrowser(); t.setPlainText(rows); lay.addWidget(t)
        bb = QDialogButtonBox(QDialogButtonBox.Close); bb.rejected.connect(self.reject); bb.accepted.connect(self.accept)
        lay.addWidget(bb)
        self.resize(640, 400)


class ImportPage(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.setObjectName("page")
        self.ctx = ctx
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 10)
        lay.addWidget(label("Import and Indexing", "h1"))
        lay.addWidget(label("Books stay where they are. Atlas Library only reads them and keeps its own database. "
                            "Rescans are incremental: unchanged files are skipped.", "muted", True))
        g = QGroupBox("Library sources")
        gl = QVBoxLayout(g)
        self.sources = SourcesPanel(ctx)
        gl.addWidget(self.sources)
        lay.addWidget(g)

        pg = QGroupBox("Progress")
        pl = QVBoxLayout(pg)
        self.phase = label("Idle")
        self.bar = QProgressBar()
        self.cur = label("", "muted")
        self.counts = label("")
        pl.addWidget(self.phase); pl.addWidget(self.bar); pl.addWidget(self.cur); pl.addWidget(self.counts)
        self.btns = [button("Resume / index pending", self.resume, True), button("Scan all sources", lambda: ctx.start_import("scan")),
                     button("Full rescan (re-read all)", self.force_scan), button("Reindex everything", self.reindex_all),
                     button("Retry failed", lambda: ctx.start_import("retry"))]
        self.cancel_btn = button("Cancel", ctx.cancel_import, danger=True)
        self.cancel_btn.setEnabled(False)
        pl.addLayout(hbox(*self.btns, self.cancel_btn, None))
        lay.addWidget(pg)

        fg = QGroupBox("Files needing attention")
        fl = QVBoxLayout(fg)
        self.fail = QTableWidget(0, 4)
        self.fail.setHorizontalHeaderLabels(["File", "Status", "Reason", "Location"])
        self.fail.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.fail.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.fail.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.fail.verticalHeader().hide()
        self.fail.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        fl.addWidget(self.fail)
        fl.addLayout(hbox(button("Retry selected", self.retry_selected), button("Run OCR on selected PDF", self.run_ocr, tip="Needs Tesseract; see Settings > OCR"),
                          button("Purge text index of offline books…", self.purge_offline, tip="Removes only extracted text; notes and annotations stay"), None))
        lay.addWidget(fg, 1)

        mg = QGroupBox("Index maintenance")
        ml = QHBoxLayout(mg)
        ml.addWidget(button("Check index integrity", self.check_index))
        ml.addWidget(button("Repair / rebuild search index", self.repair_index))
        ml.addWidget(button("Optimise", self.optimise))
        ml.addStretch(1)
        self.fmt_label = label("Supported: " + ", ".join(f.label for f in FORMATS if f.support == "supported") +
                               ".  Planned (not yet readable): " + ", ".join(f.label for f in FORMATS if f.support == "planned"), "muted", True)
        ml.addWidget(self.fmt_label, 3)
        lay.addWidget(mg)

        ctx.progress.connect(self.on_progress)
        ctx.import_running.connect(self.on_running)
        ctx.library_changed.connect(self.refresh_failures)
        self.refresh_failures()
        self._t = 0.0

    # ----------------------------------------------------------------- state
    def on_running(self, running: bool) -> None:
        for b in self.btns:
            b.setEnabled(not running)
        self.cancel_btn.setEnabled(running)
        if not running:
            self.refresh_failures()

    def on_progress(self, p) -> None:
        if time.time() - self._t < 0.1 and p.phase not in ("done", "cancelled"):
            return
        self._t = time.time()
        name = {"discovering": "Scanning folders…", "indexing": "Extracting and indexing text…", "done": "Finished", "cancelled": "Cancelled — unfinished books stay queued and resume later"}.get(p.phase, p.phase)
        self.phase.setText(name)
        total = p.processed + p.remaining
        if p.phase == "discovering":
            self.bar.setRange(0, 0)
        else:
            self.bar.setRange(0, max(total, 1)); self.bar.setValue(p.processed if total else 1)
        self.cur.setText(p.current)
        self.counts.setText(f"Discovered {p.discovered:,} · new {p.new:,} · changed {p.changed:,} · moved {p.moved:,} · processed {p.processed:,} · "
                            f"skipped (unchanged) {p.skipped:,} · failed {p.failed:,} · remaining {p.remaining:,} · missing {p.missing:,}"
                            + (f"\n{p.note}" if p.note else ""))

    def resume(self) -> None:
        self.ctx.start_import("resume")

    def force_scan(self) -> None:
        if confirm(self, "Full rescan", "Re-read every file in every source. This can take a long time for large libraries.", "Rescan"):
            self.ctx.start_import("scan", force=True)

    def reindex_all(self) -> None:
        if confirm(self, "Reindex everything", "Rebuild the text index for all books. Your metadata, tags and notes are kept.", "Reindex"):
            self.ctx.start_import("reindex_all")

    # -------------------------------------------------------------- failures
    def refresh_failures(self) -> None:
        rows = self.ctx.db.conn.execute(
            "SELECT b.id, b.original_filename, b.index_status, b.index_detail, b.rel_path, b.format FROM books b "
            "WHERE b.index_status IN ('failed','encrypted','unsupported','ocr_needed') ORDER BY b.index_status, b.original_filename LIMIT 2000").fetchall()
        self.fail.setRowCount(len(rows))
        for i, r in enumerate(rows):
            vals = [r["original_filename"], STATUS_TEXT.get(r["index_status"], r["index_status"]), r["index_detail"], os.path.dirname(r["rel_path"])]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 0:
                    it.setData(Qt.UserRole, (r["id"], r["format"]))
                self.fail.setItem(i, c, it)
        self.fail.resizeColumnToContents(0)

    def _sel(self) -> list[tuple[int, str]]:
        return [self.fail.item(i.row(), 0).data(Qt.UserRole) for i in self.fail.selectionModel().selectedRows()]

    def retry_selected(self) -> None:
        ids = [b for b, _ in self._sel()]
        if ids:
            self.ctx.start_import("books", book_ids=ids)

    def run_ocr(self) -> None:
        pdfs = [b for b, f in self._sel() if f == "pdf"]
        if not pdfs:
            info(self, "OCR", "Select one or more PDF files first.")
            return
        s = self.ctx.settings

        def work():
            out = []
            for b in pdfs:
                out.append(ocr_mod.ocr_book(self.ctx.lib, b, s.ocr_language, s.tesseract_path, s.path_ocr_dir(), s.ocr_min_chars))
            return out
        self.ctx.message.emit("OCR started in the background…")
        self.ctx.run_async(work, lambda r: (self.ctx.library_changed.emit(), info(self, "OCR finished", f"OCR added text to {sum(x['pages_ocr'] for x in r)} page(s).")),
                           lambda e: warn(self, "OCR could not run", str(e)))

    def purge_offline(self) -> None:
        ids = [r[0] for r in self.ctx.db.conn.execute("SELECT id FROM books WHERE available=0")]
        if not ids:
            info(self, "Nothing to purge", "There are no offline books.")
            return
        if confirm(self, "Purge text index", f"Remove the searchable text of {len(ids)} offline book(s)? Search will no longer find passages in them "
                   "until they are re-indexed. Metadata, notes, annotations and clippings are NOT removed.", "Remove text index"):
            n = self.ctx.lib.purge_index(ids)
            self.ctx.library_changed.emit()
            info(self, "Done", f"Removed {n:,} text passages.")

    # ------------------------------------------------------------ maintenance
    def check_index(self) -> None:
        def done(f):
            lines = [f"Search index (FTS5): {'OK' if f['fts_ok'] else 'PROBLEM — ' + f['fts_message']}",
                     f"Main database: {'OK' if f['main_ok'] else 'PROBLEM'}",
                     f"Orphan text passages: {f['orphan_segments']}", f"Books marked indexed but without text: {f['indexed_without_text']}"]
            if not f["ok"]:
                lines.append("\nUse 'Repair / rebuild search index' to fix this.")
            info(self, "Index check", "\n".join(lines))
        self.ctx.run_async(lambda: backup_mod.check_index(self.ctx.db), done)

    def repair_index(self) -> None:
        if not confirm(self, "Repair index", "Rebuild the full-text index from the stored text and re-queue books whose text is missing.", "Repair"):
            return
        self.ctx.run_async(lambda: backup_mod.repair_index(self.ctx.db),
                           lambda r: (self.ctx.library_changed.emit(), info(self, "Repair finished",
                                      f"Orphans removed: {r['orphans_removed']}\nBooks re-queued: {r['requeued']}\nUse 'Resume / index pending' to process them.")))

    def optimise(self) -> None:
        self.ctx.run_async(lambda: backup_mod.optimize(self.ctx.db), lambda _: info(self, "Optimise", "Index optimised."))
