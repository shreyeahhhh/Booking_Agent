"""The STT service: noise classification and the transcribe client --
MASTER_PLAN.md step 3.1.

`is_noise` is pure and gets exhaustive coverage here with no mocking at all.
`transcribe`'s retry/fallback logic uses a mocked AsyncGroq, the same
approach as test_extractor.py -- the live proof that Groq's audio endpoint
and the silence-hallucination assumptions actually hold is test_stt_live.py.
"""

from unittest.mock import AsyncMock

import groq
import httpx
import pytest

from app.services.stt import _PROMPT_ECHO_PREFIXES, _has_repeated_word_loop, is_noise, transcribe


def _rate_limit_error(headers: dict[str, str]) -> groq.RateLimitError:
    request = httpx.Request("POST", "https://api.groq.com/x")
    response = httpx.Response(429, headers=headers, request=request)
    return groq.RateLimitError("rate limited", response=response, body=None)


# --- is_noise ---------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        ".",
        " .",
        "...",
        "Thank you.",
        "thank you",
        "  THANK YOU  ",
        "Thank you!",
        "Thank you so much.",  # observed only after _LOCALITY_PROMPT was added
        "Thank you very much.",
        "Hentai, Bengaluru localities and Hikana, Kompanya, Bengaluru localities, "
        "Kajana, Utsuwa, Kwonamu, Kosovo, Kwonamu, Kado, Kwonamu, Kwonamu, Kwonamu, "
        "Kwonamu, Kwonamu, Kwonamu, Kwonamu.",  # exact repetition-loop hallucination, observed live
    ],
)
def test_is_noise_true_for_empty_and_observed_hallucinations(text):
    assert is_noise(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "word word word word",  # 4 identical words running
        "one two three three three three four",  # a loop embedded mid-sentence
    ],
)
def test_is_noise_true_for_a_repeated_word_loop(text):
    assert is_noise(text) is True


def test_has_repeated_word_loop_requires_four_in_a_row_not_just_four_total():
    assert _has_repeated_word_loop("kwonamu kosovo kwonamu kado kwonamu") is False
    assert _has_repeated_word_loop("kwonamu kwonamu kwonamu kwonamu") is True


@pytest.mark.parametrize(
    "text",
    [
        "Kerala places.",  # observed live from silence, repeatedly
        "Kerala places, Kosovo.",  # observed live -- extracted as 2 fake localities before this fix
        "kerala places",
        "  KERALA PLACES, Koko.  ",
        "Bengaluru localities, foo bar.",  # same mechanism, not yet independently observed live
    ],
)
def test_is_noise_true_for_a_verbatim_prompt_echo(text):
    assert is_noise(text) is True


def test_prompt_echo_prefixes_are_lowercase_since_is_noise_normalizes_to_lowercase():
    assert all(prefix == prefix.lower() for prefix in _PROMPT_ECHO_PREFIXES)


@pytest.mark.parametrize(
    "text",
    [
        "yes",
        "no",
        "third floor",
        "2",
        "Koramangala",
        "thank you for helping me move the sofa",  # real sentence containing the phrase
        "no thank you",
        "Kerala is where I'm moving from",  # mentions the word but isn't the echoed label
        "I live near Bengaluru, close to the airport",
        "I'm moving from Kerala places, my hometown",  # label mid-sentence, not a leading echo
    ],
)
def test_is_noise_false_for_real_answers(text):
    assert is_noise(text) is False


# --- transcribe ---------------------------------------------------------


def _mock_client(*, side_effects) -> AsyncMock:
    client = AsyncMock()

    async def create(**_kwargs):
        effect = side_effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return AsyncMock(text=effect)

    client.audio.transcriptions.create = create
    return client


async def test_transcribe_returns_text_on_success():
    client = _mock_client(side_effects=["third floor"])
    result = await transcribe(client, model="m", audio=b"fake-audio-bytes", filename="a.webm")
    assert result == "third floor"


async def test_transcribe_empty_audio_short_circuits_without_calling_the_api():
    client = _mock_client(side_effects=[])  # any call would raise IndexError
    result = await transcribe(client, model="m", audio=b"", filename="a.webm")
    assert result == ""


async def test_transcribe_retries_once_after_a_connection_error_then_succeeds():
    client = _mock_client(
        side_effects=[groq.APIConnectionError(request=AsyncMock()), "third floor"]
    )
    result = await transcribe(client, model="m", audio=b"x", filename="a.webm")
    assert result == "third floor"


async def test_transcribe_returns_none_after_the_retry_budget_is_exhausted():
    err = groq.APIConnectionError(request=AsyncMock())
    client = _mock_client(side_effects=[err, err])
    result = await transcribe(client, model="m", audio=b"x", filename="a.webm")
    assert result is None


async def test_transcribe_does_not_retry_a_bad_request_error():
    """Deliberate difference from llm/extractor.py: there is no schema being
    enforced on Whisper output, so a 400 here means something structurally
    wrong with the request that an identical retry cannot fix -- it should
    propagate immediately, not be swallowed into a retry or a None fallback."""
    err = groq.BadRequestError("bad audio", response=AsyncMock(status_code=400), body=None)
    client = _mock_client(side_effects=[err])
    with pytest.raises(groq.BadRequestError):
        await transcribe(client, model="m", audio=b"x", filename="a.webm")


async def test_a_non_retriable_error_propagates_instead_of_being_swallowed():
    """Same reasoning as extractor.py: an auth/permission failure is a
    deployment problem, not a per-turn STT hiccup, and must not be hidden
    behind a generic 'didn't catch that' fallback."""
    client = _mock_client(
        side_effects=[
            groq.AuthenticationError("bad key", response=AsyncMock(status_code=401), body=None)
        ]
    )
    with pytest.raises(groq.AuthenticationError):
        await transcribe(client, model="m", audio=b"x", filename="a.webm")


async def test_transcribe_waits_out_a_short_rate_limit_then_succeeds():
    """A short Retry-After is worth spending part of the retry budget on --
    see app/retry.py. The header names 0.01s so this test does not actually
    pause for anything a human would notice."""
    client = _mock_client(side_effects=[_rate_limit_error({"retry-after": "0.01"}), "third floor"])
    result = await transcribe(client, model="m", audio=b"x", filename="a.webm")
    assert result == "third floor"


async def test_transcribe_gives_up_immediately_on_a_long_rate_limit_wait():
    """No second attempt at all -- only one side_effect is provided, so a
    wrongly-attempted retry would IndexError instead of quietly passing."""
    client = _mock_client(side_effects=[_rate_limit_error({"retry-after": "999"})])
    result = await transcribe(client, model="m", audio=b"x", filename="a.webm")
    assert result is None


async def test_transcribe_gives_up_immediately_when_no_retry_after_header_present():
    client = _mock_client(side_effects=[_rate_limit_error({})])
    result = await transcribe(client, model="m", audio=b"x", filename="a.webm")
    assert result is None
