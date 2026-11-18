"""A named coordination session belongs to one transport, not the shared pool."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from clickhouse_connect.driver.exceptions import DatabaseError

from server.core import ch


class _RoutedDriver:
    def __init__(self, *, pooled: bool) -> None:
        self.pooled = pooled
        self.calls: list[dict[str, Any]] = []
        self.sessions: dict[int, set[str]] = {0: set(), 1: set()}
        self.closed = False

    def _send(self, settings: dict[str, Any]) -> None:
        connection = len(self.calls) % 2 if self.pooled else 0
        self.calls.append(dict(settings))
        identifier = settings["session_id"]
        if settings["session_check"]:
            if identifier not in self.sessions[connection]:
                raise DatabaseError("Code: 372. SESSION_NOT_FOUND")
        else:
            self.sessions[connection].add(identifier)

    async def query(self, _sql: str, *, settings: Any, **_kwargs: Any) -> Any:
        self._send(settings)
        return SimpleNamespace(column_names=["value"], result_rows=[[1]], summary={})

    async def command(self, _sql: str, *, settings: Any, **_kwargs: Any) -> None:
        self._send(settings)

    async def raw_query(self, _sql: str, *, settings: Any, **_kwargs: Any) -> bytes:
        self._send(settings)
        return b"1"

    async def close(self) -> None:
        self.closed = True

    def _force_close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["query", "command", "raw"])
async def test_proof_uses_an_owned_transport_instead_of_rotating_the_shared_pool(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    shared = _RoutedDriver(pooled=True)
    channels: list[_RoutedDriver] = []

    async def create(**_kwargs: Any) -> _RoutedDriver:
        channel = _RoutedDriver(pooled=False)
        channels.append(channel)
        return channel

    monkeypatch.setattr(ch, "guard_pending_schema", AsyncMock())
    monkeypatch.setattr(ch.clickhouse_connect, "get_async_client", create)
    client: Any = ch.CHClient()
    client._client = shared
    client._connection_config = ch.connection_kwargs()
    async with client.coordination_session():
        if operation == "query":
            assert await client.query_one("SELECT 1") == {"value": 1}
        elif operation == "command":
            await client.execute("SELECT 1")
        else:
            assert await client.raw_bytes("SELECT 1") == b"1"
    assert client.client is shared and shared.calls == [] and not shared.closed
    assert len(channels) == 1 and channels[0].closed
    assert [call["session_check"] for call in channels[0].calls] == [0, 1]
    assert len({call["session_id"] for call in channels[0].calls}) == 1
