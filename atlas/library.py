"""Library model: roots, books, metadata, collections, duplicates, statistics."""
from __future__ import annotations

import logging
import os
import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import storage
from .db import Database

log = logging.getLogger(__name__)

EDITABLE_FIELDS = {
    "title", "publisher", "year", "isbn", "language", "series", "series_index",
    "description", "reading_status", "rating", "favorite", "personal_notes",
}
READING_STATUSES = ("unread", "reading", "finished")

SORT_COLUMNS = {
    "title": "b.title COLLATE NOCASE",
    "author": "b.author_sort COLLATE NOCASE",
    "added": "b.import_date",
    "year": "b.year",
    "format": "b.format",
    "size": "b.size",
    "rating": "b.rating",
    "last_read": "b.last_read",
}

BOOK_COLUMNS = """b.id, b.uuid, b.title, b.author_sort AS authors, b.format, b.size, b.year, b.rating,
    b.favorite, b.available, b.index_status, b.reading_status, b.cover_path, b.language, b.import_date,
    b.last_read, b.root_id, b.rel_path, b.page_count"""


@dataclass
class BookFilter:
    text: str = ""
    author: str = ""
    tag_id: int | None = None
    genre: str = ""
    subject: str = ""
    format: str = ""
    language: str = ""
    year_from: int | None = None
    year_to: int | None = None
    rating_min: int = 0
    status: str = ""
    collection_id: int | None = None
    root_id: int | None = None
    favorite: bool = False
    available: bool | None = None
    index_status: str = ""
    added_since: str = ""
    folder: str = ""
    book_ids: Sequence[int] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v not in ("", None, 0, False)}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "BookFilter":
        f = cls()
        for k, v in (d or {}).items():
            if hasattr(f, k):
                setattr(f, k, v)
        return f

    def sql(self, db: Database) -> tuple[str, list[Any]]:
        """WHERE fragments + parameters against books aliased as ``b``."""
        w: list[str] = []
        p: list[Any] = []
        if self.text:
            like = f"%{_escape_like(self.text)}%"
            w.append("(b.title LIKE ? ESCAPE '\\' OR b.author_sort LIKE ? ESCAPE '\\' OR b.original_filename LIKE ? ESCAPE '\\')")
            p += [like, like, like]
        if self.author:
            w.append("EXISTS (SELECT 1 FROM book_authors ba JOIN authors a ON a.id=ba.author_id WHERE ba.book_id=b.id AND a.name=?)")
            p.append(self.author)
        if self.tag_id:
            ids = descendant_tag_ids(db, self.tag_id)
            w.append(f"EXISTS (SELECT 1 FROM book_tags bt WHERE bt.book_id=b.id AND bt.tag_id IN ({','.join('?' * len(ids))}))")
            p += ids
        for kind, val in (("genre", self.genre), ("subject", self.subject)):
            if val:
                w.append("EXISTS (SELECT 1 FROM book_terms t WHERE t.book_id=b.id AND t.kind=? AND t.value=?)")
                p += [kind, val]
        if self.format:
            w.append("b.format=?"); p.append(self.format)
        if self.language:
            w.append("b.language=?"); p.append(self.language)
        if self.year_from:
            w.append("b.year>=?"); p.append(self.year_from)
        if self.year_to:
            w.append("b.year<=?"); p.append(self.year_to)
        if self.rating_min:
            w.append("b.rating>=?"); p.append(self.rating_min)
        if self.status:
            w.append("b.reading_status=?"); p.append(self.status)
        if self.collection_id:
            w.append("EXISTS (SELECT 1 FROM collection_books cb WHERE cb.book_id=b.id AND cb.collection_id=?)")
            p.append(self.collection_id)
        if self.root_id:
            w.append("b.root_id=?"); p.append(self.root_id)
        if self.favorite:
            w.append("b.favorite=1")
        if self.available is not None:
            w.append("b.available=?"); p.append(1 if self.available else 0)
        if self.index_status:
            w.append("b.index_status=?"); p.append(self.index_status)
        if self.added_since:
            w.append("b.import_date>=?"); p.append(self.added_since)
        if self.folder:
            f = self.folder.replace("\\", "/").strip("/")
            w.append("REPLACE(b.rel_path,'\\','/') LIKE ? ESCAPE '\\'")
            p.append(_escape_like(f) + "/%")
        if self.book_ids is not None:
            ids = list(self.book_ids)
            w.append(f"b.id IN ({','.join('?' * len(ids))})" if ids else "0")
            p += ids
        return (" AND ".join(w) if w else "1"), p


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def descendant_tag_ids(db: Database, tag_id: int) -> list[int]:
    rows = db.conn.execute(
        "WITH RECURSIVE d(id) AS (SELECT ? UNION SELECT t.id FROM tags t JOIN d ON t.parent_id=d.id) SELECT id FROM d",
        (tag_id,)).fetchall()
    return [r[0] for r in rows]


class Library:
    def __init__(self, db: Database, covers_dir: Path | None = None) -> None:
        self.db = db
        self.covers_dir = covers_dir

    # ------------------------------------------------------------------ roots
    def add_root(self, path: str | Path, label: str = "", recursive: bool = True) -> int:
        norm = storage.normalize(path)
        key = storage.canonical_key(norm)     # mapped drive and UNC path to the same share compare equal
        for r in self.roots():
            rk = storage.canonical_key(r["path"])
            if key == rk:
                raise ValueError(f"This folder is already a library source: {r['path']}")
            if key.startswith(rk.rstrip("\\") + "\\"):
                raise ValueError(f"This folder is inside the existing source '{r['path']}'. Adding it again would index the same books twice.")
            if rk.startswith(key.rstrip("\\") + "\\"):
                raise ValueError(f"The existing source '{r['path']}' is inside this folder. Remove it first, or choose a different folder.")
        serial, vlabel = storage.volume_info(norm)
        with self.db.tx() as c:
            cur = c.execute(
                "INSERT INTO library_roots(uuid,path,label,recursive,volume_serial,volume_label,kind,online,last_seen)"
                " VALUES(?,?,?,?,?,?,?,1,datetime('now'))",
                (uuid.uuid4().hex, norm, label or os.path.basename(norm.rstrip("\\/")) or norm,
                 int(recursive), serial, vlabel, storage.kind_of(norm)))
            return cur.lastrowid

    def roots(self) -> list[sqlite3.Row]:
        return self.db.conn.execute("SELECT * FROM library_roots ORDER BY id").fetchall()

    def root(self, root_id: int) -> sqlite3.Row | None:
        return self.db.conn.execute("SELECT * FROM library_roots WHERE id=?", (root_id,)).fetchone()

    def update_root(self, root_id: int, **fields: Any) -> None:
        allowed = {"label", "enabled", "recursive"}
        sets = [k for k in fields if k in allowed]
        if not sets:
            return
        with self.db.tx() as c:
            c.execute(f"UPDATE library_roots SET {','.join(k + '=?' for k in sets)} WHERE id=?",
                      [fields[k] for k in sets] + [root_id])

    def remove_root(self, root_id: int, delete_records: bool = False) -> None:
        """Detach a source. Book records are only deleted if explicitly requested."""
        with self.db.tx() as c:
            if delete_records:
                ids = [r[0] for r in c.execute("SELECT id FROM books WHERE root_id=?", (root_id,))]
                for i in range(0, len(ids), 500):
                    chunk = ids[i:i + 500]
                    c.execute(f"DELETE FROM segments WHERE book_id IN ({','.join('?' * len(chunk))})", chunk)
                c.execute("DELETE FROM books WHERE root_id=?", (root_id,))
                c.execute("DELETE FROM library_roots WHERE id=?", (root_id,))
            else:
                c.execute("UPDATE library_roots SET enabled=0 WHERE id=?", (root_id,))

    def check_roots(self) -> list[dict[str, Any]]:
        """Refresh online/offline state, follow drive-letter changes, re-link books.

        Returns one event dict per root whose state changed.
        """
        events: list[dict[str, Any]] = []
        for r in self.roots():
            if not r["enabled"]:
                continue
            path = r["path"]
            online = storage.is_dir_available(path)
            new_path = path
            if not online and r["volume_serial"] and not storage.is_unc(path):
                drive = storage.find_drive_by_serial(r["volume_serial"])
                if drive:
                    cand = storage.rebase_to_drive(path, drive)
                    if cand != path and storage.is_dir_available(cand):
                        new_path, online = cand, True
                        events.append({"root_id": r["id"], "event": "drive_letter_changed", "old": path, "new": cand})
            if online:
                serial = r["volume_serial"]
                if not serial:
                    serial, vlabel = storage.volume_info(new_path)
                    if serial:
                        with self.db.tx() as c:
                            c.execute("UPDATE library_roots SET volume_serial=?, volume_label=? WHERE id=?", (serial, vlabel, r["id"]))
            was_online = bool(r["online"])
            with self.db.tx() as c:
                c.execute("UPDATE library_roots SET path=?, online=?, last_seen=CASE WHEN ? THEN datetime('now') ELSE last_seen END WHERE id=?",
                          (new_path, int(online), int(online), r["id"]))
                if not online:
                    c.execute("UPDATE books SET available=0 WHERE root_id=? AND available=1", (r["id"],))
            if online and not was_online:
                n = self.refresh_availability(r["id"])
                events.append({"root_id": r["id"], "event": "reconnected", "relinked": n})
            elif not online and was_online:
                events.append({"root_id": r["id"], "event": "went_offline"})
        return events

    def refresh_availability(self, root_id: int) -> int:
        """Re-check which books of an online root exist on disk. Returns number now available."""
        root = self.root(root_id)
        if root is None:
            return 0
        rows = self.db.conn.execute("SELECT id, rel_path, available FROM books WHERE root_id=?", (root_id,)).fetchall()
        changes: list[tuple[int, int]] = []
        for r in rows:
            exists = 1 if os.path.isfile(os.path.join(root["path"], r["rel_path"])) else 0
            if exists != r["available"]:
                changes.append((exists, r["id"]))
        if changes:
            with self.db.tx() as c:
                c.executemany("UPDATE books SET available=? WHERE id=?", changes)
        return sum(1 for e, _ in changes if e)

    def book_path(self, book: sqlite3.Row | dict | int) -> str:
        if isinstance(book, int):
            row = self.db.conn.execute(
                "SELECT r.path, b.rel_path FROM books b JOIN library_roots r ON r.id=b.root_id WHERE b.id=?", (book,)).fetchone()
            if row is None:
                raise KeyError(book)
            return os.path.join(row["path"], row["rel_path"])
        row = self.db.conn.execute(
            "SELECT r.path FROM library_roots r WHERE r.id=?", (book["root_id"],)).fetchone()
        return os.path.join(row["path"], book["rel_path"])

    def is_available(self, book_id: int) -> bool:
        r = self.db.conn.execute("SELECT available FROM books WHERE id=?", (book_id,)).fetchone()
        return bool(r and r[0])

    # ------------------------------------------------------------------ books
    def query_books(self, flt: BookFilter | None = None, sort: str = "title", desc: bool = False,
                    limit: int = 200, offset: int = 0) -> list[sqlite3.Row]:
        flt = flt or BookFilter()
        where, params = flt.sql(self.db)
        col = SORT_COLUMNS.get(sort, SORT_COLUMNS["title"])
        order = f"{col} {'DESC' if desc else 'ASC'}"
        if sort in ("last_read", "year", "rating"):
            order = f"({col.split()[0]} IS NULL), " + order
        sql = f"SELECT {BOOK_COLUMNS} FROM books b WHERE {where} ORDER BY {order}, b.id LIMIT ? OFFSET ?"
        return self.db.conn.execute(sql, params + [limit, offset]).fetchall()

    def count_books(self, flt: BookFilter | None = None) -> int:
        where, params = (flt or BookFilter()).sql(self.db)
        return self.db.conn.execute(f"SELECT COUNT(*) FROM books b WHERE {where}", params).fetchone()[0]

    def book_ids(self, flt: BookFilter) -> list[int]:
        where, params = flt.sql(self.db)
        return [r[0] for r in self.db.conn.execute(f"SELECT b.id FROM books b WHERE {where}", params)]

    def get_book(self, book_id: int) -> dict[str, Any] | None:
        c = self.db.conn
        r = c.execute("SELECT b.*, r.path AS root_path, r.label AS root_label, r.online AS root_online "
                      "FROM books b JOIN library_roots r ON r.id=b.root_id WHERE b.id=?", (book_id,)).fetchone()
        if r is None:
            return None
        d = dict(r)
        d["authors"] = [x[0] for x in c.execute(
            "SELECT a.name FROM book_authors ba JOIN authors a ON a.id=ba.author_id WHERE ba.book_id=? ORDER BY ba.pos", (book_id,))]
        for kind in ("genre", "subject"):
            d[kind + "s"] = [x[0] for x in c.execute(
                "SELECT value FROM book_terms WHERE book_id=? AND kind=? ORDER BY value", (book_id, kind))]
        d["tags"] = [dict(x) for x in c.execute(
            "SELECT t.id, t.name FROM book_tags bt JOIN tags t ON t.id=bt.tag_id WHERE bt.book_id=? ORDER BY t.name", (book_id,))]
        d["path"] = os.path.join(d["root_path"], d["rel_path"])
        d["collections"] = [dict(x) for x in c.execute(
            "SELECT c.id, c.name FROM collection_books cb JOIN collections c ON c.id=cb.collection_id WHERE cb.book_id=?", (book_id,))]
        return d

    def get_book_by_uuid(self, uid: str) -> dict[str, Any] | None:
        r = self.db.conn.execute("SELECT id FROM books WHERE uuid=?", (uid,)).fetchone()
        return self.get_book(r[0]) if r else None

    def set_authors(self, book_id: int, names: Iterable[str], conn: sqlite3.Connection | None = None) -> None:
        names = [n.strip() for n in names if n and n.strip()]
        seen: set[str] = set()
        uniq = []
        for n in names:
            if n.lower() not in seen:
                seen.add(n.lower()); uniq.append(n)
        with self.db.tx() as c:
            c.execute("DELETE FROM book_authors WHERE book_id=?", (book_id,))
            for pos, n in enumerate(uniq):
                c.execute("INSERT OR IGNORE INTO authors(name) VALUES(?)", (n,))
                aid = c.execute("SELECT id FROM authors WHERE name=?", (n,)).fetchone()[0]
                c.execute("INSERT INTO book_authors(book_id,author_id,pos) VALUES(?,?,?)", (book_id, aid, pos))
            c.execute("UPDATE books SET author_sort=? WHERE id=?", ("; ".join(uniq), book_id))

    def set_terms(self, book_id: int, kind: str, values: Iterable[str]) -> None:
        assert kind in ("genre", "subject")
        vals = []
        seen: set[str] = set()
        for v in values:
            v = v.strip()
            if v and v.lower() not in seen:
                seen.add(v.lower()); vals.append(v)
        with self.db.tx() as c:
            c.execute("DELETE FROM book_terms WHERE book_id=? AND kind=?", (book_id, kind))
            c.executemany("INSERT INTO book_terms(book_id,kind,value) VALUES(?,?,?)", [(book_id, kind, v) for v in vals])

    def update_book(self, book_id: int, authors: list[str] | None = None, genres: list[str] | None = None,
                    subjects: list[str] | None = None, mark_edited: bool = True, **fields: Any) -> None:
        bad = set(fields) - EDITABLE_FIELDS
        if bad:
            raise ValueError(f"Fields not editable: {sorted(bad)}")
        if "reading_status" in fields and fields["reading_status"] not in READING_STATUSES:
            raise ValueError("Invalid reading status")
        if "rating" in fields:
            fields["rating"] = max(0, min(5, int(fields["rating"] or 0)))
        for k in ("year", "series_index"):
            if k in fields and fields[k] in ("", None):
                fields[k] = None
        if "year" in fields and fields["year"] is not None:
            fields["year"] = int(fields["year"])
        with self.db.tx() as c:
            if fields:
                sets = ",".join(f"{k}=?" for k in fields)
                meta_edit = mark_edited and any(k not in ("reading_status", "rating", "favorite", "personal_notes") for k in fields)
                c.execute(f"UPDATE books SET {sets}{', metadata_edited=1' if meta_edit else ''} WHERE id=?",
                          list(fields.values()) + [book_id])
            if authors is not None:
                self.set_authors(book_id, authors)
                if mark_edited:
                    c.execute("UPDATE books SET metadata_edited=1 WHERE id=?", (book_id,))
            if genres is not None:
                self.set_terms(book_id, "genre", genres)
            if subjects is not None:
                self.set_terms(book_id, "subject", subjects)

    def bulk_update(self, book_ids: Sequence[int], add_tags: Sequence[int] = (), remove_tags: Sequence[int] = (),
                    add_to_collection: int | None = None, add_genres: Sequence[str] = (), **fields: Any) -> None:
        with self.db.tx():
            for bid in book_ids:
                if fields:
                    self.update_book(bid, **fields)
                for g in add_genres:
                    cur = [r[0] for r in self.db.conn.execute("SELECT value FROM book_terms WHERE book_id=? AND kind='genre'", (bid,))]
                    self.set_terms(bid, "genre", cur + [g])
            c = self.db.conn
            c.executemany("INSERT OR IGNORE INTO book_tags(book_id,tag_id) VALUES(?,?)", [(b, t) for b in book_ids for t in add_tags])
            c.executemany("DELETE FROM book_tags WHERE book_id=? AND tag_id=?", [(b, t) for b in book_ids for t in remove_tags])
            if add_to_collection:
                c.executemany("INSERT OR IGNORE INTO collection_books(collection_id,book_id) VALUES(?,?)",
                              [(add_to_collection, b) for b in book_ids])

    def set_favorite(self, book_ids: Sequence[int], value: bool) -> None:
        with self.db.tx() as c:
            c.executemany("UPDATE books SET favorite=? WHERE id=?", [(int(value), b) for b in book_ids])

    def purge_index(self, book_ids: Sequence[int]) -> int:
        """Remove extracted text for books (records, notes and annotations stay)."""
        n = 0
        with self.db.tx() as c:
            for b in book_ids:
                n += c.execute("DELETE FROM segments WHERE book_id=?", (b,)).rowcount
                c.execute("UPDATE books SET index_status='pending', index_detail='Index removed by user', last_indexed=NULL WHERE id=?", (b,))
        return n

    def delete_book_record(self, book_id: int) -> None:
        """Forget a book (never touches the file). Annotations are removed with it; clippings keep their copy."""
        with self.db.tx() as c:
            c.execute("DELETE FROM segments WHERE book_id=?", (book_id,))
            c.execute("DELETE FROM books WHERE id=?", (book_id,))

    # ---------------------------------------------------------------- listings
    def distinct_values(self, what: str) -> list[str]:
        c = self.db.conn
        if what == "author":
            return [r[0] for r in c.execute("SELECT name FROM authors WHERE id IN (SELECT author_id FROM book_authors) ORDER BY name COLLATE NOCASE")]
        if what in ("genre", "subject"):
            return [r[0] for r in c.execute("SELECT DISTINCT value FROM book_terms WHERE kind=? ORDER BY value COLLATE NOCASE", (what,))]
        if what in ("format", "language"):
            return [r[0] for r in c.execute(f"SELECT DISTINCT {what} FROM books WHERE {what}<>'' ORDER BY 1")]
        raise ValueError(what)

    # ------------------------------------------------------------- collections
    def collections(self) -> list[sqlite3.Row]:
        return self.db.conn.execute(
            "SELECT c.*, (SELECT COUNT(*) FROM collection_books cb WHERE cb.collection_id=c.id) AS n FROM collections c ORDER BY c.name").fetchall()

    def create_collection(self, name: str, description: str = "") -> int:
        name = name.strip()
        if not name:
            raise ValueError("A collection needs a name.")
        with self.db.tx() as c:
            try:
                return c.execute("INSERT INTO collections(name,description) VALUES(?,?)", (name, description)).lastrowid
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"A collection named '{name}' already exists.") from exc

    def rename_collection(self, cid: int, name: str) -> None:
        with self.db.tx() as c:
            try:
                c.execute("UPDATE collections SET name=? WHERE id=?", (name.strip(), cid))
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"A collection named '{name}' already exists.") from exc

    def delete_collection(self, cid: int) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM collections WHERE id=?", (cid,))

    def add_to_collection(self, cid: int, book_ids: Sequence[int]) -> None:
        with self.db.tx() as c:
            c.executemany("INSERT OR IGNORE INTO collection_books(collection_id,book_id) VALUES(?,?)", [(cid, b) for b in book_ids])

    def remove_from_collection(self, cid: int, book_ids: Sequence[int]) -> None:
        with self.db.tx() as c:
            c.executemany("DELETE FROM collection_books WHERE collection_id=? AND book_id=?", [(cid, b) for b in book_ids])

    # -------------------------------------------------------------- duplicates
    def duplicate_groups(self) -> list[dict[str, Any]]:
        """Groups of books with identical fingerprint, or same title+first author but different content.

        Nothing is deleted; the caller shows groups for inspection only.
        """
        c = self.db.conn
        groups: list[dict[str, Any]] = []
        for r in c.execute("SELECT fingerprint, COUNT(*) n FROM books WHERE fingerprint<>'' GROUP BY fingerprint HAVING n>1"):
            ids = [x[0] for x in c.execute("SELECT id FROM books WHERE fingerprint=? ORDER BY id", (r[0],))]
            groups.append({"kind": "identical", "key": r[0], "book_ids": ids})
        in_identical = {i for g in groups for i in g["book_ids"]}
        for r in c.execute(
                "SELECT lower(title) t, lower(author_sort) a, COUNT(*) n FROM books WHERE author_sort<>'' AND title<>'' "
                "GROUP BY t, a HAVING n>1"):
            ids = [x[0] for x in c.execute("SELECT id FROM books WHERE lower(title)=? AND lower(author_sort)=? ORDER BY id", (r[0], r[1]))]
            if set(ids) <= in_identical and len(ids) == len(set(ids) & in_identical):
                continue
            groups.append({"kind": "similar", "key": f"{r[0]} / {r[1]}", "book_ids": ids})
        return groups

    # ------------------------------------------------------------------- stats
    def stats(self) -> dict[str, Any]:
        c = self.db.conn
        one = lambda q, *a: c.execute(q, a).fetchone()[0]
        return {
            "books": one("SELECT COUNT(*) FROM books"),
            "available": one("SELECT COUNT(*) FROM books WHERE available=1"),
            "offline": one("SELECT COUNT(*) FROM books WHERE available=0"),
            "indexed": one("SELECT COUNT(*) FROM books WHERE index_status IN ('indexed','partial')"),
            "pending": one("SELECT COUNT(*) FROM books WHERE index_status='pending' AND available=1"),
            "failed": one("SELECT COUNT(*) FROM books WHERE index_status IN ('failed','encrypted','unsupported','ocr_needed')"),
            "authors": one("SELECT COUNT(*) FROM authors WHERE id IN (SELECT author_id FROM book_authors)"),
            "tags": one("SELECT COUNT(*) FROM tags"),
            "bytes": one("SELECT COALESCE(SUM(size),0) FROM books"),
            "segments": one("SELECT COUNT(*) FROM segments"),
            "annotations": one("SELECT COUNT(*) FROM annotations"),
            "clippings": one("SELECT COUNT(*) FROM clippings"),
            "topics": one("SELECT COUNT(*) FROM topics"),
            "finished": one("SELECT COUNT(*) FROM books WHERE reading_status='finished'"),
        }

    def review_candidates(self) -> list[dict[str, Any]]:
        """Books whose metadata is incomplete or suspicious."""
        out = []
        rows = self.db.conn.execute(
            "SELECT id, title, author_sort, year, publisher, original_filename, language, metadata_edited, format FROM books ORDER BY title COLLATE NOCASE").fetchall()
        for r in rows:
            issues = []
            stem = os.path.splitext(r["original_filename"])[0]
            if not r["author_sort"]:
                issues.append("no author")
            if not r["year"]:
                issues.append("no year")
            if r["title"] == stem:
                issues.append("title taken from filename")
            if len(r["title"]) < 3 or r["title"].lower() in ("untitled", "microsoft word", "document"):
                issues.append("suspicious title")
            if r["year"] and not (1000 <= r["year"] <= 2100):
                issues.append("implausible year")
            if not r["language"]:
                issues.append("no language")
            if issues and not r["metadata_edited"]:
                out.append({"id": r["id"], "title": r["title"], "authors": r["author_sort"], "issues": issues})
        return out
