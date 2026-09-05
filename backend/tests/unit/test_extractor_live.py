"""Live proof of the extractor client end to end -- MASTER_PLAN.md step 2.3.

Uses the real AsyncGroq client (not the sync client the 2.1/2.2 live tests
used), which is what app/llm/extractor.py actually calls -- this is the
first test that exercises the real async path against the live API, not
just the mocked control flow test_extractor.py already covers.

Also settles an open question: architecture.md's original "~900 tokens"
figure was an estimate made before any of this existed. This test prints
Groq's own reported prompt_tokens for a realistic multi-turn exchange so
that estimate can be corrected to a measured number.
"""

import pytest
from groq import AsyncGroq

from app.config import get_settings
from app.domain.reducer import apply, confirm_all
from app.domain.specs import get_field
from app.domain.state import BookingState
from app.llm.extractor import Exchange, _build_messages, extract
from app.llm.schema import EXTRACTION_RESPONSE_FORMAT

_settings = get_settings()

pytestmark = [
    pytest.mark.llm,
    pytest.mark.skipif(
        not _settings.is_configured,
        reason="GROQ_API_KEY not configured -- see .env.example",
    ),
]


async def test_multi_turn_conversation_with_a_correction():
    client = AsyncGroq(api_key=_settings.groq_api_key)

    # Turn 1: the project's canonical example, from an empty state.
    result_1 = await extract(
        client,
        model=_settings.groq_llm_model,
        state=BookingState(),
        last_question=None,
        recent_turns=[],
        utterance="I need to move a sofa from Koramangala to Whitefield tomorrow evening.",
    )
    state = apply(BookingState(), result_1.patches).state
    assert get_field(state, "pickup.locality").value is not None
    assert get_field(state, "schedule.date").value is not None

    state = confirm_all(state)  # so the correction below is a real op:correct, not op:set

    # Turn 2: a correction, with realistic context -- a prior question and
    # one exchange of history, exactly what _build_messages assembles.
    result_2 = await extract(
        client,
        model=_settings.groq_llm_model,
        state=state,
        last_question="Which floor is the pickup on?",
        recent_turns=[
            Exchange(
                "agent",
                "Got it -- Koramangala to Whitefield, tomorrow evening. "
                "Which floor is the pickup on?",
            ),
            Exchange("user", "third floor"),
        ],
        utterance="Actually, make it Saturday, not tomorrow.",
    )

    correction_patches = [p for p in result_2.patches if p.field == "schedule.date"]
    assert correction_patches, f"expected a schedule.date patch, got: {result_2.patches}"
    date_patch = correction_patches[0]
    print("\n--- correction patch ---\n", date_patch)
    assert date_patch.op.value == "correct", f"expected op=correct, got {date_patch.op!r}"

    final_state = apply(state, result_2.patches).state
    assert get_field(final_state, "schedule.date").revisions, "expected a revision to be recorded"


async def test_measure_real_prompt_token_usage():
    """Not a correctness test -- a measurement. Reports Groq's own
    prompt_tokens for a realistic mid-conversation call, to replace
    architecture.md's original "~900 tokens" estimate with a real number."""
    client = AsyncGroq(api_key=_settings.groq_api_key)
    state = apply(
        BookingState(),
        (
            await extract(
                client,
                model=_settings.groq_llm_model,
                state=BookingState(),
                last_question=None,
                recent_turns=[],
                utterance=(
                    "I need to move a sofa and two cupboards from Koramangala "
                    "to Whitefield tomorrow evening."
                ),
            )
        ).patches,
    ).state

    messages = _build_messages(
        state,
        "Which floor is the pickup on?",
        [Exchange("agent", "Which floor is the pickup on?"), Exchange("user", "third floor")],
        "Is there a lift? Also there's no rush, whenever works.",
    )
    response = await client.chat.completions.create(
        model=_settings.groq_llm_model,
        messages=messages,
        response_format=EXTRACTION_RESPONSE_FORMAT,
        reasoning_effort="low",
        temperature=0,
        timeout=20,
    )
    print("\n--- measured token usage (mid-conversation call) ---")
    print(f"prompt_tokens: {response.usage.prompt_tokens}")
    print(f"completion_tokens: {response.usage.completion_tokens}")
    print(f"total_tokens: {response.usage.total_tokens}")
    assert response.usage.prompt_tokens > 0
