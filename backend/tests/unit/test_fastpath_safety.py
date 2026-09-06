"""Fast-path safety: the adversarial corpus -- docs/test-plan.md Layer 4.

The one way this architecture can silently corrupt state is a fast path
matching when it should not (docs/architecture.md, "Fast paths fail open").
Every case here is a superficially fast-path-shaped utterance that must
still be declined (classify(...) is None), each for a distinct reason.

The "Last question" column in test-plan.md's table is prose; here it is the
actual `(phase, decision)` pair that prose describes -- "Which floor?" is a
pending MISSING/AMBIGUOUS decision on an INTEGER-typed field, "Is that
correct?" is a REVIEW-phase confirmation (decision=None), and "no prior
question" is the true start of a session (phase=GREETING).
"""

from app.conversation.fastpath import classify
from app.conversation.machine import Phase
from app.domain.policy import SlotDecision, SlotReason

_WHICH_FLOOR = SlotDecision("pickup.floor", "pickup floor", SlotReason.MISSING)
_IS_THAT_CORRECT = None  # REVIEW-phase confirmation: no field is pending


def test_a_second_fact_is_not_swallowed_by_the_numeric_matcher():
    """ "third floor, but there's no lift" carries a second fact -- it must
    not be reduced to just the number "3" for the pending floor question."""
    result = classify(
        "third floor, but there's no lift", phase=Phase.GATHERING, decision=_WHICH_FLOOR
    )
    assert result is None


def test_a_correction_is_not_mistaken_for_a_rejection():
    result = classify("no, wait, make it three", phase=Phase.REVIEW, decision=_IS_THAT_CORRECT)
    assert result is None


def test_affirmation_plus_new_information_is_not_a_clean_confirm():
    result = classify("yes, and also add a fridge", phase=Phase.REVIEW, decision=_IS_THAT_CORRECT)
    assert result is None


def test_affirmation_plus_a_correction_is_not_a_clean_confirm():
    result = classify("yeah but not Kochi", phase=Phase.REVIEW, decision=_IS_THAT_CORRECT)
    assert result is None


def test_an_answer_to_a_different_slot_than_the_one_pending_is_declined():
    """ "no lift" is a real, natural answer -- to a lift question. The
    question actually pending is about the floor (INTEGER-typed), so the
    boolean yes/no lexicon is never even consulted for it."""
    result = classify("no lift", phase=Phase.GATHERING, decision=_WHICH_FLOOR)
    assert result is None


def test_a_bare_number_with_no_prior_question_has_no_expected_answer_type():
    """The true start of a session: nothing has been asked yet, so there is
    no field for a bare "2" to be an answer to."""
    result = classify("2", phase=Phase.GREETING, decision=None)
    assert result is None


def test_the_trailing_unit_strip_is_anchored_not_a_general_word_removal():
    """Extending the integer matcher to ordinal words ("third") plus an
    optional trailing "floor" (step 3.4's live-verified fix) must not
    reopen the exact hole the numeric matcher was already guarded against:
    "floor" only strips when it is the *last* word, so a real second fact
    that happens to mention "floor" earlier in the sentence is untouched
    and still fails to reduce to a bare number."""
    result = classify(
        "the floor is third, but there's no lift", phase=Phase.GATHERING, decision=_WHICH_FLOOR
    )
    assert result is None
