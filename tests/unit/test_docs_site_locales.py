"""Translation routes and alternate links follow the translations that exist."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

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


def test_routes_report_stale_stamps_and_retain_only_owned_obsolete_translations(tmp_path):
    blob = _source(tmp_path, "use-cases/index.md")
    _translation(tmp_path, "ja", "use-cases/index.md", blob)
    _translation(tmp_path, "es", "use-cases/index.md", "0" * 40)
    _translation(tmp_path, "ko", "missing.md", blob)
    pages = locales.translations(tmp_path, languages=locales.LANGUAGES)
    assert set(pages) == {"ja/use-cases/index.md", "es/use-cases/index.md", "ko/missing.md"}
    assert pages["ja/use-cases/index.md"]["stale"] is False
    assert pages["es/use-cases/index.md"]["stale"] is True
    assert pages["ko/missing.md"]["obsolete"] is True
    assert pages["ko/missing.md"]["stale"] is True
    _source(tmp_path, "use-cases/index.md", "# Changed\n")
    assert (
        locales.translations(tmp_path, languages=locales.LANGUAGES)["ja/use-cases/index.md"][
            "stale"
        ]
        is True
    )


def test_equivalent_links_are_reciprocal_and_missing_locales_are_absent(tmp_path):
    blob = _source(tmp_path, "use-cases/index.md")
    _translation(tmp_path, "ja", "use-cases/index.md", blob)
    pages = locales.translations(tmp_path, languages=locales.LANGUAGES)
    expected = [
        ("en", URL + "use-cases/"),
        ("ja", URL + "ja/use-cases/"),
        ("x-default", URL + "use-cases/"),
    ]
    assert [
        (a["lang"], a["url"]) for a in locales.alternates("use-cases/index.md", pages, URL)
    ] == expected
    for uri in ("use-cases/index.md", "ja/use-cases/index.md"):
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
    pages = locales.translations(tmp_path, languages=locales.LANGUAGES)
    monkeypatch.setattr(locales, "ROOT", tmp_path)
    body = (
        f"<!-- stamp -->\n> [English]({locales.BLOB}docs/{source})\n\n"
        "# Page\n\n[memory](memory.md#save) [setup](../features/setup.md) "
        "![figure](../assets/figure.svg) "
        f"[absolute]({locales.BLOB}i18n/ja/docs/use-cases/memory.md) "
        f"[English docs]({locales.BLOB}docs/getting-started/quickstart.md) "
        "`[code](memory.md)`\n```md\n[fenced](memory.md)\n```\n[same](#anchor)"
    )
    out = locales.translated_markdown(body, pages["ja/use-cases/index.md"], pages, URL)
    assert f"[English]({URL}use-cases/)" in out
    assert f"[memory]({URL}ja/use-cases/memory/#save)" in out
    assert f"[absolute]({URL}ja/use-cases/memory/)" in out
    assert f"[setup]({URL}features/setup/)" in out
    assert f"![figure]({URL}assets/figure.svg)" in out
    assert f"[English docs]({locales.BLOB}docs/getting-started/quickstart.md)" in out
    assert "`[code](memory.md)`" in out and "[fenced](memory.md)" in out
    assert "[same](#anchor)" in out


def test_homepage_links_to_landings_without_claiming_equivalent_content(tmp_path):
    blob = _source(tmp_path, "use-cases/index.md")
    _translation(tmp_path, "ja", "use-cases/index.md", blob)
    page = SimpleNamespace(file=SimpleNamespace(src_uri="index.md"), meta={})
    config = SimpleNamespace(
        extra={"locale_pages": locales.translations(tmp_path, languages=locales.LANGUAGES)},
        site_url=URL,
    )
    locales.on_page_markdown("# Home", page, config, None)
    assert page.meta["language_landings"] == [
        {"lang": "ja", "name": "日本語", "url": URL + "ja/use-cases/"}
    ]
    assert page.meta["alternates"] == []


def test_html_language_matches_the_page():
    page = SimpleNamespace(meta={"language": "pt-BR"})
    assert (
        locales.on_post_page('<html lang="en" class="no-js"><p lang="en">Source</p>', page, None)
        == '<html lang="pt-BR" class="no-js"><p lang="en">Source</p>'
    )


def test_indexnow_maps_translation_paths_and_omits_readme():
    from scripts.docs_site import indexnow

    assert indexnow.page_url("i18n/ja/docs/use-cases/index.md") == indexnow.SITE + "ja/use-cases/"
    assert (
        locales.localized_page_path(
            "i18n/es/docs/getting-started/quickstart.md", languages=locales.LANGUAGES
        )
        == "es/getting-started/quickstart/"
    )
    assert indexnow.page_url("i18n/es/docs/getting-started/quickstart.md") is None
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
        assert indexnow.urls_for([changed]) == [
            indexnow.SITE + "ja/use-cases/",
            indexnow.SITE + "use-cases/",
        ]


def test_translated_heading_sets_page_and_metadata_title_after_the_source_banner(tmp_path):
    blob = _source(tmp_path, "use-cases/index.md")
    _translation(tmp_path, "ja", "use-cases/index.md", blob)
    pages = locales.translations(tmp_path, languages=locales.LANGUAGES)
    path = Path(pages["ja/use-cases/index.md"]["path"])
    stamp = path.read_text().splitlines()[0]
    markdown = (
        f'{stamp}\n> Machine translation.\n\n<a id="use-cases"></a>\n\n# ユースケース\n\n本文。\n'
    )
    path.write_text(markdown)
    page = SimpleNamespace(
        file=SimpleNamespace(src_uri="ja/use-cases/index.md"), meta={}, title="Index"
    )
    config = SimpleNamespace(extra={"locale_pages": pages}, site_url=URL)
    locales.on_page_markdown(markdown, page, config, None)
    assert page.title == "ユースケース"
    assert page.meta["title"] == "ユースケース"


def test_full_indexnow_submission_retains_owned_orphans_and_excludes_unowned_output(
    tmp_path, monkeypatch
):
    from scripts.docs_site import indexnow

    (tmp_path / "docs").mkdir()
    _translation(tmp_path, "ja", "missing.md", "0" * 40)
    assert "ja/missing.md" in locales.translations(tmp_path, languages=locales.LANGUAGES)
    monkeypatch.setattr(indexnow, "DOCS", tmp_path / "docs")
    assert indexnow.every_page() == [indexnow.SITE + "ja/missing/"]
    assert indexnow.urls_for(["scripts/docs_site/locales.py"]) == [indexnow.SITE + "ja/missing/"]
    (tmp_path / "i18n/ja/docs/missing.md").write_text("# Unstamped orphan\n")
    _translation(tmp_path, "unknown", "hidden.md", "0" * 40)
    _translation(tmp_path, ".pending/ja", "hidden.md", "0" * 40)
    _translation(tmp_path, "es", "mismatch.md", "0" * 40)
    (tmp_path / "i18n/es/docs/mismatch.md").write_text(
        "<!-- i18n: source=docs/other.md blob=" + "0" * 40 + " model=test -->\n# Wrong source\n"
    )
    assert locales.translations(tmp_path, languages=locales.LANGUAGES) == {}
    assert indexnow.every_page() == []
    assert indexnow.urls_for(["scripts/docs_site/locales.py"]) == []
    assert indexnow.urls_for(["i18n/ja/docs/missing.md"]) == [indexnow.SITE + "ja/missing/"]


def test_open_graph_locales_require_a_language_and_territory(tmp_path):
    blob = _source(tmp_path, "use-cases/index.md")
    for language in locales.LANGUAGES:
        _translation(tmp_path, language, "use-cases/index.md", blob)
    pages = locales.translations(tmp_path, languages=locales.LANGUAGES)
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


def test_translation_changes_resubmit_pages_with_alternates_and_rewritten_links(
    tmp_path, monkeypatch
):
    from scripts.docs_site import indexnow

    blob = _source(tmp_path, "use-cases/index.md")
    _source(tmp_path, "other.md")
    _translation(tmp_path, "ja", "use-cases/index.md", blob)
    monkeypatch.setattr(indexnow, "DOCS", tmp_path / "docs")
    every = indexnow.every_page()
    assert indexnow.urls_for(["docs/use-cases/index.md"]) == every
    assert indexnow.urls_for(["i18n/ja/docs/use-cases/index.md"]) == every
    deleted = "i18n/ja/docs/use-cases/deleted.md"
    assert indexnow.urls_for([deleted]) == sorted([*every, indexnow.SITE + "ja/use-cases/deleted/"])
    assert indexnow.urls_for(["docs/other.md"]) == every


def test_deleted_english_source_announces_removed_translated_routes(tmp_path, monkeypatch):
    from scripts.docs_site import indexnow

    _source(tmp_path, "other.md")
    _translation(tmp_path, "ja", "use-cases/index.md", "0" * 40)
    monkeypatch.setattr(indexnow, "DOCS", tmp_path / "docs")
    assert indexnow.urls_for(["docs/use-cases/index.md"]) == [
        indexnow.SITE + "ja/use-cases/",
        indexnow.SITE + "other/",
        indexnow.SITE + "use-cases/",
    ]


def test_removed_untranslated_target_resubmits_translations_with_rewritten_links(
    tmp_path, monkeypatch
):
    from scripts.docs_site import indexnow

    blob = _source(tmp_path, "use-cases/index.md")
    _source(tmp_path, "features/setup.md")
    _translation(tmp_path, "ja", "use-cases/index.md", blob)
    monkeypatch.setattr(indexnow, "DOCS", tmp_path / "docs")
    monkeypatch.setattr(locales, "ROOT", tmp_path)
    pages = locales.translations(tmp_path, languages=locales.LANGUAGES)
    entry = pages["ja/use-cases/index.md"]
    body = f"<!-- stamp -->\n\n[setup]({locales.BLOB}docs/features/setup.md)"
    assert locales.translated_markdown(body, entry, pages, URL) == body
    (tmp_path / "docs/features/setup.md").unlink()
    assert locales.translated_markdown(body, entry, pages, URL) == body
    assert indexnow.urls_for(["docs/features/setup.md"]) == sorted(
        [*indexnow.every_page(), indexnow.SITE + "features/setup/"]
    )


def test_translated_section_index_serves_the_same_markdown_aliases(tmp_path, monkeypatch):
    blob = _source(tmp_path, "use-cases/index.md")
    _translation(tmp_path, "ja", "use-cases/index.md", blob)
    pages = locales.translations(tmp_path, languages=locales.LANGUAGES)
    site = tmp_path / "site"
    monkeypatch.setattr(locales, "ROOT", tmp_path)
    locales.on_post_build(
        SimpleNamespace(site_dir=site, site_url=URL, extra={"locale_pages": pages})
    )
    body = (site / "ja/use-cases/index.md").read_text()
    assert (site / "ja/use-cases.md").read_text() == body
    assert (site / "ja/use-cases/index.html.md").read_text() == body


@pytest.mark.parametrize("source", ["use-cases/index.md", "getting-started/quickstart.md"])
@pytest.mark.parametrize("stale", [False, True])
def test_markdown_aliases_warn_only_when_the_english_source_changes(
    tmp_path, monkeypatch, source, stale
):
    blob = _source(tmp_path, source)
    _translation(tmp_path, "ja", source, blob)
    if stale:
        _source(tmp_path, source, "# Updated instructions\n")
    pages = locales.translations(tmp_path, languages=locales.LANGUAGES)
    site = tmp_path / "site"
    monkeypatch.setattr(locales, "ROOT", tmp_path)
    locales.on_post_build(
        SimpleNamespace(site_dir=site, site_url=URL, extra={"locale_pages": pages})
    )
    uri = locales.localized_uri("ja", source)
    directory = (
        "ja/use-cases" if source == "use-cases/index.md" else "ja/getting-started/quickstart"
    )
    paths = [site / uri, site / directory / "index.html.md"]
    if source == "use-cases/index.md":
        paths.append(site / "ja/use-cases.md")
    notice = "The English source has changed since this machine translation."
    english = "use-cases/" if source == "use-cases/index.md" else "getting-started/quickstart/"
    for path in paths:
        text = path.read_text()
        assert (notice in text) is stale
        if stale:
            assert f"[English page]({URL}{english})" in text


def test_explicit_english_anchor_fallback_is_not_redirected_to_a_translation(tmp_path, monkeypatch):
    source = "use-cases/index.md"
    blob = _source(tmp_path, source)
    memory = _source(tmp_path, "use-cases/agent-memory.md", "# Memory\n\n## Measured\n")
    _translation(tmp_path, "ja", source, blob)
    _translation(tmp_path, "ja", "use-cases/agent-memory.md", memory)
    pages = locales.translations(tmp_path, languages=locales.LANGUAGES)
    monkeypatch.setattr(locales, "ROOT", tmp_path)
    body = (
        "<!-- stamp -->\n\n"
        f"[measured]({locales.BLOB}docs/use-cases/agent-memory.md#measured) "
        f"[translated]({locales.BLOB}i18n/ja/docs/use-cases/agent-memory.md)"
    )
    result = locales.translated_markdown(body, pages["ja/use-cases/index.md"], pages, URL)
    assert f"[measured]({locales.BLOB}docs/use-cases/agent-memory.md#measured)" in result
    assert f"[translated]({URL}ja/use-cases/agent-memory/)" in result


@pytest.mark.parametrize("source", ["use-cases/agent-memory.md", "use-cases/index.md"])
def test_retained_machine_translation_survives_source_rename_until_dependency_cleanup(
    tmp_path, monkeypatch, source
):
    blob = _source(tmp_path, source)
    _translation(tmp_path, "ja", source, blob)
    path = tmp_path / "i18n/ja/docs" / source
    path.write_text(
        f"<!-- i18n: source=docs/{source} blob={blob} model=test -->\n"
        f"> Machine-translated from the [English page]({locales.BLOB}docs/{source}).\n\n"
        '# Retained page\n\n<a id="measured"></a>\n'
    )
    monkeypatch.setattr(locales, "ROOT", tmp_path)
    uri = locales.localized_uri("ja", source)
    assert uri in locales.translations(tmp_path, languages=locales.LANGUAGES)
    (tmp_path / "docs" / source).rename(tmp_path / "docs/use-cases/replacement.md")
    pages = locales.translations(tmp_path, languages=locales.LANGUAGES)
    assert uri in pages
    assert pages[uri]["stale"] is True
    page = SimpleNamespace(file=SimpleNamespace(src_uri=uri), meta={}, title="Page")
    config = SimpleNamespace(
        extra={"locale_pages": pages}, site_url=URL, site_dir=tmp_path / "site"
    )
    result = locales.on_page_markdown(path.read_text(), page, config, None)
    assert page.meta["alternates"] == []
    assert "Its English source is no longer available." in result
    assert f"]({locales.BLOB}docs/{source})" not in result
    assert "# Retained page" in result and 'id="measured"' in result
    referring = {"language": "ja", "source": "use-cases/index.md"}
    link = (
        f"<!-- stamp -->\n\n[retained]({locales.BLOB}i18n/ja/docs/{source}#measured) "
        f"[relative]({Path(source).name}#measured)"
    )
    assert (
        locales.translated_markdown(link, referring, pages, URL).count(
            URL + locales.page_path(f"docs/{uri}") + "#measured"
        )
        == 2
    )
    locales.on_post_build(config)
    aliases = [
        config.site_dir / uri,
        config.site_dir / locales.page_path(f"docs/{uri}") / "index.html.md",
    ]
    if source == "use-cases/index.md":
        aliases.append(config.site_dir / "ja/use-cases.md")
    for alias in aliases:
        text = alias.read_text()
        assert "Its English source is no longer available." in text
        assert f"]({locales.BLOB}docs/{source})" not in text
        assert "Read the [English page]" not in text
    path.unlink()
    assert uri not in locales.translations(tmp_path, languages=locales.LANGUAGES)


@pytest.mark.parametrize("language", ["ja", "es"])
def test_home_and_use_case_indexes_keep_distinct_routes_and_aliases(
    tmp_path, monkeypatch, language
):
    from scripts.docs_site import indexnow

    for source, title in (("index.md", "Home"), ("use-cases/index.md", "Use cases")):
        blob = _source(tmp_path, source, f"# {title}\n")
        _translation(tmp_path, language, source, blob)
        path = tmp_path / "i18n" / language / "docs" / source
        path.write_text(path.read_text() + f"\n{title} body\n")
    monkeypatch.setattr(locales, "ROOT", tmp_path)
    monkeypatch.setattr(indexnow, "DOCS", tmp_path / "docs")
    pages = locales.translations(tmp_path, languages=locales.LANGUAGES)
    assert {uri: entry["source"] for uri, entry in pages.items()} == {
        f"{language}/index.md": "index.md",
        f"{language}/use-cases/index.md": "use-cases/index.md",
    }
    config = SimpleNamespace(
        extra={"locale_pages": pages}, site_url=URL, site_dir=tmp_path / "site"
    )
    for source, suffix in (("index.md", ""), ("use-cases/index.md", "use-cases/")):
        expected = [
            ("en", URL + suffix),
            (language, URL + language + "/" + suffix),
            ("x-default", URL + suffix),
        ]
        for uri in (source, f"{language}/{source}"):
            page = SimpleNamespace(file=SimpleNamespace(src_uri=uri), meta={})
            locales.on_page_markdown("# Page", page, config, None)
            assert [(a["lang"], a["url"]) for a in page.meta["alternates"]] == expected
    homepage = SimpleNamespace(file=SimpleNamespace(src_uri="index.md"), meta={})
    locales.on_page_markdown("# Home", homepage, config, None)
    assert homepage.meta["language_landings"] == [
        {"lang": language, "name": locales.LANGUAGES[language], "url": URL + language + "/"}
    ]
    body = "<!-- stamp -->\n\n[home](../index.md#intro)"
    assert f"[home]({URL}{language}/#intro)" in locales.translated_markdown(
        body, pages[f"{language}/use-cases/index.md"], pages, URL
    )
    locales.on_post_build(config)
    for suffix, title in (("", "Home"), ("/use-cases", "Use cases")):
        directory = language + suffix
        aliases = [f"{directory}/index.md", f"{directory}.md", f"{directory}/index.html.md"]
        texts = [(config.site_dir / alias).read_text() for alias in aliases]
        assert texts[0] == texts[1] == texts[2]
        assert f"{title} body" in texts[0]
    assert (
        locales.localized_page_path(f"i18n/{language}/docs/index.md", languages=locales.LANGUAGES)
        == language + "/"
    )
    assert (
        locales.localized_page_path(
            f"i18n/{language}/docs/use-cases/index.md", languages=locales.LANGUAGES
        )
        == language + "/use-cases/"
    )
    published = language in locales.PUBLISHED_LANGUAGES
    for source, suffix in (("index.md", "/"), ("use-cases/index.md", "/use-cases/")):
        assert indexnow.page_url(f"i18n/{language}/docs/{source}") == (
            indexnow.SITE + language + suffix if published else None
        )
    expected = ["", "use-cases/"]
    if published:
        expected.extend([language + "/", language + "/use-cases/"])
    assert indexnow.every_page() == sorted(indexnow.SITE + suffix for suffix in expected)


@pytest.mark.parametrize("source", ["space name.md", "日本語 guide.md"])
def test_space_named_source_stamp_tracks_fresh_stale_and_retained_states(tmp_path, source):
    blob = _source(tmp_path, source)
    _translation(tmp_path, "ja", source, blob)
    uri = f"ja/{source}"
    assert locales.translations(tmp_path, languages=locales.LANGUAGES)[uri]["stale"] is False
    _source(tmp_path, source, "# Changed\n")
    assert locales.translations(tmp_path, languages=locales.LANGUAGES)[uri]["stale"] is True
    (tmp_path / "docs" / source).unlink()
    retained = locales.translations(tmp_path, languages=locales.LANGUAGES)[uri]
    assert retained["obsolete"] is True and retained["stale"] is True


@pytest.mark.parametrize("language", ["ja", "es"])
def test_relative_translated_readme_stays_on_repository_in_html_and_aliases(
    tmp_path, monkeypatch, language
):
    source = "development/repository-guide.md"
    blob = _source(tmp_path, source)
    _translation(tmp_path, language, source, blob)
    (tmp_path / "README.md").write_text("# Loom\n")
    (tmp_path / "i18n" / language / "README.md").write_text("# Loom\n")
    path = tmp_path / "i18n" / language / "docs" / source
    path.write_text(path.read_text() + "\n[README](../../README.md#loom)\n")
    monkeypatch.setattr(locales, "ROOT", tmp_path)
    pages = locales.translations(tmp_path, languages=locales.LANGUAGES)
    uri = f"{language}/{source}"
    page = SimpleNamespace(file=SimpleNamespace(src_uri=uri), meta={})
    config = SimpleNamespace(
        extra={"locale_pages": pages}, site_url=URL, site_dir=tmp_path / "site"
    )
    expected = f"[README]({locales.BLOB}i18n/{language}/README.md#loom)"
    assert expected in locales.on_page_markdown(path.read_text(), page, config, None)
    locales.on_post_build(config)
    for alias in (uri, f"{language}/development/repository-guide/index.html.md"):
        assert expected in (config.site_dir / alias).read_text()
    assert set(pages) == {uri}


def test_default_site_publishes_only_japanese_and_keeps_other_artifacts(tmp_path, monkeypatch):
    from scripts.docs_site import indexnow

    blob = _source(tmp_path, "use-cases/index.md")
    _source(tmp_path, "index.md")
    for language in locales.LANGUAGES:
        _translation(tmp_path, language, "use-cases/index.md", blob)
    monkeypatch.setattr(indexnow, "DOCS", tmp_path / "docs")
    pages = locales.translations(tmp_path)
    assert set(pages) == {"ja/use-cases/index.md"}
    assert {a["lang"] for a in locales.alternates("use-cases/index.md", pages, URL)} == {
        "en",
        "ja",
        "x-default",
    }
    assert locales.language_landings(pages, URL) == [
        {"lang": "ja", "name": "日本語", "url": URL + "ja/use-cases/"}
    ]
    assert indexnow.every_page() == sorted(
        indexnow.SITE + suffix for suffix in ("", "use-cases/", "ja/use-cases/")
    )
    for language in ("zh-CN", "ko", "pt-BR", "es"):
        path = f"i18n/{language}/docs/use-cases/index.md"
        assert (tmp_path / path).is_file()
        assert indexnow.page_url(path) is None
        assert indexnow.urls_for([path]) == []
