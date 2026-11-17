"""Refresh every possible marked search pass before choosing a route or a provider."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from server.core.ch import sequential_reads, set_query_tag
from server.core.diagnostics import private_work
from server.knowledge.app.search import ChunkHit, HybridSearchQuery
from server.knowledge.app.source_access import Admitted, ReadPlan
from server.knowledge.app.source_ranking import RankedHit, ordinary_families, rank
from server.knowledge.app.source_search import Hydrated, _chunk_key, discover, hydrate, recheck
from server.knowledge.store.source_candidates import ChunkKey, Leg


@dataclass
class _Pass:
    ordinary: dict[Leg, list[ChunkHit]]
    mirrored: dict[Leg, list[Hydrated]]
    legs: tuple[Leg, ...]
    fallback: list[ChunkHit]
    top_k: int
    lexical_weight: float

    def ranked(self, checked: dict[ChunkKey, Admitted]) -> list[RankedHit]:
        mirrored = {
            leg: [
                replace(
                    item,
                    admitted=replace(
                        checked[_chunk_key(item.admitted.candidate)],
                        candidate=item.admitted.candidate,
                    ),
                )
                for item in pool
                if _chunk_key(item.admitted.candidate) in checked
            ]
            for leg, pool in self.mirrored.items()
        }
        return rank(
            self.ordinary,
            mirrored,
            legs=self.legs,
            fallback=self.fallback,
            top_k=self.top_k,
            lexical_weight=self.lexical_weight,
        )


@dataclass
class SearchPasses:
    plan: ReadPlan
    _passes: list[_Pass] = field(default_factory=list, init=False, repr=False)

    async def collect(
        self,
        query: HybridSearchQuery,
        ordinary: dict[Leg, list[ChunkHit]],
        *,
        legs: tuple[Leg, ...],
        fallback: list[ChunkHit],
        top_k: int,
        lexical_weight: float = 1.0,
    ) -> None:
        """Gather a statically selected pass without exposing provisional rankings."""
        set_query_tag("knowledge.mirror.search_pass")
        mirrored = {}
        try:
            with private_work(), sequential_reads():
                candidates = await discover(
                    self.plan,
                    query,
                    legs=legs,
                    limit=query.top_k,
                    ordinary=ordinary_families({leg: ordinary.get(leg, []) for leg in legs}),
                )
                mirrored = await hydrate(self.plan, candidates, model=query.embedding_model)
        except Exception:  # noqa: BLE001 — a failed conditional pass must disclose no partial mirrors
            self.plan.failed = True
        self._passes.append(_Pass(ordinary, mirrored, legs, fallback, top_k, lexical_weight))

    async def finish(self) -> list[list[RankedHit]]:
        """Return final pass pools; route selection and providers run only after this."""
        set_query_tag("knowledge.mirror.finish_passes")
        try:
            with private_work(), sequential_reads():
                retained: dict[Leg, list[Admitted]] = {}
                for selected in self._passes:
                    for leg, pool in selected.mirrored.items():
                        retained.setdefault(leg, []).extend(item.admitted for item in pool)
                refreshed = await recheck(self.plan, retained)
                checked = {
                    _chunk_key(item.candidate): item for pool in refreshed.values() for item in pool
                }
                if self.plan.failed or self.plan.unread:
                    checked = {}
                return [selected.ranked(checked) for selected in self._passes]
        except Exception:  # noqa: BLE001 — final candidate failure invalidates every earlier mirror pass
            self.plan.failed = True
            return [[RankedHit(hit) for hit in selected.fallback] for selected in self._passes]
