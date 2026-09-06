"""Text-to-speech and its audio cache -- MASTER_PLAN.md step 3.2.

Three pieces, same separation of concerns as services/stt.py:

- `chunk_text()` is pure: split a response into pieces no longer than
  `_MAX_CHARS`, packing whole sentences greedily rather than emitting one
  chunk per sentence. Fully unit-testable with no mocking.
- `_synthesize_chunk()` is thin I/O: one chunk of text -> WAV bytes, same
  timeout/retry/never-raise-for-transient-failures shape as extractor.py
  and stt.py.
- `synthesize()` composes the two with an on-disk cache keyed by a hash of
  (model, voice, text): architecture.md's "Pre-synthesised template audio
  cache" -- because agent responses are built from a small set of fixed
  phrases (see conversation/templates.py), a repeated phrase is served from
  disk with zero API calls after the first time it is ever spoken.

`_MAX_CHARS = 200` is a *self-imposed* chunk size, not an API-enforced one --
that correction itself came from live testing, worth recording because it
reversed an earlier belief documented in MASTER_PLAN.md. Groq's own Orpheus
doc page states "max 200 characters," and a first pass here took that as a
hard validation limit. A live sweep once the account's model-terms block was
cleared showed the API happily accepts 1000+ characters with no length
complaint at all -- the wall that eventually appears is the account's daily
token budget (`RateLimitError`, "tokens per day (TPD)"), not a per-request
size check. Chunking is still the right call for two *different*, still-valid
reasons: architecture.md's own latency mitigation ("stream TTS, play the
first chunk immediately"), and this project's free tier has a materially
tight TPD budget (see MASTER_PLAN.md's risk register) -- small, cacheable,
reusable chunks spend that budget more slowly than large ones would.

Verified live before writing this, not assumed: an early web search on the
character question turned up a conflicting "10K characters" claim from a
stale/unrelated page, and the terms-acceptance block (below) took an actual
live call to discover -- neither was visible from reading documentation
alone. This project's Groq org had not accepted Orpheus's model terms
either, so every call initially returned a 400 with code
`model_terms_required`; an org admin visiting
https://console.groq.com/playground?model=canopylabs%2Forpheus-v1-english
resolved it. That 400 was structural, not transient -- six different
requests (different lengths, different formats, an invalid voice) all
produced the identical error, the same "retrying cannot help" signature
services/stt.py already established for a 400 on an audio endpoint.
test_tts_live.py detects and skips on that error specifically, so a
not-yet-accepted account fails clearly rather than confusingly.

Returns a *list* of WAV byte-strings, one per chunk, deliberately not
concatenated into one file: WAV's header encodes a single length, so naively
gluing multiple complete WAV files together produces a malformed file. This
also matches architecture.md's own latency mitigation -- "stream TTS, play
the first chunk immediately" -- rather than fighting it: the caller can start
playing chunk 0 while chunk 1 is still being synthesised.

A `None` return (from `synthesize()` or `_synthesize_chunk()`) means the
same thing services/stt.py's `None` does: TTS is unavailable right now, and
the caller (the /turn endpoint, step 3.4) should fall back to the browser's
`speechSynthesis` API rather than play nothing. That fallback decision itself
belongs to the endpoint, not this module -- there is nothing left for a
dedicated "flag" type to carry beyond the None this module already returns.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

import groq
from groq import AsyncGroq

log = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 15
_MAX_ATTEMPTS = 2  # one call + one retry, for transient failures only
_MAX_CHARS = 200  # self-imposed chunk size, not an API-enforced limit -- see module docstring
_RESPONSE_FORMAT = "wav"  # the only format Orpheus supports (other Groq TTS
# models accept flac/mp3/mulaw/ogg too; Orpheus's own docs page does not)

# Same reasoning as services/stt.py: a 400 here (bad voice, terms not yet
# accepted) is structural, not transient -- verified live, see the module
# docstring -- so it is deliberately excluded and left to propagate rather
# than retried or swallowed into a None fallback.
#
# RateLimitError is retried here on the same "might be transient" assumption
# extractor.py and stt.py already make, but a live TPD (tokens-per-day)
# exhaustion during this step's own testing showed that assumption does not
# hold for every rate-limit cause: the error said "try again in 5h40m0s",
# which one immediate, no-backoff retry cannot help with at all. Left as-is
# rather than fixed piecemeal here -- distinguishing a short-lived rate
# limit from a daily-quota one (the error body names which) is a real
# improvement, but a cross-cutting one that belongs to phase 4.4's "every
# external call has a timeout, a retry and a degraded fallback" pass across
# all three modules at once, not a one-off change to just this one.
_RETRIABLE_ERRORS = (
    groq.APIConnectionError,  # also covers APITimeoutError, a subclass
    groq.RateLimitError,
    groq.InternalServerError,
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def chunk_text(text: str, max_chars: int = _MAX_CHARS) -> list[str]:
    """Split `text` into pieces that each fit within `max_chars`.

    Packs whole sentences greedily into a chunk rather than one chunk per
    sentence: most composed responses (conversation/templates.py) are well
    under the limit on their own, and a separate API call per short
    sentence would multiply latency and cost for no benefit. A single
    sentence longer than `max_chars` (not expected from this project's own
    templates, but never guaranteed for arbitrary text) falls back to a
    word-boundary split so it is never sent to the API oversized.
    """
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(text.strip()) if s.strip()]
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        current = sentence if len(sentence) <= max_chars else ""
        if not current:
            chunks.extend(_hard_split(sentence, max_chars))
    if current:
        chunks.append(current)
    return chunks


def _hard_split(text: str, max_chars: int) -> list[str]:
    """Word-boundary fallback for a single sentence longer than max_chars."""
    chunks: list[str] = []
    current = ""
    for word in text.split(" "):
        candidate = f"{current} {word}".strip() if current else word
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        current = word[:max_chars]  # a single word longer than the limit: last resort
    if current:
        chunks.append(current)
    return chunks


def _cache_key(text: str, *, model: str, voice: str) -> str:
    digest_input = f"{model}\x1f{voice}\x1f{text}".encode()
    return hashlib.sha256(digest_input).hexdigest()


def _cache_path(cache_dir: Path, key: str) -> Path:
    return cache_dir / f"{key}.wav"


async def _synthesize_chunk(
    client: AsyncGroq,
    *,
    model: str,
    voice: str,
    text: str,
) -> bytes | None:
    """One chunk of text (<= _MAX_CHARS) -> WAV audio bytes. Never raises
    for a retriable failure; see module docstring for why a 400 propagates
    instead of being retried or swallowed."""
    last_error: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            response = await client.audio.speech.create(
                model=model,
                voice=voice,
                input=text,
                response_format=_RESPONSE_FORMAT,
                timeout=_TIMEOUT_SECONDS,
            )
            return await response.read()
        except _RETRIABLE_ERRORS as err:
            last_error = err
            log.warning(
                "tts call failed (attempt %d): %s: %s", attempt + 1, type(err).__name__, err
            )
    log.warning("tts retry budget exhausted: %s", last_error)
    return None


async def _get_or_synthesize_chunk(
    client: AsyncGroq,
    *,
    model: str,
    voice: str,
    text: str,
    cache_dir: Path,
) -> bytes | None:
    path = _cache_path(cache_dir, _cache_key(text, model=model, voice=voice))
    if path.exists():
        return path.read_bytes()
    audio = await _synthesize_chunk(client, model=model, voice=voice, text=text)
    if audio is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(audio)
    return audio


async def synthesize(
    client: AsyncGroq,
    *,
    model: str,
    voice: str,
    text: str,
    cache_dir: Path,
) -> list[bytes] | None:
    """One agent response -> a list of playable WAV chunks, in order.

    Each chunk is cached independently, keyed by its own exact text, so a
    repeated fixed phrase across turns or sessions costs one API call ever,
    not one per occurrence. Returns None if any chunk could not be
    synthesised -- the caller should fall back to the browser's
    speechSynthesis for the whole response rather than play a partial one.
    """
    chunks = chunk_text(text)
    audio_chunks: list[bytes] = []
    for chunk in chunks:
        audio = await _get_or_synthesize_chunk(
            client, model=model, voice=voice, text=chunk, cache_dir=cache_dir
        )
        if audio is None:
            return None
        audio_chunks.append(audio)
    return audio_chunks
