"""Completed translation units retain their authenticated cuts across source edits."""

from dataclasses import replace
from hashlib import sha256

import pytest

from scripts.i18n import chunks
from scripts.i18n.reconcile import (
    OriginalUnit,
    PreparedSource,
    UnitFault,
    original_unit_manifest_sha256,
    reconcile_completed_units,
)


def _reconcile(historical, current, units, **kwargs):
    return reconcile_completed_units(
        historical,
        current,
        units,
        unit_manifest_sha256=original_unit_manifest_sha256(units),
        **kwargs,
    )


def _source(text):
    return PreparedSource("docs/guide.md", text, sha256(text.encode()).hexdigest())


def _history(*parts):
    source = _source("\n".join(parts))
    units, start = [], 0
    for i, part in enumerate(parts):
        units.append(
            OriginalUnit(start, start + len(part), sha256(part.encode()).hexdigest(), str(i))
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
    plan = _reconcile(old, old, units)
    _assert_assembly(plan)
    assert [u.disposition for u in plan.units] == ["reuse"]
    assert plan.units[0].original == units[0]


def test_insertion_uses_current_context_and_keeps_original_neighbors():
    old, units = _history("First paragraph.\n", "Final paragraph.\n")
    current = _source("First paragraph.\n\nInserted paragraph.\n\nFinal paragraph.\n")
    plan = _reconcile(old, current, units)
    _assert_assembly(plan)
    assert [u.disposition for u in plan.units] == ["reuse", "new", "reuse"]
    assert [u.original for u in plan.units] == [units[0], None, units[1]]
    assert all(
        u.scope.path == current.path and u.scope.english == current.english for u in plan.units
    )


def test_removal_never_repackages_retained_units():
    old, units = _history("First.\n", "Removed.\n", "Last.\n")
    current = _source("First.\n\nLast.\n")
    plan = _reconcile(old, current, units)
    _assert_assembly(plan)
    assert [u.original for u in plan.units] == [units[0], units[2]]
    assert plan.removed == (units[1],)


def test_changed_unit_coalesces_faults_without_old_assistant_replay():
    old, units = _history("First.\n", "Old statement.\n", "Last.\n")
    current = _source("First.\n\nChanged statement.\n\nLast.\n")
    faults = (UnitFault("1", "semantic"), UnitFault("1", "language"), UnitFault("1", "semantic"))
    plan = _reconcile(old, current, units, faults=faults)
    _assert_assembly(plan)
    assert [u.disposition for u in plan.units] == ["reuse", "changed", "reuse"]
    assert plan.units[1].fault_ids == ("language", "semantic")
    assert _texts(plan)[1] == "Changed statement.\n"


def test_unchanged_fault_is_explicit_original_unit_correction():
    old, units = _history("First.\n", "Last.\n")
    plan = _reconcile(old, old, units, faults=(UnitFault("1", "semantic"),))
    _assert_assembly(plan)
    assert [u.disposition for u in plan.units] == ["reuse", "correction"]
    assert plan.units[1].original == units[1]


@pytest.mark.parametrize("text", ["Repeated.\n\nRepeated.\n", "Repeated.\n\nExtra.\n\nRepeated.\n"])
def test_repeated_whole_unit_is_an_explicit_ambiguity(text):
    old, units = _history("Repeated.\n", "Repeated.\n")
    plan = _reconcile(old, _source(text), units)
    assert [c.kind for c in plan.conflicts] == ["ambiguity"]
    assert not plan.units


@pytest.mark.parametrize("wrapper", [("<div>\n\n", "\n</div>\n"), ("```text\n", "```\n")])
def test_identical_text_inside_a_new_protected_container_is_not_reused(wrapper):
    old, units = _history("First.\n", "Last.\n")
    current = _source(wrapper[0] + old.english + wrapper[1])
    plan = _reconcile(old, current, units)
    assert plan.conflicts
    assert not plan.units


def test_pair_refusal_blocks_even_unchanged_source():
    old, units = _history("A paragraph.\n")
    plan = _reconcile(old, old, units, pair_refused=True)
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
    plan = _reconcile(old, current, units)
    assert plan.conflicts
    assert not plan.units


def test_changed_historical_whole_page_is_not_repaid():
    old, units = _history("Original page.\n")
    plan = _reconcile(old, _source("Changed page.\n"), units)
    assert [c.kind for c in plan.conflicts] == ["whole_page_replacement"]


def test_multiple_changed_units_refuse_to_guess_a_new_partition():
    old, units = _history("First.\n", "Old one.\n", "Old two.\n", "Last.\n")
    current = _source("First.\n\nReplacement one.\n\nReplacement two.\n\nLast.\n")
    plan = _reconcile(old, current, units)
    assert [c.kind for c in plan.conflicts] == ["boundary"]
    assert not plan.units


def test_reordered_units_are_not_reused_in_new_order():
    old, units = _history("First.\n", "Last.\n")
    plan = _reconcile(old, _source("Last.\n\nFirst.\n"), units)
    assert [c.kind for c in plan.conflicts] == ["ambiguity"]


def test_fault_cannot_target_an_unknown_reply():
    old, units = _history("First.\n", "Last.\n")
    plan = _reconcile(old, old, units, faults=(UnitFault("absent", "fault"),))
    assert [c.kind for c in plan.conflicts] == ["context"]


def test_faulty_large_original_unit_cannot_be_sent_again():
    old, units = _history("First.\n", "界" * 6000 + "\n", "Last.\n")
    free = _reconcile(old, old, units)
    _assert_assembly(free)
    repair = _reconcile(old, old, units, faults=(UnitFault("1", "fault"),))
    assert [c.kind for c in repair.conflicts] == ["boundary"]
    assert not repair.units


def test_new_gap_is_bounded_without_splitting_completed_neighbors():
    old, units = _history("First.\n", "Last.\n")
    paragraphs = "Inserted paragraph " + "界" * 2000 + "\n\n"
    current = _source("First.\n\n" + paragraphs * 3 + "Last.\n")
    plan = _reconcile(old, current, units)
    _assert_assembly(plan)
    assert plan.units[0].original == units[0] and plan.units[-1].original == units[-1]
    assert len(plan.units) > 3
    assert all(
        len(text.encode()) <= chunks.MAX_BYTES
        for u, text in zip(plan.units, _texts(plan), strict=True)
        if u.disposition != "reuse"
    )


def test_twenty_one_original_units_with_only_first_two_completed():
    old, units = _history(*(f"Unit {i:02d}. " + "source " * 200 + "\n" for i in range(21)))
    units = tuple(unit if i < 2 else replace(unit, reply_ref=None) for i, unit in enumerate(units))
    plan = _reconcile(old, old, units)
    _assert_assembly(plan)
    assert [unit.original for unit in plan.units if unit.disposition == "reuse"] == list(units[:2])
    assert all(unit.original is None for unit in plan.units if unit.disposition == "new")
    assert plan.original_units == units
    assert plan.unit_manifest_sha256 == original_unit_manifest_sha256(units)
    assert all(
        len(text.encode()) <= chunks.MAX_BYTES
        for unit, text in zip(plan.units, _texts(plan), strict=True)
        if unit.disposition == "new"
    )


@pytest.mark.parametrize("incomplete", [0, 1, 2], ids=["start", "middle", "end"])
def test_explicit_incomplete_unit_is_new_without_repaying_completed_neighbors(incomplete):
    old, units = _history("First.\n", "Middle.\n", "Last.\n")
    units = tuple(
        replace(unit, reply_ref=None) if i == incomplete else unit for i, unit in enumerate(units)
    )
    plan = _reconcile(old, old, units)
    _assert_assembly(plan)
    assert [unit.disposition for unit in plan.units] == [
        "new" if i == incomplete else "reuse" for i in range(3)
    ]
    assert [unit.original for unit in plan.units if unit.disposition == "reuse"] == [
        unit for unit in units if unit.reply_ref is not None
    ]


def test_incomplete_gap_tracks_changed_english_outside_completed_cuts():
    old, units = _history("First.\n", "Unfinished one.\n", "Unfinished two.\n", "Last.\n")
    units = (
        units[0],
        replace(units[1], reply_ref=None),
        replace(units[2], reply_ref=None),
        units[3],
    )
    current = _source("First.\n\nA changed gap.\n\nAnother new paragraph.\n\nLast.\n")
    plan = _reconcile(old, current, units)
    _assert_assembly(plan)
    assert [unit.disposition for unit in plan.units] == ["reuse", "new", "reuse"]
    assert plan.units[0].original == units[0] and plan.units[-1].original == units[-1]
    assert _texts(plan)[1] == "A changed gap.\n\nAnother new paragraph.\n"


def test_partial_plan_preserves_completed_deletion_and_incomplete_gaps():
    old, units = _history("Prefix.\n", "First.\n", "Removed.\n", "Last.\n", "Suffix.\n")
    units = (replace(units[0], reply_ref=None), *units[1:4], replace(units[4], reply_ref=None))
    current = _source("Prefix.\n\nFirst.\n\nLast.\n\nSuffix.\n")
    plan = _reconcile(old, current, units)
    _assert_assembly(plan)
    assert plan.removed == (units[2],)
    assert [unit.disposition for unit in plan.units] == ["new", "reuse", "reuse", "new"]


def test_deleted_mixed_completed_and_incomplete_gap_creates_no_replacement():
    old, units = _history("First.\n", "Finished then removed.\n", "Unfinished.\n", "Last.\n")
    units = (units[0], units[1], replace(units[2], reply_ref=None), units[3])
    plan = _reconcile(old, _source("First.\n\nLast.\n"), units)
    _assert_assembly(plan)
    assert plan.removed == units[1:3]
    assert [unit.disposition for unit in plan.units] == ["reuse", "reuse"]


def test_missing_incomplete_manifest_range_is_still_an_authentication_failure():
    old, units = _history("First.\n", "Unfinished.\n", "Last.\n")
    units = (units[0], replace(units[1], reply_ref=None), units[2])
    plan = _reconcile(old, old, (units[0], units[2]))
    assert [conflict.kind for conflict in plan.conflicts] == ["authentication"]


def test_completed_reply_cannot_be_relabelled_incomplete_under_authenticated_manifest():
    old, units = _history("First.\n", "Bad settled reply.\n", "Last.\n")
    digest = original_unit_manifest_sha256(units)
    changed = (units[0], replace(units[1], reply_ref=None), units[2])
    plan = reconcile_completed_units(old, old, changed, unit_manifest_sha256=digest)
    assert [conflict.kind for conflict in plan.conflicts] == ["authentication"]
    correction = _reconcile(old, old, units, faults=(UnitFault("1", "semantic"),))
    _assert_assembly(correction)
    assert correction.units[1].disposition == "correction"


def test_uncompleted_unit_cannot_be_a_settled_reply_fault_target():
    old, units = _history("First.\n", "Unfinished.\n", "Last.\n")
    units = (units[0], replace(units[1], reply_ref=None), units[2])
    plan = _reconcile(old, old, units, faults=(UnitFault("1", "fault"),))
    assert [conflict.kind for conflict in plan.conflicts] == ["context"]


def test_partial_history_does_not_clear_repeated_completed_unit_ambiguity_or_refusal():
    old, units = _history("Repeated.\n", "Unfinished.\n", "Repeated.\n")
    units = (units[0], replace(units[1], reply_ref=None), units[2])
    ambiguous = _reconcile(old, old, units)
    assert [conflict.kind for conflict in ambiguous.conflicts] == ["ambiguity"]
    refused = _reconcile(old, old, units, pair_refused=True)
    assert [conflict.kind for conflict in refused.conflicts] == ["refusal"]


def test_never_completed_whole_page_can_receive_its_first_bounded_plan():
    old, units = _history("Not completed.\n")
    units = (replace(units[0], reply_ref=None),)
    plan = _reconcile(old, _source("Current English.\n"), units)
    _assert_assembly(plan)
    assert [unit.disposition for unit in plan.units] == ["new"]
