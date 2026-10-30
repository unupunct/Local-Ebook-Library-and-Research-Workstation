# Semantic search (optional)

Keyword search (SQLite FTS5) is always the primary search and never depends on this feature.

**What exists:** `atlas/semantic.py` provides an `EmbeddingProvider` interface, a `SentenceTransformersProvider` that loads a *local* model folder (`local_files_only`), and `SemanticService`, which embeds the indexed passages, stores the vectors in its own `semantic.db`, and answers queries with brute-force cosine search (numpy). Research → *Also search by meaning* shows the hits as a separate "Related passages by meaning" group. Settings → Metadata and semantic search → *Build / update semantic index* builds it in the background; the index updates incrementally, drops vectors of removed passages and is discarded if you change the model.

**What does not exist:** no model is bundled, and the optional packages are **not included in the packaged exe**. To use it you must run from source with `pip install numpy sentence-transformers` and download a model (e.g. all-MiniLM-L6-v2) yourself into a folder. Without that, Settings and Research say plainly that semantic search is unavailable.

**Verification status:** the service (building, incremental updates, stale removal, model change, cancel, ranking, separation from the keyword index) is covered by automated tests using a deterministic fake provider. It has **not** been run with a real embedding model.

Requirements and costs: a local model (100 MB – 1 GB), CPU time to embed every passage (hours for ~10 GB of text on a laptop), roughly 1.5–6 KB of vector storage per passage (384–1536 float32 dimensions) in `semantic.db`, and enough RAM to hold all vectors during a search (passages × dimensions × 4 bytes). Nothing is sent over the network; deleting `semantic.db` (or *Delete semantic index*) removes the feature's data without touching anything else.
