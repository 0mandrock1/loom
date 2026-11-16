"""Fenced cell control: one directory claim and the exact local lock-owning backend."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from server.cli import cell_attach_pg as apg
from server.cli.cell_ch import Refusal
from server.cli.cell_ch import Refused as Refused
from server.router.directory import CONTROL_FIELDS, CONTROL_MAX_S
from server.settings import settings

# A control transaction has few metadata statements; COMMIT's standby wait is bounded too.
TRANSACTION_S = 60.0  # not a setting: a single control transaction's deadline
OPEN_S = 75.0  # not a setting: connection initialization and direct-session checks
REQUEST_S = CONTROL_MAX_S + 30.0  # not a setting: directory bound plus probes/connect/close
Call = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


def _refused(reason: str) -> Refused:
    return Refused([Refusal("cell control", reason)])


def preconditions(row: dict[str, Any], *, claim: bool = False) -> dict[str, Any]:
    """Carry the whole observed registration and exact fingerprint pair."""
    if any(name not in row for name in (*CONTROL_FIELDS, "mutation_generation")):
        raise _refused("a router replica is not upgraded for cell control fencing")
    try:
        generation = str(UUID(row["mutation_generation"]))
    except (ValueError, TypeError, AttributeError):
        raise _refused(
            "a router replica returned no readable cell control generation: upgrade it"
        ) from None
    values = {name: row[name] for name in CONTROL_FIELDS}
    if not claim:
        values["mutation_generation"] = generation
    return {"expected": values}


async def check_lock(conn: Any) -> None:
    try:
        held = await apg.lock_held(conn)
    except Exception as e:  # noqa: BLE001 — an unreadable lock ends this ownership
        raise _refused(
            f"lock session lost ({type(e).__name__}): stop and rerun with a fresh claim"
        ) from None
    if not held:
        raise _refused("attach lock lost: stop and rerun with a fresh claim")


async def paired(conn: Any, row: dict[str, Any]) -> None:
    """The locked authority and configured bucket name the observed registration."""
    recorded = await conn.fetchval("SELECT deployment_id FROM loom.authority")
    prefix = settings.document_s3_prefix if settings.document_s3_bucket else ""
    if (
        not recorded
        or recorded != row.get("deployment_id")
        or settings.cell != row.get("cell")
        or (settings.document_s3_bucket, prefix) != (row.get("bucket"), row.get("bucket_prefix"))
    ):
        raise _refused(
            "this cell configuration and locked Postgres do not match the directory registration"
        )


async def claim(conn: Any, call: Call, row: dict[str, Any]) -> dict[str, Any]:
    """Claim once; a lost acknowledgement ends the attempt, never restoring an old token."""
    await check_lock(conn)
    await paired(conn, row)
    answer = await call("/claim", preconditions(row, claim=True))
    await check_lock(conn)
    preconditions(answer)
    expected = {name: row[name] for name in CONTROL_FIELDS}
    if json.dumps(expected, sort_keys=True) != json.dumps(
        {name: answer[name] for name in CONTROL_FIELDS}, sort_keys=True
    ):
        raise _refused(
            "claim returned a different registration or fingerprint pair: stop and rerun"
        )
    return answer


async def mutate(
    conn: Any, call: Call, row: dict[str, Any], path: str, body: dict[str, Any]
) -> dict[str, Any]:
    """An unknown response stops the caller; a fresh run must claim and recompute."""
    await check_lock(conn)
    answer = await call(path, {**body, **preconditions(row)})
    await check_lock(conn)
    return answer


@asynccontextmanager
async def transaction(conn: Any) -> AsyncIterator[Any]:
    """Check, write and durably commit on the lock backend; cancellation ends ownership."""
    settled = False
    tx = conn.transaction()
    try:
        async with asyncio.timeout(TRANSACTION_S):
            await tx.start()
            try:
                await conn.execute("SET LOCAL lock_timeout = '5s'")
                await conn.execute("SET LOCAL statement_timeout = '10s'")
                await check_lock(conn)
                await conn.execute("SELECT pg_logical_emit_message(true, 'loom.control', '')")
                yield conn
            except Exception:
                await tx.rollback()
                settled = True
                raise
            else:
                await tx.commit()
                settled = True
    finally:
        if not settled:
            # This includes cancellation during COMMIT/rollback. A later session is a new run.
            conn.terminate()


@asynccontextmanager
async def session(what: str) -> AsyncIterator[Any]:
    """A direct, marked, UTF8 session holding ATTACH for its entire control run."""
    from server.core import pg

    cm = pg.dedicated(statement_timeout_ms=60_000)
    conn = None
    try:
        async with asyncio.timeout(OPEN_S):
            conn = await cm.__aenter__()
            refused = await apg.direct_refusals(conn, what)
            refused += await apg.encoding_refusals(conn)
            if refused:
                raise Refused(refused)
            await apg.take_lock(conn, what)
        yield conn
    finally:
        if conn is not None:
            # Use dedicated()'s bounded close; this does not prove the server backend ended.
            await cm.__aexit__(None, None, None)


async def request(
    url: Any,
    name: str,
    transport: Any,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    method: str = "POST",
) -> dict[str, Any]:
    """Read or mutate one operator row; gateways and lost replies are unknown outcomes."""
    import httpx

    from server.cli import cell as cli

    endpoint = cli.at(url, f"/cells/v1/operator/cells/{name}" + path)
    try:
        async with (
            asyncio.timeout(REQUEST_S),
            httpx.AsyncClient(
                timeout=cli._OPERATOR_TIMEOUT,
                trust_env=url.scheme == "https",
                transport=transport,
                headers={"Authorization": f"Bearer {settings.router_operator_token}"},
            ) as client,
        ):
            response = await client.request(method, endpoint, json=body)
    except (httpx.HTTPError, TimeoutError) as e:
        raise _refused(f"router reply unknown ({type(e).__name__}): stop and rerun") from None
    answer = cli.router_answer(response, {"cell": name}, cli.OPERATOR_REFUSALS)
    if answer is None:
        raise _refused(cli.not_the_router(response))
    if response.status_code == 404 and answer.get("code") != "NO_SUCH_CELL":
        raise _refused("a router replica is not upgraded for cell control fencing")
    if response.status_code != 200:
        raise _refused(
            f"router refused {path or 'the read'} ({response.status_code}): {answer['error']}"
        )
    return answer


async def _acceptance(conn: Any) -> dict[str, Any]:
    return {
        "first": dict(row)
        if (row := await conn.fetchrow("SELECT is_first, cell FROM loom.cell_first"))
        else None,
        "mode": await conn.fetchval("SELECT mode FROM loom.tenancy_mode"),
        "own": dict(row)
        if (row := await conn.fetchrow("SELECT current_fp, next_fp FROM loom.cell_self"))
        else None,
    }


async def backout(conn: Any, call: Call, row: dict[str, Any]) -> None:
    """The runbook's exact state cleanup, fenced against queued controllers/placements."""
    from server.core.schema import pg_migrate

    async with transaction(conn):
        before = await _acceptance(conn)
    first = before["first"]
    if first is not None and (first["cell"], first["is_first"]) != (row["cell"], row["is_first"]):
        raise _refused("the local first row names another registration")
    already_open = not any(before.values()) and not await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM loom.tenancy)"
    )
    if row["is_first"] and before["mode"] != "recording" and not already_open:
        raise _refused("first-cell backout requires local recording mode; enforced goes forward")
    row = await claim(conn, call, row)
    if row.get("state") != "attaching":
        raise _refused("a serving cell goes forward; state cleanup refused")
    # Closing and reading placed under one directory row lock excludes an ordinary
    # placement queued before the claim. A later failure deliberately leaves it closed.
    limited = await mutate(conn, call, row, "/limit", {"closed": True})
    if type(limited.get("placed_orgs")) is not int or limited["placed_orgs"] != 0:
        raise _refused("cell holds placements: cleanup refused; the cell remains closed")
    async with transaction(conn):
        await paired(conn, row)
        if json.dumps(await _acceptance(conn), sort_keys=True) != json.dumps(
            before, sort_keys=True
        ):
            raise _refused("local acceptance changed: cleanup refused; the cell remains closed")
        await conn.execute("SELECT pg_advisory_xact_lock($1)", pg_migrate._LOCK_KEY)
        await conn.execute("ALTER TABLE loom.cell_first DISABLE TRIGGER cell_first_immutable")
        for table in ("cell_first", "tenancy_mode", "cell_self", "tenancy"):
            await conn.execute(f"DELETE FROM loom.{table}")
        await conn.execute("ALTER TABLE loom.cell_first ENABLE ALWAYS TRIGGER cell_first_immutable")
        await conn.execute(
            "ALTER TABLE loom.cell_first ENABLE ALWAYS TRIGGER cell_first_no_truncate"
        )
    # Once state is open, a migrating boot cannot recreate the org triggers. The same
    # session still holds ATTACH; a retry repeats the durable state cleanup first.
    async with transaction(conn):
        await conn.execute("SELECT pg_advisory_xact_lock($1)", pg_migrate._LOCK_KEY)
        triggers = await conn.fetch(
            "SELECT t.tgname, c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'loom' "
            "AND t.tgname IN ('tenancy_insert', 'tenancy_update', 'tenancy_record')"
        )
        for trigger in triggers:
            name, table = (str(trigger[k]).replace('"', '""') for k in ("tgname", "relname"))
            await conn.execute(f'DROP TRIGGER "{name}" ON loom."{table}"')
