"""Coverage line numbers need the identity of the source the index parsed."""

from __future__ import annotations

import pytest

from server.code.api.models_testing import CodeCoverageClaimInput
from server.code.app import coverage as app
from server.code.domain.index import coverage_claim as cc
from server.code.domain.index import coverage_runs as cr

ORG, NS = "acme", "code-sample"
SRC = "src/sample.cpp"
TEST = f"{cc.STATELESS}01234_sample.sql"
pytestmark = pytest.mark.usefixtures("scalar_code_repo")


class CoverageRepo:
    def __init__(self, *, indexed="current", measured="old", head="current", lines="[[10,12]]"):
        self.indexed = indexed
        self.measured = measured
        self.head = head
        self.lines = lines

    async def coverage_files_for(self, org, ns, paths):
        assert (org, ns) == (ORG, NS)
        return {p: {"commit_sha": self.measured, "lines": self.lines} for p in paths}

    async def coverage_rows_for(self, org, ns, paths):
        assert (org, ns) == (ORG, NS)
        return [
            {
                "src_path": SRC,
                "test_path": TEST,
                "confidence": 1.0,
                "evidence": cr.pair_evidence(self.measured, [(10, 12)], 3),
            }
        ]

    async def get_state(self, org, ns, key):
        assert (org, ns, key) == (ORG, NS, "git_head")
        return self.head

    async def file_rows_for_paths(self, org, ns, paths):
        assert (org, ns) == (ORG, NS)
        return {p: {"indexed_commit": self.indexed} for p in paths}

    async def test_map_detail(self, org, ns, paths, *, limit):
        return []


async def run_claim(monkeypatch, repo, span):
    async def control(*args):
        return True, "probe"

    async def resolve(*args):
        return [{"path": SRC, "start_line": span[0], "end_line": span[1]}], ""

    async def terms(*args):
        return {}

    async def empty(*args, **kwargs):
        return []

    monkeypatch.setattr(app, "CodeRepo", lambda: repo)
    monkeypatch.setattr(app, "require_code_intel", lambda: None)
    monkeypatch.setattr(app, "resolve_code_identity", lambda *args: (ORG, NS))
    monkeypatch.setattr(app, "schedule_code_trace", lambda **kwargs: None)
    monkeypatch.setattr(app, "positive_control", control)
    monkeypatch.setattr(app, "_resolve_subject", resolve)
    monkeypatch.setattr(app, "search_terms", terms)
    monkeypatch.setattr(app, "unit_tests_reaching", empty)
    return await app.handle_code_coverage_claim(
        CodeCoverageClaimInput(org=ORG, namespace=NS, symbol="sample")
    )


@pytest.mark.asyncio
async def test_changed_source_does_not_make_old_overlapping_lines_executed(monkeypatch):
    out = await run_claim(monkeypatch, CoverageRepo(), (10, 12))
    assert out.verdict == cc.UNKNOWN
    assert out.strongest_tier == cc.FILE_REFERENCE
    assert out.tests[0]["methods"] == ["coverage_history"]
    assert "unverified" in out.tests[0]["evidence"]
    assert out.measured["freshness"] == "unverified"
    assert not out.measured["measured"] and not out.measured["executed"]


@pytest.mark.asyncio
async def test_shifted_source_does_not_make_old_nonoverlapping_lines_uncovered(monkeypatch):
    out = await run_claim(monkeypatch, CoverageRepo(), (30, 32))
    assert out.verdict == cc.UNKNOWN
    assert out.tests[0]["test_path"] == TEST
    assert out.measured["hits"] == {}


@pytest.mark.asyncio
async def test_unchanged_file_keeps_verified_coverage_after_repository_moves(monkeypatch):
    repo = CoverageRepo(indexed="old", head="current")
    out = await run_claim(monkeypatch, repo, (10, 12))
    assert out.verdict == cc.COVERED_BY
    assert out.strongest_tier == cc.EXECUTED
    assert out.measured["freshness"] == "verified"
    assert out.measured["commit"] == "old"
    assert out.measured["executed"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "indexed,measured,head", [("", "old", "current"), ("old", "", "current"), ("old", "old", "")]
)
async def test_missing_identity_cannot_verify_coverage(monkeypatch, indexed, measured, head):
    repo = CoverageRepo(indexed=indexed, measured=measured, head=head)
    out = await run_claim(monkeypatch, repo, (10, 12))
    assert out.verdict == cc.UNKNOWN
    assert out.measured["freshness"] == "unverified"


@pytest.mark.asyncio
async def test_current_nonexecution_remains_a_measured_negative(monkeypatch):
    repo = CoverageRepo(indexed="current", measured="current")
    out = await run_claim(monkeypatch, repo, (30, 32))
    assert out.verdict == cc.UNCOVERED
    assert out.measured["measured"] and not out.measured["executed"]


@pytest.mark.asyncio
async def test_mixed_commits_are_reported_per_file_without_inventing_one_commit():
    class MixedRepo(CoverageRepo):
        async def coverage_files_for(self, org, ns, paths):
            return {
                SRC: {"commit_sha": "old", "lines": "[[10,12]]"},
                "src/other.cpp": {"commit_sha": "current", "lines": "[]"},
            }

    out = await app.measured_for(MixedRepo(), ORG, NS, [SRC, "src/other.cpp"], None)
    assert out["commits"] == ["current", "old"]
    assert out["commit"] == ""
    assert out["freshness"] == "partial"
    assert out["paths"] == ["src/other.cpp"]
    assert out["unverified_paths"] == [SRC]


@pytest.mark.asyncio
async def test_pair_uses_its_own_commit_even_when_file_measurement_is_current(monkeypatch):
    class OldPairRepo(CoverageRepo):
        async def coverage_rows_for(self, org, ns, paths):
            rows = await super().coverage_rows_for(org, ns, paths)
            rows[0]["evidence"] = cr.pair_evidence("different", [(10, 12)], 3)
            return rows

    repo = OldPairRepo(indexed="current", measured="current")
    out = await run_claim(monkeypatch, repo, (10, 12))
    assert out.strongest_tier == cc.FILE_REFERENCE
    assert out.verdict == cc.UNKNOWN
    assert out.measured["freshness"] == "verified"


@pytest.mark.asyncio
async def test_missing_source_file_does_not_match_a_measurement(monkeypatch):
    class MissingRepo(CoverageRepo):
        async def file_rows_for_paths(self, org, ns, paths):
            return {}

    out = await run_claim(monkeypatch, MissingRepo(), (10, 12))
    assert out.verdict == cc.UNKNOWN
    assert out.measured["files"][SRC]["indexed_commit"] == ""


@pytest.mark.asyncio
async def test_unmeasured_source_prevents_a_complete_negative():
    class MissingMeasurementRepo(CoverageRepo):
        async def coverage_files_for(self, org, ns, paths):
            return {SRC: {"commit_sha": "current", "lines": "[]"}}

    out = await app.measured_for(MissingMeasurementRepo(), ORG, NS, [SRC, "src/other.cpp"], None)
    assert out["freshness"] == "partial"
    assert out["unmeasured_paths"] == ["src/other.cpp"]
    assert not out["executed"]


@pytest.mark.asyncio
async def test_independent_strong_evidence_still_establishes_coverage(monkeypatch):
    class StrongRepo(CoverageRepo):
        async def test_map_detail(self, org, ns, paths, *, limit):
            return [{"test_path": TEST, "method": "pr_cooccurrence", "confidence": 0.8}]

    out = await run_claim(monkeypatch, StrongRepo(), (10, 12))
    assert out.verdict == cc.COVERED_BY
    assert out.strongest_tier == cc.PR_COOCCURRENCE
    assert out.measured["freshness"] == "unverified"
