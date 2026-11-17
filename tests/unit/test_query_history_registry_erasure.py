"""Registry sends and namespace erasure cannot cross an unacknowledged write."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from server.core import leases
from server.core.errors import BackendUnavailableError, ValidationError
from server.erasure.store import steps_ledgers
from server.knowledge.api.models_query_history import QueryHistoryAddInput, QueryHistoryRemoveInput
from server.knowledge.app import query_history_sources as sources
from tests.unit.definition_queue_fake import install


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> Any:
    state = SimpleNamespace(
        rows=[], pending=None, fence=install(monkeypatch), failure=None, version=0, clock=1000
    )

    async def find(*args: Any) -> Any:
        return dict(state.rows[-1]) if state.rows else None

    async def insert(row: Any) -> None:
        state.pending = dict(row)
        if state.failure:
            raise state.failure
        state.rows.append(dict(row))

    async def version(*args: Any) -> int:
        state.version += 1
        return state.version

    async def delete(*args: Any, **kw: Any) -> int:
        assert args[0] == "loom.query_history_sources"
        count = len(state.rows)
        state.rows.clear()
        return count

    async def probe(*args: Any) -> None:
        assert state.fence.holders == {}  # remote I/O does not hold the org writer fence

    @asynccontextmanager
    async def claim(*args: Any, **kw: Any) -> Any:
        yield True

    monkeypatch.setattr(leases, "holding", claim)
    monkeypatch.setattr(sources.settings, "knowledge_query_history_enabled", True)
    monkeypatch.setattr(sources, "require_draft_fleet", AsyncMock())
    monkeypatch.setattr(sources, "probe", probe)
    monkeypatch.setattr(sources, "seal", lambda *args: "sealed-password")
    monkeypatch.setattr(sources, "writer_actor", lambda *args: "user:admin")
    monkeypatch.setattr(sources.time, "time", lambda: state.clock)
    monkeypatch.setattr(sources.repo, "find", find)
    monkeypatch.setattr(sources.repo, "list_sources", AsyncMock(return_value=[]))
    monkeypatch.setattr(sources.repo, "next_source_version", version)
    monkeypatch.setattr(sources.repo, "insert", insert)
    monkeypatch.setattr(steps_ledgers, "delete_scoped", delete)
    return state


def registration() -> QueryHistoryAddInput:
    return QueryHistoryAddInput(
        org="o",
        namespace="n",
        name="sales",
        host="db.example",
        user="reader",
        password="secret",
        databases=["sales"],
    )


async def change(registry: Any, kind: str) -> Any:
    if kind == "add":
        return await sources.handle_add(registration())
    if not registry.rows:
        registry.rows.append(
            {
                **registration().model_dump(exclude={"password"}),
                "source_id": "source",
                "created_at": datetime(2026, 1, 1),
                "password_enc": "sealed-password",
                "tombstoned_at": None,
                "version": 1,
            }
        )
    return await sources.handle_remove(
        QueryHistoryRemoveInput(org="o", namespace="n", name="sales")
    )


async def erase(registry: Any) -> int:
    registry.fence.erasures = [{"scope_type": "namespace_list", "scope_value": '["n", "other"]'}]
    return await steps_ledgers.delete_query_history_by_namespace("o", ["n", "other"])


@pytest.mark.parametrize("kind", ["add", "remove"])
@pytest.mark.parametrize("failure", [TimeoutError, asyncio.CancelledError])
async def test_uncertain_registry_send_excludes_namespace_list_erasure(
    registry: Any, kind: str, failure: type[BaseException]
) -> None:
    registry.failure = failure()
    with pytest.raises(failure):
        await change(registry, kind)
    with pytest.raises(BackendUnavailableError):
        await erase(registry)
    registry.rows.append(registry.pending)  # the server admits the already-sent row
    registry.fence.holders.clear()  # its server deadline has elapsed before takeover
    await erase(registry)
    assert registry.rows == []


@pytest.mark.parametrize("kind", ["add", "remove"])
async def test_registry_read_before_completed_erasure_cannot_restore_metadata(
    registry: Any, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    version = sources.repo.next_source_version
    erased = False

    async def stale(*args: Any) -> int:
        nonlocal erased
        result = await version(*args)
        if not erased:
            erased = True
            await erase(registry)
        return result

    monkeypatch.setattr(sources.repo, "next_source_version", stale)
    with pytest.raises(ValidationError, match="eras"):
        await change(registry, kind)
    assert registry.rows == []


async def test_registration_start_precedes_its_first_await(
    registry: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fleet() -> None:
        registry.clock = 1200  # erasure at 1100 completes during the first fleet read

    async def erased(org: str, namespace: str, started: int, askers: Any) -> bool:
        return started <= 1100

    monkeypatch.setattr(sources, "require_draft_fleet", fleet)
    monkeypatch.setattr(sources, "erasure_since", erased)
    with pytest.raises(ValidationError, match="eras"):
        await change(registry, "add")
    assert registry.rows == []


async def test_registry_roundtrip_preserves_sealing_and_clears_credential_on_remove(
    registry: Any,
) -> None:
    added = await change(registry, "add")
    assert "password" not in added.model_dump() and "password_enc" not in added.model_dump()
    assert registry.rows[0]["password_enc"] == "sealed-password"
    assert registry.rows[0]["actor"] == "user:admin"
    result = await change(registry, "remove")
    assert result.removed and registry.rows[-1]["password_enc"] == ""
    assert registry.rows[-1]["tombstoned_at"] is not None
    assert registry.rows[-1]["version"] > registry.rows[0]["version"]


@pytest.mark.parametrize("kind", ["add", "remove"])
async def test_registry_eraser_cannot_finish_during_an_admitted_insert(
    registry: Any, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    entered, resume = asyncio.Event(), asyncio.Event()
    insert = sources.repo.insert

    async def paused(row: Any) -> None:
        entered.set()
        await resume.wait()
        await insert(row)

    monkeypatch.setattr(sources.repo, "insert", paused)
    writer = asyncio.create_task(change(registry, kind))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        with pytest.raises(BackendUnavailableError):
            await erase(registry)
    finally:
        resume.set()
        await writer
    await erase(registry)
    assert registry.rows == []


async def test_registry_ledger_failure_refuses_without_sending_or_retaining_a_write(
    registry: Any,
) -> None:
    registry.fence.failure = RuntimeError("ledger unavailable")
    with pytest.raises(ValidationError, match="eras"):
        await change(registry, "add")
    assert registry.pending is None and registry.rows == [] and registry.fence.holders == {}


async def test_remove_missing_source_stays_a_noop(registry: Any) -> None:
    result = await sources.handle_remove(
        QueryHistoryRemoveInput(org="o", namespace="n", name="absent")
    )
    assert not result.removed and registry.pending is None and registry.fence.reads == []


async def test_registered_source_still_requires_explicit_removal(registry: Any) -> None:
    await change(registry, "add")
    original = [dict(row) for row in registry.rows]
    with pytest.raises(ValidationError, match="already|registered"):
        await change(registry, "add")
    assert registry.rows == original


async def test_removal_start_precedes_its_first_claim_await(
    registry: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    @asynccontextmanager
    async def claim(*args: Any, **kw: Any) -> Any:
        registry.clock = 1200
        yield True

    async def erased(org: str, namespace: str, started: int, askers: Any) -> bool:
        return started <= 1100

    monkeypatch.setattr(leases, "holding", claim)
    monkeypatch.setattr(sources, "erasure_since", erased)
    with pytest.raises(ValidationError, match="eras"):
        await change(registry, "remove")
    assert registry.pending is None
