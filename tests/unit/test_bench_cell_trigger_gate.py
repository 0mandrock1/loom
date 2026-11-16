"""The trigger gate deletes only the scope rows its own batch inserts wrote."""

from __future__ import annotations

import argparse
import sqlite3
from typing import Any

import pytest

from bench import cell_trigger_gate as gate


@pytest.mark.asyncio
async def test_batch_cleanup_preserves_preexisting_gate_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = sqlite3.connect(":memory:")
    db.execute("ATTACH DATABASE ':memory:' AS loom")
    db.execute("CREATE TABLE loom.scope_generations (org text, scope text)")
    db.create_function("starts_with", 2, lambda value, prefix: value.startswith(prefix))
    prefix = "gate-own-run-"
    sentinel = [("org", "gate-existing"), ("other", prefix + "existing")]
    db.executemany("INSERT INTO loom.scope_generations VALUES (?, ?)", sentinel)

    class _Conn:
        async def execute(self, sql: str, *args: Any) -> None:
            sql = sql.replace(
                "FROM generate_series(1, 1000) g",
                "FROM (WITH RECURSIVE seq(g) AS (VALUES(1) UNION ALL "
                "SELECT g + 1 FROM seq WHERE g < 1000) SELECT g FROM seq)",
            )
            db.execute(sql, {str(i): value for i, value in enumerate(args, 1)})

        async def close(self) -> None:
            pass

    async def connect(*_a: Any, **_kw: Any) -> Any:
        return _Conn()

    monkeypatch.setattr(gate.asyncpg, "connect", connect)
    monkeypatch.setattr(gate, "INSERTS", 1)
    args = argparse.Namespace(pg_dsn="unused", org="org", scope_prefix=prefix)
    try:
        await gate._batch(args, True)
        assert db.execute("SELECT count(*) FROM loom.scope_generations").fetchone()[0] == 1002
        await gate._clean(args)
        assert (
            db.execute("SELECT org, scope FROM loom.scope_generations ORDER BY org").fetchall()
            == sentinel
        )
    finally:
        db.close()
