"""A write that did not land at its key, and a lock that found
no row, say so.

A set can merge the same content into ANOTHER key (no row at the asked key);
a lock that then answers 200 locked:false sends the follow-up write to a key
that was never locked. An update can answer 200 updated:false because its key
resolution missed a row written moments earlier on a lagging replica.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from server.memory.api.models_crud import MemoryUpdateInput
from server.memory.api.models_lock import MemoryLockInput, MemoryUnlockInput
from server.memory.app import update as update_mod
from server.memory.app import write as write_mod
from server.memory.app.lock import handle_memory_lock, handle_memory_unlock
from server.memory.domain.conflicts import ConflictResult
from tests.unit.test_memory_set_verdict import _PEER_ID, _existing_row, _input, _peer, _wire

_ID = {"org": "test", "namespace": "personal", "agent": "cursor"}


@pytest.mark.asyncio
async def test_a_merge_into_another_key_names_that_key(monkeypatch: pytest.MonkeyPatch) -> None:
    other = _existing_row().model_copy(update={"memory_key": "test/lockrace/update/0"})
    for dry in (False, True):
        _wire(
            monkeypatch,
            ConflictResult(
                verdict="merge", peers=[_peer(similarity=0.99)], existing_memory_id=_PEER_ID
            ),
            existing_by_id=other,
        )
        inp = _input(dry_run=dry)
        out = await write_mod.handle_memory_set(inp)
        assert out.conflict_verdict == "merge" and out.noop is True
        assert out.memory_key == inp.memory_key
        assert out.merged_into_key == "test/lockrace/update/0"


@pytest.mark.asyncio
async def test_a_merge_into_the_same_key_leaves_merged_into_key_empty(monkeypatch) -> None:
    _wire(
        monkeypatch,
        ConflictResult(
            verdict="merge", peers=[_peer(similarity=0.99)], existing_memory_id=_PEER_ID
        ),
        existing_by_id=_existing_row().model_copy(update={"memory_key": _input().memory_key}),
    )
    out = await write_mod.handle_memory_set(_input())
    assert out.merged_into_key == ""


@pytest.mark.asyncio
async def test_a_lock_or_unlock_with_no_row_says_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _none(*_: Any, **__: Any) -> None:
        return None

    monkeypatch.setattr("server.memory.store.get_by_key", _none)
    out = await handle_memory_lock(MemoryLockInput(**_ID, memory_key="k", reason="r"))
    assert out.locked is False and out.reason == "not_found"
    un = await handle_memory_unlock(MemoryUnlockInput(**_ID, memory_key="k", reason="r"))
    assert un.unlocked is False and un.reason == "not_found"


def test_key_resolution_retries_consistently_on_a_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    """The lagging replica answers the plain read empty; only the
    sequentially consistent retry sees the row written a moment ago."""
    sqls: list[str] = []

    class _CH:
        async def query_all(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
            sqls.append(sql)
            if "select_sequential_consistency = 1" in sql:
                return [
                    {
                        "memory_id": "m",
                        "agent": "a",
                        "user_id": "-",
                        "session_id": "-",
                        "version": 2,
                        "status": "active",
                    }
                ]
            return []

    rows = asyncio.run(
        update_mod._live_rows_for_key(MemoryUpdateInput(**_ID, memory_key="k", value="v"), _CH())
    )
    assert [r["memory_id"] for r in rows] == ["m"]
    assert len(sqls) == 2 and "select_sequential_consistency = 1" in sqls[1]


async def test_key_resolution_sees_a_postgres_commit_before_publication(monkeypatch) -> None:
    from server.identity.domain.read_scope import WHOLE_NAMESPACE

    committed = {
        "memory_id": "committed",
        "agent": "cursor",
        "user_id": "-",
        "session_id": "-",
        "version": 2,
        "status": "active",
    }

    async def heads(org, namespace, keys, *, scope):
        assert (org, namespace, keys, scope) == ("test", "personal", ["k"], WHOLE_NAMESPACE)
        return [committed]

    class UnpublishedCH:
        async def query_all(self, *args, **kwargs):
            raise AssertionError("the committed head is not published to ClickHouse")

    monkeypatch.setattr(update_mod.pg, "enabled", lambda: True)
    monkeypatch.setattr(update_mod.pg_memory, "live_key_heads", heads)
    rows = await update_mod._live_rows_for_key(
        MemoryUpdateInput(**_ID, memory_key="k", value="v"), UnpublishedCH()
    )
    assert rows == [committed]


@pytest.mark.parametrize(
    "identity, expected",
    [
        ({}, ["star", "three", "two", "one"]),
        ({"agent": "*", "user_id": "-", "session_id": "-"}, ["star", "three", "two", "one"]),
        ({"agent": "-"}, ["star", "three", "two", "one"]),
        ({"agent": "writer"}, ["three", "two"]),
        ({"user_id": "u"}, ["two", "one"]),
        ({"session_id": "s1"}, ["three", "one"]),
        ({"agent": "writer", "user_id": "u", "session_id": "s2"}, ["two"]),
        ({"user_id": "*", "session_id": "*"}, ["star"]),
    ],
)
async def test_postgres_key_resolution_keeps_identity_and_status_filters(
    monkeypatch, identity, expected
):
    from datetime import datetime

    heads = [
        dict(
            memory_id="one",
            agent="other",
            user_id="u",
            session_id="s1",
            version=1,
            status="active",
            expires_at=datetime.min,
        ),
        dict(
            memory_id="two",
            agent="writer",
            user_id="u",
            session_id="s2",
            version=2,
            status="duplicate",
        ),
        dict(
            memory_id="three",
            agent="writer",
            user_id="v",
            session_id="s1",
            version=3,
            status="conflicted",
        ),
        dict(memory_id="star", agent="*", user_id="*", session_id="*", version=4, status="active"),
        dict(
            memory_id="superseded",
            agent="writer",
            user_id="u",
            session_id="s2",
            version=5,
            status="superseded",
        ),
    ]

    async def committed(*args, **kwargs):
        return heads

    monkeypatch.setattr(update_mod.pg, "enabled", lambda: True)
    monkeypatch.setattr(update_mod.pg_memory, "live_key_heads", committed)
    inp = MemoryUpdateInput(org="test", namespace="personal", memory_key="k", value="v", **identity)
    rows = await update_mod._live_rows_for_key(inp, None)
    assert [r["memory_id"] for r in rows] == expected


async def test_postgres_key_resolution_uses_the_token_scope(monkeypatch):
    from server.auth.context import current_auth
    from server.identity.domain.read_scope import ReadFilters, read_filters_bound

    scope = ReadFilters(scope="own_session", user_id="u", session_id="s")

    async def committed(org, namespace, keys, *, scope):
        assert scope == ReadFilters(scope="own_session", user_id="u", session_id="s")
        return []

    monkeypatch.setattr(update_mod.pg, "enabled", lambda: True)
    monkeypatch.setattr(update_mod.pg_memory, "live_key_heads", committed)
    with read_filters_bound(current_auth(), scope):
        assert (
            await update_mod._live_rows_for_key(
                MemoryUpdateInput(**_ID, memory_key="k", value="v"), None
            )
            == []
        )


async def test_postgres_key_resolution_does_not_fall_back_after_a_store_failure(monkeypatch):
    async def failed(*args, **kwargs):
        raise RuntimeError("authority unavailable")

    monkeypatch.setattr(update_mod.pg, "enabled", lambda: True)
    monkeypatch.setattr(update_mod.pg_memory, "live_key_heads", failed)
    with pytest.raises(RuntimeError, match="authority unavailable"):
        await update_mod._live_rows_for_key(
            MemoryUpdateInput(**_ID, memory_key="k", value="v"), None
        )


async def test_postgres_key_resolution_returns_the_ten_newest_candidates(monkeypatch):
    async def committed(*args, **kwargs):
        return [{"memory_id": str(v), "version": v, "status": "active"} for v in range(20)]

    monkeypatch.setattr(update_mod.pg, "enabled", lambda: True)
    monkeypatch.setattr(update_mod.pg_memory, "live_key_heads", committed)
    rows = await update_mod._live_rows_for_key(
        MemoryUpdateInput(org="test", namespace="personal", memory_key="k", value="v"), None
    )
    assert [r["memory_id"] for r in rows] == [str(v) for v in range(19, 9, -1)]


async def test_postgres_key_resolution_keeps_multiple_identities_ambiguous(monkeypatch):
    from fastapi import HTTPException

    async def committed(*args, **kwargs):
        return [
            {"memory_id": writer, "agent": writer, "version": 1, "status": "active"}
            for writer in ("one", "two")
        ]

    monkeypatch.setattr(update_mod.pg, "enabled", lambda: True)
    monkeypatch.setattr(update_mod.pg_memory, "live_key_heads", committed)
    monkeypatch.setattr(update_mod, "get_client", lambda: None)
    with pytest.raises(HTTPException) as caught:
        await update_mod.handle_memory_update(
            MemoryUpdateInput(org="test", namespace="personal", memory_key="k", value="v")
        )
    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "AMBIGUOUS_KEY"
    assert {r["memory_id"] for r in caught.value.detail["candidates"]} == {"one", "two"}
