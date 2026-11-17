"""The deployment CLI revokes one exact subject across orgs through shared steps."""

from __future__ import annotations

import argparse
import asyncio
from types import SimpleNamespace
from typing import Any

import pytest


@pytest.mark.parametrize("subject", ["sa:worker", "person@example.com"])
def test_ordinary_cli_global_subject_is_recorded_and_finished_without_org_expansion(
    subject: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from server.auth import subject_revocation
    from server.cli import access

    calls: list[tuple[str, str, dict[str, Any]]] = []
    governing = {"reason": "retire", "issued_before": None, "expires_at": None}

    async def record(value: str, **kwargs: Any) -> tuple[dict[str, Any], bool]:
        calls.append(("record", value, kwargs))
        return governing, False

    async def finish(value: str, row: Any, kept: bool, **kwargs: Any) -> Any:
        assert row is governing and kept is False
        calls.append(("finish", value, kwargs))
        return SimpleNamespace(row=row, kept=kept, sessions=0, tokens=0)

    def no_org_expansion(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the global command must not use org alias expansion")

    monkeypatch.setattr(subject_revocation, "record", record)
    monkeypatch.setattr(subject_revocation, "finish", finish)
    monkeypatch.setattr(subject_revocation, "revoke_in_org", no_org_expansion)
    monkeypatch.setattr(access, "run_connected", lambda work, **kwargs: asyncio.run(work(None)))
    monkeypatch.setattr(access, "_print_revoked", lambda *args, **kwargs: None)
    parser = argparse.ArgumentParser()
    access._add_revoke_token_parser(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(["revoke-token", "--subject", subject, "--reason", "retire"])
    assert access._cmd_revoke_token(args) == 0
    assert [name for name, _, _ in calls] == ["record", "finish"]
    assert all(value == subject for _, value, _ in calls)
    assert calls[0][2]["reason"] == "retire"
    assert calls[1][2]["org"] == "-"
    assert calls[0][2]["by"] == calls[1][2]["by"]
    assert calls[0][2]["by"].startswith("cli:")
