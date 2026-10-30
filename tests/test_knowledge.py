import json
import shutil
import threading
from datetime import datetime

import pytest

from atlas import backup, export
from atlas.library import BookFilter
from atlas.metadata_lookup import parse_openlibrary
from atlas.ocr import OcrUnavailable, find_tesseract, ocr_book
from atlas.reading import Reading
from atlas.research import Research
from atlas.semantic import semantic_status
from atlas.taxonomy import Tags


def bid(env, rel):
    return env.book(rel)["id"]


def test_reading_progress_roundtrip(indexed):
    r = Reading(indexed.db)
    b = bid(indexed, "Gardening/Permaculture/food_forest.pdf")
    assert r.get_progress(b) is None
    r.save_progress(b, {"type": "page", "num": 2}, 66.0, zoom=1.5)
    p = r.get_progress(b)
    assert p["locator"]["num"] == 2 and p["zoom"] == 1.5 and abs(p["percent"] - 66) < 1e-6
    assert indexed.lib.get_book(b)["reading_status"] == "reading"
    assert [x["id"] for x in r.continue_reading()] == [b]
    r.save_progress(b, {"type": "page", "num": 3}, 100)
    assert indexed.lib.get_book(b)["reading_status"] == "finished"
    r.record_session(b, datetime.now(), 120)
    assert r.stats()["seconds_total"] == 120


def test_annotations(indexed):
    r = Reading(indexed.db)
    b = bid(indexed, "Gardening/Permaculture/soil_book.epub")
    a = r.add(b, "highlight", {"type": "chapter", "num": 1, "start": 10, "end": 30, "prefix": "x", "suffix": "y"}, "turns waste", note="nice")
    r.add(b, "bookmark", {"type": "chapter", "num": 2})
    got = r.for_book(b)
    assert [x["type"] for x in got] == ["highlight", "bookmark"] and got[0]["locator"]["start"] == 10
    r.update(a, note="changed")
    assert r.for_book(b, "highlight")[0]["note"] == "changed"
    # source changed -> flagged, never silently moved
    (indexed.books / "Gardening" / "Permaculture" / "soil_book.epub").write_bytes(
        (indexed.books / "Gardening" / "Permaculture" / "soil_book.epub").read_bytes() + b"x")
    indexed.importer().run()
    assert r.mark_invalid_for_changed(b) == 1
    assert len(r.invalid_annotations()) == 1
    r.delete(a)


def test_clippings_survive_offline_and_deletion(indexed):
    res = Research(indexed.db)
    tags = Tags(indexed.db)
    b = bid(indexed, "Gardening/Permaculture/food_forest.pdf")
    t = res.create_topic("Food Forest Design")
    tag = tags.create("Soil")
    cid = res.create_clipping("Tomatoes love basil.", b, {"type": "page", "num": 2, "label": "Page 2"}, note="try it", tag_ids=[tag], topic_id=t)
    c = res.get_clipping(cid)
    assert c["book_title"] == "Food Forest Handbook" and c["loc_num"] == 2 and c["book_authors"] == "Ana Popescu"
    assert c["source_path"].endswith("food_forest.pdf") and c["created"]
    # go offline: clipping + topic intact
    shutil.move(indexed.books, indexed.tmp / "gone")
    indexed.lib.check_roots()
    c = res.get_clipping(cid)
    assert c["text"] == "Tomatoes love basil." and c["book_available"] == 0
    topic = res.get_topic(t)
    assert topic["clippings"][0]["id"] == cid
    # even if the book record is forgotten the clipping keeps its source info
    indexed.lib.delete_book_record(b)
    c = res.get_clipping(cid)
    assert c["book_title"] == "Food Forest Handbook" and c["book_id"] is None
    with pytest.raises(ValueError):
        res.create_clipping("   ")


def test_topics_backlinks_and_links(indexed):
    res = Research(indexed.db)
    a = res.create_topic("Solar Heating", "See [[Greenhouses]] and [[Missing]].")
    g = res.create_topic("Greenhouses", "Back to [[Solar Heating]].")
    assert [x["title"] for x in res.backlinks(g)] == ["Solar Heating"]
    assert [x["title"] for x in res.backlinks(a)] == ["Greenhouses"]
    res.save_topic(a, body="No links now")
    assert res.backlinks(g) == []
    res.create_topic("Missing")
    res.save_topic(a, body="[[missing]]")          # case-insensitive
    assert res.get_topic(a)["links"][0]["title"] == "Missing"
    with pytest.raises(ValueError):
        res.create_topic("solar heating")
    assert res.topics("Greenhouse")
    c1 = res.create_clipping("one"); c2 = res.create_clipping("two")
    res.link_clippings(c1, c2)
    assert res.get_clipping(c1)["links"] == [c2]
    res.delete_topic(a)
    assert res.get_topic(a) is None and res.get_clipping(c1)


def test_exports(indexed):
    res = Research(indexed.db)
    b = bid(indexed, "Gardening/Permaculture/food_forest.pdf")
    t = res.create_topic("T", "my own words")
    c = res.create_clipping("Exact quote here.", b, {"type": "page", "num": 2}, note="commentary", topic_id=t)
    md = export.export_clippings_md(indexed.db, [c])
    assert "> Exact quote here." in md and "p. 2" in md and "Ana Popescu" in md and "**My note:** commentary" in md
    assert "retrieved" in md
    tm = export.export_topic_md(indexed.db, t)
    assert "## Notes" in tm and "## Saved passages" in tm
    assert "<blockquote>" in export.markdown_to_html(tm)
    csv_text = export.export_clippings_csv(indexed.db, [c])
    assert "Exact quote here." in csv_text and "Food Forest Handbook" in csv_text
    resp = indexed.engine.search("tomatoes")
    assert "tomatoes" in export.export_search_csv(resp).lower()
    assert "Page 2" in export.export_search_md(resp)


def test_backup_and_restore(indexed, tmp_path):
    res = Research(indexed.db)
    rd = Reading(indexed.db)
    b = bid(indexed, "Misc/romanian.txt")
    res.create_topic("Keep me", "body")
    rd.save_progress(b, {"num": 0}, 40)
    rd.add(b, "highlight", {"num": 0}, "x")
    t = Tags(indexed.db); tid = t.create("T1"); t.add_to_books(tid, [b])
    indexed.engine.save_search("s1", "mulch")
    z = backup.create_backup(indexed.db, tmp_path / "bk", include_index=False)
    assert backup.verify_backup(z)["includes_index"] is False
    # damage everything then restore
    indexed.db.conn.execute("DELETE FROM topics")
    indexed.db.conn.execute("DELETE FROM annotations")
    indexed.db.conn.execute("DELETE FROM saved_searches")
    dbp, ip = indexed.db.db_path, indexed.db.index_path
    indexed.db.close()
    backup.restore_backup(z, dbp, ip)
    from atlas.db import Database
    db2 = Database(dbp, ip)
    try:
        assert db2.conn.execute("SELECT COUNT(*) FROM topics").fetchone()[0] == 1
        assert db2.conn.execute("SELECT COUNT(*) FROM annotations").fetchone()[0] == 1
        assert db2.conn.execute("SELECT COUNT(*) FROM saved_searches").fetchone()[0] == 1
        assert db2.conn.execute("SELECT percent FROM reading_progress WHERE book_id=?", (b,)).fetchone()[0] == 40
        assert db2.conn.execute("SELECT COUNT(*) FROM book_tags").fetchone()[0] == 1
        # without index in the backup, books are re-queued
        assert db2.conn.execute("SELECT COUNT(*) FROM books WHERE index_status='pending'").fetchone()[0] >= 8
    finally:
        db2.close()


def test_backup_with_index_and_prune(indexed, tmp_path):
    for _ in range(3):
        backup.create_backup(indexed.db, tmp_path / "bk2", include_index=True, keep=2)
        import time; time.sleep(1.1)
    assert len(backup.list_backups(tmp_path / "bk2")) == 2
    with pytest.raises(backup.BackupError):
        bad = tmp_path / "bad.zip"; bad.write_bytes(b"nope")
        backup.verify_backup(bad)


def test_index_integrity_and_repair(indexed):
    assert backup.check_index(indexed.db)["ok"]
    indexed.db.conn.execute("DELETE FROM segments WHERE book_id=?", (bid(indexed, "Misc/romanian.txt"),))   # missing text
    indexed.db.conn.execute("INSERT INTO segments(book_id,seq,loc_type,text) VALUES(99999,0,'text','orphan')")
    f = backup.check_index(indexed.db)
    assert not f["ok"] and f["orphan_segments"] == 1 and f["indexed_without_text"] == 1
    rep = backup.repair_index(indexed.db)
    assert rep["orphans_removed"] == 1 and rep["requeued"] == 1
    indexed.importer().run()
    assert backup.check_index(indexed.db)["ok"]
    assert indexed.engine.search("gradina").books


def test_cancel_and_resume(env):
    from atlas.testdata import build_bulk
    build_bulk(env.books, 24)
    env.lib.add_root(env.books)
    cancel = threading.Event()
    seen = []

    def cb(p):
        seen.append(p.processed)
        if p.processed >= 5:
            cancel.set()
    p = env.importer(progress_cb=cb, cancel=cancel).run()
    assert p.phase == "cancelled"
    pending = env.db.conn.execute("SELECT COUNT(*) FROM books WHERE index_status='pending'").fetchone()[0]
    assert 0 < pending < 24
    # "restart": new importer resumes without rescanning everything as new
    p2 = env.importer().run()
    assert p2.new == 0
    assert env.db.conn.execute("SELECT COUNT(*) FROM books WHERE index_status='indexed'").fetchone()[0] == 24
    assert env.db.conn.execute("SELECT COUNT(*) FROM segments WHERE book_id NOT IN (SELECT id FROM books)").fetchone()[0] == 0


def test_concurrent_indexing_threads(env):
    from atlas.testdata import build_bulk
    build_bulk(env.books, 24, seed=3)
    env.lib.add_root(env.books)
    env.settings.index_threads = 4
    env.importer().run()
    assert env.db.conn.execute("SELECT COUNT(*) FROM books WHERE index_status='indexed'").fetchone()[0] == 24
    assert backup.check_index(env.db)["ok"]


def test_ui_thread_responsive_during_indexing(env):
    """Reads on another connection must not be blocked while the indexer writes."""
    import time
    from atlas.testdata import build_bulk
    build_bulk(env.books, 40, seed=5)
    env.lib.add_root(env.books)
    t = threading.Thread(target=lambda: env.importer().run())
    t.start()
    worst = 0.0
    while t.is_alive():
        s = time.perf_counter()
        env.lib.count_books(); env.lib.query_books(limit=50)
        worst = max(worst, time.perf_counter() - s)
        time.sleep(0.01)
    t.join()
    assert worst < 2.0, f"read latency during indexing reached {worst:.2f}s"


def test_ocr_unavailable_message(indexed, monkeypatch):
    monkeypatch.setattr("atlas.ocr.find_tesseract", lambda p="": None)
    with pytest.raises(OcrUnavailable) as e:
        ocr_book(indexed.lib, bid(indexed, "Misc/scanned.pdf"))
    assert "Tesseract" in str(e.value)


@pytest.mark.skipif(find_tesseract() is None, reason="Tesseract not installed")
def test_ocr_real(indexed):
    b = bid(indexed, "Misc/scanned.pdf")
    res = ocr_book(indexed.lib, b, "eng")
    assert res["pages_ocr"] == 1
    assert indexed.engine.search("scanned").books


def test_semantic_status_is_honest():
    assert not semantic_status(False).available
    s = semantic_status(True, "nothing")
    assert not s.available and "not available" in s.message


def test_openlibrary_parse():
    r = parse_openlibrary({"docs": [{"title": "T", "author_name": ["A"], "publisher": ["P"], "first_publish_year": 1999, "isbn": ["9780306406157"]}]})
    assert r[0].title == "T" and r[0].year == 1999 and r[0].isbn == "9780306406157"


def test_stats_home_data(indexed):
    s = indexed.lib.stats()
    assert s["books"] == 12 and s["indexed"] >= 8 and s["segments"] > 10
    assert indexed.lib.review_candidates()
