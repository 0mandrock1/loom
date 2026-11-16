"""Publish the generated translations without changing their repository copies."""

from __future__ import annotations

import hashlib
import html
import posixpath
import re
from pathlib import Path

from scripts.codegen.page_summary import page_summary
from scripts.docs_site.hooks import _TOKEN, BLOB, ROOT, _with_source
from scripts.docs_site.indexnow import page_path

LANGUAGES = {
    "zh-CN": "简体中文",
    "ja": "日本語",
    "ko": "한국어",
    "pt-BR": "Português (Brasil)",
    "es": "Español",
}
# Generic language codes do not identify an Open Graph territory.
_OG_LOCALES = {"zh-CN": "zh_CN", "pt-BR": "pt_BR"}
_STAMP = re.compile(r"<!-- i18n: source=(\S+) blob=([0-9a-f]{40}) model=\S+ -->")


def localized_uri(language: str, source: str) -> str:
    """The translated use-case index is the language's landing page."""
    return f"{language}/index.md" if source == "use-cases/index.md" else f"{language}/{source}"


def localized_page_path(path: str) -> str | None:
    """The site path for an existing or deleted repository translation."""
    parts = path.split("/", 3)
    if (
        len(parts) != 4
        or parts[0] != "i18n"
        or parts[1] not in LANGUAGES
        or parts[2] != "docs"
        or not parts[3].endswith(".md")
    ):
        return None
    return page_path(f"docs/{localized_uri(parts[1], parts[3])}")


def translations(root: Path) -> dict[str, dict]:
    pages = {}
    for language in LANGUAGES:
        for path in sorted((root / "i18n" / language / "docs").rglob("*.md")):
            source = path.relative_to(root / "i18n" / language / "docs").as_posix()
            english = root / "docs" / source
            if not english.is_file():
                continue
            stamp = _STAMP.match(path.read_text(encoding="utf-8"))
            content = english.read_bytes()
            blob = hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest()
            pages[localized_uri(language, source)] = {
                "language": language,
                "source": source,
                "path": str(path),
                "stale": stamp is None
                or stamp.group(1) != f"docs/{source}"
                or stamp.group(2) != blob,
            }
    return pages


def on_files(files, config):
    from mkdocs.structure.files import File, InclusionLevel

    pages = config.extra["locale_pages"] = translations(ROOT)
    for uri, entry in pages.items():
        files.append(
            File.generated(
                config,
                uri,
                abs_src_path=entry["path"],
                inclusion=InclusionLevel.NOT_IN_NAV,
            )
        )
    return files


def alternates(source: str, pages: dict, site_url: str) -> list[dict[str, str]]:
    available = [
        {
            "lang": entry["language"],
            "name": LANGUAGES[entry["language"]],
            "url": site_url + page_path(f"docs/{uri}"),
        }
        for uri, entry in pages.items()
        if entry["source"] == source
    ]
    if not available:
        return []
    english = {"lang": "en", "name": "English", "url": site_url + page_path(f"docs/{source}")}
    return [english, *available, {**english, "lang": "x-default"}]


def _link_target(target: str, entry: dict, pages: dict, site_url: str, *, banner: bool) -> str:
    language = entry["language"]
    if target.startswith(BLOB + "docs/"):
        source = target[len(BLOB + "docs/") :]
    elif target.startswith(BLOB + "i18n/"):
        parts = target[len(BLOB + "i18n/") :].split("/", 2)
        if len(parts) != 3 or parts[1] != "docs":
            return target
        language, _, source = parts
    elif target and not re.match(r"(?:[a-zA-Z][\w+.-]*:|/)", target):
        source = posixpath.normpath(posixpath.join(posixpath.dirname(entry["source"]), target))
    else:
        return target
    if not (ROOT / "docs" / source).is_file():
        return target
    if not source.endswith(".md"):
        return site_url + source
    uri = localized_uri(language, source)
    if banner or uri not in pages:
        uri = source
    return site_url + page_path(f"docs/{uri}")


def translated_markdown(markdown: str, entry: dict, pages: dict, site_url: str) -> str:
    # The stamped banner identifies the authoritative English source, even when a
    # translation of its target exists. Body links prefer the matching language.
    header, separator, body = markdown.partition("\n\n")

    def rewrite(text: str, *, banner: bool) -> str:
        def replace(match: re.Match[str]) -> str:
            if not match.group("link") or not match.group("target"):
                return match.group(0)
            target = _link_target(match.group("target"), entry, pages, site_url, banner=banner)
            return (
                f"{match.group('bang')}{match.group('text')}{target}"
                f"{match.group('anchor') or ''}{match.group('tail')}"
            )

        return _TOKEN.sub(replace, text)

    return rewrite(header, banner=True) + separator + rewrite(body, banner=False)


def on_page_markdown(markdown, page, config, files):
    pages = config.extra["locale_pages"]
    uri = page.file.src_uri
    entry = pages.get(uri)
    source = entry["source"] if entry else uri
    page.meta["language"] = entry["language"] if entry else "en"
    page.meta["og_locale"] = _OG_LOCALES.get(page.meta["language"])
    page.meta["alternates"] = alternates(source, pages, config.site_url)
    if uri == "index.md":
        page.meta["language_landings"] = [
            {"lang": language, "name": name, "url": config.site_url + language + "/"}
            for language, name in LANGUAGES.items()
            if f"{language}/index.md" in pages
        ]
    if not entry:
        return markdown
    page.edit_url = None
    page.meta["title"] = page.title = page_summary(Path(entry["path"]))[0]
    page.meta["translation_stale"] = entry["stale"]
    return translated_markdown(markdown, entry, pages, config.site_url)


def on_post_page(output, page, config):
    language = html.escape(page.meta.get("language", "en"), quote=True)
    return re.sub(r'(<html\b[^>]*\blang=")[^"]*(")', rf"\g<1>{language}\2", output, count=1)


def on_post_build(config):
    site = Path(config.site_dir)
    pages = config.extra["locale_pages"]
    for uri, entry in pages.items():
        body = translated_markdown(
            Path(entry["path"]).read_text(encoding="utf-8"), entry, pages, config.site_url
        )
        text = _with_source(body, config.site_url, uri)
        for out in (site / uri, site / page_path(f"docs/{uri}") / "index.html.md"):
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8")
