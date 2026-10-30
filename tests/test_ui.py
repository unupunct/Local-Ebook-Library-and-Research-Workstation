import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from atlas.settings import Settings  # noqa: E402
from atlas.testdata import build_bulk, build_collection  # noqa: E402
from atlas.ui.context import AppContext  # noqa: E402
from atlas.ui.main_window import NAV, MainWindow  # noqa: E402
from atlas.ui.theme import stylesheet  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    a.setStyleSheet(stylesheet("dark"))
    return a


def pump(app, ms=200):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()


@pytest.fixture
def win(app, tmp_path):
    s = Settings(data_dir=str(tmp_path / "data"), first_run_done=True)
    books = tmp_path / "books"
    build_collection(books)
    ctx = AppContext(s)
    ctx.lib.add_root(books)
    w = MainWindow(ctx)
    w.show()
    ctx.start_import("scan")
    t0 = time.time()
    while ctx.importing and time.time() - t0 < 120:
        app.processEvents()
        time.sleep(0.005)
    pump(app, 300)
    yield w
    w.pages["reader"].shutdown()
    ctx.shutdown()
    w.deleteLater()


def test_all_pages_render(app, win):
    for i in range(len(NAV)):
        win.nav.setCurrentRow(i)
        pump(app, 100)
        assert win.stack.currentWidget() is win.pages[NAV[i][0]]


def test_global_search_and_results(app, win):
    win.search.setText('"companion planting"')
    win.global_search()
    pump(app, 800)
    r = win.pages["research"]
    assert r.response is not None and r.response.total_books == 1
    assert r.tree.topLevelItemCount() == 1
    r.query.setText('"unclosed')
    r.run()
    pump(app, 500)
    assert "quotation" in r.error.text().lower() and not r.error.isHidden()


def test_open_pdf_result_goes_to_page(app, win):
    ctx = win.ctx
    r = ctx.engine.search("tomatoes")
    p = r.books[0].passages[0]
    ctx.open_book(r.books[0].book_id, p.locator(r.parsed.terms))
    pump(app, 1200)
    rd = win.pages["reader"]
    assert rd.kind == "pdf" and rd.pdf.cur == 2
    assert rd.pdf.hits.get(2)
    assert win.stack.currentWidget() is rd


def test_open_epub_result_goes_to_chapter_and_progress_saved(app, win):
    ctx = win.ctx
    r = ctx.engine.search("clover")
    p = r.books[0].passages[0]
    bid = r.books[0].book_id
    ctx.open_book(bid, p.locator(r.parsed.terms))
    pump(app, 800)
    rd = win.pages["reader"]
    assert rd.kind == "reflow" and rd.chapter == 2
    rd.save_position()
    prog = ctx.reading.get_progress(bid)
    assert prog["locator"]["num"] == 2 and prog["percent"] > 50
    # reopening without a locator resumes there
    ctx.open_book(bid)
    pump(app, 500)
    assert rd.chapter == 2


def test_highlight_and_clip_from_reader(app, win):
    ctx = win.ctx
    bid = ctx.db.conn.execute("SELECT id FROM books WHERE format='epub'").fetchone()[0]
    ctx.open_book(bid, {"type": "chapter", "num": 1})
    pump(app, 600)
    rd = win.pages["reader"]
    from PySide6.QtGui import QTextCursor
    c = rd.reflow.textCursor()
    c.setPosition(0)
    c.setPosition(20, QTextCursor.KeepAnchor)
    rd.reflow.setTextCursor(c)
    rd.highlight("#f2c94c")
    anns = ctx.reading.for_book(bid)
    assert len(anns) == 1 and anns[0]["locator"]["quote"] == anns[0]["selected_text"]
    assert rd.reflow.highlights
    # annotation is re-anchored after changing fonts
    rd.ctx.settings.reader_font_size = 20
    rd.apply_prefs()
    rd.load_chapter(1)
    assert rd.reflow.highlights


def test_offline_book_message_and_notes_accessible(app, win, tmp_path):
    import shutil
    ctx = win.ctx
    bid = ctx.db.conn.execute("SELECT id FROM books WHERE format='pdf' AND title='Food Forest Handbook'").fetchone()[0]
    ctx.reading.add(bid, "bookmark", {"type": "page", "num": 2}, "", "", "#fff", "Page 2")
    shutil.move(str(tmp_path / "books"), str(tmp_path / "away"))
    ctx.lib.check_roots()
    ctx.open_book(bid)
    pump(app, 300)
    rd = win.pages["reader"]
    assert rd.kind == "" and "offline" in rd.empty_msg.text()
    assert rd.anns.count() == 1       # bookmarks still listed
    win.pages["library"].apply()
    assert win.pages["library"].browser.total == 12


def test_ui_stays_responsive_during_indexing(app, tmp_path):
    s = Settings(data_dir=str(tmp_path / "data"), first_run_done=True, index_threads=1)
    build_bulk(tmp_path / "bulk", 60)
    ctx = AppContext(s)
    ctx.lib.add_root(tmp_path / "bulk")
    ticks, gaps = [], []
    last = [time.perf_counter()]

    def tick():
        now = time.perf_counter()
        gaps.append(now - last[0])
        last[0] = now
    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(10)
    ctx.start_import("scan")
    while ctx.importing:
        app.processEvents()
        time.sleep(0.002)
    timer.stop()
    ctx.shutdown()
    assert len(gaps) > 20, "event loop barely ran during indexing"
    assert max(gaps) < 1.0, f"UI thread stalled for {max(gaps):.2f}s"


def test_epub_css_subset_is_applied_and_unsafe_css_dropped(app, tmp_path):
    import zipfile
    from atlas.testdata import make_epub
    from atlas.ui.text_reader import ReflowView, load_source
    ep = make_epub(tmp_path / "css.epub", "Styled", "A B", [("One", "Centered line\n\nSecond para")])
    # add a stylesheet with safe and unsafe rules and link it from the chapter
    src = tmp_path / "css2.epub"
    with zipfile.ZipFile(ep) as zin, zipfile.ZipFile(src, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename.endswith("c0.xhtml"):
                data = data.replace(b"<head>", b'<head><link rel="stylesheet" href="s.css"/>').replace(b"<p>Centered", b'<p class="c" style="color:red;text-align:center">Centered')
            zout.writestr(item, data)
        zout.writestr("OEBPS/s.css", "@import url(http://evil/x.css); p.c{font-weight:bold;color:red;background:url(http://x/y.png);margin-left:20px;font-size:12px} body{background:#000} .x{position:absolute}")
    s = load_source(str(src), "epub")
    html, _ = s.chapter(0)
    assert "<style>" in html and "evil" not in html and "color" not in html and "background" not in html and "position" not in html
    assert "font-weight:bold" in html and "margin-left:20px" in html and "font-size" not in html
    v = ReflowView()
    v.apply_prefs({"theme": "dark"})
    v.show_chapter(html, {})
    blk = v.document().firstBlock()
    while blk.isValid() and not blk.text().startswith("Centered"):
        blk = blk.next()
    assert blk.blockFormat().leftMargin() == 20 and blk.charFormat().fontWeight() == 700
    assert v.document().toPlainText().startswith("One")     # style text is not shown as content


def test_badges_drag_drop_and_research_state(app, win):
    import json
    from PySide6.QtCore import QMimeData
    from atlas.ui.book_browser import BOOKS_MIME
    from atlas.ui.common import badge_for
    assert badge_for(0, "indexed") == "offline" and badge_for(1, "failed") == "problem"
    assert badge_for(1, "ocr_needed") == "attention" and badge_for(1, "indexed") == ""
    lib = win.pages["library"]
    model = lib.browser.model
    assert model.mimeTypes() == [BOOKS_MIME]
    ids = lib.browser.model.rows[0]["id"], lib.browser.model.rows[1]["id"]
    md = model.mimeData([model.index(0, 0), model.index(1, 0)])
    assert sorted(json.loads(bytes(md.data(BOOKS_MIME)).decode())) == sorted(ids)
    # dropping on a collection adds the books
    cid = win.ctx.lib.create_collection("Dropped")
    lib.filters.populate()
    lib.filters._dropped(cid, list(ids))
    assert win.ctx.lib.count_books(__import__("atlas.library", fromlist=["BookFilter"]).BookFilter(collection_id=cid)) == 2
    # the last search is remembered across restarts
    r = win.pages["research"]
    r.query.setText("zanzibarium")
    r.run()
    pump(app, 600)
    saved = json.loads(win.ctx.db.get_state("research_state"))
    assert saved["query"] == "zanzibarium"
    r.query.clear()
    r._restore_state()
    assert r.query.text() == "zanzibarium"
