"""The attach owner preserves failures through bounded public-close cleanup."""

import argparse
import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from server.cli import cell_attach
from server.cli import cell_attach_pg as apg
from server.core import pg


@pytest.mark.asyncio
async def test_failed_work_keeps_its_error_when_public_close_also_fails():
    events = []

    class Conn:
        async def close(self, **kwargs):
            events.append("close")
            raise RuntimeError("close failed")

        def terminate(self):
            events.append("abort")

    async def work():
        raise ValueError("original work failure")

    with pytest.raises(ValueError, match="original work failure"):
        await apg.supervise(Conn(), work(), end=time.monotonic() + 0.1)
    assert events == ["close", "abort"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_second_cancellation_preserves_original_failure_and_closes_before_cancel(failed):
    entered, closing = asyncio.Event(), asyncio.Event()
    events = []

    class Conn:
        async def close(self, **kwargs):
            events.append("close")
            closing.set()
            await asyncio.Future()

        def terminate(self):
            events.append("abort")

    async def work():
        entered.set()
        try:
            if failed:
                raise ValueError("original work failure")
            await asyncio.Future()
        finally:
            events.append("work ended")

    task = asyncio.create_task(apg.supervise(Conn(), work(), end=time.monotonic() + 0.2))
    await entered.wait()
    if not failed:
        task.cancel("first cancellation")
    await closing.wait()
    task.cancel("second cancellation")
    with pytest.raises(
        ValueError if failed else asyncio.CancelledError,
        match="original work failure" if failed else "first cancellation",
    ):
        await task
    assert events.count("abort") >= 1
    if not failed:
        assert events.index("close") < events.index("work ended")


@pytest.mark.asyncio
async def test_lock_owner_bounds_context_exit_inside_the_same_cleanup_allowance(monkeypatch):
    conn = AsyncMock()
    conn.is_closed = lambda: False
    conn.terminate = lambda: None
    cm = AsyncMock()
    exit_started = asyncio.Event()

    async def exit(*_):
        exit_started.set()
        await asyncio.Future()

    cm.__aexit__.side_effect = exit
    run = cell_attach.Run(None, argparse.Namespace(cell="test", wait=0), None, lambda _: None)
    run._lock_cm, run.lock_conn = cm, conn
    monkeypatch.setattr(pg, "_RELEASE_TIMEOUT_S", 0.03)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        await run.close_lock()
    assert time.monotonic() - started < 0.15
    assert exit_started.is_set() and run.lock_conn is None
    conn.close.assert_awaited_once()
    await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_run_preserves_its_own_failure_without_hiding_normal_close_failure(failed):
    router = AsyncMock()
    router.call.return_value = {"state": "attaching"}
    run = cell_attach.Run(
        None, argparse.Namespace(cell="test", wait=0, dry_run=True), router, lambda _: None
    )
    run.open_lock = AsyncMock(side_effect=ValueError("run failed") if failed else None)
    run.read_own_row = AsyncMock()
    run.refusals = AsyncMock(return_value=(None, None, None))
    run.dry_run = AsyncMock(return_value=0)
    run.close_lock = AsyncMock(side_effect=RuntimeError("close failed"))
    try:
        raise LookupError("unrelated caller exception")
    except LookupError:
        with pytest.raises(
            ValueError if failed else RuntimeError, match="run failed" if failed else "close failed"
        ):
            await run.run()
