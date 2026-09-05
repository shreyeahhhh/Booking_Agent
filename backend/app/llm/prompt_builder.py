"""Building the extractor's system prompt from the domain schema.

The field-vocabulary section of the prompt is generated from `FIELD_SPECS`
rather than hand-typed into the template, so it cannot silently drift out of
sync with the schema the reducer actually enforces. That drift is not a
hypothetical risk: `booking_type` and `schedule.is_asap` both existed in the
schema with real predicates depending on them, yet nothing populated either
one, for the whole of phase 1 -- caught only while writing this generator
(see docs/design.md SS3.4 and specs.py's `_time_window_matters`). A generated
reference cannot go stale the same way a hand-written list already did once.

Only the fields with a plain scalar `FieldSpec` (REQUIRED or CONDITIONAL) are
generated here. Three kinds of field are deliberately left out and documented
by hand in the template instead, because they need prose a generator would
have to special-case anyway: `goods.items` and `notes` (lists, not scalars,
with their own op/value shape), and the two INFERRED fields (`vehicle_type`,
`helpers_required`), which need an explicit "do not guess this" caveat that
does not apply to anything else in the table.
"""

from __future__ import annotations

from pathlib import Path

from app.domain.specs import FIELD_SPECS, AnswerType, FieldSpec, RequirementKind

_PROMPT_PATH = Path(__file__).parent / "prompts" / "extractor.md"

# No per-field enum-values lookup here (there was one, for goods.category,
# until that field lost its FieldSpec entirely -- see docs/design.md SS3.4):
# service.vehicle_type is the only other ENUM-typed spec, and it is INFERRED
# kind, already excluded below and documented separately in the template. If
# a future REQUIRED/CONDITIONAL enum field is ever added, generating its
# allowed values here (rather than just "enum") is the thing to reinstate.
_TYPE_LABELS: dict[AnswerType, str] = {
    AnswerType.TEXT: "text",
    AnswerType.DATE: 'raw phrase as the user said it (e.g. "tomorrow"), needs_normalization: true',
    AnswerType.TIME_WINDOW: (
        'raw phrase as the user said it (e.g. "evening", "4:30pm"), needs_normalization: true'
    ),
    AnswerType.INTEGER: "integer",
    AnswerType.BOOLEAN: "true / false",
    AnswerType.ENUM: "enum",
}


def _describe(spec: FieldSpec) -> str:
    return f"- `{spec.field_path}` ({_TYPE_LABELS[spec.answer_type]}) -- {spec.label}"


def build_field_reference() -> str:
    """The auto-generated block of plain scalar, directly-askable fields."""
    scalar_specs = (
        spec
        for spec in FIELD_SPECS
        if spec.kind in (RequirementKind.REQUIRED, RequirementKind.CONDITIONAL)
        and spec.field_path != "goods.items"
    )
    return "\n".join(_describe(spec) for spec in scalar_specs)


def load_extractor_system_prompt() -> str:
    """Read the template and interpolate the generated field reference."""
    template = _PROMPT_PATH.read_text(encoding="utf-8")
    return template.replace("{{FIELD_REFERENCE}}", build_field_reference())
