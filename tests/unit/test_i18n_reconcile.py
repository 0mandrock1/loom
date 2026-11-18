"""Completed translation units retain their authenticated cuts across source edits."""

from dataclasses import replace
from hashlib import sha256

import pytest

from scripts.i18n import chunks
from scripts.i18n.reconcile import (
    CompletedUnit,
    PreparedSource,
    UnitFault,
    reconcile_completed_units,
)


def _source(text):
    return PreparedSource("docs/guide.md", text, sha256(text.encode()).hexdigest())


def _history(*parts):
    source = _source("\n".join(parts))
    units, start = [], 0
    for i, part in enumerate(parts):
        units.append(
            CompletedUnit(start, start + len(part), sha256(part.encode()).hexdigest(), str(i))
        )
        start += len(part) + 1
    return source, tuple(units)


def _texts(plan):
    return [u.scope.english[u.scope.start : u.scope.end] for u in plan.units]


def _assert_assembly(plan):
    assert not plan.conflicts
    assert (
        "".join(u.separator_before + text for u, text in zip(plan.units, _texts(plan), strict=True))
        + plan.trailing_separator
        == plan.current.english
    )


def test_large_completed_whole_page_is_free_reuse():
    old, units = _history("An indivisible paragraph " + "界" * 6000)
    plan = reconcile_completed_units(old, old, units)
    _assert_assembly(plan)
    assert [u.disposition for u in plan.units] == ["reuse"]
    assert plan.units[0].original == units[0]


def test_insertion_uses_current_context_and_keeps_original_neighbors():
    old, units = _history("First paragraph.\n", "Final paragraph.\n")
    current = _source("First paragraph.\n\nInserted paragraph.\n\nFinal paragraph.\n")
    plan = reconcile_completed_units(old, current, units)
    _assert_assembly(plan)
    assert [u.disposition for u in plan.units] == ["reuse", "new", "reuse"]
    assert [u.original for u in plan.units] == [units[0], None, units[1]]
    assert all(
        u.scope.path == current.path and u.scope.english == current.english for u in plan.units
    )


def test_removal_never_repackages_retained_units():
    old, units = _history("First.\n", "Removed.\n", "Last.\n")
    current = _source("First.\n\nLast.\n")
    plan = reconcile_completed_units(old, current, units)
    _assert_assembly(plan)
    assert [u.original for u in plan.units] == [units[0], units[2]]
    assert plan.removed == (units[1],)


def test_changed_unit_coalesces_faults_without_old_assistant_replay():
    old, units = _history("First.\n", "Old statement.\n", "Last.\n")
    current = _source("First.\n\nChanged statement.\n\nLast.\n")
    faults = (UnitFault("1", "semantic"), UnitFault("1", "language"), UnitFault("1", "semantic"))
    plan = reconcile_completed_units(old, current, units, faults=faults)
    _assert_assembly(plan)
    assert [u.disposition for u in plan.units] == ["reuse", "changed", "reuse"]
    assert plan.units[1].fault_ids == ("language", "semantic")
    assert _texts(plan)[1] == "Changed statement.\n"


def test_unchanged_fault_is_explicit_original_unit_correction():
    old, units = _history("First.\n", "Last.\n")
    plan = reconcile_completed_units(old, old, units, faults=(UnitFault("1", "semantic"),))
    _assert_assembly(plan)
    assert [u.disposition for u in plan.units] == ["reuse", "correction"]
    assert plan.units[1].original == units[1]


@pytest.mark.parametrize("text", ["Repeated.\n\nRepeated.\n", "Repeated.\n\nExtra.\n\nRepeated.\n"])
def test_repeated_whole_unit_is_an_explicit_ambiguity(text):
    old, units = _history("Repeated.\n", "Repeated.\n")
    plan = reconcile_completed_units(old, _source(text), units)
    assert [c.kind for c in plan.conflicts] == ["ambiguity"]
    assert not plan.units


@pytest.mark.parametrize("wrapper", [("<div>\n\n", "\n</div>\n"), ("```text\n", "```\n")])
def test_identical_text_inside_a_new_protected_container_is_not_reused(wrapper):
    old, units = _history("First.\n", "Last.\n")
    current = _source(wrapper[0] + old.english + wrapper[1])
    plan = reconcile_completed_units(old, current, units)
    assert plan.conflicts
    assert not plan.units


def test_pair_refusal_blocks_even_unchanged_source():
    old, units = _history("A paragraph.\n")
    plan = reconcile_completed_units(old, old, units, pair_refused=True)
    assert [c.kind for c in plan.conflicts] == ["refusal"]
    assert not plan.units


@pytest.mark.parametrize("change", ["container", "unit", "gap", "context"])
def test_unauthenticated_or_incomplete_history_is_a_conflict(change):
    old, units = _history("First.\n", "Last.\n")
    current = old
    if change == "container":
        old = replace(old, sha256="wrong")
    elif change == "unit":
        units = (replace(units[0], source_sha256="wrong"), units[1])
    elif change == "gap":
        units = units[1:]
    else:
        current = replace(current, path="docs/other.md")
    plan = reconcile_completed_units(old, current, units)
    assert plan.conflicts
    assert not plan.units


def test_changed_historical_whole_page_is_not_repaid():
    old, units = _history("Original page.\n")
    plan = reconcile_completed_units(old, _source("Changed page.\n"), units)
    assert [c.kind for c in plan.conflicts] == ["whole_page_replacement"]


def test_multiple_changed_units_refuse_to_guess_a_new_partition():
    old, units = _history("First.\n", "Old one.\n", "Old two.\n", "Last.\n")
    current = _source("First.\n\nReplacement one.\n\nReplacement two.\n\nLast.\n")
    plan = reconcile_completed_units(old, current, units)
    assert [c.kind for c in plan.conflicts] == ["boundary"]
    assert not plan.units


def test_reordered_units_are_not_reused_in_new_order():
    old, units = _history("First.\n", "Last.\n")
    plan = reconcile_completed_units(old, _source("Last.\n\nFirst.\n"), units)
    assert [c.kind for c in plan.conflicts] == ["ambiguity"]


def test_fault_cannot_target_an_unknown_reply():
    old, units = _history("First.\n", "Last.\n")
    plan = reconcile_completed_units(old, old, units, faults=(UnitFault("absent", "fault"),))
    assert [c.kind for c in plan.conflicts] == ["context"]


def test_faulty_large_original_unit_cannot_be_sent_again():
    old, units = _history("First.\n", "界" * 6000 + "\n", "Last.\n")
    free = reconcile_completed_units(old, old, units)
    _assert_assembly(free)
    repair = reconcile_completed_units(old, old, units, faults=(UnitFault("1", "fault"),))
    assert [c.kind for c in repair.conflicts] == ["boundary"]
    assert not repair.units


def test_new_gap_is_bounded_without_splitting_completed_neighbors():
    old, units = _history("First.\n", "Last.\n")
    paragraphs = "Inserted paragraph " + "界" * 2000 + "\n\n"
    current = _source("First.\n\n" + paragraphs * 3 + "Last.\n")
    plan = reconcile_completed_units(old, current, units)
    _assert_assembly(plan)
    assert plan.units[0].original == units[0] and plan.units[-1].original == units[-1]
    assert len(plan.units) > 3
    assert all(
        len(text.encode()) <= chunks.MAX_BYTES
        for u, text in zip(plan.units, _texts(plan), strict=True)
        if u.disposition != "reuse"
    )
