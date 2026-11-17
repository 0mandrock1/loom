"""Feed content publications advance beyond every persisted record version."""

from datetime import UTC, datetime, timedelta

import pytest

from server.tracker.store import github_feed as store
from tests.unit.ch_fake import RecordingCH

NOW = datetime(2025, 1, 2, microsecond=123000, tzinfo=UTC)
NOW_MS = int(NOW.timestamp()) * 1000 + NOW.microsecond // 1000
IDENTITY = {name: "team" if name == "org" else "-" for name in store.IDENTITY}


@pytest.mark.asyncio
@pytest.mark.parametrize("item_table", ["feed_issues", "feed_prs"])
async def test_item_then_comment_publication_advances_with_an_unchanged_record_clock(item_table):
    ch = RecordingCH()

    def publication_clock(sql, params):
        versions = [
            int(row["version"].timestamp()) * 1000 + row["version"].microsecond // 1000
            for call in ch.calls
            if call.kind == "insert"
            for row in call.rows
        ]
        return [{"now_ms": NOW_MS, "prior_ms": max(versions, default=0)}]

    ch.on("AS now_ms", publication_clock)
    await store.put_items(ch, item_table, [{"number": 1}], IDENTITY, "widgets/repo")
    await store.put_items(ch, "feed_comments", [{"comment_id": 1}], IDENTITY, "widgets/repo")
    item = ch.inserted(item_table)[0]["version"]
    comment = ch.inserted("feed_comments")[0]["version"]
    assert item == NOW
    assert comment == NOW + timedelta(milliseconds=1)
    assert item.microsecond % 1000 == comment.microsecond % 1000 == 0
    assert all(call.params == {"org": "team"} for call in ch.calls if call.kind == "query_one")


@pytest.mark.asyncio
async def test_publication_advances_beyond_persisted_versions_ahead_of_the_record_clock():
    ch = RecordingCH().on("AS now_ms", [{"now_ms": NOW_MS, "prior_ms": NOW_MS + 7}])
    await store.put_items(ch, "feed_comments", [{"comment_id": 1}], IDENTITY, "widgets/repo")
    assert ch.inserted("feed_comments")[0]["version"] == NOW + timedelta(milliseconds=8)


@pytest.mark.asyncio
async def test_other_repository_publication_exceeds_the_records_logical_watermark():
    ch = RecordingCH()
    await ch.insert(
        "feed_issues",
        [IDENTITY | {"repository": "widgets/first", "version": NOW + timedelta(milliseconds=7)}],
    )

    def publication_clock(sql, params):
        versions = [
            int(row["version"].timestamp()) * 1000 + row["version"].microsecond // 1000
            for call in ch.calls
            if call.kind == "insert"
            for row in call.rows
            if row["org"] == params["org"]
            and ("repo" not in params or row["repository"] == params["repo"])
        ]
        return [{"now_ms": NOW_MS, "prior_ms": max(versions, default=0)}]

    ch.on("AS now_ms", publication_clock)
    await store.put_items(ch, "feed_prs", [{"number": 1}], IDENTITY, "widgets/second")
    assert ch.inserted("feed_prs")[0]["version"] == NOW + timedelta(milliseconds=8)
