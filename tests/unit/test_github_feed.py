"""A copied tracker record keeps upstream identities and complete cursor boundaries."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from server.tracker.domain import github_feed as feed

NOW = datetime(2025, 1, 2, tzinfo=UTC)


def item(number: int, seconds: int = 0, **fields):
    return {
        "id": number + 100,
        "number": number,
        "title": "Retention decision",
        "body": "Keep an explicit retention period.",
        "user": {"login": "reader"},
        "created_at": NOW.isoformat(),
        "updated_at": (NOW + timedelta(seconds=seconds)).isoformat(),
        **fields,
    }


def test_copied_item_matches_contract_identity_and_defaults():
    row = feed.item_row("widgets/repo", item(7), is_pr=False)
    assert row["source_id"] == "widgets/repo#7"
    assert row["id"] == uuid.uuid5(uuid.NAMESPACE_DNS, "github_issue:widgets/repo#7")
    assert row["author"] == "reader" and row["number"] == 7
    assert row["closed_at"] == datetime(1970, 1, 1, tzinfo=UTC)
    assert row["labels"] == [] and row["is_private"] == 0
    assert row["triage_clean_on"] == [] and row["triage_component"] == ""
    assert row["updated_at"] == NOW


def test_pr_keeps_merge_state_files_and_reviewers():
    row = feed.item_row(
        "widgets/repo",
        item(
            7,
            merged_at=NOW.isoformat(),
            state="closed",
            base={"ref": "main"},
            requested_reviewers=[{"login": "maintainer"}],
            additions=8,
            deletions=2,
        ),
        is_pr=True,
    )
    assert row["state"] == "merged" and row["is_merged"] == 1
    assert row["reviewers"] == ["maintainer"] and row["base_branch"] == "main"
    assert row["additions"] == 8 and row["deletions"] == 2


def test_full_budget_replays_last_incomplete_timestamp():
    batch = [item(n, 1 if n < 60 else 2) for n in range(100)]
    boundary = NOW + timedelta(seconds=1)
    assert feed.next_cursor(batch, complete=False, previous=NOW) == boundary
    with pytest.raises(feed.FeedCapacityError):
        feed.next_cursor(batch, complete=False, previous=boundary)
    assert feed.since_parameter(NOW) == "2025-01-01T23:59:59Z"


def test_dense_timestamp_refuses_silent_stall():
    with pytest.raises(feed.FeedCapacityError, match="timestamp"):
        feed.next_cursor([item(n) for n in range(100)], complete=False, previous=NOW)


def test_complete_scan_advances_and_empty_scan_preserves_checkpoint():
    assert feed.next_cursor([item(1, 2)], complete=True, previous=NOW) == NOW + timedelta(seconds=2)
    assert feed.next_cursor([], complete=True, previous=NOW) == NOW


@pytest.mark.parametrize("repo", ["../secret", "a/b/c", "a/%2f", "a/b?token=secret", "a/b\n"])
def test_repository_path_cannot_change_origin_or_endpoint(repo):
    with pytest.raises(feed.FeedInputError):
        feed.repository(repo)


def test_comment_parent_url_is_validated_without_following_it():
    row = feed.comment_row(
        "widgets/repo",
        {
            **item(1),
            "issue_url": "https://api.github.com/repos/widgets/repo/issues/7",
        },
        review=False,
    )
    assert row["item_number"] == 7 and row["comment_id"] == 101
    with pytest.raises(feed.FeedInputError):
        feed.comment_row(
            "widgets/repo", {**item(1), "issue_url": "https://evil.example/issues/7"}, review=False
        )


def test_invalid_update_time_refuses_checkpoint_and_content():
    with pytest.raises(feed.FeedInputError):
        feed.item_row("widgets/repo", item(1, updated_at="bad-time"), is_pr=False)
