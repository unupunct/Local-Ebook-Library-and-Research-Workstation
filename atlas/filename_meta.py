"""Suggest metadata from filenames such as "Author - Title (Year).pdf".

Suggestions are only proposals shown in a preview; the user confirms, and only empty fields are filled.
Nothing is guessed when a name does not match the chosen pattern.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

YEAR = r"(?P<year>1[5-9]\d\d|20\d\d)"

# label -> regex (applied to the file name without extension, underscores treated as spaces)
PATTERNS: dict[str, str] = {
    "Author - Title (Year)": rf"^(?P<author>[^-]+?)\s+-\s+(?P<title>.+?)\s*[\(\[]{YEAR}[\)\]]$",
    "Author - Title": r"^(?P<author>[^-]+?)\s+-\s+(?P<title>.+)$",
    "Title - Author": r"^(?P<title>.+?)\s+-\s+(?P<author>[^-]+)$",
    "Title (Year)": rf"^(?P<title>.+?)\s*[\(\[]{YEAR}[\)\]]$",
    "Title (Author)": r"^(?P<title>.+?)\s*\((?P<author>[^()\d][^()]*)\)$",
}


@dataclass
class Suggestion:
    title: str = ""
    author: str = ""
    year: int | None = None


def suggest(filename: str, pattern: str) -> Suggestion | None:
    rx = PATTERNS.get(pattern)
    if rx is None:
        raise KeyError(pattern)
    stem = re.sub(r"\s+", " ", os.path.splitext(filename)[0].replace("_", " ")).strip()
    m = re.match(rx, stem)
    if not m:
        return None
    g = m.groupdict()
    s = Suggestion(title=(g.get("title") or "").strip(" .-"), author=(g.get("author") or "").strip(" .-"),
                   year=int(g["year"]) if g.get("year") else None)
    if len(s.author) > 60 or (g.get("author") and not re.search(r"[^\W\d_]", s.author)):
        return None            # looks like a sentence or number, not a name
    return s


def apply_suggestion(lib, book_id: int, pattern: str) -> list[str]:
    """Fill EMPTY metadata from the file name; the title is replaced only if it still equals the file name stem.
    Returns the names of the fields that were changed."""
    b = lib.get_book(book_id)
    if b is None:
        return []
    s = suggest(b["original_filename"], pattern)
    if s is None:
        return []
    stem = os.path.splitext(b["original_filename"])[0]
    changed: list[str] = []
    fields: dict = {}
    if s.title and b["title"] in (stem, re.sub(r"_+", " ", stem).strip()):
        fields["title"] = s.title
        changed.append("title")
    kwargs: dict = {}
    if s.author and not b["authors"]:
        kwargs["authors"] = [s.author]
        changed.append("authors")
    if s.year and not b["year"]:
        fields["year"] = s.year
        changed.append("year")
    if changed:
        lib.update_book(book_id, **kwargs, **fields)
    return changed
