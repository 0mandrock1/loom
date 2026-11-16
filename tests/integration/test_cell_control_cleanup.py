"""Native cell-control interruption closes its actual Postgres backend and locks."""

from __future__ import annotations

import argparse
import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from server.cli import cell_control, cell_prepare
from server.core import pg
from server.settings import settings
from tests.integration._pg_database import own_database
from tests.integration.test_pg_owned_cleanup import released, sleeping

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def native_database(monkeypatch) -> AsyncIterator[Any]:
    import asyncpg

    async with own_database() as dsn:
        monkeypatch.setattr(settings, "pg_dsn", dsn)
        monkeypatch.setattr(settings, "cell", "first")
        monkeypatch.setattr(pg, "_expected", None)
        observer = await asyncpg.connect(dsn)
        await observer.execute(
            "CREATE TABLE probe(id int PRIMARY KEY); INSERT INTO probe VALUES(1)"
        )
        try:
            yield observer
        finally:
            await observer.close(timeout=1)


@pytest.mark.parametrize("interrupt", ["cancel", "deadline"])
async def test_native_prepare_interrupt_releases_backend_and_locks(
    native_database, monkeypatch, interrupt
):
    observed = asyncio.Event()
    backend: dict[str, Any] = {}

    async def read(conn, **_):
        backend["pid"] = conn.get_server_pid()
        observed.set()
        await conn.execute("SELECT * FROM probe FOR UPDATE; SELECT pg_sleep(10)")

    monkeypatch.setattr(cell_prepare, "_read_rows", read)
    if interrupt == "deadline":
        monkeypatch.setattr(cell_control, "TRANSACTION_S", 0.4)
    task = asyncio.create_task(
        cell_prepare.prepare(None, argparse.Namespace(), httpx.URL("http://router"))
    )
    await asyncio.wait_for(observed.wait(), 3)
    await sleeping(native_database, backend["pid"])
    began = time.monotonic()
    if interrupt == "cancel":
        task.cancel("native prepare cancelled")
    with pytest.raises(asyncio.CancelledError if interrupt == "cancel" else TimeoutError):
        await asyncio.wait_for(task, 1.5)
    assert time.monotonic() - began < 1.5
    await released(native_database, backend["pid"])


async def test_native_prepare_body_failure_is_not_hidden_by_rollback(native_database, monkeypatch):
    import asyncpg

    execute = asyncpg.Connection.execute
    original = ValueError("original native transaction failure")
    backend = {}

    async def read(conn, **_):
        backend["pid"] = conn.get_server_pid()
        await conn.execute("SELECT * FROM probe FOR UPDATE")
        raise original

    async def stalled_rollback(self, query, *args, **kwargs):
        if query.startswith("ROLLBACK"):
            await execute(self, "SELECT pg_sleep(10)")
        return await execute(self, query, *args, **kwargs)

    monkeypatch.setattr(asyncpg.Connection, "execute", stalled_rollback)
    monkeypatch.setattr(cell_prepare, "_read_rows", read)
    monkeypatch.setattr(cell_control, "TRANSACTION_S", 0.4)
    began = time.monotonic()
    with pytest.raises(ValueError, match="original native transaction failure") as captured:
        await asyncio.wait_for(
            cell_prepare.prepare(None, argparse.Namespace(), httpx.URL("http://router")), 1.5
        )
    assert captured.value is original
    assert time.monotonic() - began < 0.4
    await released(native_database, backend["pid"])


async def test_native_prepare_second_cancel_preserves_first_and_releases_locks(
    native_database, monkeypatch
):
    import asyncpg

    real_close = asyncpg.Connection.close
    observed, close_started, close_return = asyncio.Event(), asyncio.Event(), asyncio.Event()
    backend = {}

    async def read(conn, **_):
        backend["conn"], backend["pid"] = conn, conn.get_server_pid()
        observed.set()
        await conn.execute("SELECT * FROM probe FOR UPDATE; SELECT pg_sleep(10)")

    async def delayed_close(self, **kwargs):
        await real_close(self, **kwargs)
        if self is backend.get("conn"):
            close_started.set()
            await close_return.wait()

    monkeypatch.setattr(cell_prepare, "_read_rows", read)
    monkeypatch.setattr(asyncpg.Connection, "close", delayed_close)
    task = asyncio.create_task(
        cell_prepare.prepare(None, argparse.Namespace(), httpx.URL("http://router"))
    )
    try:
        await observed.wait()
        await sleeping(native_database, backend["pid"])
        task.cancel("first native cancellation")
        await asyncio.wait_for(close_started.wait(), 1)
        task.cancel("second cancellation")
        with pytest.raises(asyncio.CancelledError, match="first native cancellation"):
            await asyncio.wait_for(task, 0.5)
        await released(native_database, backend["pid"])
    finally:
        close_return.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("stage", ["open", "phase", "body"])
@pytest.mark.parametrize("kind", ["org", "keyboard"])
async def test_native_fatal_guard_is_inert_until_backend_closed(native_database, stage, kind):
    import os
    import sys

    # A separate interpreter keeps a regression in child fatal-exception transport
    # from stopping pytest's event loop. Proof runs before the native command exits.
    from tests.integration._pg_database import with_database

    base = os.environ["LOOM_PG_DSN"]
    dsn = with_database(base, await native_database.fetchval("SELECT current_database()"))
    script = """
import argparse, asyncio, json, os, sys
import asyncpg, httpx
from server.cli import cell_control, cell_prepare
from server.cli._ch import OrgNotHeld
from server.core import pg
from server.settings import settings
stage, kind = sys.argv[1:]
settings.pg_dsn = os.environ['LOOM_NATIVE_CLEANUP_DSN']
settings.cell = 'first'
pg._expected = None
original = OrgNotHeld('org refused') if kind == 'org' else KeyboardInterrupt('guard interrupted')
backend = {}
real_init = pg.init_connection
async def initialize(conn):
    await real_init(conn)
    backend['pid'] = conn.get_server_pid()
    if stage == 'open':
        raise original
async def read(conn, **kwargs):
    backend['pid'] = conn.get_server_pid()
    await conn.execute('SELECT * FROM probe FOR UPDATE')
    if stage == 'phase':
        raise original
    return cell_prepare._CellRows('dep-first', None, '', None, None, 0)
async def beats(ch):
    raise original
pg.init_connection = initialize
cell_prepare._read_rows = read
cell_prepare._read_beats = beats
async def main():
    observer = await asyncpg.connect(settings.pg_dsn)
    try:
        try:
            await cell_prepare.prepare(None, argparse.Namespace(), httpx.URL('http://router'))
        except BaseException as found:
            assert found is original
            assert not await observer.fetchval('SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=$1)', backend['pid'])
            assert not await observer.fetchval('SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE pid=$1)', backend['pid'])
            print(json.dumps({'original': True, 'backend_absent': True}))
        else:
            raise AssertionError('fatal guard became success')
    finally:
        await observer.close(timeout=1)
asyncio.run(main())
"""
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        stage,
        kind,
        env={**os.environ, "LOOM_NATIVE_CLEANUP_DSN": dsn},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _stderr = await asyncio.wait_for(child.communicate(), 10)
        assert child.returncode == 0, "native fatal exception interrupted its supervisor"
        assert b'"backend_absent": true' in stdout
    finally:
        if child.returncode is None:
            child.kill()
            await child.wait()
