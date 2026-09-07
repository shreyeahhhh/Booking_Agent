"""llm/scope_guard.py's classify() -- the pre-filter in front of the main
extractor. Same mocked-AsyncGroq approach as test_extractor.py; the live
proof that the real model actually classifies correctly is
test_scope_guard_live.py, including the assessment's own required cases.
"""

from unittest.mock import AsyncMock

import groq
import httpx
import pytest

from app.llm.scope_guard import ScopeDecision, ScopeIntent, classify

_ALLOWED = '{"allowed": true, "intent": "price_estimation"}'
_REJECTED = '{"allowed": false, "intent": "unrelated"}'


def _rate_limit_error(headers: dict[str, str]) -> groq.RateLimitError:
    request = httpx.Request("POST", "https://api.groq.com/x")
    response = httpx.Response(429, headers=headers, request=request)
    return groq.RateLimitError("rate limited", response=response, body=None)


def _mock_client(*, side_effects) -> AsyncMock:
    """Mirrors test_extractor.py's identical helper: each entry is either a
    raw content string (success) or an exception instance to raise."""
    client = AsyncMock()

    async def create(**_kwargs):
        effect = side_effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        response = AsyncMock()
        response.choices = [AsyncMock(message=AsyncMock(content=effect))]
        return response

    client.chat.completions.create = create
    return client


async def test_classify_returns_the_parsed_decision_on_success():
    client = _mock_client(side_effects=[_ALLOWED])
    result = await classify(client, model="m", utterance="how much will this cost")
    assert result == ScopeDecision(allowed=True, intent=ScopeIntent.PRICE_ESTIMATION)


async def test_classify_returns_a_rejected_decision_on_success():
    client = _mock_client(side_effects=[_REJECTED])
    result = await classify(client, model="m", utterance="what is 25 times 48")
    assert result == ScopeDecision(allowed=False, intent=ScopeIntent.UNRELATED)


async def test_classify_retries_once_after_a_connection_error_then_succeeds():
    client = _mock_client(side_effects=[groq.APIConnectionError(request=AsyncMock()), _ALLOWED])
    result = await classify(client, model="m", utterance="x")
    assert result.allowed is True


async def test_classify_fails_open_after_the_retry_budget_is_exhausted():
    """Reversed from an earlier fail-closed design: live use showed a
    guard-side outage otherwise blocks *every* turn with the fixed
    redirect, regardless of what was actually said, until it clears --
    worse in practice than letting it through to the now-hardened main
    extractor (extractor.md's own boundary statement, Rule 9)."""
    err = groq.APIConnectionError(request=AsyncMock())
    client = _mock_client(side_effects=[err, err])
    result = await classify(client, model="m", utterance="x")
    assert result.allowed is True


async def test_classify_fails_open_on_malformed_json():
    client = _mock_client(side_effects=["not json{{{"])
    result = await classify(client, model="m", utterance="x")
    assert result.allowed is True


async def test_classify_fails_open_on_a_schema_violation():
    """Valid JSON, wrong shape -- e.g. the model echoed the extractor's own
    schema instead of this one, which pydantic must still reject rather
    than silently coerce."""
    client = _mock_client(side_effects=['{"intent": "provide_info", "patches": []}'])
    result = await classify(client, model="m", utterance="x")
    assert result.allowed is True


async def test_classify_waits_out_a_short_rate_limit_then_succeeds():
    client = _mock_client(side_effects=[_rate_limit_error({"retry-after": "0.01"}), _ALLOWED])
    result = await classify(client, model="m", utterance="x")
    assert result.allowed is True


async def test_classify_fails_open_immediately_on_a_long_rate_limit_wait():
    """No second attempt at all -- only one side_effect is provided, so a
    wrongly-attempted retry would IndexError instead of quietly passing."""
    client = _mock_client(side_effects=[_rate_limit_error({"retry-after": "999"})])
    result = await classify(client, model="m", utterance="x")
    assert result.allowed is True


async def test_classify_does_not_retry_a_bad_request_error_beyond_the_budget():
    """BadRequestError is in scope_guard's retriable set (unlike stt.py's
    exclusion of it) -- a strict-mode schema-violating generation is
    exactly the kind of transient, retry-worthy failure llm/extractor.py
    already treats the same way."""
    err = groq.BadRequestError("bad", response=AsyncMock(status_code=400), body=None)
    client = _mock_client(side_effects=[err, err])
    result = await classify(client, model="m", utterance="x")
    assert result.allowed is True


async def test_classify_propagates_a_non_retriable_error_instead_of_swallowing_it():
    """Same deliberate choice as llm/extractor.py's identical test: an
    invalid API key or permission error is a deployment problem, not a
    per-turn hiccup, and must not be hidden behind failing open or closed
    the way a genuinely transient failure is -- only the explicitly
    retriable errors are caught."""
    client = _mock_client(
        side_effects=[groq.AuthenticationError("bad key", response=AsyncMock(status_code=401), body=None)]
    )
    with pytest.raises(groq.AuthenticationError):
        await classify(client, model="m", utterance="x")
