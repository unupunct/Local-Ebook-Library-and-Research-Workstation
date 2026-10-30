"""Reading progress, sessions, bookmarks / highlights / annotations (stored in the DB only)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from .db import Database


class Reading:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ---------------------------------------------------------------- progress
    def save_progress(self, book_id: int, locator: dict[str, Any], percent: float, zoom: float | None = None) -> None:
        percent = max(0.0, min(100.0, percent))
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO reading_progress(book_id,locator,percent,zoom,updated) VALUES(?,?,?,?,datetime('now')) "
                "ON CONFLICT(book_id) DO UPDATE SET locator=excluded.locator, percent=excluded.percent, zoom=excluded.zoom, updated=excluded.updated",
                (book_id, json.dumps(locator), percent, zoom))
            c.execute("UPDATE books SET last_read=datetime('now'), reading_status=CASE "
                      "WHEN ?>=98 THEN 'finished' WHEN reading_status='unread' THEN 'reading' ELSE reading_status END WHERE id=?",
                      (percent, book_id))

    def get_progress(self, book_id: int) -> dict[str, Any] | None:
        r = self.db.conn.execute("SELECT * FROM reading_progress WHERE book_id=?", (book_id,)).fetchone()
        if not r:
            return None
        return {"locator": json.loads(r["locator"] or "{}"), "percent": r["percent"], "zoom": r["zoom"], "updated": r["updated"]}

    def record_session(self, book_id: int, started: datetime, seconds: int) -> None:
        if seconds < 5:
            return
        with self.db.tx() as c:
            c.execute("INSERT INTO reading_sessions(book_id,started,seconds) VALUES(?,?,?)",
                      (book_id, started.strftime("%Y-%m-%d %H:%M:%S"), int(seconds)))

    def continue_reading(self, limit: int = 8) -> list[sqlite3.Row]:
        return self.db.conn.execute(
            "SELECT b.id, b.title, b.author_sort AS authors, b.cover_path, b.available, b.format, p.percent, p.updated "
            "FROM reading_progress p JOIN books b ON b.id=p.book_id WHERE p.percent<98 ORDER BY p.updated DESC LIMIT ?", (limit,)).fetchall()

    def history(self, limit: int = 200) -> list[sqlite3.Row]:
        return self.db.conn.execute(
            "SELECT b.id, b.title, b.author_sort AS authors, b.last_read, b.reading_status, b.available, COALESCE(p.percent,0) AS percent, "
            "(SELECT COALESCE(SUM(seconds),0) FROM reading_sessions s WHERE s.book_id=b.id) AS seconds "
            "FROM books b LEFT JOIN reading_progress p ON p.book_id=b.id WHERE b.last_read IS NOT NULL ORDER BY b.last_read DESC LIMIT ?",
            (limit,)).fetchall()

    def stats(self) -> dict[str, Any]:
        c = self.db.conn
        return {
            "seconds_total": c.execute("SELECT COALESCE(SUM(seconds),0) FROM reading_sessions").fetchone()[0],
            "seconds_30d": c.execute("SELECT COALESCE(SUM(seconds),0) FROM reading_sessions WHERE started>=datetime('now','-30 days')").fetchone()[0],
            "finished": c.execute("SELECT COUNT(*) FROM books WHERE reading_status='finished'").fetchone()[0],
            "in_progress": c.execute("SELECT COUNT(*) FROM books WHERE reading_status='reading'").fetchone()[0],
        }

    # -------------------------------------------------------------- annotations
    def add(self, book_id: int, type_: str, locator: dict[str, Any], selected_text: str = "", note: str = "",
            color: str = "#f2c94c", loc_label: str = "") -> int:
        if type_ not in ("bookmark", "highlight", "note"):
            raise ValueError("Unknown annotation type")
        fp = self.db.conn.execute("SELECT fingerprint FROM books WHERE id=?", (book_id,)).fetchone()
        if fp is None:
            raise KeyError(book_id)
        loc_num = locator.get("num", locator.get("page"))
        with self.db.tx() as c:
            return c.execute(
                "INSERT INTO annotations(book_id,type,selected_text,locator,loc_num,loc_label,note,color,book_fingerprint) VALUES(?,?,?,?,?,?,?,?,?)",
                (book_id, type_, selected_text, json.dumps(locator), loc_num, loc_label, note, color, fp[0])).lastrowid

    def update(self, ann_id: int, note: str | None = None, color: str | None = None) -> None:
        with self.db.tx() as c:
            if note is not None:
                c.execute("UPDATE annotations SET note=?, modified=datetime('now') WHERE id=?", (note, ann_id))
            if color is not None:
                c.execute("UPDATE annotations SET color=?, modified=datetime('now') WHERE id=?", (color, ann_id))

    def delete(self, ann_id: int) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM annotations WHERE id=?", (ann_id,))

    def for_book(self, book_id: int, type_: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM annotations WHERE book_id=?"
        p: list[Any] = [book_id]
        if type_:
            q += " AND type=?"; p.append(type_)
        out = []
        for r in self.db.conn.execute(q + " ORDER BY COALESCE(loc_num,0), id", p):
            d = dict(r)
            d["locator"] = json.loads(d["locator"] or "{}")
            out.append(d)
        return out

    def recent(self, limit: int = 10) -> list[sqlite3.Row]:
        return self.db.conn.execute(
            "SELECT a.id, a.book_id, a.type, a.selected_text, a.note, a.modified, b.title FROM annotations a "
            "JOIN books b ON b.id=a.book_id ORDER BY a.modified DESC, a.id DESC LIMIT ?", (limit,)).fetchall()

    def all_with_books(self, query: str = "") -> list[sqlite3.Row]:
        q = ("SELECT a.*, b.title, b.available FROM annotations a JOIN books b ON b.id=a.book_id")
        p: list[Any] = []
        if query:
            like = f"%{query}%"
            q += " WHERE a.selected_text LIKE ? OR a.note LIKE ? OR b.title LIKE ?"
            p = [like, like, like]
        return self.db.conn.execute(q + " ORDER BY a.modified DESC", p).fetchall()

    def mark_invalid_for_changed(self, book_id: int) -> int:
        """After a source file changed, flag annotations created against a different fingerprint.

        They are reported to the user rather than silently re-anchored.
        """
        fp = self.db.conn.execute("SELECT fingerprint FROM books WHERE id=?", (book_id,)).fetchone()
        if not fp:
            return 0
        with self.db.tx() as c:
            return c.execute("UPDATE annotations SET invalid=1 WHERE book_id=? AND book_fingerprint<>? AND type<>'bookmark'", (book_id, fp[0])).rowcount

    def mark_invalid(self, ann_ids) -> None:
        with self.db.tx() as c:
            c.executemany("UPDATE annotations SET invalid=1 WHERE id=?", [(i,) for i in ann_ids])

    def invalid_annotations(self) -> list[sqlite3.Row]:
        return self.db.conn.execute(
            "SELECT a.id, a.book_id, a.selected_text, b.title FROM annotations a JOIN books b ON b.id=a.book_id WHERE a.invalid=1").fetchall()
