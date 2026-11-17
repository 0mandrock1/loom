"""Operator errors stay actionable without exposing database credentials."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from server.cli import tracker_feed as cli
from server.core.github_values import FeedInputError


def test_missing_credential_names_only_the_environment_variable(monkeypatch):
    monkeypatch.delenv("FEED_TEST_MISSING_SECRET", raising=False)
    with pytest.raises(FeedInputError, match="FEED_TEST_MISSING_SECRET"):
        cli._environment("FEED_TEST_MISSING_SECRET")


@pytest.mark.asyncio
async def test_deployment_connect_failure_still_disconnects(monkeypatch):
    from server.core import ch

    deployment = SimpleNamespace(
        connect=AsyncMock(side_effect=OSError("failed")), disconnect=AsyncMock()
    )
    monkeypatch.setattr(ch, "get_client", lambda: deployment)
    from server.settings import settings

    monkeypatch.setattr(settings, "tracker_org", "team")
    with pytest.raises(OSError):
        await cli._run(SimpleNamespace(feed_command="init", org="team"))
    deployment.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_connect_failure_still_disconnects(monkeypatch):
    from server.core import ch, hosts

    client = SimpleNamespace(
        connect=AsyncMock(side_effect=OSError("failed")), disconnect=AsyncMock()
    )
    monkeypatch.setattr(ch, "ExternalCHClient", lambda **kwargs: client)
    monkeypatch.setattr(hosts, "vet_host", AsyncMock(return_value=("", "203.0.113.1")))
    monkeypatch.setenv("FEED_TEST_WRITER_PASSWORD", "secret")
    endpoint = SimpleNamespace(host="record.example", database="record", port=8443, secure=True)
    args = SimpleNamespace(writer_user="writer", writer_password_env="FEED_TEST_WRITER_PASSWORD")
    with pytest.raises(OSError):
        await cli._connection(args, endpoint)
    client.disconnect.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "grant",
    [
        "GRANT ALL ON *.* TO writer",
        "GRANT INSERT ON loom.* TO writer",
        "GRANT broad_role TO writer",
    ],
)
async def test_record_connection_refuses_broad_or_role_writer_grants(monkeypatch, grant):
    from server.core import ch, hosts

    client = SimpleNamespace(
        connect=AsyncMock(),
        disconnect=AsyncMock(),
        query_all=AsyncMock(return_value=[{"grant": grant}]),
    )
    monkeypatch.setattr(ch, "ExternalCHClient", lambda **kwargs: client)
    monkeypatch.setattr(hosts, "vet_host", AsyncMock(return_value=("", "203.0.113.1")))
    monkeypatch.setenv("FEED_TEST_WRITER_PASSWORD", "secret")
    endpoint = SimpleNamespace(host="record.example", database="record", port=8443, secure=True)
    args = SimpleNamespace(writer_user="writer", writer_password_env="FEED_TEST_WRITER_PASSWORD")
    with pytest.raises(FeedInputError, match="writer grants"):
        await cli._connection(args, endpoint)
    client.disconnect.assert_awaited_once()


def test_unexpected_failure_does_not_print_secret(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_run", AsyncMock(side_effect=OSError("password=secret")))
    assert cli._command(SimpleNamespace()) == 1
    assert capsys.readouterr().err == "tracker feed refused: OSError\n"


@pytest.mark.asyncio
async def test_init_refuses_unusable_org_before_connecting(monkeypatch):
    from server.core import ch
    from server.settings import settings

    connect = AsyncMock()
    monkeypatch.setattr(settings, "tracker_org", "team")
    monkeypatch.setattr(ch, "get_client", connect)
    with pytest.raises(FeedInputError, match="configured tracker organization"):
        await cli._run(SimpleNamespace(feed_command="init", org="other"))
    connect.assert_not_called()
