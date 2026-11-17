"""Placement reuses the control owner without replacing its phase budgets."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from server.cli import cell_control
from server.core import pg
from tests.unit.test_cell_attach import FakeConn

pytestmark = pytest.mark.asyncio


async def test_custom_exit_is_used_only_outside_a_local_phase():
    owner = cell_control.Owner(exit_s=0.09)
    conn = FakeConn()

    async def opening():
        owner.conn = conn

    before = time.monotonic()
    assert await owner.run(opening, AsyncMock(return_value=17), opening_s=0.3) == 17
    assert before + 0.09 <= owner.close_end <= time.monotonic() + 0.09
    assert conn.executed == ["CLOSE"]


@pytest.mark.parametrize("occupied", ["conn", "phase", "task", "closed", "repeated"])
async def test_command_entry_refuses_reuse_before_any_callback(occupied):
    owner = cell_control.Owner()
    held = None
    if occupied == "conn":
        owner.conn = FakeConn()
    elif occupied == "phase":
        owner.phase_end = time.monotonic() + 1
    elif occupied == "task":
        held = owner._start(lambda: asyncio.Future())
    elif occupied == "closed":
        await owner.close()
    else:
        await owner.run(AsyncMock(), AsyncMock(), opening_s=0.1)
    opening, body = AsyncMock(), AsyncMock()
    try:
        with pytest.raises(cell_control.Refused, match="fresh"):
            await owner.run(opening, body, opening_s=0.1)
        opening.assert_not_called()
        body.assert_not_called()
    finally:
        if held is not None:
            held.cancel()
            await asyncio.gather(held, return_exceptions=True)


@pytest.mark.parametrize("stage", ["opening", "body"])
async def test_recursive_entry_cannot_replace_the_original_backend(stage):
    owner = cell_control.Owner()
    original = FakeConn()
    other_open, other_body = AsyncMock(), AsyncMock()

    async def recurse():
        with pytest.raises(cell_control.Refused, match="fresh"):
            await owner.run(other_open, other_body, opening_s=0.1)

    async def opening():
        if stage == "opening":
            await recurse()
        owner.conn = original

    async def body():
        if stage == "body":
            await recurse()
        return 3

    assert await owner.run(opening, body, opening_s=0.1) == 3
    other_open.assert_not_called()
    other_body.assert_not_called()
    assert owner.conn is original and original.executed == ["CLOSE"]


async def test_native_wrapper_keeps_its_existing_three_budgets(monkeypatch):
    observed = {}
    actual_run = cell_control.Owner.run
    actual_call = cell_control.Owner.call

    async def wrapped_run(self, opening, work, *, opening_s):
        observed.update(opening=opening_s, exit=self.exit_s)
        return await actual_run(self, opening, work, opening_s=opening_s)

    async def wrapped_call(self, work, *, total_s):
        observed.setdefault("phases", []).append(total_s)
        return await actual_call(self, work, total_s=total_s)

    async def opening(self, what):
        assert what == "native"
        self.conn = FakeConn()

    monkeypatch.setattr(cell_control.Owner, "run", wrapped_run)
    monkeypatch.setattr(cell_control.Owner, "call", wrapped_call)
    monkeypatch.setattr(cell_control.Owner, "_open", opening)
    assert (
        await cell_control.run("native", lambda owner: owner.transaction(AsyncMock(return_value=7)))
        == 7
    )
    assert observed == {"opening": 75.0, "exit": pg._RELEASE_TIMEOUT_S, "phases": [75.0, 60.0]}


async def test_failed_phase_keeps_its_end_instead_of_custom_exit():
    owner = cell_control.Owner(exit_s=99)
    conn = FakeConn()

    async def opening():
        owner.conn = conn

    async def body():
        return await owner.call(lambda: asyncio.Future(), total_s=0.04)

    before = time.monotonic()
    with pytest.raises(TimeoutError):
        await owner.run(opening, body, opening_s=0.1)
    assert before < owner.close_end <= before + 0.1
    assert owner.close_end == owner.phase_end
    assert conn.executed == ["CLOSE"]


async def test_public_close_defers_repeated_signals_until_acknowledged_within_original_end():
    events = []
    closing, finish = asyncio.Event(), asyncio.Event()

    class Connection:
        async def close(self, **kwargs):
            events.append("close")
            closing.set()
            await finish.wait()
            events.append("ack")

        def terminate(self):
            events.append("abort")

        def is_closed(self):
            return "ack" in events

    owner = cell_control.Owner(exit_s=0.2)
    owner.conn = Connection()
    task = asyncio.create_task(owner.close())
    try:
        await closing.wait()
        original_end = owner.close_end
        task.cancel("first signal")
        await asyncio.sleep(0)
        task.cancel("second signal")
        await asyncio.sleep(0)
        assert not task.done(), "a second signal must not interrupt owned public close"
        assert owner.close_end == original_end
        finish.set()
        with pytest.raises(asyncio.CancelledError, match="first signal"):
            await task
        assert events == ["close", "ack"]
    finally:
        finish.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("stage", ["opening", "initial", "directory", "transaction", "close"])
@pytest.mark.parametrize("kind", ["SystemExit", "KeyboardInterrupt"])
async def test_placement_fatal_stays_inside_owner_until_public_close(stage, kind):
    import subprocess
    import sys
    import textwrap

    program = textwrap.dedent(
        """
        import argparse, asyncio, sys
        import httpx
        from server.cli import cell_place
        from server.core import pg
        stage, kind = sys.argv[1:]
        original = (SystemExit if kind == 'SystemExit' else KeyboardInterrupt)('original')
        events = []
        class Transaction:
            async def start(self): pass
            async def commit(self): events.append('commit')
        class Connection:
            def transaction(self): return Transaction()
            async def execute(self, *args):
                if stage == 'transaction': raise original
            async def close(self, **kw):
                events.append('close')
                if stage == 'close': raise original
            def terminate(self): events.append('abort')
        conn = Connection()
        async def connect(**kw): return conn
        async def initialize(c):
            assert c is conn
            if stage == 'opening': raise original
        async def noop(*args): pass
        async def direct(c, what): return []
        async def identity(c, name):
            assert c is conn
            if stage == 'initial': raise original
            return 'dep', {}
        async def directory(*args):
            if stage == 'directory': raise original
        async def held(c): return True
        async def main():
            pg._connect_dedicated = connect
            pg._check_deployment = initialize
            pg.init_connection = noop
            cell_place.apg.direct_refusals = direct
            cell_place.apg.take_lock = noop
            cell_place.apg.lock_held = held
            cell_place._identity = identity
            cell_place._directory = directory
            try:
                await cell_place.place(argparse.Namespace(cell='first', org='acme'), httpx.URL('http://router'))
            except BaseException as error:
                assert error is original, (type(error).__name__, events)
                assert events.count('close') == 1, events
                await asyncio.sleep(0)
                print('original-after-public-close')
                return
            raise AssertionError('fatal became success')
        asyncio.run(main())
        """
    )
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", program, stage, kind],
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "original-after-public-close" in result.stdout


async def test_repeated_signals_cannot_extend_an_expired_public_close():
    closing = asyncio.Event()
    conn = FakeConn()

    async def stalled(**kwargs):
        closing.set()
        await asyncio.Future()

    conn.close = stalled
    owner = cell_control.Owner(exit_s=0.04)
    owner.conn = conn
    started = time.monotonic()
    task = asyncio.create_task(owner.close())
    await closing.wait()
    end = owner.close_end

    async def signals():
        while not task.done():
            task.cancel("first signal")
            await asyncio.sleep(0.001)

    signal_task = asyncio.create_task(signals())
    try:
        done, _ = await asyncio.wait({task}, timeout=0.2)
        assert done, "repeated signals cannot restart the original cleanup allowance"
        with pytest.raises(asyncio.CancelledError, match="first signal"):
            await task
        assert time.monotonic() - started < 0.2
        assert owner.close_end == end
        assert "TERMINATE" in conn.executed
    finally:
        signal_task.cancel()
        await asyncio.gather(signal_task, task, return_exceptions=True)


async def test_closed_transport_cannot_acknowledge_pending_public_cancellation():
    closing, acknowledged = asyncio.Event(), asyncio.Event()
    events = []

    class Connection:
        async def close(self, **kwargs):
            closing.set()
            await acknowledged.wait()
            events.append("ack")

        def is_closed(self):
            # An aborted transport is closed even while its cancellation is pending.
            return True

        def terminate(self):
            events.append("abort")

    owner = cell_control.Owner(exit_s=0.2)
    owner.conn = Connection()
    task = asyncio.create_task(owner.close())
    try:
        await closing.wait()
        task.cancel("stop")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not task.done() and events == []
        acknowledged.set()
        with pytest.raises(asyncio.CancelledError, match="stop"):
            await task
        assert events == ["ack"]
    finally:
        acknowledged.set()
        await asyncio.gather(task, return_exceptions=True)
