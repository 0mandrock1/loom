"""Crawl checkouts retain exact registration, provider identity and refusal generations."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TypeVar

from server.auth import source_registration
from server.clients import gdrive
from server.clients import google_workspace as google
from server.clients._checked_access import CheckedAccess
from server.clients._vendor import CredentialRefused
from server.core.ch import get_client, own_write_reads
from server.core.secrets_at_rest import unseal
from server.knowledge.app import crawl_exclusions, crawl_expansion
from server.knowledge.app import sources as source_ops
from server.knowledge.domain.source_crawl import Expansion, Options, Person
from server.knowledge.domain.sources import MIRRORED_ACCESS, KnowledgeSource
from server.knowledge.store import sources as store
from server.settings import settings

_now = time.monotonic
_T = TypeVar("_T")


@dataclass(frozen=True)
class Bounds:
    directory: crawl_expansion.Limits
    lease_ttl_s: float
    lease_wait_s: float

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.lease_ttl_s)
            or self.lease_ttl_s <= 0
            or not math.isfinite(self.lease_wait_s)
            or self.lease_wait_s < 0
        ):
            raise ValueError("crawl lease bounds refused")


def scope(source: KnowledgeSource) -> source_registration.Scope:
    payload = json.dumps(
        {
            "kind": source.kind,
            "access": source.access,
            "items": source.items,
            "config": source.config,
            "options": source.crawl_options,
            "customer_id": source.crawl_customer_id,
            "credential_hash": source.crawl_credential_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return source_registration.Scope(
        source.org,
        source.namespace,
        source.source_id,
        source.crawl_generation,
        hashlib.sha256(payload.encode()).hexdigest(),
    )


def _shape(source: KnowledgeSource) -> None:
    if (
        source.credential_mode != "crawl"
        or source.kind != "gdrive"
        or source.access != MIRRORED_ACCESS
        or not source.active
        or source.pending_delete
        or source.owner_subject
        or source.connection_id
        or source.credential_enc
        or not source.secret_enc
        or not source.registrant_subject
        or not source.approval_enc
        or not source.crawl_customer_id
        or not source.crawl_generation
        or len(source.crawl_credential_hash) != 64
        or any(c not in "0123456789abcdef" for c in source.crawl_credential_hash)
        or not settings.knowledge_mirrored_sources_enabled
        or not settings.auth_actually_required()
    ):
        raise CredentialRefused("crawl source unavailable")
    Options.parse(source.crawl_options)


async def resolve(source: KnowledgeSource, *, deadline: float, bounds: Bounds) -> Authority:
    """A worker-only checkout; ordinary live credential selection never calls this path."""
    if type(deadline) not in (float, int) or not math.isfinite(deadline) or deadline <= _now():
        raise TimeoutError("crawl deadline elapsed")
    async with asyncio.timeout_at(deadline):
        _shape(source)
        approval = source_registration.load(
            source.approval_enc, scope(source), registrant=source.registrant_subject
        )
        await approval.validate()
        index = await crawl_exclusions.Index.open(source.org, ch=get_client())
        authority = Authority(source, approval, index, deadline, bounds)
        await authority.validate()
        # Credentials are touched only after the source, registrant and refusal proof.
        plain = unseal(source_ops._SECRET_PURPOSE, source.secret_enc, warn=False)
        if not plain or not secrets.compare_digest(
            hashlib.sha256(plain.encode()).hexdigest(), source.crawl_credential_hash
        ):
            raise CredentialRefused("crawl source unavailable")
        authority._account = gdrive.parse_service_account(plain)
        await authority.validate()
        return authority


class Authority:
    __slots__ = (
        "source",
        "approval",
        "exclusions",
        "deadline",
        "bounds",
        "_account",
        "_expansion",
        "_preparing",
        "_directory_token",
        "_drive_tokens",
    )

    def __init__(
        self,
        source: KnowledgeSource,
        approval: source_registration.Approval,
        exclusions: crawl_exclusions.Index,
        deadline: float,
        bounds: Bounds,
    ) -> None:
        self.source, self.approval, self.exclusions = source, approval, exclusions
        self.deadline, self.bounds = deadline, bounds
        self._account: gdrive.ServiceAccount | None = None
        self._expansion: Expansion | None = None
        self._preparing = asyncio.Lock()
        self._directory_token: google.DelegatedToken | None = None
        self._drive_tokens: dict[str, google.DelegatedToken] = {}

    def __repr__(self) -> str:
        return "Authority(<redacted>)"

    async def validate(self) -> None:
        if _now() >= self.deadline:
            raise TimeoutError("crawl deadline elapsed")
        async with asyncio.timeout_at(self.deadline):
            _shape(self.source)
            await self.approval.validate()
            await self.exclusions.version()
            with own_write_reads():
                current = await store.latest_source(
                    self.source.org, self.source.namespace, self.source.name
                )
            if (
                current is None
                or not current.active
                or any(
                    getattr(current, field) != getattr(self.source, field)
                    for field in (
                        "org",
                        "namespace",
                        "name",
                        "source_id",
                        "kind",
                        "credential_mode",
                        "access",
                        "items",
                        "config",
                        "crawl_options",
                        "registrant_subject",
                        "approval_enc",
                        "crawl_generation",
                        "crawl_customer_id",
                        "crawl_credential_hash",
                        "secret_enc",
                        "owner_subject",
                        "connection_id",
                        "credential_enc",
                    )
                )
            ):
                raise CredentialRefused("crawl registration changed")
            await source_ops._require_source_namespace(self.source.org, self.source.namespace)
            await source_ops._require_no_erasure(self.source.org, self.source.namespace)
            await self.exclusions.version()

    def _service_account(self) -> gdrive.ServiceAccount:
        if self._account is None:
            raise CredentialRefused("crawl source unavailable")
        return self._account

    async def prepare(self) -> Expansion:
        async with asyncio.timeout_at(self.deadline):
            async with self._preparing:
                await self.validate()
                if self._expansion is None:
                    result = await crawl_expansion.expand(
                        Options.parse(self.source.crawl_options),
                        self._service_account(),
                        standing=self,
                        exclusions=self.exclusions,
                        limits=self.bounds.directory,
                        deadline=self.deadline,
                    )
                    if result.customer != self.source.crawl_customer_id:
                        raise CredentialRefused("crawl workspace changed")
                    await self.validate()
                    self._expansion = result
                return self._expansion

    async def _person(self, person: Person) -> None:
        await self.validate()
        if self._expansion is None or person not in self._expansion.people:
            raise CredentialRefused("crawl person unavailable")
        if await self.exclusions.contains(person):
            raise CredentialRefused("crawl person unavailable")
        if self._directory_token is None or self._directory_token.deadline <= _now():
            self._directory_token = await google.delegated_token(
                self._service_account(),
                subject=Options.parse(self.source.crawl_options).directory_subject,
                scope=gdrive.ReadScope.DIRECTORY_USERS,
                deadline=self.deadline,
                max_bytes=self.bounds.directory.body_bytes,
            )
            await self.validate()
        current = await google.user(
            self._directory_token,
            person.id,
            customer_id=person.customer,
            max_bytes=self.bounds.directory.body_bytes,
        )
        await self.validate()
        if (
            current.id != person.id
            or current.email != person.email
            or frozenset(current.aliases) != frozenset(person.aliases)
        ):
            raise CredentialRefused("crawl person changed")

    async def drive(self, person: Person) -> CheckedAccess:
        """The proven provider person selects sub; no caller, audit or Loom subject does."""
        async with asyncio.timeout_at(self.deadline):
            await self._person(person)
            token = self._drive_tokens.get(person.id)
            if token is None or token.deadline <= _now():
                token = await google.delegated_token(
                    self._service_account(),
                    subject=person.email,
                    scope=gdrive.ReadScope.DRIVE,
                    deadline=self.deadline,
                    max_bytes=self.bounds.directory.body_bytes,
                )
                await self._person(person)
                self._drive_tokens[person.id] = token

            async def validate() -> None:
                await self._person(person)

            return CheckedAccess(
                token._value, kind="gdrive", deadline=token.deadline, validate=validate
            )

    async def run(self, person: Person, callback: Callable[[CheckedAccess], Awaitable[_T]]) -> _T:
        async with asyncio.timeout_at(self.deadline):
            access = await self.drive(person)
            result = await callback(access)
            await access.check()
            return result

    @asynccontextmanager
    async def publication_guard(self) -> AsyncIterator[None]:
        """No content, ACL, membership or freshness write outlives this exact authority."""
        await self.validate()
        async with crawl_exclusions.org_lease(
            self.source.org,
            ttl_s=self.bounds.lease_ttl_s,
            wait_s=self.bounds.lease_wait_s,
        ):
            async with source_ops.sources_lease(self.source.org, self.source.namespace):
                await self.validate()
                yield
                await self.validate()
