"""Lifecycle changes use private Git fixtures and fake translators, with no provider calls."""

from __future__ import annotations

import pathlib
import subprocess

import pytest

from scripts.i18n import lifecycle as lc
from scripts.i18n import translate as t

JA = t.LANGUAGES[1]


def git(root: pathlib.Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def put(root: pathlib.Path, name: str, text: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def repo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.name", "test")
    git(tmp_path, "config", "user.email", "test@example.test")
    put(tmp_path, "docs/a.md", "# Alpha\n")
    put(tmp_path, "docs/b.md", "# Beta\n")
    git(tmp_path, "add", "docs")
    git(tmp_path, "commit", "-qm", "English")
    monkeypatch.setattr(t, "ROOT", tmp_path)
    monkeypatch.setenv("LOOM_I18N_MODEL", t.DEFAULT_MODEL)
    return tmp_path


def translated(source: str) -> str:
    text, line = t.prepare(source, JA)
    body = "\n".join(t.english_requests(text))
    for english, spanish in (
        ("Alpha", "Uno"),
        ("Beta", "Dos"),
        ("New", "Nuevo"),
        ("Peer", "Par"),
        ("guide", "guía"),
    ):
        # Anchors preserve the English spelling; translate only heading and link prose.
        body = body.replace("# " + english, "# " + spanish).replace(
            "[" + english + "]", "[" + spanish + "]"
        )
    return t.render(source, JA, body, line, t.DEFAULT_MODEL)


def publish(root: pathlib.Path, source: str) -> None:
    put(root, t.target_path("ja", source), translated(source))


def test_inventory_tracks_nested_generated_additions_but_not_untracked_or_deleted(
    repo: pathlib.Path,
) -> None:
    put(repo, "README.md", "# Loom\n")
    put(repo, "docs/reference/operations/deep.md", "# Nested\n<!-- generated -->\n")
    put(repo, "docs/not tracked.md", "# Draft\n")
    git(repo, "add", "README.md", "docs/reference")
    (repo / "docs/a.md").unlink()
    assert t.sources() == ("README.md", "docs/b.md", "docs/reference/operations/deep.md")
    git(repo, "mv", "docs/b.md", "docs/renamed.md")
    assert "docs/renamed.md" in t.sources() and "docs/b.md" not in t.sources()


def test_edit_invalidates_only_the_changed_source(repo: pathlib.Path) -> None:
    publish(repo, "docs/a.md")
    publish(repo, "docs/b.md")
    assert t.stale((JA,)) == []
    put(repo, "docs/a.md", "# Alpha\n\nChanged.\n")
    assert t.stale((JA,)) == [("docs/a.md", JA)]


def test_links_fall_back_until_the_translated_target_and_anchor_exist(repo: pathlib.Path) -> None:
    text = "[guide](b.md#beta) ![image](asset.svg)"
    assert f"[guide]({t.REPO_URL}/blob/main/docs/b.md#beta)" in t.rewrite_links(
        text, "docs/a.md", "ja"
    )
    publish(repo, "docs/b.md")
    output = t.rewrite_links(text, "docs/a.md", "ja")
    assert "[guide](b.md#beta)" in output
    assert f"![image]({t.REPO_URL}/raw/main/docs/asset.svg)" in output
    assert "blob/main/docs/b.md#missing" in t.rewrite_links(
        "[guide](b.md#missing)", "docs/a.md", "ja"
    )


def test_bounded_batches_progress_without_repeating_completed_pages(
    repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = []

    def fake(source: str, lang: t.Language, client: object, spend: t.Spend) -> None:
        calls.append(source)
        lc.stage(t.target_path(lang.code, source), translated(source))

    monkeypatch.setenv("LOOM_I18N_MAX_USD", "1")
    monkeypatch.setattr(t, "estimate_usd", lambda model, chars: 1.0)
    monkeypatch.setattr(t, "bedrock_client", lambda: None)
    monkeypatch.setattr(t, "translate_page", fake)
    assert t.update(codes=["ja"]) == 0
    assert calls == ["docs/a.md"]
    assert t.update(codes=["ja"]) == 0
    assert calls == ["docs/a.md", "docs/b.md"]
    assert t.stale((JA,)) == []
    assert "translated=1 failed=0 deferred=1 obsolete=0" in capsys.readouterr().out


def test_changed_present_pages_precede_new_pages(repo: pathlib.Path) -> None:
    publish(repo, "docs/b.md")
    put(repo, "docs/b.md", "# Beta\n\nEdited.\n")
    selected, _ = t.batch(False, ["ja"], 5)
    assert selected[0] == ("docs/b.md", JA)


def test_checkpoint_waits_for_inbound_anchor_repair_without_retranslation(
    repo: pathlib.Path,
) -> None:
    put(repo, "docs/a.md", "# New\n")
    put(repo, "docs/b.md", "# Peer\n\n[guide](a.md#new)\n")
    put(
        repo,
        "i18n/ja/docs/a.md",
        t.header("docs/a.md", JA, t.DEFAULT_MODEL, "0" * 40) + '<a id="old"></a>\n# Anterior\n',
    )
    put(
        repo,
        "i18n/ja/docs/b.md",
        t.header("docs/b.md", JA, t.DEFAULT_MODEL, "0" * 40)
        + '<a id="peer"></a>\n# Par\n[guía](a.md#old)\n',
    )
    lc.stage("i18n/ja/docs/a.md", translated("docs/a.md"))
    assert lc.activate_ready() == 0
    assert '<a id="old">' in (repo / "i18n/ja/docs/a.md").read_text()
    selected, _ = t.batch(False, ["ja"], 5)
    assert selected == [("docs/b.md", JA)]
    lc.stage("i18n/ja/docs/b.md", translated("docs/b.md"))
    assert lc.activate_ready() == 2
    assert t.stale((JA,)) == [] and t.link_problems() == []
    assert lc.checkpoint_files() == []


def test_delete_waits_for_inbound_dependency_closure_and_removes_cycles(repo: pathlib.Path) -> None:
    publish(repo, "docs/a.md")
    publish(repo, "docs/b.md")
    for path, target in (("a", "b"), ("b", "a")):
        with (repo / f"i18n/ja/docs/{path}.md").open("a") as stream:
            stream.write(f"[otra]({target}.md)\n")
    put(repo, "docs/keep.md", "# Peer\n")
    git(repo, "add", "docs/keep.md")
    publish(repo, "docs/keep.md")
    with (repo / "i18n/ja/docs/keep.md").open("a") as stream:
        stream.write("[guía](a.md)\n")
    (repo / "docs/a.md").unlink()
    (repo / "docs/b.md").unlink()
    assert lc.retire_obsolete() == (0, 2)
    lc.stage("i18n/ja/docs/keep.md", translated("docs/keep.md"))
    assert lc.activate_ready() == 1
    assert lc.retire_obsolete() == (2, 0)
    assert t.link_problems() == []


def test_unknown_output_is_never_deleted_or_overwritten(
    repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    put(repo, "i18n/ja/docs/mine.md", "# My notes\n")
    assert lc.retire_obsolete() == (0, 1)
    monkeypatch.setenv("LOOM_I18N_MAX_USD", "0")
    with pytest.raises(SystemExit, match="without generator ownership"):
        t.update()
    assert (repo / "i18n/ja/docs/mine.md").read_text() == "# My notes\n"


def test_rename_retires_the_old_page_after_surviving_links_are_repaired(repo: pathlib.Path) -> None:
    publish(repo, "docs/a.md")
    put(repo, "docs/b.md", "# Beta\n\n[guide](a.md)\n")
    publish(repo, "docs/b.md")
    git(repo, "mv", "docs/a.md", "docs/new.md")
    put(repo, "docs/b.md", "# Beta\n\n[guide](new.md)\n")
    assert lc.retire_obsolete() == (0, 1)
    for source in ("docs/new.md", "docs/b.md"):
        lc.stage(t.target_path("ja", source), translated(source))
    assert lc.activate_ready() == 2
    assert lc.retire_obsolete() == (1, 0)
    assert t.link_problems() == []


def pending_branch(repo: pathlib.Path, path: str, text: str) -> str:
    git(repo, "switch", "-qc", "i18n/update")
    put(repo, path, text)
    git(repo, "add", "i18n")
    git(repo, "commit", "-qm", "Generated")
    sha = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "-q", "main")
    return sha


def test_pending_branch_data_is_reused_and_fresh_main_wins(repo: pathlib.Path) -> None:
    text = translated("docs/a.md")
    sha = pending_branch(repo, "i18n/ja/docs/a.md", text)
    assert t.import_pending(sha) == 1
    assert (repo / "i18n/ja/docs/a.md").read_text() == text
    fresh = text + "\nTexto revisado.\n"
    put(repo, "i18n/ja/docs/a.md", fresh)
    assert t.import_pending(sha) == 0
    assert (repo / "i18n/ja/docs/a.md").read_text() == fresh


@pytest.mark.parametrize("change", ["stale", "html", "banner", "model", "code"])
def test_invalid_pending_output_is_rejected(repo: pathlib.Path, change: str) -> None:
    text = translated("docs/a.md")
    if change == "html":
        text += '<img src="x" onerror="alert(1)">\n'
    elif change == "banner":
        text = text.replace("Machine-translated", "Translated")
    elif change == "model":
        text = text.replace(t.DEFAULT_MODEL, "unknown-model")
    elif change == "code":
        text += "```sh\nunsafe-command\n```\n"
    sha = pending_branch(repo, "i18n/ja/docs/a.md", text)
    if change == "stale":
        put(repo, "docs/a.md", "# Alpha\n\nEdited.\n")
    assert t.import_pending(sha) == 0
    assert not (repo / "i18n/ja/docs/a.md").exists()


def test_symlink_source_or_output_is_refused(
    repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (repo / "docs/a.md").unlink()
    (repo / "docs/a.md").symlink_to("b.md")
    with pytest.raises(SystemExit, match="not a regular"):
        t.sources()
    (repo / "docs/a.md").unlink()
    put(repo, "i18n/ja/docs/a.md", "temporary")
    (repo / "i18n/ja/docs/a.md").unlink()
    (repo / "i18n/ja/docs/a.md").symlink_to(repo / "docs/b.md")
    monkeypatch.setenv("LOOM_I18N_MAX_USD", "0")
    with pytest.raises(SystemExit, match="nonregular"):
        t.update()
    assert (repo / "docs/b.md").read_text() == "# Beta\n"


def test_strict_reports_missing_stale_and_obsolete_while_async_validates_present_files(
    repo: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    publish(repo, "docs/a.md")
    publish(repo, "docs/b.md")
    put(repo, "docs/b.md", "# Beta\n\nEdited.\n")
    (repo / "docs/a.md").unlink()
    put(repo, "docs/new.md", "# New\n")
    git(repo, "add", "docs/new.md")
    assert t.check() == 1
    output = capsys.readouterr().out
    assert "i18n/ja/docs/a.md is outside" in output
    assert "i18n/ja/docs/new.md is missing or stale" in output
    assert "i18n/ja/docs/b.md is missing or stale" in output
    assert t.check(skip_stale=True) == 0
    with (repo / "i18n/ja/docs/a.md").open("a") as stream:
        stream.write("[rota](absent.md)\n")
    assert t.check(skip_stale=True) == 1


def test_reservation_books_all_verifier_and_api_attempts(repo: pathlib.Path) -> None:
    reservation = t.batch_reservation(codes=["ja"], cap=5)
    minimum_outputs = 2 * 2 * 4 * t.MAX_TOKENS * t.PRICES[t.DEFAULT_MODEL][1] / 1e6
    assert reservation > minimum_outputs
    assert t.batch_reservation(codes=["ja"], cap=0) == 0


def test_encoded_source_paths_are_resolved_and_encoded_once(repo: pathlib.Path) -> None:
    put(repo, "docs/space name.md", "# Beta\n")
    git(repo, "add", "docs/space name.md")
    text = "[guide](space%20name.md#beta)"
    fallback = t.rewrite_links(text, "docs/a.md", "ja")
    assert fallback == f"[guide]({t.REPO_URL}/blob/main/docs/space%20name.md#beta)"
    publish(repo, "docs/space name.md")
    localized = t.rewrite_links(text, "docs/a.md", "ja")
    assert localized == text
    assert lc.canonical_links(localized, "i18n/ja/docs/a.md") == fallback


def test_import_refuses_to_overwrite_unowned_published_data(repo: pathlib.Path) -> None:
    text = translated("docs/a.md")
    sha = pending_branch(repo, "i18n/ja/docs/a.md", text)
    put(repo, "i18n/ja/docs/a.md", "# My notes\n")
    with pytest.raises(SystemExit, match="without generator ownership"):
        t.import_pending(sha)
    assert (repo / "i18n/ja/docs/a.md").read_text() == "# My notes\n"
