"""Request-owned work that settles before publication or the next marked call."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

import anyio

from server.core.ch import class_timeout_s, current_query_tag, query_tag, statement_timeout

log = logging.getLogger("loom.request_tail")
REQUIRED_STEP_TIMEOUT_S = 60.0


class RequiredTailError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Required request work was not confirmed")


class _Tail:
    __slots__ = (
        "closed",
        "completed",
        "has_required",
        "keys",
        "lock",
        "required_failed",
        "steps",
    )

    def __init__(self) -> None:
        self.steps: list[tuple[str, Callable[[], Awaitable[Any]], bool]] = []
        self.keys: set[str] = set()
        self.closed = False
        self.completed = False
        self.has_required = False
        self.required_failed = False
        self.lock = asyncio.Lock()


_tail: ContextVar[_Tail | None] = ContextVar("loom_request_tail", default=None)


def active() -> bool:
    tail = _tail.get()
    return tail is not None and not tail.closed


def required_active() -> bool:
    """Whether this request can still own required private-work delivery."""
    tail = _tail.get()
    return tail is not None and not tail.closed and not tail.required_failed


def fail_required() -> bool:
    """Retain a failed mandatory capture without running or closing request work."""
    tail = _tail.get()
    if tail is None:
        return False
    tail.has_required = True
    tail.required_failed = True
    return True


def defer(step: Callable[[], Awaitable[Any]]) -> bool:
    """Run ordinary best-effort work before the response starts."""
    tail = _tail.get()
    if tail is None or tail.closed:
        return False
    tail.steps.append((current_query_tag(), step, False))
    return True


def defer_once(key: str, step: Callable[[], Awaitable[Any]]) -> bool:
    """Defer ordinary work once per key while registration remains open."""
    tail = _tail.get()
    if tail is None or tail.closed:
        return False
    if key not in tail.keys:
        tail.keys.add(key)
        tail.steps.append((current_query_tag(), step, False))
    return True


def deferred(key: str) -> bool:
    tail = _tail.get()
    return tail is not None and key in tail.keys


def defer_required(step: Callable[[], Awaitable[Any]]) -> bool:
    """Register one owned required attempt; its logical caller deduplicates registration."""
    tail = _tail.get()
    if tail is None or tail.closed or tail.required_failed:
        return False
    tail.has_required = True
    tail.steps.append((current_query_tag(), step, True))
    return True


async def _required_step(tail: _Tail, tag: str, step: Callable[[], Awaitable[Any]]) -> None:
    succeeded = False
    try:
        deadlines = [REQUIRED_STEP_TIMEOUT_S]
        deadlines.extend(
            value for kind in ("read", "write") if (value := class_timeout_s(kind)) > 0
        )
        with query_tag(tag), statement_timeout(min(deadlines)):
            async with asyncio.timeout(REQUIRED_STEP_TIMEOUT_S):
                try:
                    await step()
                finally:
                    await asyncio.sleep(0)
        succeeded = True
    except BaseException:  # noqa: BLE001 — required callback outcomes never expose private details
        pass
    finally:
        if not succeeded:
            tail.required_failed = True


async def _ordinary_step(tail: _Tail, tag: str, step: Callable[[], Awaitable[Any]]) -> None:
    try:
        with query_tag(tag):
            try:
                await step()
            finally:
                if tail.has_required:
                    await asyncio.sleep(0)
    except Exception as error:  # noqa: BLE001 — ordinary cleanup stays best effort
        log.warning("request tail step failed: %s", error)


async def _drain(tail: _Tail, required_only: bool) -> None:
    while True:
        try:
            await tail.lock.acquire()
            break
        except asyncio.CancelledError:
            if not tail.has_required:
                raise
            tail.required_failed = True
    try:
        if tail.completed:
            if tail.required_failed:
                raise RequiredTailError() from None
            return
        control: BaseException | None = None
        try:
            while tail.steps:
                index = 0
                if required_only:
                    selected = next((i for i, step in enumerate(tail.steps) if step[2]), None)
                    if selected is None:
                        break
                    index = selected
                tag, step, required = tail.steps.pop(index)
                try:
                    if required:
                        await _required_step(tail, tag, step)
                    else:
                        await _ordinary_step(tail, tag, step)
                except BaseException as error:  # marked cleanup must finish before control returns
                    if not tail.has_required:
                        raise
                    if control is None:
                        control = error
        finally:
            if not required_only and not tail.steps:
                tail.completed = True
        if control is not None:
            raise control from None
        if tail.required_failed:
            raise RequiredTailError() from None
    finally:
        tail.lock.release()


async def _owned_drain(tail: _Tail, required_only: bool) -> None:
    async def attempt() -> BaseException | None:
        try:
            with anyio.CancelScope(shield=True):
                await _drain(tail, required_only)
        except BaseException as error:  # noqa: BLE001 — separate task and waiter cancellation
            return error
        return None

    cancelled: asyncio.CancelledError | None = None
    caller = asyncio.current_task()
    cancellations = caller.cancelling() if caller is not None else 0
    with anyio.CancelScope(shield=True):
        # Enter the ownership wrapper in a distinct task before its first suspension.
        task = asyncio.eager_task_factory(
            asyncio.get_running_loop(), attempt(), name="request.required-tail"
        )
        while True:
            try:
                failure = await asyncio.shield(task)
                break
            except asyncio.CancelledError as error:
                if task.cancelled():
                    tail.required_failed = True
                    failure = RequiredTailError()
                    if caller is not None and caller.cancelling() > cancellations:
                        cancelled = cancelled or asyncio.CancelledError()
                    break
                if cancelled is None:
                    cancelled = error
    try:
        await anyio.lowlevel.checkpoint_if_cancelled()
    except asyncio.CancelledError as error:
        if cancelled is None:
            cancelled = error
    if cancelled is not None:
        raise cancelled from None
    if failure is not None:
        raise failure from None


async def drain_required() -> None:
    """Commit required work between logical calls without closing ordinary cleanup."""
    tail = _tail.get()
    if tail is not None and tail.has_required:
        await _owned_drain(tail, True)


async def settle(tail: _Tail) -> None:
    """Close registration and finish remaining steps, retaining required failure."""
    if tail.closed and not tail.has_required:
        return
    tail.closed = True
    if tail.has_required:
        await _owned_drain(tail, False)
    else:
        await _drain(tail, False)


async def settle_current() -> None:
    tail = _tail.get()
    if tail is not None:
        await settle(tail)


class RequestTailMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        tail = _Tail()
        token = _tail.set(tail)
        primary: BaseException | None = None

        async def _send(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                await settle(tail)
            await send(message)

        try:
            await self.app(scope, receive, _send)
        except BaseException as error:  # preserve the application's error or cancellation
            primary = error
            raise
        finally:
            try:
                try:
                    await settle(tail)
                except BaseException:  # cleanup failure must not replace a primary outcome
                    if primary is None:
                        raise
            finally:
                _tail.reset(token)


__all__ = [
    "RequestTailMiddleware",
    "RequiredTailError",
    "active",
    "defer",
    "defer_once",
    "defer_required",
    "deferred",
    "drain_required",
    "required_active",
    "fail_required",
    "settle",
    "settle_current",
]
