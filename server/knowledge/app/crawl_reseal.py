"""Root rotation carries refusal keys and source approvals without changing authority."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from server.auth.source_registration import PURPOSE
from server.clients._vendor import VendorError
from server.core.ch import get_client, set_query_tag
from server.core.secrets_at_rest import resealed, unseal
from server.knowledge.app import crawl_exclusions as exclusions
from server.knowledge.app import sources as source_ops
from server.knowledge.store import crawl_exclusions as store
from server.knowledge.store import sources as source_store
from server.settings import settings


async def keys(*, dry_run: bool, ch: Any) -> tuple[int, list[str]]:
    if ch is not get_client():
        raise RuntimeError("crawl_reseal_target_mismatch")
    set_query_tag("knowledge.crawl.reseal.keys")
    count, scanned, after, lost = 0, 0, "", []
    while page := await store.page(after, settings.knowledge_crawl_registry_page_size, ch=ch):
        scanned += len(page)
        if scanned > settings.knowledge_crawl_registry_scan_limit:
            raise RuntimeError("crawl_reseal_scan_bound")
        for org in page:
            async with exclusions.org_lease(
                org,
                ttl_s=settings.knowledge_crawl_lease_ttl_s,
                wait_s=settings.knowledge_crawl_lease_wait_s,
                ch=ch,
            ):
                row = await store.key(org, ch=ch)
                if row is not None:
                    changed = await _key(org, row, dry_run=dry_run, ch=ch)
                    if changed is None:
                        lost.append(org)
                    else:
                        count += changed
        after = page[-1]
    return count, lost


async def _key(org: str, row: dict[str, Any], *, dry_run: bool, ch: Any) -> int | None:
    # A lost key never gets regenerated: doing so would discard every old refusal.
    try:
        exclusions._open_key(row)
    except (ValueError, TypeError, VendorError):
        return None
    new = resealed(exclusions.KEY_PURPOSE, str(row["key_enc"]))
    if new is None:
        return 0
    if not dry_run:
        await store.key_put(
            org,
            str(row["key_id"]),
            new,
            generation=str(row["generation"]),
            pending_id=str(row["pending_id"]),
            pending_digests=tuple(row["pending_digests"]),
            pending_exclude=bool(row["pending_exclude"]),
            ch=ch,
        )
    return 1


async def approvals(*, dry_run: bool, ch: Any) -> tuple[int, list[str]]:
    if ch is not get_client():
        raise RuntimeError("crawl_reseal_target_mismatch")
    set_query_tag("knowledge.crawl.reseal.approvals")
    count, scanned, after, lost = 0, 0, ("", "", ""), []
    while page := await source_store.crawl_approval_page(
        after, maximum=settings.knowledge_crawl_registry_page_size, ch=ch
    ):
        scanned += len(page)
        if scanned > settings.knowledge_crawl_registry_scan_limit:
            raise RuntimeError("crawl_reseal_scan_bound")
        for org, ns, name in page:
            async with source_ops.sources_lease(org, ns):
                current = await source_store.latest_source(org, ns, name, ch=ch)
                if current is None or not current.approval_enc:
                    continue
                if not unseal(PURPOSE, current.approval_enc, warn=False):
                    lost.append("/".join((org, ns, name)))
                    continue
                new = resealed(PURPOSE, current.approval_enc)
                if new is not None:
                    count += 1
                    if not dry_run:
                        await source_store.insert_source(replace(current, approval_enc=new), ch=ch)
        after = page[-1]
    return count, lost
