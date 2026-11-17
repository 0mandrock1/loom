"""Every mirror leg shares source freshness, strict permissions and failure policy."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from server.auth.jwt import AuthInfo
from server.auth.reader_identity import ReaderIdentity
from server.core import request_work
from server.knowledge.app import source_access as app
from server.knowledge.domain.access_list import AccessList, to_json
from server.knowledge.domain.serving import Serving
from server.operations.base import Role

SID = "11111111-1111-4111-8111-111111111111"
MIRROR = "acl-" + "1" * 24
URI = "gdrive:file#" + SID
NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def world(monkeypatch):
    token = request_work._current.set(None)
    bag = request_work.begin()
    auth = AuthInfo(
        subject="lee",
        org="acme",
        namespaces=("docs",),
        roles=(Role.VIEWER,),
        session_id="sid",
        is_service_account=False,
    )
    doc = {
        "org": "acme",
        "namespace": MIRROR,
        "source_uri": URI,
        "tags": ["source-id:" + SID],
        "version": 10,
        "revision": 0,
        "title": "checked title",
        "superseded_at": None,
        "access_json": to_json(AccessList(emails=("lee@example.com",))),
    }
    mocks = {
        "sources": AsyncMock(return_value=[{"namespace": "docs", "source_id": SID}]),
        "mirrors": AsyncMock(return_value={MIRROR: "docs"}),
        "listings": AsyncMock(
            return_value=[{"namespace": MIRROR, "source_id": SID, "started_at": NOW}]
        ),
        "metadata": AsyncMock(side_effect=lambda *_a, **_k: [dict(doc)]),
        "groups": AsyncMock(return_value=[]),
        "heads": AsyncMock(return_value={}),
        "reader": AsyncMock(
            return_value=ReaderIdentity("lee", frozenset({"lee@example.com"}), frozenset())
        ),
        "owner": AsyncMock(return_value="lee"),
        "gate": AsyncMock(),
        "bag": bag,
        "doc": doc,
    }
    monkeypatch.setattr(app, "current_auth", lambda: auth)
    monkeypatch.setattr(app.settings, "auth_required", True)
    monkeypatch.setattr(app.settings, "knowledge_source_acl_stale_seconds", 3600)
    monkeypatch.setattr(app, "now", lambda: NOW)
    monkeypatch.setattr(app, "check_authorized", mocks["gate"])
    monkeypatch.setattr(app, "resolve_reader", mocks["reader"])
    monkeypatch.setattr(app, "canonical_subject", mocks["owner"])
    monkeypatch.setattr(app.pg, "enabled", lambda: False)
    monkeypatch.setattr(app.store, "active_sources", mocks["sources"])
    monkeypatch.setattr(app.mirrors_repo, "mirrors_of", mocks["mirrors"])
    monkeypatch.setattr(app.store, "listings", mocks["listings"])
    monkeypatch.setattr(app.store, "metadata", mocks["metadata"])
    monkeypatch.setattr(app.store, "group_members", mocks["groups"])
    monkeypatch.setattr(app.store, "heads", mocks["heads"])
    yield mocks
    request_work._current.reset(token)


def candidate(**changes):
    return {"namespace": MIRROR, "source_uri": URI, "doc_version": 10, "revision": 0, **changes}


async def test_active_source_marks_even_without_admitted_documents(world):
    plan = await app.prepare("acme", ["docs"])
    world["doc"]["access_json"] = to_json(AccessList(emails=("other@example.com",)))
    assert await plan.check([candidate()], chunks=True) == []
    assert plan.marked and not plan.unread and world["bag"].mirror_marked
    world["gate"].assert_awaited_once()


async def test_checked_document_retains_its_scope_and_metadata(world):
    plan = await app.prepare("acme", ["docs"])
    [admitted] = await plan.check([candidate()], chunks=True)
    assert admitted.namespace == "docs" and admitted.candidate == candidate()
    assert admitted.document == world["doc"]


@pytest.mark.parametrize("stage", ["sources", "mirrors", "listings", "reader"])
async def test_unconditional_read_failure_has_one_public_marker(world, stage):
    world[stage].side_effect = RuntimeError("private row or backend cause")
    plan = await app.prepare("acme", ["docs"])
    assert await plan.check([candidate()], chunks=True) == []
    assert plan.marked and plan.unread and world["bag"].mirror_unread


async def test_no_active_mirrored_source_runs_no_leg(world):
    world["sources"].return_value = []
    plan = await app.prepare("acme", ["docs"])
    assert not plan.marked and not world["bag"].mirror_marked
    world["mirrors"].assert_not_awaited()
    world["reader"].assert_not_awaited()


@pytest.mark.parametrize(
    "change", ["auth_off", "missing_binding", "stale_listing", "future_listing", "wrong_source"]
)
async def test_missing_scope_or_freshness_admits_nothing(world, monkeypatch, change):
    if change == "auth_off":
        monkeypatch.setattr(app.settings, "auth_required", False)
    elif change == "missing_binding":
        world["mirrors"].return_value = {}
    elif change in {"stale_listing", "future_listing"}:
        world["listings"].return_value[0]["started_at"] = NOW + timedelta(
            seconds=-3601 if change == "stale_listing" else 1
        )
    else:
        world["doc"]["tags"] = ["source-id:22222222-2222-4222-8222-222222222222"]
    plan = await app.prepare("acme", ["docs"])
    assert await plan.check([candidate()], chunks=True) == []
    assert not plan.unread


async def test_unbound_direct_helper_serves_no_mirror(world):
    request_work._current.set(None)
    plan = await app.prepare("acme", ["docs"])
    assert await plan.check([candidate()], chunks=True) == []
    world["metadata"].assert_not_awaited()


@pytest.mark.parametrize("stage", ["metadata", "heads", "groups", "owner"])
async def test_candidate_failure_discards_prior_results_without_read_marker(
    world, monkeypatch, stage
):
    plan = await app.prepare("acme", ["docs"])
    assert await plan.check([candidate()], chunks=True)
    if stage == "heads":
        monkeypatch.setattr(app.pg, "enabled", lambda: True)
    if stage == "groups":
        world["doc"]["access_json"] = to_json(AccessList(groups=("team@example.com",)))
    if stage == "owner":
        world["doc"]["access_json"] = to_json(AccessList(owner="alias@example.com"))
    world[stage].side_effect = RuntimeError("private candidate error")
    assert await plan.check([candidate()], chunks=True) == []
    assert plan.failed and not plan.unread and not world["bag"].mirror_unread
    assert await plan.check([candidate()], chunks=True) == []


@pytest.mark.parametrize(
    "state", ["missing", "deleted", "wrong_revision", "missing_chunk_state", "old_chunk_version"]
)
async def test_pg_requires_current_live_head_and_recorded_chunk_pair(world, monkeypatch, state):
    monkeypatch.setattr(app.pg, "enabled", lambda: True)
    head = app.store.ReadHead(dict(world["doc"]), Serving(False, 0, 0, 10))
    if state == "deleted":
        head = head._replace(serving=Serving(True, 0, 0, 10))
    elif state == "wrong_revision":
        head = head._replace(serving=Serving(False, 1, 1, 10))
    elif state == "missing_chunk_state":
        head = head._replace(serving=Serving(False, 0, None, None))
    elif state == "old_chunk_version":
        head = head._replace(serving=Serving(False, 0, 0, 11))
    world["heads"].return_value = {} if state == "missing" else {(MIRROR, URI): head}
    plan = await app.prepare("acme", ["docs"])
    assert await plan.check([candidate()], chunks=True) == []
    assert not plan.unread


async def test_pg_metadata_only_acl_uses_recorded_chunks_and_current_permission(world, monkeypatch):
    monkeypatch.setattr(app.pg, "enabled", lambda: True)
    world["doc"]["access_json"] = ""  # publication has not carried a widening yet
    head = {
        **world["doc"],
        "version": 11,
        "access_json": to_json(AccessList(emails=("lee@example.com",))),
    }
    world["heads"].return_value = {
        (MIRROR, URI): app.store.ReadHead(head, Serving(False, 0, 0, 10))
    }
    plan = await app.prepare("acme", ["docs"])
    assert len(await plan.check([candidate()], chunks=True)) == 1
    head["access_json"] = to_json(AccessList(emails=("other@example.com",)))
    assert await plan.check([candidate()], chunks=True) == []


async def test_group_membership_is_current_and_bound_to_owning_source(world):
    world["doc"]["access_json"] = to_json(AccessList(groups=("team@example.com",)))
    row = {
        "namespace": MIRROR,
        "source_id": SID,
        "group": "team@example.com",
        "members": ["lee@example.com"],
        "read_at": NOW,
        "deleted": False,
    }
    world["groups"].return_value = [row]
    plan = await app.prepare("acme", ["docs"])
    assert len(await plan.check([candidate()], chunks=True)) == 1
    for changed in (
        {"source_id": "other"},
        {"namespace": "acl-" + "2" * 24},
        {"read_at": NOW - timedelta(seconds=3601)},
        {"deleted": True},
    ):
        world["groups"].return_value = [{**row, **changed}]
        assert await plan.check([candidate()], chunks=True) == []


async def test_registry_read_is_sequential_before_listing_evidence(world, monkeypatch):
    from contextlib import contextmanager

    held = []

    @contextmanager
    def sequential():
        held.append(True)
        try:
            yield
        finally:
            held.pop()

    async def sources(*_args, **_kwargs):
        assert held
        return [{"namespace": "docs", "source_id": SID}]

    monkeypatch.setattr(app, "sequential_reads", sequential)
    world["sources"].side_effect = sources
    plan = await app.prepare("acme", ["docs"])
    assert not plan.unread and plan.sources == {(MIRROR, SID): "docs"}


@pytest.mark.parametrize("authority", ["clickhouse", "postgres"])
async def test_wrong_tenant_document_metadata_cannot_authorize(world, monkeypatch, authority):
    world["doc"]["org"] = "another-org"
    if authority == "postgres":
        monkeypatch.setattr(app.pg, "enabled", lambda: True)
        world["heads"].return_value = {
            (MIRROR, URI): app.store.ReadHead(dict(world["doc"]), Serving(False, 0, 0, 10))
        }
    plan = await app.prepare("acme", ["docs"])
    assert await plan.check([candidate()], chunks=True) == []


@pytest.mark.parametrize("chunks", [False, True])
async def test_pg_head_metadata_must_match_requested_pair_and_serving_revision(
    world, monkeypatch, chunks
):
    monkeypatch.setattr(app.pg, "enabled", lambda: True)
    for change in ({"namespace": "other"}, {"source_uri": "other"}, {"revision": 1}):
        world["heads"].return_value = {
            (MIRROR, URI): app.store.ReadHead({**world["doc"], **change}, Serving(False, 0, 0, 10))
        }
        plan = await app.prepare("acme", ["docs"])
        assert await plan.check([candidate()], chunks=chunks) == []
