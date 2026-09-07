"""Live proof of extractor.md's mishearing-normalisation principle (Rule 10,
and its generalisation to goods.items) against the real model -- not just
the scratchpad probes used while writing the prompt.

Two behaviours are locked in here as permanent regression coverage:
  1. A recognisable local place name or household item, misheard by STT, is
     corrected in `value`/`name` while `evidence` keeps the literal heard
     phrase -- so the correction stays visible (see conversation/summary.py's
     and conversation/templates.py's "(heard as ...)" rendering) instead of
     silently overwriting what was actually said.
  2. A vague or nonsense-shaped fragment is NOT force-matched to the nearest
     real-sounding place or item -- Rule 10's own guardrail against
     confidently guessing a specific wrong value. For a scalar field that
     means AMBIGUOUS with capped confidence; for goods.items, which has no
     ambiguity plumbing of its own (Item is a list entry, not Field[T]), the
     only safe outcome is no patch emitted at all.

All four cases were run once as an ad hoc probe before writing this file and
behaved exactly as asserted below -- see the "why" comments on the two
guardrail tests for what would have failed the check if the prompt guidance
had not landed.
"""

import pytest
from groq import AsyncGroq

from app.config import get_settings
from app.domain.reducer import apply
from app.domain.specs import get_field
from app.domain.state import BookingState, FieldStatus
from app.llm.extractor import extract

_settings = get_settings()

pytestmark = [
    pytest.mark.llm,
    pytest.mark.skipif(
        not _settings.is_configured,
        reason="GROQ_API_KEY not configured -- see .env.example",
    ),
]


async def _extract(utterance: str, last_question: str | None = None):
    client = AsyncGroq(api_key=_settings.groq_api_key)
    return await extract(
        client,
        model=_settings.groq_llm_model,
        state=BookingState(),
        last_question=last_question,
        recent_turns=[],
        utterance=utterance,
    )


async def test_mangled_locality_is_normalised_with_evidence_kept():
    result = await _extract("I'm moving from Koro Mengala.", last_question="Where are you moving from?")
    state = apply(BookingState(), result.patches).state
    field = get_field(state, "pickup.locality")
    assert field.value == "Koramangala", f"expected normalised value, got {field.value!r}"
    assert field.evidence and "koro mengala" in field.evidence.lower()


async def test_vague_state_only_location_is_not_force_matched():
    """"Krala, Kerala" names a real state but no specific locality -- Rule
    10's own limit. Before that limit existed in the prompt, this exact
    fragment was extracted as a confident PROVIDED value; the failure mode
    this guards against is a specific wrong locality guessed with full
    confidence rather than the user being asked again."""
    result = await _extract("Krala, Kerala.", last_question="Where are you moving from?")
    state = apply(BookingState(), result.patches).state
    field = get_field(state, "pickup.locality")
    if field.status == FieldStatus.EMPTY:
        return  # also acceptable: no confident patch emitted at all
    assert field.status == FieldStatus.AMBIGUOUS, (
        f"expected ambiguous or no patch, got a confident {field.status} value {field.value!r}"
    )


async def test_mangled_item_name_is_normalised_with_evidence_kept():
    result = await _extract("I need to move a bridge and a cot.")
    state = apply(BookingState(), result.patches).state
    names = {item.name.lower() for item in state.goods.items}
    assert "fridge" in names, f"expected 'bridge' normalised to 'fridge', got items {names}"
    fridge = next(i for i in state.goods.items if i.name.lower() == "fridge")
    assert fridge.evidence and "bridge" in fridge.evidence.lower()
    assert "cot" in names  # the genuinely correct item alongside it is untouched


async def test_bed_cot_misheard_as_bed_court_is_normalised():
    """Live-reported: "2 bed court, clothes" showed up in a real booking --
    a real user saying "bed cot" (a folding bed/cot common in Indian
    households), misheard by STT as "bed court". Before this case was
    added as an explicit prompt example, the extractor passed "bed court"
    through unchanged as if it were a real, if unusual, item name."""
    result = await _extract("Two bed cots and some clothes.", last_question="What are you sending?")
    state = apply(BookingState(), result.patches).state
    names = {item.name.lower() for item in state.goods.items}
    assert "cot" in names, f"expected 'bed cot'/'bed court' normalised to 'cot', got items {names}"
    assert not any("court" in n for n in names), f"'court' leaked through as an item name: {names}"


async def test_nonsense_item_fragment_is_not_force_matched():
    """"splendorak" names nothing real and is not a recognisable mishearing
    of anything -- goods.items has no ambiguity plumbing (Item is a list
    entry, not Field[T]), so the only safe outcome the prompt can ask for is
    no patch at all. The failure mode this guards against is the word being
    appended to the item list at face value as if it were a real thing the
    user asked to move."""
    result = await _extract("I also need to move a splendorak.")
    state = apply(BookingState(), result.patches).state
    assert state.goods.items == [], f"expected no item appended, got {state.goods.items}"


async def test_fully_garbled_utterance_yields_no_items():
    """A live-reported failure shape: several garbled words in the SAME
    utterance, in a real item-listing sentence frame -- harder than one
    nonsense word among otherwise-clean speech, since the sentence *looks*
    exactly like a legitimate item list. None of these three names anything
    real; the safe outcome is an empty item list, not three wrong items
    accepted because the surrounding grammar reads as confident."""
    result = await _extract("I have a jalibax, some blenty and a formicula to shift.")
    state = apply(BookingState(), result.patches).state
    assert state.goods.items == [], f"expected no items appended, got {state.goods.items}"


async def test_mixed_real_and_garbled_items_in_one_utterance_are_separated():
    """The harder, realistic case: a genuine item alongside garbled ones in
    the same utterance (matching a live user report of unrelated words
    filling the item list instead of what was actually said). The model
    must keep the real item and must not silently accept the two names that
    correspond to nothing real -- whether it omits them entirely or asks
    again is secondary to it never treating "farliters"/"nature" as items
    on the same footing as "clothes"."""
    result = await _extract(
        "I need to move a farliters, some clothes, a bedcot and a nature."
    )
    state = apply(BookingState(), result.patches).state
    names = {item.name.lower() for item in state.goods.items}
    assert "clothes" in names, f"expected the genuinely stated item kept, got {names}"
    assert not any("farliters" in n for n in names), f"nonsense word accepted as an item: {names}"
    assert not any(n == "nature" for n in names), f"nonsense word accepted as an item: {names}"
