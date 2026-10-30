"""Offscreen smoke run: builds a demo library, opens every page, saves screenshots to the given folder."""
import os, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from atlas.settings import Settings
from atlas.testdata import build_collection
from atlas.ui.context import AppContext
from atlas.ui.main_window import MainWindow, NAV
from atlas.ui.theme import stylesheet

out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp())
out.mkdir(parents=True, exist_ok=True)
tmp = Path(tempfile.mkdtemp())
app = QApplication([])
s = Settings(data_dir=str(tmp / "data"), first_run_done=True)
app.setStyleSheet(stylesheet("dark"))
books = tmp / "books"; build_collection(books)
ctx = AppContext(s)
ctx.lib.add_root(books)
win = MainWindow(ctx); win.resize(1360, 860); win.show()

def pump(ms=300):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()

ctx.start_import("scan")
t0 = time.time(); ticks = 0
while ctx.importing and time.time() - t0 < 120:
    app.processEvents(); ticks += 1; time.sleep(0.005)
pump(500)
print("import done; UI event-loop ticks during import:", ticks)
ctx.tags.get_or_create_path("Gardening/Permaculture")
ctx.tags_changed.emit()
for i, (k, name) in enumerate(NAV):
    win.nav.setCurrentRow(i); pump(250)
    win.grab().save(str(out / f"{i:02d}_{k}.png"))
# research
win.run_research('"companion planting" OR zanzibarium'); pump(1500)
win.nav.setCurrentRow(2); pump(300); win.grab().save(str(out / "20_research_results.png"))
# reader: pdf and epub
pid = ctx.db.conn.execute("SELECT id FROM books WHERE format='pdf' AND title='Food Forest Handbook'").fetchone()[0]
ctx.open_book(pid, {"type": "page", "num": 2, "terms": ["tomatoes"], "excerpt": ""}); pump(1500)
win.grab().save(str(out / "21_reader_pdf.png"))
eid = ctx.db.conn.execute("SELECT id FROM books WHERE format='epub'").fetchone()[0]
ctx.open_book(eid, {"type": "chapter", "num": 1, "terms": ["humus"], "excerpt": "Composting turns waste into humus", "char_start": 20}); pump(1000)
win.grab().save(str(out / "22_reader_epub.png"))
win.close()
print("ok", out)
