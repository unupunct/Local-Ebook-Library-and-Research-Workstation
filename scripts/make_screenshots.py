"""Create the README screenshots from the SYNTHETIC test collection (never from a real library).

    python scripts/make_screenshots.py docs/images
"""
import sys, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PySide6.QtWidgets import QApplication

from atlas.settings import Settings
from atlas.testdata import build_collection, make_mobi
from atlas.ui.context import AppContext
from atlas.ui.main_window import MainWindow
from atlas.ui.theme import stylesheet

out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs/images")
out.mkdir(parents=True, exist_ok=True)
tmp = Path(tempfile.mkdtemp(prefix="atlas-shots-"))
app = QApplication([])
app.setStyleSheet(stylesheet("dark"))
s = Settings(data_dir=str(tmp / "data"), first_run_done=True)
books = tmp / "Library"
build_collection(books)
make_mobi(books / "Misc" / "garden_notes.mobi", "Garden Notes", "Mira Dalton",
          [("Beds", "Raised beds warm earlier in spring."), ("Water", "Drip lines save water and time.")])
ctx = AppContext(s)
ctx.lib.add_root(books)
win = MainWindow(ctx)
win.resize(1360, 860)
win.show()


def pump(ms=300):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()


ctx.start_import("scan")
while ctx.importing:
    app.processEvents(); time.sleep(0.01)
pump(500)
g = ctx.tags.get_or_create_path("Gardening/Permaculture")
w = ctx.tags.get_or_create_path("Woodworking/Joinery")
for rel, tag in (("Gardening/Permaculture/food_forest.pdf", g), ("Gardening/Permaculture/soil_book.epub", g), ("Woodworking/Hand Tools/joints.txt", w)):
    bid = ctx.db.conn.execute("SELECT id FROM books WHERE rel_path=?", (rel.replace("/", "\\"),)).fetchone()[0]
    ctx.tags.add_to_books(tag, [bid])
cid = ctx.lib.create_collection("Greenhouse project")
ctx.lib.add_to_collection(cid, [r[0] for r in ctx.db.conn.execute("SELECT id FROM books WHERE format IN ('pdf','epub') LIMIT 3")])
ctx.lib.set_favorite([ctx.db.conn.execute("SELECT id FROM books WHERE title='Living Soil'").fetchone()[0]], True)
ctx.tags_changed.emit(); ctx.library_changed.emit()
pump(300)

win.nav.setCurrentRow(1); pump(600)
win.pages["library"].browser.select_ids([ctx.db.conn.execute("SELECT id FROM books WHERE title='Living Soil'").fetchone()[0]]); pump(400)
win.grab().save(str(out / "library.png"))

win.run_research('"companion planting" OR mulch'); pump(1500)
win.grab().save(str(out / "research.png"))

pid = ctx.db.conn.execute("SELECT id FROM books WHERE title='Food Forest Handbook'").fetchone()[0]
ctx.open_book(pid, {"type": "page", "num": 2, "terms": ["companion", "planting"], "excerpt": ""}); pump(1500)
win.grab().save(str(out / "reader-pdf.png"))

eid = ctx.db.conn.execute("SELECT id FROM books WHERE format='epub'").fetchone()[0]
ctx.open_book(eid, {"type": "chapter", "num": 1, "terms": ["humus"], "excerpt": "Composting turns waste into humus", "char_start": 20}); pump(1000)
win.grab().save(str(out / "reader-epub.png"))

win.nav.setCurrentRow(0); pump(600)
win.grab().save(str(out / "home.png"))
win.close()
print("screenshots written to", out)
