"""Step-by-step offscreen UI probe used to diagnose crashes (prints progress, dumps tracebacks to stdout)."""
import faulthandler
import os
import sys
import tempfile
from pathlib import Path

faulthandler.enable(file=sys.stdout, all_threads=True)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication

from atlas.settings import Settings
from atlas.ui.context import AppContext
from atlas.ui.theme import stylesheet

print("start", flush=True)
app = QApplication([])
print("QApplication", flush=True)
app.setStyleSheet(stylesheet("dark"))
print("stylesheet applied", flush=True)
tmp = Path(tempfile.mkdtemp())
ctx = AppContext(Settings(data_dir=str(tmp / "data"), first_run_done=True))
print("context", flush=True)
from atlas.ui.main_window import NAV, MainWindow
import atlas.ui.pages.home as home, atlas.ui.pages.library as lib, atlas.ui.pages.research as res, atlas.ui.pages.reader as rd
import atlas.ui.pages.collections_page as col, atlas.ui.pages.notes_page as notes, atlas.ui.pages.import_page as imp
import atlas.ui.pages.tags_page as tags, atlas.ui.pages.settings_page as st
for name, mod, cls in (("home", home, "HomePage"), ("library", lib, "LibraryPage"), ("research", res, "ResearchPage"), ("reader", rd, "ReaderPage"),
                       ("collections", col, "CollectionsPage"), ("notes", notes, "NotesPage"), ("history", notes, "HistoryPage"),
                       ("import", imp, "ImportPage"), ("tags", tags, "TagsPage"), ("settings", st, "SettingsPage")):
    print("constructing", name, flush=True)
    w = getattr(mod, cls)(ctx)
    w.show()
    app.processEvents()
    print("ok", name, flush=True)
print("main window", flush=True)
win = MainWindow(ctx)
win.show()
app.processEvents()
print("done", flush=True)

# ---- second phase: the same flow as the UI test fixture (background import with the window open)
import time
from atlas.testdata import build_collection
books = tmp / "books"
build_collection(books)
print("collection built", flush=True)
ctx.lib.add_root(books)
ctx.start_import("scan")
print("import started", flush=True)
t0 = time.time()
while ctx.importing and time.time() - t0 < 120:
    app.processEvents()
    time.sleep(0.005)
print("import finished", flush=True)
for i in range(len(NAV)):
    win.nav.setCurrentRow(i)
    app.processEvents()
    print("page", NAV[i][0], flush=True)
win.pages["reader"].shutdown()
print("reader shutdown", flush=True)
ctx.shutdown()
print("ctx shutdown", flush=True)
win.deleteLater()
app.processEvents()
print("ALL DONE", flush=True)
