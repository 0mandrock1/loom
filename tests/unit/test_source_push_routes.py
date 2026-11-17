"""Provider ingress bounds its bytes and acknowledges with the required status."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.knowledge.app import source_push_routes as routes
from server.knowledge.domain.source_push import MAX_BODY_BYTES, DeliveryRefused

URL = (
    "/hooks/knowledge/org/12345678-1234-1234-1234-123456789abc/22345678-1234-1234-1234-123456789abc"
)


def client() -> TestClient:
    app = FastAPI()
    routes.attach(app)
    return TestClient(app)


def test_verified_delivery_acknowledges_200_inside_provider_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receive = AsyncMock()
    monkeypatch.setattr(routes, "receive", receive)
    assert client().post(URL, content=b"signal").status_code == 200
    assert routes.CALLBACK_TIMEOUT_S < 5
    assert receive.call_args.args[3] == b"signal"


def test_oversized_delivery_never_reaches_store(monkeypatch: pytest.MonkeyPatch) -> None:
    receive = AsyncMock()
    monkeypatch.setattr(routes, "receive", receive)
    assert client().post(URL, content=b"x" * (MAX_BODY_BYTES + 1)).status_code == 413
    receive.assert_not_awaited()


def test_unverifiable_delivery_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        routes, "receive", AsyncMock(side_effect=DeliveryRefused("unverifiable delivery"))
    )
    assert client().post(URL, content=b"signal").status_code == 403
