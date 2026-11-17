"""Offline proofs for the fixed cell trigger timing protocol; no fixture is opened."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from bench import cell_trigger_gate as gate


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("unexpected real I/O")

    monkeypatch.setattr(gate.asyncpg, "connect", forbidden)
    monkeypatch.setattr(gate.clickhouse_connect, "get_async_client", forbidden)
    monkeypatch.setattr(gate.httpx.AsyncClient, "send", forbidden)


def args(**overrides):
    return argparse.Namespace(
        **{
            "base_url": "http://127.0.0.1:17932",
            "pg_dsn": "postgresql://u:secret@127.0.0.1:15790/gate_cell",
            "token": "secret-token",
            "org": "org",
            "namespace": "ns",
            **overrides,
        }
    )


def fixture_config():
    config = SimpleNamespace(
        pg_dsn=args().pg_dsn,
        ch_host="127.0.0.1",
        ch_port=18790,
        ch_database="gate_cell",
        ch_telemetry_database="gate_cell_t",
        cell="gate-cell",
        background_workers="off",
        storage_authority="postgres",
        embedding_provider="fake",
        llm_provider="fake",
        build_commit="a" * 40,
        pg_delivery_batch_rows=500,
    )
    config.model_dump = lambda **_: {k: v for k, v in vars(config).items() if k != "model_dump"}
    manifest = gate.Fixture(
        base_url=args().base_url,
        pg_host="127.0.0.1",
        pg_port=15790,
        pg_database="gate_cell",
        ch_host=config.ch_host,
        ch_port=config.ch_port,
        ch_database=config.ch_database,
        ch_telemetry_database=config.ch_telemetry_database,
        cell=config.cell,
        org="org",
        namespace="ns",
        deployment_id="deployment",
        epoch=1,
        instance_id="instance",
        build_commit=config.build_commit,
        source_tree="b" * 40,
        launch_manifest_sha256="c" * 64,
        ownership_receipt_sha256="d" * 64,
        configuration_sha256=gate._digest(config.model_dump()),
        background_workers="off",
        controller_excluded=True,
        pg_internal_port=5432,
    )
    return manifest, config


def test_gate_keeps_the_original_api_sample_contract():
    assert (gate.CALLS, gate.WARMUP, gate.ROUNDS, gate.BATCHES) == (2000, 50, 5, 5)
    assert not hasattr(gate, "_batch") and not hasattr(gate, "_clean")


@pytest.mark.parametrize(("batch", "expected"), [(500, 45500), (1590, 100000)])
def test_complete_fixture_count_uses_the_unchanged_delivery_batch(batch, expected):
    assert gate._planned_keys(batch) == expected


@pytest.mark.parametrize("batch", [1591, 0, -1, 1.5, True])
def test_invalid_batch_refuses_before_loading(batch):
    with pytest.raises(ValueError):
        gate._planned_keys(batch)


def test_arm_order_alternates_pair_order_and_has_five_of_each():
    order = gate._arm_order()
    assert order == (True, False, False, True, True, False, False, True, True, False)
    assert order.count(True) == order.count(False) == 5


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:1234",
        "http://example.com:1234",
        "http://10.0.0.1:1234",
        "http://127.0.0.1",
        "http://127.0.0.1:1234/other",
        "http://u:pw@127.0.0.1:1234",
        "http://127.0.0.1:1234?x=1",
        "http://127.0.0.1:1234#fragment",
    ],
)
def test_http_endpoint_refuses_ambiguous_or_remote_targets(url):
    with pytest.raises(gate.Invalid):
        gate._endpoint(url)


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://u@localhost:15790/scratch",
        "postgresql://u@127.0.0.1:15790/loom",
        "postgresql://u@127.0.0.1:15790/scratch?host=remote",
        "postgresql:///scratch?service=other",
        "postgresql://u@127.0.0.1,127.0.0.2:15790/scratch",
        "postgresql://u@127.0.0.1:15790/scratch?options=-cdefault_transaction_read_only=off",
    ],
)
def test_pg_endpoint_refuses_overrides_defaults_and_alternate_targets(dsn):
    with pytest.raises(gate.Invalid):
        gate._endpoint(dsn, pg=True)


def test_manifest_binds_published_port_without_conflating_internal_port():
    manifest, config = fixture_config()
    assert manifest.pg_port == 15790 and manifest.pg_internal_port == 5432
    assert manifest.validate(args(), config) == 45500
    with pytest.raises(gate.Invalid, match="manifest PG endpoint"):
        dataclasses.replace(manifest, pg_port=5432).validate(args(), config)


@pytest.mark.parametrize(
    "change",
    [
        {"ch_host": "10.0.0.1"},
        {"ch_database": "loom"},
        {"cell": "other"},
        {"background_workers": "on"},
        {"llm_provider": "openai"},
        {"embedding_provider": "local"},
        {"pg_delivery_batch_rows": 1591},
    ],
)
def test_configuration_drift_and_unsafe_settings_refuse(change):
    manifest, config = fixture_config()
    for k, v in change.items():
        setattr(config, k, v)
    with pytest.raises(gate.Invalid):
        manifest.validate(args(), config)


def test_artifact_never_overwrites_existing_file_or_symlink(tmp_path):
    original = tmp_path / "old.jsonl"
    original.write_text("preserve")
    with pytest.raises(FileExistsError):
        gate.Artifact(str(original))
    link = tmp_path / "link"
    link.symlink_to(original)
    with pytest.raises(FileExistsError):
        gate.Artifact(str(link))
    assert original.read_text() == "preserve"
    fresh = tmp_path / "fresh"
    artifact = gate.Artifact(str(fresh))
    artifact.emit("test", n=1)
    artifact.close()
    assert fresh.stat().st_mode & 0o777 == 0o600
    assert json.loads(fresh.read_text()) == {"kind": "test", "n": 1}


@pytest.mark.asyncio
async def test_actual_api_loop_sends_unique_four_kib_values_and_full_sample(monkeypatch):
    sent, outcomes = [], []

    class Client:
        def __init__(self, **kw):
            assert kw["follow_redirects"] is False and kw["trust_env"] is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, url, *, json):
            sent.append(json)
            return httpx.Response(
                200,
                json={
                    "memory_id": str(uuid.uuid4()),
                    "version": len(sent),
                    "admission": "ready",
                    "status": "active",
                    "content_sha256": gate.sha256_value(json["value"]),
                    "memory_key": json["memory_key"],
                    "namespace": json["namespace"],
                },
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(gate.httpx, "AsyncClient", Client)
    rows = await gate._memory_set(
        args(), emit=lambda kind, **fields: outcomes.append((kind, fields))
    )
    assert len(sent) == len(rows) == len(outcomes) == 2050
    assert (
        len({body["memory_key"] for body in sent}) == len({body["value"] for body in sent}) == 2050
    )
    assert all(len(body["value"].encode("utf-8")) == 4096 for body in sent)
    assert sum(row.measured for row in rows) == 2000
    assert all("value" not in fields and "token" not in fields for _, fields in outcomes)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,changes",
    [
        (302, {}),
        (503, {}),
        (200, {"status": "duplicate"}),
        (200, {"noop": True}),
        (200, {"content_sha256": "wrong"}),
        (200, {"version": 0}),
    ],
)
async def test_api_refusal_is_recorded_once_and_never_enters_samples(monkeypatch, status, changes):
    events = []

    class Client:
        def __init__(self, **_):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, url, *, json):
            body = {
                "memory_id": str(uuid.uuid4()),
                "version": 1,
                "status": "active",
                "admission": "ready",
                "memory_key": json["memory_key"],
                "namespace": json["namespace"],
                "content_sha256": gate.sha256_value(json["value"]),
                **changes,
            }
            return httpx.Response(status, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(gate.httpx, "AsyncClient", Client)
    with pytest.raises(gate.Invalid):
        await gate._memory_set(args(), emit=lambda *a, **kw: events.append((a, kw)))
    assert len(events) == 1 and events[0][0] == ("request_error",)
    assert "secret-token" not in str(events) and "value" not in events[0][1]


def samples(on_p99, off_p99, on_rps=90.1, off_rps=100):
    on, off = iter(on_p99), iter(off_p99)
    return [
        gate.Samples(arm, [next(on) if arm else next(off)] * gate.CALLS, on_rps if arm else off_rps)
        for arm in gate._arm_order()
    ]


def test_statistics_use_difference_of_arm_medians_and_throughput_not_time():
    result = gate._statistics(samples([2, 12, 22, 32, 102], [1, 6, 51, 61, 71]))
    assert result["p99_delta_ms"] == -29
    assert result["paired_p99_differences_ms"] == [1, 6, -29, -29, 31]
    assert result["latency_within_limit"] is True
    # Time increases 10.99%, but the original rows/second gate passes at 9.9% loss.
    assert result["throughput_loss"] == pytest.approx(0.099)
    assert result["throughput_within_limit"] is True
    failed = gate._statistics(samples([20] * 5, [1] * 5, 89, 100))
    assert failed["latency_within_limit"] is failed["throughput_within_limit"] is False
    assert gate._quantile(list(range(2000)), 0.99) == 1979


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_invalid_throughput_cannot_become_passing_number(value):
    with pytest.raises(gate.Invalid):
        gate._statistics(samples([1] * 5, [1] * 5, value))


@pytest.mark.asyncio
async def test_actual_production_worker_body_runs_five_times_with_unchanged_batch(monkeypatch):
    from server.core import idempotency, pg
    from server.llm import customer
    from server.memory.app import pg_delivery as publish
    from server.memory.store import pg_memory
    from server.memory.workers import pg_delivery as worker
    from server.settings import settings

    monkeypatch.setattr(settings, "pg_delivery_batch_rows", 2)
    monkeypatch.setattr(pg, "enabled", lambda: True)
    monkeypatch.setattr(worker, "_read_backlog", AsyncMock())
    claim = AsyncMock(return_value=[object(), object()])
    monkeypatch.setattr(pg_memory, "claim_due", claim)
    attribution = []

    async def publish_claims(claims):
        attribution.append(customer._attribution.get())
        assert len(claims) == 2
        return 2, 0

    monkeypatch.setattr(publish, "publish_claims", publish_claims)
    for module, name in (
        (idempotency, "sweep_expired"),
        (pg_memory, "sweep_erasure_evidence"),
        (pg_memory, "sweep_settled"),
        (publish, "publish_audit_outbox"),
    ):
        monkeypatch.setattr(module, name, AsyncMock(return_value=0))
    times = iter([0, 2_000_000_000])
    monkeypatch.setattr(gate.time, "perf_counter_ns", lambda: next(times))
    events = []
    assert (
        await gate._delivery(args(), 2, emit=lambda kind, **kw: events.append((kind, kw)), arm=0)
        == 5
    )
    assert claim.await_count == 5 and all(call.args == (2,) for call in claim.await_args_list)
    assert len(attribution) == 5
    assert all(
        a.org == "org" and a.namespace == "ns" and a.subject == gate.SUBJECT for a in attribution
    )
    assert [kind for kind, _ in events] == ["worker"] * 5 + ["delivery_block"]
    assert customer._attribution.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        {"claimed": 1, "published": 1},
        {"claimed": 2, "published": 1},
        {"claimed": 2, "published": 2, "failed": 1},
    ],
)
async def test_worker_count_failure_invalidates_block_without_retries(monkeypatch, result):
    from server.memory.workers import pg_delivery as worker
    from server.settings import settings

    monkeypatch.setattr(settings, "pg_delivery_batch_rows", 2)
    run = AsyncMock(return_value=worker.DeliveryResult(**result))
    monkeypatch.setattr(worker, "run_pass", run)
    with pytest.raises(gate.Invalid):
        await gate._delivery(args(), 2, emit=lambda *_a, **_kw: None, arm=0)
    assert run.await_count == 1


@pytest.mark.asyncio
async def test_canonical_full_rows_audits_and_expected_vectors_use_real_fake_embedder(monkeypatch):
    import server.embedding
    from server.embedding.fake import FakeNGramEmbedder
    from server.llm import customer
    from server.memory.store import pg_memory
    from server.memory.store.rows import MEMORY_PERSISTED_COLS

    embedder = FakeNGramEmbedder(dim=16)
    monkeypatch.setattr(server.embedding, "get_embedder", lambda: embedder)
    # Keep actual require(): refresh its real switch read through a fake store edge.
    import logging

    from server.core.polled_copy import PolledCopy

    monkeypatch.setattr(
        customer,
        "_switch",
        PolledCopy("switch", AsyncMock(return_value=frozenset()), logging.getLogger(__name__)),
    )
    block = await gate._build_block(args(), 1)
    assert len(block.rows) == len(block.audits) == len(block.documents) == 5
    for row, audit, doc in zip(block.rows, block.audits, block.documents, strict=True):
        assert set(MEMORY_PERSISTED_COLS) <= row.keys()
        assert len(json.loads(row["value"]).encode()) == 4096
        assert row["actor"] == gate.SUBJECT and row["source_class"] == "asserted"
        assert row["content_sha256"] == gate.sha256_value(json.loads(row["value"]))
        assert audit["event_type"] == "memory.revision" and len(audit["row_hash"]) == 64
        assert row["value"] not in audit["subject"]
        assert len(doc["embedding"]) == doc["embedding_dim"] == 16
        assert doc["content_sha256"] == row["content_sha256"]
        assert pg_memory.decode_row(pg_memory.encode_row(row)) == row
    assert customer._attribution.get() is None


@pytest.mark.asyncio
async def test_stage_calls_real_commit_interface_with_due_fresh_targets(monkeypatch):
    from server.memory.store import pg_memory

    block = gate.Block(
        [{"memory_id": str(uuid.uuid4()), "org": "org", "namespace": "ns", "memory_key": "key"}],
        [{"row_hash": "hash"}],
        [],
    )
    debt = AsyncMock()
    absent = AsyncMock()
    monkeypatch.setattr(gate, "_debt", debt)
    monkeypatch.setattr(gate, "_absent", absent)
    committed = pg_memory.Committed(
        rows=block.rows, audits=block.audits, seqs=[("org", "ns", 1)], targets=[7]
    )
    commit = AsyncMock(return_value=committed)
    monkeypatch.setattr(pg_memory, "commit_versions", commit)
    conn = SimpleNamespace(fetchval=AsyncMock(side_effect=[0, 1]))
    await gate._stage(conn, object(), block)
    assert commit.await_args.kwargs == {"targets": 7, "publisher_lease": False}
    assert block.seqs == [("org", "ns", 1)]
    assert [call.args[1] for call in debt.await_args_list] == [0, 1]
    absent.assert_awaited_once()


@pytest.mark.parametrize(
    ("field", "expected", "actual"),
    [
        ("value", "right", "wrong"),
        ("version", 2, 1),
        ("content_sha256", "a", "b"),
        ("embedding", [1.0, 0.0], [0.0, 1.0]),
        ("embedding", [1.0], []),
        ("row_hash", "good", "bad"),
    ],
)
def test_stored_payload_vector_version_and_audit_mismatch_refuse(field, expected, actual):
    with pytest.raises(gate.Invalid):
        gate._compare({field: expected}, {field: actual}, [field], "stored")


class TriggerConn:
    def __init__(self):
        self.on = True
        self.drift = False
        self.statements = []

    @asynccontextmanager
    async def transaction(self):
        yield

    async def execute(self, sql):
        self.statements.append(sql)
        if sql.startswith("DROP TRIGGER"):
            self.on = False
        elif sql.startswith("CREATE TRIGGER"):
            self.on = True


@pytest.mark.asyncio
async def test_captured_trigger_state_allows_off_to_off_and_restores_exact_tables(monkeypatch):
    conn = TriggerConn()

    async def snapshot(c):
        return (
            ("memory_current", "tenancy"),
            "drift" if c.drift else "invariant",
            "org" if c.on else gate._digest([]),
        )

    monkeypatch.setattr(gate.Triggers, "snapshot", staticmethod(snapshot))
    monkeypatch.setattr(gate.cell_triggers, "missing", AsyncMock(return_value=[]))
    triggers = await gate.Triggers.capture(conn)
    await triggers.set(conn, False)
    await triggers.set(conn, False)
    assert triggers.on is False
    await triggers.restore(conn)
    assert triggers.on is True
    assert conn.statements and all('ON loom."memory_current"' in sql for sql in conn.statements)
    conn.drift = True
    with pytest.raises(gate.Invalid, match="drift"):
        await triggers.set(conn, False)


class ProofPG:
    def __init__(self, fault=""):
        self.fault = fault
        self.closed = False
        self.sql = []

    async def fetchrow(self, sql, *params):
        self.sql.append(sql)
        if "current_database()" in sql:
            return {
                "db": "wrong" if self.fault == "database" else "gate_cell",
                "port": 5432,
                "recovery": self.fault == "recovery",
            }
        if "FROM loom.authority" in sql:
            return {
                "authority": "postgres",
                "deployment_id": "wrong" if self.fault == "pair" else "deployment",
                "epoch": 1,
            }
        if "FROM loom.cell_first" in sql:
            return None if self.fault == "partial" else {"cell": "gate-cell", "is_first": False}
        if "FROM loom.cell_self" in sql:
            return {
                "current_fp": json.dumps({"v": 1, "fields": {}}),
                "next_fp": {} if self.fault == "transition" else None,
            }
        if "AS pending" in sql:
            return {"pending": int(self.fault == "debt"), "audits": 0, "errors": 0}
        raise AssertionError(sql)

    async def fetchval(self, sql, *params):
        self.sql.append(sql)
        if "FROM loom.tenancy_mode" in sql:
            return "recording" if self.fault == "recording" else "enforced"
        if "FROM loom.tenancy WHERE" in sql:
            return self.fault != "held"
        raise AssertionError(sql)

    async def close(self, **kwargs):
        self.closed = True


class ProofCH:
    def __init__(self, fault=""):
        self.fault = fault
        self.closed = False
        self.sql = []

    async def query_all(self, sql, parameters=None):
        self.sql.append(sql)
        if "currentDatabase()" in sql:
            return [{"db": "wrong" if self.fault == "ch_database" else "gate_cell"}]
        if "FROM loom.storage_authority" in sql:
            return [
                {
                    "authority": "postgres",
                    "deployment_id": "wrong" if self.fault == "ch_pair" else "deployment",
                    "epoch": 1,
                }
            ]
        if "FROM loom.provider_configuration" in sql:
            return [{"n": int(self.fault == "saved_provider")}]
        if "FROM loom.runtime_overrides" in sql:
            return [{"n": int(self.fault == "overrides")}]
        if "FROM loom.cell_heartbeats" in sql:
            return [
                {
                    "cell": "gate-cell",
                    "tenancy_mode": "enforced",
                    "fingerprint": json.dumps(
                        {"serves": True, "marked": self.fault != "fingerprint"}
                    ),
                }
            ]
        raise AssertionError(sql)

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "database",
        "recovery",
        "pair",
        "partial",
        "recording",
        "held",
        "transition",
        "debt",
        "ch_database",
        "ch_pair",
        "saved_provider",
        "overrides",
        "fingerprint",
        "health",
        "competing_worker",
    ],
)
async def test_read_only_preflight_faults_refuse_before_native_setup(monkeypatch, fault):
    import server.settings
    from server.cli import _ch
    from server.core import cell, instances

    fixture, config = fixture_config()
    pg, ch = ProofPG(fault), ProofCH(fault)
    connections = []

    async def connect(dsn, **kwargs):
        connections.append(kwargs)
        assert kwargs["server_settings"] == {"default_transaction_read_only": "on"}
        return pg

    async def close(conn):
        await conn.close()

    async def live(*_args, **_kw):
        return (
            []
            if fault == "competing_worker"
            else [{"instance_id": "instance", "build_commit": "a" * 40, "workers_lease": ""}]
        )

    async def health(*_args):
        if fault == "health":
            raise gate.Invalid("health source")

    native = AsyncMock(side_effect=AssertionError("native setup before proof"))
    monkeypatch.setattr(server.settings, "settings", config)
    monkeypatch.setattr(gate.asyncpg, "connect", connect)
    monkeypatch.setattr(gate.ReadOnlyCH, "open", AsyncMock(return_value=ch))
    monkeypatch.setattr(gate, "_close_pg", close)
    monkeypatch.setattr(gate, "_health", health)
    monkeypatch.setattr(gate, "_source", lambda _: {"clean": True})
    monkeypatch.setattr(instances, "read_live", live)
    monkeypatch.setattr(cell, "mine", lambda: {})
    monkeypatch.setattr(cell, "matches", lambda mine, value: value is not None)
    monkeypatch.setattr(_ch, "run_connected", native)
    with pytest.raises(gate.Invalid):
        await gate._preflight(args(), fixture)
    assert pg.closed and ch.closed
    native.assert_not_called()
    assert connections and all(sql.startswith("SELECT ") for sql in pg.sql + ch.sql)
    assert not any("model_transitions" in sql for sql in ch.sql)


@pytest.mark.asyncio
async def test_readonly_ch_startup_restricts_connection_before_first_query(monkeypatch):
    client = SimpleNamespace(close=AsyncMock())
    opened = AsyncMock(return_value=client)
    monkeypatch.setattr(gate.clickhouse_connect, "get_async_client", opened)
    ch = await gate.ReadOnlyCH.open()
    assert opened.await_args.kwargs["settings"]["readonly"] == 1
    await ch.close()
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_health_never_follows_redirects(monkeypatch):
    fixture, _ = fixture_config()

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url):
            return httpx.Response(
                302,
                headers={"Location": "http://external.invalid"},
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr(gate.httpx, "AsyncClient", Client)
    with pytest.raises(gate.Invalid, match="health HTTP status"):
        await gate._health(args(), fixture)


@pytest.mark.asyncio
async def test_restore_preserves_primary_failure_and_survives_second_cancellation(monkeypatch):
    from server.settings import settings

    fixture, _ = fixture_config()
    conn = ProofPG()
    restoring, release = asyncio.Event(), asyncio.Event()
    failure = ValueError("primary-secret")
    events = []

    class T:
        tables = ("memory_current",)
        invariant = org = "digest"

        async def set(self, *_):
            pass

    async def restore(*_):
        restoring.set()
        await release.wait()

    async def close(c):
        c.closed = True

    monkeypatch.setattr(gate.asyncpg, "connect", AsyncMock(return_value=conn))
    monkeypatch.setattr(gate.Triggers, "capture", AsyncMock(return_value=T()))
    monkeypatch.setattr(gate, "_proof", AsyncMock(return_value={}))
    monkeypatch.setattr(gate, "_memory_set", AsyncMock(side_effect=failure))
    monkeypatch.setattr(gate, "_restore", restore)
    monkeypatch.setattr(gate, "_close_pg", close)
    artifact = SimpleNamespace(emit=lambda kind, **kw: events.append(kind))
    task = asyncio.create_task(gate._run(args(), fixture, artifact, object()))
    await restoring.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    release.set()
    with pytest.raises(ValueError) as raised:
        await task
    assert raised.value is failure and conn.closed
    assert "triggers_restored" in events
    assert settings.pg_delivery_batch_rows > 0


@pytest.mark.parametrize("cleanup_failure", [False, True])
def test_final_verdict_follows_native_cleanup_and_artifact_close(
    monkeypatch, tmp_path, capsys, cleanup_failure
):
    import sys

    import server.settings
    import server.wiring
    from server.cli import _ch

    fixture, config = fixture_config()
    manifest = tmp_path / "manifest"
    manifest.write_text(json.dumps(dataclasses.asdict(fixture)))
    output = tmp_path / "result.jsonl"
    arguments = args()
    argv = ["driver"]
    for name, value in {
        **vars(arguments),
        "fixture_manifest": str(manifest),
        "output": str(output),
    }.items():
        argv += ["--" + name.replace("_", "-"), str(value)]
    order = []
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(server.settings, "settings", config)
    monkeypatch.setattr(server.wiring, "wire", lambda: order.append("wire"))
    monkeypatch.setattr(gate, "_preflight", AsyncMock())
    monkeypatch.setattr(gate, "_source", lambda _: {"clean": True})

    def native(*_a, **_kw):
        order.append("native_cleanup")
        return {
            "latency_within_limit": True,
            "throughput_within_limit": True,
            "p99_delta_ms": 0.0,
            "throughput_loss": 0.0,
        }

    monkeypatch.setattr(_ch, "run_connected", native)

    def sync(_fd):
        assert capsys.readouterr().out == ""
        assert order == ["wire", "native_cleanup"]
        if cleanup_failure:
            raise OSError("secret-token")

    monkeypatch.setattr(gate.os, "fsync", sync)
    assert gate.main() == int(cleanup_failure)
    final = capsys.readouterr().out
    assert "secret-token" not in final
    assert json.loads(final)["verdict"] == ("INVALID" if cleanup_failure else "PASS")
    assert '"verdict": "PASS"' not in output.read_text()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "",
        "projection_value",
        "projection_version",
        "vector",
        "audit_hash",
        "audit_subject",
        "missing_document",
        "targets",
        "attempts",
        "head",
    ],
)
async def test_composed_stored_verification_compares_authority_projection_vectors_and_audits(
    monkeypatch, fault
):
    import logging

    import server.embedding
    from server.core.polled_copy import PolledCopy
    from server.embedding.fake import FakeNGramEmbedder
    from server.llm import customer
    from server.memory.store import pg_memory

    monkeypatch.setattr(
        customer,
        "_switch",
        PolledCopy("switch", AsyncMock(return_value=frozenset()), logging.getLogger(__name__)),
    )
    monkeypatch.setattr(server.embedding, "get_embedder", lambda: FakeNGramEmbedder(dim=16))
    block = await gate._build_block(args(), 1)
    block.seqs = [("org", "ns", i + 1) for i in range(5)]
    records = [
        {
            "head": json.dumps(pg_memory.encode_row(row)),
            "revision": json.dumps(pg_memory.encode_row(row)),
            "audit": json.dumps(pg_memory.encode_audit(audit)),
            "seq": i + 1,
            "targets": 1 if fault == "targets" else 0,
            "attempts": 1 if fault == "attempts" else 0,
            "last_error": "",
        }
        for i, (row, audit) in enumerate(zip(block.rows, block.audits, strict=True))
    ]
    projections = [dict(row) for row in block.rows]
    documents = [dict(row) for row in block.documents]
    audits = [dict(row) for row in block.audits]
    if fault == "projection_value":
        projections[0]["value"] = '"wrong"'
    if fault == "projection_version":
        projections[0]["version"] -= 1
    if fault == "vector":
        documents[0]["embedding"] = [0.0] * 16
    if fault == "audit_hash":
        audits[0]["row_hash"] = "bad"
    if fault == "audit_subject":
        audits[0]["subject"] = "wrong"
    if fault == "missing_document":
        documents.pop()
    if fault == "head":
        records.pop()
    conn = SimpleNamespace(
        fetch=AsyncMock(return_value=records),
        fetchrow=AsyncMock(return_value={"pending": 0, "audits": 0, "errors": 0}),
    )

    async def query(sql, params):
        if "FROM loom.memory FINAL" in sql:
            return projections
        if "FROM loom.memory_embeddings FINAL" in sql:
            return documents
        if "FROM loom.audit_log" in sql:
            return audits
        raise AssertionError(sql)

    ch = SimpleNamespace(query_all=query)
    if fault:
        with pytest.raises(gate.Invalid):
            await gate._verify(conn, ch, args(), block)
    else:
        await gate._verify(conn, ch, args(), block)
    assert all(
        "org=" in call.args[0] and "namespace=" in call.args[0]
        for call in conn.fetch.await_args_list
    )


@pytest.mark.asyncio
async def test_real_customer_policy_can_refuse_expected_vectors_without_bypass(monkeypatch):
    import logging

    import server.embedding
    from server.core.polled_copy import PolledCopy
    from server.embedding.fake import FakeNGramEmbedder
    from server.llm import customer

    monkeypatch.setattr(
        customer,
        "_switch",
        PolledCopy(
            "switch", AsyncMock(return_value=frozenset({"org"})), logging.getLogger(__name__)
        ),
    )
    monkeypatch.setattr(server.embedding, "get_embedder", lambda: FakeNGramEmbedder(dim=16))
    with pytest.raises(customer.ModelCallsDisabled):
        await gate._build_block(args(), 1)
    assert customer._attribution.get() is None


@pytest.mark.asyncio
async def test_partial_trigger_toggle_is_restored_on_original_failure(monkeypatch):
    conn = TriggerConn()
    original = ValueError("DDL failed")
    writes = 0

    async def execute(sql):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise original
        conn.on = sql.startswith("CREATE TRIGGER")

    async def snapshot(c):
        return ("memory_current", "tenancy"), "invariant", "org" if c.on else gate._digest([])

    monkeypatch.setattr(conn, "execute", execute)
    monkeypatch.setattr(gate.Triggers, "snapshot", staticmethod(snapshot))
    monkeypatch.setattr(gate.cell_triggers, "missing", AsyncMock(return_value=[]))
    triggers = await gate.Triggers.capture(conn)
    with pytest.raises(ValueError) as raised:
        try:
            await triggers.set(conn, False)
        finally:
            await triggers.restore(conn)
    assert raised.value is original and conn.on and triggers.on


def test_manifest_unknown_fields_and_credentials_refuse(tmp_path):
    fixture, _ = fixture_config()
    path = tmp_path / "manifest"
    path.write_text(json.dumps({**dataclasses.asdict(fixture), "password": "secret"}))
    with pytest.raises(gate.Invalid, match="fixture manifest"):
        gate.Fixture.read(str(path))


@pytest.mark.parametrize("fault", ["", "head", "tree", "dirty", "refused"])
def test_source_provenance_uses_only_fixed_bounded_readonly_git(monkeypatch, fault):
    fixture, _ = fixture_config()
    calls = []

    def git(argv, **kwargs):
        calls.append(argv)
        assert argv[0] == "git" and kwargs["timeout"] == 5 and kwargs["check"] is False
        assert kwargs["capture_output"] is True and kwargs["cwd"].name == "scale-attachcli"
        assert "shell" not in kwargs
        if argv[1:] == ["rev-parse", "HEAD"]:
            value = "wrong" if fault == "head" else fixture.build_commit
        elif argv[1:] == ["rev-parse", "HEAD^{tree}"]:
            value = "wrong" if fault == "tree" else fixture.source_tree
        else:
            assert argv[1:] == ["status", "--porcelain"]
            value = " M file" if fault == "dirty" else ""
        return SimpleNamespace(returncode=int(fault == "refused"), stdout=value)

    monkeypatch.setattr(gate.subprocess, "run", git)
    if fault:
        with pytest.raises(gate.Invalid):
            gate._source(fixture)
    else:
        assert gate._source(fixture) == {
            "head": fixture.build_commit,
            "tree": fixture.source_tree,
            "clean": True,
        }
        assert len(calls) == 3


@pytest.mark.asyncio
async def test_restoration_has_finite_deadline_and_closes_owned_connection(monkeypatch):
    conn = ProofPG()

    class Stuck:
        async def restore(self, _):
            await asyncio.Event().wait()

    monkeypatch.setattr(gate, "RESTORE_S", 0.01)

    async def close(owned):
        assert owned is conn
        await conn.close()

    monkeypatch.setattr(gate, "_close_pg", close)
    artifact = SimpleNamespace(emit=lambda *_a, **_kw: None)
    with pytest.raises(TimeoutError):
        await gate._cleanup(Stuck(), conn, artifact)
    assert conn.closed
