"""The extractor client: serialization, messages, retry, repair, fallback --
MASTER_PLAN.md step 2.3.

Every test here uses a mocked Groq client (AsyncMock) and makes no network
call -- the live proof that this works against the real API is
test_llm_extractor_live.py / test_extractor_prompt_live.py from steps 2.1/2.2.
This file is about the client's own reliability logic: retry, repair, and
the fallback, which are far cheaper and more precisely testable by
controlling exactly what "Groq" returns than by depending on what a live
model happens to do on any given run.
"""

import json
from unittest.mock import AsyncMock

import groq
import httpx
import pytest

from app.domain.state import (
    BookingState,
    Field,
    FieldStatus,
    GoodsCategory,
    Item,
)
from app.llm.extractor import (
    FALLBACK_RESULT,
    Exchange,
    _build_messages,
    _parse,
    extract,
    serialize_state,
)

_VALID_RESPONSE = json.dumps(
    {
        "intent": "provide_info",
        "patches": [
            {
                "op": "set",
                "field": "pickup.locality",
                "value": "Koramangala",
                "previous_value": None,
                "confidence": 0.95,
                "evidence": "Koramangala",
                "needs_normalization": False,
                "ambiguity": None,
            }
        ],
        "unresolved_mentions": [],
        "suggested_reply": None,
    }
)


def _rate_limit_error(headers: dict[str, str]) -> groq.RateLimitError:
    request = httpx.Request("POST", "https://api.groq.com/x")
    response = httpx.Response(429, headers=headers, request=request)
    return groq.RateLimitError("rate limited", response=response, body=None)


def _mock_client(*, side_effects) -> AsyncMock:
    """A fake AsyncGroq whose chat.completions.create() yields side_effects
    in order -- each entry is either a raw content string (success) or an
    exception instance to raise for that call."""
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


# --- serialize_state ---------------------------------------------------


def test_serialize_empty_state_is_an_empty_dict():
    assert serialize_state(BookingState()) == {}


def test_serialize_state_includes_only_non_empty_scalar_fields():
    state = BookingState()
    state = state.model_copy(
        update={
            "pickup": state.pickup.model_copy(
                update={"locality": Field(value="Koramangala", status=FieldStatus.PROVIDED)}
            )
        }
    )
    view = serialize_state(state)
    assert view == {"pickup.locality": "Koramangala"}


def test_serialize_state_includes_items_and_notes():
    from app.domain.state import Note

    state = BookingState()
    state = state.model_copy(
        update={
            "goods": state.goods.model_copy(update={"items": [Item(name="sofa", quantity=1)]}),
            "notes": [Note(text="fragile")],
        }
    )
    view = serialize_state(state)
    assert view["goods.items"] == [{"name": "sofa", "quantity": 1, "size_hint": None}]
    assert view["notes"] == ["fragile"]


def test_serialize_state_enum_values_are_plain_strings():
    state = BookingState()
    state = state.model_copy(
        update={
            "goods": state.goods.model_copy(
                update={
                    "category": Field(value=GoodsCategory.FURNITURE, status=FieldStatus.PROVIDED)
                }
            )
        }
    )
    view = serialize_state(state)
    assert json.dumps(view) == '{"goods.category": "furniture"}'


# --- message building ----------------------------------------------------


def test_build_messages_has_system_and_user_roles():
    messages = _build_messages(BookingState(), None, [], "hello")
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "hello" in messages[1]["content"]


def test_build_messages_with_no_turns_says_none_yet():
    messages = _build_messages(BookingState(), None, [], "hello")
    assert "(none yet)" in messages[1]["content"]


def test_build_messages_includes_recent_turns_and_last_question():
    turns = [Exchange("user", "hi"), Exchange("agent", "where from?")]
    messages = _build_messages(BookingState(), "where from?", turns, "Koramangala")
    content = messages[1]["content"]
    assert "user: hi" in content
    assert "agent: where from?" in content
    assert "where from?" in content  # LAST_QUESTION


# --- parsing ---------------------------------------------------------------


def test_parse_valid_response():
    result, error = _parse(_VALID_RESPONSE)
    assert error is None
    assert result.patches[0].field == "pickup.locality"


def test_parse_invalid_json_returns_an_error_not_an_exception():
    result, error = _parse("not json{{{")
    assert result is None
    assert "JSON" in error


def test_parse_schema_violation_returns_an_error_not_an_exception():
    result, error = _parse(json.dumps({"intent": "not_a_real_intent", "patches": []}))
    assert result is None
    assert error is not None


# --- extract(): retry, repair, fallback ------------------------------------


async def test_extract_succeeds_on_the_first_attempt():
    client = _mock_client(side_effects=[_VALID_RESPONSE])
    result = await extract(
        client, model="m", state=BookingState(), last_question=None, recent_turns=[], utterance="x"
    )
    assert result.patches[0].field == "pickup.locality"


async def test_extract_retries_once_after_a_connection_error_then_succeeds():
    client = _mock_client(
        side_effects=[groq.APIConnectionError(request=AsyncMock()), _VALID_RESPONSE]
    )
    result = await extract(
        client, model="m", state=BookingState(), last_question=None, recent_turns=[], utterance="x"
    )
    assert result.patches[0].field == "pickup.locality"


async def test_extract_falls_back_after_the_retry_budget_is_exhausted():
    err = groq.APIConnectionError(request=AsyncMock())
    client = _mock_client(side_effects=[err, err])
    result = await extract(
        client, model="m", state=BookingState(), last_question=None, recent_turns=[], utterance="x"
    )
    assert result == FALLBACK_RESULT


async def test_extract_treats_a_bad_request_error_as_retriable():
    """The observed live failure mode: strict mode rejects a malformed
    generation with a 400 before any content is ever returned."""
    err = groq.BadRequestError(
        "invalid",
        response=AsyncMock(status_code=400),
        body={"error": {"code": "json_validate_failed"}},
    )
    client = _mock_client(side_effects=[err, _VALID_RESPONSE])
    result = await extract(
        client, model="m", state=BookingState(), last_question=None, recent_turns=[], utterance="x"
    )
    assert result.patches[0].field == "pickup.locality"


async def test_extract_repairs_a_response_that_fails_to_parse():
    client = _mock_client(side_effects=["not json{{{", _VALID_RESPONSE])
    result = await extract(
        client, model="m", state=BookingState(), last_question=None, recent_turns=[], utterance="x"
    )
    assert result.patches[0].field == "pickup.locality"


async def test_extract_falls_back_if_the_repair_pass_also_fails_to_parse():
    client = _mock_client(side_effects=["not json{{{", "still not json"])
    result = await extract(
        client, model="m", state=BookingState(), last_question=None, recent_turns=[], utterance="x"
    )
    assert result == FALLBACK_RESULT


async def test_a_non_retriable_error_propagates_instead_of_being_swallowed():
    """Deliberate: an invalid API key or permission error is a deployment
    problem, not a per-turn hiccup. Swallowing it into the generic fallback
    would make every turn look like a speech-recognition failure and hide
    the real, fixable cause. Only the explicitly retriable errors are caught."""
    client = _mock_client(
        side_effects=[
            groq.AuthenticationError("bad key", response=AsyncMock(status_code=401), body=None)
        ]
    )
    with pytest.raises(groq.AuthenticationError):
        await extract(
            client,
            model="m",
            state=BookingState(),
            last_question=None,
            recent_turns=[],
            utterance="x",
        )


async def test_extract_waits_out_a_short_rate_limit_then_succeeds():
    client = _mock_client(
        side_effects=[_rate_limit_error({"retry-after": "0.01"}), _VALID_RESPONSE]
    )
    result = await extract(
        client, model="m", state=BookingState(), last_question=None, recent_turns=[], utterance="x"
    )
    assert result.patches[0].field == "pickup.locality"


async def test_extract_falls_back_immediately_on_a_long_rate_limit_wait():
    """No second attempt at all -- only one side_effect is provided, so a
    wrongly-attempted retry would IndexError instead of quietly passing."""
    client = _mock_client(side_effects=[_rate_limit_error({"retry-after": "999"})])
    result = await extract(
        client, model="m", state=BookingState(), last_question=None, recent_turns=[], utterance="x"
    )
    assert result == FALLBACK_RESULT


async def test_extract_falls_back_when_the_repair_pass_itself_is_rate_limited():
    """The repair pass (after a malformed first response) gets no extra
    retry-after wait budget of its own -- a rate limit there is treated the
    same as any other repair-pass failure: the safe fallback, not a raise."""
    client = _mock_client(side_effects=["not json{{{", _rate_limit_error({"retry-after": "0.01"})])
    result = await extract(
        client, model="m", state=BookingState(), last_question=None, recent_turns=[], utterance="x"
    )
    assert result == FALLBACK_RESULT
