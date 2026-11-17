"""Enroll native CLI data-plane work for the duration of the entire command."""

import asyncio
import contextlib
import logging
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from server.core import retirement, settings_overrides
from server.core.background import drain_task_sets, flush_registered_buffers
from server.core.ch import set_query_tag
from server.core.model_work_gate import get_model_work_gate
from server.insights.app.provider_barrier import register_process, retire_process
from server.insights.app.provider_runtime import load_active_configuration
from server.settings import settings

log = logging.getLogger(__name__)

COMMAND_DRAIN_S = 15.0  # bounded cleanup; timeout leaves the participant unretired


async def _adopt_overrides(*, failing: bool = False) -> bool:
    """Whether the stored overrides loaded. A failure is logged when a run of them
    starts: a command that closes its own client fails every refresh until it
    reopens it."""
    try:
        await settings_overrides.refresh()
        return True
    except Exception as e:  # noqa: BLE001 — the command runs on the values it has
        if not failing:
            log.warning("live-setting overrides not adopted: %s", e)
        return False


async def _keep_overrides(
    loaded: bool, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
) -> SystemExit | KeyboardInterrupt | None:
    """A long command (a rechunk, a re-embed) follows an override set while it runs."""
    set_query_tag("cli.settings_overrides")
    try:
        while True:
            await sleep(max(1.0, float(settings.runtime_overrides_refresh_seconds)))
            loaded = await _adopt_overrides(failing=not loaded)
    except (SystemExit, KeyboardInterrupt) as error:
        # A task's fatal exception must reach its owner before the loop stops.
        return error


async def _finish_participant() -> None:
    gate = retirement.get_gate()
    gate.close()
    from server.memory.app import embedder_calibration

    first: BaseException | None = None
    stopped = False
    try:
        embedder_calibration.stop_refresh_loop()
        stopped = True
    except BaseException as error:  # noqa: BLE001 — finish safe cleanup, then raise the first failure
        first = error
    producers = tuple(gate.producers)
    for producer in producers:
        producer.cancel()
    try:
        if producers:
            _, pending = await asyncio.wait(producers, timeout=COMMAND_DRAIN_S)
            if pending:
                raise RuntimeError("Native command producers did not stop")
    except BaseException as error:  # noqa: BLE001 — finish safe cleanup, then raise the first failure
        if first is None:
            first = error
        stopped = False
    for producer in producers:
        if producer.done() and not producer.cancelled():
            try:
                producer.result()
            except BaseException as error:  # noqa: BLE001 — finish safe cleanup, then raise the first failure
                if first is None:
                    first = error
    if stopped:
        gate.stopped()
        try:
            if not await drain_task_sets(timeout_s=COMMAND_DRAIN_S):
                raise RuntimeError("Native command deferred writes did not drain")
            if not await flush_registered_buffers():
                raise RuntimeError("Native command buffers did not flush")
            if await retire_process() is not True:
                raise RuntimeError("Native command retirement was not certified")
        except BaseException as error:  # noqa: BLE001 — finish safe cleanup, then raise the first failure
            if first is None:
                first = error
    if first is not None:
        raise first


@asynccontextmanager
async def participant_command() -> AsyncIterator[None]:
    """Register all native org/principal work, including commands without models."""
    retirement.get_gate().install("command")
    await register_process()
    try:
        async with retirement.get_gate().admit():
            yield
    except BaseException:
        with contextlib.suppress(BaseException):
            await _finish_participant()
        raise
    else:
        await _finish_participant()


@asynccontextmanager
async def model_command() -> AsyncIterator[None]:
    """The permanent lifecycle also holds the reversible model-work admission."""
    async with participant_command():
        keeper: asyncio.Task[SystemExit | KeyboardInterrupt | None] | None = None
        try:
            await load_active_configuration()
            loaded = await _adopt_overrides()
            keeper = asyncio.create_task(
                _keep_overrides(loaded), name="loom-cli-settings-overrides"
            )
            async with get_model_work_gate().admit():
                yield
        finally:
            first = sys.exception()
            if keeper is not None:
                owner = asyncio.current_task()
                assert owner is not None
                cancelling = owner.cancelling()
                requested = keeper.cancel()
                try:
                    try:
                        fatal = await keeper
                    except asyncio.CancelledError:
                        if not requested or owner.cancelling() != cancelling:
                            raise
                    else:
                        if fatal is not None:
                            raise fatal
                except BaseException:
                    if first is None:
                        raise
