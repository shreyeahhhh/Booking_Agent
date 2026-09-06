"""The /turn pipeline's core logic -- MASTER_PLAN.md step 3.4.

`_process_turn` is private to api/routes.py but tested directly here, the
same convention test_reducer.py already uses for domain/reducer.py's own
private helpers: it is the actual orchestration logic (STT -> fastpath/
extract -> reduce -> policy -> template -> TTS), and asserting on it
directly is far more precise than only exercising it through a real HTTP
request -- see test_api_turn.py for the complementary "does the endpoint
actually wire this up" check.

One mocked AsyncGroq stands in for all three Groq APIs a single turn can
touch (transcription, chat completion, speech), with call counters so a
test can assert not just "the right output" but "the right APIs were even
called" -- e.g. that a noise transcript truly never reaches the LLM.

Every test gets its own `tts_cache_dir` (pytest's `tmp_path`), never the
real configured one: services/tts.py caches to disk by text hash, and two
tests that happen to produce the same response text would otherwise share
a cache entry across test runs, silently skipping the very API call a test
is trying to observe. That is not a hypothetical -- it is exactly what
happened the first time this file ran without this fixture, and it read as
a passing test for the wrong reason until an intentionally-failing TTS
mock exposed it (the cached bytes from an earlier test were served instead
of the simulated failure ever being reached).
"""

import json
from unittest.mock import AsyncMock

import groq
import pytest

from app.api.routes import _noise_reprompt, _process_turn
from app.config import get_settings
from app.conversation import templates
from app.conversation.machine import ConversationState, Phase, start
from app.domain.policy import SlotDecision, SlotReason
from app.domain.specs import get_field
from app.session.store import Session

_VALID_LOCALITY_RESPONSE = json.dumps(
    {
        "intent": "provide_info",
        "patches": [
            {
                "op": "set",
                "field": "pickup.locality",
                "value": "Koramangala",
                "evidence": "Koramangala",
                "confidence": 0.95,
            }
        ],
        "unresolved_mentions": [],
        "suggested_reply": None,
    }
)


@pytest.fixture
def settings(tmp_path):
    # Settings is a pydantic model, not a stdlib dataclass -- model_copy,
    # not dataclasses.replace, is the correct way to override one field.
    return get_settings().model_copy(update={"tts_cache_dir": tmp_path})


def _mock_client(*, stt_text=None, llm_content=None, tts_bytes=b"wav-bytes"):
    calls = {"stt": 0, "llm": 0, "tts": 0}
    client = AsyncMock()

    async def transcribe(**_kwargs):
        calls["stt"] += 1
        return AsyncMock(text=stt_text)

    async def chat_create(**_kwargs):
        calls["llm"] += 1
        response = AsyncMock()
        response.choices = [AsyncMock(message=AsyncMock(content=llm_content))]
        return response

    async def speech_create(**_kwargs):
        calls["tts"] += 1
        response = AsyncMock()
        response.read = AsyncMock(return_value=tts_bytes)
        return response

    client.audio.transcriptions.create = transcribe
    client.chat.completions.create = chat_create
    client.audio.speech.create = speech_create
    client.calls = calls
    return client


def _session(*, phase=Phase.GATHERING, decision=None, last_question=None) -> Session:
    conversation = ConversationState(booking=start().booking, phase=phase)
    return Session(conversation=conversation, decision=decision, last_question=last_question)


# --- empty / noise: no LLM call at all ------------------------------------


async def test_empty_audio_produces_a_reprompt_with_no_api_calls_at_all(settings):
    client = _mock_client()
    session = _session(last_question="What floor is the pickup on?")
    outcome = await _process_turn(client, settings, session, b"", "audio.webm")
    assert outcome.agent_text == "Sorry, I didn't catch that. What floor is the pickup on?"
    assert client.calls == {"stt": 0, "llm": 0, "tts": 1}  # the re-prompt itself is still spoken


async def test_a_noise_hallucination_produces_a_reprompt_with_no_llm_call(settings):
    client = _mock_client(stt_text=" Thank you.")
    session = _session(last_question="Which city are you moving to?")
    outcome = await _process_turn(client, settings, session, b"fake-audio", "audio.webm")
    assert outcome.agent_text == "Sorry, I didn't catch that. Which city are you moving to?"
    assert client.calls["llm"] == 0
    assert outcome.session is session  # nothing about the conversation changed


def test_noise_reprompt_with_no_prior_question_is_just_the_apology():
    assert _noise_reprompt(None) == "Sorry, I didn't catch that."


# --- meta-commands: no LLM call, no state change (repeat) -----------------


async def test_repeat_replays_the_last_question_verbatim_with_no_llm_call(settings):
    client = _mock_client(stt_text="repeat that")
    decision = SlotDecision("pickup.floor", "pickup floor", SlotReason.MISSING)
    session = _session(last_question="Which floor is the pickup on?", decision=decision)
    outcome = await _process_turn(client, settings, session, b"fake-audio", "audio.webm")
    assert outcome.agent_text == "Which floor is the pickup on?"
    assert client.calls["llm"] == 0
    assert outcome.session is session


async def test_restart_resets_the_conversation_to_the_greeting(settings):
    client = _mock_client(stt_text="start over")
    mid_booking = _session(phase=Phase.REVIEW, last_question="Does this all look right?")
    outcome = await _process_turn(client, settings, mid_booking, b"fake-audio", "audio.webm")
    assert outcome.agent_text == templates.GREETING
    assert outcome.session.conversation.phase == Phase.GREETING
    assert get_field(outcome.session.conversation.booking, "pickup.locality").value is None
    assert client.calls["llm"] == 0


# --- fast-path hit: no LLM call --------------------------------------------


async def test_a_fast_path_hit_answers_without_calling_the_llm(settings):
    client = _mock_client(stt_text="yes")
    decision = SlotDecision("pickup.has_lift", "lift at pickup", SlotReason.MISSING)
    session = _session(last_question="Is there a lift?", decision=decision)
    outcome = await _process_turn(client, settings, session, b"fake-audio", "audio.webm")
    assert get_field(outcome.session.conversation.booking, "pickup.has_lift").value is True
    assert client.calls["llm"] == 0
    assert client.calls["stt"] == 1
    assert client.calls["tts"] == 1


# --- fast-path miss: falls through to the real extractor -------------------


async def test_a_fast_path_miss_falls_through_to_the_llm(settings):
    client = _mock_client(stt_text="Koramangala", llm_content=_VALID_LOCALITY_RESPONSE)
    decision = SlotDecision("pickup.locality", "pickup location", SlotReason.MISSING)
    session = _session(last_question="Where are you moving from?", decision=decision)
    outcome = await _process_turn(client, settings, session, b"fake-audio", "audio.webm")
    booking = outcome.session.conversation.booking
    assert get_field(booking, "pickup.locality").value == "Koramangala"
    assert client.calls["llm"] == 1


async def test_recent_turns_accumulate_after_a_real_llm_turn(settings):
    client = _mock_client(stt_text="Koramangala", llm_content=_VALID_LOCALITY_RESPONSE)
    decision = SlotDecision("pickup.locality", "pickup location", SlotReason.MISSING)
    session = _session(last_question="Where are you moving from?", decision=decision)
    outcome = await _process_turn(client, settings, session, b"fake-audio", "audio.webm")
    assert len(outcome.session.recent_turns) == 2
    assert outcome.session.recent_turns[0].text == "Koramangala"
    assert outcome.session.decision is not None  # something is now pending next


# --- TTS unavailable: agent_text still returned, audio_chunks is None ------


async def test_tts_failure_still_returns_the_agent_text_with_no_audio(settings):
    client = _mock_client(stt_text="yes")
    client.audio.speech.create = AsyncMock(side_effect=groq.APIConnectionError(request=AsyncMock()))
    decision = SlotDecision("pickup.has_lift", "lift at pickup", SlotReason.MISSING)
    session = _session(last_question="Is there a lift?", decision=decision)
    outcome = await _process_turn(client, settings, session, b"fake-audio", "audio.webm")
    assert outcome.audio_chunks is None
    assert outcome.agent_text  # still a real response, just unspoken
