"""Marked logical calls commit one captured event through their request tail."""

from __future__ import annotations

import asyncio
import copy
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from server.auth import tenancy
from server.core import request_tail, request_work
from server.core.diagnostics import private_work
from server.observability import cost_accumulator, events, ledger


@pytest.fixture
def world(monkeypatch):
    rows, queued, held = [], [], []

    async def insert(table, data, *, settings, required_columns):
        assert table == "loom.agent_events"
        assert required_columns == frozenset({"mirror_marked"})
        assert settings["async_insert"] == settings["wait_for_async_insert"] == 1
        rows.extend(copy.deepcopy(data))

    def hold(event):
        held.append(event)
        return False

    client = SimpleNamespace(insert=AsyncMock(side_effect=insert))
    emitter = SimpleNamespace(emit=queued.append)
    monkeypatch.setattr(tenancy, "attributable", lambda org: True)
    monkeypatch.setattr(events, "get_client", lambda: client)
    monkeypatch.setattr(events, "get_emitter", lambda: emitter)
    monkeypatch.setattr(events, "stamp_row", lambda row: row)
    monkeypatch.setattr(events, "for_this_cell", lambda event: event)
    monkeypatch.setattr(ledger, "get_emitter", lambda: emitter)
    monkeypatch.setattr(ledger, "_event_hold", hold)
    spend = SimpleNamespace(
        cost_usd=0.125,
        embed_tokens=13,
        llm_input_tokens=17,
        llm_output_tokens=19,
        llm_calls=2,
        dominant_model=lambda: "test-model",
    )
    monkeypatch.setattr(cost_accumulator, "current", lambda: spend)
    local_token = request_work._current.set(None)
    tail = request_tail._Tail()
    tail_token = request_tail._tail.set(tail)
    request_work.begin()
    try:
        yield SimpleNamespace(
            rows=rows, queued=queued, held=held, client=client, tail=tail, spend=spend
        )
    finally:
        request_tail._tail.reset(tail_token)
        request_work._current.reset(local_token)


def emit():
    ledger.emit_agent_event(
        op_name="knowledge.get",
        identity={
            "org": "org-a",
            "namespace": "team",
            "agent": "reader",
            "user_id": "user-a",
            "session_id": "session-a",
        },
        request_id="request-a",
        latency_ms=23,
        success=True,
        error_code="",
        raw_args={"mirror_marked": False},
        subject="subject-a",
        consumer="test-client",
        traffic_class="interactive",
    )


@pytest.mark.parametrize("private", [False, True])
@pytest.mark.parametrize("hold_enabled", [False, True])
async def test_marked_delivery_is_required_once_even_without_a_named_policy(
    monkeypatch, world, private, hold_enabled
):
    if not hold_enabled:
        monkeypatch.setattr(ledger, "_event_hold", None)
    assert request_work.mark_mirrored()
    with private_work() if private else nullcontext():
        emit()
        emit()
    assert world.tail.has_required
    assert world.rows == world.queued == world.held == []
    request_work.begin()
    world.spend.cost_usd = 9.0
    await request_tail.drain_required()
    await request_tail.drain_required()
    await request_tail.settle(world.tail)
    assert len(world.rows) == 1
    assert world.client.insert.await_count == 1
    assert world.queued == world.held == []
    row = world.rows[0]
    assert "ts" not in row and row["mirror_marked"] == 1
    assert {name: row[name] for name in ("org", "namespace", "agent", "user_id", "session_id")} == {
        "org": "org-a",
        "namespace": "team",
        "agent": "reader",
        "user_id": "user-a",
        "session_id": "session-a",
    }
    assert row["subject"] == "subject-a" and row["request_id"] == "request-a"
    assert row["operation"] == "knowledge.get" and row["latency_ms"] == 23
    assert row["success"] is True and row["upstream_cost_usd"] == 0.125
    assert (
        row["embedding_tokens"],
        row["input_tokens"],
        row["output_tokens"],
        row["llm_calls"],
    ) == (13, 17, 19, 2)
    assert row["model"] == "test-model" and "mirror_marked" not in row["args"]


async def test_required_insert_failure_stays_fatal_without_queue_fallback(world):
    world.client.insert.side_effect = RuntimeError("private backend detail")
    assert request_work.mark_mirrored()
    emit()
    assert world.tail.has_required
    with pytest.raises(request_tail.RequiredTailError) as failed:
        await request_tail.drain_required()
    assert "private backend detail" not in str(failed.value)
    with pytest.raises(request_tail.RequiredTailError):
        await request_tail.settle(world.tail)
    assert world.client.insert.await_count == 1
    assert world.rows == world.queued == world.held == []


@pytest.mark.parametrize("closed", [False, True])
async def test_unavailable_tail_cannot_queue_a_marked_request(world, closed):
    if closed:
        await request_tail.settle(world.tail)
    else:
        request_tail._tail.set(None)
    assert request_work.mark_mirrored()
    assert request_work.mirror_marked()
    with pytest.raises(request_tail.RequiredTailError):
        emit()
    assert world.client.insert.await_count == 0
    assert world.rows == world.queued == world.held == []


async def test_waiter_cancellation_drains_the_one_registered_insert(world):
    started, release = asyncio.Event(), asyncio.Event()
    original = world.client.insert.side_effect

    async def paused(*args, **kwargs):
        started.set()
        await release.wait()
        await original(*args, **kwargs)

    world.client.insert.side_effect = paused
    assert request_work.mark_mirrored()
    emit()
    assert world.tail.has_required
    waiter = asyncio.create_task(request_tail.drain_required())
    try:
        await asyncio.wait_for(started.wait(), 1)
        waiter.cancel()
        await asyncio.sleep(0)
        waiter.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        await request_tail.settle(world.tail)
        assert len(world.rows) == 1 and world.client.insert.await_count == 1
        assert world.queued == world.held == []
    finally:
        release.set()
        if not waiter.done():
            waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)


def test_ordinary_event_still_uses_the_existing_queue(world):
    emit()
    assert not world.tail.has_required and world.client.insert.await_count == 0
    assert len(world.queued) == len(world.held) == 1
    assert world.queued[0].mirror_marked is False
    assert world.rows == []
