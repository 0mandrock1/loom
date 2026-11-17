"""Explicit checked mirror legs; ordinary stores do not widen their reads."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from server.auth.aliases import canonical_subject
from server.auth.authz import check_authorized
from server.auth.context import current_auth
from server.auth.reader_identity import ReaderIdentity, reader_subject, resolve_reader
from server.core import pg, request_work
from server.core.ch import sequential_reads, set_query_tag
from server.core.diagnostics import private_work
from server.core.naming import is_mirror_name
from server.knowledge.domain.access_list import AccessList, allows, from_json
from server.knowledge.domain.document_access import fresh, source_id
from server.knowledge.store import mirrors as mirrors_repo
from server.knowledge.store import source_access as store
from server.operations.base import Role
from server.settings import settings


def now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Admitted:
    namespace: str
    candidate: dict[str, Any]
    document: dict[str, Any]


@dataclass
class ReadPlan:
    org: str
    sources: dict[tuple[str, str], str] = field(default_factory=dict)
    reader: ReaderIdentity | None = None
    marked: bool = False
    unread: bool = False
    failed: bool = False

    async def check(
        self, candidates: list[dict[str, Any]], *, chunks: bool = False
    ) -> list[Admitted]:
        """Check a bounded batch; any candidate read failure invalidates the whole plan."""
        if self.failed or self.unread or not self.sources or not candidates:
            return []
        set_query_tag("knowledge.mirror.check")
        try:
            with sequential_reads(), private_work():
                return await self._check(candidates, chunks=chunks)
        except Exception:  # noqa: BLE001 — a failed candidate read must disclose no partial leg
            self.failed = True
            return []

    async def _check(self, candidates: list[dict[str, Any]], *, chunks: bool) -> list[Admitted]:
        pairs = sorted({(r["namespace"], r["source_uri"]) for r in candidates})
        heads = await store.heads(self.org, pairs) if pg.enabled() else {}
        rows = (
            [h.row for h in heads.values()]
            if pg.enabled()
            else await store.metadata(self.org, pairs)
        )
        docs = {
            (r["namespace"], r["source_uri"]): r
            for r in rows
            if r.get("org") == self.org and (r["namespace"], r["source_uri"]) in pairs
        }
        permissions = {p: from_json(r.get("access_json") or "") for p, r in docs.items()}
        groups = await self._groups(docs, permissions)
        out = []
        for candidate in candidates:
            pair = (candidate["namespace"], candidate["source_uri"])
            doc = docs.get(pair)
            if doc is None or doc.get("superseded_at") is not None:
                continue
            sid = source_id(doc["source_uri"], doc["tags"])
            original = self.sources.get((doc["namespace"], sid))
            if not original or (pg.enabled() and not _head_serves(heads[pair], candidate, chunks)):
                continue
            access = permissions[pair]
            owner = (
                await canonical_subject(self.org, access.owner)
                if access.owner and self.reader
                else ""
            )
            reader = self.reader
            if allows(
                access,
                subject=reader.subject if reader else "",
                canonical_owner=owner,
                emails=reader.emails if reader else frozenset(),
                domains=reader.domains if reader else frozenset(),
                groups=groups.get((doc["namespace"], sid), frozenset()),
            ):
                out.append(Admitted(original, candidate, doc))
        return out

    async def _groups(
        self,
        docs: dict[tuple[str, str], dict[str, Any]],
        permissions: dict[tuple[str, str], AccessList],
    ) -> dict[tuple[str, str], frozenset[str]]:
        if self.reader is None or not self.reader.emails:
            return {}
        wanted = [
            (ns, source_id(uri, docs[(ns, uri)]["tags"]), group)
            for (ns, uri), access in permissions.items()
            for group in access.groups
            if (ns, source_id(uri, docs[(ns, uri)]["tags"])) in self.sources
        ]
        rows = await store.group_members(self.org, wanted) if wanted else []
        found: dict[tuple[str, str], set[str]] = {}
        requested = set(wanted)
        for row in rows:
            key = (row["namespace"], row["source_id"], row["group"])
            if (
                key not in requested
                or row["deleted"]
                or not fresh(row["read_at"], now(), settings.knowledge_source_acl_stale_seconds)
            ):
                continue
            members = {email.strip().lower() for email in row["members"]}
            if self.reader.emails.intersection(members):
                found.setdefault(key[:2], set()).add(row["group"])
        return {key: frozenset(groups) for key, groups in found.items()}


def _head_serves(head: store.ReadHead, candidate: dict[str, Any], chunks: bool) -> bool:
    state = head.serving
    if state.deleted or head.row.get("revision") != state.revision:
        return False
    if not chunks:
        return True
    return (
        state.chunks_revision is not None
        and state.chunks_version is not None
        and state.chunks_revision == state.revision
        and int(candidate["revision"]) == state.revision
        and int(candidate["doc_version"]) == state.chunks_version
    )


async def prepare(org: str, namespaces: list[str]) -> ReadPlan:
    """Read public registrations first, then bind one marked, fail-closed mirror plan."""
    plan = ReadPlan(org)
    auth = current_auth()
    if (
        auth is None
        or not settings.auth_actually_required()
        or any(is_mirror_name(ns) for ns in namespaces)
    ):
        return plan
    set_query_tag("knowledge.mirror.prepare")
    try:
        with sequential_reads():
            for namespace in sorted(set(namespaces)):
                await check_authorized(auth, Role.VIEWER, org, namespace)
            sources = await store.active_sources(org, namespaces)
            if not sources:
                return plan
            if not request_work.mark_mirrored():
                plan.failed = True  # an unbound direct helper cannot serve a mirror
                return plan
            plan.marked = True
            with private_work():
                await _prepare(plan, sources, reader_subject(auth))
    except Exception:  # noqa: BLE001 — unreadable unconditional evidence must fail closed uniformly
        plan.marked = plan.unread = True
        request_work.mark_mirrored(unread=True)
    return plan


async def _prepare(plan: ReadPlan, sources: list[dict[str, Any]], subject: str) -> None:
    names = sorted({s["namespace"] for s in sources})
    mirrors = await mirrors_repo.mirrors_of(plan.org, names, live_only=True)
    by_original: dict[str, list[str]] = {}
    for mirror, original in mirrors.items():
        if is_mirror_name(mirror):
            by_original.setdefault(original, []).append(mirror)
    pairs = {
        (by_original[s["namespace"]][0], s["source_id"]): s["namespace"]
        for s in sources
        if len(by_original.get(s["namespace"], [])) == 1
    }
    rows = await store.listings(plan.org, list(pairs))
    plan.reader = await resolve_reader(plan.org, subject) if subject else None
    plan.sources = {
        (r["namespace"], r["source_id"]): pairs[(r["namespace"], r["source_id"])]
        for r in rows
        if (r["namespace"], r["source_id"]) in pairs
        and fresh(r["started_at"], now(), settings.knowledge_source_acl_stale_seconds)
    }
