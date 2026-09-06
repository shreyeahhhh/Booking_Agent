"""Text-to-speech and its audio cache -- MASTER_PLAN.md step 3.2, moved to
Cartesia in a later pass.

Three pieces, same separation of concerns as services/stt.py:

- `chunk_text()` is pure: split a response into pieces no longer than
  `_MAX_CHARS`, packing whole sentences greedily rather than emitting one
  chunk per sentence. Fully unit-testable with no mocking. Provider-agnostic,
  unchanged since this module's original Groq/Orpheus implementation.
- `_synthesize_chunk()` is thin I/O: one chunk of text -> WAV audio bytes,
  same timeout/retry/never-raise-for-transient-failures shape as
  extractor.py and stt.py, calling Cartesia's REST API directly via
  `httpx.AsyncClient` rather than a vendor SDK (Cartesia's own Python SDK
  exists but is not a dependency here -- httpx is already pulled in
  transitively by `groq`, and the wire contract is a single, simple JSON
  POST with no benefit to wrapping it further for this project's needs).
- `synthesize()` composes the two with an on-disk cache keyed by a hash of
  (model, voice, text): architecture.md's "Pre-synthesised template audio
  cache" -- because agent responses are built from a small set of fixed
  phrases (see conversation/templates.py), a repeated phrase is served from
  disk with zero API calls after the first time it is ever spoken.

Cartesia, not Groq Orpheus: Orpheus's free tier is 3600 tokens/*day*, and live
testing during this project's own development exhausted it more than once from
ordinary use, well before any real evaluator ever saw the deploy -- a genuine
risk for a submission whose entire point is a human testing the live link. See
MASTER_PLAN.md for the full comparison and the reasoning behind the switch.
`client` here is a plain `httpx.AsyncClient`, not the `AsyncGroq` client
`services/stt.py`/`llm/extractor.py` still use -- STT and the LLM stayed on
Groq (confirmed live to not share Orpheus's fragile daily cap; see
MASTER_PLAN.md), so this module now depends on a second, independent
credential (`Settings.cartesia_api_key`) that is allowed to be entirely
absent without blocking a turn -- see `Settings.cartesia_is_configured` and
`synthesize()` below, which treat "not configured" exactly like "the API
call failed": skip straight to the caller's existing None -> speechSynthesis
fallback, never a hard error.

`_MAX_CHARS = 200` is a *self-imposed* chunk size (Cartesia has no comparable
per-request character limit either, per its own docs), kept for the two
reasons that were always independent of Orpheus specifically:
architecture.md's own latency mitigation ("stream TTS, play the first chunk
immediately"), and spending any provider's finite free-tier budget in small,
cacheable, reusable pieces rather than large ones.

Returns a *list* of WAV byte-strings, one per chunk, deliberately not
concatenated into one file: WAV's header encodes a length, so naively gluing
multiple WAV files together produces a malformed file. Cartesia's own WAV
output uses the streaming convention of an unknown-length sentinel
(0xFFFFFFFF) in both the RIFF and data chunk size fields rather than the
real byte count -- confirmed live before trusting it, not assumed: a saved
response loaded and played correctly in a real browser `<audio>` element
(the exact mechanism frontend/src/audio.ts uses), reaching `ended` with a
correct, non-infinite `duration` despite the sentinel header.

A `None` return (from `synthesize()` or `_synthesize_chunk()`) means the
same thing services/stt.py's `None` does: TTS is unavailable right now, and
the caller (the /turn endpoint, step 3.4) should fall back to the browser's
`speechSynthesis` API rather than play nothing.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from pathlib import Path

import httpx

from app.retry import retry_after_seconds

log = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 15
_MAX_ATTEMPTS = 2  # one call + one retry, for transient failures only
_MAX_CHARS = 200  # self-imposed chunk size -- see module docstring

_API_VERSION = "2026-08-14"  # required header; confirmed live, the only version offered
_OUTPUT_FORMAT = {"container": "wav", "encoding": "pcm_s16le", "sample_rate": 44100}

# A 4xx other than 429 (bad API key, an unknown voice/model id, a malformed
# request) is structural -- the same "retrying an identical request cannot
# help" reasoning services/stt.py already applies to its own 400s -- so it is
# deliberately left to propagate rather than retried or swallowed. 429 and 5xx
# are handled separately below: a 429 only retries when Cartesia names a short
# enough wait (app.retry), and a 5xx gets one blind retry as a plain transient
# fault, the same budget every other Groq-calling module in this project uses.
_RETRIABLE_STATUS = frozenset({429, 500, 502, 503, 504})

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


def _cache_key(text: str, *, model: str, voice_id: str) -> str:
    digest_input = f"{model}\x1f{voice_id}\x1f{text}".encode()
    return hashlib.sha256(digest_input).hexdigest()


def _cache_path(cache_dir: Path, key: str) -> Path:
    return cache_dir / f"{key}.wav"


async def _synthesize_chunk(
    client: httpx.AsyncClient,
    *,
    model: str,
    voice_id: str,
    text: str,
) -> bytes | None:
    """One chunk of text (<= _MAX_CHARS) -> WAV audio bytes. Never raises
    for a retriable failure; see module docstring for why a structural 4xx
    propagates instead of being retried or swallowed."""
    last_error: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            response = await client.post(
                "/tts/bytes",
                json={
                    "model_id": model,
                    "transcript": text,
                    "voice": voice_id,
                    "language": "en",
                    "output_format": _OUTPUT_FORMAT,
                },
                timeout=_TIMEOUT_SECONDS,
            )
            if response.status_code == 429:
                last_error = httpx.HTTPStatusError(
                    "rate limited", request=response.request, response=response
                )
                wait = retry_after_seconds(response)
                if wait is None:
                    log.warning(
                        "tts call rate-limited (attempt %d), no short retry-after", attempt + 1
                    )
                    break
                log.warning(
                    "tts call rate-limited (attempt %d), retrying in %.2fs", attempt + 1, wait
                )
                await asyncio.sleep(wait)
                continue
            response.raise_for_status()
            return response.content
        except httpx.HTTPStatusError as err:
            if err.response.status_code not in _RETRIABLE_STATUS:
                raise  # structural 4xx: bad key, bad voice/model id, malformed request
            last_error = err
            log.warning(
                "tts call failed (attempt %d): HTTP %d: %s",
                attempt + 1,
                err.response.status_code,
                err,
            )
        except (httpx.ConnectError, httpx.TimeoutException) as err:
            last_error = err
            log.warning(
                "tts call failed (attempt %d): %s: %s", attempt + 1, type(err).__name__, err
            )
    log.warning("tts retry budget exhausted: %s", last_error)
    return None


async def _get_or_synthesize_chunk(
    client: httpx.AsyncClient,
    *,
    model: str,
    voice_id: str,
    text: str,
    cache_dir: Path,
) -> bytes | None:
    path = _cache_path(cache_dir, _cache_key(text, model=model, voice_id=voice_id))
    if path.exists():
        return path.read_bytes()
    audio = await _synthesize_chunk(client, model=model, voice_id=voice_id, text=text)
    if audio is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(audio)
    return audio


async def synthesize(
    client: httpx.AsyncClient | None,
    *,
    model: str,
    voice_id: str,
    text: str,
    cache_dir: Path,
) -> list[bytes] | None:
    """One agent response -> a list of playable WAV chunks, in order.

    `client` is None exactly when `Settings.cartesia_is_configured` is
    False -- treated identically to any other TTS failure (returns None
    immediately, no attempt made) rather than raising, so a deploy missing
    only the optional Cartesia key still speaks via the browser fallback
    instead of erroring.

    Each chunk is cached independently, keyed by its own exact text, so a
    repeated fixed phrase across turns or sessions costs one API call ever,
    not one per occurrence. Chunks are synthesised *concurrently*, not one
    at a time -- a multi-chunk response (the final booking summary readback
    is the common case actually long enough to span more than one chunk)
    otherwise pays the full per-chunk network latency once per chunk instead
    of once total. Returns None if any chunk could not be synthesised -- the
    caller should fall back to the browser's speechSynthesis for the whole
    response rather than play a partial one.
    """
    if client is None:
        return None
    chunks = chunk_text(text)
    if not chunks:
        return []
    results = await asyncio.gather(
        *(
            _get_or_synthesize_chunk(
                client, model=model, voice_id=voice_id, text=chunk, cache_dir=cache_dir
            )
            for chunk in chunks
        )
    )
    if any(audio is None for audio in results):
        return None
    return list(results)
