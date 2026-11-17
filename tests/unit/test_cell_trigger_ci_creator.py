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
