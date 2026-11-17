"""Exact document content reads never substitute another tenant, head or revision."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from tests.integration._resources import unique_org


async def test_content_identity_and_pending_head_are_exact() -> None:
    os.environ.get("LOOM_CH_HOST") or pytest.skip("LOOM_CH_HOST not set")
    from server.core import pg
    from server.core.ch import get_client, set_query_tag
    from server.core.naming import new_mirror_name
    from server.knowledge.domain.access_list import AccessList, to_json
    from server.knowledge.store import documents, pg_documents, source_documents

    org, mirror, uri = (
        unique_org("checked-content"),
        new_mirror_name(),
        "gdrive:file#" + str(uuid4()),
    )
    ch = get_client()
    await ch.connect()
    set_query_tag("test.checked_document_content")
    row = documents.build_document_row(
        org=org,
        namespace=mirror,
        source_uri=uri,
        doc_id=str(uuid4()),
        title="Checked title",
        description="Checked description",
        content="Exact content",
        metadata='{"key":"checked"}',
        tags=["checked"],
        version=10,
        revision=0,
        ingested_at=datetime.now(UTC).replace(microsecond=0, tzinfo=None),
        agent="fixture",
        user_id="fixture-user",
        session_id="fixture-session",
        actor="process:fixture",
        source_class="external",
        access_json=to_json(AccessList(owner="owner")),
    )
    try:
        await ch.insert("loom.documents", [row])
        if pg.enabled():
            assert await source_documents.contents(org, mirror, uri, 10, 0) is None
            await pg_documents.commit([row], over={}, targets={uri: 1})
        got = await source_documents.contents(org, mirror, uri, 10, 0)
        assert got is not None and got.model_dump() == row
        assert (
            await source_documents.contents(unique_org("foreign-content"), mirror, uri, 10, 0)
            is None
        )
        assert await source_documents.contents(org, new_mirror_name(), uri, 10, 0) is None
        assert await source_documents.contents(org, mirror, uri + "other", 10, 0) is None
        assert await source_documents.contents(org, mirror, uri, 11, 0) is None
        assert await source_documents.contents(org, mirror, uri, 10, 1) is None
        next_row = {**row, "version": 11, "content": "Current head only"}
        if pg.enabled():
            await pg_documents.commit([next_row], over={uri: 10}, targets={uri: 1})
        else:
            await ch.insert("loom.documents", [next_row])
        assert await source_documents.contents(org, mirror, uri, 10, 0) is None
        current = await source_documents.contents(org, mirror, uri, 11, 0)
        assert current is not None and current.model_dump() == next_row
        deleted = {
            **next_row,
            "version": 12,
            "superseded_at": row["ingested_at"],
            "access_json": "",
        }
        if pg.enabled():
            await pg_documents.commit([deleted], over={uri: 11}, targets={uri: 1})
        else:
            await ch.insert("loom.documents", [deleted])
        assert await source_documents.contents(org, mirror, uri, 11, 0) is None
        assert await source_documents.contents(org, mirror, uri, 12, 0) is None
    finally:
        await ch.disconnect()
        await pg.close_pool()
