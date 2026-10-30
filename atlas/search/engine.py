"""Search execution over the FTS5 index with library filters."""
from __future__ import annotations

import html
import json
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any

from ..db import Database
from ..library import BookFilter
from .excerpt import compile_groups, make_excerpt
from .query import ParsedQuery, QuerySyntaxError, build_match

HL_START, HL_END = "\x02", "\x03"
SORTS = ("relevance", "title", "author", "year", "added")


@dataclass
class Passage:
    segment_id: int
    book_id: int
    loc_type: str
    loc_num: int
    loc_label: str
    char_start: int
    excerpt: str            # contains HL_START/HL_END markers
    rank: float = 0.0

    @property
    def excerpt_plain(self) -> str:
        return self.excerpt.replace(HL_START, "").replace(HL_END, "")

    @property
    def excerpt_html(self) -> str:
        parts = html.escape(self.excerpt, quote=False)
        return parts.replace(HL_START, "<b>").replace(HL_END, "</b>")

    @property
    def location_text(self) -> str:
        if self.loc_type == "page":
            return f"Page {self.loc_num}"
        if self.loc_type == "chapter":
            return self.loc_label or f"Chapter {self.loc_num + 1}"
        return self.loc_label or ""

    def locator(self, terms: list[str]) -> dict[str, Any]:
        return {"type": self.loc_type, "num": self.loc_num, "label": self.loc_label,
                "char_start": self.char_start, "terms": terms, "excerpt": self.excerpt_plain}


@dataclass
class BookResult:
    book_id: int
    title: str
    authors: str
    fmt: str
    year: int | None
    available: bool
    match_count: int
    tags: list[str] = field(default_factory=list)
    passages: list[Passage] = field(default_factory=list)


@dataclass
class SearchResponse:
    query: str
    parsed: ParsedQuery
    books: list[BookResult]
    passages: list[Passage]          # flat list in display order
    total_passages: int
    total_books: int
    elapsed_ms: float
    truncated: bool


class SearchEngine:
    def __init__(self, db: Database) -> None:
        self.db = db

    def search(self, query: str, flt: BookFilter | None = None, sort: str = "relevance",
               limit: int = 200, passages_per_book: int = 5, grouped: bool = True,
               record_history: bool = True, history_limit: int = 100) -> SearchResponse:
        t0 = time.perf_counter()
        parsed = build_match(query)           # raises QuerySyntaxError
        flt = flt or BookFilter()
        where, params = flt.sql(self.db)
        c = self.db.conn
        # seg_meta is a narrow table (no text), so the join stays cheap even for 100k+ hits
        frm = (" FROM hits JOIN seg_meta m ON m.id=hits.sid"
               + (" JOIN books b ON b.id=m.book_id" if where != "1" else "") + f" WHERE {where}")
        cte = "WITH hits AS (SELECT rowid AS sid, rank FROM segments_fts WHERE segments_fts MATCH ?) "
        p0 = [parsed.match] + params
        cols = "m.id, m.book_id, m.loc_type, m.loc_num, m.loc_label, m.char_start, hits.rank AS rank"
        truncated = False

        if grouped:
            rows = c.execute(
                cte + f"SELECT * FROM (SELECT {cols}, ROW_NUMBER() OVER (PARTITION BY m.book_id ORDER BY hits.rank) AS rn, "
                f"COUNT(*) OVER (PARTITION BY m.book_id) AS n{frm}) WHERE rn<=?", p0 + [max(1, passages_per_book)]).fetchall()
            counts = {r["book_id"]: r["n"] for r in rows if r["rn"] == 1}
            best = {r["book_id"]: r["rank"] for r in rows if r["rn"] == 1}
            total_passages, total_books = sum(counts.values()), len(counts)
            info = self._book_info(list(counts))
            books_sorted = sorted((i for i in counts if i in info), key=self._order_key(sort, info, best),
                                  reverse=(sort == "added"))
            if len(books_sorted) > limit:
                truncated, books_sorted = True, books_sorted[:limit]
            keep = set(books_sorted)
            per: dict[int, list[sqlite3.Row]] = {}
            for r in sorted(rows, key=lambda r: r["rank"]):
                if r["book_id"] in keep:
                    per.setdefault(r["book_id"], []).append(r)
            chosen = [r for bid in books_sorted for r in per.get(bid, [])]
        else:
            fetch = limit if sort == "relevance" else limit * 4
            rows = c.execute(cte + f"SELECT {cols}, COUNT(*) OVER () AS total{frm} ORDER BY hits.rank LIMIT ?", p0 + [fetch]).fetchall()
            total_passages = rows[0]["total"] if rows else 0
            total_books = c.execute(cte + f"SELECT COUNT(DISTINCT m.book_id){frm}", p0).fetchone()[0] if rows else 0
            info = self._book_info(list({r["book_id"] for r in rows}))
            best = {}
            for r in rows:
                best.setdefault(r["book_id"], r["rank"])
            if sort != "relevance":
                order = sorted(best, key=self._order_key(sort, info, best), reverse=(sort == "added"))
                pos = {b: i for i, b in enumerate(order)}
                rows = sorted(rows, key=lambda r: (pos.get(r["book_id"], 1 << 30), r["rank"]))
            chosen = rows[:limit]
            truncated = total_passages > len(chosen)
            seen: list[int] = []
            for r in chosen:
                if r["book_id"] not in seen:
                    seen.append(r["book_id"])
            books_sorted = [b for b in seen if b in info]
            counts = {b: sum(1 for r in chosen if r["book_id"] == b) for b in books_sorted}

        excerpts = self._excerpts(compile_groups(parsed.groups), [r["id"] for r in chosen])
        passages = [Passage(r["id"], r["book_id"], r["loc_type"], r["loc_num"], r["loc_label"], r["char_start"],
                            excerpts.get(r["id"], ""), r["rank"]) for r in chosen]
        by_book: dict[int, list[Passage]] = {}
        for p in passages:
            by_book.setdefault(p.book_id, []).append(p)
        tags = self._tags(books_sorted)
        books = [BookResult(b, info[b]["title"], info[b]["author_sort"], info[b]["format"], info[b]["year"],
                            bool(info[b]["available"]), counts.get(b, 0), tags.get(b, []), by_book.get(b, [])) for b in books_sorted]
        elapsed = (time.perf_counter() - t0) * 1000
        if record_history:
            self._history(query, flt, total_passages, history_limit)
        return SearchResponse(query, parsed, books, passages, total_passages, total_books, elapsed, truncated)

    @staticmethod
    def _order_key(sort: str, info: dict, best: dict):
        return {
            "title": lambda b: (info[b]["title"] or "").lower(),
            "author": lambda b: (info[b]["author_sort"] or "~").lower(),
            "year": lambda b: -(info[b]["year"] or 0),
            "added": lambda b: info[b]["import_date"] or "",
        }.get(sort, lambda b: best.get(b, 0))

    def _book_info(self, ids: list[int]) -> dict[int, sqlite3.Row]:
        out: dict[int, sqlite3.Row] = {}
        for chunk in _chunks(ids, 400):
            for r in self.db.conn.execute(
                    f"SELECT id, title, author_sort, format, year, available, import_date FROM books WHERE id IN ({','.join('?' * len(chunk))})", chunk):
                out[r["id"]] = r
        return out

    def _excerpts(self, pattern, seg_ids: list[int]) -> dict[int, str]:
        out: dict[int, str] = {}
        for chunk in _chunks(seg_ids, 400):
            for r in self.db.conn.execute(f"SELECT id, text FROM segments WHERE id IN ({','.join('?' * len(chunk))})", chunk):
                out[r["id"]] = make_excerpt(r["text"], pattern)
        return out

    def _tags(self, book_ids: list[int]) -> dict[int, list[str]]:
        out: dict[int, list[str]] = {}
        for chunk in _chunks(book_ids, 400):
            for r in self.db.conn.execute(
                    f"SELECT bt.book_id, t.name FROM book_tags bt JOIN tags t ON t.id=bt.tag_id WHERE bt.book_id IN ({','.join('?' * len(chunk))}) ORDER BY t.name", chunk):
                out.setdefault(r[0], []).append(r[1])
        return out

    # ------------------------------------------------------ semantic results
    def related_passages(self, hits: list[dict]) -> list[BookResult]:
        """Turn semantic hits ([{segment_id, book_id, score}]) into BookResults, best book first.
        Excerpts are plain text starts of the passage (there are no keywords to highlight)."""
        if not hits:
            return []
        score = {h["segment_id"]: h["score"] for h in hits}
        rows: list[sqlite3.Row] = []
        for chunk in _chunks(list(score), 400):
            rows += self.db.conn.execute(
                f"SELECT m.id, m.book_id, m.loc_type, m.loc_num, m.loc_label, m.char_start, s.text FROM seg_meta m "
                f"JOIN segments s ON s.id=m.id WHERE m.id IN ({','.join('?' * len(chunk))})", chunk).fetchall()
        info = self._book_info(list({r["book_id"] for r in rows}))
        by_book: dict[int, list[Passage]] = {}
        for r in sorted(rows, key=lambda r: -score[r["id"]]):
            if r["book_id"] not in info:
                continue
            text = " ".join(r["text"].split())
            by_book.setdefault(r["book_id"], []).append(
                Passage(r["id"], r["book_id"], r["loc_type"], r["loc_num"], r["loc_label"], r["char_start"],
                        text[:300] + (" …" if len(text) > 300 else ""), -score[r["id"]]))
        tags = self._tags(list(by_book))
        books = []
        for bid, ps in by_book.items():
            i = info[bid]
            books.append(BookResult(bid, i["title"], i["author_sort"], i["format"], i["year"], bool(i["available"]), len(ps), tags.get(bid, []), ps))
        books.sort(key=lambda b: b.passages[0].rank)
        return books

    # ---------------------------------------------------------- within a book
    def find_in_book(self, book_id: int, query: str) -> SearchResponse:
        return self.search(query, BookFilter(book_ids=[book_id]), grouped=True, limit=1,
                           passages_per_book=500, record_history=False)

    # ------------------------------------------------------ history / saved
    def _history(self, query: str, flt: BookFilter, n: int, keep: int) -> None:
        with self.db.tx() as c:
            c.execute("INSERT INTO search_history(query,filters,result_count) VALUES(?,?,?)", (query, json.dumps(flt.to_dict()), n))
            c.execute("DELETE FROM search_history WHERE id NOT IN (SELECT id FROM search_history ORDER BY id DESC LIMIT ?)", (keep,))

    def history(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.db.conn.execute("SELECT * FROM search_history ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def clear_history(self) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM search_history")

    def save_search(self, name: str, query: str, flt: BookFilter | None = None) -> int:
        name = name.strip()
        if not name:
            raise ValueError("A saved search needs a name.")
        with self.db.tx() as c:
            c.execute("INSERT INTO saved_searches(name,query,filters) VALUES(?,?,?) "
                      "ON CONFLICT(name) DO UPDATE SET query=excluded.query, filters=excluded.filters",
                      (name, query, json.dumps((flt or BookFilter()).to_dict())))
            return c.execute("SELECT id FROM saved_searches WHERE name=?", (name,)).fetchone()[0]

    def saved_searches(self) -> list[sqlite3.Row]:
        return self.db.conn.execute("SELECT * FROM saved_searches ORDER BY name COLLATE NOCASE").fetchall()

    def delete_saved(self, sid: int) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM saved_searches WHERE id=?", (sid,))


def _chunks(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]
