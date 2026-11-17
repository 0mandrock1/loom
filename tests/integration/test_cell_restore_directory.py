"""A real router directory returns durable bounded placement snapshots for restore."""

from __future__ import annotations

import copy
import json

import pytest

from server.cli import cell_restore
from server.core import cell
from server.router import directory
from server.router import restore as protocol
from tests.integration import test_cell_place as placement_tests
from tests.integration.test_cell_place import ROUTER


@pytest.fixture
async def stores(monkeypatch):
    async for value in placement_tests.stores.__wrapped__(monkeypatch):
        yield value


async def seed(orgs):
    async with directory._connection() as conn, conn.transaction():
        await conn.execute("SELECT cell FROM loom_router.cells WHERE cell='first' FOR UPDATE")
        await conn.execute(
            "INSERT INTO loom_router.placements(org,cell) SELECT unnest($1::text[]),'first'", orgs
        )
        await conn.execute(
            "UPDATE loom_router.cells SET placed=placed+$1, closed=true, placements_max=0 WHERE cell='first'",
            len(orgs),
        )


async def test_real_directory_pages_closed_full_cell_and_audits_final_empty_read(stores):
    names = [f"org-{n:04d}" for n in range(1002)]
    await seed(names)
    after, previous, found = None, None, []
    while True:
        answer = await directory.restore_read(
            "first", "dep-first", after, protocol.digest(previous) if previous else None
        )
        previous = protocol.validate_page(answer, after=after, previous=previous)
        found.extend(answer["orgs"])
        if answer["done"]:
            break
        after = answer["next"]
    assert found == names
    empty = await directory.restore_read("first", "dep-first", names[-1], protocol.digest(previous))
    assert empty["orgs"] == [] and empty["done"] is True
    async with directory._connection() as conn:
        assert await conn.fetchval(
            "SELECT placed FROM loom_router.cells WHERE cell='first'"
        ) == len(names)
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM loom_router.audit WHERE action='restore_read'"
            )
            == 4
        )


async def test_insertion_before_cursor_requests_full_restart(stores):
    await seed([f"m-{n:04d}" for n in range(502)])
    first = await cell_restore._page(ROUTER, "first", "dep-first", None, None, 5)
    await seed(["a-before-the-cursor"])
    with pytest.raises(protocol.SnapshotChanged):
        await cell_restore._page(
            ROUTER, "first", "dep-first", first["next"], first["descriptor"], 5
        )
    restarted = await cell_restore._page(ROUTER, "first", "dep-first", None, None, 5)
    assert restarted["orgs"][0] == "a-before-the-cursor"


async def test_configuration_change_refuses_but_limit_change_does_not(stores):
    first = await directory.restore_read("first", "dep-first", None, None)
    async with directory._connection() as conn:
        await conn.execute(
            "UPDATE loom_router.cells SET closed=true, placements_max=0 WHERE cell='first'"
        )
    assert (await directory.restore_read("first", "dep-first", None, first["sha256"]))[
        "sha256"
    ] == first["sha256"]
    async with directory._connection() as conn:
        fp = copy.deepcopy(first["descriptor"]["current_fp"])
        fp["fields"]["public_host"] = "changed.example.test"
        await conn.execute("UPDATE loom_router.cells SET current_fp=$1 WHERE cell='first'", fp)
    with pytest.raises(ValueError) as error:
        await cell_restore._page(ROUTER, "first", "dep-first", None, first["descriptor"], 5)
    assert not isinstance(error.value, protocol.SnapshotChanged)


async def test_maximum_org_names_have_a_bounded_progressing_page(stores):
    names = [f"{n:04d}" + "x" * (cell.ORG_MAX_BYTES - 4) for n in range(501)]
    await seed(names)
    answer = await directory.restore_read("first", "dep-first", None, None)
    assert 0 < len(answer["orgs"]) < 500
    assert len(protocol.encode(answer)) <= protocol.MAX_RESPONSE_BYTES
    assert answer["next"] == answer["orgs"][-1] and answer["done"] is False


async def test_index_seek_is_available_in_a_generic_prepared_plan(stores):
    await seed([f"org-{n:04d}" for n in range(1500)])
    async with directory._connection() as conn:
        await conn.execute(
            "INSERT INTO loom_router.placements(org,cell) "
            "SELECT 'other-' || n::text, 'second' FROM generate_series(1,1500) n"
        )
        await conn.execute("UPDATE loom_router.cells SET placed=1500 WHERE cell='second'")
        await conn.execute("ANALYZE loom_router.placements")
        await conn.execute("SET plan_cache_mode=force_generic_plan")
        await conn.execute(
            "PREPARE restore_keyset(text,text) AS SELECT org FROM loom_router.placements "
            "WHERE cell=$1 AND org>$2 ORDER BY org LIMIT 501"
        )
        plan = await conn.fetchval(
            "EXPLAIN (FORMAT JSON) EXECUTE restore_keyset('first','org-1200')"
        )
        rendered = json.dumps(plan)
        assert "placements_by_cell_org" in rendered
        assert "Index Cond" in rendered and "org > $2" in rendered
        assert "Sort" not in rendered


async def test_directory_deadline_terminates_a_real_backend_and_releases_cell_lock(
    stores, monkeypatch
):
    original = directory._audit
    pid = None

    async def stalled(conn, action, detail):
        nonlocal pid
        pid = await conn.fetchval("SELECT pg_backend_pid()")
        await conn.execute("SELECT pg_sleep(10)")

    monkeypatch.setattr(directory, "_audit", stalled)
    monkeypatch.setattr(directory, "RESTORE_READ_MAX_S", 0.08)
    with pytest.raises(directory.DirectoryUnavailable, match="deadline"):
        await directory.restore_read("first", "dep-first", None, None)
    assert pid is not None
    monkeypatch.setattr(directory, "_audit", original)
    async with directory._connection() as conn:
        assert not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=$1)", pid)
    assert (await directory.restore_read("first", "dep-first", None, None))["done"] is True


class OfflineClickHouse:
    """The offline fixture supplies schema/identity and probe I/O without inference."""

    def __init__(self):
        self.nonce = None
        self.writes = []

    async def query_one(self, sql, params=None):
        if "storage_authority" in sql:
            return {"authority": "postgres", "epoch": 1, "deployment_id": "dep-first"}
        if "worker_status" in sql:
            return {"n": 0}
        if "_migration_lock" in sql:
            return None if self.nonce is None else {"holder": self.nonce}
        raise AssertionError(sql)

    async def query_all(self, sql, params=None):
        if "loom.instances" in sql:
            return []
        if "loom._migrations" in sql:
            from server.core.paths import MIGRATIONS_DIR
            from server.core.schema.migrate import _checksum

            return [
                {"filename": f.name, "checksum": _checksum(f.read_text())}
                for f in MIGRATIONS_DIR.glob("*.sql")
            ]
        raise AssertionError(sql)

    async def execute(self, sql, params=None):
        self.writes.append(sql)
        if sql.startswith("INSERT"):
            self.nonce = params["nonce"]
        elif sql.startswith("ALTER"):
            assert params["me"] == self.nonce
            self.nonce = None
        else:
            raise AssertionError(sql)


async def test_complete_offline_cli_repairs_stale_own_and_retries_noop(stores, monkeypatch):
    import argparse

    import asyncpg

    from server.cli import cell_restore_pg as local
    from server.core import pg
    from server.settings import settings

    monkeypatch.setattr(settings, "cell", "first")
    monkeypatch.setattr(settings, "pg_dsn", stores["first"])
    monkeypatch.setattr(settings, "document_s3_bucket", "")
    await seed(["acme", "erased-org"])
    conn = await asyncpg.connect(stores["first"])
    try:
        await conn.execute(
            "UPDATE loom.cell_self SET current_fp=$1::jsonb",
            json.dumps(
                cell.as_value({"deployment_id": "dep-first", "public_host": "stale.example.test"})
            ),
        )
    finally:
        conn.terminate()
    ch = OfflineClickHouse()
    args = argparse.Namespace(cell="first", wait=15)
    runner = cell_restore.Restore(args, ROUTER, cell_restore.Budget(args.wait))
    first = await runner.run(ch)
    assert first["snapshot_orgs"] == 2 and first["inserted_tenancy"] == 2
    assert first["prepare_required"] is False and ch.nonce is None
    again = await cell_restore.Restore(args, ROUTER, cell_restore.Budget(args.wait)).run(ch)
    assert again["inserted_tenancy"] == 0
    async with pg.dedicated() as conn:
        rows = await local.read_rows(conn)
        assert rows["own"]["current_fp"] != cell.as_value(
            {"deployment_id": "dep-first", "public_host": "stale.example.test"}
        )
        assert await conn.fetchval("SELECT count(*) FROM loom.tenancy") == 2
    async with directory._connection() as conn:
        assert await conn.fetchval("SELECT placed FROM loom_router.cells WHERE cell='first'") == 2


async def test_wrong_directory_identity_precedes_every_schema_or_probe_write(stores, monkeypatch):
    import argparse

    from server.core import pg
    from server.settings import settings

    monkeypatch.setattr(settings, "cell", "first")
    monkeypatch.setattr(settings, "pg_dsn", stores["first"])
    async with directory._connection() as conn:
        await conn.execute(
            "UPDATE loom_router.cells SET deployment_id='another-deployment' WHERE cell='first'"
        )
    ch = OfflineClickHouse()
    with pytest.raises(ValueError, match="refused"):
        await cell_restore.Restore(
            argparse.Namespace(cell="first", wait=5), ROUTER, cell_restore.Budget(5)
        ).run(ch)
    assert ch.writes == []
    async with pg.dedicated() as conn:
        assert await conn.fetchval("SELECT count(*) FROM loom.tenancy") == 0


async def test_pending_cell_postgres_file_applies_offline_before_repair(
    stores, monkeypatch, tmp_path
):
    import argparse

    from server.core import pg
    from server.core.schema import pg_migrate
    from server.settings import settings

    monkeypatch.setattr(settings, "cell", "first")
    monkeypatch.setattr(settings, "pg_dsn", stores["first"])
    files = pg_migrate.migration_files()
    file = tmp_path / "999_restore_fixture.sql"
    file.write_text("CREATE TABLE IF NOT EXISTS loom.restore_fixture (id integer PRIMARY KEY);")
    monkeypatch.setattr(pg_migrate, "migration_files", lambda *args: [*files, file])
    ch = OfflineClickHouse()
    await cell_restore.Restore(
        argparse.Namespace(cell="first", wait=15), ROUTER, cell_restore.Budget(15)
    ).run(ch)
    async with pg.dedicated() as conn:
        assert await conn.fetchval("SELECT to_regclass('loom.restore_fixture') IS NOT NULL")
        assert await conn.fetchval(
            "SELECT checksum FROM loom.pg_migrations WHERE filename=$1", file.name
        ) == pg_migrate.checksum(file)


@pytest.mark.parametrize("ending", ["deadline", "cancel", "second_cancel"])
async def test_offline_cli_cancellation_releases_its_actual_backend(stores, monkeypatch, ending):
    import argparse
    import asyncio
    import time

    import asyncpg

    from server.cli import cell_restore_pg as local
    from server.core import pg
    from server.settings import settings

    monkeypatch.setattr(settings, "cell", "first")
    monkeypatch.setattr(settings, "pg_dsn", stores["first"])
    monkeypatch.setattr(settings, "document_s3_bucket", "")
    started, closing = asyncio.Event(), asyncio.Event()
    owned = {}

    async def stalled_probe(ch, conn):
        owned["pid"] = conn.get_server_pid()

        async def stalled():
            started.set()
            await conn.execute("SELECT pg_sleep(10)")

        await local.transaction(conn, stalled)

    from server.cli import cell_restore_stores

    monkeypatch.setattr(cell_restore_stores, "nonces", stalled_probe)
    if ending == "second_cancel":
        original = pg.close_owned

        async def stalled_close(conn, *, end):
            pid = conn.get_server_pid()
            await original(conn, end=end)
            if pid == owned.get("pid"):
                closing.set()
                # A second signal must not cancel a close that can still finish.
                await asyncio.sleep(0.03)

        monkeypatch.setattr(pg, "close_owned", stalled_close)
    seconds = 2 if ending == "deadline" else 15
    task = asyncio.create_task(
        cell_restore.Restore(
            argparse.Namespace(cell="first", wait=seconds), ROUTER, cell_restore.Budget(seconds)
        ).run(OfflineClickHouse())
    )
    observer = await asyncpg.connect(stores["first"])
    try:
        await asyncio.wait_for(started.wait(), 10)
        async with asyncio.timeout(2):
            for _ in range(400):
                if await observer.fetchval(
                    "SELECT wait_event='PgSleep' FROM pg_stat_activity WHERE pid=$1", owned["pid"]
                ):
                    break
                await asyncio.sleep(0.005)
            else:
                pytest.fail("restore did not reach its active query")
        before = time.monotonic()
        if ending != "deadline":
            task.cancel("original restore cancellation")
        if ending == "second_cancel":
            await asyncio.wait_for(closing.wait(), 1)
            task.cancel("second restore cancellation")
        if ending == "deadline":
            with pytest.raises(TimeoutError, match="--wait"):
                await asyncio.wait_for(task, 2.5)
        else:
            with pytest.raises(asyncio.CancelledError, match="original restore cancellation"):
                await asyncio.wait_for(task, 1.5)
        assert time.monotonic() - before < (2.5 if ending == "deadline" else 1.5)
        assert not await observer.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=$1)", owned["pid"]
        )
        assert not await observer.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE pid=$1)", owned["pid"]
        )
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await observer.close(timeout=1)


async def test_full_restore_restarts_count_growth_without_losing_its_session(stores, monkeypatch):
    import argparse

    from server.core import pg
    from server.settings import settings

    monkeypatch.setattr(settings, "cell", "first")
    monkeypatch.setattr(settings, "pg_dsn", stores["first"])
    monkeypatch.setattr(settings, "document_s3_bucket", "")
    await seed(["m-already-placed"])
    original = cell_restore.Restore.copy
    grew = False

    async def copy_then_grow(runner, first):
        nonlocal grew
        await original(runner, first)
        if not grew:
            grew = True
            await seed(["a-new-before-cursor"])

    monkeypatch.setattr(cell_restore.Restore, "copy", copy_then_grow)
    result = await cell_restore.Restore(
        argparse.Namespace(cell="first", wait=15), ROUTER, cell_restore.Budget(15)
    ).run(OfflineClickHouse())
    assert grew and result["snapshot_orgs"] == 2 and result["inserted_tenancy"] == 2
    async with directory._connection() as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM loom_router.audit WHERE action='control_claim'"
            )
            == 1
        )
    async with pg.dedicated() as conn:
        assert await conn.fetchval("SELECT count(*) FROM loom.tenancy") == 2


@pytest.fixture
async def command_loop():
    import asyncio

    return asyncio.get_running_loop()


@pytest.mark.parametrize(
    "shape", ["partial", "stale", "missing_ch_authority", "missing_pg_authority", "wrong_first"]
)
def test_public_cli_uses_real_org_free_gate_before_repair(
    stores, command_loop, monkeypatch, capsys, shape
):
    import argparse

    import asyncpg

    from server.core import ch as ch_module
    from server.settings import settings

    class ConnectedClickHouse(OfflineClickHouse):
        # Storage I/O is supplied; all production CLI declarations/gates execute.
        connected = False

        async def connect(self):
            self.connected = True

        async def disconnect(self):
            self.connected = False

        async def query_one(self, sql, params=None):
            if "storage_authority" in sql:
                if shape == "missing_ch_authority":
                    return None
                return {
                    "authority": "postgres",
                    "epoch": 1,
                    "deployment_id": "dep-second" if shape == "wrong_first" else "dep-first",
                }
            return await super().query_one(sql, params)

    target = stores["second" if shape == "wrong_first" else "first"]
    monkeypatch.setattr(settings, "cell", "first")
    monkeypatch.setattr(settings, "pg_dsn", target)
    monkeypatch.setattr(settings, "document_s3_bucket", "")
    ch = ConnectedClickHouse()
    monkeypatch.setattr(ch_module, "get_client", lambda: ch)
    # Synchronous CLI entry reuses the fixture's stopped loop, including its ASGI pool.
    monkeypatch.setattr(cell_restore.asyncio, "run", command_loop.run_until_complete)

    async def setup():
        conn = await asyncpg.connect(target)
        try:
            if shape == "partial":
                await conn.execute("DELETE FROM loom.cell_self; DELETE FROM loom.tenancy_mode")
                assert await cell.read_state(conn) == "partial"
            elif shape == "stale":
                await conn.execute(
                    "UPDATE loom.cell_self SET current_fp=$1::jsonb",
                    json.dumps(
                        cell.as_value({"deployment_id": "dep-first", "bucket": "stale-bucket"})
                    ),
                )
            elif shape == "missing_pg_authority":
                await conn.execute("DELETE FROM loom.authority")
        finally:
            await conn.close(timeout=1)

    command_loop.run_until_complete(setup())
    args = argparse.Namespace(cell="first", wait=15, router=str(ROUTER))
    result = cell_restore.cmd_restore(args)
    assert result == (0 if shape in ("partial", "stale") else 1)
    printed = capsys.readouterr()
    if shape in ("partial", "stale"):
        answer = json.loads(printed.out)
        assert answer["mode"] == "enforced" and answer["prepare_required"] is False
    else:
        assert printed.out == "" and ch.writes == []
        if shape == "wrong_first":
            assert "first row names cell" in printed.err
    assert ch.connected is False


@pytest.mark.parametrize("own_case", ["matching", "mismatching", "next_matches", "absent"])
async def test_retained_own_configuration_recovery_is_reported_without_changing_approval(
    stores, monkeypatch, own_case
):
    import argparse

    import asyncpg

    from server.settings import settings

    monkeypatch.setattr(settings, "cell", "first")
    monkeypatch.setattr(settings, "pg_dsn", stores["first"])
    monkeypatch.setattr(settings, "document_s3_bucket", "")
    async with directory._connection() as conn:
        await conn.execute("DELETE FROM loom_router.hosts WHERE cell='second'")
        await conn.execute("DELETE FROM loom_router.cells WHERE cell='second'")
        await conn.execute(
            "UPDATE loom_router.cells SET state='attaching',mode='recording',current_fp=NULL,next_fp=NULL WHERE cell='first'"
        )
    mine = cell.as_value(cell.fingerprint("dep-first"))
    stale = cell.as_value({**cell.fingerprint("dep-first"), "public_host": "stale.example.test"})
    conn = await asyncpg.connect(stores["first"])
    try:
        if own_case == "absent":
            await conn.execute("DELETE FROM loom.cell_self")
        else:
            await conn.execute(
                "UPDATE loom.cell_self SET current_fp=$1::jsonb,next_fp=$2::jsonb",
                json.dumps(mine if own_case == "matching" else stale),
                json.dumps(mine) if own_case == "next_matches" else None,
            )
        before = await conn.fetchrow(
            "SELECT current_fp::text AS current,next_fp::text AS next FROM loom.cell_self"
        )
        result = await cell_restore.Restore(
            argparse.Namespace(cell="first", wait=15), ROUTER, cell_restore.Budget(15)
        ).run(OfflineClickHouse())
        assert result["prepare_required"] is (own_case == "absent")
        assert result["configuration_recovery_required"] is (own_case == "mismatching")
        assert result["resume_attach"] is True
        assert (
            await conn.fetchrow(
                "SELECT current_fp::text AS current,next_fp::text AS next FROM loom.cell_self"
            )
            == before
        )
    finally:
        await conn.close(timeout=1)


@pytest.mark.parametrize("failure", ["pair", "type", "claim_shape", "claim_ack"])
async def test_full_restore_claim_refuses_before_any_repair_write(stores, monkeypatch, failure):
    import argparse

    import asyncpg

    from server.cli import cell_control, cell_restore_stores
    from server.settings import settings

    monkeypatch.setattr(settings, "cell", "first")
    monkeypatch.setattr(settings, "pg_dsn", stores["first"])
    monkeypatch.setattr(settings, "document_s3_bucket", "")
    request = cell_control.request
    called = []

    async def change_reply(url, name, transport, path, body=None, *, method="POST"):
        answer = await request(url, name, transport, path, body, method=method)
        if method == "GET" and failure in ("pair", "type"):
            answer = {
                **answer,
                **({"bucket": "different"} if failure == "pair" else {"is_first": 1}),
            }
        if path == "/claim":
            called.append("claim")
            if failure == "claim_shape":
                answer.pop("mutation_generation")
            if failure == "claim_ack":
                raise cell_control._refused("claim acknowledgement lost: stop and rerun")
        return answer

    async def no_migration(conn):
        called.append("migration")

    async def no_nonces(ch, conn):
        called.append("nonces")

    monkeypatch.setattr(cell_control, "request", change_reply)
    monkeypatch.setattr(cell_restore, "_migrate", no_migration)
    monkeypatch.setattr(cell_restore_stores, "nonces", no_nonces)
    ch = OfflineClickHouse()
    conn = await asyncpg.connect(stores["first"])
    try:
        before = await conn.fetchval("SELECT row_to_json(s)::text FROM loom.cell_self s")
        with pytest.raises((ValueError, cell_control.Refused)):
            await cell_restore.Restore(
                argparse.Namespace(cell="first", wait=15), ROUTER, cell_restore.Budget(15)
            ).run(ch)
        assert "migration" not in called and "nonces" not in called and ch.writes == []
        assert await conn.fetchval("SELECT row_to_json(s)::text FROM loom.cell_self s") == before
        assert await conn.fetchval("SELECT count(*) FROM loom.tenancy") == 0
    finally:
        await conn.close(timeout=1)


@pytest.mark.parametrize("moment", ["restart", "final"])
async def test_delayed_prior_claim_invalidates_restore_without_reclaim(stores, monkeypatch, moment):
    import argparse

    from server.cli import cell_control
    from server.settings import settings

    monkeypatch.setattr(settings, "cell", "first")
    monkeypatch.setattr(settings, "pg_dsn", stores["first"])
    monkeypatch.setattr(settings, "document_s3_bucket", "")
    request, copy_rows = cell_control.request, cell_restore.Restore.copy
    stale = None
    own_claims = 0
    delayed = False

    async def observe(url, name, transport, path, body=None, *, method="POST"):
        nonlocal stale, own_claims
        answer = await request(url, name, transport, path, body, method=method)
        if method == "GET" and stale is None:
            stale = answer
        if path == "/claim":
            own_claims += 1
        return answer

    async def delayed_claim(runner):
        nonlocal delayed
        assert await cell_restore.apg.lock_held(runner.conn)
        assert stale is not None
        await request(
            ROUTER, "first", None, "/claim", cell_control.preconditions(stale, claim=True)
        )
        delayed = True

    async def grow(runner, first):
        await copy_rows(runner, first)
        if moment == "restart" and not delayed:
            await delayed_claim(runner)
            await seed(["new-placement"])

    page = cell_restore.Restore.page
    pages = 0

    async def final(runner, after, previous):
        nonlocal pages
        answer = await page(runner, after, previous)
        pages += 1
        # Empty placement traversal's third page is the final acknowledgement.
        if moment == "final" and pages == 3 and not delayed:
            await delayed_claim(runner)
        return answer

    monkeypatch.setattr(cell_control, "request", observe)
    monkeypatch.setattr(cell_restore.Restore, "copy", grow)
    monkeypatch.setattr(cell_restore.Restore, "page", final)
    with pytest.raises(cell_control.Refused, match="generation|registration"):
        await cell_restore.Restore(
            argparse.Namespace(cell="first", wait=15), ROUTER, cell_restore.Budget(15)
        ).run(OfflineClickHouse())
    assert delayed and own_claims == 1


async def test_lost_restore_session_never_reconnects_or_probes(stores, monkeypatch):
    import argparse

    import asyncpg

    from server.cli import cell_control
    from server.core import pg
    from server.settings import settings

    monkeypatch.setattr(settings, "cell", "first")
    monkeypatch.setattr(settings, "pg_dsn", stores["first"])
    monkeypatch.setattr(settings, "document_s3_bucket", "")
    connect, migrate = pg._connect_dedicated, cell_restore._migrate
    opens = 0

    async def count_open(**kw):
        nonlocal opens
        opens += 1
        return await connect(**kw)

    async def lose_backend(conn):
        await migrate(conn)
        observer = await asyncpg.connect(stores["first"])
        try:
            await observer.execute("SELECT pg_terminate_backend($1)", conn.get_server_pid())
        finally:
            await observer.close(timeout=1)

    monkeypatch.setattr(pg, "_connect_dedicated", count_open)
    monkeypatch.setattr(cell_restore, "_migrate", lose_backend)
    ch = OfflineClickHouse()
    with pytest.raises(cell_control.Refused, match="lock session lost|attach lock lost"):
        await cell_restore.Restore(
            argparse.Namespace(cell="first", wait=15), ROUTER, cell_restore.Budget(15)
        ).run(ch)
    assert opens == 1 and ch.writes == []


async def test_normal_restore_requires_owned_public_close_success(stores, monkeypatch):
    import argparse

    import asyncpg

    from server.core import pg
    from server.settings import settings

    monkeypatch.setattr(settings, "cell", "first")
    monkeypatch.setattr(settings, "pg_dsn", stores["first"])
    monkeypatch.setattr(settings, "document_s3_bucket", "")
    connect, close = pg._connect_dedicated, pg.close_owned
    failure = RuntimeError("public close acknowledgement failed")
    owned = {}

    async def remember(**kwargs):
        conn = await connect(**kwargs)
        owned["pid"] = conn.get_server_pid()
        return conn

    async def bad_close(conn, *, end):
        pid = conn.get_server_pid()
        await close(conn, end=end)
        if pid == owned.get("pid"):
            raise failure

    monkeypatch.setattr(pg, "_connect_dedicated", remember)
    monkeypatch.setattr(pg, "close_owned", bad_close)
    with pytest.raises(RuntimeError) as caught:
        await cell_restore.Restore(
            argparse.Namespace(cell="first", wait=15), ROUTER, cell_restore.Budget(15)
        ).run(OfflineClickHouse())
    assert caught.value is failure
    observer = await asyncpg.connect(stores["first"])
    try:
        assert not await observer.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=$1)", owned["pid"]
        )
        assert not await observer.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE pid=$1)", owned["pid"]
        )
    finally:
        await observer.close(timeout=1)
