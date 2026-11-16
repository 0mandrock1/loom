"""Translation routes and alternate links follow the translations that exist."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from scripts.docs_site import locales

URL = "https://example.com/loom/"


def _source(root: Path, uri: str, text: str = "# Page\n\nThe current page.\n") -> str:
    path = root / "docs" / uri
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    content = path.read_bytes()
    return hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest()


def _translation(root: Path, language: str, source: str, blob: str) -> None:
    path = root / "i18n" / language / "docs" / source
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"<!-- i18n: source=docs/{source} blob={blob} model=test -->\n# Page\n")


def test_routes_require_existing_english_and_translation_and_report_stale_stamps(tmp_path):
    blob = _source(tmp_path, "use-cases/index.md")
    _translation(tmp_path, "ja", "use-cases/index.md", blob)
    _translation(tmp_path, "es", "use-cases/index.md", "0" * 40)
    _translation(tmp_path, "ko", "missing.md", blob)
    pages = locales.translations(tmp_path)
    assert set(pages) == {"ja/index.md", "es/index.md"}
    assert pages["ja/index.md"]["stale"] is False
    assert pages["es/index.md"]["stale"] is True
    _source(tmp_path, "use-cases/index.md", "# Changed\n")
    assert locales.translations(tmp_path)["ja/index.md"]["stale"] is True


def test_equivalent_links_are_reciprocal_and_missing_locales_are_absent(tmp_path):
    blob = _source(tmp_path, "use-cases/index.md")
    _translation(tmp_path, "ja", "use-cases/index.md", blob)
    pages = locales.translations(tmp_path)
    expected = [("en", URL + "use-cases/"), ("ja", URL + "ja/"), ("x-default", URL + "use-cases/")]
    assert [
        (a["lang"], a["url"]) for a in locales.alternates("use-cases/index.md", pages, URL)
    ] == expected
    for uri in ("use-cases/index.md", "ja/index.md"):
        page = SimpleNamespace(file=SimpleNamespace(src_uri=uri), meta={})
        config = SimpleNamespace(extra={"locale_pages": pages}, site_url=URL)
        locales.on_page_markdown("# Page", page, config, None)
        assert [(a["lang"], a["url"]) for a in page.meta["alternates"]] == expected
    assert locales.alternates("index.md", pages, URL) == []


def test_localized_root_links_resolve_from_source_and_banner_stays_english(tmp_path, monkeypatch):
    source = "use-cases/index.md"
    blob = _source(tmp_path, source)
    for uri in ("use-cases/memory.md", "getting-started/quickstart.md", "features/setup.md"):
        _source(tmp_path, uri)
    _translation(tmp_path, "ja", source, blob)
    _translation(tmp_path, "ja", "use-cases/memory.md", blob)
    asset = tmp_path / "docs" / "assets" / "figure.svg"
    asset.parent.mkdir()
    asset.write_text("<svg/>")
    pages = locales.translations(tmp_path)
    monkeypatch.setattr(locales, "ROOT", tmp_path)
    body = (
        f"<!-- stamp -->\n> [English]({locales.BLOB}docs/{source})\n\n"
        "# Page\n\n[memory](memory.md#save) [setup](../features/setup.md) "
        "![figure](../assets/figure.svg) "
        f"[absolute]({locales.BLOB}i18n/ja/docs/use-cases/memory.md) "
        f"[English docs]({locales.BLOB}docs/getting-started/quickstart.md) "
        "`[code](memory.md)`\n```md\n[fenced](memory.md)\n```\n[same](#anchor)"
    )
    out = locales.translated_markdown(body, pages["ja/index.md"], pages, URL)
    assert f"[English]({URL}use-cases/)" in out
    assert f"[memory]({URL}ja/use-cases/memory/#save)" in out
    assert f"[absolute]({URL}ja/use-cases/memory/)" in out
    assert f"[setup]({URL}features/setup/)" in out
    assert f"![figure]({URL}assets/figure.svg)" in out
    assert f"[English docs]({URL}getting-started/quickstart/)" in out
    assert "`[code](memory.md)`" in out and "[fenced](memory.md)" in out
    assert "[same](#anchor)" in out


def test_homepage_links_to_landings_without_claiming_equivalent_content(tmp_path):
    blob = _source(tmp_path, "use-cases/index.md")
    _translation(tmp_path, "ja", "use-cases/index.md", blob)
    page = SimpleNamespace(file=SimpleNamespace(src_uri="index.md"), meta={})
    config = SimpleNamespace(extra={"locale_pages": locales.translations(tmp_path)}, site_url=URL)
    locales.on_page_markdown("# Home", page, config, None)
    assert page.meta["language_landings"] == [{"lang": "ja", "name": "日本語", "url": URL + "ja/"}]
    assert page.meta["alternates"] == []


def test_html_language_matches_the_page():
    page = SimpleNamespace(meta={"language": "pt-BR"})
    assert (
        locales.on_post_page('<html lang="en" class="no-js"><p lang="en">Source</p>', page, None)
        == '<html lang="pt-BR" class="no-js"><p lang="en">Source</p>'
    )


def test_indexnow_maps_translation_paths_and_omits_readme():
    from scripts.docs_site import indexnow

    assert indexnow.page_url("i18n/ja/docs/use-cases/index.md") == indexnow.SITE + "ja/"
    assert (
        indexnow.page_url("i18n/es/docs/getting-started/quickstart.md")
        == indexnow.SITE + "es/getting-started/quickstart/"
    )
    assert indexnow.page_url("i18n/ja/README.md") is None
    assert indexnow.page_url("i18n/unknown/docs/page.md") is None


def test_locale_render_changes_submit_all_existing_translations(tmp_path, monkeypatch):
    from scripts.docs_site import indexnow

    blob = _source(tmp_path, "use-cases/index.md")
    _translation(tmp_path, "ja", "use-cases/index.md", blob)
    monkeypatch.setattr(indexnow, "DOCS", tmp_path / "docs")
    for changed in (
        "scripts/docs_site/locales.py",
        "scripts/docs_site/overrides/partials/locales-head.html",
        "scripts/docs_site/overrides/partials/locales-switch.html",
        "scripts/docs_site/overrides/sitemap.xml",
    ):
        assert indexnow.urls_for([changed]) == [indexnow.SITE + "ja/", indexnow.SITE + "use-cases/"]


def test_translated_heading_sets_page_and_metadata_title_after_the_source_banner(tmp_path):
    blob = _source(tmp_path, "use-cases/index.md")
    _translation(tmp_path, "ja", "use-cases/index.md", blob)
    pages = locales.translations(tmp_path)
    path = Path(pages["ja/index.md"]["path"])
    stamp = path.read_text().splitlines()[0]
    markdown = (
        f'{stamp}\n> Machine translation.\n\n<a id="use-cases"></a>\n\n# ユースケース\n\n本文。\n'
    )
    path.write_text(markdown)
    page = SimpleNamespace(file=SimpleNamespace(src_uri="ja/index.md"), meta={}, title="Index")
    config = SimpleNamespace(extra={"locale_pages": pages}, site_url=URL)
    locales.on_page_markdown(markdown, page, config, None)
    assert page.title == "ユースケース"
    assert page.meta["title"] == "ユースケース"


def test_full_indexnow_submission_excludes_unpublished_orphans_but_accepts_deletion(
    tmp_path, monkeypatch
):
    from scripts.docs_site import indexnow

    (tmp_path / "docs").mkdir()
    _translation(tmp_path, "ja", "missing.md", "0" * 40)
    assert locales.translations(tmp_path) == {}
    monkeypatch.setattr(indexnow, "DOCS", tmp_path / "docs")
    assert indexnow.every_page() == []
    assert indexnow.urls_for(["scripts/docs_site/locales.py"]) == []
    assert indexnow.urls_for(["i18n/ja/docs/missing.md"]) == [indexnow.SITE + "ja/missing/"]


def test_open_graph_locales_require_a_language_and_territory(tmp_path):
    blob = _source(tmp_path, "use-cases/index.md")
    for language in locales.LANGUAGES:
        _translation(tmp_path, language, "use-cases/index.md", blob)
    pages = locales.translations(tmp_path)
    expected = {"zh-CN": "zh_CN", "pt-BR": "pt_BR"}
    config = SimpleNamespace(extra={"locale_pages": pages}, site_url=URL)
    for uri in ["use-cases/index.md", *pages]:
        page = SimpleNamespace(file=SimpleNamespace(src_uri=uri), meta={})
        locales.on_page_markdown("# Page", page, config, None)
        assert page.meta.get("og_locale") == expected.get(page.meta["language"])


def test_the_hook_loads_without_an_editable_repository_install():
    hook = Path(__file__).resolve().parents[2] / "scripts/docs_site/locales.py"
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            "import runpy, sys; runpy.run_path(sys.argv[1])",
            str(hook),
        ],
        cwd=hook.parent,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
