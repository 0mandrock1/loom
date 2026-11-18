"""Org-scoped refusal identities; no raw provider id or address is stored."""

from __future__ import annotations

from typing import Any

from server.core.ch import own_write_reads
from server.core.repo_base import _CHClient, _default_client

KEYS = "loom.knowledge_crawl_exclusion_keys"
REFUSALS = "loom.knowledge_crawl_exclusions"


async def key(org: str, *, ch: _CHClient | None = None) -> dict[str, Any] | None:
    with own_write_reads():
        return await (ch or _default_client()).query_one(
            "SELECT key_id, key_enc, generation, pending_id, pending_digests, pending_exclude "
            f"FROM {KEYS} FINAL "
            "WHERE org = {org:String} LIMIT 1",
            params={"org": org},
        )


async def key_put(
    org: str,
    key_id: str,
    key_enc: str,
    *,
    generation: str,
    pending_id: str = "",
    pending_digests: tuple[str, ...] = (),
    pending_exclude: bool = False,
    ch: _CHClient | None = None,
) -> None:
    await (ch or _default_client()).insert(
        KEYS,
        [
            {
                "org": org,
                "key_id": key_id,
                "key_enc": key_enc,
                "generation": generation,
                "pending_id": pending_id,
                "pending_digests": list(pending_digests),
                "pending_exclude": pending_exclude,
            }
        ],
    )


async def any_refusal(org: str, *, ch: _CHClient | None = None) -> bool:
    with own_write_reads():
        return (
            await (ch or _default_client()).query_one(
                f"SELECT digest FROM {REFUSALS} FINAL WHERE org = {{org:String}} LIMIT 1",
                params={"org": org},
            )
            is not None
        )


async def excluded(org: str, digests: list[str], *, ch: _CHClient | None = None) -> set[str]:
    if not digests:
        return set()
    with own_write_reads():
        rows = await (ch or _default_client()).query_all(
            f"SELECT digest FROM {REFUSALS} FINAL WHERE org = {{org:String}} "
            "AND digest IN {digests:Array(String)} AND excluded = 1 LIMIT {limit:UInt32}",
            params={"org": org, "digests": digests, "limit": len(digests)},
        )
    found = {str(row["digest"]) for row in rows}
    if len(rows) != len(found) or not found.issubset(digests):
        raise RuntimeError("crawl_exclusions_unverified")
    return found


async def put(org: str, digests: list[str], *, exclude: bool, ch: _CHClient | None = None) -> None:
    if digests:
        await (ch or _default_client()).insert(
            REFUSALS,
            [
                {"org": org, "digest": digest, "excluded": exclude}
                for digest in sorted(set(digests))
            ],
        )
