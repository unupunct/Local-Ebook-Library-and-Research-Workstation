"""Settings, About and database relocation."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QTabWidget, QVBoxLayout,
                               QWidget, QDialog, QDialogButtonBox, QTableWidget, QTableWidgetItem, QHeaderView)

from ... import APP_NAME, __version__, backup as backup_mod, ocr as ocr_mod, storage
from ...formats import FORMATS
from ...paths import bootstrap_settings_file, default_data_dir
from ...semantic import semantic_status
from ...settings import HOME_SECTIONS
from ..common import button, confirm, hbox, info, label, warn
from ..context import AppContext
from .home import TITLES
from .import_page import SourcesPanel


class PathEdit(QWidget):
    def __init__(self, value: str, placeholder: str) -> None:
        super().__init__()
        lay = QHBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit(value); self.edit.setPlaceholderText(placeholder)
        lay.addWidget(self.edit, 1)
        lay.addWidget(button("Browse…", self.browse))
        lay.addWidget(button("Open", self.open, tip="Open this folder in Explorer"))

    def browse(self) -> None:
        p = QFileDialog.getExistingDirectory(self, "Choose folder", self.edit.text() or str(Path.home()))
        if p:
            self.edit.setText(p)

    def open(self) -> None:
        p = self.edit.text() or self.edit.placeholderText()
        if os.path.isdir(p):
            QDesktopServices.openUrl(QUrl.fromLocalFile(p))

    def text(self) -> str:
        return self.edit.text().strip()


class SettingsPage(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.setObjectName("page")
        self.ctx = ctx
        s = ctx.settings
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 10)
        lay.addWidget(label("Settings", "h1"))
        tabs = QTabWidget()
        lay.addWidget(tabs, 1)

        # ---- General
        g = QWidget(); gf = QFormLayout(g)
        self.theme = QComboBox(); [self.theme.addItem(t.capitalize(), t) for t in ("dark", "light")]; self.theme.setCurrentIndex(self.theme.findData(s.theme))
        self.start = QComboBox()
        for lbl, k in (("Home", "home"), ("Library", "library"), ("Research", "research"), ("Reader", "reader")):
            self.start.addItem(lbl, k)
        self.start.setCurrentIndex(max(0, self.start.findData(s.start_page)))
        self.reopen = QCheckBox("Reopen the last book on startup"); self.reopen.setChecked(s.reopen_last_book)
        self.loglevel = QComboBox(); [self.loglevel.addItem(l) for l in ("DEBUG", "INFO", "WARNING", "ERROR")]; self.loglevel.setCurrentText(s.log_level)
        gf.addRow("Theme (applies immediately)", self.theme); gf.addRow("Start page", self.start); gf.addRow(self.reopen); gf.addRow("Log level", self.loglevel)
        gf.addRow(label("Home screen sections", "h2"))
        self.home_checks = {}
        for k in HOME_SECTIONS:
            cb = QCheckBox(TITLES[k]); cb.setChecked(k in s.home_sections); self.home_checks[k] = cb; gf.addRow(cb)
        tabs.addTab(g, "General")

        # ---- Library sources
        src = QWidget(); sl = QVBoxLayout(src)
        sl.addWidget(label("Original books stay where they are — on an internal drive, USB disk or (later) a network share. "
                           "Atlas Library never moves or modifies them.", "muted", True))
        sl.addWidget(SourcesPanel(ctx))
        tabs.addTab(src, "Library sources")

        # ---- Locations
        loc = QWidget(); lf = QFormLayout(loc)
        self.p_data = PathEdit(s.data_dir, str(default_data_dir()))
        self.p_db = PathEdit(s.db_dir, str(s.path_db_dir()))
        self.p_index = PathEdit(s.index_dir, str(s.path_index_dir()))
        self.p_cache = PathEdit(s.cache_dir, str(s.path_cache_dir()))
        self.p_ocr = PathEdit(s.ocr_dir, str(s.path_ocr_dir()))
        self.p_log = PathEdit(s.log_dir, str(s.path_log_dir()))
        self.p_backup = PathEdit(s.backup_dir, str(s.path_backup_dir()))
        for lbl, w in (("Application data", self.p_data), ("Database", self.p_db), ("Search index", self.p_index), ("Cover cache", self.p_cache),
                       ("OCR output", self.p_ocr), ("Logs", self.p_log), ("Backups", self.p_backup)):
            lf.addRow(lbl, w)
        lf.addRow(label("Empty = default location under the data folder. Changes take effect after a restart. Use 'Move database…' to carry existing data "
                        "to a new database/index folder; the old files are kept as a safety copy.", "muted", True))
        lf.addRow(hbox(button("Move database and index…", self.move_db, tip="Copies atlas.db and index.db to a new folder"), None))
        tabs.addTab(loc, "Locations")

        # ---- Reading and search
        rs = QWidget(); rf = QFormLayout(rs)
        self.autosave = QCheckBox("Automatically save reading position"); self.autosave.setChecked(s.autosave_position)
        self.rstats = QCheckBox("Record reading time statistics"); self.rstats.setChecked(s.reading_stats)
        self.limit = QSpinBox(); self.limit.setRange(10, 5000); self.limit.setValue(s.result_limit)
        self.perbook = QSpinBox(); self.perbook.setRange(1, 50); self.perbook.setValue(s.passages_per_book)
        self.group = QCheckBox("Group search results by book"); self.group.setChecked(s.group_by_book)
        self.hist = QSpinBox(); self.hist.setRange(0, 1000); self.hist.setValue(s.history_limit)
        rf.addRow(self.autosave); rf.addRow(self.rstats); rf.addRow(label("Reader appearance: use the Aa button inside the Reader.", "muted"))
        rf.addRow("Default result limit", self.limit); rf.addRow("Passages shown per book", self.perbook); rf.addRow(self.group); rf.addRow("Search history size", self.hist)
        tabs.addTab(rs, "Reading and search")

        # ---- Indexing
        ix = QWidget(); xf = QFormLayout(ix)
        self.threads = QSpinBox(); self.threads.setRange(1, 8); self.threads.setValue(s.index_threads)
        self.threads.setToolTip("PDF parsing is serialised for stability; other formats run in parallel")
        self.hidden = QCheckBox("Skip hidden folders and files"); self.hidden.setChecked(s.skip_hidden)
        self.resume = QCheckBox("Resume unfinished indexing at startup"); self.resume.setChecked(s.resume_on_start)
        self.sched = QSpinBox(); self.sched.setRange(0, 10080); self.sched.setSuffix(" min"); self.sched.setSpecialValueText("off"); self.sched.setValue(s.scheduled_scan_minutes)
        self.exdirs = QPlainTextEdit("\n".join(s.exclude_dirs)); self.exdirs.setMaximumHeight(90)
        self.expat = QPlainTextEdit("\n".join(s.exclude_patterns)); self.expat.setMaximumHeight(90)
        self.deleted = QComboBox(); self.deleted.addItem("Mark as unavailable (keep text index)", "mark"); self.deleted.addItem("Ask before removing the text index", "ask_remove_index")
        self.deleted.setCurrentIndex(max(0, self.deleted.findData(s.on_source_deleted)))
        xf.addRow("Indexing threads", self.threads); xf.addRow(self.hidden); xf.addRow(self.resume); xf.addRow("Scheduled rescan every", self.sched)
        xf.addRow("Excluded folder names (one per line, wildcards ok)", self.exdirs); xf.addRow("Excluded file patterns", self.expat)
        xf.addRow("When a source file disappears", self.deleted)
        tabs.addTab(ix, "Indexing")

        # ---- OCR
        oc = QWidget(); of = QFormLayout(oc)
        self.ocr_lang = QLineEdit(s.ocr_language); self.ocr_lang.setToolTip("Tesseract language code(s), e.g. eng or eng+ron")
        self.ocr_min = QSpinBox(); self.ocr_min.setRange(1, 500); self.ocr_min.setValue(s.ocr_min_chars)
        self.tess = PathEdit(s.tesseract_path, "auto-detect"); self.tess.edit.setPlaceholderText("auto-detect tesseract.exe")
        self.ocr_status = label("", "muted", True)
        of.addRow("OCR language", self.ocr_lang); of.addRow("Pages with fewer characters are OCR candidates", self.ocr_min)
        of.addRow("Tesseract path", self.tess); of.addRow(self.ocr_status)
        of.addRow(hbox(button("Detect Tesseract", self.detect_ocr), None))
        of.addRow(label("OCR is optional and separate from normal indexing; it never changes the PDF. Start it from Import and Indexing for selected PDFs.", "muted", True))
        tabs.addTab(oc, "OCR")
        self.detect_ocr()

        # ---- Backup
        bk = QWidget(); bf = QFormLayout(bk)
        self.b_days = QSpinBox(); self.b_days.setRange(0, 365); self.b_days.setSpecialValueText("off"); self.b_days.setSuffix(" days"); self.b_days.setValue(s.backup_every_days)
        self.b_keep = QSpinBox(); self.b_keep.setRange(1, 100); self.b_keep.setValue(s.backup_keep)
        self.b_idx = QCheckBox("Include the search index (larger; otherwise it is rebuilt after restore)"); self.b_idx.setChecked(s.backup_include_index)
        bf.addRow("Automatic backup every", self.b_days); bf.addRow("Backups to keep", self.b_keep); bf.addRow(self.b_idx)
        bf.addRow(hbox(button("Back up now", self.backup_now, True), button("Restore from backup…", self.restore), button("Open backup folder", lambda: self.p_backup.open()), None))
        bf.addRow(label("Backups contain metadata, tags, collections, annotations, clippings, notes, saved searches and reading progress. "
                        "They do NOT contain your book files — back those up separately.", "muted", True))
        tabs.addTab(bk, "Backup")

        # ---- Metadata + semantic
        ms = QWidget(); mf = QFormLayout(ms)
        self.online = QCheckBox("Allow optional online metadata lookup (Open Library)"); self.online.setChecked(s.online_metadata_enabled)
        mf.addRow(self.online)
        mf.addRow(label("When enabled, a 'Look up online' button appears in book details. Only the ISBN or title/author you see is sent, after you confirm; "
                        "no book content is ever sent. Core features never need the internet.", "muted", True))
        from ...semantic import provider_names
        self.sem = QCheckBox("Enable semantic search (optional, local)"); self.sem.setChecked(s.semantic_enabled)
        self.sem_provider = QComboBox(); self.sem_provider.addItem("(none)", "")
        for n in provider_names():
            self.sem_provider.addItem(n, n)
        self.sem_provider.setCurrentIndex(max(0, self.sem_provider.findData(s.semantic_provider)))
        self.sem_model = PathEdit(s.semantic_model_path, "path to a local embedding model")
        self.sem_status = label("", "muted", True)
        self.sem_progress = label("", "muted")
        mf.addRow(self.sem); mf.addRow("Provider", self.sem_provider); mf.addRow("Local model folder", self.sem_model); mf.addRow(self.sem_status)
        mf.addRow(hbox(button("Build / update semantic index", self.build_semantic, True, "Embeds every indexed passage locally; can take hours for a large library"),
                       button("Delete semantic index", self.delete_semantic), None))
        mf.addRow(self.sem_progress)
        mf.addRow(label("Needs the optional packages sentence-transformers and numpy (run from source) and a downloaded model folder. "
                        "Vectors are stored in semantic.db next to the main database; keyword search never depends on it. See docs/SEMANTIC_SEARCH.md.", "muted", True))
        self.sem.toggled.connect(self.update_sem)
        self.sem_provider.currentIndexChanged.connect(self.update_sem)
        self.sem_model.edit.textChanged.connect(self.update_sem)
        self.update_sem()
        tabs.addTab(ms, "Metadata and semantic search")

        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(button("Apply settings", self.apply, True))
        lay.addLayout(row)

    def update_sem(self, *_a) -> None:
        st = semantic_status(self.sem.isChecked(), self.sem_provider.currentData() or "", self.sem_model.text())
        self.sem_status.setText(("✔ " if st.available else "ℹ ") + st.message)

    def _sem_apply(self) -> None:
        s = self.ctx.settings
        s.semantic_enabled, s.semantic_provider, s.semantic_model_path = self.sem.isChecked(), self.sem_provider.currentData() or "", self.sem_model.text()
        s.save()

    def build_semantic(self) -> None:
        self._sem_apply()
        svc = self.ctx.semantic_service()
        if svc is None:
            warn(self, "Semantic search", self.ctx.semantic_status().message)
            return
        if not confirm(self, "Build semantic index", "Embed all indexed passages with the local model. This may take a long time and a lot of CPU. "
                       "You can keep using the app. Continue?", "Build"):
            return
        self.sem_progress.setText("Building…")
        self.ctx.run_async(lambda: svc.build(),
                           lambda r: self.sem_progress.setText(f"Done: {r['embedded']:,} passages embedded, {r['remaining']:,} remaining."),
                           lambda e: (self.sem_progress.setText(""), warn(self, "Semantic index", str(e))))

    def delete_semantic(self) -> None:
        svc = self.ctx.semantic_service()
        if svc and confirm(self, "Delete semantic index", "Remove all stored vectors? Keyword search is not affected.", "Delete"):
            svc.delete_all()
            self.sem_progress.setText("Semantic index deleted.")

    def detect_ocr(self) -> None:
        t = ocr_mod.find_tesseract(self.tess.text())
        if not t:
            self.ocr_status.setText("Tesseract was not found. OCR is unavailable until it is installed (the rest of the app works normally).")
        else:
            langs = ocr_mod.available_languages(t)
            self.ocr_status.setText(f"Found: {t}\nInstalled languages: {', '.join(langs) or 'unknown'}")

    def apply(self) -> None:
        s = self.ctx.settings
        old_theme = s.theme
        s.theme = self.theme.currentData(); s.start_page = self.start.currentData(); s.reopen_last_book = self.reopen.isChecked()
        s.log_level = self.loglevel.currentText()
        s.home_sections = [k for k in HOME_SECTIONS if self.home_checks[k].isChecked()]
        changed_paths = (s.data_dir, s.db_dir, s.index_dir) != (self.p_data.text(), self.p_db.text(), self.p_index.text())
        s.data_dir, s.db_dir, s.index_dir = self.p_data.text(), self.p_db.text(), self.p_index.text()
        s.cache_dir, s.ocr_dir, s.log_dir, s.backup_dir = self.p_cache.text(), self.p_ocr.text(), self.p_log.text(), self.p_backup.text()
        s.autosave_position = self.autosave.isChecked(); s.reading_stats = self.rstats.isChecked()
        s.result_limit, s.passages_per_book, s.group_by_book, s.history_limit = self.limit.value(), self.perbook.value(), self.group.isChecked(), self.hist.value()
        s.index_threads, s.skip_hidden, s.resume_on_start, s.scheduled_scan_minutes = self.threads.value(), self.hidden.isChecked(), self.resume.isChecked(), self.sched.value()
        s.exclude_dirs = [x.strip() for x in self.exdirs.toPlainText().splitlines() if x.strip()]
        s.exclude_patterns = [x.strip() for x in self.expat.toPlainText().splitlines() if x.strip()]
        s.on_source_deleted = self.deleted.currentData()
        s.ocr_language, s.ocr_min_chars, s.tesseract_path = self.ocr_lang.text().strip() or "eng", self.ocr_min.value(), self.tess.text()
        s.backup_every_days, s.backup_keep, s.backup_include_index = self.b_days.value(), self.b_keep.value(), self.b_idx.isChecked()
        s.online_metadata_enabled = self.online.isChecked(); s.semantic_enabled = self.sem.isChecked(); s.semantic_model_path = self.sem_model.text()
        s.semantic_provider = self.sem_provider.currentData() or ""
        s.save()
        from ..theme import stylesheet
        from PySide6.QtWidgets import QApplication
        QApplication.instance().setStyleSheet(stylesheet(s.theme))
        self.ctx.library_changed.emit(); self.ctx.notes_changed.emit()
        rp = getattr(self.window(), "pages", {}).get("research")
        if rp is not None:
            rp.update_semantic_state()
        msg = "Settings saved."
        if changed_paths:
            msg += "\n\nLocation changes take effect after you restart Atlas Library."
        info(self, "Settings", msg)

    # --------------------------------------------------------------- backup
    def backup_now(self) -> None:
        self.apply_quiet()
        self.ctx.backup_now(lambda p: info(self, "Backup complete", f"Backup written to:\n{p}"))

    def apply_quiet(self) -> None:
        s = self.ctx.settings
        s.backup_dir = self.p_backup.text(); s.backup_keep = self.b_keep.value(); s.backup_include_index = self.b_idx.isChecked()

    def restore(self) -> None:
        start = str(self.ctx.settings.path_backup_dir())
        path, _ = QFileDialog.getOpenFileName(self, "Choose a backup", start, "Atlas backups (*.zip)")
        if not path:
            return
        try:
            m = backup_mod.verify_backup(Path(path))
        except backup_mod.BackupError as exc:
            warn(self, "Cannot restore", str(exc)); return
        if not confirm(self, "Restore backup",
                       f"Restore the backup created {m.get('created', '?')}?\n\nThis REPLACES your current library database (a safety copy is kept next to it). "
                       "Atlas Library will close afterwards; start it again to continue. Book files are not touched.", "Restore and close"):
            return
        db_path, idx_path = self.ctx.db.db_path, self.ctx.db.index_path
        self.ctx.shutdown()
        try:
            backup_mod.restore_backup(Path(path), db_path, idx_path)
        except Exception as exc:
            warn(self, "Restore failed", f"{exc}\n\nYour previous database was kept."); return
        info(self, "Restore complete", "The backup was restored. Atlas Library will now close; please start it again.")
        from PySide6.QtWidgets import QApplication
        QApplication.quit()

    def move_db(self) -> None:
        dest = QFileDialog.getExistingDirectory(self, "Move database and index to…")
        if not dest:
            return
        d = Path(dest)
        if (d / "atlas.db").exists():
            warn(self, "Move database", "That folder already contains an atlas.db. Choose an empty folder."); return
        if not confirm(self, "Move database", f"Copy the database and search index to\n{dest}\nand use that location from the next start? The current files are kept.", "Copy and switch"):
            return
        db_path, idx_path = self.ctx.db.db_path, self.ctx.db.index_path
        try:
            from ...backup import _copy_db
            _copy_db(self.ctx.db.conn, "main", d / "atlas.db"); _copy_db(self.ctx.db.conn, "idx", d / "index.db")
        except Exception as exc:
            warn(self, "Move failed", str(exc)); return
        s = self.ctx.settings
        s.db_dir = dest; s.index_dir = dest; s.save()
        self.p_db.edit.setText(dest); self.p_index.edit.setText(dest)
        info(self, "Database copied", "The database now lives in the new folder from the next start. Please restart Atlas Library. The old files were left in place as a safety copy.")


class AboutDialog(QDialog):
    def __init__(self, ctx: AppContext, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        lay = QVBoxLayout(self)
        lay.addWidget(label(f"{APP_NAME}", "h1"))
        lay.addWidget(label(f"Version {__version__} — a local ebook library and research workstation. No account, cloud or AI service required.", wrap=True))
        s = ctx.settings
        lay.addWidget(label(f"<b>Application data:</b> {s._base()}<br><b>Database:</b> {ctx.db.db_path}<br><b>Search index:</b> {ctx.db.index_path}<br>"
                            f"<b>Logs:</b> {s.path_log_dir()}<br><b>Backups:</b> {s.path_backup_dir()}<br><b>Settings file:</b> {bootstrap_settings_file()}", wrap=True))
        lay.itemAt(lay.count() - 1).widget().setTextFormat(Qt.RichText)
        tbl = QTableWidget(len(FORMATS), 3); tbl.setHorizontalHeaderLabels(["Format", "Support", "Notes"]); tbl.verticalHeader().hide()
        tbl.setEditTriggers(QTableWidget.NoEditTriggers); tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        for i, f in enumerate(FORMATS):
            for c, v in enumerate((f.label, "Supported" if f.support == "supported" else "Planned (not yet)", f.notes)):
                tbl.setItem(i, c, QTableWidgetItem(v))
        lay.addWidget(tbl)
        lay.addLayout(hbox(button("Open log folder", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(s.path_log_dir())))),
                           button("Open data folder", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(s._base())))), None))
        bb = QDialogButtonBox(QDialogButtonBox.Close); bb.rejected.connect(self.reject); lay.addWidget(bb)
        self.resize(640, 520)
