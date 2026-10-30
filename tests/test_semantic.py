import threading

import pytest

np = pytest.importorskip("numpy")

from atlas.semantic import EmbeddingProvider, SemanticService, SemanticStatus, semantic_status  # noqa: E402

CONCEPTS = [
    {"compost", "mulch", "humus", "fertility", "fertilizer", "soil", "nutrients"},
    {"dovetail", "mortise", "tenon", "joint", "chisel", "saw"},
    {"battery", "lithium", "charge", "cell", "storage"},
]


class FakeProvider(EmbeddingProvider):
    """Deterministic stand-in: words of the same concept share a dimension, so unrelated wording still matches."""
    name = "fake"

    def status(self):
        return SemanticStatus(True, "fake")

    def embed(self, texts):
        out = np.zeros((len(texts), len(CONCEPTS) + 1), dtype="float32")
        for i, t in enumerate(texts):
            words = {w.strip(".,").lower() for w in t.split()}
            for k, group in enumerate(CONCEPTS):
                out[i, k] = len(words & group)
            out[i, -1] = 0.05
            out[i] /= np.linalg.norm(out[i]) or 1
        return out


def test_semantic_finds_related_words_and_keeps_keyword_search_separate(indexed, tmp_path):
    svc = SemanticService(indexed.db, tmp_path / "semantic.db", FakeProvider("m1"))
    r = svc.build()
    n, total = svc.counts()
    assert r["embedded"] == n == total and r["remaining"] == 0
    # the query shares no word with the passages, only the concept
    assert not indexed.engine.search("nutrients").books
    books = indexed.engine.related_passages(svc.search("nutrients", limit=5))
    titles = [b.title for b in books]
    assert "Living Soil" in titles or "Food Forest Handbook" in titles
    assert all(p.excerpt for b in books for p in b.passages)
    wood = indexed.engine.related_passages(svc.search("chisel", limit=5))
    assert wood and wood[0].title == "joints"
    # semantic data lives in its own file, never in the keyword databases
    assert (tmp_path / "semantic.db").exists()
    assert indexed.db.conn.execute("SELECT name FROM sqlite_master WHERE name LIKE 'sem_%'").fetchall() == []


def test_semantic_incremental_stale_removal_cancel_and_model_change(indexed, tmp_path):
    svc = SemanticService(indexed.db, tmp_path / "s.db", FakeProvider("m1"))
    ev = threading.Event()
    ev.set()
    assert svc.build(cancel=ev)["embedded"] == 0
    svc.build()
    (indexed.books / "Misc" / "romanian.txt").write_text("compost and mulch for fertility", encoding="utf-8")
    indexed.importer().run()
    r = svc.build()
    assert r["stale_removed"] >= 1 and r["embedded"] >= 1
    assert svc.counts()[0] == svc.counts()[1]
    svc2 = SemanticService(indexed.db, tmp_path / "s.db", FakeProvider("m2"))
    svc2.build()
    assert svc2.counts()[0] == svc2.counts()[1]


def test_semantic_status_messages():
    assert "turned off" in semantic_status(False).message
    s = semantic_status(True, "")
    assert not s.available and "not available" in s.message
    s = semantic_status(True, "sentence-transformers", "")
    assert not s.available
