# Troubleshooting

* **Logs:** Help → Open log folder (`%LOCALAPPDATA%\AtlasLibrary\logs\atlas.log`, rotating). Logs contain file names and errors, never document text. Raise the detail in Settings → General → Log level.
* **Windows SmartScreen warns on first start:** the build is unsigned. Choose *More info → Run anyway*, or sign the executable with your own certificate.
* **A book shows "OFFLINE":** its drive or share is not reachable. Reconnect it and use Import → *Check availability*. Drive-letter changes are followed automatically (by volume serial number). Notes, annotations and progress stay usable meanwhile.
* **"Needs OCR":** the PDF has no text layer. Install Tesseract (UB-Mannheim Windows build), set the language in Settings → OCR, then Import → *Run OCR on selected PDF*.
* **"Encrypted / protected":** password-protected PDFs, DRM EPUBs and encrypted Office files are reported and skipped; Atlas does not bypass protection.
* **Search finds nothing:** check spelling or use a prefix (`compost*`); look at *Files needing attention*; check that the book is indexed (Library → details panel). Syntax: SEARCH_SYNTAX.md.
* **Indexing seems stuck:** large scanned PDFs are slow. Cancel is safe; unfinished books resume later.
* **Unexpected shutdown / crash:** the next start runs an index check automatically and offers a repair.
* **Database cannot be opened:** restore a backup (BACKUP_AND_RESTORE.md) or start with the environment variable `ATLAS_DATA_DIR` pointing to a fresh folder.
* **Network shares:** UNC paths (`\\server\share`) can be added but are untested; slow shares can make scans slow. See ARCHITECTURE.md.
* **Check an installation:** `AtlasLibrary.exe --selftest result.txt` indexes a synthetic library with every parser and writes PASS/FAIL lines.
