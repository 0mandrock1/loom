"""Real event CHECK metadata, old insert drain and grant refusal on a private database."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from clickhouse_connect.driver.exceptions import DatabaseError

from server.core.ch import CHClient, query_tag
from server.insights.store import event_cutover

pytestmark = pytest.mark.usefixtures("ch_target_required")


@pytest_asyncio.fixture
async def world(monkeypatch) -> AsyncIterator[SimpleNamespace]:
    client = CHClient()
    db, identity = "event_cutover_" + uuid4().hex, str(uuid4())
    attempted = False
    try:
        await client.connect()
        attempted = True
        await client.execute(f"CREATE DATABASE {db} UUID '{identity}' ENGINE = Atomic")
        for name in ("agent_events", "agent_events_private"):
            await client.execute(f"CREATE TABLE {db}.{name} AS loom.{name}")
        monkeypatch.setattr(event_cutover, "database", lambda: db)
        yield SimpleNamespace(client=client, db=db)
    finally:
        try:
            if attempted:
                row = await client.query_one(
                    "SELECT toString(uuid) AS uuid FROM system.databases WHERE name = {db:String}",
                    params={"db": db},
                )
                if row:
                    assert row["uuid"] == identity, "Private cutover database identity changed"
                    await client.execute(f"DROP DATABASE {db} SYNC")
        finally:
            await client.disconnect()


async def test_native_check_is_enforced_and_clean_storage_is_confirmed(world) -> None:
    await event_cutover.confirm_ordinary_events(ch=world.client)
    with pytest.raises(Exception, match="CONSTRAINT|Constraint"):
        await world.client.execute(
            f"INSERT INTO {world.db}.agent_events (mirror_marked) VALUES (1)",
            settings={"async_insert": 0},
        )
    row = await world.client.query_one(f"SELECT count() AS n FROM {world.db}.agent_events")
    assert row and row["n"] == 0


@pytest.mark.parametrize("kind", ["missing", "wrong", "assume"])
async def test_native_missing_wrong_or_assumed_constraint_is_not_a_fence(world, kind) -> None:
    await world.client.execute(
        f"ALTER TABLE {world.db}.agent_events DROP CONSTRAINT ordinary_events_only"
    )
    if kind != "missing":
        clause = "CHECK 1" if kind == "wrong" else f"ASSUME {event_cutover.ORDINARY}"
        await world.client.execute(
            f"ALTER TABLE {world.db}.agent_events ADD CONSTRAINT ordinary_events_only {clause}"
        )
    with pytest.raises(event_cutover.EventCutoverUnavailable):
        await event_cutover.confirm_ordinary_events(ch=world.client)


async def test_native_private_contamination_is_preserved_and_refused(world) -> None:
    await world.client.execute(
        f"ALTER TABLE {world.db}.agent_events DROP CONSTRAINT ordinary_events_only"
    )
    await world.client.execute(
        f"INSERT INTO {world.db}.agent_events (mirror_marked) VALUES (1)",
        settings={"async_insert": 0},
    )
    await world.client.execute(
        f"ALTER TABLE {world.db}.agent_events ADD CONSTRAINT ordinary_events_only "
        f"CHECK {event_cutover.ORDINARY}"
    )
    with pytest.raises(event_cutover.EventCutoverUnavailable):
        await event_cutover.confirm_ordinary_events(ch=world.client)
    row = await world.client.query_one(f"SELECT count() AS n FROM {world.db}.agent_events")
    assert row and row["n"] == 1


async def _running(client: CHClient, marker: str) -> dict | None:
    return await client.query_one(
        "SELECT query_id, read_rows FROM system.processes "
        "WHERE query_kind = 'Insert' AND Settings['log_comment'] = {tag:String}",
        params={"tag": marker},
    )


async def test_native_insert_with_old_metadata_drains_before_the_private_census(world) -> None:
    await world.client.execute(
        f"ALTER TABLE {world.db}.agent_events DROP CONSTRAINT ordinary_events_only"
    )
    producer = CHClient()
    task = None
    marker = "cutover_old_insert_" + uuid4().hex
    try:
        await producer.connect()

        async def insert() -> None:
            with query_tag(marker):
                await producer.execute(
                    f"INSERT INTO {world.db}.agent_events (mirror_marked) "
                    "SELECT toUInt8(1) FROM numbers(100) WHERE sleepEachRow(0.05) = 0 "
                    "SETTINGS async_insert = 0, max_block_size = 10, max_threads = 1, "
                    "max_execution_time = 8, timeout_overflow_mode = 'throw'"
                )

        task = asyncio.create_task(insert())
        async with asyncio.timeout(5):
            while not (row := await _running(world.client, marker)) or not row["read_rows"]:
                assert not task.done(), "The fixture missed the prepared INSERT"
                await asyncio.sleep(0.01)
        await world.client.execute(
            f"ALTER TABLE {world.db}.agent_events ADD CONSTRAINT ordinary_events_only "
            f"CHECK {event_cutover.ORDINARY}"
        )
        assert not task.done(), "The fixture must observe an INSERT crossing the CHECK"
        query_id = row["query_id"]
        observed = {"old": False, "census": False}

        async def query(sql, params=None):
            if "AS private" in sql:
                assert observed["old"]
                assert await _running(world.client, marker) is None
                observed["census"] = True
            rows = await world.client.query_all(sql, params=params)
            if "FROM system.processes" in sql:
                observed["old"] |= any(
                    query_id == item[1] for entry in rows for item in entry["inserts"]
                )
            return rows

        with pytest.raises(event_cutover.EventCutoverUnavailable):
            await event_cutover.confirm_ordinary_events(ch=SimpleNamespace(query_all=query))
        assert observed == {"old": True, "census": True}
        await task
        assert await _running(world.client, marker) is None
        row = await world.client.query_one(f"SELECT count() AS n FROM {world.db}.agent_events")
        assert row and row["n"] == 100
    finally:
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await producer.disconnect()


async def _queued(world, query_id: str) -> None:
    rows = await world.client.query_all(
        "SELECT `entries.query_id` AS ids, `entries.bytes` AS sizes "
        "FROM system.asynchronous_inserts WHERE database = {db:String} "
        "AND table = 'agent_events' AND has(`entries.query_id`, {qid:String})",
        params={"db": world.db, "qid": query_id},
    )
    assert len(rows) == 1, "The exact detached request must still be queued"
    assert rows[0]["ids"].count(query_id) == 1
    assert rows[0]["sizes"][rows[0]["ids"].index(query_id)] > 0


async def _rejected_flush(world, query_id: str) -> None:
    # The delayed log write, rather than queue disappearance, proves the exact terminal outcome.
    async with asyncio.timeout(20):
        while True:
            row = await world.client.query_one(
                "SELECT status, exception, flush_query_id FROM system.asynchronous_insert_log "
                "WHERE database = {db:String} AND table = 'agent_events' "
                "AND query_id = {qid:String} ORDER BY event_time_microseconds DESC LIMIT 1",
                params={"db": world.db, "qid": query_id},
            )
            if row:
                assert row["status"] == "FlushError", row
                assert "ordinary_events_only" in row["exception"], row
                assert row["flush_query_id"], row
                return
            await asyncio.sleep(0.05)


async def _submit_detached(world, query_id: str) -> None:
    producer = CHClient()
    try:
        await producer.connect()
        observer = await world.client.query_one("SELECT toString(serverUUID()) AS id")
        assert await producer.query_one("SELECT toString(serverUUID()) AS id") == observer
        await producer.execute(
            f"INSERT INTO {world.db}.agent_events (mirror_marked) VALUES (1)",
            settings={
                "async_insert": 1,
                "wait_for_async_insert": 0,
                "async_insert_use_adaptive_busy_timeout": 0,
                "async_insert_busy_timeout_max_ms": 60000,
                "query_id": query_id,
            },
        )
    finally:
        await producer.disconnect()


async def test_native_disconnected_queued_insert_uses_the_installed_check(world) -> None:
    query_id = "cutover_queued_" + uuid4().hex
    await world.client.execute(
        f"ALTER TABLE {world.db}.agent_events DROP CONSTRAINT ordinary_events_only"
    )
    try:
        await _submit_detached(world, query_id)
        await _queued(world, query_id)
        await world.client.execute(
            f"ALTER TABLE {world.db}.agent_events ADD CONSTRAINT ordinary_events_only "
            f"CHECK {event_cutover.ORDINARY}"
        )
        await _queued(world, query_id)
        # Scoped flush waits its collected batches; their exceptions are logged separately.
        await world.client.execute(f"SYSTEM FLUSH ASYNC INSERT QUEUE {world.db}.agent_events")
        await _rejected_flush(world, query_id)
        await event_cutover.confirm_ordinary_events(ch=world.client)
        with pytest.raises(Exception, match="ordinary_events_only"):
            await world.client.execute(
                f"INSERT INTO {world.db}.agent_events (mirror_marked) VALUES (1)",
                settings={"async_insert": 0},
            )
        row = await world.client.query_one(f"SELECT count() AS n FROM {world.db}.agent_events")
        assert row and row["n"] == 0
    finally:
        # A failed observation must not leave this database's buffered request behind.
        await world.client.execute(f"SYSTEM FLUSH ASYNC INSERT QUEUE {world.db}.agent_events")


@pytest.mark.parametrize("valid", [False, True])
async def test_native_printed_fence_rechecks_live_metadata_in_one_session(world, valid) -> None:
    statements = await event_cutover.printed_fence(ch=world.client)
    if not valid:
        await world.client.execute(
            f"ALTER TABLE {world.db}.agent_events DROP CONSTRAINT ordinary_events_only"
        )
    completed = []
    session = {"session_id": "event_cutover_" + uuid4().hex, "session_timeout": 30}
    await world.client.execute("SELECT 1", settings=session)
    session["session_check"] = 1
    backend = await world.client.raw_bytes(
        "SELECT toString(serverUUID()) FORMAT TSV", settings=session
    )
    assert backend.strip() and backend.strip() != b"00000000-0000-0000-0000-000000000000"
    try:
        for statement in statements:
            await world.client.execute(statement, settings=session)
            completed.append(statement)
            assert (
                await world.client.raw_bytes(
                    "SELECT toString(serverUUID()) FORMAT TSV", settings=session
                )
                == backend
            )
    except DatabaseError as exc:
        assert "Ordinary event CHECK is unconfirmed" in str(exc)
        assert not valid, "A clean printed fence must finish in its one SQL session"
        assert not any(s.startswith("GRANT ") for s in completed)
        assert len(completed) == 2, "The first live metadata assertion must refuse"
    else:
        assert valid, "A print-time valid CHECK cannot replace execution-time proof"
        assert completed == statements
