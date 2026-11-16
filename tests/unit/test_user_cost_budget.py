"""Named-user cost caps remain independent of the namespace ceiling."""

from __future__ import annotations

from typing import Any

import pytest

from server.insights.api.models_cost import BudgetStatusInput
from server.insights.app import cost
from server.observability import budget


class BudgetCH:
    def __init__(self, *, namespace_cap: float = 0, user_cap: float = 2, spent: float = 3) -> None:
        self.namespace_cap, self.user_cap, self.spent = namespace_cap, user_cap, spent
        self.reads: list[dict[str, Any]] = []
        self.inbox = ""

    async def query_one(self, sql: str, params: dict[str, Any]) -> dict[str, Any]:
        self.reads.append(params)
        if "FROM loom.namespaces" in sql:
            return {
                "daily_cost_cap_usd": self.namespace_cap,
                "daily_user_cost_cap_usd": self.user_cap,
                "budget_alert_inbox": self.inbox,
            }
        return {
            "spent": 10.0,
            "own": 0.0,
            "user_spent": self.spent if params.get("uid") == "alice" else 0.0,
            "user_own": 0.0,
        }


@pytest.fixture
def ch(monkeypatch: pytest.MonkeyPatch) -> BudgetCH:
    from server.settings import settings

    client = BudgetCH()
    monkeypatch.setattr("server.core.ch.get_client", lambda: client)
    monkeypatch.setattr(cost, "get_client", lambda: client)
    monkeypatch.setattr(settings, "default_daily_cost_cap_usd", 0)
    return client


async def test_user_cap_applies_with_an_uncapped_namespace(ch: BudgetCH) -> None:
    assert await budget.namespace_over_cap("o", "n", user_id="alice") is True
    assert await budget.namespace_over_cap("o", "n", user_id="bob") is False


async def test_user_cap_controls_budget_pressure_routing(ch: BudgetCH) -> None:
    assert await budget.namespace_budget_ratio("o", "n", user_id="alice") == 1.5
    assert await budget.namespace_budget_ratio("o", "n", user_id="bob") == 0.0


async def test_namespace_cap_still_stops_a_user_under_their_own_cap(ch: BudgetCH) -> None:
    ch.namespace_cap = 5
    assert await budget.namespace_over_cap("o", "n", user_id="bob") is True
    assert await budget.namespace_budget_ratio("o", "n", user_id="bob") == 2.0


async def test_an_unattributed_check_does_not_guess_a_user(ch: BudgetCH) -> None:
    assert await budget.namespace_over_cap("o", "n") is False
    assert await budget.namespace_budget_ratio("o", "n") == 0.0
    assert len(ch.reads) == 2, "uncapped anonymous checks need only their cap reads"


async def test_user_budget_status_uses_the_named_users_spend(ch: BudgetCH) -> None:
    out = await cost.handle_budget_status(
        BudgetStatusInput(org="o", namespace="n", user_id="alice")
    )
    assert out.user_id == "alice"
    assert (out.cap_usd, out.spent_24h_usd, out.cap_source) == (2.0, 3.0, "namespace_user")
    assert out.over_cap is True


async def test_user_status_reports_the_namespace_gate_too(ch: BudgetCH) -> None:
    ch.namespace_cap = 5
    out = await cost.handle_budget_status(BudgetStatusInput(org="o", namespace="n", user_id="bob"))
    assert out.over_cap is False and out.namespace_over_cap is True


async def test_global_user_cap_is_the_fallback(
    ch: BudgetCH, monkeypatch: pytest.MonkeyPatch
) -> None:
    from server.settings import settings

    ch.user_cap = 0
    monkeypatch.setattr(settings, "default_daily_user_cost_cap_usd", 1)
    out = await cost.handle_budget_status(
        BudgetStatusInput(org="o", namespace="n", user_id="alice")
    )
    assert out.over_cap and out.cap_source == "global_user" and out.cap_usd == 1


async def test_check_time_warning_threshold_and_scope(
    ch: BudgetCH, monkeypatch: pytest.MonkeyPatch
) -> None:
    from server.observability import budget_notifications
    from server.settings import settings

    warnings: list[budget_notifications.BudgetWarning] = []
    monkeypatch.setattr(budget_notifications, "_observer", warnings.append)
    monkeypatch.setattr(settings, "cost_budget_alert_share", 0.8)
    ch.inbox, ch.namespace_cap, ch.spent = "warnings", 20, 1.6
    assert not await budget.namespace_over_cap("o", "n", user_id="alice")
    assert len(warnings) == 1 and warnings[0].user_id == "alice"
    warnings.clear()
    await budget.namespace_over_cap("o", "n")
    assert warnings == [], "an unscoped check does not discover users"
    ch.namespace_cap = 10
    await budget.namespace_over_cap("o", "n")
    assert len(warnings) == 1 and warnings[0].user_id == ""


async def test_notification_failure_cannot_change_the_cap_decision(
    ch: BudgetCH, monkeypatch: pytest.MonkeyPatch
) -> None:
    from server.observability import budget_notifications

    def unavailable(warning: budget_notifications.BudgetWarning) -> None:
        raise RuntimeError("inbox delivery unavailable")

    ch.inbox = "warnings"
    monkeypatch.setattr(budget_notifications, "_observer", unavailable)
    assert await budget.namespace_over_cap("o", "n", user_id="alice")


async def test_user_status_requires_a_namespace(ch: BudgetCH) -> None:
    from server.core.errors import ValidationError

    with pytest.raises(ValidationError, match="namespace is required"):
        await cost.handle_budget_status(BudgetStatusInput(org="o", user_id="alice"))


@pytest.mark.parametrize("cascade", [False, True])
async def test_ask_admission_uses_the_same_named_user_as_its_ledger(
    ch: BudgetCH, monkeypatch: pytest.MonkeyPatch, cascade: bool
) -> None:
    from server.memory.api.models_ask import MemoryAskInput
    from server.memory.app.ask.handler import _ask_clients
    from server.settings import settings

    monkeypatch.setattr(settings, "memory_ask_synth_model_cheap", "fake-cheap" if cascade else "")
    state = await _ask_clients(
        MemoryAskInput(
            org="o", namespace="n", user_id="alice", query="explain", allow_synthesis=True
        ),
        settings,
    )
    assert state[4] is True
    assert state[:4] == (None, None, None, None)
    assert ch.reads[-1]["uid"] == "alice"
