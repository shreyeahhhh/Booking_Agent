"""Speech-to-text -- MASTER_PLAN.md step 3.1.

Two independent building blocks, deliberately not fused into one function:

- `transcribe()` is thin I/O: call Groq's Whisper endpoint, return the raw
  text, never raise. Mirrors `llm/extractor.py`'s call/retry shape.
- `is_noise()` is a pure function over a transcript string, with no network
  dependency at all -- it can be unit-tested exhaustively without mocking
  anything, the same way the rest of the deterministic core is.

The `/turn` endpoint (step 3.4) composes them: a noise transcript or a failed
call both mean the same thing to the conversation -- no usable speech, so
skip straight to a fixed re-prompt with zero LLM calls (docs/architecture.md's
"When the LLM is NOT called" table already commits to this).

`is_noise`'s hallucination list is not a guess. Whisper is trained on a huge
corpus of captioned/subtitled video and is widely documented to hallucinate
video-outro phrases when fed silence instead of returning an empty string.
Verified live against this project's exact model (whisper-large-v3-turbo on
Groq, temperature 0): true digital silence, low-amplitude noise, and random/
tonal noise at every duration tried (0.3s-3s) deterministically transcribed
as either "Thank you." or a lone punctuation mark, never an empty string.
See docs/architecture.md's STT section for the full probe results. Extend
the set only from another *observed* transcript, never from a phrase that
merely seems plausible -- that discipline is what keeps this list from
becoming the kind of hand-maintained guesswork this project has deliberately
avoided elsewhere (specs.field_class, prompt_builder's generated reference).
"""

from __future__ import annotations

import logging
import re

import groq
from groq import AsyncGroq

log = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 15
_MAX_ATTEMPTS = 2  # one call + one retry, for transient failures only

# Unlike llm/extractor.py, a 400 here is NOT retried: extractor.py's 400 is a
# specific, observed strict-mode schema violation that a retry can plausibly
# fix (the model tries generating again). There is no schema being enforced
# on Whisper's output, so a 400 here means something structurally wrong with
# the request instead -- verified live, not assumed by analogy: sending
# malformed/empty/wrong-format audio deterministically reproduces the same
# error body every time ("could not process file - is it a valid media
# file?", "file is empty", "file must be one of the following types: [...]",
# "Audio file is too short..."), including on an immediate identical retry.
# See test_stt_live.py::test_malformed_audio_is_a_structural_400_not_a_transient_one.
_RETRIABLE_ERRORS = (
    groq.APIConnectionError,  # also covers APITimeoutError, a subclass
    groq.RateLimitError,
    groq.InternalServerError,
)

# See module docstring: each entry here is a transcript actually observed
# from silence/noise, not a general "things Whisper might say" list.
_SILENCE_HALLUCINATIONS = frozenset({"thank you"})

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def is_noise(text: str) -> bool:
    """True if `text` carries no real user speech.

    Deliberately narrow: nothing here keys off length, so a genuine short
    answer like "yes" or "no" is never at risk of being caught by this --
    only an exact match against known garbage patterns is. Two independent
    checks:

    - Stripped of punctuation, nothing alphanumeric survives. This alone
      catches what random/tonal noise actually came back as in live testing
      (" ", a lone punctuation mark) with no hand-maintained list at all.
    - What remains matches a known Whisper silence hallucination exactly.
    """
    normalized = _NON_ALNUM.sub(" ", text.lower()).strip()
    normalized = re.sub(r"\s+", " ", normalized)
    return not normalized or normalized in _SILENCE_HALLUCINATIONS


async def transcribe(
    client: AsyncGroq,
    *,
    model: str,
    audio: bytes,
    filename: str,
) -> str | None:
    """One utterance of audio -> raw transcript text. Never raises.

    Returns None only when the API call itself could not be completed
    (network/timeout/rate-limit/server error) after the retry budget --
    the caller should treat that the same as a noise transcript, since
    either way there is no usable speech to act on. An empty byte string
    is treated as silence immediately, without spending an API call on
    audio that cannot possibly contain speech.

    Returns the raw text otherwise, including empty strings or
    hallucinated noise -- classifying that is `is_noise`'s job, kept
    separate so it stays testable with no mocking at all.
    """
    if not audio:
        return ""

    last_error: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            response = await client.audio.transcriptions.create(
                model=model,
                file=(filename, audio),
                response_format="json",
                temperature=0,
                timeout=_TIMEOUT_SECONDS,
            )
            return response.text
        except _RETRIABLE_ERRORS as err:
            last_error = err
            log.warning(
                "stt call failed (attempt %d): %s: %s", attempt + 1, type(err).__name__, err
            )

    log.warning("stt retry budget exhausted: %s", last_error)
    return None
