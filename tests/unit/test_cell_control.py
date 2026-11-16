"""A control owner cannot survive an uncertain commit or a lost lock session."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from server.cli import cell_control
from tests.unit.test_cell_attach import FakeConn


@pytest.mark.asyncio
async def test_uncertain_commit_closes_before_cancelling_work(monkeypatch):
    class Stalled(FakeConn):
        async def execute(self, sql: str, *args: Any) -> str:
            answer = await super().execute(sql, *args)
            if sql == "COMMIT":
                try:
                    await asyncio.Future()
                finally:
                    self.executed.append("WORK ENDED")
            return answer

    conn = Stalled()
    owner = cell_control.Owner()
    owner.conn = conn
    monkeypatch.setattr(cell_control, "TRANSACTION_S", 0.04)
    with pytest.raises(TimeoutError):
        await owner.transaction(AsyncMock())
    assert conn.executed.index("CLOSE") < conn.executed.index("WORK ENDED")
    assert "TERMINATE" not in conn.executed and "ROLLBACK" not in conn.executed
    assert any("pg_logical_emit_message" in sql for sql in conn.executed)


@pytest.mark.asyncio
async def test_failure_never_starts_rollback_or_replaces_original():
    conn = FakeConn()
    owner = cell_control.Owner()
    owner.conn = conn
    original = ValueError("original failure")
    with pytest.raises(ValueError) as found:
        await owner.transaction(AsyncMock(side_effect=original))
    assert found.value is original
    assert conn.executed[-1] == "CLOSE"
    assert "ROLLBACK" not in conn.executed and "TERMINATE" not in conn.executed


@pytest.mark.asyncio
async def test_cancellation_during_body_closes_before_work_cancel():
    conn = FakeConn()
    owner = cell_control.Owner()
    owner.conn = conn
    entered = asyncio.Event()

    async def body():
        entered.set()
        await asyncio.Future()

    task = asyncio.create_task(owner.transaction(body))
    await entered.wait()
    task.cancel("original cancellation")
    with pytest.raises(asyncio.CancelledError, match="original cancellation"):
        await task
    assert conn.executed[-1] == "CLOSE" and "ROLLBACK" not in conn.executed


def snapshot():
    return {
        "cell": "first",
        "is_first": True,
        "host": "first.example",
        "deployment_id": "dep",
        "bucket": "",
        "bucket_prefix": "",
        "probe_url": "",
        "upstream_url": "",
        "current_fp": None,
        "next_fp": None,
        "mutation_generation": str(UUID(int=1)),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("after_claim", [False, True])
async def test_lost_lock_never_reconnects_or_continues_claim(monkeypatch, after_claim):
    lock = AsyncMock(
        side_effect=[None, cell_control._refused("lost")]
        if after_claim
        else cell_control._refused("lost")
    )
    monkeypatch.setattr(cell_control, "check_lock", lock)
    monkeypatch.setattr(cell_control, "paired", AsyncMock())
    call = AsyncMock(return_value={**snapshot(), "mutation_generation": str(UUID(int=2))})
    with pytest.raises(cell_control.Refused, match="lost"):
        await cell_control.claim(object(), call, snapshot())
    assert call.await_count == int(after_claim)


@pytest.mark.asyncio
async def test_unknown_claim_acknowledgement_never_reuses_the_observed_generation(monkeypatch):
    monkeypatch.setattr(cell_control, "check_lock", AsyncMock())
    monkeypatch.setattr(cell_control, "paired", AsyncMock())
    call = AsyncMock(side_effect=OSError("reply lost"))
    with pytest.raises(OSError, match="reply lost"):
        await cell_control.claim(object(), call, snapshot())
    assert call.await_count == 1
    assert "mutation_generation" not in call.call_args.args[1]["expected"]


def test_older_router_snapshot_cannot_make_a_control_request():
    row = snapshot()
    row.pop("mutation_generation")
    with pytest.raises(cell_control.Refused, match="not upgraded"):
        cell_control.preconditions(row)


@pytest.mark.asyncio
async def test_whole_router_request_is_bounded_even_without_a_transport_reply(monkeypatch):
    import httpx

    async def stalled(_request):
        await asyncio.Future()

    monkeypatch.setattr(cell_control, "REQUEST_S", 0.01)
    with pytest.raises(cell_control.Refused, match="reply unknown.*TimeoutError"):
        await cell_control.request(
            httpx.URL("http://127.0.0.1:1"),
            "first",
            httpx.MockTransport(stalled),
            "/claim",
            {},
        )
