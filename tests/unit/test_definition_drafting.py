"""One model attempt per claim; later decisions and erasure outrank its completion."""

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

import pytest

from server.core.errors import ValidationError
from server.llm.client import LLMResult
from server.memory.app import definition_drafts as drafts


@pytest.fixture
def state(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    value: dict[str, Any] = {"row": None, "calls": 0, "cap": False, "valid": True, "deleted": []}
    lock = asyncio.Lock()

    @asynccontextmanager
    async def claim(*a: Any, **kw: Any) -> Any:
        async with lock:
            yield True

    async def find(*a: Any, **kw: Any) -> Any:
        return value["row"]

    async def insert(row: Any) -> None:
        value["row"] = dict(row)

    async def delete(org: str, namespace: str, cid: str, version: int) -> None:
        value["deleted"].append(version)
        if value["row"] and int(value["row"]["version"]) <= version:
            value["row"] = None

    async def cap(*a: Any, **kw: Any) -> bool:
        assert kw["fail_open"] is False
        return value["cap"]

    async def none(*a: Any, **kw: Any) -> None:
        pass

    class Model:
        async def complete(self, system: str, user: str, **kw: Any) -> LLMResult:
            assert json.loads(user) == {"sql": "SELECT sum(amount) FROM sales"}
            value["calls"] += 1
            if value.get("hold"):
                from server.llm.customer import ModelCallsDisabled

                raise ModelCallsDisabled("held")
            if value.get("entered"):
                value["entered"].set()
                await value["resume"].wait()
            return LLMResult(
                text=value.get("answer", '{"term":"Revenue","body":"Sum of sales."}'),
                tokens_in=10,
                tokens_out=10,
                provider="fake",
                model="fake",
            )

    from server.core import leases
    from server.llm import client, customer

    monkeypatch.setattr(leases, "holding", claim)
    monkeypatch.setattr(drafts.learnings, "find_candidate", find)
    monkeypatch.setattr(drafts.learnings, "insert_row", insert)
    monkeypatch.setattr(drafts.learnings, "delete_candidate_through", delete)
    monkeypatch.setattr(drafts, "namespace_over_cap", cap)
    monkeypatch.setattr(drafts, "require_draft_fleet", none)
    monkeypatch.setattr(customer, "require", none)
    monkeypatch.setattr(client, "get_llm_client", Model)
    return value


async def run(state: dict[str, Any]) -> str:
    async def valid() -> bool:
        return bool(state["valid"])

    return await drafts.draft(
        "o",
        "n",
        kind="query_history",
        resource_id="source/hash",
        evidence={"sql": "SELECT sum(amount) FROM sales"},
        supporters=3,
        still_valid=valid,
    )


async def test_repeat_keeps_exact_candidate_without_a_model_call(state: Any) -> None:
    cid = await run(state)
    original = dict(state["row"])
    assert await run(state) == cid and state["calls"] == 1 and state["row"] == original
    proposal = json.loads(original["proposed_value"])
    assert proposal["rests_on"]["sql"] == "SELECT sum(amount) FROM sales"
    assert original["supporters"] == [] and original["status"] == "pending"


async def test_parallel_repeat_answers_claim_while_model_runs(state: Any) -> None:
    state.update(entered=asyncio.Event(), resume=asyncio.Event())
    first = asyncio.create_task(run(state))
    try:
        await state["entered"].wait()
        cid = await run(state)
        assert state["calls"] == 1 and state["row"]["proposed_value"] == "null"
    finally:
        state["resume"].set()
    assert await first == cid and state["calls"] == 1


@pytest.mark.parametrize("change", ["reject", "redact", "erase"])
async def test_late_model_result_does_not_restore_erased_or_decided_content(
    state: Any, change: str
) -> None:
    state.update(entered=asyncio.Event(), resume=asyncio.Event())
    task = asyncio.create_task(run(state))
    try:
        await state["entered"].wait()
        if change == "reject":
            state["row"].update(status="rejected", version=10**19)
        elif change == "redact":
            state["row"].update(proposed_value="", version=10**19)
        else:
            state["row"] = None
            state["valid"] = False
    finally:
        state["resume"].set()
    await task
    if change == "reject":
        assert state["row"]["status"] == "rejected"
    elif change == "redact":
        assert state["row"]["proposed_value"] == ""
    else:
        assert state["row"] is None and state["deleted"]


async def test_cap_refuses_before_claim_or_model(state: Any) -> None:
    state["cap"] = True
    with pytest.raises(ValidationError, match="spend cap"):
        await run(state)
    assert state["row"] is None and state["calls"] == 0


async def test_invalid_model_answer_keeps_claim_and_cannot_spend_twice(state: Any) -> None:
    state["answer"] = "not JSON"
    with pytest.raises(ValueError):
        await run(state)
    assert state["row"]["proposed_value"] == "null"
    await run(state)
    assert state["calls"] == 1


async def test_policy_hold_releases_only_the_unspent_claim_for_retry(state: Any) -> None:
    from server.llm.customer import ModelCallsDisabled

    state["hold"] = True
    with pytest.raises(ModelCallsDisabled):
        await run(state)
    assert state["row"] is None
    state["hold"] = False
    await run(state)
    assert json.loads(state["row"]["proposed_value"])["term"] == "Revenue"
