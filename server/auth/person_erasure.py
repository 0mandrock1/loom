"""Verified canonical-person deletion metadata survives contact and sign-in removal.

This is cleanup input, never a request identity, credential or permission to read as
the person. The required downstream port runs outside SCIM's identity leases.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from server.auth.reader_identity import resolve_reader
from server.core.ch import get_client, own_write_reads, set_query_tag
from server.core.errors import BackendUnavailableError
from server.core.secrets_at_rest import seal, unseal

PURPOSE = "auth.crawl_person_erasure"


def unavailable() -> BackendUnavailableError:
    return BackendUnavailableError(
        "canonical-person cleanup is unavailable", operation="crawl_erasure"
    )


@dataclass(frozen=True, repr=False)
class Person:
    org: str
    subject: str
    emails: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            any(not isinstance(v, str) or not v or "\0" in v for v in (self.org, self.subject))
            or not self.emails
            or self.emails != tuple(sorted(set(self.emails)))
            or any(
                not isinstance(v, str)
                or v != v.lower()
                or v.count("@") != 1
                or not all(v.split("@"))
                or any(ord(c) < 33 or ord(c) > 126 for c in v)
                for v in self.emails
            )
        ):
            raise unavailable()


@dataclass(frozen=True, repr=False)
class Capture:
    id: str
    people: tuple[Person, ...]


async def capture(uid: str, email: str, ties: dict[str, set[str]]) -> str:
    """Only verified directory rows can join a contact to a canonical subject and org."""
    set_query_tag("auth.person_erasure.capture")
    if not uid or not email or not isinstance(ties, dict):
        raise unavailable()
    people: dict[tuple[str, str], Person] = {}
    for subject, orgs in sorted(ties.items()):
        for org in sorted(orgs):
            identity = await resolve_reader(org, subject)
            if identity is None or email.lower() not in identity.emails:
                raise unavailable()
            person = Person(org, identity.subject, tuple(sorted(identity.emails)))
            key = org, identity.subject
            if key in people and people[key] != person:
                raise unavailable()
            people[key] = person
    value = {"format": 1, "id": uid, "people": [asdict(people[key]) for key in sorted(people)]}
    encoded = seal(PURPOSE, json.dumps(value, separators=(",", ":"), sort_keys=True))
    load(uid, encoded)
    return encoded


def load(uid: str, encoded: str) -> Capture:
    try:
        value = json.loads(unseal(PURPOSE, encoded, warn=False))
        if (
            not isinstance(value, dict)
            or set(value) != {"format", "id", "people"}
            or type(value["format"]) is not int
            or value["format"] != 1
            or not uid
            or value["id"] != uid
            or not isinstance(value["people"], list)
            or any(
                not isinstance(p, dict)
                or set(p) != {"org", "subject", "emails"}
                or not isinstance(p["emails"], list)
                for p in value["people"]
            )
        ):
            raise unavailable()
        people = tuple(Person(p["org"], p["subject"], tuple(p["emails"])) for p in value["people"])
        keys = [(p.org, p.subject) for p in people]
        if keys != sorted(set(keys)):
            raise unavailable()
        return Capture(uid, people)
    except (ValueError, TypeError, KeyError, RecursionError):
        raise unavailable() from None


class Cleanup(Protocol):
    async def __call__(self, captured: Capture) -> None: ...


_cleanup: Cleanup | None = None


def bind(cleanup: Cleanup) -> None:
    global _cleanup
    _cleanup = cleanup


async def finish(uid: str, encoded: str) -> None:
    if _cleanup is None:
        raise unavailable()
    await _cleanup(load(uid, encoded))


async def require_settled(*, ch: Any = None) -> None:
    """Missing historical identity proof stops crawls until explicit verified backfill."""
    with own_write_reads():
        row = await (ch or get_client()).query_one(
            "SELECT 1 AS pending FROM loom.scim_users FINAL "
            "WHERE (deleted = 1 OR crawl_erasure_required = 1) AND NOT crawl_erasure_done LIMIT 1"
        )
    if row is not None:
        raise unavailable()
