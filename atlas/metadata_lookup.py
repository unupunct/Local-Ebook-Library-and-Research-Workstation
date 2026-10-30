"""Optional online metadata lookup. OFF by default, never used by core functions.

Only runs when the user enables it in Settings and presses a lookup button; the only data sent
is the ISBN or title/author the user is looking up (never book content).
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from .parsers.base import clean_isbn, extract_year


@dataclass
class LookupResult:
    title: str = ""
    authors: list[str] = field(default_factory=list)
    publisher: str = ""
    year: int | None = None
    isbn: str = ""
    source: str = ""


class MetadataProvider(ABC):
    name = ""
    description = ""

    @abstractmethod
    def lookup(self, isbn: str = "", title: str = "", author: str = "") -> list[LookupResult]: ...


class OpenLibraryProvider(MetadataProvider):
    name = "Open Library (online)"
    description = "Queries openlibrary.org with an ISBN or title/author. Sends only that query text."

    def lookup(self, isbn: str = "", title: str = "", author: str = "") -> list[LookupResult]:
        if isbn:
            q = {"q": "isbn:" + clean_isbn(isbn)}
        elif title:
            q = {"title": title}
            if author:
                q["author"] = author
        else:
            return []
        q["limit"] = "5"
        q["fields"] = "title,author_name,publisher,first_publish_year,isbn"
        url = "https://openlibrary.org/search.json?" + urllib.parse.urlencode(q)
        req = urllib.request.Request(url, headers={"User-Agent": "AtlasLibrary/1.0 (local desktop app)"})
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - fixed https URL
            data = json.loads(resp.read(1_000_000))
        return parse_openlibrary(data)


def parse_openlibrary(data: dict) -> list[LookupResult]:
    out = []
    for d in data.get("docs", []):
        out.append(LookupResult(
            title=d.get("title", ""), authors=list(d.get("author_name", []))[:5],
            publisher=(d.get("publisher") or [""])[0], year=extract_year(str(d.get("first_publish_year", ""))),
            isbn=clean_isbn((d.get("isbn") or [""])[0]), source="Open Library"))
    return out


PROVIDERS: dict[str, MetadataProvider] = {OpenLibraryProvider.name: OpenLibraryProvider()}
