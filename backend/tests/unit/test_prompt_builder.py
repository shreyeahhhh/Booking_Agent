"""The extractor prompt is built correctly from the schema -- MASTER_PLAN.md step 2.2."""

import re

import pytest

from app.domain.specs import (
    FIELD_SPECS,
    OPTIONAL_SCALAR_PATHS,
    RequirementKind,
    get_field,
    spec_for,
)
from app.domain.state import BookingState
from app.llm.prompt_builder import build_field_reference, load_extractor_system_prompt

# Fields the template documents by hand rather than generating, because they
# need prose a generator would have to special-case anyway (see
# prompt_builder.py's module docstring). Checked below so this hand-written
# portion cannot drift the same way booking_type/is_asap already did once.
#
# Split by whether a FieldSpec exists at all: vehicle_type/helpers_required
# have one (INFERRED-kind, just excluded from the generated block for its
# "do not guess this" caveat); the rest genuinely have none.
# OPTIONAL_SCALAR_PATHS is specs.py's own canonical list, not re-typed here --
# a second hand-typed copy is exactly the kind of drift this project already
# hit once (booking_type, is_asap). It previously omitted schedule.exact_time
# by the same kind of oversight; importing it directly makes that impossible.
_SPEC_LESS_FIELDS = OPTIONAL_SCALAR_PATHS
_INFERRED_SPEC_FIELDS = ("service.vehicle_type", "service.helpers_required")
_HAND_DOCUMENTED_FIELDS = ("goods.items", "notes") + _SPEC_LESS_FIELDS + _INFERRED_SPEC_FIELDS


def test_field_reference_covers_every_scalar_required_or_conditional_spec():
    reference = build_field_reference()
    for spec in FIELD_SPECS:
        if spec.kind == RequirementKind.INFERRED or spec.field_path == "goods.items":
            continue
        assert f"`{spec.field_path}`" in reference, f"{spec.field_path} missing from reference"


def test_field_reference_excludes_items_and_inferred_fields():
    """These need special prose (list ops, or a "do not guess" caveat) that
    the generic per-spec generator does not produce -- see the module
    docstring for why they are documented by hand in the template instead."""
    reference = build_field_reference()
    assert "goods.items" not in reference
    assert "service.vehicle_type" not in reference
    assert "service.helpers_required" not in reference


def test_every_generated_field_path_actually_resolves():
    """Round-trip check: every path the model is told about must be a real,
    resolvable Field on BookingState -- catches a typo'd path in specs.py
    before it ever reaches a live prompt."""
    state = BookingState()
    for match in re.finditer(r"`([a-z_.]+)`", build_field_reference()):
        path = match.group(1)
        get_field(state, path)  # raises if the path is not real


_LIST_SHAPED_FIELDS = ("goods.items", "notes")  # not Field[T]-wrapped; see state.py


def test_hand_documented_special_fields_are_all_real_paths():
    """The special-fields section is hand-written prose, not generated --
    this is its drift guard, checking each path against the actual schema
    the same way the generated section is checked above."""
    state = BookingState()
    for path in _HAND_DOCUMENTED_FIELDS:
        if path in _LIST_SHAPED_FIELDS:
            obj = state
            for part in path.split("."):
                obj = getattr(obj, part)
            assert isinstance(obj, list)
            continue

        get_field(state, path)  # raises if the path is not a real Field

        if path in _SPEC_LESS_FIELDS:
            with pytest.raises(KeyError):
                spec_for(path)  # genuinely no spec entry
        else:
            assert spec_for(path).kind == RequirementKind.INFERRED


def test_loaded_prompt_has_no_leftover_placeholder():
    prompt = load_extractor_system_prompt()
    assert "{{FIELD_REFERENCE}}" not in prompt


def test_loaded_prompt_contains_every_intent_and_op_value():
    from app.domain.state import Intent, PatchOp

    prompt = load_extractor_system_prompt()
    for intent in Intent:
        assert intent.value in prompt, f"intent {intent.value!r} not documented in the prompt"
    for op in PatchOp:
        assert op.value in prompt, f"op {op.value!r} not documented in the prompt"
