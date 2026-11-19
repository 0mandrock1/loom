"""Numeric HTTP-stage observations for the mixed-version latency fixture only."""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import os
import time
from contextvars import ContextVar
from functools import wraps
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path
from typing import Any

HEADER = "X-Loom-Fixture-Timing"
_STAGES = (
    ("_begin_request", "bind"),
    ("_extract_auth", "auth"),
    ("_read_body", "body"),
    ("_authorize", "authorize"),
    ("_bind_context_and_policy", "policy"),
    ("_rate_limit", "rate"),
    ("_idempotency_gate", "idempotency"),
    ("_start_telemetry", "telemetry"),
    ("_run_handler", "handler"),
    ("_finish_success", "finish"),
)
_TAIL_STAGES = (("settle", "tail_settle"), ("_publication_barrier", "tail_publication"))
_PATHS = {
    "http_sha256": "server/transport/http.py",
    "main_sha256": "server/main.py",
    "request_tail_sha256": "server/core/request_tail.py",
}
_FIELDS = {
    "pid",
    "asgi_unix_ns",
    "asgi_monotonic_s",
    "body_delivered_ms",
    "response_start_ms",
    "endpoint_start_ms",
    *(
        name + suffix
        for _, name in (*_STAGES, *_TAIL_STAGES)
        for suffix in ("_start_ms", "_duration_ms")
    ),
}
_current: ContextVar[dict[str, Any] | None] = ContextVar("mixed_latency_stages", default=None)


def source_hashes(tree: Path) -> dict[str, str]:
    return {
        key: hashlib.sha256((tree / path).read_bytes()).hexdigest() for key, path in _PATHS.items()
    }


def _timed(name: str, function: Any) -> Any:
    def finish(record: dict[str, Any], started: float) -> None:
        # The last completed call wins; an outer call finishes after its nested calls.
        record[name + "_start_ms"] = (started - record["asgi_monotonic_s"]) * 1000
        record[name + "_duration_ms"] = (time.perf_counter() - started) * 1000
        if name == "bind":
            record["endpoint_start_ms"] = record[name + "_start_ms"]

    if inspect.iscoroutinefunction(function):

        @wraps(function)
        async def asynchronous(*args: Any, **kwargs: Any) -> Any:
            record = _current.get()
            if record is None:
                return await function(*args, **kwargs)
            started = time.perf_counter()
            try:
                return await function(*args, **kwargs)
            finally:
                finish(record, started)

        return asynchronous

    @wraps(function)
    def synchronous(*args: Any, **kwargs: Any) -> Any:
        record = _current.get()
        if record is None:
            return function(*args, **kwargs)
        started = time.perf_counter()
        try:
            return function(*args, **kwargs)
        finally:
            finish(record, started)

    return synchronous


class _ObservedApp:
    def __init__(self, app: Any, sources: dict[str, str]) -> None:
        self.app, self.sources = app, sources

    async def __call__(self, scope: Any, receive: Any, send: Any) -> Any:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        record = {
            **self.sources,
            "pid": os.getpid(),
            "asgi_unix_ns": time.time_ns(),
            "asgi_monotonic_s": time.perf_counter(),
        }
        token = _current.set(record)

        async def received() -> Any:
            message = await receive()
            if message["type"] == "http.request" and not message.get("more_body", False):
                record["body_delivered_ms"] = (
                    time.perf_counter() - record["asgi_monotonic_s"]
                ) * 1000
            return message

        async def sent(message: Any) -> None:
            if message["type"] == "http.response.start":
                # This snapshot includes completed pre-header tail calls only. Later
                # body publication checks and final cleanup cannot update this header.
                record["response_start_ms"] = (
                    time.perf_counter() - record["asgi_monotonic_s"]
                ) * 1000
                try:
                    encoded = json.dumps(record, allow_nan=False, separators=(",", ":")).encode(
                        "ascii"
                    )
                    if len(encoded) <= 4096:
                        message = {
                            **message,
                            "headers": [
                                *message.get("headers", []),
                                (HEADER.lower().encode(), encoded),
                            ],
                        }
                except Exception:  # noqa: BLE001 — timing cannot replace a response
                    pass
            await send(message)

        try:
            return await self.app(scope, received, sent)
        finally:
            _current.reset(token)


def create_app() -> _ObservedApp:
    tree = Path.cwd().resolve()
    # The helper directory may be on sys.path; server must still come from this arm.
    for module, relative in (
        ("server", "server/__init__.py"),
        ("server.transport.http", _PATHS["http_sha256"]),
        ("server.main", _PATHS["main_sha256"]),
        ("server.core.request_tail", _PATHS["request_tail_sha256"]),
    ):
        spec = find_spec(module)
        if spec is None or spec.origin is None or Path(spec.origin).resolve() != tree / relative:
            raise RuntimeError("latency observer resolved a server outside its arm")
    http, main = import_module("server.transport.http"), import_module("server.main")
    request_tail = import_module("server.core.request_tail")
    if (
        Path(http.__file__).resolve() != tree / _PATHS["http_sha256"]
        or Path(main.__file__).resolve() != tree / _PATHS["main_sha256"]
        or Path(request_tail.__file__).resolve() != tree / _PATHS["request_tail_sha256"]
    ):
        raise RuntimeError("latency observer imported a server outside its arm")
    for attribute, name in _STAGES:
        setattr(http, attribute, _timed(name, getattr(http, attribute)))
    for attribute, name in _TAIL_STAGES:
        setattr(request_tail, attribute, _timed(name, getattr(request_tail, attribute)))
    return _ObservedApp(main.app, source_hashes(tree))


def parse_metadata(raw: str, expected: dict[str, str]) -> dict[str, Any]:
    if len(raw) > 4096:
        raise ValueError("latency stage metadata exceeded its bound")
    record = json.loads(raw)
    if not isinstance(record, dict) or not record.keys() <= _FIELDS | _PATHS.keys():
        raise ValueError("latency stage metadata has unexpected fields")
    if any(record.get(key) != expected[key] for key in _PATHS):
        raise ValueError("latency stage metadata has the wrong source")
    for key, value in record.items():
        if key in _PATHS:
            continue
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
            or value < 0
        ):
            raise ValueError("latency stage metadata has an invalid number")
    required = ("pid", "asgi_unix_ns", "asgi_monotonic_s", "response_start_ms")
    if not all(key in record for key in required):
        raise ValueError("latency stage metadata is incomplete")
    if not isinstance(record["pid"], int) or record["pid"] <= 0:
        raise ValueError("latency stage metadata has no worker")
    return record
