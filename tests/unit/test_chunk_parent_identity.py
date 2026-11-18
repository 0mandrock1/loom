"""Anonymous identity spellings bind projections to their parent and keep readable keys."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from server.memory.app import chunk_projection


async def _project(monkeypatch, user, session):
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
    return call.args[1]


@pytest.mark.parametrize("user,session", [("", "s"), ("u", ""), ("", ""), ("-", "-")])
async def test_chunk_projection_uses_the_anonymous_identity_sentinel(monkeypatch, user, session):
    rows = await _project(monkeypatch, user, session)
    assert {(r["agent"], r["user_id"], r["session_id"], r["parent_memory_key"]) for r in rows} == {
        ("a", user or "-", session or "-", "parent")
    }


@pytest.mark.parametrize("session", ["", "-"])
async def test_an_anonymous_projection_remains_searchable_and_resolvable(monkeypatch, session):
    from server.memory.app.handlers import split_chunk_key
    from server.memory.app.retrieval.chunks import _chunk_anchor_keys, _search_memory_chunks
    from tests.unit.test_chunk_two_stage import _stage2_row
    from tests.unit.test_memory_search_service import StubCH, _q

    row = (await _project(monkeypatch, "", session))[0]
    ch = StubCH()
    ch.query_all_returns.append([{**row, "distance": 0.2}])
    full = _stage2_row(row["session_id"], row["chunk_index"], parent="parent")
    ch.query_all_returns.append([full])
    q = _q(query_text="")
    hits = await _search_memory_chunks(ch, q, {"q": q.query_vec})
    assert len(hits) == 1
    assert split_chunk_key(hits[0].memory_key) == ("-", row["chunk_index"])
    assert _chunk_anchor_keys([full]) == [("-", row["chunk_index"])]
