"""Shared Markdown tokens and byte-preserving bounded translation requests."""

from __future__ import annotations

import re
from itertools import pairwise

FENCE = re.compile(r"^(`{3,}|~{3,})")
# A literal comparison is prose; quoted delimiters and comments belong to their tag.
HTML_TAG = re.compile(
    r"<!--.*?-->|<\?.*?\?>|<!\[CDATA\[.*?\]\]>"
    r"|<(?:/?[A-Za-z][^\s/<>\"']*(?=[\s/>])|![A-Za-z])"
    r"(?:[^<>\"']|\"[^\"]*\"|'[^']*')*>",
    re.DOTALL,
)

CODE_SPAN = re.compile(r"(`+)(?!`)(.+?)(?<!`)\1(?!`)", re.DOTALL)

ANCHOR = re.compile(r'^<a id="([^"]+)"></a>$')


def outside_fences(text: str) -> list[tuple[str, bool]]:
    """The page's lines, each with whether it is inside a fenced code block."""
    lines, fence = [], None
    for line in text.split("\n"):
        mark = FENCE.match(line.lstrip())
        if fence is None and mark:
            fence = mark.group(1)
            lines.append((line, True))
        elif fence is not None:
            lines.append((line, True))
            if line.strip().startswith(fence) and line.strip().strip(fence[0]) == "":
                fence = None
        else:
            lines.append((line, False))
    return lines


MAX_BYTES = 16_384
_LIST = re.compile(r"^( {0,3})(?:[-+*]|\d+[.)])\s")
_SEPARATOR = re.compile(r"^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*$")
_TAG_NAME = re.compile(r"</?([A-Za-z][^\s/<>\"']*)")
_BLOCK = frozenset(
    "address article aside blockquote details dialog div dl fieldset figure footer form "
    "h1 h2 h3 h4 h5 h6 header hgroup hr iframe main menu nav ol p pre script section style "
    "summary table tbody td tfoot th thead tr ul".split()
)
_VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())


def _fences(text: str) -> list[tuple[int, int]]:
    spans = []
    fence, start, offset = None, 0, 0
    for line in text.splitlines(keepends=True):
        mark = FENCE.match(line.lstrip())
        if fence is None and mark:
            fence, start = mark[1], offset
        elif fence and line.strip().startswith(fence) and not line.strip().strip(fence[0]):
            spans.append((start, offset + len(line)))
            fence = None
        offset += len(line)
    if fence is not None:
        raise ValueError("cannot split an unclosed fenced code block")
    return spans


def _html(text: str) -> list[tuple[int, int]]:
    spans, stack = [], []
    matches = list(HTML_TAG.finditer(text))
    closing = {_TAG_NAME.match(m[0])[1].lower() for m in matches if m[0].startswith("</")}
    for match in matches:
        raw = match[0]
        if raw.startswith(("<!--", "<?", "<!")):
            spans.append(match.span())
            continue
        name = _TAG_NAME.match(raw)[1].lower()
        if raw.startswith("</"):
            if not stack or stack[-1][0] != name:
                raise ValueError(f"cannot split misnested HTML: {name}")
            _, start = stack.pop()
            spans.append((start, match.end()))
        elif name not in _VOID and not raw.endswith("/>") and name in closing:
            stack.append((name, match.start()))
        elif (
            name in _BLOCK
            and name not in _VOID
            and not raw.endswith("/>")
            and not text[text.rfind("\n", 0, match.start()) + 1 : match.start()].strip()
        ):
            raise ValueError(f"cannot split unclosed HTML: {name}")
        else:
            spans.append(match.span())
    if stack:
        raise ValueError("cannot split an unclosed HTML element")
    return spans


def _lists(text: str, protected: list[tuple[int, int]]) -> list[tuple[int, int]]:
    spans = []
    active, after_blank, offset = None, False, 0
    for line in text.splitlines(keepends=True):
        inside = any(a <= offset < b for a, b in protected)
        item = None if inside else _LIST.match(line)
        expanded = line.expandtabs(4)
        indent = len(expanded) - len(expanded.lstrip(" "))
        if item and (active is None or len(item[1]) <= active[1]):
            if active is not None:
                spans.append((active[0], offset))
            active, after_blank = (offset, len(item[1])), False
        elif active is not None and not inside:
            if not line.strip():
                after_blank = True
            elif after_blank and indent <= active[1]:
                spans.append((active[0], offset))
                active, after_blank = None, False
            else:
                after_blank = False
        offset += len(line)
    if active is not None:
        spans.append((active[0], len(text)))
    return spans


def _protected(text: str) -> list[tuple[int, int]]:
    spans = _fences(text)
    masked = list(text)
    for a, b in spans:
        masked[a:b] = ["\n" if c == "\n" else " " for c in text[a:b]]
    for match in CODE_SPAN.finditer("".join(masked)):
        spans.append(match.span())
        masked[match.start() : match.end()] = ["\n" if c == "\n" else " " for c in match[0]]
    spans.extend(_html("".join(masked)))
    return spans + _lists(text, spans)


def heading_sections(text: str) -> list[str]:
    """Cut before anchored second-level headings outside complete Markdown units."""
    protected = _protected(text)
    cuts = [0]
    for match in re.finditer(r'^<a id="[^"]+"></a>\n\n(?=## )', text, re.MULTILINE):
        offset = match.start()
        if offset and not any(a < offset < b for a, b in protected):
            cuts.append(offset)
    cuts.append(len(text))
    parts = [text[a:b] for a, b in pairwise(cuts)]
    return [part.removesuffix("\n") for part in parts[:-1]] + parts[-1:]


def _row_width(line: str) -> int:
    prose = CODE_SPAN.sub("", line.strip())
    prose = re.sub(r"\\.", "", prose).removeprefix("|").removesuffix("|")
    return prose.count("|") + 1


def _table_rows(text: str) -> dict[int, tuple[str, int]]:
    """Row roles and cell counts preserve table boundaries as well as optional pipes."""
    lines = outside_fences(text)
    rows: dict[int, tuple[str, int]] = {}
    active, delimiter, offset = False, -1, 0
    for index, (line, fenced) in enumerate(lines):
        role = "pipe"
        if fenced or not line.strip():
            active = False
        elif not active and index + 1 < len(lines) and not lines[index + 1][1]:
            following = lines[index + 1][0]
            if (
                "|" in line + following
                and _SEPARATOR.fullmatch(following)
                and _row_width(line) == _row_width(following)
            ):
                active, delimiter, role = True, index + 1, "header"
        if active and role != "header":
            role = "delimiter" if index == delimiter else "data"
        if not fenced and line.strip() and (active or line.lstrip().startswith("|")):
            rows[offset] = (role, _row_width(line))
        offset += len(line) + 1
    return rows


def _boundaries(text: str) -> list[int]:
    lines, offset, last = text.splitlines(keepends=True), 0, ""
    protected = _protected(text)
    rows = _table_rows(text)
    boundaries = [0]
    for previous, current in pairwise(lines):
        offset += len(previous)
        if previous.strip():
            last = previous.strip()
        blank = not previous.strip() and not ANCHOR.fullmatch(last)
        row = (
            offset in rows and offset - len(previous) in rows and not _SEPARATOR.fullmatch(current)
        )
        if (blank or row or _LIST.match(current)) and not any(a < offset < b for a, b in protected):
            boundaries.append(offset)
    return [*boundaries, len(text)]


def split(text: str) -> list[str]:
    """Keep code, HTML, paragraphs, list items and table rows within one bounded request."""
    if len(text.encode()) <= MAX_BYTES:
        return [text]
    boundaries = _boundaries(text)
    for a, b in pairwise(boundaries):
        if len(text[a:b].encode()) > MAX_BYTES:
            raise ValueError(
                f"indivisible Markdown unit exceeds the {MAX_BYTES}-byte translation request bound"
            )
    cuts, previous = [0], 0
    for end in boundaries[1:]:
        if len(text[cuts[-1] : end].encode()) > MAX_BYTES:
            cuts.append(previous)
        previous = end
    cuts.append(len(text))
    parts = [text[a:b] for a, b in pairwise(cuts)]
    # The caller joins requests with one newline, as it does between heading sections.
    return [part.removesuffix("\n") for part in parts[:-1]] + parts[-1:]


def table_shape(text: str) -> list[tuple[str, int]]:
    return list(_table_rows(text).values())
