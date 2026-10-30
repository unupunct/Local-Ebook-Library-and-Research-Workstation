import os
import shutil
import sqlite3

import pytest

from atlas.importer import fingerprint
from atlas.library import BookFilter
from atlas.search import QuerySyntaxError, build_match
from atlas.taxonomy import Tags, TagError
from atlas.testdata import make_pdf


def status(env, rel):
    return env.book(rel)["index_status"]


# ---------------------------------------------------------------- import
def test_import_nested_tree(indexed):
    n = indexed.lib.count_books()
    assert n == 12
    assert status(indexed, "Gardening/Permaculture/food_forest.pdf") == "indexed"
    b = indexed.book("Gardening/Permaculture/food_forest.pdf")
    assert b["title"] == "Food Forest Handbook" and b["authors"] == ["Ana Popescu"] and b["page_count"] == 3
    assert indexed.book("Misc/Deep/Er/page.html")["title"] == "Solar Heating"
    assert indexed.book("Gardening/Permaculture/soil_book.epub")["isbn"] == "9780306406157"
    assert indexed.book("Gardening/Permaculture/soil_book.epub")["year"] == 2015


def test_import_twice_no_duplicates(indexed):
    before = indexed.lib.count_books()
    p = indexed.importer().run()
    assert indexed.lib.count_books() == before
    assert p.new == 0 and p.processed == 0 and p.skipped >= 10


def test_malformed_and_unsupported(indexed):
    assert status(indexed, "Misc/broken.pdf") == "failed"
    assert status(indexed, "Misc/broken.epub") == "failed"
    assert status(indexed, "Misc/book.djvu") == "unsupported"
    assert status(indexed, "Misc/scanned.pdf") == "ocr_needed"
    assert "scanned" in indexed.book("Misc/scanned.pdf")["index_detail"].lower()
    assert indexed.db.conn.execute("SELECT COUNT(*) FROM books WHERE original_filename='ignored.xyz'").fetchone()[0] == 0


def test_retry_failed(indexed):
    p = indexed.importer().retry_failed()
    assert status(indexed, "Misc/broken.pdf") == "failed"


# ---------------------------------------------------------------- search
def hits(env, q, **kw):
    r = env.engine.search(q, **kw)
    return {b.title for b in r.books}


def test_find_word_in_pdf_with_page(indexed):
    r = indexed.engine.search("zanzibarium")
    assert [b.title for b in r.books] == ["Food Forest Handbook"]
    p = r.books[0].passages[0]
    assert p.loc_type == "page" and p.loc_num == 1
    r = indexed.engine.search("tomatoes basil")
    assert r.books[0].passages[0].loc_num == 2
    assert "<b>" in r.books[0].passages[0].excerpt_html


def test_phrase_and_operators(indexed):
    assert hits(indexed, '"companion planting"') == {"Food Forest Handbook"}
    assert hits(indexed, '"planting companion"') == set()
    assert hits(indexed, "compost AND mulch") == {"Food Forest Handbook"}
    assert hits(indexed, "mulch NOT compost") == {"Living Soil"}
    assert hits(indexed, "zanzibarium OR quixotical") == {"Food Forest Handbook", "Living Soil"}
    assert hits(indexed, "(zanzibarium OR quixotical) -earthworms") == {"Food Forest Handbook"}
    assert "Living Soil" in hits(indexed, "earthw*")
    assert hits(indexed, "TOMATOES") == {"Food Forest Handbook"}


def test_filters_combined_with_query(indexed):
    tags = Tags(indexed.db)
    g = tags.create("Gardening"); p = tags.create("Permaculture", g)
    soil = indexed.book("Gardening/Permaculture/soil_book.epub")["id"]
    tags.add_to_books(p, [soil])
    f = BookFilter(tag_id=g)  # hierarchical: parent tag includes child tag
    assert hits(indexed, "mulch", flt=f) == {"Living Soil"}
    assert hits(indexed, "mulch", flt=BookFilter(format="pdf")) == {"Food Forest Handbook"}
    assert hits(indexed, "mulch", flt=BookFilter(author="Ion Ionescu")) == {"Living Soil"}
    assert hits(indexed, "mulch", flt=BookFilter(year_from=2010, year_to=2020)) == {"Living Soil"}
    assert hits(indexed, "mulch", flt=BookFilter(folder="Gardening")) == {"Food Forest Handbook", "Living Soil"}
    assert hits(indexed, "mulch", flt=BookFilter(book_ids=[soil])) == {"Living Soil"}


def test_epub_chapter_locations(indexed):
    r = indexed.engine.search("clover")
    p = r.books[0].passages[0]
    assert p.loc_type == "chapter" and p.loc_num == 2 and p.loc_label == "Cover Crops"
    r = indexed.engine.search("humus")
    assert r.books[0].passages[0].loc_num == 1


def test_unicode_and_diacritics(indexed):
    assert hits(indexed, "gradina") == {"romanian"}
    assert hits(indexed, "grădina") == {"romanian"}
    assert hits(indexed, "sedinta") == {"romanian"}
    assert hits(indexed, "Țara") == {"romanian"}
    assert hits(indexed, "stefanescu") == {"romanian"}


def test_other_formats_found(indexed):
    for word, title in [("wolframite", "Finishing Notes"), ("zygomorphic", "joints"), ("xylophagous", "Solar Heating"),
                        ("quasimodo", "Battery Storage"), ("ostrogothic", "legacy")]:
        assert title in hits(indexed, word), word
    assert hits(indexed, "scriptmarker") == set()   # script content not indexed


def test_query_syntax_errors():
    for bad in ['"unclosed', "(a AND b", "a)", "AND", "a AND", "NOT a", "a OR NOT b", "", "   ", "*"]:
        with pytest.raises(QuerySyntaxError):
            build_match(bad)
    # SQL / FTS metacharacters are neutralised
    assert build_match("foo' OR 1=1 --").match.startswith("(")
    with pytest.raises(QuerySyntaxError):
        build_match('foo" OR 1=1 --')
    m = build_match("a NEAR b").match
    assert '"NEAR"' in m
    assert build_match("cat*").match == '"cat"*'


def test_search_sorting_and_flat(indexed):
    r = indexed.engine.search("mulch", grouped=False)
    assert len(r.passages) == r.total_passages
    r = indexed.engine.search("soil", sort="title")
    assert [b.title for b in r.books] == sorted([b.title for b in r.books], key=str.lower)


def test_saved_search_and_history(indexed):
    indexed.engine.search("mulch")
    assert indexed.engine.history()[0]["query"] == "mulch"
    sid = indexed.engine.save_search("m", "mulch", BookFilter(format="pdf"))
    assert indexed.engine.saved_searches()[0]["name"] == "m"
    indexed.engine.delete_saved(sid)
    assert not indexed.engine.saved_searches()


# ------------------------------------------------------ offline / moves / change
def test_offline_root_and_reconnect(indexed):
    root = indexed.lib.roots()[0]
    hidden = indexed.tmp / "books_hidden"
    os.rename(indexed.books, hidden)
    ev = indexed.lib.check_roots()
    assert ev[0]["event"] == "went_offline"
    assert indexed.lib.count_books(BookFilter(available=False)) == 12
    assert indexed.lib.count_books() == 12               # records preserved
    r = indexed.engine.search("zanzibarium")               # search still works offline
    assert r.books and not r.books[0].available
    os.rename(hidden, indexed.books)
    ev = indexed.lib.check_roots()
    assert ev[0]["event"] == "reconnected"
    assert indexed.lib.count_books(BookFilter(available=False)) == 0


def test_moved_file_is_relinked_not_duplicated(indexed):
    b = indexed.book("Misc/romanian.txt")
    src = indexed.books / "Misc" / "romanian.txt"
    dst = indexed.books / "Gardening" / "romanian_moved.txt"
    shutil.move(src, dst)
    p = indexed.importer().run()
    assert p.moved == 1 and p.new == 0
    assert indexed.lib.count_books() == 12
    nb = indexed.lib.get_book(b["id"])
    assert nb["available"] == 1 and nb["original_filename"] == "romanian_moved.txt"


def test_deleted_file_marked_unavailable(indexed):
    (indexed.books / "Misc" / "romanian.txt").unlink()
    p = indexed.importer().run()
    assert p.missing == 1
    assert indexed.book("Misc/romanian.txt")["available"] == 0
    assert hits(indexed, "gradina") == {"romanian"}  # index kept until user decides


def test_reindex_changed_document(indexed):
    path = indexed.books / "Misc" / "romanian.txt"
    path.write_text("Totally different text with marker alpacaville.", encoding="utf-8")
    p = indexed.importer().run()
    assert p.changed == 1 and p.processed == 1
    assert hits(indexed, "alpacaville") == {"romanian"}
    assert hits(indexed, "gradina") == set()


def test_fingerprint_differs_for_same_name(tmp_path):
    a = tmp_path / "a"; b = tmp_path / "b"; a.mkdir(); b.mkdir()
    (a / "x.txt").write_text("one"); (b / "x.txt").write_text("two")
    assert fingerprint(str(a / "x.txt")) != fingerprint(str(b / "x.txt"))


def test_duplicate_detection(indexed):
    shutil.copy(indexed.books / "Misc" / "romanian.txt", indexed.books / "Gardening" / "copy.txt")
    indexed.importer().run()
    groups = indexed.lib.duplicate_groups()
    assert any(g["kind"] == "identical" and len(g["book_ids"]) == 2 for g in groups)
    assert (indexed.books / "Gardening" / "copy.txt").exists()   # never deleted


def test_overlapping_roots_rejected(env, tmp_path):
    (tmp_path / "lib" / "sub").mkdir(parents=True)
    env.lib.add_root(tmp_path / "lib")
    with pytest.raises(ValueError):
        env.lib.add_root(tmp_path / "lib" / "sub")
    with pytest.raises(ValueError):
        env.lib.add_root(tmp_path / "LIB")


def test_drive_letter_change(env, monkeypatch, tmp_path):
    from atlas import storage
    d = tmp_path / "lib"; d.mkdir()
    rid = env.lib.add_root(d)
    env.db.conn.execute("UPDATE library_roots SET path=?, volume_serial='ABCD1234', online=1 WHERE id=?", (r"Z:\Books", rid))
    monkeypatch.setattr(storage, "find_drive_by_serial", lambda s: "Y:" if s == "ABCD1234" else None)
    real = storage.is_dir_available
    monkeypatch.setattr(storage, "is_dir_available", lambda p, t=4.0: p == "Y:\\Books" or real(p, t))
    ev = env.lib.check_roots()
    assert ev[0]["event"] == "drive_letter_changed"
    assert env.lib.root(rid)["path"] == "Y:\\Books"


# ---------------------------------------------------------------- tags
def test_tag_lifecycle(indexed):
    t = Tags(indexed.db)
    a = t.create("Gardening"); b = t.create("Soil", a); c = t.create("Compost", b)
    bid = indexed.book("Misc/romanian.txt")["id"]
    t.add_to_books(c, [bid])
    t.rename(b, "Soils")
    with pytest.raises(TagError):
        t.create("Soils", a)
    with pytest.raises(TagError):
        t.move(a, c)
    other = t.create("Dirt")
    t.merge(other, b)
    t.delete(b)                         # children move up; books untouched
    assert t.path(c) == "Gardening / Compost"
    assert indexed.lib.count_books() == 12
    t.delete(c)
    assert indexed.lib.get_book(bid)["tags"] == []
    assert indexed.lib.get_book(bid) is not None


def test_autotag_rules(indexed):
    t = Tags(indexed.db)
    g = t.get_or_create_path("Gardening/Permaculture")
    t.add_rule("directory", "Permaculture", g)
    n = t.evaluate_rules()
    assert n == 2
    sugg = {s["title"] for s in t.suggestions()}
    assert sugg == {"Food Forest Handbook", "Living Soil"}
    assert indexed.lib.get_book(indexed.book("Misc/romanian.txt")["id"])["tags"] == []   # nothing applied without confirmation
    t.accept_suggestion(indexed.book("Gardening/Permaculture/soil_book.epub")["id"], g)
    assert len(t.suggestions()) == 1
    with pytest.raises(TagError):
        t.add_rule("keyword", "ab", g)
    t.add_rule("keyword", "mulch", t.create("Mulching"), min_keyword_hits=3)
    t.evaluate_rules()
    assert "mulch" not in " ".join(s["reason"] for s in t.suggestions() if "appears" in s["reason"])  # only 1 hit each: below threshold


def test_bulk_edit_and_metadata(indexed):
    ids = [indexed.book("Misc/romanian.txt")["id"], indexed.book("Gardening/Permaculture/soil_book.epub")["id"]]
    indexed.lib.bulk_update(ids, language="ro", reading_status="reading")
    assert indexed.lib.get_book(ids[0])["language"] == "ro"
    indexed.lib.update_book(ids[0], title="Edited", authors=["A B", "C D"], genres=["Fiction", "History"])
    b = indexed.lib.get_book(ids[0])
    assert b["authors"] == ["A B", "C D"] and b["genres"] == ["Fiction", "History"] and b["metadata_edited"] == 1
    # edited metadata survives reindexing
    indexed.importer().reindex_books([ids[0]])
    assert indexed.lib.get_book(ids[0])["title"] == "Edited"
    with pytest.raises(ValueError):
        indexed.lib.update_book(ids[0], uuid="x")


def test_collections(indexed):
    cid = indexed.lib.create_collection("Greenhouse")
    ids = indexed.lib.book_ids(BookFilter(format="pdf"))
    indexed.lib.add_to_collection(cid, ids)
    assert indexed.lib.count_books(BookFilter(collection_id=cid)) == len(ids)
    indexed.lib.delete_collection(cid)
    assert indexed.lib.count_books() == 12


def test_remove_root_keeps_records(indexed):
    rid = indexed.lib.roots()[0]["id"]
    indexed.lib.remove_root(rid)
    assert indexed.lib.count_books() == 12


def test_index_lives_in_separate_file(indexed):
    import sqlite3
    main = sqlite3.connect(indexed.db.db_path)
    idx = sqlite3.connect(indexed.db.index_path)
    try:
        main_tables = {r[0] for r in main.execute("select name from sqlite_master")}
        idx_tables = {r[0] for r in idx.execute("select name from sqlite_master")}
    finally:
        main.close()
        idx.close()
    assert "segments" in idx_tables and "segments_fts" in idx_tables
    assert "segments" not in main_tables and "books" in main_tables


def test_excerpt_highlighting(indexed):
    r = indexed.engine.search("earthw*")
    assert "<b>earthworms</b>" in r.books[0].passages[0].excerpt_html
    r = indexed.engine.search("gradina")
    assert "<b>Grădina</b>" in r.books[0].passages[0].excerpt_html          # diacritics folded, original text shown
    r = indexed.engine.search('"companion planting" NOT blight')
    html = r.books[0].passages[0].excerpt_html
    assert "<b>companion planting</b>" in html and "blight" not in html
    r = indexed.engine.search("mulch -clay")
    assert r.parsed.terms == ["mulch"]


def test_index_upgrade_backfills_seg_meta(tmp_path):
    """An index created by version 1 of the schema is upgraded in place."""
    import sqlite3
    from atlas.db import Database
    from atlas.migrations import IDX_MIGRATIONS, MAIN_MIGRATIONS
    from atlas.db import _split
    idx = tmp_path / "index.db"
    con = sqlite3.connect(idx, isolation_level=None)
    for stmt in _split(IDX_MIGRATIONS[0].replace("idx.", "")):
        con.execute(stmt)
    con.execute("INSERT INTO segments(book_id,seq,loc_type,loc_num,loc_label,char_start,text) VALUES(1,0,'page',3,'Page 3',0,'old hello world')")
    con.execute("PRAGMA user_version=1")
    con.close()
    db = Database(tmp_path / "atlas.db", idx)
    try:
        assert db.conn.execute("SELECT loc_num FROM seg_meta").fetchone()[0] == 3
        assert db.conn.execute("SELECT COUNT(*) FROM segments_fts WHERE segments_fts MATCH 'hello'").fetchone()[0] == 1
    finally:
        db.close()


def test_scan_retries_flaky_share_and_aborts_when_unreachable(indexed, monkeypatch):
    import os as _os
    from atlas import importer as imp, storage
    real = _os.scandir
    calls = {"n": 0}

    def flaky(path):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise OSError("share hiccup")
        return real(path)
    monkeypatch.setattr(storage.time if hasattr(storage, "time") else __import__("time"), "sleep", lambda s: None)
    monkeypatch.setattr(imp.os, "scandir", flaky)
    files = list(imp.walk_files(str(indexed.books), True, [], [], False, attempts=3))
    assert files and calls["n"] > 2                      # transient failures were retried
    # root becomes unreachable: scan aborts, nothing is marked missing
    monkeypatch.setattr(imp.os, "scandir", lambda p: (_ for _ in ()).throw(OSError("gone")))
    monkeypatch.setattr(storage.time if hasattr(storage, "time") else __import__("time"), "sleep", lambda s: None)
    monkeypatch.setattr(storage, "is_dir_available", lambda p, t=None: False)
    indexed.db.conn.execute("UPDATE library_roots SET kind='network'")
    before = indexed.lib.count_books(BookFilter(available=True))
    p = indexed.importer().run()
    assert "unreachable" in p.note or "offline" in p.note
    assert indexed.lib.count_books() == 12 and indexed.lib.count_books(BookFilter(available=True)) <= before


def test_mapped_drive_and_unc_are_the_same_library(env, monkeypatch, tmp_path):
    from atlas import storage
    d = tmp_path / "share"
    d.mkdir()
    monkeypatch.setattr(storage, "_wnet_connection", lambda drive: str(tmp_path) if drive == "Y:" else "")
    env.lib.add_root(d)
    # the same folder reached through a "mapped drive" that resolves to the same place
    key_a = storage.canonical_key(d)
    monkeypatch.setattr(storage, "normalize", lambda p: r"Y:\share" if str(p).startswith("Y:") else __import__("os").path.normpath(str(p)))
    assert storage.canonical_key(r"Y:\share") == key_a
    with pytest.raises(ValueError):
        env.lib.add_root(r"Y:\share")
