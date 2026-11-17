"""Translation automation admits generated data and books allowance before provider calls."""

from __future__ import annotations

import json
import os
import subprocess
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.i18n import automation as a


@pytest.fixture
def repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(a.t, "ROOT", tmp_path)
    a.git("init", "-q", "-b", "main")
    a.configure_git()
    (tmp_path / ".github/workflows").mkdir(parents=True)
    (tmp_path / ".github/workflows/ci.yml").write_text("name: trusted\n")
    (tmp_path / "README.md").write_text("# English\n")
    a.git("add", ".")
    a.git("commit", "-qm", "English source")
    return tmp_path


def commit_all() -> str:
    a.git("add", ".")
    a.git("commit", "-qm", "Generated data")
    return a.git("rev-parse", "HEAD")


def output(repository: Path, source: str = "README.md", code: str = "ja") -> Path:
    lang = next(lang for lang in a.t.LANGUAGES if lang.code == code)
    path = repository / a.t.target_path(lang.code, source)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(a.t.header(source, lang, a.t.DEFAULT_MODEL, a.t.blob(source)) + "# 日本語\n")
    return path


def test_publisher_accepts_generated_data_and_refuses_workflow_changes(repository: Path) -> None:
    base = a.git("rev-parse", "HEAD")
    output(repository)
    a.validate_changes(base, commit_all())
    (repository / ".github/workflows/ci.yml").write_text("name: hostile\n")
    with pytest.raises(ValueError, match="refuses path"):
        a.validate_changes(base, commit_all())


def test_publisher_refuses_plain_files_and_symlinks(repository: Path) -> None:
    base = a.git("rev-parse", "HEAD")
    path = repository / "i18n/ja/private.md"
    path.parent.mkdir(parents=True)
    path.write_text("not generated\n")
    with pytest.raises(ValueError, match="refuses path"):
        a.validate_changes(base, commit_all())
    path.unlink()
    path.symlink_to("../../README.md")
    with pytest.raises(ValueError, match="nonregular"):
        a.validate_changes(base, commit_all())


def test_publisher_refuses_stale_generated_output(repository: Path) -> None:
    base = a.git("rev-parse", "HEAD")
    path = output(repository)
    path.write_text(path.read_text().replace(a.t.blob("README.md"), "0" * 40))
    with pytest.raises(ValueError, match="refuses path"):
        a.validate_changes(base, commit_all())


def test_publisher_refuses_deletion_of_live_generated_output(repository: Path) -> None:
    path = output(repository)
    base = commit_all()
    path.unlink()
    with pytest.raises(ValueError, match="refuses path"):
        a.validate_changes(base, commit_all())


def test_publisher_accepts_only_generated_readme_language_line(repository: Path) -> None:
    path = repository / "README.md"
    path.write_text("# English\n\n<!-- languages -->old<!-- /languages -->\n")
    base = commit_all()
    path.write_text("# English\n\n" + a.t.language_line("README.md") + "\n")
    a.validate_changes(base, commit_all())
    path.write_text(path.read_text().replace("# English", "# Other text"))
    with pytest.raises(ValueError, match="refuses path"):
        a.validate_changes(base, commit_all())


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "invalid"])
def test_budget_rejects_nonfinite_or_negative_allowance(value: str) -> None:
    with pytest.raises(ValueError, match="finite nonnegative"):
        a.money(value)


def test_default_budget_never_reads_or_changes_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(a, "fetch", lambda ref: pytest.fail("zero allowance must stay offline"))
    assert a.reserve(Decimal(0), False, None) == 0


def test_positive_budget_never_resets_missing_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(a, "fetch", lambda ref: None)
    with pytest.raises(ValueError, match="missing"):
        a.reserve(Decimal(100), False, None)


def test_budget_reservation_precedes_update_and_is_not_refunded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[Any] = []
    monkeypatch.setenv("LOOM_I18N_TOTAL_USD", "20")
    monkeypatch.setenv("LOOM_I18N_LANGUAGES", "ja")
    monkeypatch.setattr(a, "configure_git", lambda: None)
    monkeypatch.setattr(a, "fetch", lambda ref: "a" * 40)
    monkeypatch.setattr(a, "budget_total", lambda tip: Decimal(12))
    monkeypatch.setattr(a.t, "batch_reservation", lambda every, codes, cap: cap * 2)
    monkeypatch.setattr(a, "append_budget", lambda *args: events.append(("reserve", args)))

    def rejected(every: bool, codes: list[str] | None) -> int:
        assert a.os.environ["LOOM_I18N_MAX_USD"] == "2.5"
        events.append(("provider",))
        raise RuntimeError("uncertain request")

    monkeypatch.setattr(a.t, "update", rejected)
    with pytest.raises(RuntimeError, match="uncertain"):
        a.update(False, None)
    assert events == [("reserve", ("a" * 40, Decimal(12), Decimal("5.0"))), ("provider",)]


def test_failed_budget_push_prevents_provider_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOOM_I18N_TOTAL_USD", "20")
    monkeypatch.setenv("LOOM_I18N_LANGUAGES", "ja")
    monkeypatch.setattr(a, "configure_git", lambda: None)
    monkeypatch.setattr(a, "fetch", lambda ref: "a" * 40)
    monkeypatch.setattr(a, "budget_total", lambda tip: Decimal(0))
    monkeypatch.setattr(a.t, "batch_reservation", lambda *args, **kwargs: 1)
    monkeypatch.setattr(a.t, "update", lambda *args: pytest.fail("unreserved paid call"))

    def denied(*args: Any) -> None:
        raise RuntimeError("lease refused")

    monkeypatch.setattr(a, "append_budget", denied)
    with pytest.raises(RuntimeError, match="lease refused"):
        a.update(False, None)


def test_ledger_records_separate_monotonic_commits_and_rejects_rewrite(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pushes: list[tuple[str, ...]] = []
    real_git = a.git

    def no_network(*args: str, **kwargs: Any) -> str:
        if args[0] == "push":
            pushes.append(args)
            return ""
        return real_git(*args, **kwargs)

    monkeypatch.setattr(a, "git", no_network)
    first = a.append_budget(None, Decimal(0), Decimal(0))
    second = a.append_budget(first, Decimal(0), Decimal("2.125"))
    assert a.budget_total(second) == Decimal("2.125")
    assert pushes[1][-1] == f"--force-with-lease={a.BUDGET}:{first}"
    assert a.git("ls-tree", "--name-only", second) == "budget.json"
    bad = a.append_budget(second, Decimal(0), Decimal(1))
    with pytest.raises(ValueError, match="invalid translation budget"):
        a.budget_total(bad)


def test_publisher_dispatches_ci_without_merging_the_unchecked_generated_sha(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output(repository)
    monkeypatch.setattr(a.t, "check", lambda **kwargs: 0)
    monkeypatch.setattr(a, "fetch", lambda ref: None)
    real_git = a.git
    pushed: list[str] = []
    calls: list[tuple[str, ...]] = []

    def local_git(*args: str, **kwargs: Any) -> str:
        if args[0] == "push":
            pushed.append(args[2].split(":")[0])
            return ""
        return real_git(*args, **kwargs)

    monkeypatch.setattr(a, "git", local_git)
    real_command = a.command

    def gh(*args: str, **kwargs: Any) -> str:
        if args[0] != "gh":
            return real_command(*args, **kwargs)
        calls.append(args)
        if args[1:3] == ("pr", "create"):
            body = Path(args[args.index("--body-file") + 1]).read_text()
            assert "### Changelog category" in body
            assert "### Compatibility, rollout and rollback" in body
            assert "### Tests" in body
            assert "[ ] Hosted CI" in body
        return json.dumps([]) if args[1:3] == ("pr", "list") else ""

    monkeypatch.setattr(a, "command", gh)
    a.publish()
    assert len(pushed) == 1
    assert calls[-1][-1] == f"expected_sha={pushed[0]}"
    assert not any(call[1:3] == ("pr", "merge") for call in calls)


def test_hosted_ci_gates_every_job_and_keeps_original_dependencies() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.load((root / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader)
    jobs = workflow["jobs"]
    for name, job in jobs.items():
        if name != "validate-ref":
            needs = job["needs"]
            assert "validate-ref" in ([needs] if isinstance(needs, str) else needs), name
    assert "clickhouse" in jobs["unit"]["needs"]
    assert "unit" in jobs["unit-coverage"]["needs"]
    gate = jobs["validate-ref"]["steps"][0]["run"]
    assert 'test "$SHA" = "$EXPECTED_SHA"' in gate
    assert 'test "$REF" = "refs/heads/i18n/update"' in gate
    assert workflow["on"]["workflow_dispatch"]["inputs"]["expected_sha"]["required"] == "true"


def test_paid_runner_remains_main_only_with_finite_cumulative_default() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.load((root / ".github/workflows/i18n.yml").read_text(), Loader=yaml.BaseLoader)
    assert "pull_request" not in workflow["on"]
    update = workflow["jobs"]["update"]
    assert "github.ref == 'refs/heads/main'" in update["if"]
    assert update["environment"] == "i18n"
    assert "workflow_run" not in update["if"]
    assert workflow["jobs"]["finalize"]["runs-on"] == "ubuntu-latest"
    assert "environment" not in workflow["jobs"]["finalize"]
    steps = update["steps"]
    assert steps[0]["with"]["ref"] == "${{ github.sha }}"
    paid = next(step for step in steps if step.get("id") == "translate")
    assert paid["env"]["LOOM_I18N_TOTAL_USD"] == "${{ vars.LOOM_I18N_TOTAL_USD || '0' }}"


def test_verified_checkpoint_can_be_published_and_removed_on_activation(repository: Path) -> None:
    base = a.git("rev-parse", "HEAD")
    published = output(repository)
    path = repository / "i18n/.pending/ja/README.md"
    path.parent.mkdir(parents=True)
    path.write_text(published.read_text())
    a.validate_changes(base, commit_all())
    base = a.git("rev-parse", "HEAD")
    path.unlink()
    a.validate_changes(base, commit_all())


def test_publisher_refuses_to_overwrite_pending_updates_after_import(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output(repository)
    monkeypatch.setattr(a.t, "check", lambda **kwargs: 0)
    monkeypatch.setenv("LOOM_I18N_PENDING_SHA", "a" * 40)
    monkeypatch.setattr(a, "fetch", lambda ref: "b" * 40)
    with pytest.raises(ValueError, match="changed after import"):
        a.publish()


def test_prepare_reports_only_the_successfully_imported_pending_tip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_path = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_path))
    monkeypatch.setattr(a, "fetch", lambda ref: "a" * 40)
    imported: list[str] = []
    monkeypatch.setattr(a.t, "import_pending", lambda ref, codes=None: imported.append(ref))
    a.prepare()
    assert imported == ["a" * 40]
    assert output_path.read_text() == f"pending_sha={'a' * 40}\n"


def ci_run(sha: str) -> dict[str, Any]:
    return {
        "event": "workflow_dispatch",
        "path": ".github/workflows/ci.yml",
        "head_branch": "i18n/update",
        "head_sha": sha,
        "head_repository": {"full_name": "ClickHouse/loom"},
        "status": "completed",
        "conclusion": "success",
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("event", "pull_request"),
        ("head_branch", "attacker"),
        ("head_sha", "b" * 40),
        ("path", ".github/workflows/other.yml"),
        ("head_repository", {"full_name": "other/repo"}),
        ("status", "in_progress"),
        ("conclusion", "failure"),
    ],
)
def test_finalizer_rejects_unrelated_or_unsuccessful_ci(field: str, value: Any) -> None:
    run = ci_run("a" * 40)
    assert a.trusted_ci(run, "ClickHouse/loom", "a" * 40)
    run[field] = value
    assert not a.trusted_ci(run, "ClickHouse/loom", "a" * 40)


@pytest.mark.parametrize(
    "checks",
    [
        [],
        [{"state": "PENDING"}],
        [
            {"status": "COMPLETED", "conclusion": "SUCCESS"},
            {"status": "COMPLETED", "conclusion": "SKIPPED"},
        ],
    ],
)
def test_finalizer_requires_nonempty_successful_checks(checks: list[dict[str, Any]]) -> None:
    assert not a.checks_succeeded(checks)


@pytest.mark.parametrize(
    "review,merge_state,merged",
    [
        ("REVIEW_REQUIRED", "BLOCKED", False),
        ("CHANGES_REQUESTED", "BLOCKED", False),
        ("APPROVED", "CLEAN", True),
        ("", "CLEAN", True),
    ],
)
@pytest.mark.parametrize("site_raises", [False, True])
@pytest.mark.parametrize("published_code,approved", [("ja", "ja"), ("es", "ja"), ("es", "")])
def test_hosted_finalizer_respects_reviews_and_merges_only_exact_successful_head(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
    review: str,
    merge_state: str,
    merged: bool,
    site_raises: bool,
    published_code: str,
    approved: str,
) -> None:
    output(repository, code="ko")
    base = commit_all()
    output(repository, code=published_code)
    a.git("add", ".")
    sha = a.git("commit-tree", a.git("write-tree"), "-p", base, input_text="Generated\n")
    monkeypatch.setenv("GITHUB_REPOSITORY", "ClickHouse/loom")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    monkeypatch.setenv("LOOM_I18N_LANGUAGES", approved)
    monkeypatch.setattr(a, "fetch", lambda ref: sha)
    monkeypatch.setattr(a.t, "check", lambda **kwargs: 0)
    pr = {
        "number": 42,
        "draft": False,
        "head": {"sha": sha, "repo": {"full_name": "ClickHouse/loom"}},
    }

    def api(path: str) -> Any:
        if "state=closed" in path:
            return []
        if "state=open" in path:
            return [pr]
        if "actions/workflows/ci.yml/runs" in path:
            return {"workflow_runs": [ci_run(sha)]}
        assert path.endswith("/pulls/42")
        return {"merged": True, "head": {"sha": sha}}

    monkeypatch.setattr(a, "api", api)
    calls: list[tuple[str, ...]] = []
    actual = a.command

    def command(*args: str, **kwargs: Any) -> str:
        if args[0] == "mkdocs":
            calls.append(args)
            if site_raises:
                raise RuntimeError("strict site build failed")
            return ""
        if args[0] != "gh":
            return actual(*args, **kwargs)
        calls.append(args)
        if args[1:3] == ("pr", "view"):
            return json.dumps(
                {
                    "reviewDecision": review,
                    "mergeStateStatus": merge_state,
                    "statusCheckRollup": [{"status": "COMPLETED", "conclusion": "SUCCESS"}],
                }
            )
        return ""

    monkeypatch.setattr(a, "command", command)
    permitted = published_code in approved.split()
    if merged and permitted and site_raises:
        with pytest.raises(RuntimeError, match="strict site build failed"):
            a.finalize()
    else:
        a.finalize()
    merge_calls = [call for call in calls if call[1:3] == ("pr", "merge")]
    if merged and permitted and not site_raises:
        assert len(merge_calls) == 1
        assert calls.index(("mkdocs", "build", "--strict")) < calls.index(merge_calls[0])
        assert merge_calls[0][-2:] == ("--match-head-commit", sha)
        assert "--admin" not in merge_calls[0] and "--auto" not in merge_calls[0]
        assert calls[-1] == ("gh", "workflow", "run", "docs-site.yml", "--ref", "main")
    else:
        assert merge_calls == []
        assert not any(call[1:3] == ("workflow", "run") for call in calls)


def test_unchanged_pending_data_keeps_the_reviewed_commit(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = a.git("rev-parse", "HEAD")
    output(repository)
    a.git("add", ".")
    previous = a.git("commit-tree", a.git("write-tree"), "-p", base, input_text="Generated\n")
    monkeypatch.setattr(a, "fetch", lambda ref: previous)
    monkeypatch.setattr(a.t, "check", lambda **kwargs: 0)
    calls: list[tuple[str, ...]] = []
    actual = a.command

    def command(*args: str, **kwargs: Any) -> str:
        if args[:2] == ("git", "push"):
            pytest.fail("unchanged output must retain the reviewed branch head")
        if args[0] != "gh":
            return actual(*args, **kwargs)
        calls.append(args)
        return json.dumps([{"number": 42}]) if args[1:3] == ("pr", "list") else ""

    monkeypatch.setattr(a, "command", command)
    a.publish()
    assert calls[-1][-1] == f"expected_sha={previous}"


@pytest.mark.parametrize(
    "site_result,expected_dispatch", [(None, True), ("failure", True), ("success", False)]
)
def test_scheduled_reconciliation_retries_suppressed_or_failed_site_deployment(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
    site_result: str | None,
    expected_dispatch: bool,
) -> None:
    sha = a.git("rev-parse", "HEAD")
    runs = (
        []
        if site_result is None
        else [
            {
                "head_branch": "main",
                "head_sha": sha,
                "status": "completed",
                "conclusion": site_result,
            }
        ]
    )

    def api(path: str) -> Any:
        if "/pulls?" in path:
            return [{"merged_at": "one", "merge_commit_sha": sha}]
        return {"workflow_runs": runs}

    monkeypatch.setattr(a, "api", api)
    actual = a.command
    calls: list[tuple[str, ...]] = []

    def command(*args: str, **kwargs: Any) -> str:
        if args[0] == "gh":
            calls.append(args)
            return ""
        return actual(*args, **kwargs)

    monkeypatch.setattr(a, "command", command)
    a.sync_site("ClickHouse/loom")
    assert bool(calls) is expected_dispatch
    if expected_dispatch:
        assert calls == [("gh", "workflow", "run", "docs-site.yml", "--ref", "main")]


@pytest.mark.parametrize(
    "event,ref,expected,passed",
    [
        ("workflow_dispatch", "refs/heads/i18n/update", "a" * 40, True),
        ("workflow_dispatch", "refs/heads/main", "a" * 40, False),
        ("workflow_dispatch", "refs/heads/i18n/update", "b" * 40, False),
        ("workflow_dispatch", "refs/heads/i18n/update", "$(exit 0)", False),
        ("pull_request", "refs/pull/1/merge", "", True),
    ],
)
def test_ci_gate_executes_only_the_requested_exact_bot_commit(
    event: str,
    ref: str,
    expected: str,
    passed: bool,
) -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.load((root / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader)
    gate = workflow["jobs"]["validate-ref"]["steps"][0]["run"]
    result = subprocess.run(
        ["bash", "-e", "-c", gate],
        check=False,
        capture_output=True,
        env={**os.environ, "EVENT": event, "REF": ref, "SHA": "a" * 40, "EXPECTED_SHA": expected},
    )
    assert (result.returncode == 0) is passed


@pytest.mark.parametrize("source", ["docs/日本語.md", "docs/日本語 guide.md"])
def test_publisher_validates_unicode_and_space_paths_without_git_quote_parsing(
    repository: Path,
    source: str,
) -> None:
    a.git("config", "core.quotepath", "true")
    source_file = repository / source
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("# English\n")
    base = commit_all()
    path = output(repository, source)
    a.validate_changes(base, commit_all())
    # The regression must cover Git's default quoted-path output.
    assert "\\" in a.git("diff", "--name-status", base, "HEAD")
    path.write_text(path.read_text().replace(a.t.blob(source), "0" * 40))
    with pytest.raises(ValueError, match="refuses path"):
        a.validate_changes(base, commit_all())


def test_missing_language_approval_defers_without_reservation_or_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LOOM_I18N_LANGUAGES", raising=False)
    monkeypatch.setenv("LOOM_I18N_TOTAL_USD", "200")
    monkeypatch.setattr(a, "configure_git", lambda: None)
    monkeypatch.setattr(a, "reserve", lambda *args: pytest.fail("unapproved reservation"))
    calls: list[tuple[bool, list[str] | None, str]] = []
    monkeypatch.setattr(
        a.t,
        "update",
        lambda every, codes: calls.append((every, codes, a.os.environ["LOOM_I18N_MAX_USD"])) or 0,
    )
    a.update(True, None)
    assert calls == [(False, [], "0.0")]


def test_persistent_approval_selects_only_configured_languages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOOM_I18N_LANGUAGES", "ja")
    monkeypatch.setenv("LOOM_I18N_TOTAL_USD", "20")
    monkeypatch.setattr(a, "configure_git", lambda: None)
    reservations: list[tuple[Any, ...]] = []
    monkeypatch.setattr(a, "reserve", lambda *args: reservations.append(args) or 1.0)
    calls: list[list[str] | None] = []
    monkeypatch.setattr(a.t, "update", lambda every, codes: calls.append(codes) or 0)
    a.update(False, None)
    assert reservations == [(Decimal(20), False, ["ja"])]
    assert calls == [["ja"]]


@pytest.mark.parametrize("approved,requested", [("unknown", None), ("ja", ["es"]), ("", ["ja"])])
def test_unapproved_or_unknown_languages_refuse_before_budget_reservation(
    monkeypatch: pytest.MonkeyPatch,
    approved: str,
    requested: list[str] | None,
) -> None:
    monkeypatch.setenv("LOOM_I18N_LANGUAGES", approved)
    monkeypatch.setattr(a, "reserve", lambda *args: pytest.fail("unapproved reservation"))
    with pytest.raises(ValueError, match="language"):
        a.update(False, requested)


def test_finalize_retries_merged_site_while_next_translation_pr_waits_for_ci(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "ClickHouse/loom")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    sha = a.git("rev-parse", "HEAD")
    calls: list[tuple[str, ...]] = []

    def api(path: str) -> Any:
        if "/pulls?state=closed" in path:
            return [{"merged_at": "one", "merge_commit_sha": sha}]
        if "/pulls?state=open" in path:
            return [
                {"draft": False, "head": {"sha": sha, "repo": {"full_name": "ClickHouse/loom"}}}
            ]
        if "/actions/workflows/" in path:
            return {"workflow_runs": []}
        raise AssertionError(path)

    monkeypatch.setattr(a, "api", api)
    monkeypatch.setattr(a, "command", lambda *args: calls.append(args) or "")
    a.finalize()
    assert calls == [("gh", "workflow", "run", "docs-site.yml", "--ref", "main")]
