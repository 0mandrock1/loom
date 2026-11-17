"""Checked exact-document reads, kept apart from ordinary mining helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from server.core import pg, request_work
from server.core.ch import sequential_reads, set_query_tag
from server.core.diagnostics import private_work
from server.knowledge.app.source_access import Admitted, ReadPlan
from server.knowledge.store import source_access as evidence
from server.knowledge.store import source_documents as store
from server.knowledge.store.documents import DocumentRow


@dataclass(frozen=True)
class CheckedDocument:
    admitted: Admitted
    row: DocumentRow


async def _discover(plan: ReadPlan, pairs: list[tuple[str, str]]) -> list[dict[str, Any]]:
    out = []
    set_query_tag("knowledge.mirror.document_discovery")
    for start in range(0, len(pairs), 64):
        batch = pairs[start : start + 64]
        try:
            rows = (
                [head.row for head in (await evidence.heads(plan.org, batch)).values()]
                if pg.enabled()
                else await evidence.metadata(plan.org, batch)
            )
            wanted = set(batch)
            for row in rows:
                key = (row["namespace"], row["source_uri"])
                if key not in wanted:
                    raise ValueError("unexpected document discovery row")
                wanted.remove(key)
                out.append({"namespace": key[0], "source_uri": key[1]})
        except Exception:  # noqa: BLE001 — every selected discovery read must fail closed uniformly
            plan.failed = plan.unread = True
            request_work.mark_mirrored(unread=True)
    return out


def _same(row: DocumentRow, plan: ReadPlan, item: Admitted) -> bool:
    return (row.org, row.namespace, row.source_uri) == (
        plan.org,
        item.candidate["namespace"],
        item.candidate["source_uri"],
    ) and (row.version, row.revision) == (
        int(item.document["version"]),
        int(item.document["revision"]),
    )


async def get(plan: ReadPlan, namespace: str, source_uri: str) -> CheckedDocument | None:
    """Admit metadata before text, then retain only an unchanged, freshly admitted copy."""
    if plan.failed or plan.unread:
        return None
    pairs = sorted(
        (mirror, source_uri + "#" + source)
        for (mirror, source), original in plan.sources.items()
        if original == namespace
    )
    set_query_tag("knowledge.mirror.document")
    with private_work(), sequential_reads():
        candidates = await _discover(plan, pairs)
        if plan.failed or plan.unread:
            return None
        try:
            admitted = []
            for start in range(0, len(candidates), 64):
                admitted.extend(await plan.check(candidates[start : start + 64]))
            if plan.failed or plan.unread:
                return None
            for item in sorted(
                admitted, key=lambda i: (i.candidate["namespace"], i.candidate["source_uri"])
            ):
                row = await store.contents(
                    plan.org,
                    item.candidate["namespace"],
                    item.candidate["source_uri"],
                    int(item.document["version"]),
                    int(item.document["revision"]),
                )
                if row is None:
                    continue
                if not _same(row, plan, item):
                    raise ValueError("unexpected document content identity")
                refreshed = await plan.check([item.candidate])
                if plan.failed or plan.unread:
                    return None
                if refreshed and _same(row, plan, refreshed[0]) and plan.current(refreshed[0]):
                    return CheckedDocument(refreshed[0], row)
        except Exception:  # noqa: BLE001 — conditional content or permission failure invalidates all copies
            plan.failed = True
    return None
