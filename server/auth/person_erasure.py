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
class Workspace:
    """A real Directory observation tied to an already verified canonical person."""

    org: str
    subject: str
    customer: str
    user: str
    emails: tuple[str, ...]

    def __post_init__(self) -> None:
        Person(self.org, self.subject, self.emails)
        if any(
            not isinstance(value, str)
            or not value
            or len(value) > 512
            or any(not 33 <= ord(char) <= 126 for char in value)
            for value in (self.customer, self.user)
        ):
            raise unavailable()


@dataclass(frozen=True, repr=False)
class Capture:
    id: str
    people: tuple[Person, ...]
    workspaces: tuple[Workspace, ...] = ()
    prepared: bool = False


def _seal(captured: Capture) -> str:
    value = {"format": 2, **asdict(captured)}
    encoded = seal(PURPOSE, json.dumps(value, separators=(",", ":"), sort_keys=True))
    load(captured.id, encoded)
    return encoded


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
    return _seal(Capture(uid, tuple(people[key] for key in sorted(people))))


def load(uid: str, encoded: str) -> Capture:
    try:
        value = json.loads(unseal(PURPOSE, encoded, warn=False))
        if (
            not isinstance(value, dict)
            or set(value) != {"format", "id", "people", "workspaces", "prepared"}
            or type(value["format"]) is not int
            or value["format"] != 2
            or not uid
            or value["id"] != uid
            or not isinstance(value["people"], list)
            or not isinstance(value["workspaces"], list)
            or type(value["prepared"]) is not bool
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
        workspaces = _workspaces(value["workspaces"], people)
        if not value["prepared"] and workspaces:
            raise unavailable()
        return Capture(uid, people, workspaces, value["prepared"])
    except (ValueError, TypeError, KeyError, RecursionError):
        raise unavailable() from None


def _workspaces(values: Any, people: tuple[Person, ...]) -> tuple[Workspace, ...]:
    if not isinstance(values, list) or any(
        not isinstance(w, dict)
        or set(w) != {"org", "subject", "customer", "user", "emails"}
        or not isinstance(w["emails"], list)
        for w in values
    ):
        raise unavailable()
    result = tuple(
        Workspace(w["org"], w["subject"], w["customer"], w["user"], tuple(w["emails"]))
        for w in values
    )
    keys = [(w.org, w.subject, w.customer, w.user) for w in result]
    if keys != sorted(set(keys)) or any(
        not any(
            (p.org, p.subject) == (w.org, w.subject) and set(p.emails).intersection(w.emails)
            for p in people
        )
        for w in result
    ):
        raise unavailable()
    return result


@dataclass(frozen=True, repr=False)
class Pending:
    """Only this exact durable SCIM deletion can authorize its private cleanup."""

    captured: Capture
    encoded: str

    async def validate(self, org: str | None = None, *, before_delete: bool = False) -> None:
        if org is not None and org not in {p.org for p in self.captured.people}:
            raise unavailable()
        with own_write_reads():
            row = await get_client().query_one(
                "SELECT active, deleted, crawl_erasure_enc, "
                "crawl_erasure_required, crawl_erasure_done "
                "FROM loom.scim_users FINAL WHERE id = {id:String} LIMIT 1",
                params={"id": self.captured.id},
            )
        if (
            row is None
            or row["crawl_erasure_enc"] != self.encoded
            or not row["crawl_erasure_required"]
            or row["crawl_erasure_done"]
            or before_delete
            and (not row["active"] or row["deleted"])
        ):
            raise unavailable()
        if before_delete:
            for person in self.captured.people:
                if org is not None and person.org != org:
                    continue
                identity = await resolve_reader(person.org, person.subject)
                if (
                    identity is None
                    or identity.subject != person.subject
                    or identity.emails != frozenset(person.emails)
                ):
                    raise unavailable()


class Preparer(Protocol):
    async def __call__(self, pending: Pending) -> tuple[Workspace, ...]: ...


class Cleanup(Protocol):
    async def __call__(self, pending: Pending) -> None: ...


_cleanup: Cleanup | None = None
_preparer: Preparer | None = None


def bind(preparer: Preparer, cleanup: Cleanup) -> None:
    global _cleanup, _preparer
    _cleanup, _preparer = cleanup, preparer


async def prepare(uid: str, encoded: str) -> str:
    if _preparer is None:
        raise unavailable()
    captured = load(uid, encoded)
    pending = Pending(captured, encoded)
    await pending.validate(before_delete=True)
    if captured.prepared:
        return encoded
    workspaces = await _preparer(pending)
    await pending.validate(before_delete=True)
    return _seal(Capture(uid, captured.people, workspaces, True))


async def finish(uid: str, encoded: str) -> None:
    if _cleanup is None:
        raise unavailable()
    captured = load(uid, encoded)
    if not captured.prepared:
        raise unavailable()
    pending = Pending(captured, encoded)
    await pending.validate()
    await _cleanup(pending)
    await pending.validate()


async def require_settled(*, ch: Any = None) -> None:
    """Missing historical identity proof stops crawls until explicit verified backfill."""
    with own_write_reads():
        row = await (ch or get_client()).query_one(
            "SELECT 1 AS pending FROM loom.scim_users FINAL "
            "WHERE (deleted = 1 OR crawl_erasure_required = 1) AND NOT crawl_erasure_done LIMIT 1"
        )
    if row is not None:
        raise unavailable()
