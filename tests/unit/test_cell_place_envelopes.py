"""Every accepted fingerprint fits both placement envelopes for every valid org."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from starlette.requests import Request

from server.core import cell
from server.router import app, directory
from server.router.limits import MAX_BODY_BYTES
from server.settings import settings

DEPLOYMENT = str(UUID(int=1))
TOKEN = "lco_" + "7" * 128


def fingerprint():
    return cell.as_value(
        {
            **cell.DEFAULTS,
            "deployment_id": DEPLOYMENT,
            "public_host": "first.example.test",
            "oauth_issuer_host": "first.example.test",
        }
    )


def envelopes(name, value, org=""):
    return (
        {"org": org, "cell": name, "fingerprint": value},
        {"org": org, "deployment_id": DEPLOYMENT, "fingerprint": value},
    )


def attaching(name):
    row = SimpleNamespace(
        cell=name,
        is_first=True,
        state="attaching",
        mode="recording",
        deployment_id=DEPLOYMENT,
        current_fp=None,
        next_fp=None,
        host="first.example.test",
    )
    return SimpleNamespace(row=row, other=None, attach_started_at=None, bucket="", bucket_prefix="")


def begin(value, position):
    return directory.Begin(
        DEPLOYMENT,
        value if position == "current_fp" else fingerprint(),
        value if position == "next_fp" else None,
        "a" * 64,
        {},
    )


@pytest.mark.parametrize("name", ["a", "a" * 32])
@pytest.mark.parametrize("position", ["current_fp", "next_fp"])
def test_begin_reserves_operator_envelope_before_accepting_fingerprint(name, position):
    value = fingerprint()
    live = envelopes(name, value)[0]
    # This fits the previous live-only reservation, but not the operator envelope.
    live_size = len(json.dumps(live)) + 6 * cell.ORG_MAX_BYTES
    value["fields"]["oidc_client_id"] = "x" * (MAX_BODY_BYTES - live_size)
    reason = directory.begin_refusal(attaching(name), begin(value, position))
    assert position in reason and str(MAX_BODY_BYTES) in reason
    assert "oidc_client_id" in reason


@pytest.mark.parametrize("name", ["a", "a" * 32])
@pytest.mark.parametrize("position", ["current_fp", "next_fp"])
@pytest.mark.parametrize("size", ["default", "boundary"])
@pytest.mark.parametrize("org", ["acme", "\x01" * cell.ORG_MAX_BYTES])
async def test_accepted_fingerprint_fits_both_encodings_and_operator_handler(
    monkeypatch, name, position, size, org
):
    value = fingerprint()
    if size == "boundary":
        empty_size = max(len(json.dumps(body)) for body in envelopes(name, value))
        value["fields"]["oidc_client_id"] = "x" * (
            MAX_BODY_BYTES - empty_size - 6 * cell.ORG_MAX_BYTES
        )
    assert cell.named_org(org) == org and not cell.org_name_refusal(org)
    attempt = begin(value, position)
    assert directory.begin_refusal(attaching(name), attempt) == ""
    bodies = envelopes(name, value, org)
    encoded = [
        (json.dumps(body).encode(), httpx.Request("POST", "http://inert.test", json=body).content)
        for body in bodies
    ]
    assert all(len(raw) <= MAX_BODY_BYTES for pair in encoded for raw in pair)
    calls = []

    async def place(actual_org, actual_name, refusal, *, actor, verify):
        calls.append((actual_org, actual_name, actor))
        locked = SimpleNamespace(
            deployment_id=DEPLOYMENT,
            current_fp=attempt.current_fp,
            next_fp=attempt.next_fp,
        )
        assert verify(locked) == ""
        return name, DEPLOYMENT

    monkeypatch.setattr(directory, "place", place)
    monkeypatch.setattr(
        settings, "router_operator_sha256", hashlib.sha256(TOKEN.encode()).hexdigest()
    )
    for raw in encoded[1]:
        scope = {
            "type": "http",
            "method": "POST",
            "path": f"/cells/v1/operator/cells/{name}/place",
            "path_params": {"cell": name},
            "headers": [
                (b"authorization", f"Bearer {TOKEN}".encode()),
                (b"content-length", str(len(raw)).encode()),
            ],
        }

        async def receive(raw=raw):
            return {"type": "http.request", "body": raw, "more_body": False}

        response = await app.Router().operator_place(Request(scope, receive))
        assert response.status_code == 200
        assert json.loads(response.body) == {"org": org, "cell": name, "deployment_id": DEPLOYMENT}
    assert calls == [(org, name, "operator")] * 2
