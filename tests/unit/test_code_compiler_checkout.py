"""Compiler-tier working trees: created when missing, except an operator's scip-clang one."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from server.code.app.index.compiler_tier import resolve_checkout
from tests.unit.code_fixtures import repo_config


def _bare_repo(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    (source / "main.py").write_text("print('ok')\n")
    subprocess.run(["git", "-C", str(source), "add", "main.py"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "initial",
        ],
        check=True,
    )
    bare = tmp_path / "repo.git"
    subprocess.run(["git", "clone", "--bare", "-q", str(source), str(bare)], check=True)
    return bare


def test_registry_entry_initializes_checkout_for_its_bare_mirror(tmp_path: Path) -> None:
    bare = _bare_repo(tmp_path)
    checkout = tmp_path / "checkouts" / "code-example"
    cfg = repo_config(
        source="registry",
        git_dir=str(bare),
        scip_checkout_dir=str(checkout),
        classification="standard",
    )

    actual, actual_bare, missing = resolve_checkout(cfg, "python")

    assert missing is None
    assert actual == tmp_path / "checkouts" / "code-example.python"
    assert (actual / ".git").exists()
    assert actual_bare == bare.resolve()


def test_operator_entry_gets_a_checkout_per_tier_scip_clang_included(tmp_path: Path) -> None:
    """A new pod starts with no tree: the C++ tier gets one like every tier."""
    bare = _bare_repo(tmp_path)
    checkout = tmp_path / "operator-owned"
    cfg = repo_config(source="env", git_dir=str(bare), scip_checkout_dir=str(checkout))

    actual, _bare, missing = resolve_checkout(cfg, "clang")
    assert missing is None and actual == checkout and (checkout / ".git").exists()

    actual, _bare, missing = resolve_checkout(cfg, "python")
    assert missing is None
    assert actual == tmp_path / "operator-owned.python" and (actual / ".git").exists()


def test_a_checkout_that_cannot_be_created_is_reported_with_the_reason(tmp_path: Path) -> None:
    bare = _bare_repo(tmp_path)
    (tmp_path / "a-file").write_text("")
    cfg = repo_config(git_dir=str(bare), scip_checkout_dir=str(tmp_path / "a-file" / "checkout"))

    _actual, _bare, missing = resolve_checkout(cfg, "python")

    assert missing is not None and missing.startswith("checkout missing at ")
    assert "(mkdir failed)" in missing  # made before `git init` runs


@pytest.mark.parametrize("producer", ["python", "clang"])
def test_an_existing_empty_directory_becomes_a_checkout(tmp_path: Path, producer: str) -> None:
    bare = _bare_repo(tmp_path)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    cfg = repo_config(git_dir=str(bare), scip_checkout_dir=str(checkout), scip_producer=producer)

    actual, actual_bare, missing = resolve_checkout(cfg, producer)

    assert missing is None
    assert actual == checkout and actual_bare == bare.resolve()
    assert (checkout / ".git").is_dir()


@pytest.mark.parametrize("kind", ["file", "nonempty", "bare", "nested"])
def test_an_invalid_existing_checkout_is_refused_without_changing_it(
    tmp_path: Path, kind: str
) -> None:
    bare = _bare_repo(tmp_path)
    checkout = tmp_path / "checkout"
    if kind == "bare":
        checkout = bare
    elif kind == "file":
        checkout.write_text("keep this file\n")
    else:
        checkout.mkdir()
        (checkout / "keep.txt").write_text("keep this content\n")
        if kind == "nested":
            subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    cfg = repo_config(git_dir=str(bare), scip_checkout_dir=str(checkout), scip_producer="python")
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    _actual, _bare, missing = resolve_checkout(cfg, "python")

    assert missing is not None and "checkout" in missing
    after = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before


@pytest.mark.parametrize("linked", [False, True])
def test_git_checkout_metadata_must_stay_inside_the_checkout(tmp_path: Path, linked: bool) -> None:
    bare = _bare_repo(tmp_path)
    checkout = tmp_path / "checkout"
    if linked:
        subprocess.run(
            ["git", "--git-dir", str(bare), "worktree", "add", "--detach", str(checkout)],
            check=True,
            capture_output=True,
        )
    else:
        subprocess.run(["git", "clone", "-q", str(bare), str(checkout)], check=True)
    cfg = repo_config(git_dir=str(bare), scip_checkout_dir=str(checkout), scip_producer="python")
    before = (checkout / "main.py").read_bytes()
    metadata = (checkout / ".git").read_bytes() if linked else None

    actual, actual_bare, missing = resolve_checkout(cfg, "python")

    if linked:
        assert missing is not None and "standalone .git directory" in missing
        assert (checkout / ".git").read_bytes() == metadata
    else:
        assert missing is None and actual == checkout and actual_bare == bare.resolve()
    assert (checkout / "main.py").read_bytes() == before


@pytest.mark.parametrize("shared", [False, True], ids=["symlink", "commondir"])
def test_external_git_metadata_is_refused_without_changing_it(tmp_path: Path, shared: bool) -> None:
    bare = _bare_repo(tmp_path)
    checkout = tmp_path / "checkout"
    subprocess.run(["git", "clone", "-q", str(bare), str(checkout)], check=True)
    if shared:
        metadata = checkout / ".git"
        (metadata / "commondir").write_text(str(bare) + "\n")
    else:
        metadata = tmp_path / "external-metadata"
        (checkout / ".git").rename(metadata)
        (checkout / ".git").symlink_to(metadata, target_is_directory=True)
    cfg = repo_config(git_dir=str(bare), scip_checkout_dir=str(checkout), scip_producer="python")
    before = {p.relative_to(metadata): p.read_bytes() for p in metadata.rglob("*") if p.is_file()}

    _actual, _bare, missing = resolve_checkout(cfg, "python")

    assert missing is not None and "standalone .git directory" in missing
    assert (checkout / ".git").is_symlink() is not shared
    assert (checkout / "main.py").read_text() == "print('ok')\n"
    assert {
        p.relative_to(metadata): p.read_bytes() for p in metadata.rglob("*") if p.is_file()
    } == before


@pytest.mark.parametrize("existing", [False, True])
def test_checkout_git_commands_have_a_timeout_and_report_expiry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: bool
) -> None:
    from server.settings import settings

    bare = _bare_repo(tmp_path)
    checkout = tmp_path / "checkout"
    if existing:
        subprocess.run(["git", "clone", "-q", str(bare), str(checkout)], check=True)
    cfg = repo_config(git_dir=str(bare), scip_checkout_dir=str(checkout), scip_producer="python")
    seen: list[float] = []

    def timed_out(argv, **kwargs):
        seen.append(kwargs["timeout"])
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr("server.code.app.index.compiler_tier.subprocess.run", timed_out)

    _actual, _bare, missing = resolve_checkout(cfg, "python")

    assert missing is not None and "timed out" in missing
    assert seen == [settings.code_git_timeout_s]


@pytest.mark.asyncio
async def test_no_git_command_in_a_checkout_starts_a_repack(tmp_path: Path, monkeypatch) -> None:
    """Auto-gc inside a fetch writes a pack as big as the repository; every git
    command a tier runs turns it off, in trees made by any build."""
    from server.code.app.index import compiler_tier
    from server.code.app.index.sandbox import JobResult

    seen: list[list[str]] = []

    async def _job(argv, **kw):
        seen.append(argv)
        return JobResult(0, b"", b"")

    monkeypatch.setattr(compiler_tier, "run_job", _job)
    await compiler_tier.git(tmp_path, "fetch", "origin")
    assert seen[0][3:7] == ["-c", "gc.auto=0", "-c", "maintenance.auto=false"]
