"""Source identity checks before applying measured line numbers to indexed code."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from server.code.domain.index import coverage_claim as cc
from server.code.domain.index import coverage_runs as cr
from server.code.store.index import CodeRepo


async def source_identities(
    repo: CodeRepo, org: str, ns: str, paths: list[str]
) -> tuple[str, dict[str, dict[str, Any]]]:
    head, files = await asyncio.gather(
        repo.get_state(org, ns, "git_head"), repo.file_rows_for_paths(org, ns, paths)
    )
    return head, files


def verified_source(path: str, commit: str, head: str, files: dict[str, dict[str, Any]]) -> bool:
    """An unchanged file keeps its parsing commit when the repository advances."""
    indexed = str(files.get(path, {}).get("indexed_commit") or "")
    return bool(head and commit and indexed == commit)


def evidence_commit(evidence: str) -> str:
    try:
        data = json.loads(evidence or "{}")
    except ValueError:
        return ""
    return str(data.get("commit") or "") if isinstance(data, dict) else ""


async def partition_coverage_rows(
    repo: CodeRepo, org: str, ns: str, rows: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep unverified file associations without interpreting their line numbers."""
    if not rows:
        return [], []
    paths = list(dict.fromkeys(str(r.get("src_path") or "") for r in rows))
    head, files = await source_identities(repo, org, ns, paths)
    current, historical = [], []
    for row in rows:
        path = str(row.get("src_path") or "")
        commit = evidence_commit(str(row.get("evidence") or ""))
        if verified_source(path, commit, head, files):
            current.append(row)
        else:
            historical.append(
                {
                    **row,
                    "method": "coverage_history",
                    "evidence": f"historical coverage of {path}; freshness unverified "
                    f"(measurement commit {commit or 'unknown'}, indexed commit "
                    f"{files.get(path, {}).get('indexed_commit') or 'unknown'})",
                }
            )
    return current, historical


async def measured_for(
    repo: CodeRepo,
    org: str,
    ns: str,
    src_paths: list[str],
    spans: dict[str, list[tuple[int, int]]] | None,
) -> dict[str, Any]:
    """Only verified sources support current execution or nonexecution claims."""
    files = await repo.coverage_files_for(org, ns, src_paths) if src_paths else {}
    if not files:
        return {}
    head, indexed = await source_identities(repo, org, ns, src_paths)
    details = {
        p: {
            "commit": str(f.get("commit_sha") or ""),
            "indexed_commit": str(indexed.get(p, {}).get("indexed_commit") or ""),
            "freshness": "verified"
            if verified_source(p, str(f.get("commit_sha") or ""), head, indexed)
            else "unverified",
        }
        for p, f in files.items()
    }
    ranges = {
        p: cr.json_ranges(str(f.get("lines") or "[]"))
        for p, f in files.items()
        if details[p]["freshness"] == "verified"
    }
    whole = {p: [(1, 10**9)] for p in src_paths}
    out = cc.measured_execution(spans if spans else whole, ranges)
    commits = sorted({d["commit"] for d in details.values()})
    out.update(
        commit=commits[0] if len(commits) == 1 else "",
        commits=commits,
        index_commit=head,
        files=details,
        freshness="verified"
        if len(ranges) == len(set(src_paths))
        else ("partial" if ranges else "unverified"),
        unverified_paths=[p for p, d in details.items() if d["freshness"] == "unverified"],
        unmeasured_paths=[p for p in src_paths if p not in files],
    )
    return out
