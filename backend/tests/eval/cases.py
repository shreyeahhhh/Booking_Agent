"""The extraction eval set -- MASTER_PLAN.md Phase 5.2.

Each case is one utterance plus the patches a correct extraction should
produce, scored on three axes (field, op, value) by `run_eval.py`. Scoped
to 20 cases rather than the original ~40: broad enough to cover every
extractor behaviour this project has specifically built and verified live
in isolation (Rule 10 for both localities and items, corrections,
ambiguity, relative-date/time normalisation flags, multi-fact utterances,
confirm/reject/question intents) without spending an outsized share of a
shared, already-strained daily quota on a single eval run -- see
MASTER_PLAN.md's Phase 5.2 write-up for the reasoning.

`expected` uses the same shape run_eval.py compares actual patches
against: {"field": ..., "op": ..., "value": ...}. `value` is compared
loosely (see run_eval.py's `_values_match`) since a locality/item name
going through Rule 10 normalisation is expected to differ from the literal
transcript by design -- that is the feature, not a scoring bug. A case
whose point is ambiguity detection rather than a specific value sets
`value: None`, meaning "any value is acceptable, only the field/op/
ambiguity matter."
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ExpectedPatch:
    field: str
    op: str
    value: object | None = None
    ambiguous: bool = False  # True: this patch must carry an ambiguity reason


@dataclass(frozen=True)
class EvalCase:
    name: str
    utterance: str
    expected: list[ExpectedPatch] = field(default_factory=list)
    expected_intent: str | None = None
    last_question: str | None = None
    # Prior facts CURRENT_STATE must already show for this utterance to be a
    # meaningful correction ("three, not two" needs an existing quantity to
    # correct) -- applied via the real reducer before the eval call, exactly
    # like the app itself would have built that state over earlier turns.
    setup: list[ExpectedPatch] = field(default_factory=list)


CASES: list[EvalCase] = [
    EvalCase(
        "single_locality",
        "I'm moving from Koramangala.",
        [ExpectedPatch("pickup.locality", "set", "Koramangala")],
    ),
    EvalCase(
        "canonical_multi_fact",
        "I need to move a sofa from Koramangala to Whitefield tomorrow evening.",
        [
            ExpectedPatch("pickup.locality", "set", "Koramangala"),
            ExpectedPatch("drop.locality", "set", "Whitefield"),
            ExpectedPatch("goods.items", "append", "sofa"),
            ExpectedPatch("schedule.date", "set"),
            ExpectedPatch("schedule.time_window", "set"),
        ],
    ),
    EvalCase(
        "multiple_items_one_utterance",
        "I have a fridge, a washing machine and ten boxes to send.",
        [
            ExpectedPatch("goods.items", "append", "fridge"),
            ExpectedPatch("goods.items", "append", "washing machine"),
            ExpectedPatch("goods.items", "append", "box"),
        ],
    ),
    EvalCase(
        "quantity_correction",
        "Actually, it's three cupboards, not two.",
        [ExpectedPatch("goods.items", "correct", "cupboard")],
        setup=[ExpectedPatch("goods.items", "append", {"name": "cupboard", "quantity": 2})],
    ),
    EvalCase(
        "locality_mishearing_rule_10",
        "I'm moving from Koro Mengala to White Feeld.",
        [
            ExpectedPatch("pickup.locality", "set", "Koramangala"),
            ExpectedPatch("drop.locality", "set", "Whitefield"),
        ],
    ),
    EvalCase(
        "item_mishearing_rule_10",
        "I need to move a bridge and a cot.",
        [
            ExpectedPatch("goods.items", "append", "fridge"),
            ExpectedPatch("goods.items", "append", "cot"),
        ],
    ),
    EvalCase(
        "nonsense_item_omitted",
        "I also need to move a splendorak.",
        [],  # correct behaviour is emitting nothing at all
    ),
    EvalCase(
        "vague_location_stays_ambiguous",
        "Somewhere in the city, not sure exactly.",
        [ExpectedPatch("pickup.locality", "set", None, ambiguous=True)],
        last_question="Where are you moving from?",
    ),
    EvalCase(
        "relative_date_flagged_not_computed",
        "This Saturday.",
        [ExpectedPatch("schedule.date", "set", None)],
        last_question="What date works for you?",
    ),
    EvalCase(
        "vague_time",
        "Sometime in the day, whenever really.",
        [ExpectedPatch("schedule.time_window", "set", None, ambiguous=True)],
        last_question="What time of day works?",
    ),
    EvalCase(
        "is_asap_detected",
        "As soon as possible please, it's urgent.",
        [ExpectedPatch("schedule.is_asap", "set", True)],
    ),
    EvalCase(
        "floor_and_lift_combined",
        "Third floor, and yes there's a lift.",
        [
            ExpectedPatch("pickup.floor", "set", 3),
            ExpectedPatch("pickup.has_lift", "set", True),
        ],
        last_question="Which floor is the pickup on, and is there a lift?",
    ),
    EvalCase(
        "explicit_vehicle_request",
        "Can you send a bigger truck, please?",
        [ExpectedPatch("service.vehicle_type", "set", None)],
    ),
    EvalCase(
        "landmark_mentioned",
        "It's near the Infopark.",
        [ExpectedPatch("pickup.landmark", "set", None)],
        last_question="Is there a landmark near the pickup?",
        setup=[ExpectedPatch("pickup.locality", "set", "Kakkanad")],
    ),
    EvalCase(
        "booking_type_explicit",
        "I'm shifting my entire flat.",
        [ExpectedPatch("booking_type", "set", "house_shifting")],
    ),
    EvalCase(
        "note_free_text_requirement",
        "Please handle it carefully, it's fragile.",
        [ExpectedPatch("notes", "append", None)],
    ),
    EvalCase(
        "confirm_intent",
        "Yes, that's correct.",
        [],
        expected_intent="confirm",
        last_question="Is this all correct?",
    ),
    EvalCase(
        "reject_intent",
        "No, that's not right.",
        [],
        expected_intent="reject",
        last_question="Is this all correct?",
    ),
    EvalCase(
        "question_intent_gets_suggested_reply",
        "How long will the move take?",
        [],
        expected_intent="question",
    ),
    EvalCase(
        "correction_of_locality",
        "Actually, make it Indiranagar, not Koramangala.",
        [ExpectedPatch("pickup.locality", "correct", "Indiranagar")],
        last_question="Which floor is the pickup on?",
        setup=[ExpectedPatch("pickup.locality", "set", "Koramangala")],
    ),
]
