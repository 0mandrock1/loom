"""Shared Markdown tokens and byte-preserving bounded translation requests."""

from __future__ import annotations

import re
from itertools import pairwise
from xml.etree.ElementTree import Element

from markdown import Markdown
from markdown.treeprocessors import Treeprocessor
from markdown_it import MarkdownIt

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


def _line_starts(text: str) -> list[int]:
    starts = [0]
    for line in text.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))
    return starts


def _lists(text: str) -> list[tuple[int, int]]:
    starts = _line_starts(text)
    return [
        (starts[token.map[0]], starts[token.map[1]])
        for token in MarkdownIt("commonmark").enable("table").parse(text)
        if token.type == "list_item_open" and token.map is not None
    ]


def _site_line(line: str) -> tuple[int, str]:
    depth, logical = 0, line.expandtabs(4)
    while match := re.match(r"^ *> ?", logical):
        depth += 1
        logical = logical[match.end() :]
    return depth, logical


def _site_lists(text: str, protected: list[tuple[int, int]]) -> list[tuple[int, int]]:
    # The site's two-space list extension has no source maps; protect its continuations
    # in addition to CommonMark's marker-width-dependent ranges.
    spans = []
    active, after_blank, offset = None, False, 0
    for line in text.splitlines(keepends=True):
        inside = any(a <= offset < b for a, b in protected)
        depth, logical = _site_line(line)
        item = None if inside else re.match(r"^( *)(?:[-+*]|\d+[.)])\s", logical)
        indent = len(logical) - len(logical.lstrip(" "))
        new_block = not inside or any(a == offset for a, _ in protected)
        if (
            active is not None
            and new_block
            and (depth != active[2] or (after_blank and logical.strip() and indent < active[1] + 2))
        ):
            spans.append((active[0], offset))
            active, after_blank = None, False
        if item and (active is None or len(item[1]) <= active[1]):
            if active is not None:
                spans.append((active[0], offset))
            active, after_blank = (offset, len(item[1]), depth), False
        elif active is not None and not inside:
            after_blank = not logical.strip()
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
    return spans + _lists(text) + _site_lists(text, spans)


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


def _table_rows(text: str) -> dict[int, str]:
    """Source row offsets from the parser; a header and delimiter stay together."""
    starts = _line_starts(text)
    rows = {}
    for token in MarkdownIt("commonmark").enable("table").parse(text):
        if token.map is None:
            continue
        if token.type == "table_open":
            rows[starts[token.map[0] + 1]] = "delimiter"
        elif token.type == "tr_open":
            rows[starts[token.map[0]]] = "row"
    return rows


def _boundaries(text: str) -> list[int]:
    lines, offset, last = text.splitlines(keepends=True), 0, ""
    protected = _protected(text)
    rows = _table_rows(text)
    items = {a for a, _ in [*_lists(text), *_site_lists(text, [])]}
    boundaries = [0]
    for previous, _current in pairwise(lines):
        offset += len(previous)
        if previous.strip():
            last = previous.strip()
        blank = not previous.strip() and not ANCHOR.fullmatch(last)
        row = offset in rows and offset - len(previous) in rows and rows[offset] != "delimiter"
        if (blank or row or offset in items) and not any(a < offset < b for a, b in protected):
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


def _layout(element: Element) -> object:
    tags = {"thead", "tbody", "tr", "th", "td"}
    return (
        element.tag,
        tuple(sorted(element.attrib.items())),
        bool("".join(element.itertext()).strip()) if element.tag in {"th", "td"} else None,
        tuple(_layout(child) for child in element if child.tag in tags),
    )


def _tables(element: Element, parents: tuple[object, ...] = ()) -> list[object]:
    if element.tag == "table":
        return [(parents, _layout(element))]
    context = (*parents, (element.tag, tuple(sorted(element.attrib.items()))))
    return [table for child in element for table in _tables(child, context)]


class _TableShape(Treeprocessor):
    def __init__(self, md: Markdown) -> None:
        super().__init__(md)
        self.tables: list[object] = []

    def run(self, root: Element) -> None:
        self.tables = _tables(root)


def _github_tables(text: str) -> list[object]:
    stack, tables = [], []
    inside = False
    for token in MarkdownIt("commonmark").enable("table").parse(text):
        attributes = tuple(sorted(token.attrs.items()))
        if token.type == "table_open":
            tables.append(tuple(stack))
            inside = True
        if inside and token.nesting:
            tables.append((token.type, attributes))
        elif inside and token.type == "inline":
            # Ignore translated words while retaining whether a cell contains text.
            content = "".join(
                child.content
                for child in token.children or []
                if child.type in {"text", "code_inline"}
            )
            tables.append(("cell_text", bool(content.strip())))
        if token.type == "table_close":
            inside = False
        if token.nesting == 1:
            stack.append((token.type, attributes))
        elif token.nesting == -1:
            stack.pop()
    return tables


def table_shape(text: str) -> tuple[list[object], list[object]]:
    """Rendered table structure, including containers, rows, cells and alignment."""
    md = Markdown(
        extensions=[
            "tables",
            "admonition",
            "attr_list",
            "mdx_truly_sane_lists",
            "pymdownx.tasklist",
            "pymdownx.superfences",
        ]
    )
    shape = _TableShape(md)
    md.treeprocessors.register(shape, "translation_table_shape", 0)
    md.convert(text)
    return shape.tables, _github_tables(text)
