"""Restore a stopped cell's metadata from its permanent directory placement snapshot."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from typing import Any

import httpx

from server.cli import cell as cell_cli
from server.cli import cell_attach_pg as apg
from server.cli import cell_restore_pg as local
from server.cli._ch import connect_declared, disconnect, writes
from server.core import cell, pg
from server.router import directory
from server.router import restore as protocol
from server.settings import settings


class Budget:
    """An independent waiter can terminate a session stuck unwinding a transaction."""

    def __init__(self, seconds: float):
        self.end = time.monotonic() + seconds
        self.reserve = min(0.2, seconds / 10)
        self.conn: Any = None

    def left(self, *, cleanup: bool = False) -> float:
        return max(0.0, self.end - time.monotonic() - (0 if cleanup else self.reserve))

    async def call[T](self, work: Awaitable[T], *, cleanup: bool = False) -> T:
        task = asyncio.ensure_future(work)
        try:
            done, _ = await asyncio.wait({task}, timeout=self.left(cleanup=cleanup))
            if not done:
                raise TimeoutError("cell restore reached --wait")
            return task.result()
        except BaseException:
            if self.conn is not None:
                self.conn.terminate()
            task.cancel()
            done, _ = await asyncio.wait({task}, timeout=self.left(cleanup=True))
            if done:
                # Retrieve driver errors produced by synchronous transport termination.
                await asyncio.gather(task, return_exceptions=True)
            raise


def _args_problem(args: argparse.Namespace) -> str:
    if not cell.NAME.fullmatch(args.cell or "") or args.cell != settings.cell:
        return "--cell must name this configuration's LOOM_CELL"
    if settings.storage_authority != "postgres" or not settings.pg_dsn:
        return "restore needs Postgres authority and this cell's LOOM_PG_DSN"
    if not math.isfinite(args.wait) or args.wait <= 0:
        return "--wait must be finite and positive"
    return ""


async def _page(
    router: httpx.URL,
    name: str,
    deployment: str,
    after: str | None,
    previous: dict[str, Any] | None,
    seconds: float,
) -> dict[str, Any]:
    url = cell_cli.at(router, f"/cells/v1/operator/cells/{name}/restore-read")
    body = {
        "deployment_id": deployment,
        "after": after,
        "expected_sha256": protocol.digest(previous) if previous is not None else None,
    }
    timeout = httpx.Timeout(max(0.01, min(seconds, directory.RESTORE_READ_MAX_S + 10)), connect=5)
    async with httpx.AsyncClient(trust_env=router.scheme == "https", timeout=timeout) as client:
        async with client.stream(
            "POST",
            url,
            json=body,
            headers={"Authorization": f"Bearer {settings.router_operator_token}"},
        ) as response:
            raw = bytearray()
            async for part in response.aiter_bytes():
                raw.extend(part)
                if len(raw) > protocol.MAX_RESPONSE_BYTES:
                    raise ValueError("restore response exceeds its byte bound")
            answer = json.loads(raw)
            if (
                response.status_code == 409
                and isinstance(answer, dict)
                and set(answer) == {"descriptor", "sha256"}
            ):
                protocol.validate_descriptor(answer["descriptor"])
                if protocol.digest(answer["descriptor"]) != answer["sha256"] or previous is None:
                    raise ValueError("restore changed-descriptor answer is invalid")
                protocol.compare(previous, answer["descriptor"])
                raise ValueError("the router refused an unchanged restore snapshot")
            if response.status_code != 200:
                raise ValueError(f"the router refused restore-read ({response.status_code})")
            protocol.validate_page(answer, after=after, previous=previous)
            return dict(answer)


async def _migrate(conn: Any) -> None:
    from server.core.schema import pg_migrate

    @asynccontextmanager
    async def borrowed() -> AsyncIterator[Any]:
        yield conn

    await pg_migrate.migrate(connect=borrowed)
    rows = await conn.fetch("SELECT filename, checksum FROM loom.pg_migrations")
    if pg_migrate.plan({r["filename"]: r["checksum"] for r in rows}, pg_migrate.migration_files()):
        raise ValueError("Postgres migrations remain pending")
    await local.transaction(conn, lambda: _nothing())


async def _nothing() -> None:
    return None


class Restore:
    def __init__(self, args: argparse.Namespace, router: httpx.URL, budget: Budget):
        self.args, self.router, self.budget = args, router, budget
        self.conn: Any = None
        self.recorded: dict[str, Any] = {}
        self.inserted = 0

    async def page(self, after: str | None, previous: dict[str, Any] | None) -> dict[str, Any]:
        return await self.budget.call(
            _page(
                self.router,
                self.args.cell,
                self.recorded["deployment_id"],
                after,
                previous,
                self.budget.left(),
            )
        )

    async def run(self, ch: Any) -> dict[str, Any]:
        from server.cli import cell_restore_stores as stores

        cm = pg.dedicated(statement_timeout_ms=settings.pg_statement_timeout_ms)
        try:
            self.conn = await self.budget.call(cm.__aenter__())
            self.budget.conn = self.conn
            refusals = await self.budget.call(apg.direct_refusals(self.conn, "restore"))
            if refusals:
                raise ValueError("; ".join(r.reason for r in refusals))
            await self.budget.call(apg.take_lock(self.conn, "restore"))
            self.recorded = await self.budget.call(stores.identity(ch, self.conn))
            await self.budget.call(stores.stopped_and_schema(ch))
            first = await self.page(None, None)
            stores.descriptor(first["descriptor"], self.recorded)
            await self.budget.call(local.checked(self.conn, first["descriptor"], self.recorded))
            await self.budget.call(_migrate(self.conn))
            await self.budget.call(stores.nonces(ch, self.conn))
            return await self.finish(first)
        finally:
            if self.conn is not None:
                self.conn.terminate()
                await self.budget.call(cm.__aexit__(None, None, None), cleanup=True)

    async def finish(self, first: dict[str, Any]) -> dict[str, Any]:
        from server.cli.cell_restore_triggers import repair

        while True:
            try:
                descriptor = first["descriptor"]
                await self.copy(first)
                extra = await self.compare(descriptor)
                rows = await self.budget.call(local.state(self.conn, descriptor, self.recorded))
                await self.budget.call(repair(self.conn, descriptor))
                verified = await self.budget.call(local.state(self.conn, descriptor, self.recorded))
                if rows != verified:
                    raise ValueError("cell rows changed during trigger repair")
                await self.page(None, descriptor)
                await self.budget.call(local.checked(self.conn, descriptor, self.recorded))
                if await self.budget.call(local.read_rows(self.conn)) != verified:
                    raise ValueError("cell rows changed after the final directory acknowledgement")
                return {
                    "cell": self.args.cell,
                    "snapshot_orgs": descriptor["placed"],
                    "inserted_tenancy": self.inserted,
                    "retained_local_only": extra,
                    "mode": rows["mode"],
                    "prepare_required": rows["own"] is None,
                    "resume_attach": descriptor["state"] == "attaching",
                }
            except protocol.SnapshotChanged:
                first = await self.page(None, None)
                from server.cli import cell_restore_stores as stores

                try:
                    protocol.compare(descriptor, first["descriptor"])
                except protocol.SnapshotChanged:
                    pass
                stores.descriptor(first["descriptor"], self.recorded)

    async def copy(self, first: dict[str, Any]) -> None:
        answer, count = first, 0
        descriptor = first["descriptor"]
        while True:
            self.inserted += await self.budget.call(
                local.tenancy(self.conn, descriptor, self.recorded, answer["orgs"])
            )
            count += len(answer["orgs"])
            if answer["done"]:
                if count != descriptor["placed"]:
                    raise ValueError("the completed traversal differs from the placement count")
                return
            answer = await self.page(answer["next"], descriptor)

    async def compare(self, descriptor: dict[str, Any]) -> int:
        held = local.LocalTenancy(self.conn)
        after = None
        count = 0
        while True:
            answer = await self.page(after, descriptor)
            await self.budget.call(held.compare(answer["orgs"], descriptor, last=answer["done"]))
            count += len(answer["orgs"])
            if answer["done"]:
                if count != descriptor["placed"]:
                    raise ValueError("the validation traversal differs from the placement count")
                return held.extra
            after = answer["next"]


def cmd_restore(args: argparse.Namespace) -> int:
    if reason := _args_problem(args):
        print(f"loom cell restore: {reason}", file=sys.stderr)
        return 2
    router = cell_cli.operator_router(args)
    if isinstance(router, str):
        print(f"loom cell restore: {router}", file=sys.stderr)
        return 2

    async def run() -> dict[str, Any]:
        from server.core.ch import get_client

        budget = Budget(args.wait)
        ch = get_client()
        try:
            await budget.call(
                connect_declared(ch, org_free=writes("restores this cell's metadata"))
            )
            return await Restore(args, router, budget).run(ch)
        finally:
            await budget.call(disconnect(ch), cleanup=True)

    try:
        result = asyncio.run(run())
    except asyncio.CancelledError:
        print(
            "loom cell restore: outcome may be unknown; "
            "rerun the same command before starting the cell",
            file=sys.stderr,
        )
        raise
    except Exception as error:  # noqa: BLE001 — an incomplete repair must name its idempotent retry
        print(
            f"loom cell restore: {error}; outcome may be unknown; "
            "rerun the same command before starting the cell",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def add_restore_parser(csub: Any) -> None:
    parser = csub.add_parser(
        "restore", help="Repair a stopped cell's metadata before reconciliation."
    )
    parser.add_argument("--cell", required=True, help="This cell's name (LOOM_CELL)")
    parser.add_argument("--router", help="The router URL (default: LOOM_CELL_ROUTER_URL)")
    parser.add_argument(
        "--wait", type=float, default=300, help="Total repair deadline in seconds (default: 300)"
    )
    parser.set_defaults(func=cmd_restore)
