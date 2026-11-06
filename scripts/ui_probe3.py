"""Crash bisection by widget: python ui_probe3.py <page|window|none> [noimport]"""
import faulthandler
import os
import sys
import tempfile
import time
from pathlib import Path

faulthandler.enable(file=sys.stdout, all_threads=True)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
what = sys.argv[1]
do_import = "noimport" not in sys.argv

from PySide6.QtWidgets import QApplication

from atlas.settings import Settings
from atlas.testdata import build_collection
from atlas.ui.context import AppContext
from atlas.ui.theme import stylesheet

app = QApplication([])
app.setStyleSheet(stylesheet("dark"))
tmp = Path(tempfile.mkdtemp())
books = tmp / "books"
build_collection(books)
ctx = AppContext(Settings(data_dir=str(tmp / "data"), first_run_done=True))
ctx.lib.add_root(books)
import atlas.ui.pages.collections_page as col
import atlas.ui.pages.home as home
import atlas.ui.pages.import_page as imp
import atlas.ui.pages.library as lib
import atlas.ui.pages.notes_page as notes
import atlas.ui.pages.reader as rd
import atlas.ui.pages.research as res
import atlas.ui.pages.settings_page as st
import atlas.ui.pages.tags_page as tags
from atlas.ui.main_window import MainWindow

PAGES = {"home": (home, "HomePage"), "library": (lib, "LibraryPage"), "research": (res, "ResearchPage"), "reader": (rd, "ReaderPage"),
         "collections": (col, "CollectionsPage"), "notes": (notes, "NotesPage"), "history": (notes, "HistoryPage"),
         "import": (imp, "ImportPage"), "tags": (tags, "TagsPage"), "settings": (st, "SettingsPage")}
w = None
if what == "window":
    w = MainWindow(ctx)
elif what in PAGES:
    mod, cls = PAGES[what]
    w = getattr(mod, cls)(ctx)
if w is not None:
    w.show()
app.processEvents()
if do_import:
    ctx.start_import("scan")
    t0 = time.time()
    while ctx.importing and time.time() - t0 < 120:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()
if "nav" in sys.argv and what == "window":
    from atlas.ui.main_window import NAV
    for i in range(len(NAV)):
        w.nav.setCurrentRow(i)
        app.processEvents()
if "pdf" in sys.argv and what == "window":
    bid = ctx.db.conn.execute("SELECT id FROM books WHERE format='pdf' AND title='Food Forest Handbook'").fetchone()[0]
    ctx.open_book(bid, {"type": "page", "num": 2, "terms": ["tomatoes"]})
    end = time.time() + 1.5
    while time.time() < end:
        app.processEvents()
if "epub" in sys.argv and what == "window":
    bid = ctx.db.conn.execute("SELECT id FROM books WHERE format='epub'").fetchone()[0]
    ctx.open_book(bid, {"type": "chapter", "num": 1})
    end = time.time() + 1.0
    while time.time() < end:
        app.processEvents()
if "rshut" in sys.argv and what == "window":
    w.pages["reader"].shutdown()
print("before ctx.shutdown", flush=True)
ctx.shutdown()
if w is not None:
    w.deleteLater()
app.processEvents()
print("body done", what, flush=True)
