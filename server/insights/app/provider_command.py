"""Enroll native CLI data-plane work for the duration of the entire command."""

import asyncio
import contextlib
import logging
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
) -> None:
    """A long command (a rechunk, a re-embed) follows an override set while it runs."""
    set_query_tag("cli.settings_overrides")
    while True:
        await sleep(max(1.0, float(settings.runtime_overrides_refresh_seconds)))
        loaded = await _adopt_overrides(failing=not loaded)


@asynccontextmanager
async def participant_command() -> AsyncIterator[None]:
    """Register all native org/principal work, including commands without models."""
    retirement.get_gate().install("command")
    await register_process()
    try:
        async with retirement.get_gate().admit():
            yield
    finally:
        gate = retirement.get_gate()
        gate.close()
        from server.memory.app import embedder_calibration

        embedder_calibration.stop_refresh_loop()
        producers = tuple(gate.producers)
        for producer in producers:
            producer.cancel()
        if producers:
            await asyncio.wait(producers, timeout=COMMAND_DRAIN_S)
        gate.stopped()
        if await drain_task_sets(timeout_s=COMMAND_DRAIN_S) and await flush_registered_buffers():
            await retire_process()


@asynccontextmanager
async def model_command() -> AsyncIterator[None]:
    """The permanent lifecycle also holds the reversible model-work admission."""
    async with participant_command():
        keeper: asyncio.Task[None] | None = None
        try:
            await load_active_configuration()
            loaded = await _adopt_overrides()
            keeper = asyncio.create_task(
                _keep_overrides(loaded), name="loom-cli-settings-overrides"
            )
            async with get_model_work_gate().admit():
                yield
        finally:
            if keeper is not None:
                keeper.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await keeper
