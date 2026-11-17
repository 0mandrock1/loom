"""Content reads at exact admitted document identities, with no authority fallback."""

from __future__ import annotations

from server.core import pg
from server.core.ch import get_client
from server.knowledge.store.documents import DOCUMENT_COLUMNS, DocumentRow, _hydrate
from server.knowledge.store.pg_documents import decode_row


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
