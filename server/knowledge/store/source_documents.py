"""Content reads at exact admitted document identities, with no authority fallback."""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from server.core import pg
from server.core.ch import get_client
from server.knowledge.domain.live_chunks import live_chunk_sql
from server.knowledge.domain.serving import Serving
from server.knowledge.store.documents import DOCUMENT_COLUMNS, DocumentRow, _hydrate
from server.knowledge.store.pg_documents import decode_row
from server.knowledge.store.source_candidates import candidates

_LIST_COLUMNS = (
    "org",
    "namespace",
    "source_uri",
    "version",
    "revision",
    "title",
    "description",
    "tags",
    "ingested_at",
    "superseded_at",
    "blob_path",
    "blob_size",
    "blob_content_type",
    "original_filename",
)


async def list_chunk_count(
    org: str, namespace: str, source_uri: str, revision: int, serving: Serving | None
) -> int:
    """Count logical chunks of one admitted copy, including embedding stubs."""
    params: dict[str, Any] = {"org": org, "ns": namespace, "uri": source_uri, "revision": revision}
    version_filter = ""
    if pg.enabled():
        if serving is None or serving.deleted or serving.revision != revision:
            raise ValueError("missing current chunk count proof")
        if serving.chunks_revision != revision or serving.chunks_version is None:
            return 0
        params["version"] = serving.chunks_version
        version_filter = " AND doc_version = {version:UInt64}"
    row = await get_client().query_one(
        "SELECT uniqExact(embedding_model, chunk_idx) AS chunk_count "
        "FROM loom.chunks WHERE org = {org:String} AND namespace = {ns:String} "
        "AND source_uri = {uri:String} "
        "AND JSONExtractUInt(chunk_metadata, 'revision') = {revision:UInt64} "
        "AND " + live_chunk_sql() + version_filter + " LIMIT 1",
        params=params,
    )
    if row is None:
        raise ValueError("missing chunk count result")
    return int(row["chunk_count"])


async def list_projection(
    org: str, namespace: str, source_uri: str, version: int, revision: int
) -> dict[str, Any] | None:
    """Read list fields only after admission, at the admitted live version."""
    if not pg.enabled():
        columns = ", ".join(
            "toString(blob_content_type) AS blob_content_type"
            if col == "blob_content_type"
            else col
            for col in (*_LIST_COLUMNS, "content_preview")
        )
        return await get_client().query_one(
            f"SELECT {columns} FROM loom.documents FINAL "
            "WHERE org = {org:String} AND namespace = {ns:String} AND source_uri = {uri:String} "
            "AND version = {version:UInt64} AND revision = {revision:UInt64} "
            "AND superseded_at IS NULL LIMIT 1",
            params={
                "org": org,
                "ns": namespace,
                "uri": source_uri,
                "version": version,
                "revision": revision,
            },
        )
    fields = ", ".join(f"'{col}', h.row -> '{col}'" for col in _LIST_COLUMNS)
    # Return only the materialized preview's byte bound, including encoded text.
    saved = await pg.fetchrow(
        f"SELECT jsonb_build_object({fields}) AS row, "
        "encode(substring(CASE WHEN jsonb_typeof(h.row -> 'content') = 'string' "
        "THEN convert_to(h.row ->> 'content', 'UTF8') "
        "WHEN jsonb_typeof(h.row -> 'content') = 'object' "
        "THEN decode(h.row -> 'content' ->> '$loom_base64', 'base64') "
        "ELSE ''::bytea END from 1 for 220), 'base64') AS preview "
        "FROM loom.knowledge_heads h "
        "WHERE h.org = $1 AND h.namespace = $2 AND h.source_uri = $3 "
        "AND NOT h.deleted AND h.row ->> 'version' = $4 AND h.revision = $5 "
        "AND h.row ->> 'revision' = $6 AND h.row ->> 'superseded_at' IS NULL LIMIT 1",
        org,
        namespace,
        source_uri,
        str(version),
        revision,
        str(revision),
    )
    if saved is None:
        return None
    row = decode_row(saved["row"])
    row["content_preview"] = base64.b64decode(saved["preview"] or "").decode("utf-8", "replace")
    return row


@asynccontextmanager
async def chunk_candidates(
    org: str, namespace: str, source_uri: str, model: str
) -> AsyncIterator[AsyncIterator[dict[str, Any]]]:
    """Stream one admitted document's live chunk identities, including embedding stubs."""
    sql = (
        "SELECT namespace, source_uri, chunk_idx, doc_version, "
        "JSONExtractUInt(chunk_metadata, 'revision') AS revision "
        "FROM loom.chunks WHERE org = {org:String} AND namespace = {ns:String} "
        "AND source_uri = {uri:String} AND embedding_model = {model:String} "
        "AND " + live_chunk_sql() + " ORDER BY chunk_idx, doc_version DESC FORMAT JSONEachRow"
    )
    async with candidates(
        sql, {"org": org, "ns": namespace, "uri": source_uri, "model": model}
    ) as rows:
        yield rows


async def contents(
    org: str, namespace: str, source_uri: str, version: int, revision: int
) -> DocumentRow | None:
    if pg.enabled():
        saved = await pg.fetchrow(
            "SELECT h.row FROM loom.knowledge_heads h "
            "WHERE h.org = $1 AND h.namespace = $2 AND h.source_uri = $3 "
            "AND NOT h.deleted AND h.row ->> 'version' = $4 AND h.revision = $5 "
            "AND h.row ->> 'revision' = $6 LIMIT 1",
            org,
            namespace,
            source_uri,
            str(version),
            revision,
            str(revision),
        )
        row = decode_row(saved["row"]) if saved else None
    else:
        columns = ", ".join(
            f"toString({col}) AS {col}"
            if col in {"blob_content_type", "source_class", "format"}
            else col
            for col in DOCUMENT_COLUMNS
        )
        row = await get_client().query_one(
            f"SELECT {columns} FROM loom.documents FINAL "
            "WHERE org = {org:String} AND namespace = {ns:String} AND source_uri = {uri:String} "
            "AND version = {version:UInt64} AND revision = {revision:UInt64} "
            "AND superseded_at IS NULL LIMIT 1",
            params={
                "org": org,
                "ns": namespace,
                "uri": source_uri,
                "version": version,
                "revision": revision,
            },
        )
    return _hydrate(row) if row is not None and row.get("superseded_at") is None else None
