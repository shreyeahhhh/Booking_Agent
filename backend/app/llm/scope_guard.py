"""A cheap, fast scope guardrail -- keeps the booking assistant from acting
like a general-purpose chatbot.

Runs after `conversation.fastpath.classify()` declines to match (a fast-path
hit is already in-scope by construction: it is a direct answer to the
agent's own pending question) and before the main extractor call
(`llm/extractor.py`). Its only job is: is this utterance relevant to
completing or understanding a delivery/transport booking?

Deliberately a separate, single-purpose call rather than folded into the
existing `Intent.OFF_TOPIC` handling already in extractor.md's prompt (see
that file's INTENT section, and `conversation.orchestrator._uses_suggested_
reply`). Two reasons:

1. Cost and latency. extractor.md's system prompt is ~2600 tokens; this
   module's prompt is a small fraction of that. Every off-topic aside a
   real user makes today still pays the full extraction-prompt cost just
   to be told "off_topic" -- for a voice app already living against Groq's
   daily token quota (this session hit it more than once), paying the full
   price to classify a stray "what's 25 times 48" is real, avoidable waste.
2. Reliability. One instruction among many in a large, multi-purpose
   prompt is more easily "argued past" by an adversarial or merely
   talkative user than a call whose *entire* system prompt is this one
   decision. A dedicated classifier is a narrower target.

Fails open, not closed, for every *retriable* failure (a connection error,
an unworkable rate limit, a response that does not parse or does not match
the schema): `classify()` returns `allowed=True` rather than blocking the
turn on a guardrail that could not even complete its own call. This was
originally fail-closed -- the reasoning being that letting an unverifiable
request through "because the bigger model can sort it out" defeats the
point of a cheap guardrail -- but live use surfaced the real cost of that:
this guard runs on its own smaller model with its own separate quota, and
whenever that quota (or just a transient connection issue) made this call
fail, *every* turn showed the fixed redirect until it cleared, regardless
of what was actually said -- an observed "stuck on the same reply no
matter what I say" failure, not a hypothetical one. Fails open now that
the main extractor's own prompt carries a second, independent line of
defence for exactly this case (extractor.md's boundary statement, Rule 9's
off_topic handling) -- so a guard outage degrades to that second layer
rather than to a broken conversation.

An auth/permission failure is still the one exception that propagates
uncaught rather than failing open or closed: the same deliberate choice
llm/extractor.py already makes for the identical reason -- an invalid or
revoked API key is a deployment problem worth surfacing loudly, not a
per-turn condition to paper over silently either way.
"""

from __future__ import annotations

import asyncio
import json
import logging
from enum import StrEnum

import groq
from groq import AsyncGroq
from pydantic import BaseModel, ConfigDict

from app.llm.schema import to_groq_response_format
from app.retry import retry_after_seconds

log = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 8  # generous for a ~30-token response; still fails fast
_MAX_ATTEMPTS = 2  # one call + one retry -- this sits in front of every turn

_RETRIABLE_ERRORS = (
    groq.APIConnectionError,
    groq.BadRequestError,
    groq.InternalServerError,
)


class ScopeIntent(StrEnum):
    """Not app.domain.state.Intent -- that enum describes what the *reducer*
    does with an utterance (provide_info, correction, ...). This one
    describes *why* an utterance is in scope at all, for observability
    (logging, and a future "which intents actually show up live" check),
    not for any branching logic -- routes.py only ever reads `allowed`."""

    BOOKING_START = "booking_start"
    PICKUP_LOCATION = "pickup_location"
    DROP_LOCATION = "drop_location"
    VEHICLE_SELECTION = "vehicle_selection"
    PACKAGE_DETAILS = "package_details"
    PRICE_ESTIMATION = "price_estimation"
    SCHEDULING = "scheduling"
    BOOKING_CONFIRMATION = "booking_confirmation"
    BOOKING_MODIFICATION = "booking_modification"
    CANCELLATION = "cancellation"
    DELIVERY_TIME = "delivery_time"
    SERVICE_INFORMATION = "service_information"
    CLARIFICATION = "clarification"
    SMALL_TALK_RELATED = "small_talk_related_to_booking"
    UNRELATED = "unrelated"


class ScopeDecision(BaseModel):
    """Wire format the classifier returns -- see the module docstring for
    why this is a separate, minimal schema rather than reusing anything
    from llm/schema.py's (much larger) extraction contract."""

    model_config = ConfigDict(extra="forbid")

    allowed: bool
    intent: ScopeIntent


# Fails OPEN, not closed: on any failure to even complete this call, let
# the turn through to the main extractor rather than blocking it. Reversed
# from an earlier fail-closed design after live use showed the actual
# cost: this guard runs on a separate, smaller model with its own quota,
# and a transient hiccup there (or that quota running out) made *every*
# turn show the fixed redirect until it cleared -- a real, observed "stuck
# on the same reply, no matter what I say" failure, not a hypothetical
# one. The main extractor's own prompt was hardened specifically as a
# second line of defence for exactly this case (extractor.md's boundary
# statement, Rule 9's off_topic handling), so failing open here no longer
# means no protection at all, just falling back to that second layer.
_FAIL_OPEN = ScopeDecision(allowed=True, intent=ScopeIntent.CLARIFICATION)

_RESPONSE_FORMAT = to_groq_response_format(ScopeDecision, name="scope_decision")

_SYSTEM_PROMPT = """\
You are a scope guardrail in front of a delivery/transport booking voice \
assistant. Decide only one thing: is the user's message relevant to \
completing or understanding THIS booking -- not whether it merely contains \
a word associated with an unrelated task.

ALLOWED -- pick the closest intent:
  booking_start                 starting or describing a new booking
  pickup_location                stating or asking about the pickup point
  drop_location                  stating or asking about the drop-off point
  vehicle_selection               choosing or asking about a vehicle/truck type
  package_details                 describing what is being sent or moved
  price_estimation                 asking about cost, price, or an estimate
  scheduling                       stating or asking about date or time
  booking_confirmation              confirming, reviewing, or correcting a summary
  booking_modification              changing a detail already given
  cancellation                      cancelling the booking
  delivery_time                     asking how long delivery or pickup will take
  service_information               asking what the service offers or needs
  clarification                     asking what info is still needed, or how to proceed
  small_talk_related_to_booking      a greeting or pleasantry that frames the booking request

A message describing the move itself -- what to send and where, even
naming both the pickup and drop point in one sentence, or phrased as an
instruction ("pick up X from A and deliver it to B") rather than a
statement -- is booking_start or pickup_location/drop_location, not
unrelated. Describing a booking is not the same shape as asking the
assistant to perform some unrelated task; do not let the imperative
phrasing ("pick up...", "deliver...") read as a command to you instead of
as the user's own request.

NOT ALLOWED -- intent "unrelated": general knowledge, maths, code, writing \
tasks, jokes, opinions, personal advice, or any topic that does not help \
complete or understand this booking. A message containing a task-shaped \
word is not automatically unrelated -- judge by whether answering it helps \
THIS booking, not by surface keywords:
  "how much will my delivery cost" -> ALLOWED (price_estimation)
  "calculate 25 times 48" -> NOT ALLOWED (unrelated)
  "what vehicle options do you have" -> ALLOWED (vehicle_selection)
  "what is the capital of India" -> NOT ALLOWED (unrelated)
  "I don't know my exact pickup address, what should I do" -> ALLOWED (clarification)
  "pick up a package from Kakkanad and deliver it to Edappally" -> ALLOWED (booking_start)

Respond with exactly one JSON object: {"allowed": <bool>, "intent": <one \
of the values above>}. No other text.
"""


async def classify(client: AsyncGroq, *, model: str, utterance: str) -> ScopeDecision:
    """Never raises for a retriable failure -- see module docstring for why
    that means `_FAIL_OPEN`, not a block. An auth/permission error is the
    one deliberate exception: it propagates."""
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": utterance},
    ]

    for attempt in range(_MAX_ATTEMPTS):
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                response_format=_RESPONSE_FORMAT,
                reasoning_effort="low",
                temperature=0,
                max_completion_tokens=200,
                timeout=_TIMEOUT_SECONDS,
            )
        except groq.RateLimitError as err:
            wait = retry_after_seconds(err.response)
            if wait is None:
                log.warning("scope guard rate-limited, no short retry-after: %s", err)
                return _FAIL_OPEN
            log.warning("scope guard rate-limited, retrying in %.2fs: %s", wait, err)
            await asyncio.sleep(wait)
            continue
        except _RETRIABLE_ERRORS as err:
            log.warning("scope guard call failed (attempt %d): %s: %s", attempt + 1, type(err).__name__, err)
            continue

        content = response.choices[0].message.content
        try:
            return ScopeDecision.model_validate(json.loads(content))
        except (json.JSONDecodeError, ValueError) as err:
            log.warning("scope guard returned unparseable content: %s", err)
            return _FAIL_OPEN

    log.warning("scope guard retry budget exhausted -- failing open")
    return _FAIL_OPEN
