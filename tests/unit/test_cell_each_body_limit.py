"""Fixed administration caps precede JSON parsing when the global cap is off."""

from __future__ import annotations

from typing import Any

import pytest

PATHS = [
    ("capabilities", 1024),
    ("settings_list", 65536),
    ("settings_set", 65536),
    ("settings_history", 65536),
    ("signing_keys_list", 65536),
    ("signing_keys_rotate", 65536),
    ("signing_keys_retire", 65536),
    ("subject_revoke", 65536),
]


async def _request(path: str, body: bytes, *, declared: bool) -> tuple[int, list[bytes], int]:
    from server.transport.body_limit import BodyLimitMiddleware

    messages: list[dict[str, Any]] = []
    delivered: list[bytes] = []
    reads = 0
    chunks = [body] if declared else [body[:-1], body[-1:]]

    async def receive() -> dict[str, Any]:
        nonlocal reads
        reads += 1
        chunk = chunks.pop(0)
        return {"type": "http.request", "body": chunk, "more_body": bool(chunks)}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        while True:
            message = await receive()
            delivered.append(message["body"])
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    scope = {
        "type": "http",
        "path": path,
        "method": "POST",
        "headers": [(b"content-length", str(len(body)).encode())] if declared else [],
    }
    await BodyLimitMiddleware(downstream, max_bytes=0)(scope, receive, send)
    return messages[0]["status"], delivered, reads


@pytest.mark.parametrize(("action", "limit"), PATHS)
@pytest.mark.parametrize("declared", [True, False], ids=["declared", "chunked"])
async def test_feature_body_overflow_is_cut_off_before_materialization(
    action: str, limit: int, declared: bool
) -> None:
    body = b"x" * (limit + 1)
    status, delivered, reads = await _request(f"/v1/admin/cell.{action}", body, declared=declared)
    assert status == 413
    assert len(b"".join(delivered)) <= limit
    if declared:
        assert reads == 0 and delivered == []


@pytest.mark.parametrize(("action", "limit"), PATHS)
async def test_feature_body_at_the_limit_reaches_the_handler(action: str, limit: int) -> None:
    body = b"x" * limit
    status, delivered, _ = await _request(f"/v1/admin/cell.{action}", body, declared=False)
    assert status == 200 and b"".join(delivered) == body


async def test_disabling_global_cap_keeps_ordinary_paths_unchanged() -> None:
    body = b"x" * 65537
    status, delivered, _ = await _request("/ordinary", body, declared=True)
    assert status == 200 and b"".join(delivered) == body
