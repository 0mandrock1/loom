"""Renewal pages advance past failed sources and reset exhausted scans durably."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from server.knowledge.domain.source_push import PushRegistration
from server.knowledge.workers import source_push as worker
from server.workers import pass_context


def registration(source_id: str) -> PushRegistration:
    return PushRegistration(
        org="org",
        namespace="docs",
        name="source",
        source_id=source_id,
        provider="gdrive",
        generation="gen",
        fingerprint="fp",
    )


@pytest.mark.asyncio
async def test_failed_source_does_not_starve_following_row(monkeypatch: pytest.MonkeyPatch) -> None:
    context = pass_context.begin("knowledge_source_push", resume_cursor='["before", "id"]')
    page = AsyncMock(return_value=[registration("one"), registration("two")])
    renew = AsyncMock(side_effect=[ConnectionError("temporary"), None])
    monkeypatch.setattr(worker, "require_ready", AsyncMock())
    monkeypatch.setattr(worker, "drive_page", page)
    monkeypatch.setattr(worker, "renew_drive", renew)
    result = await worker.run_pass()
    page.assert_awaited_once_with(("before", "id"))
    assert result == {"items_planned": 2, "items_done": 1, "failed": 1}
    assert context.count == 1 and json.loads(context.cursor) == ["", ""]


@pytest.mark.asyncio
async def test_page_cut_persists_last_attempted_row(monkeypatch: pytest.MonkeyPatch) -> None:
    context = pass_context.begin("knowledge_source_push")
    monkeypatch.setattr(worker, "require_ready", AsyncMock())
    monkeypatch.setattr(
        worker, "drive_page", AsyncMock(return_value=[registration("one"), registration("two")])
    )
    renew = AsyncMock()
    monkeypatch.setattr(worker, "renew_drive", renew)
    clock = iter([0.0, 0.0, 80.0])
    monkeypatch.setattr(worker, "time", SimpleNamespace(monotonic=lambda: next(clock, 80.0)))
    result = await worker.run_pass()
    assert result["items_done"] == 1
    assert json.loads(context.cursor) == ["org", "one"]
    renew.assert_awaited_once()
