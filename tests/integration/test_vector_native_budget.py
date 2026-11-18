"""Native vector row ceilings refuse complete reads and release their credits."""

from __future__ import annotations

import random
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from clickhouse_connect.driver.exceptions import DatabaseError

from server.core import ch
from server.core.ch_errors import error_code
from server.core.errors import VectorAdmissionError
from server.core.vectors import admission
from server.settings import settings
from tests.integration._eventually import wait_until
from tests.integration._resources import memory_rows, minted_identity, unique_org
from tests.integration._spawn import loom_server

pytestmark = pytest.mark.usefixtures("ch_target_required")


async def test_native_row_ceiling_refuses_the_read_and_releases_credits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "vector_scan_max_rows_to_read", 16)
    monkeypatch.setattr(settings, "vector_scan_max_concurrent_per_org", 2)
    monkeypatch.setattr(settings, "vector_scan_max_waiters_per_org", 2)
    monkeypatch.setattr(settings, "ch_pool_maxsize", 2)
    monkeypatch.setattr(settings, "ch_pool_wait_ms", 500)
    org = unique_org("vector-native-budget")
    sql = "SELECT sum(number) AS total FROM numbers({rows:UInt64})"
    client = ch.CHClient()
    gate = admission._admission
    population_before = gate.population
    pool_before = ch.pool_reading()
    work_before = admission.current_work()
    try:
        with ch.query_tag("itest.vector-native-budget"), admission.vector_work("", (), 0):
            await client.connect()
            assert await client.query_all(sql, {"rows": 64}) == [{"total": 2016}]
            with admission.vector_work(org, "native-budget", weight=2):
                result: object = None
                with pytest.raises(VectorAdmissionError) as caught:
                    result = await client.query_all(sql, {"rows": 64})
                assert result is None
                assert type(caught.value) is VectorAdmissionError
                assert caught.value.code == "VECTOR_CAPACITY_REFUSED"
                assert caught.value.status_code == 429
                assert caught.value.extra == {"reason": "rows", "retry_after_s": 1}
                assert caught.value.headers() == {"Retry-After": "1"}
                assert isinstance(caught.value.__cause__, DatabaseError)
                assert error_code(caught.value.__cause__) == 158
                assert gate.population == population_before
                assert org not in gate.orgs
                assert ch.pool_reading() == pool_before
                assert client._inflight == {}
                assert client._retired == {}

                assert await client.query_all(sql, {"rows": 4}) == [{"total": 6}]
            assert admission.current_work() is None
            assert gate.population == population_before
            assert org not in gate.orgs
            assert ch.pool_reading() == pool_before
            assert client._inflight == {}
            assert client._retired == {}
    finally:
        await client.disconnect()
    assert admission.current_work() == work_before


_BUDGET_WORDS = (
    "amber anchor apple arrow autumn bamboo barley beach berry birch bridge bronze "
    "cabin canal candle canyon carbon cedar chalk cherry cipher citrus cliff cloud "
    "cobalt comet copper coral cotton crane crystal desert dune eagle echo ember "
    "engine falcon fern field flint forest fossil garden garnet glacier granite "
    "harbor hazel helium heron hollow honey horizon indigo island ivory jade jasper "
    "juniper kernel kettle lagoon lantern laurel lemon linen lotus magnet maple "
    "marble meadow meteor mint mirror mosaic nectar nickel north oasis ocean olive "
    "onyx orbit orchid oyster pebble pepper pine planet plaza pollen prism quartz "
    "quill radar raven reef ridge river rocket saffron sage salmon sapphire shadow "
    "signal silver slate solar spruce summit sunset thistle thunder timber topaz "
    "tulip tundra valley velvet violet walnut willow winter zephyr"
).split()


def _budget_value(index: int) -> str:
    # Ordinary words keep the real sensitivity and conflict checks in force.
    return " ".join(random.Random(index).sample(_BUDGET_WORDS, 12))


@pytest.fixture
def native_api_budget(
    control_plane_url: str, mint_test_token: Any, ch_client: Any
) -> Iterator[tuple[httpx.Client, httpx.Client, dict[str, str], Any]]:
    principal = minted_identity(mint_test_token, "vector-api-budget")
    identity = {
        "org": principal["org"],
        "namespace": principal["namespace"],
        "agent": "native-budget",
        "user_id": "native-user",
        "session_id": "native-session",
    }
    headers = {"Authorization": f"Bearer {principal['token']}"}
    with httpx.Client(base_url=control_plane_url, headers=headers, timeout=30) as ordinary:
        for index in range(32):
            response = ordinary.post(
                "/v1/memory.set",
                json={
                    **identity,
                    "memory_key": f"budget-{index:02d}",
                    "memory_type": "semantic",
                    "value": _budget_value(index),
                },
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["memory_key"] == f"budget-{index:02d}"
            assert body["status"] == "active" and body["version"] > 0 and not body["noop"]

        def all_seed_embeddings() -> bool:
            response = ordinary.post(
                "/v1/memory.search_trace",
                json={**identity, "query": _budget_value(0), "top_k": 64, "auto_touch": False},
            )
            assert response.status_code == 200, response.text
            keys = [row["memory_key"] for row in response.json()["results"]]
            assert len(keys) == len(set(keys))
            return set(keys) == {f"budget-{index:02d}" for index in range(32)}

        # This establishes the seed precondition, not a write-visibility timing result.
        wait_until(all_seed_embeddings, description="all native budget seed embeddings")
        with (
            loom_server(
                "vector-api-budget",
                {
                    "LOOM_AUTH_REQUIRED": "true",
                    "LOOM_JWT_SECRET": settings.jwt_secret,
                    "LOOM_BACKGROUND_WORKERS": "off",
                    "LOOM_VECTOR_SCAN_MAX_ROWS_TO_READ": "16",
                },
            ) as base,
            httpx.Client(base_url=base, headers=headers, timeout=30) as capped,
        ):
            scalar = capped.post("/v1/memory.get", json={**identity, "memory_key": "budget-00"})
            assert scalar.status_code == 200 and scalar.json()["found"] is True
            assert scalar.json()["value"] == _budget_value(0)
            yield ordinary, capped, identity, ch_client


@pytest.mark.parametrize("operation", ["search", "set", "update", "bulk_set"])
def test_native_api_row_refusal_preserves_committed_heads(
    native_api_budget: tuple[httpx.Client, httpx.Client, dict[str, str], Any],
    operation: str,
) -> None:
    ordinary, capped, identity, store = native_api_budget
    before = memory_rows(store, identity["org"])
    assert len(before) == 32
    head = ordinary.post("/v1/memory.get", json={**identity, "memory_key": "budget-00"})
    assert head.status_code == 200 and head.json()["found"] is True
    version_before = head.json()["version"]
    assert version_before > 0
    key = "budget-00" if operation == "update" else "refused-new"
    if operation == "search":
        op = "memory.search"
        payload: dict[str, Any] = {
            **identity,
            "query": _budget_value(0),
            "top_k": 8,
            "auto_touch": False,
        }
    elif operation == "update":
        op = "memory.update"
        payload = {**identity, "memory_key": key, "value": _budget_value(100)}
    else:
        entry = {"memory_key": key, "memory_type": "semantic", "value": _budget_value(100)}
        op = "memory.bulk_set" if operation == "bulk_set" else "memory.set"
        payload = (
            {**identity, "entries": [entry]}
            if operation == "bulk_set"
            else {
                **identity,
                **entry,
            }
        )
    request_id = uuid.uuid4().hex
    response = capped.post(f"/v1/{op}", json=payload, headers={"X-Request-Id": request_id})
    assert response.status_code == 429, response.text
    assert response.json()["detail"]["code"] == "VECTOR_CAPACITY_REFUSED"
    assert response.headers["Retry-After"] == "1"
    assert response.headers["X-Request-Id"] == request_id
    # A retryable response must be caused by the real rows ceiling, not a queue refusal.
    store.command("SYSTEM FLUSH LOGS")
    witnesses = store.query(
        "SELECT exception_code, Settings['max_rows_to_read'] FROM system.query_log "
        "WHERE log_comment = {tag:String} AND is_initial_query "
        "AND type IN ('ExceptionBeforeStart', 'ExceptionWhileProcessing') LIMIT 65",
        parameters={"tag": f"{op} {request_id}"},
    ).result_rows
    assert 0 < len(witnesses) <= 64
    assert any(int(code) == 158 and limit == "16" for code, limit in witnesses)
    assert memory_rows(store, identity["org"]) == before
    found = ordinary.post("/v1/memory.get", json={**identity, "memory_key": key})
    assert found.status_code == 200
    assert found.json()["found"] is (operation == "update")
    if operation == "update":
        assert found.json()["value"] == _budget_value(0)
    # The capped process still handles a complete scalar read after the refused vector work.
    scalar = capped.post("/v1/memory.get", json={**identity, "memory_key": "budget-00"})
    assert scalar.status_code == 200 and scalar.json()["found"] is True
    assert scalar.json()["value"] == _budget_value(0)
    assert scalar.json()["version"] == version_before
