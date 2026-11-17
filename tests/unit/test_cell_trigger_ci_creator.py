"""A failed fixture may remove only the containers its creator recorded."""

import signal
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.gates.cell_trigger_acceptance import Stores, native_cleanup_complete, write


class FakeStores(Stores):
    def __init__(self, folder: Path, rows: dict):
        super().__init__(folder, folder, "fixture", {"run_id": "123"})
        self.rows = deepcopy(rows)
        self.removed = []

    def inspect(self, identity, *, cleanup=False):
        return self.rows[identity]

    def command(self, *argv, timeout=120, cleanup=False):
        assert cleanup
        if argv[:3] == ("docker", "rm", "--force"):
            self.removed.append(argv[-1])
            return ""
        assert argv[:3] == ("docker", "ps", "-a")
        identity = next(part.removeprefix("id=") for part in argv if part.startswith("id="))
        return identity if identity in self.rows and identity not in self.removed else ""


def container(identity="own", created="birth", label="fixture"):
    return {
        "Id": identity,
        "Created": created,
        "Name": "/intent",
        "Config": {"Labels": {"loom.ci.trigger": label}},
    }


@pytest.mark.parametrize(
    "change",
    [
        {"Id": "reused"},
        {"Created": "new-birth"},
        {"Config": {"Labels": {"loom.ci.trigger": "foreign"}}},
    ],
)
def test_cleanup_refuses_changed_container_identity(tmp_path, change):
    actual = {**container(), **change}
    stores = FakeStores(tmp_path, {"own": actual})
    stores.items = [{"role": "pg-primary", "name": "intent", "id": "own", "created": "birth"}]
    assert stores.cleanup()
    assert stores.removed == []


def test_cleanup_continues_exact_owned_containers_after_a_refusal(tmp_path):
    stores = FakeStores(tmp_path, {"own": container(), "foreign": container(label="other")})
    stores.items = [
        {"role": "pg-primary", "name": "intent", "id": "own", "created": "birth"},
        {"role": "pg-standby", "name": "other", "id": "foreign", "created": "birth"},
    ]
    assert stores.cleanup() == ["RuntimeError:pg-standby"]
    assert stores.removed == ["own"]


def test_partial_create_requires_its_exact_name_and_random_label(tmp_path):
    stores = FakeStores(tmp_path, {"intent": container()})
    stores.items = [{"role": "clickhouse", "name": "intent", "id": None, "created": None}]
    assert stores.cleanup() == []
    assert stores.removed == ["own"]


def test_partial_create_never_adopts_a_foreign_matching_name(tmp_path):
    stores = FakeStores(tmp_path, {"intent": container(label="other")})
    stores.items = [{"role": "clickhouse", "name": "intent", "id": None, "created": None}]
    assert stores.cleanup() == ["RuntimeError:clickhouse"]
    assert stores.removed == []


def test_cancellation_is_deferred_until_owned_cleanup_finishes(tmp_path):
    stores = FakeStores(tmp_path, {})
    stores.interrupted(signal.SIGTERM, None)
    assert stores.cancelled == signal.SIGTERM
    stores.terminal = True
    with pytest.raises(SystemExit) as caught:
        stores.interrupted(signal.SIGINT, None)
    assert caught.value.code == 128 + signal.SIGTERM


def test_store_removal_requires_acknowledged_native_cleanup(tmp_path):
    (tmp_path / "fixture").mkdir()
    with pytest.raises(FileNotFoundError):
        native_cleanup_complete(tmp_path, "fixture")
    write(
        tmp_path / "fixture/outer-result.json",
        {"schema": 2, "fixture_id": "fixture", "native_cleanup_complete": False},
    )
    with pytest.raises(RuntimeError, match="Native retirement"):
        native_cleanup_complete(tmp_path, "fixture")


def test_failed_measurement_can_close_only_after_real_native_cleanup(tmp_path):
    (tmp_path / "fixture").mkdir()
    write(
        tmp_path / "fixture/outer-result.json",
        {
            "schema": 2,
            "fixture_id": "fixture",
            "candidate_verdict": "FAIL",
            "native_cleanup_complete": True,
        },
    )
    native_cleanup_complete(tmp_path, "fixture")


def test_dependency_metadata_accepts_only_the_actual_python_version(tmp_path):
    import hashlib
    import json
    import sys

    from scripts.gates.testsupport.cell_trigger import fixture

    environment = tmp_path / ".venv"
    for name in fixture.CODEC_FILES:
        path = environment / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture codec bytes")
    (environment / fixture.CODEC_FILES[1]).write_text('version = "1.9.0"\n')
    (environment / fixture.CODEC_FILES[4]).write_text('__version__ = "0.32.0"\n')
    spec = {
        "root": str(tmp_path),
        "python": str(environment / "bin/python"),
        "python_sha256": "0" * 64,
        "lockfile_sha256": "1" * 64,
    }

    def check(version):
        (environment / "pyvenv.cfg").write_text(
            f"implementation = CPython\nversion_info = {version}\n"
            "include-system-site-packages = false\n"
        )
        receipt = {
            "schema": 2,
            **spec,
            "versions": fixture.CODEC_VERSIONS,
            "codec_sources": {
                name: {"path": str(environment / name), "sha256": fixture.sha(environment / name)}
                for name in fixture.CODEC_FILES
            },
        }
        receipt.pop("dependency_receipt", None)
        path = tmp_path / "receipt.json"
        path.write_text(json.dumps(receipt))
        path.chmod(0o600)
        spec["dependency_receipt"] = {
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        fixture.dependencies(spec, verify=True)

    check(".".join(map(str, sys.version_info[:3])))
    check("3.12")
    for changed in ("3.13.0", "3.12.invalid", "3.12.999"):
        with pytest.raises(fixture.Refused, match="fixed isolated CPython"):
            check(changed)


@pytest.fixture
def protocol_reader(tmp_path, monkeypatch):
    import sys

    from scripts.gates.testsupport.cell_trigger import fixture

    monkeypatch.setitem(sys.modules, "fixture", fixture)
    from scripts.gates.testsupport.cell_trigger import capture

    data = (Path(fixture.__file__).parent / fixture.PROTOCOL).read_bytes()
    path = tmp_path / fixture.PROTOCOL
    path.write_bytes(data)
    path.chmod(0o644)
    monkeypatch.setattr(capture, "HERE", tmp_path)
    return capture, fixture, path, data


def test_committed_public_protocol_is_readable_without_private_receipt_mode(protocol_reader):
    capture, _, _, _ = protocol_reader
    assert capture.protocol()["schema"] == 1


def test_public_protocol_keeps_hash_and_symlink_guards(protocol_reader):
    capture, fixture, path, data = protocol_reader
    path.write_bytes(data + b"\n")
    with pytest.raises(fixture.Refused):
        capture.protocol()
    path.unlink()
    target = path.with_name("target.json")
    target.write_bytes(data)
    target.chmod(0o644)
    path.symlink_to(target)
    with pytest.raises((OSError, fixture.Refused)):
        capture.protocol()


def test_private_receipt_still_refuses_public_read_permissions(protocol_reader):
    _, fixture, path, _ = protocol_reader
    with pytest.raises(fixture.Refused, match="owned private stream"):
        fixture.read(path)


def test_replication_waits_for_primary_tcp_after_temporary_socket_ready(tmp_path, monkeypatch):
    from scripts.gates import cell_trigger_acceptance as creator

    monkeypatch.setattr(creator, "ports", lambda count: list(range(18000, 18000 + count)))
    monkeypatch.setattr(creator.time, "sleep", lambda _: None)

    class InitializingStores(Stores):
        def __init__(self):
            super().__init__(tmp_path, tmp_path, "fixture", {"run_id": "123"})
            self.tcp_attempts = 0
            self.tcp_ready = False

        def create(self, role, args):
            return {"id": role}

        def command(self, *argv, timeout=120, cleanup=False):
            if "pg_isready" in argv:
                assert argv[argv.index("-h") + 1] == "127.0.0.1"
                self.tcp_attempts += 1
                if self.tcp_attempts == 1:
                    raise RuntimeError("simulated TCP startup refusal")
                self.tcp_ready = True
                return "127.0.0.1:5432 - accepting connections"
            if "psql" in argv:
                query = argv[-1]
                if query == "SELECT 1":
                    return "1"  # The temporary init server accepts Unix sockets only.
                assert self.tcp_ready, "Replication configuration raced the temporary init server"
                return "sync" if "SELECT sync_state" in query else ""
            return "1" if "clickhouse-client" in argv else "network-id"

    stores = InitializingStores()
    stores.start("test-image")
    assert stores.tcp_attempts == 2 and stores.tcp_ready


@pytest.mark.parametrize("setting", ["config_mount", "database"])
def test_clickhouse_creation_uses_ci_server_configuration(tmp_path, monkeypatch, setting):
    from scripts.gates import cell_trigger_acceptance as creator

    monkeypatch.setattr(creator, "ports", lambda count: list(range(18000, 18000 + count)))

    class CapturedCreation(Exception):
        pass

    class CapturingStores(Stores):
        def command(self, *argv, timeout=120, cleanup=False):
            return "network-id"

        def create(self, role, args):
            assert role == "clickhouse"
            self.creation_args = args
            raise CapturedCreation

    stores = CapturingStores(tmp_path, tmp_path, "fixture", {"run_id": "123"})
    with pytest.raises(CapturedCreation):
        stores.start("test-image")
    args = stores.creation_args
    if setting == "config_mount":
        mounts = [args[index + 1] for index, value in enumerate(args) if value == "-v"]
        for filename, section in (
            ("custom_settings_prefixes.xml", "config.d"),
            ("log_queries_readonly.xml", "users.d"),
        ):
            assert (
                str(tmp_path / "dev/clickhouse-ci" / filename)
                + ":/etc/clickhouse-server/"
                + section
                + "/"
                + filename
                + ":ro"
            ) in mounts
    else:
        environment = [args[index + 1] for index, value in enumerate(args) if value == "-e"]
        assert "CLICKHOUSE_DB=loom" in environment


def test_failure_diagnostics_expose_only_allowlisted_owned_state(tmp_path):
    import json

    class DiagnosticStores(Stores):
        def command(self, *argv, timeout=120, cleanup=False):
            assert cleanup and timeout == 5
            if argv[:2] == ("docker", "inspect"):
                value = container()
                value["Config"]["Env"] = ["TOKEN=private-value"]
                value["State"] = {
                    "Status": "running",
                    "Running": True,
                    "ExitCode": 0,
                    "OOMKilled": False,
                    "Error": "private-log",
                }
                return json.dumps([value])
            return "primary:sync"

    stores = DiagnosticStores(tmp_path, tmp_path, "fixture", {"run_id": "123"})
    stores.phase = "standby_sync"
    stores.items = [{"role": "pg-primary", "name": "intent", "id": "own", "created": "birth"}]
    result = stores.failure_diagnostics()
    assert result == {
        "phase": "standby_sync",
        "containers": [
            {
                "role": "pg-primary",
                "available": True,
                "status": "running",
                "exit_code": 0,
                "oom_killed": False,
                "pg_state": {"recovery": False, "standby_sync": "sync"},
            }
        ],
    }
    assert "private" not in json.dumps(result)


def test_diagnostic_failure_preserves_primary_result_and_runs_cleanup(
    tmp_path, monkeypatch, capsys
):
    import json
    import sys

    from scripts.gates import cell_trigger_acceptance as creator

    cleaned = []

    class FailingStores(Stores):
        def start(self, image):
            raise ValueError("private primary failure")

        def failure_diagnostics(self):
            raise OSError("private diagnostic failure")

        def cleanup(self):
            cleaned.append(True)
            return []

    root = Path(sys.executable).parent.parent.parent
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("GITHUB_WORKSPACE", str(root))
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    for name in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_REPOSITORY"):
        monkeypatch.setenv(name, "123")
    monkeypatch.setattr(sys, "argv", ["creator", "--clickhouse-image", "test-image"])
    monkeypatch.setattr(creator, "Stores", FailingStores)
    monkeypatch.setattr(creator, "source", lambda _: {"head": "a" * 40, "tree": "b" * 40})
    monkeypatch.setattr(creator, "source_hashes", lambda _: {})
    monkeypatch.setattr(creator, "resources", lambda _: None)
    monkeypatch.setattr(creator.os, "umask", lambda _: 0)
    monkeypatch.setattr(creator.signal, "signal", lambda *_: None)
    assert creator.main() == 1
    result = json.loads(capsys.readouterr().out)
    assert cleaned == [True]
    assert result["error_type"] == "ValueError" and result["diagnostic_error_type"] == "OSError"
    assert "private" not in json.dumps(result)
    published = json.loads((tmp_path / "cell-trigger-artifacts/result.json").read_text())
    assert (
        published["error_type"] == "ValueError" and published["diagnostic_error_type"] == "OSError"
    )
