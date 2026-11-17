"""Signed searches admit current source permissions under either storage authority."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest

from tests.integration._resources import unique_org


@pytest.mark.parametrize("mode", ["semantic", "lexical", "hybrid"])
async def test_signed_search_permissions_follow_current_heads(mode, mint_test_token) -> None:
    base = os.environ.get("LOOM_BASE_URL") or pytest.skip("LOOM_BASE_URL not set")
    os.environ.get("LOOM_CH_HOST") or pytest.skip("LOOM_CH_HOST not set")
    from server.auth.jwt import mint_token
    from server.core import pg
    from server.core.ch import get_client, set_query_tag
    from server.core.naming import new_mirror_name
    from server.embedding import get_embedder
    from server.knowledge.domain.access_list import AccessList, to_json
    from server.knowledge.domain.sources import KnowledgeSource
    from server.knowledge.store import mirrors, pg_documents, sources
    from server.settings import settings

    embedder = get_embedder()
    assert embedder.model_name.startswith("fake-"), "This fixture uses only fake embeddings"
    org, namespace, mirror = unique_org("checked-search"), "team", new_mirror_name()
    source = str(uuid4())
    query = "markedsearchcanary"
    public_uri = "gdrive:checked-document#native-fragment"
    uri = public_uri + "#" + source
    vector = await embedder.embed(query)
    owner = mint_token(
        "source-owner",
        org,
        [namespace],
        ["viewer"],
        ttl_seconds=600,
        is_service_account=False,
        session_id=str(uuid4()),
    )
    outsider = mint_test_token(org=org, namespaces=[namespace], subject="outsider")
    owner_bot = mint_test_token(org=org, namespaces=[namespace], subject="source-owner")
    foreign = mint_test_token(org=unique_org("foreign-search"), namespaces=[namespace])
    ch = get_client()
    await ch.connect()
    set_query_tag("test.checked_search_http")
    now = datetime.now(UTC).replace(microsecond=0)

    async def write_document(ns, address, content, access="", tags=()):
        row = {
            "org": org,
            "namespace": ns,
            "source_uri": address,
            "doc_id": str(uuid4()),
            "content": content,
            "title": "Checked search",
            "metadata": "{}",
            "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
            "tags": list(tags),
            "version": 10,
            "revision": 0,
            "superseded_at": None,
            "ingested_at": now,
            "access_json": access,
        }
        await ch.insert("loom.documents", [row])
        await ch.insert(
            "loom.chunks",
            [
                {
                    "org": org,
                    "namespace": ns,
                    "source_uri": address,
                    "chunk_idx": 0,
                    "content": content,
                    "doc_version": 10,
                    "chunk_metadata": json.dumps({"revision": 0}),
                    "embedding_model": embedder.model_name,
                    "embedding_dim": len(vector),
                    "embedding": vector,
                    "tags": list(tags),
                }
            ],
        )
        if pg.enabled():
            await pg_documents.commit([row], over={}, targets={address: 1}, chunked={address})
        return row

    async with httpx.AsyncClient(base_url=base, timeout=30) as client:

        async def search(token):
            response = await client.post(
                "/v1/knowledge.search",
                json={
                    "org": org,
                    "namespace": namespace,
                    "query": query,
                    "namespace_scope": "this",
                    "search_mode": mode,
                    "collapse_sections": False,
                    "top_k": 10,
                },
                headers={"Authorization": "Bearer " + token},
            )
            assert response.status_code == 200, response.text
            return response.json()

        try:
            anonymous = await client.post(
                "/v1/knowledge.search",
                json={
                    "org": org,
                    "namespace": namespace,
                    "query": query,
                },
            )
            assert anonymous.status_code == 401
            # Install a trusted fixture directly; public source creation stays disabled.
            await sources.insert_source(
                KnowledgeSource(
                    org=org,
                    namespace=namespace,
                    name="checked",
                    kind="gdrive",
                    source_id=source,
                    items=[{"type": "file", "id": "synthetic-file"}],
                    access="mirrored",
                    active=True,
                )
            )
            await mirrors.add_mirror(org, namespace, mirror)
            await mirrors.record_listing(org, mirror, source, "checked", now)
            await write_document(namespace, "ordinary:visible", query + " ordinary")
            baseline = await search(outsider)
            assert [r["source_uri"] for r in baseline["results"]] == ["ordinary:visible"]
            row = await write_document(
                mirror,
                uri,
                query + " private",
                to_json(AccessList(owner="source-owner")),
                ["source-id:" + source],
            )
            assert await search(outsider) == baseline
            assert await search(owner_bot) == baseline
            allowed = await search(owner)
            private = [r for r in allowed["results"] if r["source_uri"] == public_uri]
            assert len(private) == 1 and private[0]["content"] == query + " private"
            assert private[0]["namespace"] == namespace
            denied = await client.post(
                "/v1/knowledge.search",
                json={
                    "org": org,
                    "namespace": namespace,
                    "query": query,
                },
                headers={"Authorization": "Bearer " + foreign},
            )
            assert denied.status_code == 403
            narrowed = {**row, "version": 11, "access_json": to_json(AccessList())}
            if pg.enabled():
                await pg_documents.commit([narrowed], over={uri: 10}, targets={uri: 1})
            else:
                await ch.insert("loom.documents", [narrowed])
            assert await search(owner) == baseline
            widened = {**row, "version": 12, "access_json": to_json(AccessList(anyone=True))}
            if pg.enabled():
                await pg_documents.commit([widened], over={uri: 11}, targets={uri: 1})
            else:
                await ch.insert("loom.documents", [widened])
            assert public_uri in [r["source_uri"] for r in (await search(owner_bot))["results"]]
            stale = now - timedelta(seconds=settings.knowledge_source_acl_stale_seconds + 1)
            await mirrors.record_listing(org, mirror, source, "checked", stale)
            assert await search(owner) == baseline
        finally:
            await ch.disconnect()
            await pg.close_pool()
