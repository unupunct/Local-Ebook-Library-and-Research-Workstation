# Changelog

## 1.1.0
* **Faster search:** one single-pass query over a narrow location table (`seg_meta`, added by an automatic index migration), excerpts and highlights generated in Python instead of FTS5 `snippet()`. On the 2,000-document benchmark worst-case queries went from 0.3–1.3 s to roughly 0.1–0.2 s.
* **Better EPUB/HTML rendering:** book stylesheets are reduced to a safe subset (alignment, indents, margins, emphasis, borders) so books keep their paragraph styling; colours, fonts, backgrounds and anything that loads resources are dropped. Large images are scaled to the reading width.
* **MOBI / PRC / AZW support:** own parser (no third-party GPL code) for unencrypted MOBI6 files — text, chapters, metadata, covers, images. AZW3/KF8 is best-effort (text without chapters). DRM and Huffman-compressed files are reported, not opened.
* **Semantic search provider (optional, local):** vector store, incremental builder, UI and a sentence-transformers provider. Not bundled; see docs/SEMANTIC_SEARCH.md.
* **Network share hardening:** mapped drives and UNC paths to the same share are recognised as one library; directory listings are retried with backoff; a scan that loses its source aborts without marking books missing; longer probe timeouts for network paths.
* **Packaging:** installer script builds with Inno Setup; optional code signing (`build.ps1 -Pfx`, `build_tools/sign.ps1`, `build_tools/make_cert.ps1`).
* **UI:** drag books onto collections, status badges on covers (offline / problem / waiting), "Fill from file names" in the metadata review (preview first, empty fields only), last Research query and filters are remembered.

## 1.0.0
Initial release: library, import/indexing, Research Mode, readers, notebook, backup/restore, Windows packaging.
