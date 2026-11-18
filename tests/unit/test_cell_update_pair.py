"""A configuration action reserves only its selected pair and preserves unknown fields."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from server.core import cell
from server.router import fingerprints


def value(issuer: str) -> dict[str, Any]:
    return cell.as_value({"bearer_issuers": [issuer], "future": {"nested": [False, 0]}})


@pytest.mark.parametrize(
    "action,pending,target,want",
    [
        ("config", None, "new", ("old", "new")),
        ("config", "new", "new", ("old", "new")),
        ("config", None, "old", ("old", None)),
        ("finish", "new", "new", ("new", None)),
        ("revert", "new", "old", ("old", None)),
        ("finish", None, "old", ("old", None)),
        ("revert", None, "old", ("old", None)),
    ],
)
def test_configuration_pair_selects_exact_reserved_values(
    action: str, pending: str | None, target: str, want: tuple[str, str | None]
) -> None:
    current, next_fp, chosen = value("old"), value(pending) if pending else None, value(target)
    before = deepcopy((current, next_fp, chosen))
    result = fingerprints.configuration_pair(current, next_fp, chosen, action)
    assert result == (value(want[0]), value(want[1]) if want[1] else None)
    assert (current, next_fp, chosen) == before


@pytest.mark.parametrize(
    "action,pending,target",
    [
        ("config", "new", "third"),
        ("config", "new", "old"),
        ("finish", "new", "old"),
        ("revert", "new", "new"),
        ("finish", None, "new"),
        ("revert", None, "new"),
        ("unsupported", "new", "new"),
    ],
)
def test_configuration_pair_refuses_a_different_pending_or_selected_target(
    action: str, pending: str | None, target: str
) -> None:
    with pytest.raises(ValueError):
        fingerprints.configuration_pair(
            value("old"), value(pending) if pending else None, value(target), action
        )


@pytest.mark.parametrize("broken", [None, {"v": True, "fields": {}}, {"v": 2, "fields": {}}])
def test_configuration_pair_never_invents_an_unreadable_current(broken: Any) -> None:
    with pytest.raises(ValueError, match="fingerprint"):
        fingerprints.configuration_pair(broken, None, value("new"), "config")


def test_configuration_pair_uses_type_sensitive_unknown_field_equality() -> None:
    current, pending, target = value("old"), value("new"), value("new")
    target["fields"]["future"]["nested"][0] = 0
    assert pending == target  # Python equality cannot implement the reservation CAS.
    with pytest.raises(ValueError, match="pending"):
        fingerprints.configuration_pair(current, pending, target, "config")


@pytest.mark.parametrize("action", ["config", "finish", "revert"])
def test_configuration_pair_refuses_scim_without_enforcement(action: str) -> None:
    target = value("new")
    target["fields"]["scim"] = True
    with pytest.raises(ValueError, match="per-cell SCIM"):
        fingerprints.configuration_pair(
            target if action == "revert" else value("old"),
            target if action == "finish" else None,
            target,
            action,
        )


def test_configuration_pair_allows_revert_from_an_existing_scim_candidate() -> None:
    pending = value("new")
    pending["fields"]["scim"] = True
    assert fingerprints.configuration_pair(value("old"), pending, value("old"), "revert") == (
        value("old"),
        None,
    )
