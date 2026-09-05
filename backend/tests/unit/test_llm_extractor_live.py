"""Live proof that Groq accepts the strict-mode schema -- MASTER_PLAN.md step 2.1.

Marked `llm`: makes a real network call and spends a small amount of real
money. Excluded from a plain `pytest` run and skipped automatically if no key
is configured; run explicitly with `pytest -m llm` once GROQ_API_KEY is set.

Deliberately uses a minimal, throwaway prompt, not the real extractor system
prompt -- that is step 2.2's job. This test's only claim is that the wire
contract itself works end to end against the live API: Groq accepts the
schema, returns valid JSON, and that JSON validates and adapts into
reducer-compatible domain patches. It does not judge extraction quality --
that is what step 2.2/2.3's eval set is for.
"""

import json

import groq
import pytest
from groq import Groq

from app.config import get_settings
from app.domain.reducer import apply
from app.domain.state import BookingState
from app.llm.schema import EXTRACTION_RESPONSE_FORMAT, GroqExtractionResult, to_domain_extraction

_settings = get_settings()

pytestmark = [
    pytest.mark.llm,
    pytest.mark.skipif(
        not _settings.is_configured,
        reason="GROQ_API_KEY not configured -- see .env.example",
    ),
]


def test_groq_accepts_the_strict_schema_and_returns_valid_json():
    client = Groq(api_key=_settings.groq_api_key)
    messages = [
        {
            "role": "system",
            "content": (
                "Extract booking-relevant fields from the user's message as "
                "structured patches. This is a throwaway prompt for testing "
                "the schema contract only, not the real extractor prompt."
            ),
        },
        {
            "role": "user",
            "content": "I need to move a sofa from Koramangala to Whitefield tomorrow evening.",
        },
    ]

    # Observed live: even with strict mode and temperature 0, the model can
    # occasionally generate output that violates its own schema (e.g. wrapping
    # an enum value in an object). Groq validates server-side and returns a
    # 400 rather than passing malformed JSON through -- which is the correct,
    # fail-loud behaviour, but it does mean "strict" is not an absolute
    # guarantee against a bad generation, only a guarantee that a bad one is
    # rejected rather than silently delivered. One retry here keeps this
    # empirical-proof test from being flaky on that occasional case; the real
    # retry/repair handling is step 2.3's job, not reproduced in full here.
    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model=_settings.groq_llm_model,
                messages=messages,
                response_format=EXTRACTION_RESPONSE_FORMAT,
                reasoning_effort="low",
                temperature=0,
                timeout=20,
            )
            break
        except groq.BadRequestError as err:
            if attempt == 0 and "json_validate_failed" in str(err):
                continue
            raise

    raw = response.choices[0].message.content
    parsed = json.loads(raw)  # must not raise: proves Groq returned valid JSON
    print("\n--- raw Groq response ---\n", json.dumps(parsed, indent=2))

    wire_result = GroqExtractionResult.model_validate(parsed)  # must not raise: schema-conformant
    domain_result = to_domain_extraction(wire_result)

    # Not asserting exact extraction correctness -- only that the mechanism
    # produced at least one usable, reducer-compatible patch for a clear
    # utterance. Feeding it through the reducer proves the whole chain (Groq
    # -> Pydantic -> adapter -> domain Patch -> reducer) is actually wired
    # together correctly, not just that each piece works in isolation.
    assert domain_result.patches, f"model returned zero patches for a clear utterance: {parsed}"
    result = apply(BookingState(), domain_result.patches)
    print("--- resulting state (partial) ---")
    print("pickup:", result.state.pickup.locality.value)
    print("drop:", result.state.drop.locality.value)
    print("items:", [(i.name, i.quantity) for i in result.state.goods.items])
