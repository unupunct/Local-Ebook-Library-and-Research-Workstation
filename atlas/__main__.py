"""Application entry point:  python -m atlas"""
from __future__ import annotations

import logging
import os
import sys
import traceback
from pathlib import Path


def resource_path(rel: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / rel


def selftest(report: Path) -> int:
    """Index a tiny synthetic library with every parser and search it. Used to verify packaged builds."""
    import tempfile
    lines: list[str] = []
    ok = True
    try:
        from .db import Database
        from .importer import Importer
        from .library import Library
        from .search import SearchEngine
        from .settings import Settings
        from .testdata import build_collection
        tmp = Path(tempfile.mkdtemp(prefix="atlas-selftest-"))
        build_collection(tmp / "books")
        s = Settings(data_dir=str(tmp / "data"), index_threads=2); s.ensure_dirs()
        db = Database(s.path_db_dir() / "atlas.db", s.path_index_dir() / "index.db")
        lib = Library(db, s.path_cache_dir() / "covers"); lib.add_root(tmp / "books")
        Importer(lib, s).run()
        eng = SearchEngine(db)
        for q, want in (("zanzibarium", "Food Forest Handbook"), ("quixotical", "Living Soil"), ("wolframite", "Finishing Notes"),
                        ("xylophagous", "Solar Heating"), ("quasimodo", "Battery Storage"), ("ostrogothic", "legacy"), ("gradina", "romanian")):
            titles = {b.title for b in eng.search(q, record_history=False).books}
            good = want in titles
            ok &= good
            lines.append(f"{'PASS' if good else 'FAIL'} search {q!r} -> {sorted(titles)}")
        db.close()
    except Exception:
        ok = False
        lines.append(traceback.format_exc())
    lines.append("SELFTEST " + ("OK" if ok else "FAILED"))
    report.write_text(chr(10).join(lines), encoding="utf-8")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv
    if "--selftest" in args:
        i = args.index("--selftest")
        return selftest(Path(args[i + 1]) if len(args) > i + 1 else Path("atlas-selftest.txt"))
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox

    from . import APP_NAME, __version__
    from .logging_setup import setup_logging
    from .settings import Settings
    from .ui.theme import stylesheet

    app = QApplication(args)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("AtlasLibrary")
    icon_file = resource_path("assets/atlas.ico")
    icon = QIcon(str(icon_file)) if icon_file.exists() else None
    if icon:
        app.setWindowIcon(icon)

    settings = Settings.load()
    app.setStyleSheet(stylesheet(settings.theme))

    wizard_cfg = None
    if not settings.first_run_done:
        from .ui.wizard import FirstRunWizard
        w = FirstRunWizard(settings)
        if w.exec():
            wizard_cfg = w.result_config()
            settings.data_dir = wizard_cfg["data_dir"]
        settings.first_run_done = True
        settings.save()

    settings.ensure_dirs()
    logfile = setup_logging(settings.path_log_dir(), settings.log_level)
    log = logging.getLogger("atlas")
    log.info("Starting %s %s (log %s)", APP_NAME, __version__, logfile)

    def hook(et, ev, tb):
        log.error("Unhandled exception", exc_info=(et, ev, tb))
        try:
            QMessageBox.critical(None, APP_NAME, f"An unexpected error occurred:\n{ev}\n\nDetails were written to the log:\n{logfile}")
        except Exception:
            pass
    sys.excepthook = hook

    from .ui.context import AppContext
    from .ui.main_window import MainWindow
    try:
        ctx = AppContext(settings)
    except Exception as exc:
        log.exception("Could not open the database")
        QMessageBox.critical(None, APP_NAME, f"The library database could not be opened:\n{exc}\n\nSee the log for details:\n{logfile}\n\n"
                             "You can restore a backup by deleting nothing and starting Atlas Library with a different data folder, "
                             "or follow docs/BACKUP_AND_RESTORE.md.")
        return 2

    if wizard_cfg:
        for folder in wizard_cfg["folders"]:
            try:
                ctx.lib.add_root(folder)
            except ValueError as exc:
                log.warning("Wizard source skipped: %s", exc)

    win = MainWindow(ctx, icon)
    win.show()
    if wizard_cfg and wizard_cfg["index_now"] and ctx.lib.roots():
        ctx.start_import("scan")
    elif settings.reopen_last_book:
        last = ctx.db.get_state("last_book")
        if last.isdigit():
            ctx.open_book(int(last))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
