# Architecture

```
atlas/
  settings.py paths.py logging_setup.py   JSON settings, locations, rotating log
  db.py migrations.py                      thread-local SQLite connections, WAL, versioned migrations
  storage.py                               root probing with timeouts, volume serial / drive letter, UNC detection
  library.py                               roots, books, filters, metadata, collections, duplicates, stats
  taxonomy.py                              hierarchical tags, merge/move/delete, auto-tag rules + suggestions
  formats.py parsers/                      format registry; PDF, EPUB, TXT, MD, HTML, DOCX, RTF adapters
  importer.py                              scan (new/changed/moved/missing) + indexing pipeline (Qt-free, cancellable)
  search/                                  query parser -> safe FTS5 MATCH; engine with filters, grouping, history, saved searches
  reading.py research.py export.py         progress/annotations, clippings/notebook/backlinks, Markdown/HTML/CSV export
  backup.py ocr.py semantic.py metadata_lookup.py
  ui/                                      PySide6: main window, pages, PDF view, reflow reader, workers
```

## Storage
Two SQLite files are attached to every connection: `atlas.db` (user data) and `index.db` (segments + FTS5, `unicode61 remove_diacritics 2`, prefix indexes). Index rows carry `loc_type / loc_num / loc_label / char_start` (PDF page, EPUB chapter index) so a hit can be opened at its location. Writes use short `BEGIN IMMEDIATE` transactions (WAL, 30 s busy timeout); per-book index writes are batched so UI read connections are never blocked for long. A crash in the middle of a book leaves it `pending`, and it is re-indexed on resume. Atomicity across the two files is achieved by status ordering, not a single transaction.

## Library roots
Books store `root_id + rel_path`; the absolute path is `root.path + rel_path`. A drive-letter change rebases the root path (volume serial match). Moved files are matched by quick fingerprint (size + sampled head/middle/tail SHA-1) against records whose old file no longer exists. Identical copies at different paths become separate records flagged in *Duplicates*; nothing is deleted. Nested or duplicate roots are rejected so one physical library cannot be indexed twice. `storage.py` is the seam for future SMB/UNC support (probes run with timeouts).

## Readers
PDF: custom `QAbstractScrollArea` rendering pages with PyMuPDF on a worker pool (all MuPDF calls under one lock), word-level selection, highlight overlays. EPUB/HTML/MD/TXT/DOCX/RTF: sanitised HTML in `QTextBrowser` (no scripts, no network or disk resource loading). Reflow annotations store chapter + character offsets + quote/prefix/suffix; on load they are verified and only re-anchored when the quote is unambiguous, otherwise they are flagged ⚠.

## Security
Documents are untrusted: ZIP-based formats are checked for path traversal, oversized entries and suspicious compression ratios and are never extracted to disk; HTML is sanitised (scripts, styles, forms, iframes, remote images removed); images are size-limited and decoded by Pillow/Qt with pixel limits; all SQL is parameterised; user search text is always quoted before reaching FTS5; logs never contain document text; no DRM is circumvented.

## Search internals
`search/query.py` turns user syntax into a quoted FTS5 MATCH string and also returns the positive terms/phrases for highlighting. `search/engine.py` runs one window-function query (per-book counts + top passages per book) over the FTS hits joined to `seg_meta`, a narrow location table kept in sync by triggers (so the join never touches the wide text rows), fetches the text of only the displayed passages, and builds excerpts with `search/excerpt.py` (case/diacritic-insensitive, length-preserving folding). Semantic hits come from a separate store (`semantic.py`).

## Deviations from the brief
* **EbookLib is not used.** The EPUB adapter reads the package with `zipfile` / `ElementTree` / BeautifulSoup so it can enforce size, ratio and path limits and never extract to disk.
* **PyMuPDF is pinned `<2`:** 2.x pulls in ONNX/NumPy layout dependencies that are not needed and break the PyInstaller build.
* **MOBI is parsed by our own code** (`parsers/mobi.py`), written from the public format description, to avoid GPL dependencies.
* **Reflowable formats share one reader widget** (`QTextBrowser`, with the book's CSS reduced to a safe subset); PDF has its own. QtWebEngine was evaluated and rejected for now (~300 MB larger, harder to sandbox, text-offset locators would need a JS bridge). EPUB "paginated" mode is viewport paging, not true page layout.
* **Semantic search is optional and not bundled** (see SEMANTIC_SEARCH.md); the machinery is tested with a fake provider only.
* **Indexing threads:** non-PDF formats parse in parallel; PDF parsing is serialised by the MuPDF lock.
* **Network shares:** supported with retries/timeouts and mapped-drive/UNC identity, but not tested against a real NAS.
* **The installer is built with Inno Setup 6** from `build_tools/installer.iss`; it is not code-signed.
