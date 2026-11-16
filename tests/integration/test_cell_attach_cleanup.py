"""Attach owners close active sessions before cancelling transaction work."""

import argparse
import asyncio
import contextlib
import time

import pytest

from server.cli import cell_attach
from server.cli import cell_attach_pg as apg
from server.core import cell_triggers, pg
from tests.integration.test_pg_owned_cleanup import database as database
from tests.integration.test_pg_owned_cleanup import released, sleeping

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    "owner,phase",
    [
        ("trigger", "query"),
        ("trigger", "rollback"),
        ("mode", "query"),
        ("mode", "rollback"),
        ("direct", "query"),
    ],
)
@pytest.mark.parametrize("interruption", ["cancel", "deadline", "second-cancel"])
async def test_attach_owner_releases_backend_locks_before_return(
    database, monkeypatch, owner, phase, interruption
):
    import asyncpg

    conn, observer = database
    await conn.execute(
        "CREATE SCHEMA loom; CREATE TABLE loom.tenancy_mode(mode text, since timestamptz); INSERT INTO loom.tenancy_mode VALUES('recording', now())"
    )
    pid = conn.get_server_pid()
    execute = asyncpg.Connection.execute
    closing = asyncio.Event()
    close = asyncpg.Connection.close

    async def intercepted(self, query, *args, **kwargs):
        if self is conn:
            if query.startswith("SET LOCAL statement_timeout"):
                return await execute(self, "SET LOCAL statement_timeout = '10000ms'")
            if query == "RAISE Python error":
                raise asyncpg.LockNotAvailableError("trigger statement busy")
            if phase == "rollback" and query.startswith("ROLLBACK"):
                await execute(self, "SELECT pg_sleep(10)")
            if owner == "mode" and query.startswith("LOCK TABLE"):
                await execute(self, "SELECT * FROM probe FOR UPDATE")
                if phase == "rollback":
                    raise ValueError("mode statement failed")
                return await execute(self, "SELECT pg_sleep(10)")
            if owner == "direct" and query.startswith("BEGIN"):
                await execute(self, query)
                return await execute(self, "SELECT * FROM probe FOR UPDATE; SELECT pg_sleep(10)")
        return await execute(self, query, *args, **kwargs)

    async def closing_session(self, *args, **kwargs):
        was_open = not self.is_closed()
        await close(self, *args, **kwargs)
        if self is conn and was_open and interruption == "second-cancel":
            closing.set()
            await asyncio.Future()

    async def ensure(conn, table, **kwargs):
        statements = ["SELECT * FROM probe FOR UPDATE"]
        statements += ["RAISE Python error"] if phase == "rollback" else ["SELECT pg_sleep(10)"]
        return await cell_triggers._run_ddl(
            conn, table, statements, kwargs["lock_timeout"], 1, warn=False
        )

    @contextlib.asynccontextmanager
    async def dedicated(**_):
        yield conn

    monkeypatch.setattr(asyncpg.Connection, "execute", intercepted)
    monkeypatch.setattr(asyncpg.Connection, "close", closing_session)
    monkeypatch.setattr(cell_triggers, "ensure_table", ensure)
    monkeypatch.setattr(apg, "_TRIGGER_TRY_MARGIN_S", 0.4)
    monkeypatch.setattr(apg, "_MODE_STANDBY_S", 0.5)
    monkeypatch.setattr(apg, "_MODE_STATEMENT_MARGIN_MS", 0)
    monkeypatch.setattr(cell_attach, "_LOCK_STATEMENT_MS", 600)
    monkeypatch.setattr(pg, "dedicated", dedicated)
    if owner == "trigger":
        work = apg._ensure_one(conn, "probe", 100, apg.Wait(0))
    elif owner == "mode":
        work = apg._mode_try(conn, 100, lambda _: None)
    else:
        run = cell_attach.Run(None, argparse.Namespace(cell="test", wait=0), None, lambda _: None)
        work = run.open_lock()
    task = asyncio.create_task(work)
    try:
        await sleeping(observer, pid)
        started = time.monotonic()
        if interruption != "deadline":
            task.cancel()
        if interruption == "second-cancel":
            await asyncio.wait_for(closing.wait(), 1)
            task.cancel()
        with pytest.raises(TimeoutError if interruption == "deadline" else asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(task), 1.2)
        assert time.monotonic() - started < 0.7
        await released(observer, pid)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_unexpected_trigger_error_reaches_owner_before_rollback(database, monkeypatch):
    import asyncpg

    from server.core import cell

    conn, observer = database
    pid = conn.get_server_pid()
    await conn.execute("SELECT pg_advisory_lock($1)", cell.ATTACH_LOCK_KEY)
    execute = asyncpg.Connection.execute
    original = ValueError("unexpected trigger operation failed")

    async def intercepted(self, query, *args, **kwargs):
        if self is conn:
            if query == "RAISE unexpected Python error":
                assert await observer.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=$1 AND locktype='advisory')",
                    pid,
                )
                raise original
            if query.startswith("ROLLBACK"):
                await execute(self, "SELECT pg_sleep(10)")
        return await execute(self, query, *args, **kwargs)

    monkeypatch.setattr(asyncpg.Connection, "execute", intercepted)
    started = time.monotonic()
    with pytest.raises(ValueError) as error:
        await apg.supervise(
            conn,
            cell_triggers._run_ddl(
                conn,
                "probe",
                ["SELECT * FROM probe FOR UPDATE", "RAISE unexpected Python error"],
                attempts=1,
                warn=False,
            ),
            end=started + 0.5,
        )
    assert error.value is original
    assert time.monotonic() - started < 0.5
    await released(observer, pid)
