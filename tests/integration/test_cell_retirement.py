"""Certified retirement and pruning against an owned scratch ClickHouse table."""

from __future__ import annotations

import asyncio
import os
import sys
from uuid import UUID, uuid4

import pytest

from server.core import provider_process, retirement
from server.core.ch import CHClient, query_tag
from server.insights.store import provider_participants as store


@pytest.mark.asyncio
async def test_certified_retirement_survives_old_beat_and_prunes_only_certified_history(
    monkeypatch,
):
    if not os.environ.get("LOOM_CH_HOST"):
        pytest.skip("Set LOOM_CH_HOST to the owned disposable ClickHouse")
    database = "retirement_" + uuid4().hex
    table = database + ".participants"
    client = CHClient()
    await client.connect()
    with query_tag("test.cell_retirement"):
        await client.execute("CREATE DATABASE {database:Identifier}", {"database": database})
        try:
            await client.execute(
                "CREATE TABLE {database:Identifier}.participants AS loom._provider_participants",
                {"database": database},
            )
            old_table = store.TABLE
            for name in (
                "TABLE",
                "PARTICIPANTS_SQL",
                "OWN_VERSION_SQL",
                "_PRUNE_IDS_SQL",
                "_PRUNE_SQL",
            ):
                monkeypatch.setattr(store, name, getattr(store, name).replace(old_table, table))
            monkeypatch.setattr(
                store, "_PRUNE_SQL", store._PRUNE_SQL + " SETTINGS mutations_sync=2"
            )
            stopped, legacy = uuid4(), uuid4()
            process = await asyncio.create_subprocess_exec(sys.executable, "-c", "pass")
            await asyncio.wait_for(process.wait(), timeout=10)
            fields = provider_process.host_proof().split("|")
            fields[4:6] = [str(process.pid), "1"]
            identity = "|".join(fields)
            await client.insert(
                table,
                [
                    {
                        "participant_id": stopped,
                        "state": "running",
                        "version": 1,
                        "host_proof": identity,
                    },
                    {"participant_id": legacy, "state": "retired", "version": 1, "host_proof": ""},
                ],
            )
            assert await store.running_queries(stopped, ch=client) == 0
            proof = retirement.death_proof(stopped, identity, after=1)
            assert proof is not None
            await store.record_retirement(proof, ch=client)
            # A previously in-flight heartbeat cannot outrank the certified final row.
            await client.insert(
                table,
                [
                    {
                        "participant_id": stopped,
                        "state": "running",
                        "version": 2,
                        "host_proof": identity,
                    }
                ],
            )
            rows = await client.query_all(
                f"SELECT participant_id, transition_id, state, version, stale_after_s, host_proof FROM {table} FINAL"
            )
            final = next(row for row in rows if str(row["participant_id"]) == str(stopped))
            assert str(final["transition_id"]) == str(UUID(int=0))
            assert final["state"] == "retired" and final["version"] > 2
            assert final["stale_after_s"] == 0
            assert final["host_proof"] == provider_process.certified(identity)
            assert [str(row["participant_id"]) for row in await store.participants(ch=client)] == [
                str(legacy)
            ]
            # Both are old, but only the certified identity is allowed to disappear.
            await client.execute(
                f"ALTER TABLE {table} UPDATE heartbeat_at=now64(3)-toIntervalHour(25) "
                "WHERE 1 SETTINGS mutations_sync=2"
            )
            assert await store.prune_retired(ch=client) == 1
            rows = await client.query_all(f"SELECT participant_id, state FROM {table} FINAL")
            assert [(str(row["participant_id"]), row["state"]) for row in rows] == [
                (str(legacy), "retired")
            ]
        finally:
            await client.execute("DROP DATABASE {database:Identifier} SYNC", {"database": database})
            await client.disconnect()
