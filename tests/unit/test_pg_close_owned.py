"""Owned-session cleanup delivers cancellation before aborting its transport."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from server.core import pg

pytestmark = pytest.mark.asyncio


class Connection:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.finished = asyncio.Event()
        self.aborted = False
        self.timeouts: list[float] = []

    async def close(self, *, timeout: float) -> None:  # noqa: ASYNC109 — driver API.
        self.timeouts.append(timeout)
        self.started.set()
        await self.finished.wait()

    def terminate(self) -> None:
        self.aborted = True
        self.finished.set()


async def test_public_close_completes_before_transport_abort() -> None:
    conn = Connection()
    conn.finished.set()
    await pg.close_owned(conn, end=time.monotonic() + 1)
    assert conn.timeouts and 0 < conn.timeouts[0] <= 1
    assert not conn.aborted


async def test_expired_deadline_aborts_without_starting_close() -> None:
    conn = Connection()
    with pytest.raises(TimeoutError):
        await pg.close_owned(conn, end=time.monotonic() - 1)
    assert conn.aborted and not conn.timeouts


async def test_close_timeout_aborts_within_the_original_deadline() -> None:
    conn = Connection()
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        await pg.close_owned(conn, end=started + 0.03)
    assert conn.aborted and time.monotonic() - started < 0.3


async def test_second_cancellation_aborts_without_waiting_for_close_unwind() -> None:
    conn = Connection()
    supervisor_started = asyncio.Event()
    original = asyncio.CancelledError("operation cancelled")

    async def supervisor() -> None:
        try:
            supervisor_started.set()
            await asyncio.Future()
        except asyncio.CancelledError:
            try:
                await pg.close_owned(conn, end=time.monotonic() + 1)
            except BaseException:  # noqa: BLE001 — preserve the original cancellation.
                pass
            raise original from None

    task = asyncio.create_task(supervisor())
    await supervisor_started.wait()
    task.cancel()
    await asyncio.wait_for(conn.started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError, match="operation cancelled"):
        await asyncio.wait_for(task, 0.3)
    assert conn.aborted


async def test_failed_public_close_aborts_and_reports_failure() -> None:
    class Broken(Connection):
        async def close(self, **_: Any) -> None:
            raise ValueError("close failed")

    conn = Broken()
    with pytest.raises(ValueError, match="close failed"):
        await pg.close_owned(conn, end=time.monotonic() + 1)
    assert conn.aborted


async def test_expired_close_child_exception_is_retrieved() -> None:
    release = asyncio.Event()
    errors: list[dict[str, Any]] = []

    class LateFailure(Connection):
        async def close(self, **_: Any) -> None:
            self.started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            raise ValueError("late close failure")

    conn = LateFailure()
    loop = asyncio.get_running_loop()
    old_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _, context: errors.append(context))
    try:
        with pytest.raises(TimeoutError):
            await pg.close_owned(conn, end=time.monotonic() + 0.01)
        assert conn.aborted
        release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not errors
    finally:
        release.set()
        loop.set_exception_handler(old_handler)


async def test_migration_failure_survives_cleanup_failure(monkeypatch, tmp_path) -> None:
    import contextlib

    from server.core.schema import pg_migrate

    class Broken(Connection):
        async def close(self, **_: Any) -> None:
            raise RuntimeError("cleanup failed")

    conn = Broken()

    @contextlib.asynccontextmanager
    async def connect():
        yield conn

    async def fail(*_: Any) -> None:
        raise ValueError("migration failed")

    monkeypatch.setattr(pg_migrate, "_migrate", fail)
    with pytest.raises(ValueError, match="migration failed"):
        await pg_migrate.migrate(tmp_path, connect=connect, schema="test")
    assert conn.aborted


async def test_successful_migration_still_reports_context_exit_failure(
    monkeypatch, tmp_path
) -> None:
    import contextlib

    from server.core.schema import pg_migrate

    original = ValueError("context exit failed")

    @contextlib.asynccontextmanager
    async def connect():
        yield Connection()
        raise original

    async def applied(*_: Any) -> list[str]:
        return ["001.sql"]

    monkeypatch.setattr(pg_migrate, "_migrate", applied)
    with pytest.raises(ValueError) as error:
        await pg_migrate.migrate(tmp_path, connect=connect, schema="test")
    assert error.value is original


@pytest.mark.parametrize("cancel_work", [False, True])
async def test_second_cancellation_during_migration_context_exit_keeps_first_failure(
    monkeypatch, tmp_path, cancel_work
) -> None:
    import contextlib

    from server.core.schema import pg_migrate

    conn = Connection()
    conn.finished.set()
    started, exiting = asyncio.Event(), asyncio.Event()
    observed: list[BaseException] = []
    original = ValueError("migration failed")

    @contextlib.asynccontextmanager
    async def connect():
        try:
            yield conn
        except BaseException as error:
            observed.append(error)
            exiting.set()
            await asyncio.Future()
            raise

    async def work(*_: Any) -> list[str]:
        started.set()
        if cancel_work:
            await asyncio.Future()
        raise original

    monkeypatch.setattr(pg_migrate, "_migrate", work)
    monkeypatch.setattr(pg, "_RELEASE_TIMEOUT_S", 0.1)
    task = asyncio.create_task(pg_migrate.migrate(tmp_path, connect=connect, schema="test"))
    await asyncio.wait_for(started.wait(), 1)
    before = time.monotonic()
    if cancel_work:
        task.cancel("first cancellation")
    await asyncio.wait_for(exiting.wait(), 1)
    task.cancel("second cancellation")
    with pytest.raises(BaseException) as result:
        await asyncio.wait_for(task, 0.3)
    assert time.monotonic() - before < 0.3
    assert result.value is observed[0]
    assert (
        isinstance(result.value, asyncio.CancelledError)
        if cancel_work
        else result.value is original
    )
    assert len(conn.timeouts) == 1
    assert conn.aborted
