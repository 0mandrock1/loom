"""Pure whole-unit planning over authenticated prepared English containers.

Callers authenticate selected reply references and all cross-version pair refusals.
This module checks source bindings and cuts; it neither resolves replies nor admits calls.
Every reused reply still needs validation against its returned current SourceContext,
followed by complete-page structural and semantic review before publication.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
from itertools import pairwise
from typing import Literal

from scripts.i18n import chunks
from scripts.i18n.context import SourceContext


@dataclass(frozen=True)
class PreparedSource:
    """Exact assembled English with page-wide anchors, rewritten links and placeholders."""

    path: str
    english: str
    sha256: str


@dataclass(frozen=True)
class CompletedUnit:
    start: int
    end: int
    source_sha256: str
    reply_ref: str


@dataclass(frozen=True)
class UnitFault:
    reply_ref: str
    finding_id: str


@dataclass(frozen=True)
class ReconciliationConflict:
    kind: str
    reply_refs: tuple[str, ...]
    message: str


@dataclass(frozen=True)
class PlannedUnit:
    """A disposition, never paid admission. Only correction may replay the old assistant."""

    scope: SourceContext
    disposition: Literal["reuse", "correction", "changed", "new"]
    original: CompletedUnit | None = None
    fault_ids: tuple[str, ...] = ()
    separator_before: str = ""


@dataclass(frozen=True)
class Reconciliation:
    current: PreparedSource
    units: tuple[PlannedUnit, ...] = ()
    removed: tuple[CompletedUnit, ...] = ()
    conflicts: tuple[ReconciliationConflict, ...] = ()
    trailing_separator: str = ""
    historical: PreparedSource | None = None


def _digest(text: str) -> str:
    return sha256(text.encode()).hexdigest()


def _failure(current, kind, message, units=()):
    return Reconciliation(
        current,
        conflicts=(ReconciliationConflict(kind, tuple(u.reply_ref for u in units), message),),
    )


def _safe_end(text: str, end: int, boundaries: set[int]) -> bool:
    # Nonfinal requests omit the one newline owned by the assembly separator.
    return end == len(text) or (end < len(text) and text[end] == "\n" and end + 1 in boundaries)


def _history_problem(historical, current, units, faults):
    if historical.path != current.path or not current.path:
        return "context", "historical and current source paths must agree"
    if any(_digest(s.english) != s.sha256 for s in (historical, current)):
        return "authentication", "prepared container hash differs"
    previous = -1
    refs = set()
    for unit in units:
        if not (0 <= unit.start < unit.end <= len(historical.english)):
            return "authentication", "original unit range is invalid"
        if (previous == -1 and unit.start != 0) or (
            previous != -1 and historical.english[previous : unit.start] != "\n"
        ):
            return (
                "authentication",
                "original units must cover the container with newline separators",
            )
        if unit.start <= previous or not unit.reply_ref or unit.reply_ref in refs:
            return "authentication", "original units overlap or repeat a reply reference"
        if _digest(historical.english[unit.start : unit.end]) != unit.source_sha256:
            return "authentication", "original unit source hash differs"
        refs.add(unit.reply_ref)
        previous = unit.end
    if (units and previous != len(historical.english)) or (not units and historical.english):
        return "authentication", "original units do not cover the complete container"
    if any(f.reply_ref not in refs or not f.finding_id for f in faults):
        return "context", "fault does not identify an authenticated original unit and finding"
    return None


def _matches(old_text, unit, current, boundaries):
    text = old_text[unit.start : unit.end]
    size = len(text)
    # Compare complete original units only at parser-safe endpoints, never partial matches.
    return [
        (start, start + size)
        for start in sorted(boundaries)
        if start + size <= len(current)
        and _safe_end(current, start + size, boundaries)
        and current[start : start + size] == text
        and _digest(current[start : start + size]) == unit.source_sha256
    ]


def _new_gap(source, start, end):
    pieces = chunks.split(source.english[start:end])
    planned = []
    for piece in pieces:
        planned.append(
            PlannedUnit(
                SourceContext(source.path, source.english, start, start + len(piece)), "new"
            )
        )
        start += len(piece) + 1
    return planned


def reconcile_completed_units(
    historical: PreparedSource,
    current: PreparedSource,
    units: tuple[CompletedUnit, ...],
    *,
    faults: tuple[UnitFault, ...] = (),
    pair_refused: bool = False,
) -> Reconciliation:
    """Keep unique monotone whole units; bound only genuine new gaps and replacements.

    Original ranges must cover the historical container, separated by exactly one
    assembly newline. Repeated candidates, multiple changed units between retained
    neighbors, unsafe cuts and whole-page replacements are explicit conflicts.
    Changed input carries fault IDs but must never replay an old-input assistant.
    """
    if pair_refused:
        return _failure(current, "refusal", "locale/source pair is refused across source versions")
    if problem := _history_problem(historical, current, units, faults):
        return _failure(current, *problem)
    try:
        old_cuts = set(chunks.request_boundaries(historical.english))
        cuts = set(chunks.request_boundaries(current.english))
    except ValueError as error:
        return _failure(current, "boundary", str(error))
    if any(
        u.start not in old_cuts or not _safe_end(historical.english, u.end, old_cuts) for u in units
    ):
        return _failure(current, "boundary", "original unit cuts are not parser-safe", units)
    fault_ids = {
        unit.reply_ref: tuple(
            sorted({f.finding_id for f in faults if f.reply_ref == unit.reply_ref})
        )
        for unit in units
    }
    anchors = []
    for index, unit in enumerate(units):
        matches = _matches(historical.english, unit, current.english, cuts)
        if len(matches) > 1:
            return _failure(
                current, "ambiguity", "original whole unit has repeated current candidates", (unit,)
            )
        if matches:
            anchors.append((index, *matches[0]))
    if any(left[2] >= right[1] for left, right in pairwise(anchors)):
        return _failure(
            current, "ambiguity", "whole-unit alignment is not monotone and disjoint", units
        )
    if len(units) == 1 and (not anchors or fault_ids[units[0].reply_ref]):
        return _failure(
            current, "whole_page_replacement", "a completed whole page cannot be replaced", units
        )
    return _plan(historical, current, units, anchors, cuts, fault_ids)


def _plan(historical, current, units, anchors, cuts, fault_ids):
    planned, removed = [], []
    left_index, left_end = -1, -1
    for index, start, end in [*anchors, (len(units), len(current.english) + 1, 0)]:
        missing = units[left_index + 1 : index]
        gap_start, gap_end = left_end + 1, start - 1
        if len(missing) > 1:
            return _failure(
                current,
                "boundary",
                "multiple changed original units have no proven current cuts",
                missing,
            )
        if gap_start < gap_end:
            if gap_start not in cuts or not _safe_end(current.english, gap_end, cuts):
                return _failure(
                    current,
                    "boundary",
                    "current counterpart endpoints are not parser-safe",
                    missing,
                )
            scope = SourceContext(current.path, current.english, gap_start, gap_end)
            if missing:
                original = missing[0]
                if len(current.english[gap_start:gap_end].encode()) > chunks.MAX_BYTES:
                    return _failure(
                        current,
                        "boundary",
                        "changed original unit exceeds the new-request bound",
                        missing,
                    )
                planned.append(
                    PlannedUnit(scope, "changed", original, fault_ids[original.reply_ref])
                )
            else:
                try:
                    planned.extend(_new_gap(current, gap_start, gap_end))
                except ValueError as error:
                    return _failure(current, "boundary", str(error))
        else:
            removed.extend(missing)
        if index < len(units):
            original = units[index]
            ids = fault_ids[original.reply_ref]
            if ids and len(current.english[start:end].encode()) > chunks.MAX_BYTES:
                return _failure(
                    current,
                    "boundary",
                    "faulty original unit exceeds the new-request bound",
                    (original,),
                )
            planned.append(
                PlannedUnit(
                    SourceContext(current.path, current.english, start, end),
                    "correction" if ids else "reuse",
                    original,
                    ids,
                )
            )
        left_index, left_end = index, end
    previous, separated = 0, []
    for unit in planned:
        if unit.scope.start not in cuts or not _safe_end(current.english, unit.scope.end, cuts):
            return _failure(current, "boundary", "planned request cuts are not parser-safe")
        separator = current.english[previous : unit.scope.start]
        if unit.scope.start < previous or separator.strip("\n"):
            return _failure(current, "context", "planned ranges leave uncovered source or overlap")
        separated.append(replace(unit, separator_before=separator))
        previous = unit.scope.end
    trailing = current.english[previous:]
    if trailing.strip("\n"):
        return _failure(current, "context", "planned ranges leave uncovered trailing source")
    return Reconciliation(
        current,
        tuple(separated),
        tuple(removed),
        trailing_separator=trailing,
        historical=historical,
    )
