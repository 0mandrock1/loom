"""Feed publication timestamps keep late comments in incremental tracker reads."""

from datetime import UTC, datetime

import pytest

from server.tracker.store import github_feed as store
from server.tracker.store.source.embed import EmbedReads
from tests.unit.clickhouse_local import CLICKHOUSE, run

pytestmark = pytest.mark.skipif(CLICKHOUSE is None, reason="clickhouse binary not installed")


@pytest.mark.parametrize("view,raw", [("github_issues", "feed_issues"), ("github_prs", "feed_prs")])
@pytest.mark.parametrize("edit", [False, True])
@pytest.mark.asyncio
async def test_late_comment_publication_advances_the_saved_tracker_cursor(view, raw, edit):
    setup = [
        "USE loom",
        *store.statements(),
        "INSERT INTO feed_owner (org, owner_uuid) "
        "VALUES ('team', '00000000-0000-0000-0000-000000000001')",
        "INSERT INTO feed_repositories "
        "(org, repository, owner_uuid, available, visible_until) "
        "VALUES ('team', 'widgets/repo', '00000000-0000-0000-0000-000000000001', "
        "1, '2100-01-01 00:00:00')",
        f"INSERT INTO {raw} "
        "(org, repository, number, source_id, body, updated_at, version) "
        "VALUES ('team', 'widgets/repo', 1, 'widgets/repo#1', 'Item body', "
        "'2000-01-01 00:00:00', '2025-01-02 00:00:00.000001')",
        f"CREATE TABLE saved_cursor ENGINE = Memory AS SELECT updated_at, source_id FROM {view}",
        "INSERT INTO feed_comments "
        "(org, repository, comment_id, item_number, body, updated_at, version) "
        "VALUES ('team', 'widgets/repo', 1, 1, 'Late reply', "
        "'1999-01-01 00:00:00', '2025-01-02 00:00:00.000002')",
    ]
    if edit:
        setup += [
            "TRUNCATE TABLE saved_cursor",
            f"INSERT INTO saved_cursor SELECT updated_at, source_id FROM {view}",
            "INSERT INTO feed_comments "
            "(org, repository, comment_id, item_number, body, updated_at, version) "
            "VALUES ('team', 'widgets/repo', 1, 1, 'Edited reply', "
            "'1999-01-01 00:00:01', '2025-01-02 00:00:00.000003')",
        ]
    rows = run(
        setup,
        f"SELECT body, toString(updated_at) AS published FROM {view} "
        "WHERE (updated_at, source_id) > (SELECT updated_at, source_id FROM saved_cursor)",
    )
    assert rows == [
        {
            "body": "Item body\n" + ("Edited reply" if edit else "Late reply"),
            "published": "2025-01-02 00:00:00.00000" + ("3" if edit else "2"),
        }
    ]

    class Source(EmbedReads):
        def __init__(self):
            self.db = "loom"

        def repos(self):
            return ["widgets/repo"]

        def _privacy(self):
            return "is_private = 0 AND "

        async def _rows(self, name, sql, params, **kwargs):
            return run(setup, sql, params)

    source = Source()
    cursor = datetime(2025, 1, 2, microsecond=2 if edit else 1, tzinfo=UTC)
    published = cursor.replace(microsecond=3 if edit else 2)
    kind = "issue" if view == "github_issues" else "pr"
    for since_id in (None, "widgets/repo#1"):
        options = dict(kind=kind, closed_days=365, body_chars=600, since_id=since_id)
        changed = await source.embed_items("team", "feed", since=cursor, **options)
        assert len(changed) == 1 and changed[0]["body"] == rows[0]["body"]
        assert await source.embed_items("team", "feed", since=published, **options) == []
