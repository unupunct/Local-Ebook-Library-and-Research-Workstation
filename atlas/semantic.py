"""Optional semantic ("search by meaning") layer. Entirely separate from keyword search.

* Fully local: vectors are computed by a provider running on this machine; nothing is sent anywhere.
* Vectors live in their own file (``semantic.db`` next to the main database), never in the FTS index.
  Deleting that file only removes semantic search.
* No model is bundled. A provider is available only when its optional packages and a model folder exist;
  otherwise ``semantic_status`` explains what is missing and keyword search is unaffected.

Included provider: ``sentence-transformers`` (optional install, local model folder, loaded with
``local_files_only``). The brute-force cosine search needs numpy. See docs/SEMANTIC_SEARCH.md.
"""
from __future__ import annotations

import importlib.util
import logging
import sqlite3
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from .db import Database

log = logging.getLogger(__name__)


@dataclass
class SemanticStatus:
    available: bool
    message: str


class EmbeddingProvider(ABC):
    name: str = ""
    description: str = ""

    def __init__(self, model_path: str = "") -> None:
        self.model_path = model_path

    @abstractmethod
    def status(self) -> SemanticStatus: ...

    @abstractmethod
    def embed(self, texts: list[str]) -> Any:
        """Return an (n, dim) float32 array of L2-normalised vectors."""

    @property
    def model_id(self) -> str:
        return f"{self.name}:{self.model_path}"


_PROVIDERS: dict[str, type[EmbeddingProvider]] = {}


def register_provider(cls: type[EmbeddingProvider]) -> type[EmbeddingProvider]:
    _PROVIDERS[cls.name] = cls
    return cls


def provider_names() -> list[str]:
    return sorted(_PROVIDERS)


@register_provider
class SentenceTransformersProvider(EmbeddingProvider):
    name = "sentence-transformers"
    description = "Local sentence-transformers model folder (optional packages: sentence-transformers, numpy)."

    def __init__(self, model_path: str = "") -> None:
        super().__init__(model_path)
        self._model = None
        self._lock = threading.Lock()

    def status(self) -> SemanticStatus:
        if importlib.util.find_spec("numpy") is None:
            return SemanticStatus(False, "numpy is not installed.")
        if importlib.util.find_spec("sentence_transformers") is None:
            return SemanticStatus(False, "The optional package 'sentence-transformers' is not installed (pip install sentence-transformers). "
                                         "It is not included in the packaged exe.")
        if not self.model_path or not Path(self.model_path).is_dir():
            return SemanticStatus(False, "Choose a local model folder in Settings (a downloaded sentence-transformers model, e.g. all-MiniLM-L6-v2).")
        return SemanticStatus(True, f"Ready: local model at {self.model_path}")

    def embed(self, texts: list[str]):
        import numpy as np
        with self._lock:
            if self._model is None:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(self.model_path, device="cpu", local_files_only=True)
        v = self._model.encode(texts, batch_size=32, show_progress_bar=False, normalize_embeddings=True)
        return np.asarray(v, dtype="float32")


def semantic_status(enabled: bool, provider: str = "", model_path: str = "") -> SemanticStatus:
    if not enabled:
        return SemanticStatus(False, "Semantic search is turned off. Keyword search (SQLite FTS5) is always available.")
    cls = _PROVIDERS.get(provider)
    if cls is None:
        return SemanticStatus(False, "No embedding provider is selected, so semantic search is not available. Only keyword search is active.")
    try:
        return cls(model_path).status()
    except Exception as exc:  # provider bugs must not break the app
        return SemanticStatus(False, f"The embedding provider failed to initialise: {exc}")


class SemanticService:
    """Builds and queries the vector store for the passages of the keyword index."""

    def __init__(self, db: Database, store_path: Path, provider: EmbeddingProvider) -> None:
        self.db, self.store_path, self.provider = db, Path(store_path), provider
        self._matrix = None
        self._ids: list[int] = []
        self._books: list[int] = []
        self._lock = threading.Lock()
        con = self._con()
        con.executescript("CREATE TABLE IF NOT EXISTS sem_info(key TEXT PRIMARY KEY, value TEXT);"
                          "CREATE TABLE IF NOT EXISTS sem_vec(segment_id INTEGER PRIMARY KEY, book_id INTEGER NOT NULL, dim INTEGER NOT NULL, vec BLOB NOT NULL);")
        con.close()

    def _con(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.store_path, timeout=30)
        con.execute("PRAGMA journal_mode=WAL")
        return con

    # ------------------------------------------------------------- building
    def check_model(self) -> None:
        """If the model changed since the vectors were built, they are meaningless: discard them."""
        con = self._con()
        try:
            row = con.execute("SELECT value FROM sem_info WHERE key='model'").fetchone()
            if row and row[0] != self.provider.model_id:
                log.info("Embedding model changed; discarding old vectors")
                con.execute("DELETE FROM sem_vec")
                self._matrix = None
            con.execute("INSERT INTO sem_info(key,value) VALUES('model',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (self.provider.model_id,))
            con.commit()
        finally:
            con.close()

    def counts(self) -> tuple[int, int]:
        """(embedded passages, passages in keyword index)"""
        con = self._con()
        try:
            n = con.execute("SELECT COUNT(*) FROM sem_vec").fetchone()[0]
        finally:
            con.close()
        return n, self.db.conn.execute("SELECT COUNT(*) FROM seg_meta").fetchone()[0]

    def build(self, progress: Callable[[int, int], None] | None = None, cancel: threading.Event | None = None, batch: int = 64) -> dict:
        import numpy as np
        st = self.provider.status()
        if not st.available:
            raise RuntimeError(st.message)
        self.check_model()
        con = self._con()
        try:
            have = {r[0] for r in con.execute("SELECT segment_id FROM sem_vec")}
            live = [r[0] for r in self.db.conn.execute("SELECT id FROM seg_meta ORDER BY id")]
            live_set = set(live)
            stale = [i for i in have if i not in live_set]
            for i in range(0, len(stale), 500):
                con.executemany("DELETE FROM sem_vec WHERE segment_id=?", [(x,) for x in stale[i:i + 500]])
            todo = [i for i in live if i not in have]
            done = 0
            for i in range(0, len(todo), batch):
                if cancel is not None and cancel.is_set():
                    break
                ids = todo[i:i + batch]
                rows = self.db.conn.execute(
                    f"SELECT s.id, s.book_id, s.text FROM segments s WHERE s.id IN ({','.join('?' * len(ids))})", ids).fetchall()
                vecs = self.provider.embed([r["text"][:2000] for r in rows])
                con.executemany("INSERT OR REPLACE INTO sem_vec(segment_id,book_id,dim,vec) VALUES(?,?,?,?)",
                                [(r["id"], r["book_id"], int(vecs.shape[1]), np.asarray(vecs[k], dtype="float32").tobytes()) for k, r in enumerate(rows)])
                con.commit()
                done += len(rows)
                if progress:
                    progress(done, len(todo))
            con.commit()
        finally:
            con.close()
        self._matrix = None
        return {"embedded": done, "stale_removed": len(stale), "remaining": max(0, len(todo) - done)}

    def delete_all(self) -> None:
        con = self._con()
        try:
            con.execute("DELETE FROM sem_vec")
            con.commit()
        finally:
            con.close()
        self._matrix = None

    # ------------------------------------------------------------- searching
    def _load(self) -> None:
        import numpy as np
        with self._lock:
            if self._matrix is not None:
                return
            con = self._con()
            try:
                rows = con.execute("SELECT segment_id, book_id, dim, vec FROM sem_vec ORDER BY segment_id").fetchall()
            finally:
                con.close()
            if not rows:
                self._matrix, self._ids, self._books = np.zeros((0, 1), dtype="float32"), [], []
                return
            dim = rows[0][2]
            self._matrix = np.vstack([np.frombuffer(r[3], dtype="float32", count=dim) for r in rows if r[2] == dim])
            self._ids = [r[0] for r in rows if r[2] == dim]
            self._books = [r[1] for r in rows if r[2] == dim]

    def search(self, query: str, limit: int = 30, book_ids: Sequence[int] | None = None, min_score: float = 0.2) -> list[dict]:
        import numpy as np
        if not query.strip():
            return []
        self._load()
        if not self._ids:
            return []
        q = np.asarray(self.provider.embed([query]), dtype="float32")[0]
        scores = self._matrix @ q
        if book_ids is not None:
            allowed = set(book_ids)
            mask = np.array([b in allowed for b in self._books])
            scores = np.where(mask, scores, -1.0)
        k = min(limit * 4, len(scores))
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top])]
        out, per_book = [], {}
        for i in top:
            sc = float(scores[i])
            if sc < min_score:
                break
            b = self._books[i]
            if per_book.get(b, 0) >= 3:       # at most 3 passages per book keeps the list varied
                continue
            per_book[b] = per_book.get(b, 0) + 1
            out.append({"segment_id": self._ids[i], "book_id": b, "score": sc})
            if len(out) >= limit:
                break
        return out
