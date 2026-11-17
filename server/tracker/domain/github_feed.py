"""Bounded transformations for the team's GitHub record."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from server.core.github_values import PAGE_SIZE
from server.core.github_values import FeedInputError as FeedInputError
from server.core.github_values import repository as repository
from server.core.github_values import since_parameter as since_parameter
from server.tracker.domain.columns import COLUMNS_READ

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
SECRET_PURPOSE = "tracker-github-feed"


class FeedCapacityError(RuntimeError):
    """The bounded scan cannot complete its current timestamp boundary."""


def timestamp(value: Any, *, optional: bool = False) -> datetime:
    if not value and optional:
        return EPOCH
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("missing timezone")
        return parsed.astimezone(UTC)
    except (ValueError, OverflowError) as exc:
        raise FeedInputError("invalid upstream timestamp") from exc


def next_cursor(
    items: list[dict[str, Any]], *, complete: bool, previous: datetime | None
) -> datetime | None:
    if not items:
        return previous
    stamps = [timestamp(item.get("updated_at")) for item in items]
    # Provider ordering can carry high updated_at outliers. The beginning of
    # the final page is a conservative replay boundary; its maximum is not.
    boundary = max(stamps) if complete else min(stamps[-PAGE_SIZE:])
    if not complete and previous is not None and boundary <= previous:
        raise FeedCapacityError("timestamp boundary exceeds the page budget; increase max-pages")
    return max(previous or EPOCH, boundary)


def _default(family: str) -> Any:
    if family.startswith("Array("):
        return []
    if family == "String":
        return ""
    if family == "DateTime":
        return EPOCH
    if family == "UUID":
        return uuid.UUID(int=0)
    return 0


def _names(value: Any, key: str) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(row.get(key) or "")[:256] for row in value if isinstance(row, dict)][:100]


def item_row(repo: str, data: dict[str, Any], *, is_pr: bool) -> dict[str, Any]:
    repo = repository(repo)
    number = int(data.get("number") or 0)
    if number <= 0:
        raise FeedInputError("invalid upstream item number")
    kind = "github_pr" if is_pr else "github_issue"
    row = {name: _default(family) for name, family in COLUMNS_READ[kind + "s"].items()}
    source_id = f"{repo}#{number}"
    user = data.get("user") or {}
    row.update(
        id=uuid.uuid5(uuid.NAMESPACE_DNS, f"{kind}:{source_id}"),
        source_id=source_id,
        number=number,
        url=f"https://github.com/{repo}/{'pull' if is_pr else 'issues'}/{number}",
        title=str(data.get("title") or "")[:1024],
        body=str(data.get("body") or "")[:65536],
        author=str(user.get("login") or "")[:256],
        created_at=timestamp(data.get("created_at")),
        updated_at=timestamp(data.get("updated_at")),
        closed_at=timestamp(data.get("closed_at"), optional=True),
    )
    if is_pr:
        merged = timestamp(data.get("merged_at"), optional=True)
        row.update(
            pr_number=number,
            state="merged" if merged != EPOCH else str(data.get("state") or ""),
            is_merged=int(merged != EPOCH),
            merged_at=merged,
            reviewers=_names(data.get("requested_reviewers"), "login"),
            base_branch=str((data.get("base") or {}).get("ref") or "")[:256],
            additions=max(0, int(data.get("additions") or 0)),
            deletions=max(0, int(data.get("deletions") or 0)),
        )
    else:
        row.update(
            labels=_names(data.get("labels"), "name"),
            assignees=_names(data.get("assignees"), "login"),
            state_reason=str(data.get("state_reason") or "")[:64],
        )
    return row


def comment_row(repo: str, data: dict[str, Any], *, review: bool) -> dict[str, Any]:
    repo = repository(repo)
    url = str(data.get("pull_request_url" if review else "issue_url") or "")
    parsed = urlsplit(url)
    prefix = f"/repos/{repo}/{'pulls' if review else 'issues'}/"
    suffix = parsed.path.removeprefix(prefix)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "api.github.com"
        or not parsed.path.startswith(prefix)
        or not suffix.isdecimal()
        or int(suffix) <= 0
        or parsed.query
        or parsed.fragment
    ):
        raise FeedInputError("comment parent does not match the configured repository")
    ident = int(data.get("id") or 0)
    if ident <= 0:
        raise FeedInputError("invalid upstream comment id")
    return dict(
        comment_id=ident,
        item_number=int(suffix),
        review=int(review),
        body=str(data.get("body") or "")[:65536],
        author=str((data.get("user") or {}).get("login") or "")[:256],
        created_at=timestamp(data.get("created_at")),
        updated_at=timestamp(data.get("updated_at")),
    )
