"""The reference keeps handoff evidence distinct from acceptance and respects its lease."""

from __future__ import annotations

import argparse
import functools
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest

EXAMPLE = Path(__file__).resolve().parents[2] / "sdk/python/examples/software_factory_memory.py"
COMMIT = "a" * 40


def load_example() -> ModuleType:
    spec = importlib.util.spec_from_file_location("software_factory_example", EXAMPLE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def arguments() -> argparse.Namespace:
    return argparse.Namespace(
        org="example-org",
        memory_namespace="shared",
        code_namespace="code-example",
        repo="public/project",
        commit=COMMIT,
        issue_url="https://github.com/public/project/issues/1",
        symbol="Parser.read",
        repo_tag="repo:1234567890",
        review_inbox="existing-review",
        lesson="Check the error path before changing the parser.",
    )


class FactoryServer:
    def __init__(self, case: str = "unchanged") -> None:
        self.case = case
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.memory: dict[str, Any] | None = None
        self.task: dict[str, Any] | None = None
        self.key = ""
        self.closed: list[dict[str, Any]] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        op = req.url.path.removeprefix("/v1/")
        body = json.loads(req.content)
        token = req.headers["authorization"]
        review = token == "Bearer review-token"
        self.calls.append((op, token, body))
        assert body["namespace"] == ("code-example" if op.startswith("code.") else "shared")
        assert body["org"] == "example-org"
        result = self.respond(op, body, review)
        return httpx.Response(200, json=result, headers={"X-Request-Id": "example-receipt"})

    def respond(self, op: str, body: dict[str, Any], review: bool) -> dict[str, Any]:
        if op == "code.index_status":
            commit = "b" * 40 if review and self.case == "changed" else COMMIT
            if self.case == "compiler-behind":
                return {
                    "repo": "public/project",
                    "git_head": commit,
                    "fetch_overdue": False,
                    "compiler_tiers": [{"commit": "d" * 40}],
                }
            return {"repo": "public/project", "git_head": commit, "fetch_overdue": False}
        if op == "code.symbol":
            content = (
                "def read(): return 2"
                if review and self.case == "changed"
                else "def read(): return 1"
            )
            return {
                "definitions": [{"symbol": "Parser.read", "path": "src/parser.py", "body": content}]
            }
        if op == "run.begin":
            return {"run_id": "example-run"}
        if op == "run.complete":
            self.closed.append(body)
            return {"run_id": "example-run", "status": body["status"]}
        if op == "memory.set":
            self.key = body["memory_key"]
            assert body["source_class"] == "derived" and body["tags"] == ["repo:1234567890"]
            assert (
                body["source_resource_id"]
                == f"https://github.com/public/project/blob/{COMMIT}/src/parser.py"
            )
            if self.case == "merged":
                return {"noop": True, "merged_into_key": "unrelated-peer"}
            self.memory = {
                "found": True,
                "value": body["value"],
                "source_class": "derived",
                "agent": "factory-implementation",
                "actor": "process:factory-implementation",
            }
            if self.case == "wrong-actor":
                self.memory["actor"] = "process:someone-else"
            return {"memory_id": "example-note", "content_sha256": "c" * 64}
        if op == "memory.get":
            assert body["memory_key"] == self.key
            return self.memory or {"found": False}
        if op == "memory.delete":
            assert not review and body["memory_key"] == self.key
            self.memory = None
            return {"tombstoned": True}
        return self.task_operation(op, body, review)

    def task_operation(self, op: str, body: dict[str, Any], review: bool) -> dict[str, Any]:
        if op == "task.create":
            assert not review and body["inbox"] == "existing-review"
            self.task = {
                "task_id": "example-task",
                "status": "todo",
                "owner": "",
                "fence": 0,
                "brief": body["brief"],
                "from_subject": "factory-implementation",
            }
        else:
            assert self.task is not None and body["task_id"] == "example-task"
            if op == "task.claim":
                assert review
                self.task.update(
                    owner="factory-reviewer",
                    fence=17,
                    status="in_progress",
                    last_actor="process:factory-reviewer",
                )
            elif op == "task.heartbeat":
                assert review and body["fence"] == 17
                if self.case == "lease-changed":
                    self.task["fence"] = 18
                    raise RuntimeError("Simulated lost lease before any task completion")
            elif op == "task.complete":
                assert review and body["fence"] == 17
                self.task.update(status="done", outcome=body["outcome"])
            elif op == "task.cancel":
                assert review and body["fence"] == 17
                self.task["status"] = "cancelled"
            else:
                assert op == "task.get"
        return {"found": True, "task": self.task}


def configure(monkeypatch: pytest.MonkeyPatch, server: FactoryServer) -> None:
    monkeypatch.setenv("LOOM_BASE_URL", "http://example.invalid")
    monkeypatch.setenv("LOOM_IMPLEMENTATION_API_KEY", "implementation-token")
    monkeypatch.setenv("LOOM_REVIEW_API_KEY", "review-token")
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        functools.partial(httpx.AsyncClient, transport=httpx.MockTransport(server)),
    )


@pytest.mark.parametrize(
    "case,state", [("unchanged", "unchanged_body"), ("changed", "changed_body_needs_review")]
)
async def test_handoff_reports_source_recheck_without_claiming_acceptance(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    case: str,
    state: str,
) -> None:
    server = FactoryServer(case)
    configure(monkeypatch, server)
    await load_example().main(arguments())
    assert server.task is not None and server.task["status"] == "done"
    outcome = server.task["outcome"]
    assert outcome["source_recheck"] == state and outcome["claim_truth"] == "not_verified"
    assert outcome["code_review"] == "not_performed" and outcome["ci"] == "not_run"
    assert len(server.closed) == 1 and server.closed[0]["outcome"] == "none"
    assert server.memory is None and server.key.startswith("example/software-factory/")
    output = capsys.readouterr().out
    for private in ["implementation-token", "review-token", arguments().lesson, "def read()"]:
        assert private not in output
    assert '"merge_ready": false' in output


@pytest.mark.parametrize("case", ["wrong-actor", "merged"])
async def test_bad_provenance_or_noop_never_hands_off_or_follows_a_peer(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    server = FactoryServer(case)
    configure(monkeypatch, server)
    with pytest.raises(RuntimeError):
        await load_example().main(arguments())
    assert server.task is None and server.memory is None
    assert len(server.closed) == 1 and server.closed[0]["status"] == "failed"
    keys = [body["memory_key"] for op, _, body in server.calls if op.startswith("memory.")]
    assert set(keys) == {server.key} and "unrelated-peer" not in keys


async def test_changed_commit_stops_before_writing(monkeypatch: pytest.MonkeyPatch) -> None:
    server = FactoryServer()
    configure(monkeypatch, server)
    args = arguments()
    args.commit = "d" * 40
    with pytest.raises(RuntimeError, match="requested commit"):
        await load_example().main(args)
    assert all(op.startswith("code.") for op, _, _ in server.calls)


async def test_cleanup_does_not_adopt_a_replacement_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    server = FactoryServer("lease-changed")
    configure(monkeypatch, server)
    with pytest.raises(RuntimeError, match="lease changed"):
        await load_example().main(arguments())
    assert not any(op == "task.cancel" for op, _, _ in server.calls)
    assert server.task is not None and server.task["fence"] == 18
    assert server.memory is None and server.closed[0]["status"] == "failed"


async def test_lagging_compiler_stops_before_writing(monkeypatch: pytest.MonkeyPatch) -> None:
    server = FactoryServer("compiler-behind")
    configure(monkeypatch, server)
    with pytest.raises(RuntimeError, match="compiler tier"):
        await load_example().main(arguments())
    assert [op for op, _, _ in server.calls] == ["code.index_status"]
