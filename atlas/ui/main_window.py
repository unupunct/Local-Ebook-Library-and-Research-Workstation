"""Main window: sidebar navigation, global search, status bar, startup tasks."""
from __future__ import annotations

import logging
import os
import sys

from PySide6.QtCore import QByteArray, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QMessageBox, QProgressBar, QStackedWidget, QToolButton, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__, backup as backup_mod
from ..library import BookFilter
from .common import confirm, info, label, warn
from .context import AppContext
from .pages.collections_page import CollectionsPage
from .pages.home import HomePage
from .pages.import_page import ImportPage
from .pages.library import LibraryPage
from .pages.notes_page import HistoryPage, NotesPage
from .pages.reader import ReaderPage
from .pages.research import ResearchPage
from .pages.settings_page import AboutDialog, SettingsPage
from .pages.tags_page import TagsPage

log = logging.getLogger(__name__)

NAV = [("home", "Home"), ("library", "Library"), ("research", "Research"), ("reader", "Reader"), ("collections", "Collections"),
       ("notes", "Notes"), ("history", "Reading History"), ("import", "Import and Indexing"), ("tags", "Tags and Categories"), ("settings", "Settings")]


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext, icon: QIcon | None = None) -> None:
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle(APP_NAME)
        if icon:
            self.setWindowIcon(icon)
        self.resize(1360, 860)
        central = QWidget()
        self.setCentralWidget(central)
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # sidebar
        self.sidebar = QFrame(); self.sidebar.setObjectName("sidebar"); self.sidebar.setFixedWidth(210)
        sl = QVBoxLayout(self.sidebar); sl.setContentsMargins(0, 12, 0, 8)
        title = label(APP_NAME, "h2"); title.setContentsMargins(16, 0, 0, 8)
        sl.addWidget(title)
        self.nav = QListWidget(); self.nav.setObjectName("nav"); self.nav.setAccessibleName("Main navigation")
        for i, (key, text) in enumerate(NAV):
            it = QListWidgetItem(text); it.setData(Qt.UserRole, key); it.setToolTip(f"{text}  (Ctrl+{(i + 1) % 10})"); self.nav.addItem(it)
        self.nav.currentRowChanged.connect(self._nav_changed)
        sl.addWidget(self.nav, 1)
        self.offline_badge = label("", "muted", True); self.offline_badge.setContentsMargins(14, 0, 10, 0)
        sl.addWidget(self.offline_badge)
        outer.addWidget(self.sidebar)

        # right side
        right = QWidget(); rl = QVBoxLayout(right); rl.setContentsMargins(0, 0, 0, 0); rl.setSpacing(0)
        self.topbar = QWidget(); tb = QHBoxLayout(self.topbar); tb.setContentsMargins(12, 8, 12, 4)
        self.tog = QToolButton(); self.tog.setText("☰"); self.tog.setToolTip("Show or hide the sidebar (Ctrl+B)"); self.tog.setAccessibleName("Toggle sidebar")
        self.tog.clicked.connect(self.toggle_sidebar)
        tb.addWidget(self.tog)
        self.search = QLineEdit(); self.search.setPlaceholderText("Search all books…  (Ctrl+E)   e.g.  \"passive solar\" greenhouse")
        self.search.setClearButtonEnabled(True); self.search.setAccessibleName("Global search")
        self.search.returnPressed.connect(self.global_search)
        tb.addWidget(self.search, 1)
        rl.addWidget(self.topbar)
        self.stack = QStackedWidget(); rl.addWidget(self.stack, 1)
        outer.addWidget(right, 1)

        self.pages = {
            "home": HomePage(ctx), "library": LibraryPage(ctx), "research": ResearchPage(ctx), "reader": ReaderPage(ctx),
            "collections": CollectionsPage(ctx), "notes": NotesPage(ctx), "history": HistoryPage(ctx), "import": ImportPage(ctx),
            "tags": TagsPage(ctx), "settings": SettingsPage(ctx)}
        for k, _ in NAV:
            self.stack.addWidget(self.pages[k])

        # status bar
        sb = self.statusBar()
        self.msg = QLabel(""); sb.addWidget(self.msg, 1)
        self.mini = QProgressBar(); self.mini.setFixedWidth(180); self.mini.setTextVisible(False); self.mini.hide()
        self.mini_label = QLabel(""); sb.addPermanentWidget(self.mini_label); sb.addPermanentWidget(self.mini)

        # shortcuts and menus
        for i in range(len(NAV)):
            QShortcut(QKeySequence(f"Ctrl+{(i + 1) % 10}"), self, activated=lambda i=i: self.nav.setCurrentRow(i))
        QShortcut(QKeySequence("Ctrl+E"), self, activated=self.focus_search)
        QShortcut(QKeySequence("Ctrl+B"), self, activated=self.toggle_sidebar)
        self._menus()

        ctx.message.connect(self.show_message)
        ctx.goto_page_requested.connect(self.goto)
        ctx.search_requested.connect(self.run_research)
        ctx.progress.connect(self.on_progress)
        ctx.import_running.connect(self.on_running)
        ctx.roots_changed.connect(self.update_badge)
        ctx.library_changed.connect(self.update_badge)

        start = {"home": 0, "library": 1, "research": 2, "reader": 3}.get(ctx.settings.start_page, 0)
        self.nav.setCurrentRow(start)
        self._restore_geometry()
        self._sched = QTimer(self)
        self._sched.timeout.connect(lambda: (not ctx.importing) and ctx.start_import("scan"))
        self.apply_schedule()
        QTimer.singleShot(200, self.startup_tasks)

    # ------------------------------------------------------------------ menus
    def _menus(self) -> None:
        mb = self.menuBar()
        f = mb.addMenu("&File")
        f.addAction("Add library source…", lambda: (self.nav.setCurrentRow(7), self.pages["import"].sources.add()))
        f.addAction("Back up database now", lambda: self.ctx.backup_now(lambda p: info(self, "Backup complete", f"Backup written to:\n{p}")))
        f.addSeparator(); f.addAction("Quit", self.close, QKeySequence("Ctrl+Q"))
        v = mb.addMenu("&View")
        v.addAction("Toggle sidebar", self.toggle_sidebar)
        v.addAction("Full screen", lambda: self.showNormal() if self.isFullScreen() else self.showFullScreen(), QKeySequence("F11"))
        t = mb.addMenu("&Tools")
        t.addAction("Check library sources", lambda: self.pages["import"].sources.check())
        t.addAction("Scan all sources", lambda: self.ctx.start_import("scan"))
        t.addAction("Check search index", lambda: self.pages["import"].check_index())
        h = mb.addMenu("&Help")
        h.addAction("Search syntax", lambda: self.pages["research"].show_help())
        h.addAction("Open log folder", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.ctx.settings.path_log_dir()))))
        h.addAction(f"About {APP_NAME}", lambda: AboutDialog(self.ctx, self).exec())

    # -------------------------------------------------------------- navigation
    def _nav_changed(self, row: int) -> None:
        if row < 0:
            return
        key = NAV[row][0]
        self.stack.setCurrentWidget(self.pages[key])
        if key == "reader":
            self.pages["reader"].setFocus()

    def goto(self, where: str) -> None:
        if where == "focus:on" or where == "focus:off":
            on = where.endswith("on")
            self.sidebar.setVisible(not on); self.topbar.setVisible(not on); self.statusBar().setVisible(not on)
            return
        if where.startswith("library:"):
            bid = int(where.split(":")[1])
            self.nav.setCurrentRow(1)
            self.pages["library"].show_filter(BookFilter(book_ids=[bid]))
            return
        for i, (k, _) in enumerate(NAV):
            if k == where:
                self.nav.setCurrentRow(i)
                return

    def toggle_sidebar(self) -> None:
        self.sidebar.setVisible(not self.sidebar.isVisible())

    def focus_search(self) -> None:
        self.search.setFocus(); self.search.selectAll()

    def global_search(self) -> None:
        q = self.search.text().strip()
        if q:
            self.run_research(q)

    def run_research(self, q: str) -> None:
        self.nav.setCurrentRow(2)
        self.pages["research"].search_for(q)

    # ------------------------------------------------------------------ status
    def show_message(self, text: str) -> None:
        self.statusBar().showMessage(text, 7000)

    def on_running(self, running: bool) -> None:
        self.mini.setVisible(running)
        if not running:
            self.mini_label.setText("")
            p = self.ctx.last_progress
            if p and p.phase in ("done", "cancelled"):
                self.show_message(f"Indexing {p.phase}: {p.processed:,} processed, {p.failed:,} failed, {p.skipped:,} unchanged.")

    def on_progress(self, p) -> None:
        total = p.processed + p.remaining
        if p.phase == "discovering":
            self.mini.setRange(0, 0)
            self.mini_label.setText(f"Scanning… {p.discovered:,} files")
        else:
            self.mini.setRange(0, max(total, 1)); self.mini.setValue(p.processed)
            self.mini_label.setText(f"Indexing {p.processed:,} / {total:,}")

    def update_badge(self) -> None:
        off = [r for r in self.ctx.lib.roots() if r["enabled"] and not r["online"]]
        self.offline_badge.setText(("⚠ Offline: " + ", ".join(r["label"] for r in off)) if off else "")

    # ------------------------------------------------------------------ startup
    def startup_tasks(self) -> None:
        ctx = self.ctx
        unclean = ctx.db.get_state("running") == "1"
        ctx.db.set_state("running", "1")
        if unclean:
            log.warning("Previous session did not shut down cleanly; checking index")
            ctx.run_async(lambda: backup_mod.check_index(ctx.db), self._after_crash_check)

        def after_check(events):
            ctx.roots_changed.emit(); ctx.library_changed.emit()
            for e in events:
                if e["event"] == "drive_letter_changed":
                    self.show_message(f"A drive letter changed ({e['old']} → {e['new']}); the library followed it automatically.")
                elif e["event"] == "reconnected":
                    self.show_message("A library source is back online; its books were reconnected.")
            if ctx.settings.resume_on_start and ctx.lib.stats()["pending"] > 0 and not ctx.importing:
                ctx.start_import("resume")
            if ctx.backup_due():
                ctx.backup_now()
        ctx.run_async(ctx.lib.check_roots, after_check)
        self.update_badge()

    def _after_crash_check(self, f: dict) -> None:
        if f["ok"]:
            self.show_message("Atlas Library did not close cleanly last time; the database check found no problems.")
        else:
            if confirm(self, "Recovery", "Atlas Library did not close cleanly last time and the index check found problems. Repair the search index now? "
                       "(Your books, notes and annotations are not affected.)", "Repair"):
                self.ctx.run_async(lambda: backup_mod.repair_index(self.ctx.db), lambda r: (self.ctx.start_import("resume"), self.show_message("Index repaired; re-indexing affected books.")))

    def apply_schedule(self) -> None:
        m = self.ctx.settings.scheduled_scan_minutes
        self._sched.stop()
        if m > 0:
            self._sched.start(m * 60_000)

    # ------------------------------------------------------------------ closing
    def _restore_geometry(self) -> None:
        g = self.ctx.db.get_state("geometry")
        if g:
            self.restoreGeometry(QByteArray.fromBase64(g.encode()))

    def closeEvent(self, e) -> None:
        if self.ctx.importing:
            if not confirm(self, "Indexing in progress", "Indexing is still running. Quit now? Unfinished books will be resumed next time.", "Quit"):
                e.ignore()
                return
        try:
            self.ctx.db.set_state("geometry", bytes(self.saveGeometry().toBase64()).decode())
            self.pages["reader"].shutdown()
            self.ctx.settings.save()
            self.ctx.shutdown()
            self.ctx.db.conn  # reopen for the flag
            self.ctx.db.set_state("running", "0")
            self.ctx.db.close()
        except Exception:
            log.exception("Error during shutdown")
        e.accept()
