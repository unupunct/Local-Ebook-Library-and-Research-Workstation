"""Excerpt + highlight generation done in Python (much cheaper than FTS5 snippet() for prefix queries).

Matching is case- and diacritic-insensitive and length-preserving, so match positions in the folded
text are valid positions in the original text.
"""
from __future__ import annotations

import re
import unicodedata

HL_START, HL_END = "\x02", "\x03"


class _Fold(dict):
    def __missing__(self, k: int) -> int:
        ch = chr(k)
        base = unicodedata.normalize("NFD", ch)[:1] or ch
        low = base.lower()[:1] or base
        self[k] = ord(low)
        return self[k]


_FOLD = _Fold()


def fold(text: str) -> str:
    """Lower-case and strip diacritics without changing the string length."""
    if text.isascii():
        return text.lower()
    return text.translate(_FOLD)


_PATTERN_CACHE: dict[tuple, re.Pattern] = {}


def compile_groups(groups: list[tuple[list[str], bool]]) -> re.Pattern | None:
    key = tuple((tuple(w), p) for w, p in groups)
    if key in _PATTERN_CACHE:
        return _PATTERN_CACHE[key]
    alts = []
    for words, prefix in groups:
        fw = [re.escape(fold(w)) for w in words if w]
        if not fw:
            continue
        body = r"[\W_]+".join(fw) + (r"\w*" if prefix else r"(?!\w)")
        alts.append(r"(?<!\w)" + body)
    pat = re.compile("|".join(alts)) if alts else None
    if len(_PATTERN_CACHE) > 200:
        _PATTERN_CACHE.clear()
    _PATTERN_CACHE[key] = pat  # type: ignore[assignment]
    return pat


def make_excerpt(text: str, pattern: re.Pattern | None, before: int = 90, after: int = 150) -> str:
    """A window of ``text`` around the first match, with every match in the window wrapped in HL markers."""
    if pattern is None:
        return " ".join(text[:before + after].split())
    folded = fold(text)
    m = pattern.search(folded)
    if m is None:
        return " ".join(text[:before + after].split())
    first = m.start()
    start = max(0, first - before)
    end = min(len(text), first + after)
    if start > 0:
        sp = text.find(" ", start, first)
        start = sp + 1 if sp != -1 else start
    if end < len(text):
        sp = text.rfind(" ", first, end)
        end = sp if sp != -1 and sp > first else end
    out, pos = [], start
    for mm in pattern.finditer(folded, start, end):
        s_, e_ = mm.start(), mm.end()
        if e_ <= s_ or s_ < pos:
            continue
        out.append(text[pos:s_])
        out.append(HL_START + text[s_:e_] + HL_END)
        pos = e_
    out.append(text[pos:end])
    body = " ".join("".join(out).split())
    return ("… " if start > 0 else "") + body + (" …" if end < len(text) else "")
