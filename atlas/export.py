"""Export clippings, topics and search results to Markdown, HTML and CSV.

Verbatim excerpts are always rendered as quotations; personal commentary is
labelled separately so the two can never be confused.
"""
from __future__ import annotations

import csv
import html
import io
from datetime import date
from typing import Any, Sequence

import markdown as md_lib

from .db import Database
from .research import Research


def source_line(c: dict[str, Any]) -> str:
    parts = []
    if c.get("book_authors"):
        parts.append(c["book_authors"])
    parts.append(f"*{c['book_title']}*" if c.get("book_title") else "Unknown source")
    loc = location_text(c)
    if loc:
        parts.append(loc)
    return ", ".join(parts)


def location_text(c: dict[str, Any]) -> str:
    if c.get("loc_type") == "page" and c.get("loc_num") is not None:
        return f"p. {c['loc_num']}"
    if c.get("loc_type") == "chapter":
        return c.get("loc_label") or f"chapter {int(c.get('loc_num') or 0) + 1}"
    return c.get("loc_label") or ""


def _quote(text: str) -> str:
    return "\n".join("> " + ln if ln.strip() else ">" for ln in text.splitlines())


def clipping_markdown(c: dict[str, Any], retrieved: str) -> str:
    out = [_quote(c["text"]), "", f"— {source_line(c)}"]
    if c.get("source_path"):
        out.append(f"  File: `{c['source_path']}`")
    out.append(f"  Clipped: {c['created']}, retrieved {retrieved}")
    if c.get("note"):
        out += ["", "**My note:** " + c["note"]]
    return "\n".join(out)


def export_clippings_md(db: Database, clipping_ids: Sequence[int], title: str = "Research export") -> str:
    r = Research(db)
    today = date.today().isoformat()
    parts = [f"# {title}", "", f"Exported {today}. Block quotes are verbatim text from the sources; "
             "text marked **My note** is personal commentary.", ""]
    for cid in clipping_ids:
        c = r.get_clipping(cid)
        if c:
            parts += [clipping_markdown(c, today), "", "---", ""]
    return "\n".join(parts)


def export_topic_md(db: Database, topic_id: int) -> str:
    r = Research(db)
    t = r.get_topic(topic_id)
    if t is None:
        raise KeyError(topic_id)
    today = date.today().isoformat()
    parts = [f"# {t['title']}", "", f"Created {t['created']} · modified {t['modified']} · exported {today}", ""]
    if t["tags"]:
        parts += ["Tags: " + ", ".join(x["name"] for x in t["tags"]), ""]
    parts += ["## Notes", "", t["body"] or "_(no notes)_", ""]
    if t["clippings"]:
        parts += ["## Saved passages", "", "Block quotes are verbatim; **My note** is personal commentary.", ""]
        for c in t["clippings"]:
            parts += [clipping_markdown(c, today), "", "---", ""]
    if t["books"]:
        parts += ["## Linked books", ""] + [f"- {b['title']}" + (f" — {b['authors']}" if b["authors"] else "") for b in t["books"]] + [""]
    if t["links"]:
        parts += ["## Related topics", ""] + [f"- {x['title']}" for x in t["links"]] + [""]
    if t["backlinks"]:
        parts += ["## Referenced by", ""] + [f"- {x['title']}" for x in t["backlinks"]] + [""]
    return "\n".join(parts)


def markdown_to_html(text: str, title: str = "Atlas Library export") -> str:
    body = md_lib.markdown(text, extensions=["extra", "sane_lists"])
    return ("<!doctype html><html><head><meta charset='utf-8'><title>" + html.escape(title) + "</title>"
            "<style>body{font-family:Georgia,serif;max-width:46em;margin:2em auto;line-height:1.5;padding:0 1em}"
            "blockquote{border-left:4px solid #999;margin:1em 0;padding:.2em 1em;background:#f5f5f5}</style></head><body>"
            + body + "</body></html>")


def export_clippings_csv(db: Database, clipping_ids: Sequence[int]) -> str:
    r = Research(db)
    buf = io.StringIO()
    w = csv.writer(buf)
    today = date.today().isoformat()
    w.writerow(["id", "book_title", "authors", "location", "source_path", "verbatim_text", "personal_note", "tags", "created", "retrieved"])
    for cid in clipping_ids:
        c = r.get_clipping(cid)
        if c:
            w.writerow([c["id"], c["book_title"], c["book_authors"], location_text(c), c["source_path"], c["text"], c["note"],
                        "; ".join(t["name"] for t in c["tags"]), c["created"], today])
    return buf.getvalue()


def export_search_csv(response) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    today = date.today().isoformat()
    w.writerow(["book", "authors", "location", "excerpt", "query", "retrieved"])
    titles = {b.book_id: b for b in response.books}
    for p in response.passages:
        b = titles.get(p.book_id)
        w.writerow([b.title if b else "", b.authors if b else "", p.location_text, p.excerpt_plain, response.query, today])
    return buf.getvalue()


def export_search_md(response) -> str:
    today = date.today().isoformat()
    out = [f"# Search results: {response.query}", "", f"Retrieved {today}. Excerpts are verbatim from the indexed text.", ""]
    for b in response.books:
        out += [f"## {b.title}" + (f" — {b.authors}" if b.authors else ""), ""]
        for p in b.passages:
            out += [_quote(p.excerpt_plain), f"— {p.location_text}" if p.location_text else "", ""]
    return "\n".join(out)
