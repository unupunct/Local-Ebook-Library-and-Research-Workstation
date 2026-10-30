"""Reduce book CSS to a safe, QTextDocument-friendly subset.

Books keep their paragraph styling (alignment, indents, margins, emphasis, borders) but cannot set colours,
backgrounds, absolute font sizes, fonts, positioning, or load anything (url(), @import, @font-face are dropped),
so the reader's theme, font and size preferences stay in control.
"""
from __future__ import annotations

import re

PROPS = {
    "text-align", "text-indent", "font-style", "font-weight", "font-variant", "text-decoration", "vertical-align",
    "margin", "margin-top", "margin-bottom", "margin-left", "margin-right",
    "padding", "padding-top", "padding-bottom", "padding-left", "padding-right",
    "border", "border-top", "border-bottom", "border-left", "border-right", "border-style", "border-width", "border-collapse",
    "list-style-type", "page-break-before", "page-break-after", "white-space",
}
_SIZE_OK = re.compile(r"^(-?\d+(\.\d+)?)(em|%|ex)$|^(xx-small|x-small|small|medium|large|x-large|xx-large|smaller|larger)$")
_LEN = re.compile(r"^-?\d+(\.\d+)?(px|pt|em|ex|%)?$|^auto$|^0$")
_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_AT_BLOCK = re.compile(r"@[a-zA-Z-]+[^{;]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", re.S)
_AT_LINE = re.compile(r"@[a-zA-Z-]+[^;{]*;")
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_SELECTOR_OK = re.compile(r"^[A-Za-z0-9_.#\- ]+$")
MAX_CSS = 400_000


def filter_declarations(block: str) -> str:
    out = []
    for decl in block.split(";"):
        name, sep, value = decl.partition(":")
        name, value = name.strip().lower(), value.strip()
        if not sep or not value or "url(" in value.lower() or "expression" in value.lower() or "\\" in value:
            continue
        value = re.sub(r"\s*!important\s*$", "", value, flags=re.I)
        if name == "font-size":
            if _SIZE_OK.match(value.lower()):
                out.append(f"font-size:{value}")
            continue
        if name not in PROPS:
            continue
        if name.startswith(("margin", "padding")) or name == "text-indent":
            if not all(_LEN.match(v) for v in value.split()):
                continue
        out.append(f"{name}:{value}")
    return ";".join(out)


def filter_css(css: str) -> str:
    css = _COMMENT.sub("", css[:MAX_CSS])
    css = _AT_BLOCK.sub("", css)
    css = _AT_LINE.sub("", css)
    rules = []
    for sel, body in _RULE.findall(css):
        decls = filter_declarations(body)
        if not decls:
            continue
        sels = [s.strip() for s in sel.split(",")]
        good = [s for s in sels if s and _SELECTOR_OK.match(s) and not s.startswith(("html", "body"))]
        if good:
            rules.append(f"{', '.join(good)} {{{decls}}}")
    return "\n".join(rules)
