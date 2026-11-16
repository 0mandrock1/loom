"""A cell reserves both accepted values against every other cell's values."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from server.core import cell


def _value(**fields: Any) -> dict[str, Any]:
    return cell.as_value(deepcopy(cell.DEFAULTS) | fields)


@pytest.mark.parametrize(
    "left,right,reason",
    [
        ({"bearer_issuers": ["issuer"]}, {"bearer_issuers": ["issuer"]}, "bearer"),
        (
            {"oidc_issuer": "https://login/", "oidc_client_id": "client"},
            {"oidc_issuer": "https://login", "oidc_client_id": "client"},
            "OIDC",
        ),
        ({"end_user_orgs": ["team-*"]}, {"end_user_orgs": ["team-one*"]}, "pattern"),
        ({"end_user_orgs": ["team-*"]}, {"configured_orgs": ["team-one"]}, "configured"),
        ({"configured_orgs": ["team-one"]}, {"end_user_orgs": ["team-*"]}, "configured"),
        ({"configured_orgs": ["same"]}, {"configured_orgs": ["same"]}, "configured"),
    ],
)
def test_conflicts_include_next_and_both_directions(
    left: dict[str, Any], right: dict[str, Any], reason: str
) -> None:
    from server.router.fingerprints import reservation_refusal

    assert reason in reservation_refusal([_value(), _value(**left)], [_value(**right)])
    assert reason in reservation_refusal([_value(**left)], [_value(), _value(**right)])
    assert reason in reservation_refusal([_value(**right)], [_value(**left)])


def test_configured_orgs_are_literal_and_empty_oidc_pair_is_unreserved() -> None:
    from server.router.fingerprints import reservation_refusal

    assert (
        reservation_refusal(
            [_value(configured_orgs=["team*"])], [_value(configured_orgs=["teams"])]
        )
        == ""
    )
    assert (
        reservation_refusal([_value(configured_orgs=["team*"])], [_value(end_user_orgs=["teams*"])])
        == ""
    )
    assert reservation_refusal([_value(oidc_issuer="issuer")], [_value(oidc_issuer="issuer")]) == ""
    assert (
        reservation_refusal([_value(oidc_client_id="client")], [_value(oidc_client_id="client")])
        == ""
    )


@pytest.mark.parametrize(
    "value",
    [
        {"v": True, "fields": {}},
        {"v": 99, "fields": {}},
        {"v": 1, "fields": {"scim": 0}},
        {"v": 1, "fields": {"bearer_issuers": "issuer"}},
        {"v": 1, "fields": {"configured_orgs": ["*"]}},
        {"v": 1, "fields": {"configured_orgs": ["-"]}},
        {"v": 1, "fields": {"configured_orgs": [""]}},
        {"v": 1, "fields": {"configured_orgs": ["x" * (cell.ORG_MAX_BYTES + 1)]}},
        {"v": 1, "fields": {"end_user_orgs": ["*"]}},
        {"v": 1, "fields": {"public_host": "bad\0host"}},
        {"v": 1, "fields": {"future": {"nested": "\ud800"}}},
        {"v": 1, "fields": {"future": float("nan")}},
        {"v": 1, "fields": {"future": ("hidden\0text",)}},
        {"v": 1, "fields": {"future": {1: "non-string key"}}},
    ],
)
def test_unreadable_reservations_refuse_on_either_side(value: Any) -> None:
    from server.router.fingerprints import reservation_refusal

    assert "unreadable" in reservation_refusal([_value()], [value])
    assert "unreadable" in reservation_refusal([value], [_value()])


def test_known_defaults_and_unknown_fields_survive_validation() -> None:
    from server.router.fingerprints import reservation_refusal

    old = {"v": 1, "fields": {}}
    newer = {"v": 1, "fields": {"future": {"items": [1, True, None]}}}
    before = deepcopy(newer)
    assert reservation_refusal([old], [newer]) == ""
    assert newer == before


def test_cycle_and_non_json_unknowns_fail_closed() -> None:
    from server.router.fingerprints import reservation_refusal

    cyclic = _value()
    cyclic["fields"]["future"] = cyclic
    assert "unreadable" in reservation_refusal([_value()], [cyclic])
    assert "unreadable" in reservation_refusal([_value()], [_value(future=object())])


def test_candidate_fits_a_live_placement_with_the_largest_org() -> None:
    import json

    from server.router.fingerprints import placement_refusal
    from server.router.limits import MAX_BODY_BYTES

    value = _value(future="")
    overhead = len(json.dumps({"org": "", "cell": "first", "fingerprint": value}))
    room = MAX_BODY_BYTES - overhead - 6 * cell.ORG_MAX_BYTES
    value["fields"]["future"] = "x" * room
    assert placement_refusal("first", "candidate", value) == ""
    value["fields"]["future"] += "x"
    reason = placement_refusal("first", "candidate", value)
    assert "future" in reason
    assert "x" * 20 not in reason
