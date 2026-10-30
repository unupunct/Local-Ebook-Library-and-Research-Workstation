# Project status — Atlas Library 1.1.0

_Last updated 2026-10-30. Everything below was run on the development machine (Windows 11, Python 3.13, PySide6 6.12, PyMuPDF 1.28)._

## Phases
All seven phases of the original brief are implemented (foundation, library, search, reading, research, reliability, delivery). Version 1.1.0 adds: faster search, safer-CSS EPUB rendering, MOBI/AZW support, an optional semantic-search layer, network-share hardening, installer + signing scripts, and several UI improvements (see CHANGELOG.md).

## Test results (actual)
`pytest -q` → **65 passed, 1 skipped** (the skipped test runs real OCR and needs Tesseract, which is not installed here).
* `tests/test_core.py` — nested import, re-import without duplicates, PDF page and EPUB chapter references, phrases/AND/OR/NOT/prefix, filters + query, excerpt highlighting (prefix, diacritics, negation), malformed/unsupported/scanned files, offline roots + reconnect, moved/deleted/changed files, duplicates, overlapping roots, drive-letter change (**simulated** by patching the Windows volume lookup), mapped-drive/UNC identity (**simulated**), flaky-share retry and unreachable-source abort (**simulated** by patching `os.scandir`), tags, auto-tag suggestions, bulk edit, collections, Romanian diacritics, query-syntax errors, separate index file, in-place index upgrade (seg_meta backfill).
* `tests/test_knowledge.py` — progress, annotations (incl. invalidation after the source changes), clippings surviving offline/forgotten books, topics/backlinks, exports, backup + restore, pruning, index check + repair, cancel/resume, concurrent indexing, read latency during indexing, OCR-unavailable message, Open Library parsing.
* `tests/test_mobi.py` — PalmDOC decompression (incl. back-references), MOBI metadata/chapters/cover/Unicode, DRM and Huffman files reported, truncated/garbage files, import → search → reader.
* `tests/test_semantic.py` — vector store, incremental build, stale removal, cancel, model change, ranking by meaning, separation from the keyword index (with a deterministic **fake** provider; no real model was run).
* `tests/test_filename_meta.py` — filename patterns; only empty fields are filled.
* `tests/test_ui.py` (offscreen Qt) — all pages, global search + syntax errors, PDF/EPUB result opens at the right page/chapter, progress saved and resumed, highlight re-anchoring after font change, CSS subset applied and unsafe CSS dropped, offline book message with notes reachable, badges, drag-and-drop mime + collection drop, remembered research query, UI event loop stays responsive during background indexing.
* Packaged build: `AtlasLibrary.exe --selftest` → `SELFTEST OK`; the GUI exe starts, creates its database under the data directory, and exits cleanly. Installer built with Inno Setup 6 (see the release); the installer itself was not run end to end.

## Measured performance (`scripts/benchmark.py 2000`: synthetic PDF/EPUB/TXT mix, 34 MB, 2 indexing threads)
* Indexing: 2,000 documents in 47 s (42 docs/s, 0.72 MB/s); database + index 53 MB for 10,000 passages
* Search (median, includes excerpts for up to 200 books × 5 passages): 131 ms for a word in every passage (10,000 hits), 139 ms for AND, 86 ms for a phrase (4,492 hits), 136 ms for a prefix matching everything, ~2 ms for a rare term. (Version 1.0.0 took 180–1,300 ms for the same queries.)
* Peak working set of the benchmark process: 77 MB
Single measurements on one machine, **not** tested at the 10 GB / tens-of-thousands-of-books scale.

## Known limitations / not verified
* Never tested on a real 10 GB collection, real scanned books, or real-world EPUBs/MOBIs with complex layouts. `QTextBrowser` supports only simple HTML/CSS: books keep paragraph styling but not fonts, colours or complex layout.
* MOBI: only unencrypted MOBI6 is verified (with synthetic files built from the format description). **AZW3/KF8 is untested** (best-effort text, no chapters). DRM and Huffman-compressed files are reported, not read.
* GUI behaviour was exercised programmatically and by screenshots, not by a human clicking through everything: PDF drag-selection, highlight-click editing, drag-and-drop tag moves and real drag-and-drop of books onto collections, the first-run wizard, scheduled scans, Explorer integration and restore-then-restart were **not** exercised end to end.
* OCR code is implemented but untested against a real Tesseract install.
* Network sources: retries, mapped-drive identity and abort-on-loss are tested only with simulated failures, never against a real NAS; real drive-letter changes were only simulated.
* Semantic search is not bundled and only tested with a fake provider; the optional packages are not in the exe.
* The executable and installer are unsigned (signing scripts exist; a self-signed signature was verified to apply, but it does not remove SmartScreen warnings).
* Legacy `.doc`, DjVu are unsupported; RTF loses formatting.
* EPUB "paginated" mode scrolls by viewport; locators are chapter + character offset, not CFI.
* PDF text selection depends on PyMuPDF word order; multi-column layouts may select oddly.
* No license has been chosen; PyMuPDF is AGPL, which matters for redistribution.

## Possible future work
A real semantic provider tested with an actual model; AZW3/KF8 chapter reconstruction; EPUB CFI locators or WebEngine rendering; real NAS testing; in-app duplicate resolution helper; signed releases; automatic update check.
