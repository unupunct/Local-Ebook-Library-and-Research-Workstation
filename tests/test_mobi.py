import io

import pytest
from PIL import Image

from atlas.parsers import parse_file
from atlas.parsers.base import EncryptedError, ParseError
from atlas.parsers.mobi import MobiBook, palmdoc_decompress
from atlas.testdata import make_mobi


def png() -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (40, 60), (200, 30, 30)).save(b, "PNG")
    return b.getvalue()


CHAPTERS = [("Prefață", "O introducere despre grădină și zanzibarium."), ("Compost", "Compostul transformă deșeurile în humus. Marker: quixotical."),
            ("Final", "Ultimul capitol.")]


def test_palmdoc_lz77_pair():
    # "hello " literals, then a length/distance pair (dist 6, len 8)
    data = b"hello " + bytes([0x80, 0x35])
    assert palmdoc_decompress(data) == b"hello hello he"
    assert palmdoc_decompress(bytes([0xC0 | 0x21])) == b" a"      # space + char shortcut
    with pytest.raises(ParseError):
        palmdoc_decompress(bytes([0x80, 0x08]))                    # distance beyond output


@pytest.mark.parametrize("compress", [True, False])
def test_parse_mobi(tmp_path, compress):
    p = make_mobi(tmp_path / "b.mobi", "Grădina mea", "Ana Ionescu", CHAPTERS, compress=compress, cover_png=png())
    d = parse_file(p, "mobi")
    segs = list(d.segments)
    assert d.metadata.title == "Grădina mea" and d.metadata.authors == ["Ana Ionescu"]
    assert d.metadata.publisher == "Test Press" and d.metadata.year == 2011 and d.metadata.language == "ro"
    assert d.cover and d.cover[:4] == b"\x89PNG"
    assert [s.loc_label for s in segs] == ["Prefață", "Compost", "Final"]
    assert [s.loc_num for s in segs] == [0, 1, 2] and "humus" in segs[1].text and "grădină" in segs[0].text
    assert len(d.toc) == 3


def test_mobi_drm_huffman_and_damage(tmp_path):
    with pytest.raises(EncryptedError):
        parse_file(make_mobi(tmp_path / "d.mobi", "x", "y", CHAPTERS, encryption=2), "mobi")
    with pytest.raises(ParseError) as e:
        parse_file(make_mobi(tmp_path / "h.mobi", "x", "y", CHAPTERS, compression_override=17480), "mobi")
    assert e.value.status == "unsupported"
    good = make_mobi(tmp_path / "g.mobi", "x", "y", CHAPTERS).read_bytes()
    (tmp_path / "t.mobi").write_bytes(good[:300])
    with pytest.raises(ParseError):
        parse_file(tmp_path / "t.mobi", "mobi")
    (tmp_path / "n.mobi").write_bytes(b"not a mobi" * 20)
    with pytest.raises(ParseError):
        parse_file(tmp_path / "n.mobi", "mobi")


def test_mobi_imported_searched_and_read(env, tmp_path):
    env.books.mkdir()
    make_mobi(env.books / "g.mobi", "Grădina mea", "Ana Ionescu", CHAPTERS, cover_png=png())
    make_mobi(env.books / "drm.azw", "Locked", "Z", CHAPTERS, encryption=1)
    env.lib.add_root(env.books)
    env.importer().run()
    b = env.book("g.mobi")
    assert b["index_status"] == "indexed" and b["title"] == "Grădina mea" and b["cover_path"]
    assert env.book("drm.azw")["index_status"] == "encrypted"
    r = env.engine.search("quixotical")
    p = r.books[0].passages[0]
    assert p.loc_type == "chapter" and p.loc_num == 1 and p.loc_label == "Compost"
    assert env.engine.search("gradina").books
    # the reflow reader can open it too
    from atlas.ui.text_reader import load_source
    s = load_source(b["path"], "mobi")
    assert s.count() == 3 and s.titles[1] == "Compost"
    html, imgs = s.chapter(1)
    assert "humus" in html
