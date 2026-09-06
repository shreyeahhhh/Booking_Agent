"""The fast-path classifier's positive matches -- MASTER_PLAN.md step 3.3.

Pure function, zero mocking needed: every case here is a direct
(utterance, phase, decision) -> FastPathResult assertion. The adversarial
"must decline to match" corpus lives separately in test_fastpath_safety.py,
mirroring docs/test-plan.md's own Layer 1 / Layer 4 split.
"""

from app.conversation.fastpath import FastPathResult, MetaCommand, classify
from app.conversation.machine import Phase
from app.domain.policy import SlotDecision, SlotReason
from app.domain.state import Intent, PatchOp

_CONFIRM_INFERRED_VEHICLE = SlotDecision(
    "service.vehicle_type", "vehicle type", SlotReason.CONFIRM_INFERRED
)
_MISSING_HAS_LIFT = SlotDecision("pickup.has_lift", "lift at pickup", SlotReason.MISSING)
_AMBIGUOUS_HAS_LIFT = SlotDecision("pickup.has_lift", "lift at pickup", SlotReason.AMBIGUOUS)
_CONFLICT_HAS_LIFT = SlotDecision("pickup.has_lift", "lift at pickup", SlotReason.CONFLICT)
_MISSING_FLOOR = SlotDecision("pickup.floor", "pickup floor", SlotReason.MISSING)
_CONFLICT_FLOOR = SlotDecision("drop.floor", "drop floor", SlotReason.CONFLICT)
_MISSING_LOCALITY = SlotDecision("pickup.locality", "pickup location", SlotReason.MISSING)
_MISSING_DATE = SlotDecision("schedule.date", "date", SlotReason.MISSING)


# --- empty / no match at all -------------------------------------------


def test_empty_utterance_never_matches():
    assert classify("", phase=Phase.REVIEW, decision=None) is None
    assert classify("   ", phase=Phase.REVIEW, decision=None) is None


def test_no_fast_path_at_greeting_even_for_a_bare_yes():
    """decision=None at GREETING is not the same as decision=None at
    REVIEW/COMPLETE -- there is no pending yes/no-shaped question yet."""
    assert classify("yes", phase=Phase.GREETING, decision=None) is None
    assert classify("2", phase=Phase.GREETING, decision=None) is None


# --- meta-commands: context-independent, work at any phase -----------------


def test_repeat_phrases_match_at_any_phase():
    for phase in (Phase.GREETING, Phase.GATHERING, Phase.REVIEW, Phase.CONFIRM_INFERRED):
        result = classify("repeat that", phase=phase, decision=None)
        assert result == FastPathResult(meta_command=MetaCommand.REPEAT)


def test_restart_phrases_match_at_any_phase():
    result = classify("start over", phase=Phase.GATHERING, decision=_MISSING_FLOOR)
    assert result == FastPathResult(meta_command=MetaCommand.RESTART)


def test_various_repeat_and_restart_phrasings():
    for phrase in ("say that again", "come again", "pardon", "sorry what"):
        result = classify(phrase, phase=Phase.REVIEW, decision=None)
        assert result.meta_command == MetaCommand.REPEAT
    for phrase in ("restart", "reset", "start again"):
        result = classify(phrase, phase=Phase.REVIEW, decision=None)
        assert result.meta_command == MetaCommand.RESTART


# --- yes/no as a whole-booking or inferred-guess confirmation ---------------


def test_yes_at_review_confirms_with_no_patches():
    result = classify("yes", phase=Phase.REVIEW, decision=None)
    assert result.extraction.intent == Intent.CONFIRM
    assert result.extraction.patches == []


def test_no_at_review_rejects_with_no_patches():
    result = classify("no", phase=Phase.REVIEW, decision=None)
    assert result.extraction.intent == Intent.REJECT
    assert result.extraction.patches == []


def test_yes_confirms_an_inferred_vehicle_guess_not_a_field_value():
    """decision.field_path is service.vehicle_type (an ENUM), but yes/no
    here means confirm/reject the *guess*, never an enum value."""
    result = classify("yes", phase=Phase.CONFIRM_INFERRED, decision=_CONFIRM_INFERRED_VEHICLE)
    assert result.extraction.intent == Intent.CONFIRM
    assert result.extraction.patches == []


def test_no_rejects_an_inferred_guess():
    result = classify("no", phase=Phase.CONFIRM_INFERRED, decision=_CONFIRM_INFERRED_VEHICLE)
    assert result.extraction.intent == Intent.REJECT


def test_common_affirmation_and_rejection_phrasings_at_review():
    for phrase in ("yeah", "yep", "that's right", "sounds good", "confirmed"):
        result = classify(phrase, phase=Phase.REVIEW, decision=None)
        assert result.extraction.intent == Intent.CONFIRM
    for phrase in ("nope", "nah", "incorrect", "wrong"):
        result = classify(phrase, phase=Phase.REVIEW, decision=None)
        assert result.extraction.intent == Intent.REJECT


def test_case_and_punctuation_are_normalised():
    yes = classify("YES!", phase=Phase.REVIEW, decision=None)
    also_yes = classify("  Yes.  ", phase=Phase.REVIEW, decision=None)
    assert yes.extraction.intent == also_yes.extraction.intent == Intent.CONFIRM


# --- yes/no as a boolean field's own value ----------------------------------


def test_yes_answers_a_pending_boolean_field():
    result = classify("yes", phase=Phase.GATHERING, decision=_MISSING_HAS_LIFT)
    patch = result.extraction.patches[0]
    assert patch.field == "pickup.has_lift"
    assert patch.value is True
    assert patch.op == PatchOp.SET


def test_no_answers_a_pending_boolean_field_as_false():
    result = classify("no", phase=Phase.CLARIFYING, decision=_AMBIGUOUS_HAS_LIFT)
    patch = result.extraction.patches[0]
    assert patch.value is False
    assert patch.op == PatchOp.SET


def test_a_conflicted_boolean_field_is_corrected_not_set():
    """CONFLICT means the field is already CONFIRMED -- op: set would hit
    the reducer's own guard and produce a second, nonsensical conflict
    instead of resolving the first one. op: correct is required."""
    result = classify("no", phase=Phase.CLARIFYING, decision=_CONFLICT_HAS_LIFT)
    assert result.extraction.patches[0].op == PatchOp.CORRECT


def test_boolean_patch_carries_the_original_utterance_as_evidence():
    result = classify("yeah", phase=Phase.GATHERING, decision=_MISSING_HAS_LIFT)
    assert result.extraction.patches[0].evidence == "yeah"


# --- bare numeric as an integer field's own value ---------------------------


def test_a_bare_digit_answers_a_pending_integer_field():
    result = classify("3", phase=Phase.GATHERING, decision=_MISSING_FLOOR)
    patch = result.extraction.patches[0]
    assert patch.field == "pickup.floor"
    assert patch.value == 3
    assert isinstance(patch.value, int)
    assert patch.op == PatchOp.SET


def test_zero_is_a_valid_floor_answer():
    result = classify("0", phase=Phase.GATHERING, decision=_MISSING_FLOOR)
    assert result.extraction.patches[0].value == 0


def test_a_conflicted_integer_field_is_corrected_not_set():
    result = classify("2", phase=Phase.CLARIFYING, decision=_CONFLICT_FLOOR)
    assert result.extraction.patches[0].op == PatchOp.CORRECT


def test_a_word_number_does_not_fast_path_the_integer_matcher():
    """The regex is deliberately "tight" (digits only) -- word numbers need
    the same normalisation judgement dates/enums do, so they escalate."""
    assert classify("three", phase=Phase.GATHERING, decision=_MISSING_FLOOR) is None


# --- no fast path for TEXT / DATE answer types ------------------------------


def test_no_fast_path_for_a_text_typed_field():
    assert classify("yes", phase=Phase.GATHERING, decision=_MISSING_LOCALITY) is None
    assert classify("3", phase=Phase.GATHERING, decision=_MISSING_LOCALITY) is None


def test_no_fast_path_for_a_date_typed_field():
    assert classify("yes", phase=Phase.GATHERING, decision=_MISSING_DATE) is None
