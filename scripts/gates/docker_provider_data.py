"""Seed/check nonempty rebuild surfaces inside the disposable Docker gate."""

import asyncio
import json
import os
import re
import signal
import sys
import time
from multiprocessing import spawn
from pathlib import Path

from server.core import provider_process
from server.core.ch import get_client
from server.insights.app.provider_command import model_command
from server.settings import settings

SUPERVISOR = [
    "-m",
    "uvicorn",
    "docker_provider_app:app",
    "--host",
    "0.0.0.0",
    "--port",
    "7777",
    "--workers",
    "2",
    "--timeout-graceful-shutdown",
    "10",
]


def _process(pid: int) -> dict:
    root = Path(f"/proc/{pid}")
    stat = (root / "stat").read_text().rsplit(")", 1)[1].split()
    return {
        "pid": pid,
        "parent": int(stat[1]),
        "start": stat[19],
        "command": (root / "cmdline").read_bytes().decode().rstrip("\0").split("\0"),
        "namespace": os.stat(root / "ns/pid").st_ino,
        "executable": os.readlink(root / "exe"),
    }


def _supervisor() -> dict:
    parent, me = _process(1), _process(os.getpid())
    assert parent["command"][1:] == SUPERVISOR, "Unexpected fixture supervisor"
    assert parent["executable"] == me["executable"], "Fixture supervisor executable mismatch"
    assert parent["namespace"] == me["namespace"], "Fixture supervisor PID namespace mismatch"
    return parent


def _worker_process(raw: str, parent: dict) -> dict:
    proof = raw.split("|")
    assert len(proof) == 9 and provider_process.has_certificate(raw), "Worker certificate invalid"
    assert proof[:4] == provider_process.host_proof().split("|")[:4], "Worker proof scope mismatch"
    process = _process(int(proof[4]))
    pid = process["pid"]
    assert pid > 1, "Worker identity names the supervisor"
    assert process["parent"] == 1, f"Worker PID {pid} parent mismatch: {process['parent']} != 1"
    assert process["start"] == proof[5], f"Worker PID {pid} process-start proof mismatch"
    assert process["namespace"] == parent["namespace"], f"Worker PID {pid} namespace mismatch"
    assert process["executable"] == parent["executable"], f"Worker PID {pid} executable mismatch"
    _spawn_command(process["command"], pid)
    return process


def _spawn_command(command: list[str], pid: int) -> None:
    # The fixed supervisor and exec helper use the same executable and container
    # startup flags. Let stdlib reproduce those flags, including inherited -B.
    expected = spawn.get_command_line(tracker_fd=0, pipe_handle=0)
    assert len(command) == len(expected) and command[1:-2] == expected[1:-2], (
        f"Worker PID {pid} interpreter flags or -c mismatch"
    )
    assert command[-1] == "--multiprocessing-fork", f"Worker PID {pid} fork argument mismatch"
    assert re.fullmatch(
        r"from multiprocessing\.spawn import spawn_main; "
        r"spawn_main\(tracker_fd=[0-9]+, pipe_handle=[0-9]+\)",
        command[-2],
    ), f"Worker PID {pid} spawn command mismatch"


def _kernel_workers() -> dict[int, dict]:
    """The supervisor's actual spawn children, before consulting participant history."""
    found = {}
    children = Path("/proc/1/task/1/children").read_text().split()
    for value in children:
        pid = int(value)
        process = _process(pid)
        command = process["command"]
        if not any(
            value.startswith("from multiprocessing.spawn import spawn_main;") for value in command
        ):
            continue  # The multiprocessing resource tracker is not an application worker.
        _spawn_command(command, pid)
        assert process["parent"] == 1, f"Kernel child PID {pid} parent changed during census"
        assert pid > 1 and pid not in found, "Invalid supervisor child PID census"
        found[pid] = process
    assert len(found) == 2, f"Expected two live supervisor spawn children; found {len(found)}"
    return found


def _workers(rows: list[dict], parent: dict) -> list[dict]:
    here = provider_process.host_proof().split("|")
    children = _kernel_workers()
    workers = []
    for pid, process in children.items():
        matches = []
        for row in rows:
            proof = str(row["host_proof"]).split("|")
            if (
                len(proof) >= 6
                and row["state"] != "retired"
                and proof[:4] == here[:4]
                and proof[4] == str(pid)
                and proof[5] == process["start"]
            ):
                matches.append(row)
        assert len(matches) == 1, (
            f"Worker PID {pid} exact registrations: {len(matches)}; expected 1"
        )
        row = matches[0]
        assert _worker_process(row["host_proof"], parent) == process, (
            f"Worker PID {pid} changed during proof join"
        )
        workers.append({key: row[key] for key in ("participant_id", "host_proof", "version")})
    assert len({row["participant_id"] for row in workers}) == 2, "Worker participant IDs collide"
    assert _kernel_workers() == children, "Supervisor worker identities changed during census"
    return sorted(workers, key=lambda row: row["host_proof"])


async def _worker_rows(client) -> list[dict]:
    return await client.query_all(
        "SELECT toString(participant_id) AS participant_id, state, version, host_proof "
        "FROM loom._provider_participants FINAL WHERE state != 'retired'"
    )


async def workers(client) -> list[dict]:
    parent = _supervisor()
    result = _workers(await _worker_rows(client), parent)
    assert len(result) == 2, "Worker registration census is incomplete"
    assert _supervisor() == parent, "Fixture supervisor changed during registration census"
    return result


def _kill_worker(worker: dict, parent: dict) -> None:
    proof = worker["host_proof"].split("|")
    pid = int(proof[4])
    assert pid > 1, "Refusing to signal the supervisor"
    captured = _worker_process(worker["host_proof"], parent)
    fd = os.pidfd_open(pid)
    try:
        # A reused PID or changed supervisor refuses before the process-bound signal.
        assert _process(pid) == captured, f"Worker PID {pid} changed after pidfd open"
        assert _supervisor() == parent, "Fixture supervisor changed before worker signal"
        signal.pidfd_send_signal(fd, signal.SIGKILL)
    finally:
        os.close(fd)


async def retired(client, captured: list[dict]) -> bool:
    from server.insights.store.provider_participants import running_queries

    rows = await client.query_all(
        "SELECT toString(participant_id) AS participant_id, state, version, host_proof "
        "FROM loom._provider_participants FINAL WHERE participant_id IN {ids:Array(UUID)}",
        {"ids": [row["participant_id"] for row in captured]},
    )
    if len(rows) != len(captured):
        return False
    expected = {row["participant_id"]: row for row in captured}
    for row in rows:
        old = expected[row["participant_id"]]
        if (
            row["state"] != "retired"
            or int(row["version"]) <= int(old["version"])
            or row["host_proof"] != old["host_proof"]
            or not provider_process.has_certificate(row["host_proof"])
            or await running_queries(row["participant_id"], ch=client) != 0
        ):
            return False
    return True


async def crash_worker(client) -> None:
    parent = _supervisor()
    before = await workers(client)
    victim = before[0]
    _kill_worker(victim, parent)
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        assert _supervisor() == parent
        if provider_process.proves_stopped(victim["host_proof"]) and await retired(
            client, [victim]
        ):
            after = _workers(await _worker_rows(client), parent)
            old = {row["participant_id"] for row in before}
            new = {row["participant_id"] for row in after}
            if len(new) == 2 and len(old & new) == 1 and victim["participant_id"] not in new:
                return
        await asyncio.sleep(1)
    raise AssertionError("Crashed worker retirement or supervised replacement was not proved")


async def seed() -> None:
    from server import boot
    from server.core.background import register_buffer_drainer

    register_buffer_drainer("boot-writes", boot.drain_writes)
    # The served width: a coded vector column takes vectors of that length only.
    width = settings.embedding_dim
    vector = [1.0] + [0.0] * (width - 1)
    async with model_command():
        client = get_client()
        await client.execute(
            "INSERT INTO loom.memory_embeddings "
            "(org, namespace, agent, user_id, session_id, memory_key, memory_type, scope, "
            "content_excerpt, embedding_model, embedding_dim, embedding, memory_key_embedding, "
            "memory_key_embedding_dim, verified, tags) SETTINGS async_insert = 0 VALUES "
            "('dev', 'local', 'docker-smoke', '-', '-', 'docker/vector-smoke', 'semantic', "
            "'private', 'Provider rebuild preserves metadata', {model:String}, {dim:UInt16}, "
            "{vec:Array(BFloat16)}, {vec:Array(BFloat16)}, {dim:UInt16}, 1, ['provider-smoke'])",
            {"model": settings.embedding_model, "dim": width, "vec": vector},
        )
        await client.execute(
            "INSERT INTO loom.code_chunks "
            "(org, namespace, embedding_model, path, chunk_id, content, lexemes, embedding, "
            "embedding_dim, start_line, end_line) SETTINGS async_insert = 0 VALUES "
            "('dev', 'code-provider-smoke', {model:String}, 'example.py', "
            "'ace68a73-808a-4b37-9ae0-2ab1a03b1c53', 'def greet(): return 42', "
            "'greet return', {vec:Array(BFloat16)}, {dim:UInt16}, 1, 1)",
            {"model": settings.embedding_model, "dim": width, "vec": vector},
        )


async def verify(model: str, dimensions: int) -> None:
    client = get_client()
    memory = await client.query_one(
        "SELECT embedding_model, embedding_dim, memory_key_embedding_dim, "
        "length(embedding) AS width, verified, tags, content_excerpt "
        "FROM loom.memory_embeddings FINAL WHERE org='dev' AND namespace='local' "
        "AND memory_key='docker/vector-smoke' AND tombstoned_at IS NULL",
    )
    assert memory is not None
    assert memory["embedding_model"] == model and memory["embedding_dim"] == dimensions, memory
    assert memory["memory_key_embedding_dim"] == dimensions and memory["width"] == dimensions
    assert memory["verified"] == 1 and memory["tags"] == ["provider-smoke"]
    assert memory["content_excerpt"] == "Provider rebuild preserves metadata"
    code = await client.query_one(
        "SELECT embedding_model, embedding_dim, length(embedding) AS width, content, start_line "
        "FROM loom.code_chunks FINAL WHERE org='dev' AND namespace='code-provider-smoke' "
        "AND path='example.py' AND tombstoned_at IS NULL",
    )
    assert code is not None
    assert code["embedding_model"] == model and code["embedding_dim"] == dimensions, code
    assert code["width"] == dimensions and code["content"] == "def greet(): return 42"
    assert code["start_line"] == 1
    copies = await client.query_one(
        "SELECT count() AS n FROM system.tables WHERE database = currentDatabase() "
        "AND startsWith(name, '_provider_rebuild_')"
    )
    assert copies is not None and copies["n"] == 0, copies
    print(f"PASS provider data: {model}/{dimensions}, memory + code metadata, no retained copies")


async def main() -> None:
    client = get_client()
    await client.connect()
    try:
        if sys.argv[1] == "seed":
            await seed()
        elif sys.argv[1] == "workers":
            print(json.dumps(await workers(client)))
        elif sys.argv[1] == "crash-worker":
            await crash_worker(client)
        elif sys.argv[1] == "retired":
            assert await retired(client, json.loads(sys.argv[2])), (
                "Recreated workers did not retire"
            )
        else:
            await verify(sys.argv[1], int(sys.argv[2]))
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
