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

That "Thank you." finding held as long as nothing else influenced decoding.
Once `_LOCALITY_PROMPT` was added (conditioning the decoder toward Kerala/
Karnataka place names, for real speech recognition -- see its own docstring
below), re-probing silence live showed the hallucination widen considerably:
"Pag.", "Thank you so much.", a stray URL, and once several seconds of a
single word looping. The phrase list alone could no longer keep pace, so
`is_noise` now also checks for Whisper's *other* well-documented noise
failure mode -- looping the same word several times running -- which is
structural rather than tied to specific wording and does not need
rediscovering every time the prompt (or the model) changes what silence
hallucinates into. See `is_noise`'s own docstring for the residual gap this
still leaves, and why it is an accepted trade rather than chased further.
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
# from silence/noise, not a general "things Whisper might say" list. The
# "so much"/"very much" variants were observed only after _LOCALITY_PROMPT
# below started conditioning the decoder -- the plain "thank you" hallucination
# drifted into slightly longer relatives of itself under the new prompt.
_SILENCE_HALLUCINATIONS = frozenset({"thank you", "thank you so much", "thank you very much"})

# Whisper's `prompt` parameter conditions the decoder on prior "context" text
# -- a well-documented technique (OpenAI's own Whisper prompting guide) for
# biasing recognition of proper nouns a general-purpose model has little
# training data for, without fine-tuning anything. This project's brief is
# intra-city moves specifically in Kerala and Karnataka, so unusual local
# place names are the normal case, not an edge case -- a name Whisper mishears
# is a booking sent to the wrong address, not a cosmetic transcription slip.
# A representative spread across both states, not an exhaustive gazetteer:
# the prompt conditions the decoder's sense of what *kind* of word to expect
# next, it does not have to (and per Whisper's own ~224-token prompt budget,
# cannot) enumerate every locality this project might ever hear.
_LOCALITY_PROMPT = (
    "Bengaluru localities: Koramangala, Indiranagar, Whitefield, Marathahalli, "
    "HSR Layout, Jayanagar, Electronic City, Yelahanka, Basavanagudi, Hebbal. "
    "Kerala places: Kochi, Ernakulam, Kakkanad, Edappally, Thiruvananthapuram, "
    "Kozhikode, Thrissur, Kollam, Kottayam, Alappuzha, Kaloor, Vyttila, Aluva."
)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# A real answer never legitimately says the same word four times running.
# Verified live: once _LOCALITY_PROMPT started priming the decoder with real
# place names, silence/noise hallucinations stopped being reliably "Thank
# you." and started ranging much more widely -- including, once, several
# seconds of "...Kwonamu, Kosovo, Kwonamu, Kado, Kwonamu, Kwonamu, Kwonamu,
# Kwonamu, Kwonamu, Kwonamu, Kwonamu." This is a second, well-documented
# Whisper failure signature independent of the *specific* words involved
# (looping on noise/silence), so it is checked structurally instead of by
# trying to keep pace with an open-ended, prompt-dependent phrase list.
_MIN_LOOP_REPEATS = 4

# Fragments of _LOCALITY_PROMPT's own text that Whisper has been observed,
# repeatedly and independently, to echo back verbatim when there is no real
# speech to transcribe at all -- not a hallucination of "some place-like
# word" but the prompt's own section-header text ("Kerala places:",
# "Bengaluru localities:") reproduced almost exactly. This is the single
# most dangerous hallucination shape found in this investigation: unlike
# "Pag." or a repeated-word loop, it reads as a well-formed, confident
# location statement to the downstream extractor. Live-fed through the real
# extractor, "Kerala places, Kosovo." came back as PROVIDED patches for
# *both* pickup.locality ("Kerala places") and drop.locality ("Kosovo") at
# confidence 1.0 with no ambiguity flag -- the exact silent-corruption
# outcome the rest of this module exists to prevent (see
# test_stt_live.py::test_digital_silence_never_reaches_conversation_state_as_a_confirmed_fact,
# which caught this live). "kerala places" was observed independently four
# times across two sessions, from both silence and random noise. "bengaluru
# localities" has not been separately observed but is included on the same
# verbatim-prompt-echo mechanism -- the prompt's other, exactly parallel
# list header.
_PROMPT_ECHO_PREFIXES = ("kerala places", "bengaluru localities")


def _has_repeated_word_loop(normalized: str) -> bool:
    words = normalized.split()
    run_length = 1
    for i in range(1, len(words)):
        if words[i] == words[i - 1]:
            run_length += 1
            if run_length >= _MIN_LOOP_REPEATS:
                return True
        else:
            run_length = 1
    return False


def is_noise(text: str) -> bool:
    """True if `text` carries no real user speech.

    Deliberately narrow: nothing here keys off overall length, so a genuine
    short answer like "yes" or "no" is never at risk of being caught by this.
    Four independent checks:

    - Stripped of punctuation, nothing alphanumeric survives. This alone
      catches what random/tonal noise actually came back as in live testing
      (" ", a lone punctuation mark) with no hand-maintained list at all.
    - What remains matches a known Whisper silence hallucination exactly.
    - The transcript starts with a verbatim fragment of _LOCALITY_PROMPT's
      own text (see `_PROMPT_ECHO_PREFIXES`) -- Whisper echoing its own
      conditioning prompt back, the single most dangerous shape found here
      since it reads as a confident, well-formed location statement rather
      than obvious garbage.
    - The same word repeats several times running -- Whisper's other
      well-documented noise/silence failure signature (see
      `_has_repeated_word_loop`), structural rather than a phrase match so
      it does not need to be re-discovered every time the hallucination
      vocabulary shifts.

    Known, accepted residual gap, verified rather than assumed:
    _LOCALITY_PROMPT (below) widened what Whisper hallucinates on
    silence/noise well past what these checks catch -- live samples included
    "If you feel like a" and "Kerenaga, Ejiti, Gopal.", neither matching any
    check here. Confidence signals were considered and rejected too:
    Whisper's `no_speech_prob` is always 0 on this API (see
    architecture.md), and a live comparison of `avg_logprob` across silence
    and real speech showed overlapping ranges, not a usable cutoff -- a
    threshold that catches one hallucination sample rejects genuine speech
    at a similar confidence.

    For what still gets through, it is genuinely the booking agent's OWN
    existing safety net that catches it, not an assumption -- but verify the
    specific claim being relied on, not just its general shape: fed live
    into the real extractor, "Kerenaga, Ejiti, Gopal." landed with low
    confidence and an `ambiguity` reason attached (`vague_location`), the
    same path a genuinely ambiguous real answer takes. That does NOT hold
    for every shape of leaked hallucination, though -- confirmed live the
    hard way: "Kerala places, Kosovo." (before `_PROMPT_ECHO_PREFIXES`
    existed to catch it) was extracted as two separate PROVIDED localities
    at confidence 1.0 with no ambiguity flag at all, i.e. silently recorded
    as fact. So the backstop is real but not total; closing the specific
    shape that defeated it (verbatim prompt-echo) was worth doing directly
    rather than trusting the backstop to cover it. Shorter unrelated
    fragments ("If you feel like a", "Thank you, sir.") were classified
    `off_topic` with no patches at all.
    """
    normalized = _NON_ALNUM.sub(" ", text.lower()).strip()
    normalized = re.sub(r"\s+", " ", normalized)
    if not normalized or normalized in _SILENCE_HALLUCINATIONS:
        return True
    if normalized.startswith(_PROMPT_ECHO_PREFIXES):
        return True
    return _has_repeated_word_loop(normalized)


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
                prompt=_LOCALITY_PROMPT,
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
