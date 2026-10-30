"""Research clippings and the research notebook (topic pages, links, backlinks)."""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from typing import Any, Sequence

from .db import Database

WIKILINK = re.compile(r"\[\[([^\[\]|]{1,200}?)\]\]")


class Research:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ---------------------------------------------------------------- clippings
    def create_clipping(self, text: str, book_id: int | None = None, locator: dict[str, Any] | None = None,
                        note: str = "", tag_ids: Sequence[int] = (), topic_id: int | None = None) -> int:
        """Save verbatim text with its source. Book details are copied so the clipping outlives the file."""
        text = text.strip()
        if not text:
            raise ValueError("A clipping needs text.")
        locator = locator or {}
        title = authors = path = buuid = ""
        if book_id is not None:
            b = self.db.conn.execute(
                "SELECT b.uuid, b.title, b.author_sort, b.original_filename, b.rel_path, r.path AS rp FROM books b "
                "JOIN library_roots r ON r.id=b.root_id WHERE b.id=?", (book_id,)).fetchone()
            if b is None:
                raise KeyError(book_id)
            buuid, title, authors = b["uuid"], b["title"], b["author_sort"]
            path = b["rp"].rstrip("\\/") + "\\" + b["rel_path"]
        loc_type = locator.get("type", "")
        loc_num = locator.get("num")
        if loc_type == "page" and loc_num is None:
            loc_num = locator.get("page")
        with self.db.tx() as c:
            cid = c.execute(
                "INSERT INTO clippings(uuid,book_id,book_uuid,book_title,book_authors,source_path,loc_type,loc_num,loc_label,locator,text,note)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, book_id, buuid, title, authors, path, loc_type, loc_num,
                 locator.get("label", ""), json.dumps(locator), text, note)).lastrowid
            c.executemany("INSERT OR IGNORE INTO clipping_tags(clipping_id,tag_id) VALUES(?,?)", [(cid, t) for t in tag_ids])
            if topic_id:
                self._attach(c, topic_id, cid)
        return cid

    def _attach(self, c: sqlite3.Connection, topic_id: int, clipping_id: int) -> None:
        pos = c.execute("SELECT COALESCE(MAX(pos),0)+1 FROM topic_clippings WHERE topic_id=?", (topic_id,)).fetchone()[0]
        c.execute("INSERT OR IGNORE INTO topic_clippings(topic_id,clipping_id,pos) VALUES(?,?,?)", (topic_id, clipping_id, pos))
        c.execute("UPDATE topics SET modified=datetime('now') WHERE id=?", (topic_id,))

    def update_clipping(self, cid: int, note: str | None = None) -> None:
        with self.db.tx() as c:
            if note is not None:
                c.execute("UPDATE clippings SET note=? WHERE id=?", (note, cid))

    def get_clipping(self, cid: int) -> dict[str, Any] | None:
        c = self.db.conn
        r = c.execute("SELECT cl.*, b.available AS book_available FROM clippings cl LEFT JOIN books b ON b.id=cl.book_id WHERE cl.id=?", (cid,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["locator"] = json.loads(d["locator"] or "{}")
        d["tags"] = [dict(x) for x in c.execute("SELECT t.id, t.name FROM clipping_tags ct JOIN tags t ON t.id=ct.tag_id WHERE ct.clipping_id=?", (cid,))]
        d["links"] = [x[0] for x in c.execute("SELECT b FROM clipping_links WHERE a=? UNION SELECT a FROM clipping_links WHERE b=?", (cid, cid))]
        d["topics"] = [dict(x) for x in c.execute("SELECT t.id, t.title FROM topic_clippings tc JOIN topics t ON t.id=tc.topic_id WHERE tc.clipping_id=?", (cid,))]
        return d

    def clippings(self, query: str = "", tag_id: int | None = None, topic_id: int | None = None, limit: int = 500) -> list[sqlite3.Row]:
        q = "SELECT cl.* FROM clippings cl WHERE 1"
        p: list[Any] = []
        if query:
            like = f"%{query}%"
            q += " AND (cl.text LIKE ? OR cl.note LIKE ? OR cl.book_title LIKE ?)"
            p += [like, like, like]
        if tag_id:
            q += " AND EXISTS (SELECT 1 FROM clipping_tags ct WHERE ct.clipping_id=cl.id AND ct.tag_id=?)"
            p.append(tag_id)
        if topic_id:
            q += " AND EXISTS (SELECT 1 FROM topic_clippings tc WHERE tc.clipping_id=cl.id AND tc.topic_id=?)"
            p.append(topic_id)
        return self.db.conn.execute(q + " ORDER BY cl.created DESC, cl.id DESC LIMIT ?", p + [limit]).fetchall()

    def delete_clipping(self, cid: int) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM clippings WHERE id=?", (cid,))

    def tag_clipping(self, cid: int, tag_id: int, on: bool = True) -> None:
        with self.db.tx() as c:
            if on:
                c.execute("INSERT OR IGNORE INTO clipping_tags(clipping_id,tag_id) VALUES(?,?)", (cid, tag_id))
            else:
                c.execute("DELETE FROM clipping_tags WHERE clipping_id=? AND tag_id=?", (cid, tag_id))

    def link_clippings(self, a: int, b: int) -> None:
        if a == b:
            raise ValueError("A clipping cannot be linked to itself.")
        a, b = sorted((a, b))
        with self.db.tx() as c:
            c.execute("INSERT OR IGNORE INTO clipping_links(a,b) VALUES(?,?)", (a, b))

    def unlink_clippings(self, a: int, b: int) -> None:
        a, b = sorted((a, b))
        with self.db.tx() as c:
            c.execute("DELETE FROM clipping_links WHERE a=? AND b=?", (a, b))

    def attach_clipping(self, topic_id: int, clipping_id: int) -> None:
        with self.db.tx() as c:
            self._attach(c, topic_id, clipping_id)

    def detach_clipping(self, topic_id: int, clipping_id: int) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM topic_clippings WHERE topic_id=? AND clipping_id=?", (topic_id, clipping_id))

    # ------------------------------------------------------------------ topics
    def create_topic(self, title: str, body: str = "") -> int:
        title = title.strip()
        if not title:
            raise ValueError("A topic needs a title.")
        with self.db.tx() as c:
            try:
                tid = c.execute("INSERT INTO topics(title,body) VALUES(?,?)", (title, body)).lastrowid
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"A topic named '{title}' already exists.") from exc
            self._relink(c, tid, body)
            # topics that already link to this title now resolve
            for r in c.execute("SELECT id, body FROM topics WHERE id<>?", (tid,)).fetchall():
                if title.lower() in r["body"].lower():
                    self._relink(c, r["id"], r["body"])
            return tid

    def save_topic(self, tid: int, title: str | None = None, body: str | None = None) -> None:
        with self.db.tx() as c:
            if title is not None:
                try:
                    c.execute("UPDATE topics SET title=? WHERE id=?", (title.strip(), tid))
                except sqlite3.IntegrityError as exc:
                    raise ValueError(f"A topic named '{title}' already exists.") from exc
            if body is not None:
                c.execute("UPDATE topics SET body=? WHERE id=?", (body, tid))
                self._relink(c, tid, body)
            c.execute("UPDATE topics SET modified=datetime('now') WHERE id=?", (tid,))

    def _relink(self, c: sqlite3.Connection, tid: int, body: str) -> None:
        c.execute("DELETE FROM topic_links WHERE from_topic=?", (tid,))
        for m in WIKILINK.finditer(body):
            name = m.group(1).strip()
            if name.lower().startswith("clip:"):
                continue
            r = c.execute("SELECT id FROM topics WHERE title=?", (name,)).fetchone()
            if r and r[0] != tid:
                c.execute("INSERT OR IGNORE INTO topic_links(from_topic,to_topic) VALUES(?,?)", (tid, r[0]))

    def delete_topic(self, tid: int) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM topics WHERE id=?", (tid,))

    def topics(self, query: str = "") -> list[sqlite3.Row]:
        q = "SELECT id, title, modified, created, (SELECT COUNT(*) FROM topic_clippings tc WHERE tc.topic_id=topics.id) AS n_clips FROM topics"
        p: list[Any] = []
        if query:
            like = f"%{query}%"
            q += (" WHERE title LIKE ? OR body LIKE ? OR id IN (SELECT tc.topic_id FROM topic_clippings tc JOIN clippings c ON c.id=tc.clipping_id "
                  "WHERE c.text LIKE ? OR c.note LIKE ?)")
            p = [like, like, like, like]
        return self.db.conn.execute(q + " ORDER BY modified DESC", p).fetchall()

    def get_topic(self, tid: int) -> dict[str, Any] | None:
        c = self.db.conn
        r = c.execute("SELECT * FROM topics WHERE id=?", (tid,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["tags"] = [dict(x) for x in c.execute("SELECT t.id, t.name FROM topic_tags tt JOIN tags t ON t.id=tt.tag_id WHERE tt.topic_id=?", (tid,))]
        d["books"] = [dict(x) for x in c.execute("SELECT b.id, b.title, b.author_sort AS authors, b.available FROM topic_books tb JOIN books b ON b.id=tb.book_id WHERE tb.topic_id=?", (tid,))]
        d["clippings"] = [self.get_clipping(x[0]) for x in c.execute("SELECT clipping_id FROM topic_clippings WHERE topic_id=? ORDER BY pos", (tid,))]
        d["links"] = [dict(x) for x in c.execute("SELECT t.id, t.title FROM topic_links l JOIN topics t ON t.id=l.to_topic WHERE l.from_topic=?", (tid,))]
        d["backlinks"] = self.backlinks(tid)
        return d

    def backlinks(self, tid: int) -> list[dict[str, Any]]:
        return [dict(x) for x in self.db.conn.execute(
            "SELECT t.id, t.title FROM topic_links l JOIN topics t ON t.id=l.from_topic WHERE l.to_topic=? ORDER BY t.title", (tid,))]

    def topic_by_title(self, title: str) -> int | None:
        r = self.db.conn.execute("SELECT id FROM topics WHERE title=?", (title.strip(),)).fetchone()
        return r[0] if r else None

    def tag_topic(self, tid: int, tag_id: int, on: bool = True) -> None:
        with self.db.tx() as c:
            if on:
                c.execute("INSERT OR IGNORE INTO topic_tags(topic_id,tag_id) VALUES(?,?)", (tid, tag_id))
            else:
                c.execute("DELETE FROM topic_tags WHERE topic_id=? AND tag_id=?", (tid, tag_id))

    def link_book(self, tid: int, book_id: int, on: bool = True) -> None:
        with self.db.tx() as c:
            if on:
                c.execute("INSERT OR IGNORE INTO topic_books(topic_id,book_id) VALUES(?,?)", (tid, book_id))
            else:
                c.execute("DELETE FROM topic_books WHERE topic_id=? AND book_id=?", (tid, book_id))

    def recent_topics(self, limit: int = 6) -> list[sqlite3.Row]:
        return self.db.conn.execute("SELECT id, title, modified FROM topics ORDER BY modified DESC LIMIT ?", (limit,)).fetchall()
