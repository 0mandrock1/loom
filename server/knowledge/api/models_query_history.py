"""Opt-in query-history sources that queue definitions for an admin to review."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class QueryHistoryAddInput(BaseModel):
    org: str
    namespace: str
    # Registration name within this namespace.
    name: str = Field(min_length=1, max_length=100)
    # ClickHouse host; follows the tracker's host-vetting policy.
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=8443, ge=1, le=65535)
    secure: bool = True
    # Read-only ClickHouse account with SELECT on system.query_log.
    user: str = Field(min_length=1, max_length=255)
    # Sealed at rest and never returned.
    password: str = Field(max_length=4096)
    # Only queries whose databases all belong to this allowlist are considered.
    databases: list[Annotated[str, Field(min_length=1, max_length=255)]] = Field(
        min_length=1, max_length=100
    )
    # Optional configured ClickHouse cluster, read through clusterAllReplicas.
    cluster: str = Field(default="", max_length=255)


class QueryHistorySource(BaseModel):
    name: str
    source_id: str
    kind: Literal["clickhouse_queries"] = "clickhouse_queries"
    host: str
    port: int
    secure: bool
    user: str
    databases: list[str]
    cluster: str = ""
    created_at: datetime


class QueryHistoryListInput(BaseModel):
    org: str
    namespace: str
    limit: int = Field(default=50, ge=1, le=1000)


class QueryHistoryListOutput(BaseModel):
    # Whether the operator permits new registrations and query-history turns.
    enabled: bool = False
    sources: list[QueryHistorySource] = Field(default_factory=list)


class QueryHistoryRemoveInput(BaseModel):
    org: str
    namespace: str
    name: str


class QueryHistoryRemoveOutput(BaseModel):
    removed: bool
