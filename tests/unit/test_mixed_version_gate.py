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
    from datetime import timedelta
    from types import SimpleNamespace

    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(
            status_code=503,
            text="projection behind",
            json=lambda: {"detail": {"code": "PROJECTION_BEHIND"}},
            elapsed=timedelta(0),
            headers={"X-Request-Id": "fixture-refusal"},
        )

    class Client:
        def __init__(self, *, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def post(self, url, *, extensions, **kwargs):
            return post(url, timeout=self.timeout, **kwargs)

    monkeypatch.setattr(mixed_version.httpx, "Client", Client)
    monkeypatch.setattr(mixed_version, "_free_port", lambda: 18080)
    monkeypatch.setattr(mixed_version.time, "time_ns", lambda: 0)
    monkeypatch.setattr(mixed_version.time, "perf_counter", lambda: 1.0)
    srv = mixed_version.Server("latency-test", tmp_path, 18123, tmp_path)
    observation = {}
    with pytest.raises(AssertionError, match="503"):
        srv.call("token", "memory.search", {}, observation=observation)
    assert len(calls) == 1
    assert calls[0][1]["headers"] == {"Authorization": "Bearer token"}
    assert calls[0][1]["timeout"] == 60
    original = {key: observation[key] for key in ("elapsed_ms", "status", "error")}
    assert original == {"elapsed_ms": 0.0, "status": 503, "error": "503:PROJECTION_BEHIND"}
    assert {key: value for key, value in observation.items() if key not in original} == {
        "started_unix_ns": 0,
        "started_monotonic_s": 1.0,
        "connection_events_ms": {},
        "client_setup_ms": 0.0,
        "finished_unix_ns": 0,
        "httpx_elapsed_ms": 0.0,
        "request_id": "fixture-refusal",
    }


@pytest.fixture
def latency_transport(monkeypatch, tmp_path):
    from datetime import timedelta
    from types import SimpleNamespace

    from tests.unit.clock_fake import ManualClock

    clock = ManualClock(t=10.0)
    body = {"results": [{"memory_key": "k", "value": "complete private body"}]}
    response = SimpleNamespace(
        status_code=200,
        text="complete private body",
        json=lambda: body,
        elapsed=timedelta(milliseconds=15),
        headers={
            "X-Request-Id": "from-response",
            "Server-Timing": 'app;dur=6, ch;dur=9;desc="concurrent statements"',
            "Authorization": "private header",
        },
    )
    state = SimpleNamespace(
        clock=clock, body=body, response=response, failure=None, clients=[], calls=[], closed=[]
    )

    class Client:
        def __init__(self, **options):
            state.clients.append((self, options))
            clock.advance(0.002)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            clock.advance(0.011)
            state.closed.append(self)

        def post(self, url, **kwargs):
            state.calls.append((url, kwargs))
            trace = kwargs["extensions"]["trace"]
            info = {"private": "private trace credentials/body/SQL/address"}
            trace("private.unrecognized", info)
            clock.advance(0.003)
            trace("connection.connect_tcp.started", info)
            clock.advance(0.005)
            trace("connection.connect_tcp.complete", info)
            trace("http11.send_request_body.complete", info)
            clock.advance(0.007)
            if state.failure is not None:
                raise state.failure
            trace("http11.receive_response_headers.complete", info)
            return state.response

    monkeypatch.setattr(mixed_version.httpx, "Client", Client)
    monkeypatch.setattr(mixed_version.time, "perf_counter", clock.monotonic)
    monkeypatch.setattr(mixed_version.time, "time_ns", lambda: round(clock.t * 1e9))
    monkeypatch.setattr(mixed_version, "_free_port", lambda: 18080)
    state.server = mixed_version.Server("latency-test", tmp_path, 18123, tmp_path)
    return state


def test_observed_calls_keep_cold_clients_complete_results_and_closed_timing(latency_transport):
    import json

    state = latency_transport
    request = {"query": "complete request"}
    samples = [{}, {}]
    for sample in samples:
        assert (
            state.server.call("fixture-token", "memory.search", request, observation=sample)
            == state.body
        )
        assert sample["elapsed_ms"] == pytest.approx(28)
        assert sample["client_setup_ms"] == pytest.approx(2)
        assert sample["httpx_elapsed_ms"] == 15
        assert sample["request_id"] == "from-response"
        assert (sample["status"], sample["error"]) == (200, "")
        assert (sample["server_app_ms"], sample["server_ch_ms"]) == (6, 9)
        assert sample["finished_unix_ns"] - sample["started_unix_ns"] == 28_000_000
        assert sample["connection_events_ms"] == pytest.approx(
            {
                "connection.connect_tcp.started": 5,
                "connection.connect_tcp.complete": 10,
                "http11.send_request_body.complete": 10,
                "http11.receive_response_headers.complete": 17,
            }
        )
        assert set(sample) == {
            "elapsed_ms",
            "client_setup_ms",
            "httpx_elapsed_ms",
            "request_id",
            "status",
            "error",
            "server_app_ms",
            "server_ch_ms",
            "started_unix_ns",
            "started_monotonic_s",
            "finished_unix_ns",
            "connection_events_ms",
        }
        assert "private" not in json.dumps(sample)
    assert len(state.clients) == len(state.closed) == len(state.calls) == 2
    assert state.clients[0][0] is not state.clients[1][0]
    assert state.closed == [client for client, _options in state.clients]
    assert [options for _client, options in state.clients] == [{"timeout": 60}] * 2
    for url, kwargs in state.calls:
        assert url == state.server.url + "/v1/memory.search"
        assert kwargs["json"] is request
        assert kwargs["headers"] == {"Authorization": "Bearer fixture-token"}
        assert set(kwargs) == {"json", "headers", "extensions"}


def test_observed_transport_exception_keeps_identity_and_includes_client_close(latency_transport):
    state = latency_transport
    state.failure = mixed_version.httpx.ReadError("fixture transport error")
    sample = {}
    with pytest.raises(mixed_version.httpx.ReadError) as caught:
        state.server.call("fixture-token", "memory.search", {}, observation=sample)
    assert caught.value is state.failure
    assert sample["elapsed_ms"] == pytest.approx(28)
    assert sample["error"] == "ReadError"
    assert len(state.calls) == len(state.clients) == len(state.closed) == 1
    assert state.closed[0] is state.clients[0][0]


@pytest.mark.parametrize("refused", [False, True], ids=["complete", "same-exception"])
def test_unobserved_call_keeps_the_original_post_path(monkeypatch, latency_transport, refused):
    state = latency_transport
    failure = mixed_version.httpx.ReadError("original transport error")
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        if refused:
            raise failure
        return state.response

    monkeypatch.setattr(mixed_version.httpx, "post", post)
    request = {"memory_key": "k"}
    if refused:
        with pytest.raises(mixed_version.httpx.ReadError) as caught:
            state.server.call("fixture-token", "memory.get", request)
        assert caught.value is failure
    else:
        assert state.server.call("fixture-token", "memory.get", request) == state.body
    assert state.clients == state.closed == []
    assert calls == [
        (
            state.server.url + "/v1/memory.get",
            {
                "json": request,
                "headers": {"Authorization": "Bearer fixture-token"},
                "timeout": 60,
            },
        )
    ]


@pytest.mark.parametrize("broken", [False, True], ids=["invalid-values", "unreadable-headers"])
def test_malformed_timing_metadata_cannot_replace_the_complete_response(latency_transport, broken):
    state = latency_transport
    state.response.headers = (
        None
        if broken
        else {
            "X-Request-Id": "invalid request id",
            "Server-Timing": "app;dur=invalid, ch;dur=-1, private;dur=10",
        }
    )
    sample = {}
    assert state.server.call("fixture-token", "memory.search", {}, observation=sample) == state.body
    assert (sample["status"], sample["error"]) == (200, "")
    assert sample["elapsed_ms"] == pytest.approx(28)
    assert not {"request_id", "server_app_ms", "server_ch_ms"} & sample.keys()
    assert sample.get("timing_error") == ("AttributeError" if broken else None)
    assert len(state.closed) == len(state.calls) == 1
