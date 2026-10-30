"""User query syntax -> safe FTS5 MATCH expression.

Supported syntax (documented in docs/SEARCH_SYNTAX.md):
    word word          implicit AND
    "exact phrase"     phrase
    AND OR NOT         upper-case boolean operators, parentheses for grouping
    -word              same as NOT word
    prefix*            prefix match
Everything the user types is emitted as a quoted FTS5 string, so FTS5 syntax
characters in the input can never change the structure of the query.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


class QuerySyntaxError(ValueError):
    def __init__(self, message: str, position: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.position = position


@dataclass
class ParsedQuery:
    match: str
    terms: list[str] = field(default_factory=list)   # plain words for highlighting
    groups: list[tuple[list[str], bool]] = field(default_factory=list)  # (words, last_is_prefix) of non-negated terms
    warnings: list[str] = field(default_factory=list)


_TOKEN = re.compile(r'\s*(?:(?P<lp>\()|(?P<rp>\))|"(?P<phrase>[^"]*)"|(?P<word>[^\s()"]+))')
_HAS_WORD = re.compile(r"\w", re.UNICODE)


def _tokenize(q: str) -> list[tuple[str, str, int]]:
    out: list[tuple[str, str, int]] = []
    pos = 0
    q = q.strip()
    while pos < len(q):
        if q[pos:].strip() == "":
            break
        if q[pos:].lstrip().startswith('"') and q.count('"', pos) % 2 == 1:
            raise QuerySyntaxError("Unbalanced quotation mark. Close the phrase with a second \".", q.index('"', pos))
        m = _TOKEN.match(q, pos)
        if not m:
            raise QuerySyntaxError("Could not understand the query.", pos)
        start = m.start() + (len(m.group(0)) - len(m.group(0).lstrip()))
        if m.group("lp"):
            out.append(("lp", "(", start))
        elif m.group("rp"):
            out.append(("rp", ")", start))
        elif m.group("phrase") is not None:
            out.append(("phrase", m.group("phrase"), start))
        else:
            w = m.group("word")
            if w in ("AND", "OR", "NOT"):
                out.append(("op", w, start))
            else:
                out.append(("word", w, start))
        pos = m.end()
    return out


class _Parser:
    def __init__(self, tokens: list[tuple[str, str, int]]) -> None:
        self.t = tokens
        self.i = 0
        self.terms: list[str] = []
        self.groups: list[tuple[list[str], bool]] = []
        self.warnings: list[str] = []

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else None

    def parse(self):
        node = self.or_expr()
        if self.peek() is not None:
            kind, val, pos = self.peek()
            if kind == "rp":
                raise QuerySyntaxError("Unbalanced parenthesis: ')' has no matching '('.", pos)
            raise QuerySyntaxError(f"Unexpected '{val}'.", pos)
        return node

    def or_expr(self):
        items = [self.and_expr()]
        while self.peek() and self.peek()[:2] == ("op", "OR"):
            pos = self.peek()[2]
            self.i += 1
            if self.peek() is None or self.peek()[0] == "rp":
                raise QuerySyntaxError("OR needs a term after it.", pos)
            items.append(self.and_expr())
        return items[0] if len(items) == 1 else ("or", items)

    def and_expr(self):
        items = [self.unary()]
        while True:
            p = self.peek()
            if p is None or p[0] == "rp" or p[:2] == ("op", "OR"):
                break
            if p[:2] == ("op", "AND"):
                self.i += 1
                if self.peek() is None or self.peek()[0] == "rp":
                    raise QuerySyntaxError("AND needs a term after it.", p[2])
            items.append(self.unary())
        return items[0] if len(items) == 1 else ("and", items)

    def unary(self):
        p = self.peek()
        if p is None:
            raise QuerySyntaxError("The query ends unexpectedly.")
        if p[:2] == ("op", "NOT"):
            self.i += 1
            if self.peek() is None or self.peek()[0] == "rp":
                raise QuerySyntaxError("NOT needs a term after it.", p[2])
            n_terms, n_groups = len(self.terms), len(self.groups)
            inner = self.unary()
            del self.terms[n_terms:], self.groups[n_groups:]   # negated words are not highlighted
            return ("not", inner)
        if p[0] == "word" and p[1].startswith("-") and len(p[1]) > 1:
            self.i += 1
            n_terms, n_groups = len(self.terms), len(self.groups)
            inner = self.word_node(p[1][1:], p[2])
            del self.terms[n_terms:], self.groups[n_groups:]
            return ("not", inner)
        return self.primary()

    def primary(self):
        kind, val, pos = self.peek()
        self.i += 1
        if kind == "lp":
            if self.peek() is None or self.peek()[0] == "rp":
                raise QuerySyntaxError("Empty parentheses.", pos)
            node = self.or_expr()
            if self.peek() is None or self.peek()[0] != "rp":
                raise QuerySyntaxError("Unbalanced parenthesis: '(' is never closed.", pos)
            self.i += 1
            return node
        if kind == "phrase":
            if not _HAS_WORD.search(val):
                raise QuerySyntaxError("The quoted phrase contains no searchable words.", pos)
            words = re.findall(r"\w+", val, re.UNICODE)
            self.terms += words
            self.groups.append((words, False))
            return ("phrase", val)
        if kind == "word":
            return self.word_node(val, pos)
        if kind == "op":
            raise QuerySyntaxError(f"'{val}' needs a search term before it.", pos)
        raise QuerySyntaxError("Unbalanced parenthesis: ')' has no matching '('.", pos)

    def word_node(self, word: str, pos: int):
        prefix = word.endswith("*")
        core = word.rstrip("*")
        if not _HAS_WORD.search(core):
            if prefix and not core:
                raise QuerySyntaxError("A prefix search needs at least one letter before '*'.", pos)
            self.warnings.append(f"'{word}' has no searchable characters and was ignored.")
            return ("empty",)
        words = re.findall(r"\w+", core, re.UNICODE)
        self.terms += words
        self.groups.append((words, prefix))
        return ("term", core, prefix)


def _quote(s: str) -> str:
    return '"' + s.replace('"', '""') + '"'


def _emit(node, top: bool = False) -> str:
    kind = node[0]
    if kind == "term":
        return _quote(node[1]) + ("*" if node[2] else "")
    if kind == "phrase":
        return _quote(node[1])
    if kind == "or":
        parts = [_emit(n) for n in node[1] if n[0] != "empty"]
        for n in node[1]:
            if n[0] == "not":
                raise QuerySyntaxError("NOT can only be combined with AND, e.g. 'soil NOT clay'.")
        if not parts:
            return ""
        return parts[0] if len(parts) == 1 else "(" + " OR ".join(parts) + ")"
    if kind == "and":
        pos = [n for n in node[1] if n[0] not in ("not", "empty")]
        neg = [n for n in node[1] if n[0] == "not"]
        pos_s = [s for s in (_emit(n) for n in pos) if s]
        if not pos_s:
            if neg:
                raise QuerySyntaxError("A query cannot consist only of NOT terms; add a word to search for.")
            return ""
        out = pos_s[0] if len(pos_s) == 1 else "(" + " AND ".join(pos_s) + ")"
        if neg:
            neg_s = [_emit(n[1]) for n in neg]
            out = f"{out} NOT " + (neg_s[0] if len(neg_s) == 1 else "(" + " OR ".join(neg_s) + ")")
        return out
    if kind == "not":
        raise QuerySyntaxError("A query cannot consist only of NOT terms; add a word to search for.")
    return ""


def build_match(query: str) -> ParsedQuery:
    """Parse ``query`` into an FTS5 MATCH string; raises QuerySyntaxError with a user-readable message."""
    if not query or not query.strip():
        raise QuerySyntaxError("Type something to search for.")
    if len(query) > 1000:
        raise QuerySyntaxError("The query is too long (limit 1000 characters).")
    p = _Parser(_tokenize(query))
    node = p.parse()
    match = _emit(node, top=True)
    if not match:
        raise QuerySyntaxError("The query contains nothing that can be searched for.")
    seen: set[str] = set()
    terms = [t for t in p.terms if not (t.lower() in seen or seen.add(t.lower()))]
    return ParsedQuery(match, terms, p.groups, p.warnings)
