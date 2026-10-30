"""Hierarchical tags and rules-based auto-tagging suggestions."""
from __future__ import annotations

import fnmatch
import os
import re
import sqlite3
from typing import Any, Sequence

from .db import Database
from .library import descendant_tag_ids


class TagError(ValueError):
    pass


class Tags:
    def __init__(self, db: Database) -> None:
        self.db = db

    # -------------------------------------------------------------- structure
    def all(self) -> list[sqlite3.Row]:
        return self.db.conn.execute(
            "SELECT t.id, t.name, t.parent_id, "
            "(SELECT COUNT(*) FROM book_tags bt WHERE bt.tag_id=t.id) AS n FROM tags t ORDER BY t.name COLLATE NOCASE").fetchall()

    def tree(self) -> list[dict[str, Any]]:
        rows = self.all()
        nodes = {r["id"]: {"id": r["id"], "name": r["name"], "parent_id": r["parent_id"], "count": r["n"], "children": []} for r in rows}
        roots = []
        for n in nodes.values():
            p = nodes.get(n["parent_id"])
            (p["children"] if p else roots).append(n)
        return roots

    def path(self, tag_id: int) -> str:
        parts = []
        cur: int | None = tag_id
        seen = set()
        while cur and cur not in seen:
            seen.add(cur)
            r = self.db.conn.execute("SELECT name, parent_id FROM tags WHERE id=?", (cur,)).fetchone()
            if not r:
                break
            parts.append(r["name"])
            cur = r["parent_id"]
        return " / ".join(reversed(parts))

    def create(self, name: str, parent_id: int | None = None) -> int:
        name = name.strip()
        if not name:
            raise TagError("A tag needs a name.")
        with self.db.tx() as c:
            ex = c.execute("SELECT id FROM tags WHERE name=? AND COALESCE(parent_id,0)=?", (name, parent_id or 0)).fetchone()
            if ex:
                raise TagError(f"A tag named '{name}' already exists here.")
            return c.execute("INSERT INTO tags(name,parent_id) VALUES(?,?)", (name, parent_id)).lastrowid

    def get_or_create_path(self, path: str) -> int:
        """'Gardening/Permaculture' -> id of the leaf, creating missing levels."""
        parent = None
        tid = None
        for part in [p.strip() for p in re.split(r"[/>]", path) if p.strip()]:
            r = self.db.conn.execute("SELECT id FROM tags WHERE name=? AND COALESCE(parent_id,0)=?", (part, parent or 0)).fetchone()
            tid = r[0] if r else self.create(part, parent)
            parent = tid
        if tid is None:
            raise TagError("Empty tag path.")
        return tid

    def rename(self, tag_id: int, name: str) -> None:
        name = name.strip()
        if not name:
            raise TagError("A tag needs a name.")
        with self.db.tx() as c:
            r = c.execute("SELECT parent_id FROM tags WHERE id=?", (tag_id,)).fetchone()
            dup = c.execute("SELECT id FROM tags WHERE name=? AND COALESCE(parent_id,0)=? AND id<>?", (name, r["parent_id"] or 0, tag_id)).fetchone()
            if dup:
                raise TagError(f"A tag named '{name}' already exists here. Use Merge instead.")
            c.execute("UPDATE tags SET name=? WHERE id=?", (name, tag_id))

    def move(self, tag_id: int, new_parent: int | None) -> None:
        if new_parent == tag_id or (new_parent and new_parent in descendant_tag_ids(self.db, tag_id)):
            raise TagError("A tag cannot be moved under itself.")
        with self.db.tx() as c:
            name = c.execute("SELECT name FROM tags WHERE id=?", (tag_id,)).fetchone()[0]
            dup = c.execute("SELECT id FROM tags WHERE name=? AND COALESCE(parent_id,0)=? AND id<>?", (name, new_parent or 0, tag_id)).fetchone()
            if dup:
                raise TagError(f"The target already contains a tag named '{name}'. Merge them instead.")
            c.execute("UPDATE tags SET parent_id=? WHERE id=?", (new_parent, tag_id))

    def merge(self, source_id: int, target_id: int) -> None:
        """Move all books/clippings/topics of source to target, re-parent children, delete source."""
        if source_id == target_id:
            return
        if target_id in descendant_tag_ids(self.db, source_id):
            raise TagError("Cannot merge a tag into its own descendant.")
        with self.db.tx() as c:
            for tbl, col in (("book_tags", "book_id"), ("clipping_tags", "clipping_id"), ("topic_tags", "topic_id")):
                c.execute(f"INSERT OR IGNORE INTO {tbl}({col},tag_id) SELECT {col}, ? FROM {tbl} WHERE tag_id=?", (target_id, source_id))
                c.execute(f"DELETE FROM {tbl} WHERE tag_id=?", (source_id,))
            c.execute("UPDATE autotag_rules SET tag_id=? WHERE tag_id=?", (target_id, source_id))
            c.execute("UPDATE OR IGNORE tag_suggestions SET tag_id=? WHERE tag_id=?", (target_id, source_id))
            for ch in c.execute("SELECT id, name FROM tags WHERE parent_id=?", (source_id,)).fetchall():
                clash = c.execute("SELECT id FROM tags WHERE name=? AND parent_id=?", (ch["name"], target_id)).fetchone()
                if clash:
                    self.merge(ch["id"], clash["id"])
                else:
                    c.execute("UPDATE tags SET parent_id=? WHERE id=?", (target_id, ch["id"]))
            c.execute("DELETE FROM tags WHERE id=?", (source_id,))

    def delete(self, tag_id: int, with_children: bool = False) -> None:
        """Delete a tag. Books are never deleted; children move up one level unless with_children."""
        with self.db.tx() as c:
            if with_children:
                for i in descendant_tag_ids(self.db, tag_id):
                    c.execute("DELETE FROM tags WHERE id=?", (i,))
            else:
                parent = c.execute("SELECT parent_id FROM tags WHERE id=?", (tag_id,)).fetchone()
                if parent is None:
                    return
                for ch in c.execute("SELECT id, name FROM tags WHERE parent_id=?", (tag_id,)).fetchall():
                    clash = c.execute("SELECT id FROM tags WHERE name=? AND COALESCE(parent_id,0)=?", (ch["name"], parent[0] or 0)).fetchone()
                    if clash:
                        self.merge(ch["id"], clash["id"])
                    else:
                        c.execute("UPDATE tags SET parent_id=? WHERE id=?", (parent[0], ch["id"]))
                c.execute("DELETE FROM tags WHERE id=?", (tag_id,))

    # ----------------------------------------------------------- assignments
    def add_to_books(self, tag_id: int, book_ids: Sequence[int]) -> None:
        with self.db.tx() as c:
            c.executemany("INSERT OR IGNORE INTO book_tags(book_id,tag_id) VALUES(?,?)", [(b, tag_id) for b in book_ids])
            c.executemany("DELETE FROM tag_suggestions WHERE book_id=? AND tag_id=?", [(b, tag_id) for b in book_ids])

    def remove_from_books(self, tag_id: int, book_ids: Sequence[int]) -> None:
        with self.db.tx() as c:
            c.executemany("DELETE FROM book_tags WHERE book_id=? AND tag_id=?", [(b, tag_id) for b in book_ids])

    def book_tags(self, book_id: int) -> list[sqlite3.Row]:
        return self.db.conn.execute(
            "SELECT t.id, t.name FROM book_tags bt JOIN tags t ON t.id=bt.tag_id WHERE bt.book_id=?", (book_id,)).fetchall()

    def popular(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.db.conn.execute(
            "SELECT t.id, t.name, COUNT(*) n FROM book_tags bt JOIN tags t ON t.id=bt.tag_id GROUP BY t.id ORDER BY n DESC, t.name LIMIT ?",
            (limit,)).fetchall()

    # ----------------------------------------------------------------- rules
    def add_rule(self, kind: str, pattern: str, tag_id: int, auto_apply: bool = False, min_keyword_hits: int = 5) -> int:
        if not pattern.strip():
            raise TagError("A rule needs a pattern.")
        if kind == "keyword" and len(pattern.strip()) < 4:
            raise TagError("Keyword rules need at least 4 characters to avoid weak matches.")
        with self.db.tx() as c:
            return c.execute("INSERT INTO autotag_rules(kind,pattern,tag_id,auto_apply,min_keyword_hits) VALUES(?,?,?,?,?)",
                             (kind, pattern.strip(), tag_id, int(auto_apply), max(2, min_keyword_hits))).lastrowid

    def rules(self) -> list[sqlite3.Row]:
        return self.db.conn.execute("SELECT * FROM autotag_rules ORDER BY id").fetchall()

    def delete_rule(self, rule_id: int) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM autotag_rules WHERE id=?", (rule_id,))

    def evaluate_rules(self, book_ids: Sequence[int] | None = None) -> int:
        """Create tag suggestions (or apply, for auto_apply rules). Returns number created/applied.

        Keyword rules count whole-word hits in the indexed text and require
        ``min_keyword_hits`` so a single passing mention never classifies a book.
        """
        c = self.db.conn
        rules = [r for r in self.rules() if r["enabled"]]
        if not rules:
            return 0
        q = "SELECT b.id, b.rel_path, b.original_filename, b.title, b.author_sort, b.publisher FROM books b"
        params: list[Any] = []
        if book_ids is not None:
            ids = list(book_ids)
            q += f" WHERE b.id IN ({','.join('?' * len(ids))})" if ids else " WHERE 0"
            params = ids
        count = 0
        for b in c.execute(q, params).fetchall():
            have = {r[0] for r in c.execute("SELECT tag_id FROM book_tags WHERE book_id=?", (b["id"],))}
            for r in rules:
                if r["tag_id"] in have:
                    continue
                reason = self._match(r, b)
                if not reason:
                    continue
                with self.db.tx() as w:
                    if r["auto_apply"]:
                        w.execute("INSERT OR IGNORE INTO book_tags(book_id,tag_id) VALUES(?,?)", (b["id"], r["tag_id"]))
                        w.execute("DELETE FROM tag_suggestions WHERE book_id=? AND tag_id=?", (b["id"], r["tag_id"]))
                    else:
                        w.execute("INSERT OR IGNORE INTO tag_suggestions(book_id,tag_id,rule_id,reason) VALUES(?,?,?,?)",
                                  (b["id"], r["tag_id"], r["id"], reason))
                count += 1
        return count

    def _match(self, rule: sqlite3.Row, b: sqlite3.Row) -> str:
        pat = rule["pattern"]
        kind = rule["kind"]
        if kind == "directory":
            dirs = os.path.dirname(b["rel_path"]).replace("\\", "/").split("/")
            if any(fnmatch.fnmatch(d.lower(), pat.lower()) for d in dirs if d):
                return f"folder name matches '{pat}'"
        elif kind == "filename":
            if fnmatch.fnmatch(b["original_filename"].lower(), pat.lower()):
                return f"filename matches '{pat}'"
        elif kind == "metadata":
            hay = " ".join([b["title"], b["author_sort"], b["publisher"]]).lower()
            subj = " ".join(r[0] for r in self.db.conn.execute("SELECT value FROM book_terms WHERE book_id=?", (b["id"],))).lower()
            if re.search(r"\b" + re.escape(pat.lower()) + r"\b", hay + " " + subj):
                return f"title/author/subject contains '{pat}'"
        elif kind == "keyword":
            from .search.query import build_match
            m = build_match(f'"{pat}"').match
            n = self.db.conn.execute(
                "SELECT COUNT(*) FROM segments_fts WHERE segments_fts MATCH ? AND rowid IN (SELECT id FROM segments WHERE book_id=?)",
                (m, b["id"])).fetchone()[0]
            if n >= rule["min_keyword_hits"]:
                return f"'{pat}' appears in {n} passages"
        return ""

    def suggestions(self, limit: int = 500) -> list[sqlite3.Row]:
        return self.db.conn.execute(
            "SELECT s.book_id, s.tag_id, s.reason, b.title, t.name AS tag FROM tag_suggestions s "
            "JOIN books b ON b.id=s.book_id JOIN tags t ON t.id=s.tag_id ORDER BY b.title LIMIT ?", (limit,)).fetchall()

    def accept_suggestion(self, book_id: int, tag_id: int) -> None:
        self.add_to_books(tag_id, [book_id])

    def reject_suggestion(self, book_id: int, tag_id: int) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM tag_suggestions WHERE book_id=? AND tag_id=?", (book_id, tag_id))
