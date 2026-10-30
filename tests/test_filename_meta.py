import pytest

from atlas.filename_meta import suggest
from atlas.library import BookFilter


def test_patterns():
    s = suggest("Ion Creangă - Amintiri din copilărie (1892).pdf", "Author - Title (Year)")
    assert (s.author, s.title, s.year) == ("Ion Creangă", "Amintiri din copilărie", 1892)
    s = suggest("The_Backyard_Orchardist - Stella Otto.epub", "Title - Author")
    assert (s.title, s.author) == ("The Backyard Orchardist", "Stella Otto")
    s = suggest("Joinery Basics (2004).pdf", "Title (Year)")
    assert (s.title, s.year, s.author) == ("Joinery Basics", 2004, "")
    assert suggest("Soil [2019].pdf", "Title (Year)").year == 2019
    assert suggest("random file name.pdf", "Author - Title") is None            # no guess when it does not match
    assert suggest("12 - 34.pdf", "Author - Title") is None                      # numbers are not authors
    with pytest.raises(KeyError):
        suggest("x.pdf", "nope")


def test_only_empty_fields_are_filled(indexed):
    """The apply helper used by the UI never overwrites existing metadata."""
    from atlas.filename_meta import apply_suggestion
    lib = indexed.lib
    bid = indexed.book("Misc/romanian.txt")["id"]
    lib.db.conn.execute("UPDATE books SET original_filename='Dan Barbu - Titlu Nou (2001).txt', title='Dan Barbu - Titlu Nou (2001)' WHERE id=?", (bid,))
    changed = apply_suggestion(lib, bid, "Author - Title (Year)")
    b = lib.get_book(bid)
    assert changed == ["title", "authors", "year"] and b["title"] == "Titlu Nou" and b["authors"] == ["Dan Barbu"] and b["year"] == 2001
    lib.update_book(bid, authors=["Someone Else"], year=1999)
    assert apply_suggestion(lib, bid, "Author - Title (Year)") == []            # nothing overwritten
    assert lib.get_book(bid)["authors"] == ["Someone Else"] and lib.get_book(bid)["year"] == 1999
