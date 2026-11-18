"""Conversation projections retain the canonical parent's explicit identity spellings."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from server.memory.app import chunk_projection


@pytest.mark.parametrize("user,session", [("", "s"), ("u", ""), ("", ""), ("-", "-")])
async def test_chunk_projection_retains_the_parent_identity(monkeypatch, user, session):
    ch = SimpleNamespace(insert=AsyncMock())

    async def embedded(text, ranges, chunks, embed):
        return [SimpleNamespace(written=True, vector=[1.0, 0.0]) for _ in chunks]

    monkeypatch.setattr("server.core.ch.get_client", lambda: ch)
    monkeypatch.setattr("server.embedding.get_embedder", lambda: SimpleNamespace(model_name="fake"))
    monkeypatch.setattr(chunk_projection, "embed_chunks_of", embedded)
    parent = SimpleNamespace(org="o", namespace="n", agent="a", user_id=user, session_id=session)
    await chunk_projection.write_memory_chunks(
        parent,
        [{"role": "user", "content": "A conversation passage linked to a durable parent memory."}],
        parent_memory_keys=["parent"],
    )
    call = next(c for c in ch.insert.call_args_list if c.args[0] == "loom.memory_chunks")
    assert call.args[1]
    assert {
        (r["agent"], r["user_id"], r["session_id"], r["parent_memory_key"]) for r in call.args[1]
    } == {("a", user, session, "parent")}
