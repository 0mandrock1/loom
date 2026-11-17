"""The actual native command cannot outlive its certified participant."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from server.cli import _ch, access
from server.core import retirement
from server.insights.app import provider_barrier as barrier
from server.insights.app import provider_command as command


@pytest.fixture
def native(monkeypatch):
    from server.core import background, ch, pg, pg_profile
    from server.core.model_work_gate import ModelWorkGate

    events = []
    gate = retirement.get_gate()
    client = SimpleNamespace(connected=False)

    async def connect():
        client.connected = True

    async def disconnect():
        client.connected = False

    client.connect, client.disconnect = connect, disconnect
    monkeypatch.setattr(ch, "get_client", lambda: client)
    monkeypatch.setattr(pg, "close_pool", AsyncMock())

    async def verified(_):
        assert not gate.enrolled
        events.append("verified")

    async def enroll():
        assert events == ["verified"]
        gate.enrolled = True
        events.append("enrolled")

    async def acceptance(**_):
        assert gate.enrolled and gate.active > 0 and not gate.closed
        events.append("acceptance")

    async def certificate(proof):
        assert gate.quiescent and client.connected
        assert events[-1] == "buffer"
        events.append("certificate")

    async def flush():
        events.append("buffer")
        return True

    monkeypatch.setattr(pg_profile, "check_configured", verified)
    monkeypatch.setattr(command, "register_process", enroll)
    monkeypatch.setattr(_ch, "check_acceptance", acceptance)
    model_gate = ModelWorkGate()
    monkeypatch.setattr(command, "get_model_work_gate", lambda: model_gate)
    monkeypatch.setattr(barrier, "get_model_work_gate", lambda: model_gate)
    monkeypatch.setattr(command, "load_active_configuration", AsyncMock())
    monkeypatch.setattr(command, "_adopt_overrides", AsyncMock(return_value=True))
    monkeypatch.setattr(background, "_registered_tasks", set())
    monkeypatch.setattr(command, "drain_task_sets", background.drain_task_sets)
    monkeypatch.setattr(barrier, "drain_task_sets", background.drain_task_sets)
    monkeypatch.setattr(command, "flush_registered_buffers", flush)
    monkeypatch.setattr(barrier, "flush_registered_buffers", flush)
    monkeypatch.setattr(barrier, "_server_side_settled", AsyncMock(return_value=True))
    monkeypatch.setattr(barrier, "running_queries", AsyncMock(return_value=0))
    monkeypatch.setattr(barrier, "_OWN", barrier._Own())
    monkeypatch.setattr(
        barrier, "_HEARTBEAT", SimpleNamespace(signal_stop=lambda: None, running=False)
    )
    monkeypatch.setattr(barrier, "record_retirement", certificate)
    return gate, events


def test_actual_claim_release_keeps_lookup_and_release_in_one_lifetime(native, monkeypatch):
    import asyncio

    from server.core.background import create_deferred_task
    from server.identity.app import claim_release
    from server.identity.store import namespaces

    gate, events = native

    async def lookup(*_, **__):
        assert gate.active > 0 and not gate.closed
        events.append("lookup")
        return {"owner_email": "owner@example.test"}

    async def release(**_):
        assert gate.active > 0 and gate.enrolled and not gate.closed
        assert "certificate" not in events
        events.append("release")

        async def descendant():
            assert gate.enrolled and gate.closed
            assert "certificate" not in events
            events.append("descendant")

        async def write():
            await asyncio.sleep(0)
            assert gate.enrolled and gate.closed
            assert "certificate" not in events
            events.append("deferred")
            create_deferred_task(descendant())

        create_deferred_task(write())
        return SimpleNamespace(grants_revoked=2)

    monkeypatch.setattr(namespaces, "fetch_row", lookup)
    monkeypatch.setattr(claim_release, "release_claim", release)
    assert access._cmd_claim_release(SimpleNamespace(org="o", namespace="n", yes=True)) == 0
    assert events.count("enrolled") == events.count("certificate") == 1
    assert events.index("lookup") < events.index("release") < events.index("certificate")
    assert events.index("release") < events.index("deferred") < events.index("descendant")
    assert events.index("descendant") < events.index("buffer") < events.index("certificate")
    assert gate.quiescent


@pytest.mark.parametrize("platform_admin", [False, True])
def test_actual_mint_readiness_keyring_signing_and_audit_precede_retirement(
    native, monkeypatch, platform_admin
):
    import argparse

    from server.auth import jwt, keyring, machines
    from server.observability import audit

    gate, events = native

    def observed(event):
        assert gate.active > 0 and gate.enrolled and not gate.closed
        assert "certificate" not in events
        events.append(event)

    async def ready(_):
        observed("readiness")
        return True

    async def refresh(**_):
        observed("keyring")
        return True

    def mint(**_):
        observed("mint")
        return "test-token"

    async def record(*_, **__):
        observed("audit")
        return "audit-id"

    monkeypatch.setattr(machines, "platform_admin_tokens_ready", ready)
    monkeypatch.setattr(keyring, "refresh", refresh)
    monkeypatch.setattr(jwt, "mint_token", mint)
    monkeypatch.setattr(audit, "emit", record)
    parser = argparse.ArgumentParser()
    access._add_mint_token_parser(parser.add_subparsers(dest="cmd"))
    argv = ["mint-token", "--subject", "sa:test", "--org", "o", "--ttl-days", "1"]
    argv += ["--platform-admin"] if platform_admin else ["--namespaces", "n"]
    assert access._cmd_mint_token(parser.parse_args(argv)) == 0
    assert events.count("enrolled") == events.count("certificate") == 1
    assert events.index("keyring") < events.index("mint") < events.index("certificate")
    if platform_admin:
        assert events.index("readiness") < events.index("keyring")
        assert events.index("mint") < events.index("audit") < events.index("certificate")
    assert gate.quiescent


@pytest.mark.parametrize("failure", ["unavailable", "wrong-pair"])
def test_actual_mint_never_signs_without_verified_enrollment(native, monkeypatch, failure):
    import argparse
    from unittest.mock import Mock

    from server.auth import jwt
    from server.core import pg_profile
    from server.core.pg import AuthorityMismatch

    gate, events = native

    async def refuse(_):
        if failure == "wrong-pair":
            raise AuthorityMismatch("different paired stores")
        raise ConnectionError("unavailable")

    minted = Mock(side_effect=AssertionError("unenrolled mint"))
    monkeypatch.setattr(pg_profile, "check_configured", refuse)
    monkeypatch.setattr(jwt, "mint_token", minted)
    parser = argparse.ArgumentParser()
    access._add_mint_token_parser(parser.add_subparsers(dest="cmd"))
    args = parser.parse_args(
        ["mint-token", "--subject", "sa:test", "--org", "o", "--namespaces", "n"]
    )
    if failure == "wrong-pair":
        with pytest.raises(AuthorityMismatch):
            access._cmd_mint_token(args)
    else:
        assert access._cmd_mint_token(args) == 1
    minted.assert_not_called()
    assert not gate.enrolled and not gate.closed and events == []
