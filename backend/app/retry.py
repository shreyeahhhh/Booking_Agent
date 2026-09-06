"""Shared rate-limit backoff -- MASTER_PLAN.md step 4.4.

An immediate, no-delay retry cannot help a rate limit: the limit is still active a
few milliseconds later. Confirmed live, not assumed -- services/tts.py's step 3.2
findings hit a real Orpheus tokens-per-day exhaustion that reported "try again in
5h40m0s"; retrying instantly, as every one of this project's three Groq-calling
modules originally did for every retriable error alike, cannot address that at all.

Groq's own SDK already parses the standard `Retry-After` / `Retry-After-Ms` response
headers for its own built-in retry logic (see groq._base_client's
`_parse_retry_after_header`, and its own choice to trust the header only up to 60
seconds before falling back to its own default). This reads the same headers
directly, for the same reason: services/stt.py, services/tts.py and llm/extractor.py
all replace the SDK's generic retry with their own domain-aware "one retry, then a
safe fallback" shape, so the SDK's internal retry (and its handling of this header)
never runs.

`max_wait` defaults to a small fraction of that 60-second SDK default: these calls sit
inside a single voice turn against a ~2-3s *total* latency budget across STT, the LLM
and TTS combined (docs/architecture.md), not a batch job where waiting a couple of
seconds is free. Waiting out a multi-hour daily-quota exhaustion is never correct here
regardless of what the header says -- but neither is silently ignoring a *short*,
genuinely transient throttle a header explicitly named a wait time for. Skipping the
retry entirely and falling through to the existing, already-tested degraded fallback
(None -> re-prompt/speechSynthesis/FALLBACK_RESULT) beats waiting for a duration that
would blow the turn's latency budget on its own.
"""

from __future__ import annotations

import groq

_DEFAULT_MAX_WAIT_SECONDS = 1.0


def retry_after_seconds(
    err: groq.RateLimitError, *, max_wait: float = _DEFAULT_MAX_WAIT_SECONDS
) -> float | None:
    """How long to wait before retrying `err`, or None to skip the retry entirely.

    None covers three cases alike, all with the same answer -- do not retry: the
    response carried neither header, the header did not parse as a number, or the
    wait it named is longer than `max_wait` is willing to spend on a single retry.
    """
    headers = err.response.headers
    seconds = _parse_seconds(headers.get("retry-after-ms"), scale=1 / 1000)
    if seconds is None:
        seconds = _parse_seconds(headers.get("retry-after"), scale=1.0)
    if seconds is None or seconds <= 0 or seconds > max_wait:
        return None
    return seconds


def _parse_seconds(raw: str | None, *, scale: float) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw) * scale
    except ValueError:
        return None
