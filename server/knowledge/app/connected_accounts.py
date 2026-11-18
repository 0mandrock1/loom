"""Canonical-owner connection lifecycle and source-bound credential authority.

Account changes precede namespace source leases. HTTP exchanges run outside both;
the connection identity and rotation reservation fence late completions.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import math
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, TypeVar, cast

from server.auth import aliases, authz, claims, revocation, scim
from server.auth.context import current_auth
from server.auth.jwt import AuthInfo
from server.auth.oauth_store import PENDING_TTL, ClickHouseStore, token_id
from server.auth.reader_identity import reader_subject, resolve_reader
from server.clients._checked_access import CheckedAccess
from server.clients.connected_oauth import Kind, OAuthClient, OAuthError, Tokens
from server.core import leases, shielded
from server.core.ch import get_client, own_write_reads
from server.core.errors import AuthInsufficientError, ValidationError
from server.core.secrets_at_rest import seal, unseal
from server.knowledge.domain.connected_accounts import (
    CREDENTIAL_PURPOSE,
    EMAIL_PURPOSE,
    KINDS,
    PKCE_PURPOSE,
    ConnectedAccount,
    accepts_callback,
    refreshing,
    rotated,
    source_matches,
    stopped,
)
from server.knowledge.domain.sources import MIRRORED_ACCESS, KnowledgeSource
from server.knowledge.store import connected_accounts as store
from server.knowledge.store import sources as source_store
from server.observability import audit
from server.operations.base import Role
from server.settings import settings

pending_store = ClickHouseStore()
_T = TypeVar("_T")
# KeeperMap's UInt32 epoch ceiling; a registration's fence never expires while it lives.
_FENCE_EXPIRY = 2**32 - 1


def unavailable() -> ValidationError:
    return ValidationError("the connected account is unavailable", code="CONNECTION_UNAVAILABLE")


def _next_connection(previous: str = "", *, inactive: bool = False) -> str:
    key = previous.partition("/")[0] if previous else secrets.token_hex(16)
    # The generation's first nibble distinguishes a durable stop from a live checkout.
    generation = ("0" if inactive else "1") + secrets.token_hex(16)[1:]
    return key + "/" + generation


def _fence_key(connection_id: str) -> str:
    parts = connection_id.split("/")
    if len(parts) != 2 or any(
        len(p) != 32 or any(c not in "0123456789abcdef" for c in p) for p in parts
    ):
        raise unavailable()
    return "connected-account-generation:" + parts[0]


async def _fence_current(account: ConnectedAccount, *, ch: Any = None) -> bool:
    return (
        await claims.get(ch or get_client(), _fence_key(account.connection_id))
        == account.connection_id
    )


async def _move_fence(previous: str | None, current: str, *, ch: Any = None) -> None:
    if previous is not None and _fence_key(previous) != _fence_key(current):
        raise unavailable()
    value = await claims.swap(
        ch or get_client(), _fence_key(current), previous, current, _FENCE_EXPIRY
    )
    if value != current:
        raise unavailable()


def client(kind: str) -> OAuthClient:
    if kind == "gdrive":
        ident, secret = settings.connect_gdrive_client_id, settings.connect_gdrive_client_secret
    elif kind == "notion":
        if not settings.connect_notion_read_content_only:
            raise unavailable()
        ident, secret = settings.connect_notion_client_id, settings.connect_notion_client_secret
    elif kind == "linear":
        ident, secret = settings.connect_linear_client_id, settings.connect_linear_client_secret
    else:
        raise unavailable()
    try:
        return OAuthClient(cast(Kind, kind), ident, secret, settings.public_base_url)
    except OAuthError:
        raise unavailable() from None


@asynccontextmanager
async def account_lease(
    org: str, subject: str, kind: str, *, ch: Any = None
) -> AsyncIterator[None]:
    key = hashlib.sha256(f"{org}\0{subject}\0{kind}".encode()).hexdigest()
    async with leases.cluster_lease(
        "connected-account:" + key,
        ttl_s=settings.knowledge_connected_lease_ttl_s,
        wait_s=settings.knowledge_connected_lease_wait_s,
        ch=ch,
    ):
        yield


async def _standing(org: str, subject: str, since: int, keys: tuple[str, ...] = ()) -> None:
    if not settings.auth_actually_required():
        raise unavailable()
    identity = await resolve_reader(org, subject)
    if identity is None or identity.subject != subject:
        raise unavailable()
    spellings = await aliases.verified_spellings(org, subject)
    if not await store.account_active(org, spellings):
        raise unavailable()
    for email in identity.emails:
        if await scim.deactivated(email):
            raise unavailable()
    for at in (await revocation.cutoffs([*spellings, *keys])).values():
        if revocation.kills(at, since):
            raise unavailable()


def _primary(auth: AuthInfo | None) -> AuthInfo:
    if (
        auth is None
        or not settings.auth_actually_required()
        or not auth.session_id
        or not auth.first_party
        or auth.is_service_account
        or auth.impersonated_by
        or auth.creator
        or auth.parents
        or auth.agent_principal
        or auth.read_user_id
        or auth.reads_as_creator
        or auth.issued_at is None
        or auth.org in ("", "*")
    ):
        raise AuthInsufficientError("a primary sign-in session is required")
    return auth


async def own_subject(auth: AuthInfo | None) -> tuple[str, str]:
    subject = reader_subject(auth)
    if auth is None or not subject or auth.org in ("", "*") or auth.issued_at is None:
        raise AuthInsufficientError("a personal signed-in identity is required")
    if auth.expires_at is not None and auth.expires_at <= time.time():
        raise unavailable()
    canonical = await aliases.canonical_subject(auth.org, subject)
    await _standing(
        auth.org,
        canonical,
        min((auth.issued_at, *auth.parent_iats)),
        (auth.subject, auth.token_id, auth.session_id, *auth.parents),
    )
    return auth.org, canonical


async def start(auth: AuthInfo | None, kind: str) -> tuple[str, str, str]:
    auth = _primary(auth)
    provider = client(kind)  # Missing configuration does no Keeper or provider work.
    seal(CREDENTIAL_PURPOSE, "sealable")
    org, subject = await own_subject(auth)
    state, binding = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48) if kind != "notion" else ""
    async with account_lease(org, subject, kind):
        current = await store.get(org, subject, kind)
        if current is None:
            current = ConnectedAccount(org, subject, kind, _next_connection())
            await _move_fence(None, current.connection_id)
            await store.put(current)
        if not await _fence_current(current):
            raise unavailable()
        expected = current.connection_id
        await pending_store.save_pending(
            "connect:" + token_id(state),
            {
                "kind": "connected_account",
                "provider": kind,
                "org": org,
                "subject": subject,
                "binding": token_id(binding),
                "session_id": auth.session_id,
                "token_id": auth.token_id,
                "issued_at": auth.issued_at,
                "expires_at": auth.expires_at,
                "expected_connection": expected,
                "client_id": provider.client_id,
                "redirect_uri": provider.redirect_uri,
                "verifier_enc": seal(PKCE_PURPOSE, verifier),
            },
            ttl=PENDING_TTL,
        )
    return provider.authorization_url(state, verifier=verifier or None), state, binding


async def _pending_standing(pending: dict[str, Any]) -> None:
    expires = pending.get("expires_at")
    if expires is not None and (not isinstance(expires, int) or expires <= time.time()):
        raise unavailable()
    await _standing(
        pending["org"],
        pending["subject"],
        pending["issued_at"],
        (pending["session_id"], pending["token_id"]),
    )


async def callback(kind: str, state: str, binding: str, code: str, *, denied: bool = False) -> None:
    deadline = time.monotonic() + settings.knowledge_connected_oauth_timeout_s
    async with asyncio.timeout_at(deadline):
        await _callback(kind, state, binding, code, denied=denied, deadline=deadline)


async def _callback(
    kind: str, state: str, binding: str, code: str, *, denied: bool, deadline: float
) -> None:
    provider = client(kind)
    if not state or len(state) > 256 or not binding:
        raise unavailable()
    key = "connect:" + token_id(state)
    pending = await pending_store.pending(key)
    if (
        pending is None
        or pending.get("kind") != "connected_account"
        or pending.get("provider") != kind
        or pending.get("client_id") != provider.client_id
        or pending.get("redirect_uri") != provider.redirect_uri
        or not secrets.compare_digest(str(pending.get("binding") or ""), token_id(binding))
    ):
        raise unavailable()
    pending = await pending_store.take_pending(key)
    if pending is None or denied or not code or len(code) > 8192:
        raise unavailable()
    await _pending_standing(pending)
    org, subject = pending["org"], pending["subject"]
    async with account_lease(org, subject, kind):
        current = await store.get(org, subject, kind)
        if (
            current is None
            or not accepts_callback(current, pending["expected_connection"])
            or not await _fence_current(current)
        ):
            raise unavailable()
    verifier = unseal(PKCE_PURPOSE, pending["verifier_enc"], warn=False)
    if kind != "notion" and not verifier:
        raise unavailable()
    grant = await provider.exchange(code, verifier=verifier or None, deadline=deadline)
    old: ConnectedAccount | None = None
    async with account_lease(org, subject, kind):
        await _pending_standing(pending)
        current = await store.get(org, subject, kind)
        if (
            current is None
            or not accepts_callback(current, pending["expected_connection"])
            or not await _fence_current(current)
        ):
            raise unavailable()
        # First fence the old registration, then all of its source ids, before replacing it.
        old = current
        ended = await _stop_locked(current, strict=True)
        account = ConnectedAccount(
            org,
            subject,
            kind,
            _next_connection(ended.connection_id),
            account_id=grant.account_id,
            workspace_id=grant.workspace_id or "",
            scopes=grant.tokens.scopes,
            account_email_enc=seal(EMAIL_PURPOSE, grant.display_email or ""),
            credential_enc=seal(CREDENTIAL_PURPOSE, grant.tokens.refresh_token),
            active=True,
            connected_at=datetime.now(UTC),
        )
        await store.put(account)
        await _move_fence(ended.connection_id, account.connection_id)
    # Google's revoke ends the user's project grant, including a replacement for that
    # same provider identity. Such a reconnect keeps its grant and drops the old secret.
    if old is not None and (old.account_id, old.workspace_id) != (
        account.account_id,
        account.workspace_id,
    ):
        await _revoke(old, deadline=deadline)
    await audit.emit(
        "connection.connected",
        org=org,
        namespace="-",
        actor=subject,
        subject={"kind": kind, "subject": subject, "scopes": list(account.scopes)},
    )


async def _stop_locked(account: ConnectedAccount, *, strict: bool = False) -> ConnectedAccount:
    from server.knowledge.app import source_push
    from server.knowledge.app import sources as source_ops

    ended = stopped(
        account,
        connection_id=_next_connection(account.connection_id, inactive=True),
        at=datetime.now(UTC),
    )
    key = _fence_key(account.connection_id)
    previous = await claims.get(get_client(), key)
    if previous != account.connection_id:
        if strict or not isinstance(previous, str):
            raise unavailable()
        if _fence_key(previous) != key or not previous.partition("/")[2].startswith("0"):
            raise unavailable()
        # Only a durable stop can repair its lost row write. A newer live checkout
        # belongs to another connection and must never be overwritten or revoked.
        ended = dataclasses.replace(ended, connection_id=previous)
    landed = await claims.swap(get_client(), key, previous, ended.connection_id, _FENCE_EXPIRY)
    if landed != ended.connection_id:
        raise unavailable()
    await store.put(ended)
    for source in await source_store.connected_sources(account.org, account.subject):
        if source.kind != account.kind:
            continue
        async with source_ops.sources_lease(source.org, source.namespace):
            current = await source_store.latest_source(source.org, source.namespace, source.name)
            if (
                current is None
                or current.source_id != source.source_id
                or current.credential_mode != "connected"
                or current.owner_subject != account.subject
                or current.kind != account.kind
            ):
                continue
            pending = await source_ops._queue_retirement(current)
            await source_push.retire(current)
            await source_store.insert_source(
                dataclasses.replace(
                    current,
                    active=False,
                    credential_enc="",
                    secret_enc="",
                    pending_delete=pending or current.pending_delete,
                )
            )
    return ended


async def _revoke(account: ConnectedAccount, *, deadline: float | None = None) -> None:
    credential = unseal(CREDENTIAL_PURPOSE, account.credential_enc, warn=False)
    if not credential:
        return
    try:
        provider = client(account.kind)
        limit = time.monotonic() + settings.knowledge_connected_revoke_timeout_s
        deadline = min(deadline, limit) if deadline is not None else limit
        async with asyncio.timeout_at(deadline):
            if account.kind == "notion":
                # The provider revokes an access token, not a refresh token. Mint only
                # after local inactivation; the rotated pair is never persisted.
                tokens = await provider.refresh(
                    credential, scopes=account.scopes, deadline=deadline
                )
                await provider.revoke(tokens.access_token, refresh=False, deadline=deadline)
            else:
                await provider.revoke(credential, refresh=True, deadline=deadline)
    except (OAuthError, TimeoutError, ValidationError):
        return  # Local inactivation and cleared credentials are already durable.


async def disconnect(org: str, subject: str, kind: str) -> None:
    if kind not in KINDS:
        raise unavailable()
    async with account_lease(org, subject, kind):
        account = await store.get(org, subject, kind)
        if account is None:
            account = ConnectedAccount(org, subject, kind, _next_connection())
            await _move_fence(None, account.connection_id)
        await _stop_locked(account)
    await _revoke(account)
    await audit.emit(
        "connection.disconnected",
        org=org,
        namespace="-",
        actor=subject,
        subject={"kind": kind, "subject": subject},
    )


async def stop_subjects(subjects: list[str], *, org: str | None = None) -> None:
    """Revocation callers pass every spelling they covered, including repeat/error tails."""
    ended = []
    for account in await store.owned(subjects, org=org):
        async with account_lease(account.org, account.subject, account.kind):
            current = await store.get(account.org, account.subject, account.kind)
            if current is not None:
                await _stop_locked(current)
                ended.append(current)
    # All local owners and sources are fenced before the first slow provider call.
    deadline = time.monotonic() + settings.knowledge_connected_revoke_timeout_s
    for account in ended:
        await _revoke(account, deadline=deadline)


async def _source_standing(source: KnowledgeSource, account: ConnectedAccount) -> None:
    from server.knowledge.app import sources as source_ops

    if (
        source.credential_mode != "connected"
        or source.access != MIRRORED_ACCESS
        or not settings.auth_actually_required()
        or not settings.knowledge_mirrored_sources_enabled
        or not source.owner_subject
        or not source.active
        or not source_matches(
            account,
            org=source.org,
            owner=source.owner_subject,
            kind=source.kind,
            connection_id=source.connection_id,
        )
    ):
        raise unavailable()
    if not await _fence_current(account):
        raise unavailable()
    since = int(account.connected_at.timestamp()) if account.connected_at else 0
    await _standing(source.org, source.owner_subject, since)
    # Invalidate the alias cache after an uncached verified tie read; the existing grant
    # contract then reads this person's current spellings rather than a departed alias.
    await aliases.verified_spellings(source.org, source.owner_subject)
    aliases.invalidate(source.org, [source.owner_subject])
    with own_write_reads():
        role = await authz.grant_role_for(source.org, source.namespace, source.owner_subject)
        current = await source_store.latest_source(source.org, source.namespace, source.name)
    if (
        role is None
        or role < Role.WRITER
        or current is None
        or not current.active
        or current.source_id != source.source_id
        or current.connection_id != source.connection_id
        or current.owner_subject != source.owner_subject
        or current.kind != source.kind
        or current.credential_mode != "connected"
        or current.access != MIRRORED_ACCESS
    ):
        raise unavailable()
    await source_ops._require_source_namespace(source.org, source.namespace)
    if not await _fence_current(account):
        raise unavailable()


async def token_for(source: KnowledgeSource, *, deadline: float) -> tuple[Tokens, ConnectedAccount]:
    if not math.isfinite(deadline) or deadline <= time.monotonic():
        raise unavailable()
    """A source-bound, serialized rotation. Access material remains in memory only."""
    provider = client(source.kind)
    async with account_lease(source.org, source.owner_subject, source.kind):
        account = await store.get(source.org, source.owner_subject, source.kind)
        if account is None or account.refresh_id:
            raise unavailable()
        await _source_standing(source, account)
        credential = unseal(CREDENTIAL_PURPOSE, account.credential_enc, warn=False)
        if not credential:
            raise unavailable()
        reservation = secrets.token_hex(16)
        await store.put(refreshing(account, refresh_id=reservation))
    try:
        tokens = await provider.refresh(credential, scopes=account.scopes, deadline=deadline)
        async with account_lease(account.org, account.subject, account.kind):
            current = await store.get(account.org, account.subject, account.kind)
            if current is None:
                raise unavailable()
            await _source_standing(source, current)
            try:
                updated = rotated(
                    current,
                    expected_connection=account.connection_id,
                    refresh_id=reservation,
                    credential_enc=seal(CREDENTIAL_PURPOSE, tokens.refresh_token),
                )
            except ValueError:
                raise unavailable() from None
            await store.put(updated)
        return tokens, account
    except BaseException:  # A lost rotating response cannot leave a reusable old secret.
        try:
            await shielded.shielded(
                _abort_refresh(account, reservation), name="connected refresh stop"
            )
        except Exception:  # noqa: BLE001 — keep the original failure; its reservation refuses reuse
            pass
        raise


async def _abort_refresh(account: ConnectedAccount, reservation: str) -> None:
    async with asyncio.timeout(settings.knowledge_connected_stop_timeout_s):
        async with account_lease(account.org, account.subject, account.kind):
            current = await store.get(account.org, account.subject, account.kind)
            if (
                current is not None
                and current.connection_id == account.connection_id
                and current.refresh_id == reservation
            ):
                await _stop_locked(current)


class SourceAuthority:
    """An internal credential checkout bound to the selected source and requester."""

    __slots__ = ("source", "requester", "_account", "_access", "_auth", "_deadline")

    def __init__(
        self,
        source: KnowledgeSource,
        requester: str,
        account: ConnectedAccount,
        tokens: Tokens,
        auth: AuthInfo,
        deadline: float,
    ) -> None:
        self.source, self.requester = source, requester
        self._account, self._auth = account, auth
        self._deadline = deadline
        self._access = CheckedAccess(
            tokens.access_token,
            kind=cast(Kind, source.kind),
            deadline=deadline,
            validate=self.validate,
        )

    def __repr__(self) -> str:
        return "SourceAuthority(<redacted>)"

    async def validate(self) -> None:
        if time.monotonic() >= self._deadline:
            raise unavailable()
        auth = current_auth()
        if auth is not self._auth:
            raise unavailable()
        org, subject = await own_subject(auth)
        if org != self.source.org or subject != self.requester:
            raise unavailable()
        if not await authz.row_in_scope(org, self.source.namespace, Role.VIEWER):
            raise unavailable()
        current = await store.get(self._account.org, self._account.subject, self._account.kind)
        if current is None or current.connection_id != self._account.connection_id:
            raise unavailable()
        await _source_standing(self.source, current)
        if current_auth() is not self._auth:
            raise unavailable()

    def __reduce_ex__(self, protocol: int) -> Any:
        raise TypeError("a connected authority cannot be serialized")

    async def run(self, call: Callable[[CheckedAccess], Awaitable[_T]]) -> _T:
        async with asyncio.timeout_at(self._deadline):
            await self.validate()
            result = await call(self._access)
            await self.validate()
            return result


async def resolve(org: str, namespace: str, name: str, *, deadline: float) -> SourceAuthority:
    if not math.isfinite(deadline) or deadline <= time.monotonic():
        raise unavailable()
    auth = current_auth()
    actual_org, subject = await own_subject(auth)
    if actual_org != org or not await authz.row_in_scope(org, namespace, Role.VIEWER):
        raise unavailable()
    source = await source_store.get_source(org, namespace, name)
    if source is None or source.owner_subject != subject:
        raise unavailable()
    tokens, account = await token_for(source, deadline=deadline)
    if auth is None:
        raise unavailable()
    return SourceAuthority(source, subject, account, tokens, auth, deadline)


async def available(account: ConnectedAccount) -> bool:
    """An owner-visible active projection must honor the same authoritative fence."""
    if not account.active or not account.credential_enc:
        return False
    try:
        client(account.kind)
        return bool(
            unseal(CREDENTIAL_PURPOSE, account.credential_enc, warn=False)
        ) and await _fence_current(account)
    except Exception:  # noqa: BLE001 — unknown registration standing is never reported usable
        return False


async def reseal_registry(
    column: str, purpose: str, *, dry_run: bool, ch: Any
) -> tuple[int, list[str]]:
    """Re-read under the same writer lease so resealing cannot revive a stopped secret."""
    from server.core.ch import set_query_tag
    from server.core.secrets_at_rest import resealed

    if (column, purpose) not in {
        ("credential_enc", CREDENTIAL_PURPOSE),
        ("account_email_enc", EMAIL_PURPOSE),
    }:
        raise ValueError("unknown connected account secret")
    set_query_tag("knowledge.connected_accounts.reseal")
    after, count, missing = ("", "", ""), 0, []
    while rows := await store.page(after, ch=ch):
        for account in rows:
            after = account.org, account.subject, account.kind
            async with account_lease(*after, ch=ch):
                current = await store.get(*after, ch=ch)
                if current is None:
                    continue
                if not await _fence_current(current, ch=ch):
                    continue
                raw = (
                    current.credential_enc
                    if column == "credential_enc"
                    else current.account_email_enc
                )
                if not raw:
                    continue
                if not unseal(purpose, raw, warn=False):
                    missing.append("/".join(after))
                elif (new := resealed(purpose, raw)) is not None:
                    count += 1
                    if not dry_run:
                        await store.put(dataclasses.replace(current, **{column: new}), ch=ch)
    return count, missing
