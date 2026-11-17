"""Checked-leg metadata reads keep org, source and current-head boundaries."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from tests.integration._resources import unique_org


async def test_group_snapshot_readback_and_erasure_are_scoped() -> None:
    os.environ.get("LOOM_CH_HOST") or pytest.skip("LOOM_CH_HOST not set")
    from server.core.ch import get_client, set_query_tag
    from server.core.naming import new_mirror_name
    from server.erasure.store.steps_ledgers import delete_group_members_by_namespace
    from server.knowledge.store import source_access as store

    ch = get_client()
    await ch.connect()
    set_query_tag("test.source_access_group_store")
    org, other_org = unique_org("access-groups"), unique_org("other-access-groups")
    mirror, other_mirror, sid = new_mirror_name(), new_mirror_name(), str(uuid4())
    now = datetime.now(UTC).replace(microsecond=0, tzinfo=None)
    row = {
        "org": org,
        "namespace": mirror,
        "source_id": sid,
        "group": "team@example.com",
        "name": "documents",
        "members": ["lee@example.com"],
        "read_at": now,
        "deleted": False,
        "version": 1,
    }
    try:
        await ch.insert(
            "loom.knowledge_group_members",
            [row, {**row, "org": other_org}, {**row, "namespace": other_mirror}],
        )
        saved = await ch.query_all(
            "SELECT * FROM loom.knowledge_group_members FINAL WHERE org = {org:String} AND namespace = {ns:String}",
            {"org": org, "ns": mirror},
        )
        assert saved == [row]
        selected = await store.group_members(org, [(mirror, sid, "team@example.com")])
        assert selected == [
            {
                k: row[k]
                for k in ("namespace", "source_id", "group", "members", "read_at", "deleted")
            }
        ]
        assert await store.group_members(org, [(mirror, str(uuid4()), "team@example.com")]) == []
        await delete_group_members_by_namespace(org, mirror)
        assert await store.group_members(org, [(mirror, sid, "team@example.com")]) == []
        assert await store.group_members(other_org, [(mirror, sid, "team@example.com")]) == selected
        assert await store.group_members(org, [(other_mirror, sid, "team@example.com")]) == [
            {**selected[0], "namespace": other_mirror}
        ]
    finally:
        await ch.disconnect()


async def test_pg_head_metadata_has_current_acl_and_recorded_chunks() -> None:
    os.environ.get("LOOM_CH_HOST") or pytest.skip("LOOM_CH_HOST not set")
    from server.core import pg

    if not pg.enabled():
        pytest.skip("Postgres authority not selected")
    from server.core.ch import get_client, set_query_tag
    from server.core.naming import new_mirror_name
    from server.knowledge.domain.access_list import AccessList, to_json
    from server.knowledge.store import pg_documents
    from server.knowledge.store import source_access as store

    ch = get_client()
    await ch.connect()
    set_query_tag("test.source_access_head_store")
    org, mirror, sid = unique_org("access-head"), new_mirror_name(), str(uuid4())
    uri = "gdrive:file#" + sid
    row = {
        "org": org,
        "namespace": mirror,
        "source_uri": uri,
        "doc_id": str(uuid4()),
        "content": "must not be fetched before admission",
        "title": "checked title",
        "metadata": "{}",
        "description": "not candidate metadata",
        "tags": ["source-id:" + sid],
        "version": 10,
        "revision": 0,
        "superseded_at": None,
        "ingested_at": datetime.now(UTC).replace(microsecond=0),
        "access_json": to_json(AccessList(emails=("lee@example.com",))),
    }
    try:
        await pg_documents.commit([row], over={}, targets={uri: 1}, chunked={uri})
        narrowed = {**row, "version": 11, "access_json": to_json(AccessList())}
        await pg_documents.commit([narrowed], over={uri: 10}, targets={uri: 1})
        got = await store.heads(org, [(mirror, uri)])
        head = got[(mirror, uri)]
        assert head.serving.revision == head.serving.chunks_revision == 0
        assert head.serving.chunks_version == 10 and not head.serving.deleted
        assert head.row["version"] == 11 and head.row["access_json"] == narrowed["access_json"]
        assert not {"content", "description", "metadata"}.intersection(head.row)
        assert await store.heads(unique_org("other-access-head"), [(mirror, uri)]) == {}
        assert await store.heads(org, [(mirror, uri + "missing")]) == {}
    finally:
        await ch.disconnect()
        await pg.close_pool()
