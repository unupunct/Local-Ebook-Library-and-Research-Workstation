# Backup and restore

## What lives where

| Data | Location (default) | Back up? |
|---|---|---|
| Your book files | wherever you keep them (internal / USB / NAS) | **Yes – separately.** Atlas never copies or backs them up. |
| Library database `atlas.db` (metadata, tags, collections, annotations, clippings, notebook, saved searches, reading progress) | `%LOCALAPPDATA%\AtlasLibrary\db` | **Yes – irreplaceable** |
| Search index `index.db` (extracted text + FTS5) | same folder | No – rebuildable (optional in backups) |
| Cover cache, OCR output, logs | `%LOCALAPPDATA%\AtlasLibrary\{cache,ocr,logs}` | No (rebuildable) |
| `settings.json` | `%LOCALAPPDATA%\AtlasLibrary` | Included in backups |

## Backups
Settings → Backup: automatic every N days (default 7, checked at startup), keep the last N, optionally include the index. File → *Back up database now* does it on demand. A backup is `atlas-backup-YYYYmmdd-HHMMSS.zip`, created with SQLite's online-backup API, so it is consistent while the app is running. Copy the backup folder to another disk or cloud yourself for real safety.

## Restore
Settings → Backup → *Restore from backup…*. The backup is verified (integrity and version check), the current database is copied to `*.before-restore-<time>`, replaced, and the app closes. On the next start, if the backup had no index, all books are re-queued and re-indexed (automatically, or via *Resume / index pending*).

Manual restore (app closed): unzip, copy `atlas.db` (and `index.db` if present) into the database folder, and delete any `*-wal` / `*-shm` files next to them.

## Moving to a new PC
Install the app, restore the latest backup, add your book folder in *Import and Indexing*. Books are matched by path relative to the source, so a new drive letter does not matter; moved files are re-linked by fingerprint.

## Index damage
Import and Indexing → *Check index integrity* / *Repair / rebuild search index* (rebuilds FTS5 from stored text, drops orphans, re-queues books with missing text).

## Updating the application
Install or unzip the new version over/next to the old one. The database is not in the installation folder and is migrated automatically on first start (schema versions are tracked; a database from a newer version is refused rather than damaged). Make a backup first when in doubt.
