"""The Groq structured-output contract -- MASTER_PLAN.md step 2.1.

These tests need no network access and no API key: they check that the
*shape* of the schema and the wire-to-domain adapter are correct. The
separate, network-touching proof that Groq actually accepts this schema
lives in test_llm_extractor_live.py, marked `llm`.
"""

from app.domain.reducer import apply
from app.domain.specs import get_field
from app.domain.state import BookingState, Intent, PatchOp
from app.domain.state import ExtractionResult as DomainExtractionResult
from app.llm.schema import (
    EXTRACTION_RESPONSE_FORMAT,
    GroqExtractionResult,
    GroqPatch,
    ItemPayload,
    to_domain_extraction,
    to_domain_patch,
)


def _walk_object_schemas(node: object):
    """Yield every object-type schema node anywhere in a JSON Schema tree."""
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" in node:
            yield node
        for value in node.values():
            yield from _walk_object_schemas(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_object_schemas(item)


def test_every_object_in_the_schema_is_strict_mode_legal():
    """The property this whole module exists for: Groq's two strict-mode
    rules, checked generically rather than by eyeballing the printed JSON."""
    schema = EXTRACTION_RESPONSE_FORMAT["json_schema"]["schema"]
    object_nodes = list(_walk_object_schemas(schema))
    assert object_nodes, "no object schemas found -- something is very wrong"

    for node in object_nodes:
        assert node["additionalProperties"] is False, node
        assert set(node["required"]) == set(node["properties"].keys()), node


def test_response_format_envelope_matches_groqs_documented_shape():
    assert EXTRACTION_RESPONSE_FORMAT["type"] == "json_schema"
    js = EXTRACTION_RESPONSE_FORMAT["json_schema"]
    assert js["strict"] is True
    assert js["name"] == "booking_extraction"
    assert "schema" in js


def test_no_bare_object_type_survives_in_the_schema():
    """A field typed as `object | None` (unconstrained) is exactly what
    Groq's strict mode cannot accept -- this is the failure mode the whole
    wire-format mirror in schema.py exists to avoid."""
    schema = EXTRACTION_RESPONSE_FORMAT["json_schema"]["schema"]
    for node in _walk_object_schemas(schema):
        assert "properties" in node and node["properties"], (
            f"object schema with no properties (unconstrained): {node}"
        )


def test_boolean_value_is_not_coerced_to_an_integer():
    """Field.value's union has both `bool` and `int` -- Python's bool is an
    int subclass, so this specifically checks Pydantic's union resolution
    picked the exact-type match rather than silently coercing True -> 1."""
    patch = GroqPatch(op=PatchOp.SET, field="pickup.has_lift", value=True)
    assert patch.value is True
    assert isinstance(patch.value, bool)


def test_item_payload_round_trips_through_the_union():
    patch = GroqPatch(
        op=PatchOp.APPEND,
        field="goods.items",
        value=ItemPayload(name="sofa", quantity=1, size_hint=None),
    )
    assert isinstance(patch.value, ItemPayload)
    assert patch.value.name == "sofa"


# --- The adapter: wire format -> domain format -----------------------------


def test_null_quantity_becomes_the_domain_default_not_a_stored_none():
    """Strict mode means Groq always sends `quantity`, even when it has
    nothing to say -- as an explicit null, not a missing key. The adapter
    must drop that null so Item's own default (1) applies, rather than
    passing quantity=None into a field typed as a plain int."""
    wire_patch = GroqPatch(
        op=PatchOp.APPEND,
        field="goods.items",
        value=ItemPayload(name="sofa", quantity=None, size_hint=None),
    )

    domain_patch = to_domain_patch(wire_patch)

    assert domain_patch.value == {"name": "sofa"}  # quantity/size_hint excluded, not None


def test_scalar_values_pass_through_the_adapter_unchanged():
    wire_patch = GroqPatch(op=PatchOp.SET, field="pickup.locality", value="Koramangala")
    domain_patch = to_domain_patch(wire_patch)
    assert domain_patch.value == "Koramangala"


def test_to_domain_extraction_produces_a_real_domain_result():
    wire_result = GroqExtractionResult(
        intent=Intent.PROVIDE_INFO,
        patches=[GroqPatch(op=PatchOp.SET, field="pickup.locality", value="Koramangala")],
        unresolved_mentions=["something about a landlord"],
        suggested_reply=None,
    )
    domain_result = to_domain_extraction(wire_result)
    assert isinstance(domain_result, DomainExtractionResult)
    assert domain_result.patches[0].field == "pickup.locality"
    assert domain_result.unresolved_mentions == ["something about a landlord"]


# --- Full offline round trip: JSON shaped exactly like a real Groq reply,
# with no network call, all the way through the reducer. ---


def test_a_groq_shaped_json_payload_flows_through_the_reducer():
    """Simulates exactly what strict mode guarantees Groq will send: every
    key present, unused ones explicitly null. No network call."""
    raw = {
        "intent": "provide_info",
        "patches": [
            {
                "op": "set",
                "field": "pickup.locality",
                "value": "Koramangala",
                "previous_value": None,
                "confidence": 0.95,
                "evidence": "from Koramangala",
                "needs_normalization": False,
                "ambiguity": None,
            },
            {
                "op": "append",
                "field": "goods.items",
                "value": {"name": "sofa", "quantity": None, "size_hint": None},
                "previous_value": None,
                "confidence": 0.9,
                "evidence": "a sofa",
                "needs_normalization": False,
                "ambiguity": None,
            },
        ],
        "unresolved_mentions": [],
        "suggested_reply": None,
    }

    wire_result = GroqExtractionResult.model_validate(raw)
    domain_result = to_domain_extraction(wire_result)

    result = apply(BookingState(), domain_result.patches)

    assert get_field(result.state, "pickup.locality").value == "Koramangala"
    assert result.state.goods.items[0].name == "sofa"
    assert result.state.goods.items[0].quantity == 1  # Item's own default, not None
