"""Live proof that the real prompt fixes what the throwaway prompt could not
-- MASTER_PLAN.md step 2.2.

test_llm_extractor_live.py (step 2.1) used a deliberately minimal, throwaway
prompt and got back invented field names (`from_location`, `moving_date`)
because that prompt never told the model what fields actually exist. This
test loads the real, generated system prompt and checks that the model now
uses genuine schema paths for the canonical example used throughout this
project's docs. It is not a quality eval (that is phase 5's job) -- one
example, checked loosely, just enough to prove the field vocabulary landed.
"""

import json

import groq
import pytest
from groq import Groq

from app.config import get_settings
from app.domain.reducer import apply
from app.domain.specs import get_field
from app.domain.state import BookingState
from app.llm.prompt_builder import load_extractor_system_prompt
from app.llm.schema import EXTRACTION_RESPONSE_FORMAT, GroqExtractionResult, to_domain_extraction

_settings = get_settings()

pytestmark = [
    pytest.mark.llm,
    pytest.mark.skipif(
        not _settings.is_configured,
        reason="GROQ_API_KEY not configured -- see .env.example",
    ),
]


def _call_with_one_retry(client: Groq, messages: list[dict]):
    # See test_llm_extractor_live.py for why one retry: strict mode can still
    # reject an occasional malformed generation with a 400, which is the
    # correct fail-loud behaviour but makes a single live call slightly flaky.
    for attempt in range(2):
        try:
            return client.chat.completions.create(
                model=_settings.groq_llm_model,
                messages=messages,
                response_format=EXTRACTION_RESPONSE_FORMAT,
                reasoning_effort="low",
                temperature=0,
                timeout=20,
            )
        except groq.BadRequestError as err:
            if attempt == 0 and "json_validate_failed" in str(err):
                continue
            raise


def test_real_prompt_uses_genuine_field_paths_for_the_canonical_example():
    client = Groq(api_key=_settings.groq_api_key)
    system_prompt = load_extractor_system_prompt()

    user_message = (
        "CURRENT_STATE: {}\n"
        "LAST_QUESTION: null\n"
        "RECENT_TURNS: []\n\n"
        "User: I need to move a sofa from Koramangala to Whitefield tomorrow evening."
    )

    response = _call_with_one_retry(
        client,
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    )

    parsed = json.loads(response.choices[0].message.content)
    print("\n--- raw Groq response (real prompt) ---\n", json.dumps(parsed, indent=2))

    wire_result = GroqExtractionResult.model_validate(parsed)
    domain_result = to_domain_extraction(wire_result)
    result = apply(BookingState(), domain_result.patches)

    fields_used = {patch.field for patch in domain_result.patches}
    print("--- field paths used ---\n", fields_used)

    # The two locations are the clearest signal: step 2.1's throwaway prompt
    # invented "from_location"/"to_location" for exactly this sentence.
    assert "pickup.locality" in fields_used, f"model still used the wrong field path: {fields_used}"
    assert "drop.locality" in fields_used, f"model still used the wrong field path: {fields_used}"
    assert get_field(result.state, "pickup.locality").value is not None
    assert get_field(result.state, "drop.locality").value is not None
