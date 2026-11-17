"""The smoke fixture is deterministic and isolates recovery from paid providers."""

import io
import json
import threading
import urllib.error
import urllib.request

import pytest

import scripts.gates.docker_provider_data as data
import scripts.gates.docker_provider_server as fixture
import scripts.gates.docker_provider_smoke as smoke
from scripts.gates.docker_quickstart_smoke import provider_fixture


@pytest.fixture
def provider():
    server = fixture.ProviderServer(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def post(base, path, payload, key="docker-smoke-embedding-key"):
    request = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def test_provider_returns_exact_repeatable_model_dimensions(provider):
    for model, dimensions in fixture.DIMENSIONS.items():
        payload = {"model": model, "input": ["same text", "other text"]}
        result = post(provider, "/v1/embeddings", payload)
        assert result == post(provider, "/v1/embeddings", payload)
        vectors = [entry["embedding"] for entry in result["data"]]
        assert [entry["index"] for entry in result["data"]] == [0, 1]
        assert all(len(vector) == dimensions for vector in vectors)
        assert vectors[0] != vectors[1]


def test_injected_failure_recovers_without_changing_model_or_data(provider):
    payload = {"model": "docker-smoke-16", "input": ["context"]}
    expected = post(provider, "/v1/embeddings", payload)
    post(provider, "/control", {"failed_model": "docker-smoke-16"})
    assert post(
        provider,
        "/v1/embeddings",
        {"model": "docker-smoke-16", "input": ["Loom provider connection check."]},
    )["data"]
    with pytest.raises(urllib.error.HTTPError) as error:
        post(provider, "/v1/embeddings", payload)
    assert error.value.code == 400
    post(provider, "/control", {"failed_model": ""})
    assert post(provider, "/v1/embeddings", payload, key="docker-smoke-rotated-key") == expected


def test_llm_transport_fixture_still_checks_credentials(provider):
    result = post(provider, "/v1/chat/completions", {}, key="docker-smoke-llm-key")
    assert result["choices"][0]["message"]["content"] == "OK"
    with pytest.raises(urllib.error.HTTPError) as error:
        post(provider, "/v1/chat/completions", {}, key="wrong")
    assert error.value.code == 401


def test_fixture_rejects_unknown_credentials(provider):
    with pytest.raises(urllib.error.HTTPError) as error:
        post(provider, "/v1/embeddings", {"model": "docker-smoke-8", "input": ["text"]}, "wrong")
    assert error.value.code == 401


def test_fixture_is_not_exposed_outside_the_generated_network(tmp_path):
    configuration = json.loads(provider_fixture("loom:unit-smoke", tmp_path).read_text())
    services = configuration["services"]
    assert services["provider-smoke"]["image"] == "loom:unit-smoke"
    assert "ports" not in services["provider-smoke"]
    assert "environment" not in services["provider-smoke"]
    assert services["loom"]["depends_on"]["provider-smoke"]["condition"] == "service_healthy"


def test_key_rotation_omission_preserves_saved_credential_and_current_version(monkeypatch):
    calls = []

    def request(base, path, payload=None):
        calls.append((path, payload))
        return {"version": "18446744073709551615"} if payload is None else {"state": "active"}

    monkeypatch.setattr(smoke, "request", request)
    smoke.configure("http://localhost", "docker-smoke-8", 8)
    payload = calls[-1][1]
    assert payload["expected_version"] == "18446744073709551615"
    assert payload["keys"] == {}


def test_failed_status_is_an_error_unless_explicitly_waiting_for_failure(monkeypatch):
    failed = {"pending": {"state": "failed", "error_code": "rebuild_rebuild_failed"}}
    monkeypatch.setattr(smoke, "request", lambda *_: failed)
    with pytest.raises(AssertionError, match="Provider rebuild failed"):
        smoke.wait_status("http://localhost", lambda _: False)
    assert (
        smoke.wait_status("http://localhost", lambda row: row["pending"]["state"] == "failed")
        == failed
    )


@pytest.mark.asyncio
async def test_data_check_requires_no_retained_rebuild_copies(monkeypatch):
    from unittest.mock import AsyncMock

    client = AsyncMock()
    client.query_one.side_effect = [
        {
            "embedding_model": "model",
            "embedding_dim": 8,
            "memory_key_embedding_dim": 8,
            "width": 8,
            "verified": 1,
            "tags": ["provider-smoke"],
            "content_excerpt": "Provider rebuild preserves metadata",
        },
        {
            "embedding_model": "model",
            "embedding_dim": 8,
            "width": 8,
            "content": "def greet(): return 42",
            "start_line": 1,
        },
        {"n": 1},
    ]
    monkeypatch.setattr(data, "get_client", lambda: client)
    with pytest.raises(AssertionError):
        await data.verify("model", 8)


def test_crash_keeps_the_supervisor_and_verifies_retirement_before_reset(monkeypatch, tmp_path):
    override = tmp_path / "override.json"
    override.write_text(json.dumps({"services": {"loom": {}}}))
    compose = ["docker", "compose", "-f", str(override)]
    events = []
    environment = {
        "source": "environment",
        "embedding_model": "environment",
        "embedding_dimensions": 8,
        "llm_model": "fake",
    }
    active = {
        "source": "dashboard",
        "pending": None,
        "embedding_model": "docker-smoke-16",
        "embedding_dimensions": 16,
    }
    statuses = iter(
        [
            {
                **active,
                "embedding_model": "docker-smoke-8",
                "configured_keys": {"compatible": True},
            },
            {
                "embedding_model": "docker-smoke-8",
                "pending": {"state": "failed", "error_code": "rebuild_rebuild_failed"},
            },
            active,
            {**environment, "pending": None},
        ]
    )
    model_reads = iter([environment, active])

    def request(_base, path, _payload=None):
        if path == "/v1/admin/models":
            return next(model_reads)
        raise urllib.error.HTTPError("http://fixture", 503, "closed", {}, None)

    def configure(*_args):
        raise urllib.error.HTTPError(
            "http://fixture", 422, "width", {}, io.BytesIO(b"unsupported_dimensions")
        )

    applications = iter(["queued", "active", "queued"])

    def dashboard(_base, action, *_args):
        events.append(action)
        return {"state": next(applications) if action == "apply" else "queued"}

    def check(_compose, *args):
        events.append(args[0])
        return (
            [{"participant_id": "first"}, {"participant_id": "second"}]
            if args[0] == "workers"
            else None
        )

    def status(_base, predicate):
        value = next(statuses)
        assert predicate(value)
        return value

    monkeypatch.setattr(smoke, "request", request)
    monkeypatch.setattr(smoke, "configure", configure)
    monkeypatch.setattr(smoke, "dashboard_action", dashboard)
    monkeypatch.setattr(smoke, "data_check", check)
    monkeypatch.setattr(smoke, "wait_status", status)
    monkeypatch.setattr(smoke, "wait_admission", lambda *_: None)
    monkeypatch.setattr(smoke, "control", lambda *_: None)
    monkeypatch.setattr(smoke, "run", lambda *args: events.append(tuple(args)))
    smoke.smoke_providers("http://fixture", compose)
    assert not any(isinstance(event, tuple) and "SIGKILL" in event for event in events)
    assert events.index("retry") < events.index("crash-worker") < events.index("workers")
    recreate = [
        i
        for i, event in enumerate(events)
        if isinstance(event, tuple) and "--force-recreate" in event
    ]
    assert len(recreate) == 2
    assert events.index("workers") < recreate[-1] < events.index("retired") < events.index("reset")


def _process_fixture(monkeypatch):
    parent = {
        "pid": 1,
        "parent": 0,
        "start": "10",
        "namespace": 1,
        "executable": "/python",
        "command": ["python", *data.SUPERVISOR],
    }
    processes = {
        pid: {
            "pid": pid,
            "parent": 1,
            "start": str(pid * 10),
            "namespace": 1,
            "executable": "/python",
            "command": [
                "python",
                "-c",
                "from multiprocessing.spawn import spawn_main; spawn_main(tracker_fd=4, pipe_handle=8)",
                "--multiprocessing-fork",
            ],
        }
        for pid in (8, 9)
    }
    rows = [
        {
            "participant_id": str(pid),
            "version": 5,
            "state": "running",
            "host_proof": f"h|b|c|1|{pid}|{pid * 10}|||cell-retirement-v1",
        }
        for pid in (8, 9)
    ]
    monkeypatch.setattr(data.provider_process, "host_proof", lambda: "h|b|c|1|99|990||")
    monkeypatch.setattr(data, "_process", lambda pid: processes[pid])
    return parent, processes, rows


def test_worker_selection_requires_registered_supervised_processes(monkeypatch):
    parent, processes, rows = _process_fixture(monkeypatch)
    foreign = {**rows[0], "host_proof": "h|b|c|2|8|80|||cell-retirement-v1"}
    assert data._workers([*rows, foreign], parent) == [
        {k: row[k] for k in ("participant_id", "host_proof", "version")} for row in rows
    ]
    processes[8] = {**processes[8], "parent": 2}
    with pytest.raises(AssertionError):
        data._workers(rows, parent)
    processes[8] = {
        **processes[8],
        "parent": 1,
        "command": ["python", "-c", "from multiprocessing.resource_tracker import main; main(4)"],
    }
    with pytest.raises(AssertionError):
        data._workers(rows, parent)


@pytest.mark.parametrize("fault", ["none", "pid-reused", "supervisor-changed"])
def test_pidfd_crash_refuses_changed_identity_before_signalling(monkeypatch, fault):
    parent, processes, rows = _process_fixture(monkeypatch)
    events = []
    observed = iter(
        [processes[8], {**processes[8], "start": "81"} if fault == "pid-reused" else processes[8]]
    )
    monkeypatch.setattr(data, "_process", lambda _pid: next(observed))
    monkeypatch.setattr(
        data,
        "_supervisor",
        lambda: {**parent, "start": "11"} if fault == "supervisor-changed" else parent,
    )
    monkeypatch.setattr(data.os, "pidfd_open", lambda pid: events.append(("open", pid)) or 73)
    monkeypatch.setattr(data.os, "close", lambda fd: events.append(("close", fd)))
    monkeypatch.setattr(
        data.signal, "pidfd_send_signal", lambda fd, sig: events.append(("signal", fd, sig))
    )
    if fault == "none":
        data._kill_worker(rows[0], parent)
        assert events == [("open", 8), ("signal", 73, data.signal.SIGKILL), ("close", 73)]
    else:
        with pytest.raises(AssertionError):
            data._kill_worker(rows[0], parent)
        assert events == [("open", 8), ("close", 73)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", ["none", "drained", "old-version", "different-proof", "busy", "unknown"]
)
async def test_retirement_requires_exact_newer_certificate_and_known_zero_queries(
    monkeypatch, fault
):
    from unittest.mock import AsyncMock

    from server.insights.store import provider_participants

    _parent, _processes, rows = _process_fixture(monkeypatch)
    observed = {**rows[0], "state": "retired", "version": 6}
    if fault == "drained":
        observed["state"] = "drained"
    elif fault == "old-version":
        observed["version"] = 5
    elif fault == "different-proof":
        observed["host_proof"] = rows[1]["host_proof"]
    client = AsyncMock()
    client.query_all.return_value = [observed]
    count = 1 if fault == "busy" else None if fault == "unknown" else 0
    monkeypatch.setattr(provider_participants, "running_queries", AsyncMock(return_value=count))
    assert await data.retired(client, [rows[0]]) is (fault == "none")
    assert client.query_all.await_args.args[1] == {"ids": [rows[0]["participant_id"]]}
