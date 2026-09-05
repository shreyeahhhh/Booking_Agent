"""The structured-output contract with Groq.

Groq's `strict: true` JSON Schema mode uses constrained decoding: the model's
output is guaranteed to match the schema exactly (docs/design.md SS4.2). That
guarantee only holds if the schema itself is legal strict-mode JSON Schema,
which has two rules beyond plain JSON Schema (verified against Groq's own
docs, not assumed):

1. Every property of every object must be listed in `required` -- there is
   no such thing as an optional key, only a required key whose value may be
   null.
2. Every object must set `additionalProperties: false`.

`app.domain.state.Patch.value` is typed as `object | None` -- deliberately
loose, because the reducer only ever pattern-matches on it at runtime. But an
untyped `object` has no concrete JSON Schema representation, so it cannot be
sent to Groq's strict mode as-is. Rather than loosen that guarantee, or
loosen the domain model to fit a wire-format constraint that has nothing to
do with the reducer's actual needs, this module defines its own
wire-format-legal mirror of Patch/ExtractionResult (with a concrete union for
`value`) and a small adapter to the real domain types. `app.domain` stays
untouched; only this module needs to know Groq's schema quirks exist.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from pydantic import Field as PydanticField

from app.domain.state import AmbiguityReason, Intent, PatchOp, SizeHint
from app.domain.state import ExtractionResult as DomainExtractionResult
from app.domain.state import Patch as DomainPatch

# --------------------------------------------------------------------------
# Wire-format models
# --------------------------------------------------------------------------


class ItemPayload(BaseModel):
    """The shape of `value`/`previous_value` when a patch targets goods.items.

    Mirrors app.domain.state.Item, but every field is required-and-nullable
    per strict mode, rather than carrying a Python-level default -- the "if
    unspecified, assume 1" business rule belongs in exactly one place (Item's
    own default), not duplicated here as an instruction to the model.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    quantity: int | None = None
    size_hint: SizeHint | None = None


# The concrete union that stands in for Patch.value's `object | None` at the
# wire boundary. Deliberately `int`, not `float`: every numeric value that
# ever flows through a patch (floor, quantity) is conceptually a whole
# number in this domain.
PatchValue = str | int | bool | ItemPayload | None


class GroqPatch(BaseModel):
    """Wire-format mirror of app.domain.state.Patch."""

    model_config = ConfigDict(extra="forbid")

    op: PatchOp
    field: str
    value: PatchValue = None
    previous_value: PatchValue = None
    confidence: float = 1.0
    evidence: str | None = None
    needs_normalization: bool = False
    ambiguity: AmbiguityReason | None = None


class GroqExtractionResult(BaseModel):
    """Wire-format mirror of app.domain.state.ExtractionResult."""

    model_config = ConfigDict(extra="forbid")

    intent: Intent
    patches: list[GroqPatch] = PydanticField(default_factory=list)
    unresolved_mentions: list[str] = PydanticField(default_factory=list)
    suggested_reply: str | None = None


# --------------------------------------------------------------------------
# Adapters: wire format -> domain format
# --------------------------------------------------------------------------


def _unwrap(value: PatchValue) -> object | None:
    """Turn a wire-format value into what the reducer already expects.

    `exclude_none=True` matters here, not just for tidiness: strict mode
    means Groq always sends every ItemPayload key, so an unspecified
    quantity arrives as an explicit `null`, not a missing key. Dumping
    without `exclude_none` would pass `quantity=None` straight into
    `Item(**value)`, which domain/state.py types as a plain `int` and would
    reject. Excluding it lets `Item`'s own default (1) apply, which is the
    one place that fallback is allowed to live.
    """
    if isinstance(value, ItemPayload):
        return value.model_dump(exclude_none=True)
    return value


def to_domain_patch(patch: GroqPatch) -> DomainPatch:
    return DomainPatch(
        op=patch.op,
        field=patch.field,
        value=_unwrap(patch.value),
        previous_value=_unwrap(patch.previous_value),
        confidence=patch.confidence,
        evidence=patch.evidence,
        needs_normalization=patch.needs_normalization,
        ambiguity=patch.ambiguity,
    )


def to_domain_extraction(result: GroqExtractionResult) -> DomainExtractionResult:
    return DomainExtractionResult(
        intent=result.intent,
        patches=[to_domain_patch(p) for p in result.patches],
        unresolved_mentions=list(result.unresolved_mentions),
        suggested_reply=result.suggested_reply,
    )


# --------------------------------------------------------------------------
# JSON Schema generation
# --------------------------------------------------------------------------


def _inline_refs(schema: dict) -> dict:
    """Replace every `$ref` pointer with the schema it points to, and drop `$defs`.

    Discovered empirically, not from documentation: Groq's strict-mode
    validator rejects an `anyOf` branch that is a bare `$ref` sitting next to
    a `null` branch as "not disambiguated" -- even when the referenced type
    is a plain enum, which is trivially distinguishable from `null` once
    resolved. The live API returned:

        invalid JSON schema for response_format: ... GroqPatch/properties/
        ambiguity/anyOf: anyOf branches must be disambiguated via a required
        discriminator (const/enum) or by key-set exclusion with
        additionalProperties:false

    which only makes sense if the check runs before `$ref` is resolved, so
    the enum's `enum` key is not visible at the point being checked. Inlining
    everything makes every branch's shape visible with no `$ref` indirection
    left anywhere in the schema Groq actually sees.

    Safe here because nothing in this schema is self-referential (Patch and
    Item never reference themselves), so inlining always terminates.
    """
    defs: dict = schema.get("$defs", {})

    def resolve(node: object) -> object:
        if isinstance(node, dict):
            if "$ref" in node:
                target = defs[node["$ref"].rsplit("/", 1)[-1]]
                resolved = resolve(target)
                # Sibling keys (e.g. a field-specific description) win over
                # the shared definition's own, matching normal override semantics.
                return {**resolved, **{k: resolve(v) for k, v in node.items() if k != "$ref"}}
            return {k: resolve(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    return resolve(schema)  # type: ignore[return-value]


def _force_strict_mode(node: object) -> None:
    """Rewrite a Pydantic-generated JSON Schema in place for Groq's strict mode.

    Recurses through every place an object schema can appear in
    `model_json_schema()` output -- `$defs`, `properties`, `items`, `anyOf` --
    and, for each object schema found, sets `additionalProperties: false` and
    makes every one of its properties required. Everything that is not an
    object schema (enums, plain string/int/bool nodes, $ref pointers) is left
    untouched.
    """
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" in node:
            node["additionalProperties"] = False
            node["required"] = list(node["properties"].keys())
        for value in node.values():
            _force_strict_mode(value)
    elif isinstance(node, list):
        for item in node:
            _force_strict_mode(item)


def to_groq_response_format(model: type[BaseModel], *, name: str) -> dict:
    """Build the `response_format` value for a Groq chat completion call.

    `name` is Groq's label for the schema, not a domain concept -- it shows
    up in error messages, not in the extracted data.
    """
    schema = _inline_refs(model.model_json_schema())
    _force_strict_mode(schema)
    return {
        "type": "json_schema",
        "json_schema": {"name": name, "strict": True, "schema": schema},
    }


EXTRACTION_RESPONSE_FORMAT = to_groq_response_format(
    GroqExtractionResult, name="booking_extraction"
)
