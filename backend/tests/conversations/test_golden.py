"""Golden conversation tests -- MASTER_PLAN.md Phase 5.3.

Replays real, once-recorded extractor responses (tests/conversations/
fixtures/*.json, captured live by a scratch recording script -- see
MASTER_PLAN.md's Phase 5.3 write-up) through the real deterministic
pipeline (orchestrator.finish_turn -> machine.advance -> reducer ->
policy -> templates). Deterministic, fast, and zero cost: no network call,
survives Groq's own daily quota being exhausted (which happened twice in
the same session that built this), and still exercises real model output
against the real state machine rather than a hand-typed guess at what the
model would say.

Not a substitute for test_extractor_live.py / test_extractor_mishearing_
live.py's own live coverage -- those prove the *model* still behaves
correctly today; these prove the *deterministic pipeline* still handles a
known-good recorded trace correctly, which is a different, complementary
regression risk (a reducer/policy/template change breaking something a
live model call would never surface, since the live call only ever tests
today's actual model output).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.conversation import machine
from app.conversation.orchestrator import finish_turn
from app.conversation.machine import Phase
from app.domain.specs import get_field
from app.llm.schema import GroqExtractionResult, to_domain_extraction

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text())


def _replay(fixture: dict):
    """Drives every recorded turn through the real deterministic pipeline,
    using the recorded raw content in place of a live call. Returns the
    final ConversationState and the list of (utterance, response_text)
    pairs, for tests to assert on either the end state or the trace."""
    conversation = machine.start()
    trace = []
    for turn in fixture["turns"]:
        extraction = to_domain_extraction(
            GroqExtractionResult.model_validate(json.loads(turn["raw_response"]))
        )
        outcome = finish_turn(extraction, conversation)
        conversation = outcome.conversation
        trace.append((turn["utterance"], outcome.response_text))
    return conversation, trace


def test_canonical_house_move_reaches_completion_with_correct_final_state():
    fixture = _load_fixture("canonical_house_move")
    conversation, trace = _replay(fixture)

    assert conversation.phase == Phase.COMPLETE
    booking = conversation.booking
    assert get_field(booking, "pickup.locality").value == "Koramangala"
    assert get_field(booking, "drop.locality").value == "Whitefield"
    assert get_field(booking, "pickup.floor").value == 3
    assert get_field(booking, "pickup.has_lift").value is True
    assert get_field(booking, "drop.floor").value == 0
    assert get_field(booking, "schedule.date").value == "2026-09-12"  # Saturday, the corrected date
    assert get_field(booking, "schedule.date").revisions, "the date correction must be recorded"
    assert {i.name.lower() for i in booking.goods.items} == {"sofa", "cupboard"}
    assert get_field(booking, "service.needs_disassembly").value is False
    assert get_field(booking, "service.needs_packing").value is False


def test_mishearing_normalisation_conversation_corrects_both_localities_and_the_item():
    """This conversation never reaches COMPLETE (the recorded session's
    later turns did not answer the floor question that was still pending --
    itself correct behaviour, not a bug: "confirmed" is not a floor number).
    What this fixture exists to lock in is Rule 10 firing correctly for a
    real recorded model response, on both locality and item mishearings in
    the same conversation."""
    fixture = _load_fixture("mishearing_normalisation")
    conversation, trace = _replay(fixture)

    booking = conversation.booking
    assert get_field(booking, "pickup.locality").value == "Koramangala"
    assert get_field(booking, "drop.locality").value == "Whitefield"
    names = {i.name.lower() for i in booking.goods.items}
    assert "fridge" in names, f"expected 'bridge' normalised to 'fridge', got {names}"
    assert "cot" in names


def test_ambiguous_then_clarified_reaches_completion_with_the_clarified_value():
    fixture = _load_fixture("ambiguous_then_clarified")
    conversation, trace = _replay(fixture)

    assert conversation.phase == Phase.COMPLETE
    booking = conversation.booking
    # "Kochi" (a bare city name) was corrected to "Kakkanad" once clarified --
    # the revision history must show the discarded first answer.
    pickup = get_field(booking, "pickup.locality")
    assert pickup.value == "Kakkanad"
    assert pickup.revisions and pickup.revisions[-1].value == "Kochi"
    assert get_field(booking, "drop.locality").value == "Thodupuzha"


@pytest.mark.parametrize("name", ["canonical_house_move", "mishearing_normalisation", "ambiguous_then_clarified"])
def test_every_fixture_replays_without_raising(name: str):
    """A cheap, broad safety net: whatever else changes about the
    deterministic pipeline, replaying a real recorded conversation must
    never itself raise -- a crash here means a real model output the
    system has already handled once would now break it."""
    fixture = _load_fixture(name)
    _replay(fixture)  # only checking this does not raise
