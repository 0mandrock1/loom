"""Record writers retain their explicit target across reconnects."""

from datetime import UTC
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from server.core import ch


@pytest.mark.asyncio
async def test_external_client_uses_only_explicit_connection_and_utc(monkeypatch):
    driver = SimpleNamespace(server_tz=None, close=AsyncMock())
    connect = AsyncMock(return_value=driver)
    monkeypatch.setattr(ch.clickhouse_connect, "get_async_client", connect)
    client = ch.ExternalCHClient(
        host="record.example",
        port=8443,
        secure=True,
        database="team_record",
        username="record_writer",
        password="secret",
    )
    await client.connect()
    assert connect.call_args.kwargs["host"] == "record.example"
    assert connect.call_args.kwargs["database"] == "team_record"
    assert connect.call_args.kwargs["username"] == "record_writer"
    assert driver.server_tz is UTC
    await client._reconnect(0)
    assert connect.call_count == 2
    assert connect.call_args.kwargs["database"] == "team_record"
    await client.disconnect()


@pytest.mark.asyncio
async def test_external_failed_connect_keeps_existing_client(monkeypatch):
    driver = SimpleNamespace(server_tz=None, close=AsyncMock())
    connect = AsyncMock(side_effect=[driver, OSError("connection refused")])
    monkeypatch.setattr(ch.clickhouse_connect, "get_async_client", connect)
    client = ch.ExternalCHClient(
        host="record.example",
        port=8443,
        secure=True,
        database="team_record",
        username="record_writer",
        password="secret",
    )
    await client.connect()
    with pytest.raises(OSError):
        await client._reconnect(0)
    assert client.client is driver and driver.close.await_count == 0
    await client.disconnect()
