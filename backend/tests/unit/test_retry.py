"""app.retry -- MASTER_PLAN.md step 4.4.

Pure function over a plain httpx.Response, no network and no mocking beyond
building a real one so `.headers` behaves exactly like a real API response's,
not like a loose Mock that would silently accept any attribute access.
Provider-agnostic on purpose: both services/stt.py (Groq) and
services/tts.py (Cartesia) share this same header-parsing logic for their
own, unrelated 429s.
"""

import httpx

from app.retry import retry_after_seconds


def _response(headers: dict[str, str]) -> httpx.Response:
    request = httpx.Request("POST", "https://example.invalid/x")
    return httpx.Response(429, headers=headers, request=request)


def test_reads_the_plain_retry_after_header_as_seconds():
    response = _response({"retry-after": "1"})
    assert retry_after_seconds(response) == 1.0


def test_retry_after_ms_is_preferred_over_retry_after_and_converted_to_seconds():
    response = _response({"retry-after": "5", "retry-after-ms": "250"})
    assert retry_after_seconds(response) == 0.25


def test_none_when_neither_header_is_present():
    assert retry_after_seconds(_response({})) is None


def test_none_when_the_header_does_not_parse_as_a_number():
    response = _response({"retry-after": "soon"})
    assert retry_after_seconds(response) is None


def test_none_when_the_wait_exceeds_max_wait():
    response = _response({"retry-after": "5"})
    assert retry_after_seconds(response, max_wait=2.0) is None


def test_a_wait_exactly_at_max_wait_is_allowed():
    response = _response({"retry-after": "2"})
    assert retry_after_seconds(response, max_wait=2.0) == 2.0


def test_none_for_a_daily_quota_scale_wait_far_past_max_wait():
    """The shape of a real observed failure (services/tts.py's original Groq
    Orpheus integration, since replaced by Cartesia): a tokens-per-day
    exhaustion reporting a multi-hour wait ("try again in 5h40m0s") in its
    error message. Whether or not a provider also sends that as a
    structured Retry-After header, a value this large must never be
    honoured -- it would make one voice turn hang for hours instead of
    degrading."""
    response = _response({"retry-after": str(5 * 3600 + 40 * 60)})
    assert retry_after_seconds(response) is None


def test_none_when_the_header_is_zero_or_negative():
    assert retry_after_seconds(_response({"retry-after": "0"})) is None
    assert retry_after_seconds(_response({"retry-after": "-1"})) is None
