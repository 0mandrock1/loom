"""`live_objects` is what a migration replay leaves.

`/v1/admin/schema.status` compares it with the service's tables. Counting
every CREATE any migration ever ran would make a view dropped by a later
migration read as permanently missing drift."""

from __future__ import annotations

from pathlib import Path

from server.core.schema.journal import live_objects


def test_a_drop_removes_what_an_earlier_file_created_in_numeric_order(tmp_path: Path) -> None:
    (tmp_path / "990_a.sql").write_text(
        "CREATE TABLE IF NOT EXISTS loom.t (x UInt8) ENGINE = Memory;\n"
        "CREATE MATERIALIZED VIEW IF NOT EXISTS loom.t_mv TO loom.t AS SELECT 1 AS x;\n"
    )
    # numeric order: 1000 runs after 990 even though it sorts first by name
    (tmp_path / "1000_b.sql").write_text(
        "-- DROP VIEW IF EXISTS loom.t;\nDROP VIEW IF EXISTS loom.t_mv;\n"
    )
    assert live_objects(tmp_path.glob("*.sql")) == {"t"}


def test_a_renamed_view_keeps_its_final_name_in_schema_status(tmp_path: Path) -> None:
    from server.core.schema.journal import object_ddl

    sql = (
        "CREATE MATERIALIZED VIEW IF NOT EXISTS loom.staged TO loom.target AS SELECT 1;\n"
        "RENAME TABLE `loom`.`staged` TO `loom`.`final_view`;\n"
        "RENAME TABLE loom_telemetry.old TO loom_telemetry.new;\n"
    )
    assert list(object_ddl(sql)) == [
        ("create", "staged"),
        ("drop", "staged"),
        ("create", "final_view"),
        ("drop", "loom_telemetry.old"),
        ("create", "loom_telemetry.new"),
    ]
    path = tmp_path / "990_stage.sql"
    path.write_text(sql)
    assert live_objects([path]) == {"final_view", "loom_telemetry.new"}
