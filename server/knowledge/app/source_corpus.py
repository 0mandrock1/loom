"""Current metadata proofs for a synthesis corpus; no document bodies are retained."""

from __future__ import annotations

import asyncio
from dataclasses import InitVar, dataclass, field
from typing import Any

from server.core.ch import sequential_reads, set_query_tag
from server.core.datetimes import to_naive_utc_second
from server.core.diagnostics import private_work
from server.knowledge.app.source_access import Admitted, ReadPlan


class LostCorpus(Exception):
    def __init__(self) -> None:
        super().__init__("document access changed")


def _key(item: Admitted) -> tuple[str, str]:
    return item.candidate["namespace"], item.candidate["source_uri"]


def _identity(item: Admitted) -> tuple[Any, ...]:
    return (
        item.namespace,
        item.document["version"],
        item.document["revision"],
        to_naive_utc_second(item.document["ingested_at"]),
        item.serving,
    )


@dataclass
class Proofs:
    plan: ReadPlan
    items: InitVar[tuple[Admitted, ...]]
    invalid: bool = field(default=False, init=False)
    _expected: dict[tuple[str, str], tuple[Any, ...]] = field(default_factory=dict, init=False)
    _checking: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    def __post_init__(self, items: tuple[Admitted, ...]) -> None:
        for item in items:
            key, identity = _key(item), _identity(item)
            if key in self._expected and self._expected[key] != identity:
                self.invalid = True
            self._expected[key] = identity

    @property
    def mirrored(self) -> bool:
        return bool(self._expected)

    async def check(self) -> None:
        """Renew every retained copy; an invalid corpus cannot be admitted again."""
        async with self._checking:
            if self.invalid:
                raise LostCorpus()
            if not self._expected:
                return
            set_query_tag("knowledge.mirror.synthesis_proof")
            try:
                with private_work(), sequential_reads():
                    await self._check()
            except LostCorpus:
                self.invalid = True
                raise
            except Exception:  # noqa: BLE001 — conditional proof failures disclose no backend details
                self.invalid = self.plan.failed = True
                raise LostCorpus() from None

    async def _check(self) -> None:
        if self.plan.failed or self.plan.unread:
            raise LostCorpus()
        keys, refreshed = list(self._expected), []
        for start in range(0, len(keys), 64):
            batch = keys[start : start + 64]
            current = await self.plan.check(
                [{"namespace": ns, "source_uri": uri} for ns, uri in batch]
            )
            by_key = {_key(item): item for item in current}
            if (
                self.plan.failed
                or self.plan.unread
                or len(current) != len(batch)
                or set(by_key) != set(batch)
            ):
                raise LostCorpus()
            for key in batch:
                item = by_key[key]
                if _identity(item) != self._expected[key]:
                    raise LostCorpus()
                refreshed.append(item)
        if not all(self.plan.current(item) for item in refreshed):
            raise LostCorpus()
