"""Measure API p99 and real delivery throughput with cell org triggers on and off.

Requires an independently owned, attached scratch fixture and its source/ownership
manifest. The outer fixture owner stops its processes and drops its exact databases.
This driver restores triggers but never deletes fixture rows or changes providers.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import ipaddress
import json
import math
import os
import re
import socket
import stat
import statistics
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, TextIO, cast
from urllib.parse import unquote, urlsplit

import asyncpg
import clickhouse_connect
import httpx

from server.core import cell_triggers
from server.core.hashing import hash_text, sha256_value

CALLS, WARMUP, ROUNDS, BATCHES = 2000, 50, 5, 5
VALUE_BYTES, KEY_CEILING = 4096, 100_000
IO_S, SETTLE_S, RESTORE_S = 30.0, 60.0, 15.0
SUBJECT = "process:benchmark"
_SHA = re.compile(r"[a-f0-9]{64}")
_DB = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
_DEFAULT_DBS = {
    "loom",
    "loom_router",
    "loom_telemetry",
    "default",
    "system",
    "postgres",
    "template0",
    "template1",
    "information_schema",
}


class Invalid(ValueError):
    """The fixture or a sample did not meet the declared measurement contract."""


def _require(ok: object, field: str) -> None:
    if not ok:
        raise Invalid(field)


def _planned_keys(batch: int) -> int:
    _require(type(batch) is int and batch > 0, "delivery batch")
    count = 2 * ROUNDS * (CALLS + WARMUP + BATCHES * batch)
    _require(count <= KEY_CEILING, "100000 key ceiling")
    return count


def _arm_order() -> tuple[bool, ...]:
    return tuple(
        on for pair in range(ROUNDS) for on in ((True, False) if pair % 2 == 0 else (False, True))
    )


def _payload(key: str) -> str:
    prefix = f"Cell trigger measurement {key}: "
    _require(len(prefix) < VALUE_BYTES and prefix.isascii(), "payload key")
    return prefix + ("abcdefghijklmnopqrstuvwxyz " * VALUE_BYTES)[: VALUE_BYTES - len(prefix)]


def _endpoint(value: str, *, pg: bool = False) -> tuple[str, str, int, str]:
    """Validate the literal target without resolving names or accepting DSN overrides."""
    try:
        u = urlsplit(value)
        host, port = u.hostname, u.port
        _require(
            u.scheme in ({"postgres", "postgresql"} if pg else {"http", "https"}), "endpoint scheme"
        )
        _require(
            host and ipaddress.ip_address(host).is_loopback and port and 0 < port < 65536,
            "numeric loopback endpoint",
        )
        _require(not u.query and not u.fragment, "endpoint overrides")
        if not pg:
            _require(not u.username and not u.password and u.path in ("", "/"), "HTTP endpoint")
        database = unquote(u.path.removeprefix("/")) if pg else ""
        if pg:
            _scratch(database)
        return u.scheme, str(ipaddress.ip_address(str(host))), int(port or 0), database
    except (ValueError, TypeError):
        raise Invalid("invalid local endpoint") from None


def _scratch(value: str) -> None:
    _require(
        bool(_DB.fullmatch(value)) and value.lower() not in _DEFAULT_DBS,
        "explicit scratch database",
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass(frozen=True)
class Fixture:
    """Nonsecret receipt from the owner that created and launched this exact fixture."""

    base_url: str
    pg_host: str
    pg_port: int
    pg_database: str
    ch_host: str
    ch_port: int
    ch_database: str
    ch_telemetry_database: str
    cell: str
    org: str
    namespace: str
    deployment_id: str
    epoch: int
    instance_id: str
    build_commit: str
    source_tree: str
    launch_manifest_sha256: str
    ownership_receipt_sha256: str
    configuration_sha256: str
    background_workers: str
    controller_excluded: bool
    pg_internal_port: int | None = None

    @classmethod
    def read(cls, path: str) -> Fixture:
        try:
            with Path(path).open("rb") as manifest_file:
                raw = manifest_file.read(16_385)
            _require(len(raw) <= 16_384, "manifest size")
            return cls(**json.loads(raw))
        except (TypeError, ValueError, OSError):
            raise Invalid("fixture manifest") from None

    def validate(self, args: argparse.Namespace, config: Any) -> int:
        supplied = _endpoint(args.pg_dsn, pg=True)
        configured = _endpoint(config.pg_dsn, pg=True)
        _require(supplied == configured, "configured PG endpoint")
        _require(
            supplied[1:] == (self.pg_host, self.pg_port, self.pg_database), "manifest PG endpoint"
        )
        _require(_endpoint(args.base_url) == _endpoint(self.base_url), "manifest HTTP endpoint")
        for host, port in ((self.ch_host, self.ch_port), (config.ch_host, config.ch_port)):
            _endpoint(f"http://[{host}]:{port}" if ":" in host else f"http://{host}:{port}")
        _require(
            (self.ch_host, self.ch_port) == (config.ch_host, config.ch_port), "manifest CH endpoint"
        )
        for name in (self.pg_database, self.ch_database, self.ch_telemetry_database):
            _scratch(name)
        _require(
            (self.ch_database, self.ch_telemetry_database)
            == (config.ch_database, config.ch_telemetry_database),
            "manifest CH aliases",
        )
        _require(
            (self.org, self.namespace, self.cell) == (args.org, args.namespace, config.cell),
            "manifest identity",
        )
        _require(
            all((self.org, self.namespace, self.cell, self.instance_id, self.deployment_id)),
            "manifest identity fields",
        )
        _require(
            self.background_workers == config.background_workers == "off"
            and self.controller_excluded is True,
            "exclusive fixture",
        )
        _require(
            config.storage_authority == "postgres"
            and config.embedding_provider == "fake"
            and config.llm_provider == "fake",
            "explicit fake providers",
        )
        _require(
            self.build_commit == config.build_commit
            and bool(re.fullmatch(r"[a-f0-9]{40}", self.build_commit)),
            "source build",
        )
        _require(bool(re.fullmatch(r"[a-f0-9]{40}", self.source_tree)), "source tree")
        for value in (
            self.launch_manifest_sha256,
            self.ownership_receipt_sha256,
            self.configuration_sha256,
        ):
            _require(bool(_SHA.fullmatch(value)), "fixture receipt digest")
        _require(
            self.configuration_sha256 == _digest(_configuration(config)), "fixture configuration"
        )
        _require(type(self.epoch) is int and self.epoch > 0, "authority epoch")
        _require(
            self.pg_internal_port is None
            or type(self.pg_internal_port) is int
            and 0 < self.pg_internal_port < 65536,
            "internal PG port",
        )
        return _planned_keys(config.pg_delivery_batch_rows)


def _configuration(config: Any) -> dict[str, Any]:
    # A complete digest catches a setting change without publishing credentials.
    return cast(dict[str, Any], config.model_dump(mode="json"))


def _source(fixture: Fixture) -> dict[str, Any]:
    """Fixed read-only Git commands bind the launched build to this clean checkout."""
    root = Path(__file__).resolve().parents[1]
    values = []
    for command in (("rev-parse", "HEAD"), ("rev-parse", "HEAD^{tree}"), ("status", "--porcelain")):
        result = subprocess.run(
            ["git", *command], cwd=root, capture_output=True, text=True, timeout=5, check=False
        )
        _require(result.returncode == 0, "source Git read")
        _require(len(result.stdout) <= 65_536, "source status size")
        values.append(result.stdout.strip())
    head, tree, status = values
    _require(
        head == fixture.build_commit and tree == fixture.source_tree and not status,
        "clean source head/tree",
    )
    return {"head": head, "tree": tree, "clean": True}


_PUBLIC_WORKLOAD = frozenset(
    {
        "storage_authority",
        "background_workers",
        "embedding_provider",
        "embedding_model",
        "embedding_dim",
        "embedding_send_dimensions",
        "embedding_worker_enabled",
        "embedding_cache_shared",
        "embedding_cache_shared_max_chars",
        "embedding_cache_size",
        "embedding_cache_ttl_s",
        "llm_provider",
        "llm_model",
    }
)


def _workload(config: Any) -> dict[str, Any]:
    return {
        key: value
        for key, value in _configuration(config).items()
        if (key.startswith("pg_") and key != "pg_dsn")
        or key.startswith("memory_conflict")
        or key in _PUBLIC_WORKLOAD
    }


def _receipt(path: str) -> tuple[dict[str, Any], str]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        metadata = os.fstat(source.fileno())
        _require(
            stat.S_ISREG(metadata.st_mode) and metadata.st_uid == os.geteuid(),
            "owned regular receipt",
        )
        raw = source.read(65_537)
    _require(len(raw) <= 65_536, "receipt size")
    value = json.loads(raw)
    _require(isinstance(value, dict), "receipt object")
    return value, hashlib.sha256(raw).hexdigest()


def _owned_server(server: dict[str, Any]) -> None:
    from server.core.provider_process import _start_ticks

    _require(
        set(server)
        == {"pid", "start_ticks", "boot_id", "uid", "cwd", "health_instance", "instance_id"},
        "launch server fields",
    )
    pid = server["pid"]
    _require(
        type(pid) is int
        and pid > 1
        and type(server["uid"]) is int
        and server["uid"] == os.geteuid(),
        "launch process ownership",
    )
    _require(
        isinstance(server["start_ticks"], str) and server["start_ticks"].isdigit(),
        "launch process birth",
    )
    root = Path(__file__).resolve().parents[1]
    _require(
        server["cwd"] == str(root) and server["health_instance"] == f"{socket.gethostname()}:{pid}",
        "launch process source/hostname/PID",
    )
    proc = Path("/proc") / str(pid)
    _require(proc.stat().st_uid == os.geteuid(), "actual process ownership")
    birth = _start_ticks(pid)
    _require(birth == server["start_ticks"], "actual process birth")
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    _require(
        boot == server["boot_id"] and os.readlink(proc / "cwd") == str(root),
        "actual process boot/source",
    )
    _require(_start_ticks(pid) == birth, "stable process birth")


def _launch(args: argparse.Namespace, fixture: Fixture, config: Any) -> dict[str, Any]:
    from server.core import deployment_config

    receipt, digest = _receipt(args.launch_manifest)
    _require(digest == fixture.launch_manifest_sha256, "launch receipt hash")
    _require(
        set(receipt) == {"schema", "server", "source", "configuration_artifact"}
        and type(receipt["schema"]) is int
        and receipt["schema"] == 1,
        "launch receipt schema",
    )
    server, source, artifact = (
        receipt[key] for key in ("server", "source", "configuration_artifact")
    )
    _require(
        isinstance(server, dict) and isinstance(source, dict) and isinstance(artifact, dict),
        "launch receipt objects",
    )
    _require(
        source == {"head": fixture.build_commit, "tree": fixture.source_tree},
        "launch source identity",
    )
    _require(server.get("instance_id") == fixture.instance_id, "launch server UUID")
    _require(
        set(artifact) == {"path", "sha256"}
        and isinstance(artifact["path"], str)
        and Path(artifact["path"]).is_absolute(),
        "configuration artifact path",
    )
    configuration, artifact_digest = _receipt(artifact["path"])
    _require(artifact_digest == artifact["sha256"], "configuration artifact hash")
    _require(
        set(configuration) == {"schema", "configuration_sha256", "workload", "fingerprints"}
        and type(configuration["schema"]) is int
        and configuration["schema"] == 1,
        "configuration artifact schema",
    )
    _require(
        configuration["configuration_sha256"]
        == fixture.configuration_sha256
        == _digest(_configuration(config)),
        "launch effective configuration",
    )
    _require(
        _digest(configuration["workload"]) == _digest(_workload(config)), "launch workload settings"
    )
    fingerprints = deployment_config.fingerprints(config)
    _require(configuration["fingerprints"] == fingerprints, "launch configuration fingerprints")
    _owned_server(server)
    return {
        "health_instance": server["health_instance"],
        "fingerprints": fingerprints,
        "launch_sha256": digest,
        "configuration_artifact_sha256": artifact_digest,
        "birth_sha256": _digest(server),
    }


class Artifact:
    def __init__(self, path: str):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        self.file: TextIO = os.fdopen(fd, "w", encoding="utf-8")
        self.records = 0

    def emit(self, kind: str, **record: Any) -> None:
        self.records += 1
        _require(self.records <= KEY_CEILING + 1000, "output record ceiling")
        line = json.dumps({"kind": kind, **record}, sort_keys=True, allow_nan=False)
        _require(len(line) <= 65_536, "output record size")
        self.file.write(line + "\n")

    def close(self) -> None:
        try:
            self.file.flush()
            os.fsync(self.file.fileno())
        finally:
            self.file.close()


async def _close_pg(conn: Any, end: float | None = None) -> None:
    from server.core import pg

    await pg.close_owned(conn, end=time.monotonic() + IO_S if end is None else end)


class ReadOnlyCH:
    """Public ClickHouse client restricted at connection startup, before any query."""

    def __init__(self, client: Any):
        self.client = client

    @classmethod
    async def open(cls) -> ReadOnlyCH:
        from server.core.ch_names import NamedClient
        from server.core.ch_target import connection_kwargs

        raw = await clickhouse_connect.get_async_client(
            **connection_kwargs(),
            settings={
                "readonly": 1,
                "max_execution_time": int(IO_S),
                "log_comment": "bench.cell_trigger_gate.preflight",
            },
            connect_timeout=IO_S,
            send_receive_timeout=IO_S,
        )
        return cls(NamedClient(raw))

    async def query_all(
        self, sql: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        result = await self.client.query(sql, parameters=params or {})
        return list(result.named_results())

    async def close(self) -> None:
        async with asyncio.timeout(IO_S):
            await self.client.close()


async def _health(
    args: argparse.Namespace, fixture: Fixture, launch: Mapping[str, Any] | None = None
) -> None:
    from server.settings import settings

    async with httpx.AsyncClient(timeout=IO_S, follow_redirects=False, trust_env=False) as client:
        health = await client.get(f"{args.base_url.rstrip('/')}/v1/health")
        deployment = await client.get(f"{args.base_url.rstrip('/')}/v1/health/deployment")
        _require(health.status_code == deployment.status_code == 200, "health HTTP status")
        h, d = health.json(), deployment.json()
        _require(
            h.get("build_commit") == fixture.build_commit
            and d.get("deployment_id") == fixture.deployment_id,
            "serving source/deployment",
        )
        _require(
            h.get("deployment_config", {}).get("instance_id") == fixture.instance_id,
            "serving instance",
        )
        _require(
            h.get("ch_ok") is True and h.get("pg_ok") is True and h.get("status") == "ok",
            "serving health",
        )
        _require(
            h.get("fake_embedding") is True
            and h.get("fake_llm") is True
            and h.get("pending_migrations") == [],
            "serving provider/schema",
        )
        _require(h.get("storage_authority") == "postgres", "serving authority")
        for field in ("llm_provider", "llm_model", "embedding_model"):
            _require(h.get(field) == getattr(settings, field), "serving " + field)
        _require(
            h.get("llm_provider") == "fake"
            and type(h.get("embedding_dim_configured")) is int
            and h["embedding_dim_configured"] == settings.embedding_dim
            and type(h.get("embedding_dim_probed")) is int
            and h["embedding_dim_probed"] == settings.embedding_dim,
            "serving embedding dimensions/fake provider",
        )
        launch = launch if launch is not None else _launch(args, fixture, settings)
        _require(h.get("instance") == launch["health_instance"], "serving owned process")


def _object(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    _require(isinstance(value, dict), "metadata JSON object")
    return cast(dict[str, Any], value)


async def _proof(
    conn: Any, ch: Any, args: argparse.Namespace, fixture: Fixture, *, native: bool = False
) -> dict[str, Any]:
    from server.core import cell, instances
    from server.settings import settings

    fixture.validate(args, settings)
    source = _source(fixture)
    launch = _launch(args, fixture, settings)
    actual = await conn.fetchrow(
        "SELECT current_database() AS db, inet_server_port() AS port, "
        "pg_is_in_recovery() AS recovery"
    )
    _require(
        actual and actual["db"] == fixture.pg_database and actual["recovery"] is False,
        "actual PG identity/primary",
    )
    if fixture.pg_internal_port is not None:
        _require(actual["port"] == fixture.pg_internal_port, "PG internal port")
    authority = await conn.fetchrow("SELECT authority, deployment_id, epoch FROM loom.authority")
    _require(
        authority
        and (authority["authority"], authority["deployment_id"], authority["epoch"])
        == ("postgres", fixture.deployment_id, fixture.epoch),
        "PG authority identity",
    )
    first = await conn.fetchrow("SELECT is_first, cell FROM loom.cell_first")
    mode = await conn.fetchval("SELECT mode FROM loom.tenancy_mode")
    own = await conn.fetchrow("SELECT current_fp, next_fp FROM loom.cell_self")
    held = await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM loom.tenancy WHERE org = $1)", args.org
    )
    _require(
        first and first["cell"] == fixture.cell and mode == "enforced" and held is True,
        "enforced held org",
    )
    _require(own and own["next_fp"] is None, "stable accepted fingerprint")
    current = _object(own["current_fp"])
    mine = cell.mine()
    mine["deployment_id"] = fixture.deployment_id
    _require(cell.matches(mine, current), "accepted local fingerprint")
    db = await ch.query_all("SELECT currentDatabase() AS db")
    _require(db == [{"db": fixture.ch_database}], "actual CH database")
    records = await ch.query_all(
        "SELECT authority, epoch, deployment_id FROM loom.storage_authority FINAL"
    )
    _require(len(records) == 1 and records[0] == dict(authority), "paired CH authority")
    for table in ("provider_configuration", "runtime_overrides"):
        rows = await ch.query_all(f"SELECT count() AS n FROM loom.{table}")
        _require(rows == [{"n": 0}], f"fresh {table}")
    live = await instances.read_live(ch, all_deployments=True)
    _require(
        len(live) == 1
        and live[0]["instance_id"] == fixture.instance_id
        and live[0]["build_commit"] == fixture.build_commit
        and live[0].get("serving") == 1
        and live[0].get("host") == socket.gethostname()
        and not live[0]["workers_lease"],
        "exclusive serving source",
    )
    _require(
        all(live[0].get(key) == value for key, value in launch["fingerprints"].items()),
        "serving configuration fingerprints",
    )
    beats = await ch.query_all(
        "SELECT instance_id, cell, tenancy_mode, fingerprint FROM "
        "loom.cell_heartbeats WHERE instance_id = {id:String}",
        {"id": fixture.instance_id},
    )
    _require(
        len(beats) == 1
        and beats[0]["cell"] == fixture.cell
        and beats[0]["tenancy_mode"] == "enforced",
        "serving cell heartbeat",
    )
    reported = _object(beats[0]["fingerprint"])
    _require(
        reported
        and cell.matches(mine, reported)
        and reported.get("serves") is True
        and reported.get("marked") is True,
        "serving fingerprint",
    )
    await _debt(conn, 0)
    await _health(args, fixture, launch)
    if native:
        await cell.refresh()
        _require(not cell.cli_gate_reason(), "native fresh cell acceptance")
    return {
        "deployment_id": fixture.deployment_id,
        "epoch": fixture.epoch,
        "instance_id": fixture.instance_id,
        "fingerprint_sha256": _digest(current),
        "configuration_sha256": fixture.configuration_sha256,
        "source": source,
        "launch": launch,
    }


async def _preflight(args: argparse.Namespace, fixture: Fixture) -> None:
    conn = ch = None
    try:
        conn = await asyncpg.connect(
            args.pg_dsn,
            timeout=IO_S,
            command_timeout=IO_S,
            server_settings={"default_transaction_read_only": "on"},
        )
        ch = await ReadOnlyCH.open()
        async with asyncio.timeout(IO_S):
            await _proof(conn, ch, args, fixture)
    finally:
        try:
            if ch is not None:
                await ch.close()
        finally:
            if conn is not None:
                await _close_pg(conn)


async def _debt(conn: Any, expected: int) -> None:
    row = await conn.fetchrow(
        "SELECT (SELECT count(*) FROM loom.memory_deliveries WHERE targets <> "
        "0) AS pending, (SELECT count(*) FROM loom.audit_outbox) AS audits, "
        "(SELECT count(*) FROM loom.memory_deliveries WHERE attempts <> 0 OR "
        "last_error <> '') AS errors"
    )
    _require(
        row and row["pending"] == expected and row["audits"] == row["errors"] == 0,
        "fixture delivery/audit debt",
    )


@dataclass
class Triggers:
    tables: tuple[str, ...]
    invariant: str
    org: str
    on: bool = True

    @staticmethod
    async def snapshot(conn: Any) -> tuple[tuple[str, ...], str, str]:
        tables = tuple(await cell_triggers.planned_tables(conn))
        rows = await conn.fetch(
            "SELECT c.relname AS table_name, t.tgname AS name, t.tgenabled AS "
            "enabled, pg_get_triggerdef(t.oid) AS definition FROM pg_trigger t "
            "JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid "
            "= c.relnamespace WHERE n.nspname = 'loom' AND NOT t.tgisinternal "
            "ORDER BY c.relname, t.tgname"
        )
        functions = await conn.fetch(
            "SELECT p.proname, pg_get_functiondef(p.oid) AS definition FROM "
            "pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE "
            "n.nspname = 'loom' AND p.prokind = 'f' ORDER BY p.proname, p.oid"
        )
        org = [dict(r) for r in rows if r["name"] in cell_triggers.ORG_TRIGGERS]
        other = [dict(r) for r in rows if r["name"] not in cell_triggers.ORG_TRIGGERS]
        return (
            tables,
            _digest({"functions": [dict(r) for r in functions], "triggers": other}),
            _digest(org),
        )

    @classmethod
    async def capture(cls, conn: Any) -> Triggers:
        _require(not await cell_triggers.missing(conn), "canonical ON trigger baseline")
        tables, invariant, org = await cls.snapshot(conn)
        _require(tables, "guarded tables")
        return cls(tables, invariant, org)

    async def verify(self, conn: Any) -> None:
        tables, invariant, org = await self.snapshot(conn)
        _require(
            tables == self.tables and invariant == self.invariant,
            "trigger table/function/tenancy drift",
        )
        _require(org == (self.org if self.on else _digest([])), "prior trigger treatment")

    async def set(self, conn: Any, on: bool) -> None:
        await self.verify(conn)
        async with conn.transaction():
            for table in self.tables:
                if table == "tenancy":
                    continue
                _require(bool(_DB.fullmatch(table)), "guarded table identifier")
                for name in cell_triggers.ORG_TRIGGERS:
                    await conn.execute(f'DROP TRIGGER IF EXISTS {name} ON loom."{table}"')
                    if on:
                        await conn.execute(cell_triggers.create_statement(table, name))
        self.on = on
        await self.verify(conn)

    async def restore(self, conn: Any) -> None:
        # Recreate the captured exact org guards even after a partial failed toggle.
        async with conn.transaction():
            for table in self.tables:
                if table == "tenancy":
                    continue
                for name in cell_triggers.ORG_TRIGGERS:
                    await conn.execute(f'DROP TRIGGER IF EXISTS {name} ON loom."{table}"')
                    await conn.execute(cell_triggers.create_statement(table, name))
        self.on = True
        await self.verify(conn)
        _require(not await cell_triggers.missing(conn), "restored canonical triggers")


@dataclass
class Samples:
    on: bool
    api_ms: list[float]
    delivery_rps: float


def _quantile(values: Sequence[float], fraction: float) -> float:
    _require(values and all(math.isfinite(v) and v >= 0 for v in values), "finite latency samples")
    return sorted(values)[math.ceil(fraction * len(values)) - 1]


def _statistics(samples: Sequence[Samples]) -> dict[str, Any]:
    _require(len(samples) == 2 * ROUNDS, "complete arm samples")
    arms: dict[bool, dict[str, Any]] = {}
    for on in (True, False):
        arm = [s for s in samples if s.on is on]
        _require(
            len(arm) == ROUNDS and all(len(s.api_ms) == CALLS for s in arm), "complete run samples"
        )
        p99 = [_quantile(s.api_ms, 0.99) for s in arm]
        rps = [s.delivery_rps for s in arm]
        _require(all(math.isfinite(r) and r > 0 for r in rps), "positive finite throughput")
        arms[on] = {
            "p99_ms": p99,
            "median_p99_ms": statistics.median(p99),
            "min_p99_ms": min(p99),
            "max_p99_ms": max(p99),
            "rps": rps,
            "median_rps": statistics.median(rps),
        }
    delta = arms[True]["median_p99_ms"] - arms[False]["median_p99_ms"]
    loss = 1 - arms[True]["median_rps"] / arms[False]["median_rps"]
    paired = []
    for i in range(0, len(samples), 2):
        a, b = samples[i : i + 2]
        _require(a.on is not b.on, "paired arm order")
        paired.append((_quantile(a.api_ms, 0.99) - _quantile(b.api_ms, 0.99)) * (1 if a.on else -1))
    return {
        "on": arms[True],
        "off": arms[False],
        "p99_delta_ms": delta,
        "throughput_loss": loss,
        "latency_within_limit": delta <= 10,
        "throughput_within_limit": loss <= 0.10,
        "paired_p99_differences_ms": paired,
    }


@dataclass
class RequestRow:
    key: str
    memory_id: str
    version: int
    content_sha256: str
    elapsed_ms: float
    measured: bool
    admission: str


async def _memory_set(
    args: argparse.Namespace, *, arm: int = 0, emit: Callable[..., None] = lambda *_a, **_kw: None
) -> list[RequestRow]:
    rows = []
    prefix = f"gate-{uuid.uuid4().hex}-{arm}"
    async with httpx.AsyncClient(
        timeout=IO_S,
        headers={"Authorization": f"Bearer {args.token}"},
        follow_redirects=False,
        trust_env=False,
    ) as client:
        for i in range(CALLS + WARMUP):
            key = f"{prefix}-{i}"
            value = _payload(key)
            started = time.perf_counter_ns()
            response = None
            try:
                response = await client.post(
                    f"{args.base_url.rstrip('/')}/v1/memory.set",
                    json={
                        "org": args.org,
                        "namespace": args.namespace,
                        "agent": "bench",
                        "memory_key": key,
                        "memory_type": "semantic",
                        "value": value,
                    },
                )
                _require(response.status_code == 200, "memory.set HTTP status")
                out = response.json()
                _require(
                    isinstance(out, dict)
                    and out.get("status") == "active"
                    and not out.get("noop")
                    and not out.get("degraded")
                    and not out.get("duplicate_of")
                    and not out.get("merged_into_key"),
                    "memory.set outcome",
                )
                memory_id = str(uuid.UUID(out["memory_id"]))
                _require(
                    type(out.get("version")) is int and out["version"] > 0, "memory.set version"
                )
                _require(
                    out.get("memory_key") == key
                    and out.get("namespace") == args.namespace
                    and out.get("content_sha256") == sha256_value(value),
                    "memory.set identity/hash",
                )
                admission = "derived_ready"
                elapsed = (time.perf_counter_ns() - started) / 1_000_000
                _require(math.isfinite(elapsed) and elapsed >= 0, "memory.set elapsed")
                row = RequestRow(
                    key,
                    memory_id,
                    out["version"],
                    sha256_value(value),
                    elapsed,
                    i >= WARMUP,
                    admission,
                )
                rows.append(row)
                emit("request", arm=arm, index=i, status=200, **asdict(row))
            except BaseException as error:
                elapsed = (time.perf_counter_ns() - started) / 1_000_000
                code = ""
                if response is not None:
                    try:
                        envelope = response.json()
                        candidate = envelope.get("error", {}).get("code", "")
                        if isinstance(candidate, str) and re.fullmatch(
                            r"[A-Z][A-Z0-9_]{0,63}", candidate
                        ):
                            code = candidate
                    except (ValueError, TypeError, AttributeError):
                        pass
                emit(
                    "request_error",
                    arm=arm,
                    index=i,
                    key=key,
                    measured=i >= WARMUP,
                    elapsed_ms=elapsed,
                    status=response.status_code if response is not None else None,
                    code=code,
                    error_type=type(error).__name__,
                )
                raise
    return rows


@dataclass
class Block:
    rows: list[dict[str, Any]]
    audits: list[dict[str, Any]]
    documents: list[dict[str, Any]]
    seqs: list[tuple[str, str, int]] = field(default_factory=list)


async def _build_block(args: argparse.Namespace, batch: int) -> Block:
    from server.llm import customer
    from server.memory.store import pg_memory, pg_publish
    from server.memory.store.rows import (
        MemoryRow,
        build_first_version_row,
        build_memory_version_row,
        next_version,
        written_now,
    )

    _planned_keys(batch)
    prefix = f"gate-delivery-{uuid.uuid4().hex}"
    rows, audits, documents = [], [], []
    with customer.for_org(args.org, namespace=args.namespace, subject=SUBJECT):
        for index in range(BATCHES * batch):
            key = f"{prefix}-{index}"
            value = _payload(key)
            stored = json.dumps(value, sort_keys=True, separators=(",", ":"))
            row = build_memory_version_row(
                MemoryRow.from_snapshot(
                    build_first_version_row(
                        {
                            "org": args.org,
                            "namespace": args.namespace,
                            "agent": "bench",
                            "memory_key": key,
                            "memory_type": "semantic",
                            "value": stored,
                            "version": next_version(),
                            "content_sha256": sha256_value(value),
                            "actor": SUBJECT,
                            "source_class": "asserted",
                        },
                        now=written_now(),
                    )
                )
            )
            row = pg_memory.decode_row(pg_memory.encode_row(row))
            document = await _expected_document(args, row, worker=True)
            rows.append(row)
            audits.append(pg_publish.audit_row_for(row, request_id="", writer=SUBJECT))
            documents.append(document)
    return Block(rows, audits, documents)


async def _expected_document(
    args: argparse.Namespace, row: dict[str, Any], *, worker: bool = False
) -> dict[str, Any]:
    from server.embedding import get_embedder
    from server.llm import customer
    from server.memory.domain.embed_text import embedding_texts
    from server.memory.store.rows import parse_memory_value, search_document_row

    embedder = get_embedder()
    raw = str(row["value"])
    excerpt, text, key_text = embedding_texts(
        row["memory_type"], parse_memory_value(raw), raw, row["memory_key"]
    )
    wanted = sorted({t for t in (text, key_text) if t})
    with customer.for_org(args.org, namespace=args.namespace, subject=SUBJECT):
        vectors = dict(zip(wanted, await embedder.embed_many(wanted), strict=True))
    from server.memory.store.conflict_resolver import OWED_MARK
    from server.settings import settings

    return search_document_row(
        row,
        conflict_checked_at=OWED_MARK
        if worker and settings.memory_conflict_resolver_enabled
        else None,
        embedding_model=embedder.model_name,
        embedding=vectors.get(text, []),
        key_embedding=vectors.get(key_text, [0.0] * embedder.dim),
        key_text=key_text,
        excerpt=excerpt,
    )


async def _stage(conn: Any, ch: Any, block: Block) -> None:
    from server.memory.store import pg_memory

    await _debt(conn, 0)
    for offset in range(0, len(block.rows), 100):
        rows = block.rows[offset : offset + 100]
        fresh = await conn.fetchval(
            "SELECT count(*) FROM loom.memory_current WHERE org=$1 AND namespace=$2 "
            "AND (memory_id=ANY($3::uuid[]) OR memory_key=ANY($4::text[]))",
            rows[0]["org"],
            rows[0]["namespace"],
            [uuid.UUID(str(row["memory_id"])) for row in rows],
            [row["memory_key"] for row in rows],
        )
        _require(fresh == 0, "fresh absent authority identity")
    await _absent(ch, block)
    committed = await pg_memory.commit_versions(
        block.rows,
        block.audits,
        targets=pg_memory.MEMORY_ROW | pg_memory.SEARCH_DOC | pg_memory.AUDIT_ROW,
        publisher_lease=False,
    )
    _require(
        len(committed.seqs) == len(block.rows) and all(t == 7 for t in committed.targets),
        "staged delivery count/targets",
    )
    block.seqs = committed.seqs
    # The authority may canonicalize the full persisted row; retain its exact result.
    _require(
        len(committed.rows) == len(block.rows) and len(committed.audits) == len(block.audits),
        "staged row/audit count",
    )
    for expected, actual in zip(block.rows, committed.rows, strict=True):
        _compare(expected, actual, tuple(expected), "committed full row")
    for expected, actual in zip(block.audits, committed.audits, strict=True):
        _compare(expected, actual, tuple(expected), "committed full audit")
    await _debt(conn, len(block.rows))
    due = await conn.fetchval(
        "SELECT count(*) FROM loom.memory_deliveries "
        "WHERE targets=7 AND lease_until<=clock_timestamp() AND NOT held AND attempts=0"
    )
    _require(due == len(block.rows), "exact fresh due deliveries")


async def _delivery(
    args: argparse.Namespace, batch: int, *, emit: Callable[..., None], arm: int
) -> float:
    from server.llm import customer
    from server.memory.workers import pg_delivery
    from server.settings import settings

    results: list[dict[str, Any]] = []
    _index = 0
    with customer.for_org(args.org, namespace=args.namespace, subject=SUBJECT):
        started = time.perf_counter_ns()
        try:
            for _index in range(BATCHES):
                _require(settings.pg_delivery_batch_rows == batch, "unchanged delivery batch")
                result = await pg_delivery.run_pass()
                results.append(asdict(result))
                _require(
                    result.claimed == result.published == batch and result.failed == 0,
                    "real worker claimed/published/failure",
                )
        except BaseException as error:
            emit("worker_error", arm=arm, index=_index, error_type=type(error).__name__)
            raise
        finally:
            elapsed = (time.perf_counter_ns() - started) / 1_000_000_000
            for result_index, outcome in enumerate(results):
                emit("worker", arm=arm, index=result_index, **outcome)
    _require(math.isfinite(elapsed) and elapsed > 0, "positive worker block elapsed")
    rps = BATCHES * batch / elapsed
    emit("delivery_block", arm=arm, elapsed_seconds=elapsed, rows=BATCHES * batch, rps=rps)
    return rps


def _normal(value: Any) -> Any:
    from datetime import datetime

    if isinstance(value, (bytes, bytearray)):
        return hash_text(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d\d-\d\dT.*", value):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            pass
    if isinstance(value, datetime):
        return value.replace(tzinfo=None).isoformat(timespec="milliseconds")
    if isinstance(value, Mapping):
        return {k: _normal(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_normal(v) for v in value]
    return value


def _compare(
    expected: Mapping[str, Any], actual: Mapping[str, Any], fields: Sequence[str], phase: str
) -> None:
    for field_name in fields:
        left, right = expected.get(field_name), actual.get(field_name)
        if field_name in ("embedding", "memory_key_embedding"):
            _require(
                isinstance(left, list)
                and isinstance(right, list)
                and len(left) == len(right)
                and all(
                    math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-7)
                    for a, b in zip(left, right, strict=True)
                ),
                phase + " vector",
            )
        else:
            _require(_normal(left) == _normal(right), phase + " " + field_name)


async def _absent(ch: Any, block: Block) -> None:
    for offset in range(0, len(block.rows), 100):
        rows = block.rows[offset : offset + 100]
        params = {
            "org": rows[0]["org"],
            "ns": rows[0]["namespace"],
            "ids": [str(r["memory_id"]) for r in rows],
            "keys": [r["memory_key"] for r in rows],
            "hashes": [a["row_hash"] for a in block.audits[offset : offset + 100]],
        }
        for table, predicate in (
            ("memory", "memory_id IN {ids:Array(UUID)}"),
            ("memory_embeddings", "memory_key IN {keys:Array(String)}"),
            ("audit_log", "row_hash IN {hashes:Array(String)}"),
        ):
            final = "" if table == "audit_log" else " FINAL"
            found = await ch.query_all(
                f"SELECT count() AS n FROM loom.{table}{final} WHERE org = "
                f"{{org:String}} AND namespace = {{ns:String}} AND {predicate}",
                params,
            )
            _require(found == [{"n": 0}], "fresh absent " + table)


async def _verify(
    conn: Any, ch: Any, args: argparse.Namespace, identities: Sequence[RequestRow] | Block
) -> None:
    block = identities if isinstance(identities, Block) else None
    wanted = identities.rows if isinstance(identities, Block) else [asdict(r) for r in identities]
    for offset in range(0, len(wanted), 100):
        group = wanted[offset : offset + 100]
        ids = [uuid.UUID(str(r["memory_id"])) for r in group]
        stored = await conn.fetch(
            "SELECT c.row::text AS head, r.row::text AS revision, "
            "r.audit::text AS audit, r.seq, d.targets, d.attempts, "
            "d.last_error FROM loom.memory_current c JOIN "
            "loom.memory_revisions r ON "
            "(r.org,r.namespace,r.seq)=(c.org,c.namespace,c.seq) LEFT JOIN "
            "loom.memory_deliveries d ON "
            "(d.org,d.namespace,d.seq)=(r.org,r.namespace,r.seq) WHERE "
            "c.org=$1 AND c.namespace=$2 AND c.memory_id=ANY($3::uuid[])",
            args.org,
            args.namespace,
            ids,
        )
        _require(len(stored) == len(group), "exact authority head/revision count")
        by_id = {json.loads(r["head"])["memory_id"]: r for r in stored}
        params = {
            "org": args.org,
            "ns": args.namespace,
            "ids": [str(i) for i in ids],
            "keys": [r.get("memory_key", r.get("key")) for r in group],
        }
        projected = await ch.query_all(
            "SELECT * FROM loom.memory FINAL WHERE org={org:String} AND "
            "namespace={ns:String} AND memory_id IN {ids:Array(UUID)}",
            params,
        )
        docs = await ch.query_all(
            "SELECT * FROM loom.memory_embeddings FINAL WHERE org={org:String} "
            "AND namespace={ns:String} AND memory_key IN {keys:Array(String)}",
            params,
        )
        _require(len(projected) == len(docs) == len(group), "exact projection/vector counts")
        projections = {str(r["memory_id"]): r for r in projected}
        documents = {r["memory_key"]: r for r in docs}
        audit_hashes = []
        authority_audits = {}
        for index, expected in enumerate(group):
            id = str(expected["memory_id"])
            _require(id in by_id and id in projections, "authority/projection identity")
            record = by_id[id]
            _require(
                record["targets"] in (None, 0)
                and record["attempts"] in (None, 0)
                and record["last_error"] in (None, ""),
                "delivery settled without failure",
            )
            head, revision, audit = (
                json.loads(record[name]) for name in ("head", "revision", "audit")
            )
            _compare(head, revision, tuple(head), "authority full row")
            if block:
                _compare(expected, head, tuple(expected), "authority staged row")
                _compare(
                    block.audits[offset + index], audit, tuple(audit), "authority staged audit"
                )
                _require(
                    (args.org, args.namespace, record["seq"]) == block.seqs[offset + index],
                    "authority staged sequence",
                )
            else:
                key = str(expected["key"])
                _require(
                    head["memory_key"] == key
                    and json.loads(head["value"]) == _payload(key)
                    and head["version"] == expected["version"]
                    and head["content_sha256"] == expected["content_sha256"]
                    and head["status"] == "active",
                    "authority API payload/version",
                )
            _compare(head, projections[id], tuple(head), "full memory projection")
            key = str(head["memory_key"])
            _require(key in documents, "search document identity")
            document = documents[key]
            if block:
                expected_document = block.documents[offset + index]
                _compare(
                    expected_document,
                    document,
                    tuple(k for k in expected_document if k != "created_at"),
                    "full search document",
                )
            else:
                expected_document = await _expected_document(args, head)
                _compare(
                    expected_document,
                    document,
                    tuple(
                        k
                        for k in expected_document
                        if k not in ("created_at", "conflict_checked_at")
                    ),
                    "full API search document",
                )
            audit_hashes.append(audit["row_hash"])
            authority_audits[audit["row_hash"]] = audit
        params["hashes"] = audit_hashes
        audits = await ch.query_all(
            "SELECT * FROM loom.audit_log WHERE org={org:String} AND "
            "namespace={ns:String} AND row_hash IN {hashes:Array(String)}",
            params,
        )
        _require(
            sorted(hash_text(r["row_hash"]) for r in audits) == sorted(audit_hashes),
            "exact stored audit hashes",
        )
        for audit in audits:
            expected_audit = authority_audits[hash_text(audit["row_hash"])]
            _compare(expected_audit, audit, tuple(expected_audit), "full stored audit")
    await _debt(conn, 0)


async def _settle_api(
    conn: Any, ch: Any, args: argparse.Namespace, rows: Sequence[RequestRow]
) -> None:
    end = asyncio.get_running_loop().time() + SETTLE_S
    while True:
        pending = await conn.fetchval(
            "SELECT count(*) FROM loom.memory_deliveries WHERE org=$1 AND "
            "namespace=$2 AND targets<>0",
            args.org,
            args.namespace,
        )
        if pending == 0:
            break
        _require(asyncio.get_running_loop().time() < end, "API publication settlement deadline")
        await asyncio.sleep(0.1)
    await _verify(conn, ch, args, rows)


async def _physical(conn: Any, ch: Any) -> dict[str, Any]:
    from server.core.ch_names import database, telemetry_database

    pg = await conn.fetch(
        "SELECT relname, n_live_tup AS estimated_rows, "
        "pg_total_relation_size(relid) AS bytes FROM pg_stat_user_tables WHERE "
        "schemaname='loom' ORDER BY relname"
    )
    parts = await ch.query_all(
        "SELECT database, table, sum(rows) AS rows, sum(bytes_on_disk) AS "
        "bytes FROM system.parts WHERE active AND database IN "
        "{dbs:Array(String)} GROUP BY database, table ORDER BY database, table",
        {"dbs": [database(), telemetry_database()]},
    )
    exact = {}
    for table in ("memory_current", "memory_revisions", "memory_deliveries", "audit_outbox"):
        exact[table] = await conn.fetchval(f"SELECT count(*) FROM loom.{table}")
    return {
        "postgres_rows": exact,
        "postgres_estimates": [dict(r) for r in pg],
        "clickhouse_parts": parts,
    }


async def _run(
    args: argparse.Namespace, fixture: Fixture, artifact: Artifact, ch: Any
) -> dict[str, Any]:
    from server.core.ch import set_query_tag
    from server.settings import settings

    set_query_tag("bench.cell_trigger_gate")
    batch = settings.pg_delivery_batch_rows
    conn = await asyncpg.connect(
        args.pg_dsn,
        timeout=IO_S,
        command_timeout=IO_S,
        server_settings={"loom.tenancy_checked": "on"},
    )
    triggers = None
    samples = []
    try:
        async with asyncio.timeout(IO_S):
            await _proof(conn, ch, args, fixture, native=True)
            triggers = await Triggers.capture(conn)
        for arm, on in enumerate(_arm_order()):
            async with asyncio.timeout(IO_S):
                proof = await _proof(conn, ch, args, fixture, native=True)
                await triggers.set(conn, on)
                artifact.emit(
                    "arm_proof",
                    arm=arm,
                    on=on,
                    **proof,
                    tables=triggers.tables,
                    invariant_sha256=triggers.invariant,
                    org_triggers_sha256=triggers.org,
                )
            api = await _memory_set(args, arm=arm, emit=artifact.emit)
            async with asyncio.timeout(SETTLE_S):
                await _settle_api(conn, ch, args, api)
            block = await _build_block(args, batch)
            async with asyncio.timeout(SETTLE_S):
                await _stage(conn, ch, block)
            rps = await _delivery(args, batch, emit=artifact.emit, arm=arm)
            async with asyncio.timeout(SETTLE_S):
                await _verify(conn, ch, args, block)
                artifact.emit(
                    "settled",
                    arm=arm,
                    api_rows=len(api),
                    delivery_rows=len(block.rows),
                    physical=await _physical(conn, ch),
                )
            latencies = [r.elapsed_ms for r in api if r.measured]
            samples.append(Samples(on, latencies, rps))
            artifact.emit(
                "arm",
                arm=arm,
                on=on,
                p50_ms=_quantile(latencies, 0.5),
                p99_ms=_quantile(latencies, 0.99),
                delivery_rps=rps,
            )
        from server.core.background import drain_task_sets, flush_registered_buffers

        _require(await drain_task_sets(timeout_s=IO_S), "driver descendant drain")
        _require(await flush_registered_buffers(), "driver buffer drain")
        summary = _statistics(samples)
        artifact.emit("provisional_statistics", **summary)
        return summary
    finally:
        from server.cli import cell_attach_pg as apg

        prior = sys.exception()
        cleanup = asyncio.create_task(
            apg._work_result(lambda: _cleanup(triggers, conn, artifact)),
            name="trigger-gate-cleanup",
        )
        interrupted = False
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                interrupted = True
            except BaseException:  # noqa: BLE001 — preserve an earlier operation failure
                break
        try:
            if isinstance(result := cleanup.result(), apg._Interrupted):
                raise result.error
        except BaseException as error:
            try:
                artifact.emit("cleanup_error", error_type=type(error).__name__)
            except (OSError, ValueError):
                if prior is None:
                    raise
            if prior is None:
                raise
        if interrupted and prior is None:
            raise asyncio.CancelledError


async def _cleanup(triggers: Triggers | None, conn: Any, artifact: Artifact) -> None:
    end = time.monotonic() + RESTORE_S
    try:
        if triggers is not None:
            await _restore(triggers, conn, end)
            artifact.emit("triggers_restored", verified=True)
    finally:
        prior = sys.exception()
        try:
            await _close_pg(conn, end)
        except BaseException:
            if prior is None:
                raise
            artifact.emit("connection_cleanup_error", error_type=type(sys.exception()).__name__)


async def _restore(triggers: Triggers, conn: Any, end: float) -> None:
    from server.cli import cell_attach_pg as apg

    await apg.supervise(conn, triggers.restore(conn), end=end)


def main() -> int:
    from server.cli import _ch
    from server.settings import settings
    from server.wiring import wire

    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "base-url",
        "pg-dsn",
        "token",
        "org",
        "namespace",
        "fixture-manifest",
        "launch-manifest",
        "output",
    ):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    artifact = None
    verdict: dict[str, Any] = {"verdict": "INVALID"}
    phase = "local_manifest"
    try:
        fixture = Fixture.read(args.fixture_manifest)
        total = fixture.validate(args, settings)
        source = _source(fixture)
        launch = _launch(args, fixture, settings)
        artifact = Artifact(args.output)
        artifact.emit(
            "configuration",
            manifest=asdict(fixture),
            source=source,
            launch=launch,
            manifest_sha256=_digest(asdict(fixture)),
            calls=CALLS,
            warmup=WARMUP,
            rounds=ROUNDS,
            batches=BATCHES,
            batch_rows=settings.pg_delivery_batch_rows,
            logical_keys=total,
            value_bytes=VALUE_BYTES,
            order=_arm_order(),
            driver_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        )
        phase = "readonly_preflight"
        asyncio.run(_preflight(args, fixture))
        phase = "native_driver"
        wire()

        async def run(ch: Any) -> dict[str, Any]:
            return await _run(args, fixture, artifact, ch)

        summary = _ch.run_connected(run, orgs=(args.org,))
        artifact.emit("driver_cleanup", native_returned=True, outer_database_cleanup_required=True)
        verdict = {
            "verdict": "PASS"
            if summary["latency_within_limit"] and summary["throughput_within_limit"]
            else "FAIL",
            "p99_delta_ms": summary["p99_delta_ms"],
            "throughput_loss": summary["throughput_loss"],
            "outer_database_cleanup_required": True,
        }
    except BaseException as error:  # noqa: BLE001 — every failed run must emit INVALID
        # Never serialize exception text: clients may include credentials or values.
        reason = str(error) if isinstance(error, Invalid) else "operation refused"
        verdict = {
            "verdict": "INVALID",
            "phase": phase,
            "reason": reason,
            "error_type": type(error).__name__,
        }
        if artifact is not None:
            try:
                artifact.emit(
                    "invalid", phase=phase, reason=reason, error_type=type(error).__name__
                )
            except (OSError, ValueError):
                pass
    finally:
        if artifact is not None:
            try:
                artifact.close()
            except (OSError, ValueError) as error:
                verdict = {
                    "verdict": "INVALID",
                    "error_type": type(error).__name__,
                    "phase": "artifact_close",
                }
    print(json.dumps(verdict, sort_keys=True, allow_nan=False), flush=True)
    return 0 if verdict["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
