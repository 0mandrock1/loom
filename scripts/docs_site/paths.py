"""Routes shared by the HTML, Markdown and IndexNow documentation outputs."""

from __future__ import annotations


def page_path(path: str) -> str:
    """The path under the site a docs/ Markdown file is served at ("" for the home page)."""
    rel = path[len("docs/") : -len(".md")]
    if rel == "index":
        return ""
    if rel.endswith("/index"):
        rel = rel[: -len("/index")]
    return f"{rel}/"
