"""The Groq extractor client -- MASTER_PLAN.md step 2.3.

This is where everything built in steps 2.1 and 2.2 becomes one callable:
serialise the state, build the messages, call Groq, and turn whatever comes
back into a domain ExtractionResult the reducer can consume. It is also
where every failure mode observed live during 2.1/2.2 gets handled instead
of left to crash a conversation turn:

- A transient failure (timeout, connection error, rate limit, a 5xx) gets
  one retry.
- Strict mode's occasional schema-violating generation (a real, observed
  Groq 400 -- see docs/design.md SS4.2) is retried the same way; there is no
  bad output to show the model in this case, because Groq's own validator
  rejected it before ever returning content.
- A response that *does* come back but fails to parse as JSON, or fails
  Pydantic validation against the wire schema, gets one repair pass: the
  model is shown its own output and the specific error, and asked to fix it.
- If every attempt is exhausted, this returns a safe, honest fallback
  (intent UNCLEAR, no patches, a spoken "didn't catch that") rather than
  raising -- a failed extraction must never crash the conversation turn.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from functools import cache
from typing import Literal

import groq
from groq import AsyncGroq
from pydantic import ValidationError

from app.domain.specs import ALL_SCALAR_PATHS, get_field
from app.domain.state import BookingState, ExtractionResult, FieldStatus, Intent
from app.llm.prompt_builder import load_extractor_system_prompt
from app.llm.schema import EXTRACTION_RESPONSE_FORMAT, GroqExtractionResult, to_domain_extraction
from app.retry import retry_after_seconds

log = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 20
_MAX_ATTEMPTS = 2  # one call + one retry, for timeout/connection/schema-violation failures

# Retrying an auth or permission failure cannot succeed -- it should fail
# fast and clearly rather than burn the retry budget on something that will
# deterministically fail again.
_RETRIABLE_ERRORS = (
    groq.APIConnectionError,  # also covers APITimeoutError, a subclass
    groq.BadRequestError,  # includes the observed json_validate_failed case
    groq.RateLimitError,
    groq.InternalServerError,
)

FALLBACK_RESULT = ExtractionResult(
    intent=Intent.UNCLEAR,
    patches=[],
    unresolved_mentions=[],
    suggested_reply="Sorry, I didn't quite catch that -- could you say that again?",
)


@dataclass(frozen=True)
class Exchange:
    """One prior turn, for the prompt's RECENT_TURNS. Local to this module --
    conversation-level turn tracking (step 2.5) is a different concern from
    what this module needs to serialise into a prompt."""

    speaker: Literal["user", "agent"]
    text: str


@cache
def _system_prompt() -> str:
    """Computed once per process: the prompt is deterministic for a given
    FIELD_SPECS table, so there is no reason to re-render it every turn."""
    return load_extractor_system_prompt()


def serialize_state(state: BookingState) -> dict[str, object]:
    """A compact, non-empty-only view of the state for CURRENT_STATE.

    An empty booking serialises to `{}`, keeping the prompt small for the
    common early-conversation case where little is known yet. Only values
    are included, never status or confidence -- the model only needs to
    know *that* something is already known (per the extractor prompt's
    correction rule), not the internal provenance bookkeeping around it.
    """
    state_view: dict[str, object] = {
        path: get_field(state, path).value
        for path in ALL_SCALAR_PATHS
        if get_field(state, path).status != FieldStatus.EMPTY
    }
    if state.goods.items:
        state_view["goods.items"] = [
            {"name": item.name, "quantity": item.quantity, "size_hint": item.size_hint}
            for item in state.goods.items
        ]
    if state.notes:
        state_view["notes"] = [note.text for note in state.notes]
    return state_view


def _build_messages(
    state: BookingState,
    last_question: str | None,
    recent_turns: list[Exchange],
    utterance: str,
) -> list[dict[str, str]]:
    turns_text = "\n".join(f"{t.speaker}: {t.text}" for t in recent_turns) or "(none yet)"
    user_content = (
        f"CURRENT_STATE: {json.dumps(serialize_state(state))}\n"
        f"LAST_QUESTION: {json.dumps(last_question)}\n"
        f"RECENT_TURNS:\n{turns_text}\n\n"
        f"User: {utterance}"
    )
    return [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": user_content},
    ]


async def _call(client: AsyncGroq, model: str, messages: list[dict[str, str]]) -> str | None:
    """One attempt. Returns the raw content string, or None on any retriable
    error -- except a rate limit, which is left to propagate uncaught so
    `extract()`'s own loop can inspect it and honour the API's Retry-After
    hint (app.retry, phase 4.4) rather than retrying blind."""
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            response_format=EXTRACTION_RESPONSE_FORMAT,
            reasoning_effort="low",
            temperature=0,
            timeout=_TIMEOUT_SECONDS,
        )
        return response.choices[0].message.content
    except groq.RateLimitError:
        raise
    except _RETRIABLE_ERRORS as err:
        log.warning("extractor call failed: %s: %s", type(err).__name__, err)
        return None


def _parse(content: str) -> tuple[ExtractionResult | None, str | None]:
    """Returns (result, error) with exactly one populated.

    The error string is written for the *model* to read during the repair
    pass, not for a human log -- kept short and specific to what to fix.
    """
    try:
        raw = json.loads(content)
    except json.JSONDecodeError as err:
        return None, f"That was not valid JSON: {err}"
    try:
        wire_result = GroqExtractionResult.model_validate(raw)
    except ValidationError as err:
        return None, f"That did not match the required schema: {err}"
    return to_domain_extraction(wire_result), None


async def extract(
    client: AsyncGroq,
    *,
    model: str,
    state: BookingState,
    last_question: str | None,
    recent_turns: list[Exchange],
    utterance: str,
) -> ExtractionResult:
    """Understand one utterance in context. Never raises -- see module docstring."""
    messages = _build_messages(state, last_question, recent_turns, utterance)

    content: str | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            content = await _call(client, model, messages)
        except groq.RateLimitError as err:
            wait = retry_after_seconds(err)
            if wait is None:
                log.warning(
                    "extractor rate-limited (attempt %d), no short retry-after: %s",
                    attempt + 1,
                    err,
                )
                break
            log.warning(
                "extractor rate-limited (attempt %d), retrying in %.2fs: %s", attempt + 1, wait, err
            )
            await asyncio.sleep(wait)
            continue
        if content is not None:
            break
        log.info("extractor retry %d/%d", attempt + 1, _MAX_ATTEMPTS - 1)
    if content is None:
        return FALLBACK_RESULT

    result, error = _parse(content)
    if result is not None:
        return result

    # Repair pass: the call itself succeeded, but its content did not parse
    # into what is expected. Show the model its own output and the specific
    # error, once, rather than immediately giving up.
    log.info("extractor repair pass: %s", error)
    repair_messages = [
        *messages,
        {"role": "assistant", "content": content},
        {"role": "user", "content": f"{error} Reply again with corrected, schema-valid JSON only."},
    ]
    # No separate retry-after wait budgeted for the repair pass itself: it is
    # already a second chance beyond the normal attempt budget above, and a
    # rate limit here means the exact same thing a None here always has --
    # fall through to the safe fallback rather than raising.
    try:
        repaired_content = await _call(client, model, repair_messages)
    except groq.RateLimitError as err:
        log.warning("extractor repair pass rate-limited: %s", err)
        return FALLBACK_RESULT
    if repaired_content is None:
        return FALLBACK_RESULT

    result, _ = _parse(repaired_content)
    return result if result is not None else FALLBACK_RESULT
