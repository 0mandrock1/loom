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
    assert (
        await directory._control_transaction(
            AsyncMock(return_value=19), seconds=directory.CONTROL_MAX_S
        )
        == 19
    )
    assert events == ["close", "exit"]
    conn.close.assert_awaited_once()
    conn.transaction.return_value.commit.assert_awaited_once()


async def test_original_error_survives_close_and_exit_failure(owned):
    conn, context = owned
    original = ValueError("work failure")
    conn.close.side_effect = RuntimeError("close failure")
    context.__aexit__.side_effect = LookupError("exit failure")
    with pytest.raises(ValueError) as caught:
        await directory._control_transaction(
            AsyncMock(side_effect=original), seconds=directory.CONTROL_MAX_S
        )
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
        await directory._control_transaction(AsyncMock(), seconds=directory.CONTROL_MAX_S)
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
        await directory._control_transaction(AsyncMock(), seconds=directory.CONTROL_MAX_S)
    assert entered.is_set() and time.monotonic() - began < 0.2
    conn.close.assert_awaited_once()


async def test_acquisition_failure_does_not_close_an_unowned_proxy(owned):
    conn, context = owned
    original = ValueError("acquisition failure")
    context.__aenter__.side_effect = original
    with pytest.raises(ValueError) as caught:
        await directory._control_transaction(AsyncMock(), seconds=directory.CONTROL_MAX_S)
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
    task = asyncio.create_task(
        directory._control_transaction(AsyncMock(), seconds=directory.CONTROL_MAX_S)
    )
    await entered.wait()
    task.cancel("first acquisition cancellation")
    with pytest.raises(asyncio.CancelledError, match="first acquisition cancellation"):
        await task
    conn.close.assert_not_awaited()
    conn.terminate.assert_not_called()
    context.__aexit__.assert_not_awaited()
    context.__aenter__.side_effect = None
    assert (
        await directory._control_transaction(
            AsyncMock(return_value=2), seconds=directory.CONTROL_MAX_S
        )
        == 2
    )
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
    task = asyncio.create_task(
        directory._control_transaction(work, seconds=directory.CONTROL_MAX_S)
    )
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


async def test_restore_uses_its_separate_runtime_budget(owned, monkeypatch):
    conn, _ = owned
    seen = []
    answer = {"page": "audited"}

    async def transaction(change, *, seconds):
        seen.append(seconds)
        return await change(conn)

    monkeypatch.setattr(directory, "_control_transaction", transaction)
    monkeypatch.setattr(directory, "_restore_page", AsyncMock(return_value=answer))
    monkeypatch.setattr(directory, "RESTORE_READ_MAX_S", 0.037)
    assert await directory.restore_read("first", "dep-first", None, None) == answer
    assert seen == [0.037]


@pytest.mark.parametrize(
    "ending", ["success", "failure", "self_cancel", "exit", "keyboard", "deadline"]
)
@pytest.mark.parametrize("locally_closed", [False, True])
async def test_outer_signals_wait_for_the_same_close_and_keep_first(
    owned, monkeypatch, ending, locally_closed
):
    conn, _ = owned
    owner = directory._ControlOwner(0.08 if ending == "deadline" else 10)
    conn.is_closed.return_value = locally_closed
    conn._lost = locally_closed
    owner.conn = conn
    end = owner.end
    started, allow = asyncio.Event(), asyncio.Event()
    first = asyncio.CancelledError("first outer signal")
    failure = ValueError("close failure")
    real_wait = asyncio.wait
    waits = 0
    close_cancelled = []
    calls = []
    began = time.monotonic()

    async def close(_conn, *, end):
        calls.append(_conn)
        assert _conn is conn and end == owner.end
        started.set()
        try:
            await allow.wait()
        except asyncio.CancelledError:
            close_cancelled.append(True)
            raise
        if ending == "failure":
            raise failure
        if ending == "self_cancel":
            raise asyncio.CancelledError("close child cancelled itself")
        if ending == "exit":
            raise SystemExit(17)
        if ending == "keyboard":
            raise KeyboardInterrupt("close child interrupted")

    async def interrupted_wait(tasks, **kwargs):
        nonlocal waits
        waits += 1
        if waits == 1:
            await started.wait()
            raise first
        return await real_wait(tasks, **kwargs)

    monkeypatch.setattr(directory.pg, "close_owned", close)
    monkeypatch.setattr(asyncio, "wait", interrupted_wait)
    task = asyncio.create_task(owner.close())
    await started.wait()
    for _ in range(3):
        await asyncio.sleep(0)
    assert not task.done(), "an outer signal cancelled independent close before its end"
    conn.terminate.assert_not_called()
    assert not close_cancelled and owner.end == end
    if ending != "deadline":
        allow.set()
    with pytest.raises(asyncio.CancelledError) as caught:
        await task
    assert caught.value is first and owner.end == end
    assert time.monotonic() - began < 0.2
    assert calls == [conn] and waits < 5
    if ending == "success":
        conn.terminate.assert_not_called()
    else:
        conn.terminate.assert_called_once_with()


@pytest.mark.parametrize("already_done", [False, True])
async def test_self_cancelled_close_is_terminal_before_a_long_end(owned, monkeypatch, already_done):
    conn, _ = owned
    owner = directory._ControlOwner(10)
    owner.conn = conn
    original = asyncio.CancelledError("close child self-cancelled")

    async def close(_conn, *, end):
        raise original

    monkeypatch.setattr(directory.pg, "close_owned", close)
    if already_done:
        owner.close_task = owner.start(lambda: close(conn, end=owner.end))
        await asyncio.sleep(0)
        assert owner.close_task.done()
    began = time.monotonic()
    with pytest.raises(asyncio.CancelledError) as caught:
        await owner.close()
    assert caught.value is original
    assert time.monotonic() - began < 0.2
    conn.terminate.assert_called_once_with()


@pytest.mark.parametrize("abort_kind", ["ordinary", "cancel", "exit", "keyboard"])
@pytest.mark.parametrize(
    "first_kind", ["ordinary", "cancel", "exit", "keyboard", "outer_cancel", "deadline"]
)
async def test_secondary_abort_never_replaces_first_or_skips_owned_task_settlement(
    owned, monkeypatch, abort_kind, first_kind
):
    conn, _ = owned
    owner = directory._ControlOwner(0.02 if first_kind == "deadline" else 10)
    owner.conn = conn
    errors = {
        "ordinary": ValueError("first close failure"),
        "cancel": asyncio.CancelledError("first close cancellation"),
        "exit": SystemExit(17),
        "keyboard": KeyboardInterrupt("first close interruption"),
        "outer_cancel": asyncio.CancelledError("first outer cancellation"),
    }
    first = errors.get(first_kind)
    secondary = {
        "ordinary": RuntimeError("secondary abort failure"),
        "cancel": asyncio.CancelledError("secondary abort cancellation"),
        "exit": SystemExit(19),
        "keyboard": KeyboardInterrupt("secondary abort interruption"),
    }[abort_kind]
    conn.terminate.side_effect = secondary
    began = asyncio.Event()
    cancelled = asyncio.Event()
    real_wait = asyncio.wait
    signals = 0

    async def close(_conn, *, end):
        began.set()
        if first_kind == "deadline":
            try:
                await asyncio.Future()
            finally:
                cancelled.set()
        raise ValueError("later close failure") if first_kind == "outer_cancel" else first

    async def interrupted_wait(tasks, **kwargs):
        nonlocal signals
        signals += 1
        if first_kind == "outer_cancel" and signals == 1:
            raise first
        return await real_wait(tasks, **kwargs)

    async def observed():
        try:
            await owner.close()
        except BaseException as error:  # noqa: BLE001 — test native failures must remain inert too.
            return error.fatal.error if isinstance(error, directory._ControlInterrupted) else error
        pytest.fail("failed required cleanup became success")

    monkeypatch.setattr(directory.pg, "close_owned", close)
    monkeypatch.setattr(asyncio, "wait", interrupted_wait)
    result = await observed()
    if first_kind == "deadline":
        assert isinstance(result, TimeoutError)
        await asyncio.wait_for(cancelled.wait(), 0.1)
        assert owner.close_task.cancelled()
    else:
        assert result is first
    assert began.is_set()
    conn.terminate.assert_called_once_with()
