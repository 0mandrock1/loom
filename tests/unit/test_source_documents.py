"""Exact document reads distinguish discovery errors from conditional admission failures."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from server.knowledge.app.source_access import Admitted

MIRROR = "acl-" + "1" * 24


@pytest.fixture
def world(monkeypatch):
    from server.knowledge.app import source_documents as app

    phases = []
    source = "11111111-1111-4111-8111-111111111111"
    row = SimpleNamespace(
        org="acme",
        namespace=MIRROR,
        source_uri="gdrive:file#native#" + source,
        version=10,
        revision=0,
    )
    plan = SimpleNamespace(
        org="acme",
        sources={(MIRROR, source): "team"},
        failed=False,
        unread=False,
        current=lambda _item: True,
    )

    async def discover(org, pairs):
        phases.append(("discover", org, pairs))
        return [{"namespace": ns, "source_uri": uri} for ns, uri in pairs]

    async def check(candidates, *, chunks=False):
        phases.append("check")
        assert not chunks
        return [Admitted("team", c, {"version": 10, "revision": 0}) for c in candidates]

    async def contents(*args):
        phases.append(("contents", args))
        return row

    plan.check = AsyncMock(side_effect=check)
    monkeypatch.setattr(app.pg, "enabled", lambda: False)
    monkeypatch.setattr(app.evidence, "metadata", AsyncMock(side_effect=discover))
    monkeypatch.setattr(app.store, "contents", AsyncMock(side_effect=contents))
    return app, plan, row, phases


async def test_document_text_is_read_only_at_admitted_metadata_identity(world):
    app, plan, row, phases = world
    result = await app.get(plan, "team", "gdrive:file#native")
    assert result.row is row and result.admitted.namespace == "team"
    assert phases == [
        ("discover", "acme", [(MIRROR, row.source_uri)]),
        "check",
        ("contents", ("acme", MIRROR, row.source_uri, 10, 0)),
        "check",
    ]


@pytest.mark.parametrize("state", ["failed", "unread", "other_namespace"])
async def test_unavailable_or_unselected_plan_reads_nothing(world, state):
    app, plan, _row, phases = world
    if state == "other_namespace":
        plan.sources = {key: "other" for key in plan.sources}
    else:
        setattr(plan, state, True)
    assert await app.get(plan, "team", "gdrive:file#native") is None
    assert phases == []


async def test_missing_or_denied_document_never_reads_text(world):
    app, plan, _row, _phases = world
    app.evidence.metadata.return_value = []
    app.evidence.metadata.side_effect = None
    assert await app.get(plan, "team", "gdrive:file#native") is None
    app.store.contents.assert_not_awaited()
    app.evidence.metadata.side_effect = lambda _org, pairs: [
        {"namespace": ns, "source_uri": uri} for ns, uri in pairs
    ]
    plan.check.side_effect = None
    plan.check.return_value = []
    assert await app.get(plan, "team", "gdrive:file#native") is None
    app.store.contents.assert_not_awaited()


@pytest.mark.parametrize(
    "field,value",
    [
        ("org", "other"),
        ("namespace", "other"),
        ("source_uri", "other"),
        ("version", 11),
        ("revision", 1),
    ],
)
async def test_unexpected_content_invalidates_every_mirror(world, field, value):
    app, plan, row, _phases = world
    setattr(row, field, value)
    assert await app.get(plan, "team", "gdrive:file#native") is None
    assert plan.failed and not plan.unread


@pytest.mark.parametrize("change", ["denied", "metadata_version", "revision", "expired"])
async def test_postfetch_permission_or_identity_loss_drops_fetched_text(world, change):
    app, plan, _row, _phases = world
    check = plan.check.side_effect
    count = 0

    async def changed(candidates, *, chunks=False):
        nonlocal count
        count += 1
        out = await check(candidates, chunks=chunks)
        if count == 2:
            if change == "denied":
                return []
            if change == "expired":
                plan.current = lambda _item: False
            else:
                out[0].document["version" if change == "metadata_version" else "revision"] += 1
        return out

    plan.check.side_effect = changed
    assert await app.get(plan, "team", "gdrive:file#native") is None
    assert not plan.unread


async def test_all_bounded_discovery_queries_run_after_an_earlier_failure(world):
    app, plan, _row, phases = world
    plan.sources = {(MIRROR, f"{i:08x}-1111-4111-8111-111111111111"): "team" for i in range(65)}
    calls = []

    async def broken(_org, pairs):
        calls.append(pairs)
        if len(calls) == 1:
            raise RuntimeError("private database detail")
        return []

    app.evidence.metadata.side_effect = broken
    assert await app.get(plan, "team", "gdrive:file#native") is None
    assert [len(batch) for batch in calls] == [64, 1]
    assert plan.failed and plan.unread
    assert phases == []
    plan.check.assert_not_awaited()
    app.store.contents.assert_not_awaited()


@pytest.mark.parametrize("stage", ["check", "contents"])
async def test_conditional_failure_is_atomic_without_unread(world, stage):
    app, plan, _row, _phases = world
    target = plan.check if stage == "check" else app.store.contents
    target.side_effect = RuntimeError("private failure")
    assert await app.get(plan, "team", "gdrive:file#native") is None
    assert plan.failed and not plan.unread


async def test_cancellation_propagates(world):
    import asyncio

    app, plan, _row, _phases = world
    app.store.contents.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await app.get(plan, "team", "gdrive:file#native")
    assert not plan.failed and not plan.unread


@pytest.mark.parametrize("first", ["missing", "denied", "changed"])
async def test_another_admitted_source_survives_a_missing_or_revoked_copy(world, first):
    app, plan, row, _phases = world
    from copy import copy

    second = "22222222-2222-4222-8222-222222222222"
    plan.sources[(MIRROR, second)] = "team"
    second_row = copy(row)
    second_row.source_uri = "gdrive:file#native#" + second
    app.store.contents.side_effect = [None if first == "missing" else row, second_row]
    original_check = plan.check.side_effect
    calls = 0

    async def check(candidates, *, chunks=False):
        nonlocal calls
        calls += 1
        out = await original_check(candidates, chunks=chunks)
        if calls == 2 and first == "denied":
            return []
        if calls == 2 and first == "changed":
            out[0].document["version"] += 1
        return out

    plan.check.side_effect = check
    result = await app.get(plan, "team", "gdrive:file#native")
    assert result.row is second_row
    assert not plan.failed and not plan.unread


async def test_pg_discovery_uses_current_heads_without_ch_metadata_fallback(world, monkeypatch):
    app, plan, row, _phases = world
    monkeypatch.setattr(app.pg, "enabled", lambda: True)
    head = SimpleNamespace(row={"namespace": MIRROR, "source_uri": row.source_uri})
    monkeypatch.setattr(
        app.evidence, "heads", AsyncMock(return_value={(MIRROR, row.source_uri): head})
    )
    result = await app.get(plan, "team", "gdrive:file#native")
    assert result.row is row
    app.evidence.metadata.assert_not_awaited()
