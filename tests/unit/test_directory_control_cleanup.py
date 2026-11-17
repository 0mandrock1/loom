"""A directory control attempt owns acquisition, cleanup and context exit once."""

import asyncio
import contextlib
import time
from unittest.mock import AsyncMock, Mock

import pytest

from server.router import directory

pytestmark = pytest.mark.asyncio


@pytest.fixture
def owned(monkeypatch):
    conn = Mock()
    conn.transaction.return_value = Mock(start=AsyncMock(), commit=AsyncMock())
    conn.close = AsyncMock()
    conn.terminate = Mock()
    context = Mock(__aenter__=AsyncMock(return_value=conn), __aexit__=AsyncMock(return_value=None))
    monkeypatch.setattr(directory, "_connection", lambda: context)
    monkeypatch.setattr(directory, "CONTROL_MAX_S", 0.08)
    return conn, context


async def test_success_closes_once_before_pool_exit(owned):
    conn, context = owned
    events = []
    conn.close.side_effect = lambda **_: events.append("close")
    context.__aexit__.side_effect = lambda *_: events.append("exit")
    assert await directory._control_transaction(AsyncMock(return_value=19)) == 19
    assert events == ["close", "exit"]
    conn.close.assert_awaited_once()
    conn.transaction.return_value.commit.assert_awaited_once()


async def test_original_error_survives_close_and_exit_failure(owned):
    conn, context = owned
    original = ValueError("work failure")
    conn.close.side_effect = RuntimeError("close failure")
    context.__aexit__.side_effect = LookupError("exit failure")
    with pytest.raises(ValueError) as caught:
        await directory._control_transaction(AsyncMock(side_effect=original))
    assert caught.value is original
    conn.close.assert_awaited_once()
    context.__aexit__.assert_awaited_once()
    conn.transaction.return_value.commit.assert_not_awaited()


async def test_close_failure_survives_exit_failure_after_acknowledged_commit(owned):
    conn, context = owned
    original = ValueError("close failure")
    conn.close.side_effect = original
    context.__aexit__.side_effect = RuntimeError("exit failure")
    with pytest.raises(ValueError) as caught:
        await directory._control_transaction(AsyncMock())
    assert caught.value is original
    conn.transaction.return_value.commit.assert_awaited_once()


async def test_exit_cannot_open_an_extra_cleanup_allowance(owned):
    conn, context = owned
    entered = asyncio.Event()

    async def stalled(*_):
        entered.set()
        await asyncio.Future()

    context.__aexit__.side_effect = stalled
    began = time.monotonic()
    with pytest.raises(directory.DirectoryUnavailable):
        await directory._control_transaction(AsyncMock())
    assert entered.is_set() and time.monotonic() - began < 0.2
    conn.close.assert_awaited_once()


async def test_acquisition_failure_does_not_close_an_unowned_proxy(owned):
    conn, context = owned
    original = ValueError("acquisition failure")
    context.__aenter__.side_effect = original
    with pytest.raises(ValueError) as caught:
        await directory._control_transaction(AsyncMock())
    assert caught.value is original
    conn.close.assert_not_awaited()
    conn.terminate.assert_not_called()
    context.__aexit__.assert_not_awaited()


async def test_no_known_proxy_cancellation_does_not_close_another_attempt(owned):
    conn, context = owned
    entered = asyncio.Event()

    async def acquire():
        entered.set()
        await asyncio.Future()

    context.__aenter__.side_effect = acquire
    task = asyncio.create_task(directory._control_transaction(AsyncMock()))
    await entered.wait()
    task.cancel("first acquisition cancellation")
    with pytest.raises(asyncio.CancelledError, match="first acquisition cancellation"):
        await task
    conn.close.assert_not_awaited()
    conn.terminate.assert_not_called()
    context.__aexit__.assert_not_awaited()
    context.__aenter__.side_effect = None
    assert await directory._control_transaction(AsyncMock(return_value=2)) == 2
    conn.close.assert_awaited_once()


async def test_late_acquired_proxy_is_discarded_without_running_callback(owned):
    conn, context = owned
    entered, allow = asyncio.Event(), asyncio.Event()

    async def acquire():
        entered.set()
        while not allow.is_set():
            with contextlib.suppress(asyncio.CancelledError):
                await allow.wait()
        return conn

    context.__aenter__.side_effect = acquire
    work = AsyncMock()
    task = asyncio.create_task(directory._control_transaction(work))
    await entered.wait()
    task.cancel("acquisition cancelled")
    with pytest.raises(asyncio.CancelledError, match="acquisition cancelled"):
        await task
    allow.set()
    for _ in range(20):
        await asyncio.sleep(0)
    work.assert_not_awaited()
    conn.transaction.assert_not_called()
    conn.terminate.assert_called()
    conn.close.assert_not_awaited()  # Expired original end cannot create a fresh close budget.
