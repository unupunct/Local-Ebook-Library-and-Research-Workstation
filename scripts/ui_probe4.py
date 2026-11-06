"""Find who leaks references to None (PySide6 + Python 3.11): prints sys.getrefcount(None) deltas per step."""
import faulthandler
import os
import sys
import tempfile
import time
from pathlib import Path

faulthandler.enable(file=sys.stdout, all_threads=True)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
kind = sys.argv[1] if len(sys.argv) > 1 else "epub"

from PySide6.QtWidgets import QApplication

from atlas.settings import Settings
from atlas.testdata import build_collection
from atlas.ui.context import AppContext
from atlas.ui.main_window import MainWindow
from atlas.ui.theme import stylesheet

app = QApplication([])
app.setStyleSheet(stylesheet("dark"))
tmp = Path(tempfile.mkdtemp())
books = tmp / "books"
build_collection(books)
ctx = AppContext(Settings(data_dir=str(tmp / "data"), first_run_done=True))
ctx.lib.add_root(books)
ctx.start_import("scan")
while ctx.importing:
    app.processEvents(); time.sleep(0.005)
win = MainWindow(ctx)
win.show()
app.processEvents()
RC = lambda: sys.getrefcount(None)


def step(label, fn):
    a = RC()
    fn()
    print(f"{label:40s} None refcount delta = {RC() - a}", flush=True)


def pump(n=100):
    for _ in range(n):
        app.processEvents(); time.sleep(0.005)


print("None refcount at start:", RC(), flush=True)
step("idle pump x100", pump)
bid = ctx.db.conn.execute("SELECT id FROM books WHERE format=?", (kind,)).fetchone()[0]
rd = win.pages["reader"]
step("ctx.open_book (call only)", lambda: ctx.open_book(bid, {"type": "page" if kind == "pdf" else "chapter", "num": 1}))
step("pump x100 after open", pump)
step("rd.refresh_annotations", rd.refresh_annotations)
step("rd.apply_highlights", rd.apply_highlights)
step("rd.current_locator", rd.current_locator)
step("rd.save_position", rd.save_position)
step("rd._update status", lambda: rd._pdf_page(1) if kind == "pdf" else rd._update_reflow_status())
step("nav to library", lambda: win.nav.setCurrentRow(1))
step("pump x100 on library", pump)
step("nav to reader", lambda: win.nav.setCurrentRow(3))
step("pump x100 on reader", pump)
if kind == "pdf":
    step("pdf viewport update x50", lambda: [rd.pdf.viewport().repaint() for _ in range(50)])
    step("pdf goto_page", lambda: rd.pdf.goto_page(2))
    step("pdf set_zoom", lambda: rd.pdf.set_zoom(1.3))
    step("pump x100 after zoom", pump)
else:
    step("reflow viewport repaint x50", lambda: [rd.reflow.viewport().repaint() for _ in range(50)])
    step("reflow load_chapter", lambda: rd.load_chapter(2))
    step("pump x100 after chapter", pump)
print("None refcount at end:", RC(), flush=True)
