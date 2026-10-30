"""Shared application context for the UI: services, signals, background work."""
from __future__ import annotations

import logging
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThread, QThreadPool, Signal

from .. import backup as backup_mod
from ..db import Database
from ..importer import Importer, Progress
from ..library import Library
from ..paths import bootstrap_settings_file
from ..reading import Reading
from ..research import Research
from ..search import SearchEngine
from ..settings import Settings
from ..taxonomy import Tags

log = logging.getLogger(__name__)


class ImportWorker(QThread):
    progress = Signal(object)
    failed = Signal(str)

    def __init__(self, ctx: "AppContext", mode: str, root_ids: list[int] | None, book_ids: list[int] | None, force: bool) -> None:
        super().__init__()
        self.ctx, self.mode, self.root_ids, self.book_ids, self.force = ctx, mode, root_ids, book_ids, force
        self.cancel = threading.Event()

    def run(self) -> None:
        try:
            imp = Importer(self.ctx.lib, self.ctx.settings, self.progress.emit, self.cancel)
            if self.mode == "scan":
                imp.run(self.root_ids, self.force)
            elif self.mode == "resume":
                imp.p = Progress(phase="indexing")
                imp.process_pending(self.root_ids)
                imp.p.phase = "cancelled" if self.cancel.is_set() else "done"
                imp._emit(True)
            elif self.mode == "reindex_all":
                imp.reindex_all()
            elif self.mode == "retry":
                imp.retry_failed()
            elif self.mode == "books":
                imp.reindex_books(self.book_ids or [])
        except Exception as exc:  # never let a worker die silently
            log.exception("Import worker failed")
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class _Task(QRunnable):
    def __init__(self, fn: Callable[[], Any], signals: "TaskSignals") -> None:
        super().__init__()
        self.fn, self.signals = fn, signals

    def run(self) -> None:
        try:
            self.signals.done.emit(self.fn())
        except Exception as exc:
            log.exception("Background task failed")
            self.signals.error.emit(exc)


class TaskSignals(QObject):
    done = Signal(object)
    error = Signal(object)


class AppContext(QObject):
    library_changed = Signal()
    tags_changed = Signal()
    notes_changed = Signal()
    progress = Signal(object)
    import_running = Signal(bool)
    message = Signal(str)
    open_book_requested = Signal(int, object)
    search_requested = Signal(str)
    goto_page_requested = Signal(str)
    roots_changed = Signal()

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        settings.ensure_dirs()
        self.db = Database(settings.path_db_dir() / "atlas.db", settings.path_index_dir() / "index.db")
        self.lib = Library(self.db, settings.path_cache_dir() / "covers")
        self.tags = Tags(self.db)
        self.engine = SearchEngine(self.db)
        self.reading = Reading(self.db)
        self.research = Research(self.db)
        self.worker: ImportWorker | None = None
        self.last_progress: Progress | None = None
        self._pool = QThreadPool.globalInstance()
        self._tasks: set[TaskSignals] = set()

    # ------------------------------------------------------------- semantic
    def semantic_status(self):
        from ..semantic import semantic_status
        s = self.settings
        return semantic_status(s.semantic_enabled, s.semantic_provider, s.semantic_model_path)

    def semantic_service(self):
        """SemanticService for the configured provider, or None when unavailable."""
        from ..semantic import _PROVIDERS, SemanticService
        if not self.semantic_status().available:
            return None
        s = self.settings
        key = (s.semantic_provider, s.semantic_model_path)
        if getattr(self, "_sem_key", None) != key:
            self._sem = SemanticService(self.db, s.path_db_dir() / "semantic.db", _PROVIDERS[s.semantic_provider](s.semantic_model_path))
            self._sem_key = key
        return self._sem

    # ---------------------------------------------------------------- tasks
    def run_async(self, fn: Callable[[], Any], on_done: Callable[[Any], None] | None = None,
                  on_error: Callable[[Exception], None] | None = None) -> None:
        sig = TaskSignals()
        self._tasks.add(sig)

        def finish(res: Any) -> None:
            self._tasks.discard(sig)
            if on_done:
                on_done(res)

        def fail(exc: Exception) -> None:
            self._tasks.discard(sig)
            (on_error or (lambda e: self.message.emit(str(e))))(exc)
        sig.done.connect(finish)
        sig.error.connect(fail)
        self._pool.start(_Task(fn, sig))

    # --------------------------------------------------------------- import
    @property
    def importing(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def start_import(self, mode: str = "scan", root_ids: list[int] | None = None, book_ids: list[int] | None = None,
                     force: bool = False) -> bool:
        if self.importing:
            self.message.emit("An import is already running. Wait for it to finish or cancel it first.")
            return False
        w = ImportWorker(self, mode, root_ids, book_ids, force)
        w.progress.connect(self._on_progress)
        w.failed.connect(lambda m: self.message.emit("Import failed: " + m))
        w.finished.connect(self._on_import_finished)
        self.worker = w
        self.import_running.emit(True)
        w.start()
        return True

    def cancel_import(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.cancel.set()

    def _on_progress(self, p: Progress) -> None:
        self.last_progress = p
        self.progress.emit(p)

    def _on_import_finished(self) -> None:
        self.import_running.emit(False)
        self.library_changed.emit()

    def wait_import(self, ms: int = 60000) -> None:
        if self.worker:
            self.worker.wait(ms)

    # ------------------------------------------------------------- helpers
    def open_book(self, book_id: int, locator: dict | None = None) -> None:
        self.open_book_requested.emit(book_id, locator or {})

    def backup_now(self, on_done: Callable[[Any], None] | None = None) -> None:
        s = self.settings
        self.run_async(lambda: backup_mod.create_backup(
            self.db, s.path_backup_dir(), s.backup_include_index, s.backup_keep, bootstrap_settings_file()),
            on_done=lambda p: (self.db.set_state("last_backup", datetime.now().isoformat(timespec="seconds")),
                               on_done(p) if on_done else None))

    def backup_due(self) -> bool:
        days = self.settings.backup_every_days
        if days <= 0:
            return False
        last = self.db.get_state("last_backup")
        if not last:
            return self.lib.stats()["books"] > 0
        try:
            return (datetime.now() - datetime.fromisoformat(last)).days >= days
        except ValueError:
            return True

    def shutdown(self) -> None:
        self.cancel_import()
        if self.worker:
            self.worker.wait(15000)
        self._pool.waitForDone(5000)
        self.db.close()
