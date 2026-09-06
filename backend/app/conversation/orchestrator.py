"""Transport-independent turn processing -- MASTER_PLAN.md step 3.4.

Extracted from tests/repl.py (step 2.7), which had this "understand -> advance
-> decide what to say" sequence inline before any second caller needed to
share it. The `/turn` endpoint and the text REPL differ in how they get an
`ExtractionResult` (a real LLM call for the REPL, always; a fast-path hit or
an LLM call for the endpoint) and in whether audio is involved at all -- but
once an `ExtractionResult` exists, both need the exact same tail: advance the
phase machine, then decide what text the agent actually says. Duplicating
that tail in two places would let it drift the way `booking_type` and
`schedule.is_asap` drifted in step 2.2 -- defined in one place, silently
unused or reimplemented in another.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from groq import AsyncGroq

from app.conversation import machine, summary, templates
from app.conversation.machine import ConversationState, Phase, TurnResult
from app.domain.policy import DEFAULT_MAX_CLARIFY_ATTEMPTS, SlotDecision
from app.domain.state import ExtractionResult, Intent
from app.llm.extractor import Exchange, extract

MAX_RECENT_TURNS = 8  # 4 exchanges (user+agent pairs) -- matches the extractor
# prompt's own "RECENT_TURNS: the last 4 exchanges" (llm/prompts/extractor.md).


@dataclass(frozen=True)
class TurnOutcome:
    conversation: ConversationState
    decision: SlotDecision | None
    response_text: str


def _uses_suggested_reply(extraction: ExtractionResult) -> bool:
    """The phase 2.4 escape hatch: for a turn templates.py has no slot-based
    question for anyway (a side question, something off-topic, or something
    unparseable), use what the extractor already suggested instead of
    forcing a normal next-question through the machinery."""
    return bool(extraction.suggested_reply) and extraction.intent in (
        Intent.QUESTION,
        Intent.OFF_TOPIC,
        Intent.UNCLEAR,
    )


def _closing_line(phase: Phase) -> str:
    if phase == Phase.REVIEW:
        return "Is this all correct?"
    if phase == Phase.COMPLETE:
        return "Booking confirmed. Thank you!"
    return ""


def compose_response(extraction: ExtractionResult, result: TurnResult) -> str:
    """What the agent actually says, given an extraction and the machine's
    resulting TurnResult. Never calls the LLM itself -- everything it needs
    is already in its two arguments."""
    if _uses_suggested_reply(extraction):
        return extraction.suggested_reply
    if result.decision is not None:
        return templates.compose_turn_response(
            extraction.patches, result.conversation.booking, result.decision
        )
    summary_text = summary.render_summary(result.conversation.booking)
    return f"{summary_text}\n\n{_closing_line(result.conversation.phase)}"


def finish_turn(
    extraction: ExtractionResult,
    conversation: ConversationState,
    *,
    reference: datetime | None = None,
    max_clarify_attempts: int = DEFAULT_MAX_CLARIFY_ATTEMPTS,
) -> TurnOutcome:
    """Advance the machine and compose the response text -- the shared tail
    every caller needs regardless of how `extraction` was produced (a real
    LLM call, or a fast-path hit that skipped one entirely)."""
    result = machine.advance(
        conversation,
        extraction,
        reference=reference,
        max_clarify_attempts=max_clarify_attempts,
    )
    response_text = compose_response(extraction, result)
    return TurnOutcome(
        conversation=result.conversation, decision=result.decision, response_text=response_text
    )


async def process_utterance(
    client: AsyncGroq,
    *,
    model: str,
    conversation: ConversationState,
    last_question: str | None,
    recent_turns: list[Exchange],
    utterance: str,
    reference: datetime | None = None,
    max_clarify_attempts: int = DEFAULT_MAX_CLARIFY_ATTEMPTS,
) -> TurnOutcome:
    """The always-calls-the-LLM path. tests/repl.py uses this directly on
    every turn, by design -- it exists to exercise real extraction quality.
    api/routes.py's /turn endpoint only falls back to this when
    conversation.fastpath.classify() declines to match; a fast-path hit
    builds its own ExtractionResult and calls finish_turn() directly,
    skipping this function (and the LLM call it makes) entirely.
    """
    extraction = await extract(
        client,
        model=model,
        state=conversation.booking,
        last_question=last_question,
        recent_turns=recent_turns,
        utterance=utterance,
    )
    return finish_turn(
        extraction, conversation, reference=reference, max_clarify_attempts=max_clarify_attempts
    )
