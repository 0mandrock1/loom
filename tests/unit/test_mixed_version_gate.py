"""The mixed-version gate skips cleanly where it cannot run."""

from __future__ import annotations

import pytest

from scripts.gates import mixed_version


def test_without_docker_the_gate_skips_with_exit_0(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(mixed_version.shutil, "which", lambda _name: None)
    assert mixed_version.main([]) == 0
    assert "skipped" in capsys.readouterr().out


def test_with_require_the_gate_fails_where_it_cannot_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(mixed_version.shutil, "which", lambda _name: None)
    assert mixed_version.main(["--require"]) == 1
    assert "cannot run" in capsys.readouterr().out


def test_without_npm_no_bundle_is_built(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "package.json").write_text("{}")
    monkeypatch.setattr(mixed_version.shutil, "which", lambda _name: None)
    assert mixed_version.build_frontend(tmp_path) is False


def test_the_next_tree_carries_one_migration_more(tmp_path) -> None:
    migrations = tmp_path / "schemas" / "migrations"
    migrations.mkdir(parents=True)
    (migrations / "029_instances.sql").write_text("SELECT 1;\n")
    (migrations / "030_shared_replica_state.sql").write_text("SELECT 1;\n")
    assert mixed_version.add_noop_migration(tmp_path) == "031_mixed_version_gate.sql"
    assert (migrations / "031_mixed_version_gate.sql").read_text().endswith("SELECT 1;\n")


@pytest.mark.parametrize("conflict", [False, True])
def test_an_isolated_container_name_is_removed_only_after_a_successful_start(
    monkeypatch: pytest.MonkeyPatch, conflict: bool
) -> None:
    import subprocess
    from types import SimpleNamespace

    calls = []
    monkeypatch.setenv("LOOM_CH_CONTAINER", "test-mixed-isolated")
    monkeypatch.setattr(mixed_version, "_free_port", lambda: 18123)
    monkeypatch.setattr(
        mixed_version.httpx, "get", lambda *a, **k: SimpleNamespace(status_code=200)
    )

    def run(cmd, **kwargs):
        calls.append(cmd)
        if conflict and cmd[:2] == ["docker", "run"]:
            raise subprocess.CalledProcessError(125, cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(mixed_version.subprocess, "run", run)
    if conflict:
        with pytest.raises(subprocess.CalledProcessError):
            with mixed_version.clickhouse(False):
                pytest.fail("a failed container start must not yield a store")
        assert len(calls) == 1
    else:
        with mixed_version.clickhouse(False) as port:
            assert port == 18123
        assert calls[-1] == ["docker", "rm", "-f", "test-mixed-isolated"]
    assert calls[0][calls[0].index("--name") + 1] == "test-mixed-isolated"


def test_latency_summary_keeps_refusals_and_small_sample_percentiles() -> None:
    from bench.scale_load import latency_observations

    samples = [
        {"elapsed_ms": 2.0, "error": ""},
        {"elapsed_ms": 9.0, "error": ""},
        {"elapsed_ms": 3.0, "error": "503:PROJECTION_BEHIND"},
    ]
    out = latency_observations(samples)
    assert out["attempted"] == 3 and out["n"] == 2
    assert out["p50_ms"] == 2 and out["p99_ms"] == 9
    assert out["errors"] == {"503:PROJECTION_BEHIND": 1}
    assert out["samples"] == samples


@pytest.mark.parametrize("ms", [float("nan"), float("inf"), -1])
def test_latency_summary_refuses_invalid_timings(ms) -> None:
    from bench.scale_load import latency_observations

    with pytest.raises(AssertionError):
        latency_observations([{"elapsed_ms": ms}])


def _latency_written():
    return {
        "memory_key": "k",
        "version": 2,
        "memory_id": "id",
        "status": "active",
        "noop": False,
        "conflict_verdict": "clear",
    }


@pytest.mark.parametrize(
    "delta",
    [
        {"noop": True},
        {"version": 0},
        {"duplicate_of": "other"},
        {"merged_into_key": "other"},
        {"conflict_verdict": ""},
    ],
)
def test_latency_fixture_never_treats_every_200_as_a_commit(delta) -> None:
    from bench.scale_load import committed_latency_rows

    with pytest.raises(AssertionError):
        committed_latency_rows(
            "memory.set", {**_latency_written(), **delta}, [{"memory_key": "k", "value": "v"}]
        )


def test_latency_bulk_checks_complete_outcomes_and_point_endstate() -> None:
    from bench.scale_load import assert_latency_current, committed_latency_rows

    result = {"written": [_latency_written()]}
    expected = committed_latency_rows(
        "memory.bulk_set", result, [{"memory_key": "k", "value": "v"}]
    )
    assert_latency_current({"found": True, "status": "active", **expected["k"]}, expected["k"])
    with pytest.raises(AssertionError):
        committed_latency_rows(
            "memory.bulk_set", {**result, "unchecked": 1}, [{"memory_key": "k", "value": "v"}]
        )
    with pytest.raises(AssertionError):
        assert_latency_current(
            {"found": True, "status": "active", **expected["k"], "value": "old"}, expected["k"]
        )


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [{"memory_key": "k", "version": 1, "status": "active"}],
        [{"memory_key": "k", "version": 2, "status": "rejected"}],
        [{"memory_key": "k", "version": 2, "status": "active"}] * 2,
    ],
)
def test_latency_first_search_cannot_poll_away_a_missing_stale_or_rejected_row(rows) -> None:
    from bench.scale_load import assert_latency_visible

    with pytest.raises(AssertionError):
        assert_latency_visible({"results": rows}, "k", {"version": 2})


def test_latency_first_search_and_real_worker_identity_positive_controls() -> None:
    from bench.scale_load import assert_latency_visible, assert_latency_worker

    assert_latency_visible(
        {"results": [{"memory_key": "k", "version": 2, "status": "active"}]}, "k", {"version": 2}
    )
    assert_latency_worker(102, 100, 100, 100)
    for args in ((100, 100, 100, 100), (102, 100, 999, 100), (102, 100, 100, 999)):
        with pytest.raises(AssertionError):
            assert_latency_worker(*args)


def test_latency_partial_start_registers_and_stops_both_owned_groups(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from types import SimpleNamespace

    owned = []
    stopped = []

    class RefusedStart:
        def __init__(self, name, *args, **kwargs):
            self.name = name

        def start(self):
            assert len(owned) == 2
            raise RuntimeError("start refused")

        def stop(self):
            stopped.append(self.name)

    monkeypatch.setattr(mixed_version, "Server", RefusedStart)
    scenario = mixed_version.Scenario.__new__(mixed_version.Scenario)
    scenario.builds = {label: SimpleNamespace(tree=tmp_path) for label in ("N-1", "N")}
    scenario.ch_port = 18123
    scenario.owned_logs = tmp_path
    scenario.owned_servers = owned
    with pytest.raises(RuntimeError, match="start refused"):
        scenario.latency_acceptance("base", "head")
    assert len(owned) == 2 and stopped == ["latency-base-0", "latency-base-1"]


def test_latency_transport_retains_lag_refusal_without_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from types import SimpleNamespace

    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(
            status_code=503,
            text="projection behind",
            json=lambda: {"detail": {"code": "PROJECTION_BEHIND"}},
        )

    monkeypatch.setattr(mixed_version.httpx, "post", post)
    monkeypatch.setattr(mixed_version.time, "perf_counter", lambda: 1.0)
    srv = mixed_version.Server("latency-test", tmp_path, 18123, tmp_path)
    observation = {}
    with pytest.raises(AssertionError, match="503"):
        srv.call("token", "memory.search", {}, observation=observation)
    assert len(calls) == 1
    assert calls[0][1]["headers"] == {"Authorization": "Bearer token"}
    assert calls[0][1]["timeout"] == 60
    assert observation == {"elapsed_ms": 0.0, "status": 503, "error": "503:PROJECTION_BEHIND"}
