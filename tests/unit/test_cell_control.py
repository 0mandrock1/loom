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
@pytest.mark.parametrize("stall", ["COMMIT", "ROLLBACK"])
async def test_uncertain_transaction_ends_owned_session_without_unbounded_cleanup(
    monkeypatch, stall
):
    class Stalled(FakeConn):
        async def execute(self, sql: str, *args: Any) -> str:
            answer = await super().execute(sql, *args)
            if sql == stall:
                await asyncio.Future()
            return answer

    conn = Stalled()
    monkeypatch.setattr(cell_control, "TRANSACTION_S", 0.01)
    with pytest.raises(TimeoutError):
        async with cell_control.transaction(conn):
            if stall == "ROLLBACK":
                raise ValueError("refuse")
    assert conn.executed[-1] == "TERMINATE"
    assert stall in conn.executed
    assert any("pg_logical_emit_message" in sql for sql in conn.executed)


@pytest.mark.asyncio
async def test_cancellation_during_body_never_waits_for_rollback():
    conn = FakeConn()
    entered = asyncio.Event()

    async def work():
        async with cell_control.transaction(conn):
            entered.set()
            await asyncio.Future()

    task = asyncio.create_task(work())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert conn.executed[-1] == "TERMINATE"
    assert "ROLLBACK" not in conn.executed


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
