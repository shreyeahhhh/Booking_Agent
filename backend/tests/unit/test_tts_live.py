"""Live proof of Groq Orpheus TTS -- MASTER_PLAN.md step 3.2.

Marked `llm`: makes a real network call. Excluded from a plain `pytest` run;
skipped automatically if no key is configured. Run sparingly, not on every
change: this project's free-tier Orpheus quota is a tight *daily* token
budget (TPD), not just a per-dollar cost -- a live sweep during this step's
own development used a meaningful fraction of a day's allowance with only a
few thousand characters of test input, and once the daily cap is hit the
account is locked out for hours regardless of dollar cost. See
services/tts.py's module docstring and MASTER_PLAN.md's risk register.

Requires one manual, one-time step this test cannot do for you: an org
admin must accept Orpheus's model terms at
https://console.groq.com/playground?model=canopylabs%2Forpheus-v1-english
-- discovered live while building services/tts.py, where every call
(different lengths, different formats, an invalid voice) failed identically
with a 400 `model_terms_required` until that happens. Every test below
routes its call through `_speak()`, which detects that specific error and
skips with a clear message instead of failing in a confusing way that looks
like a code bug.
"""

import groq
import pytest
from groq import Groq

from app.config import get_settings
from app.services.tts import _MAX_CHARS

_settings = get_settings()

pytestmark = [
    pytest.mark.llm,
    pytest.mark.skipif(
        not _settings.is_configured,
        reason="GROQ_API_KEY not configured -- see .env.example",
    ),
]

_TERMS_URL = "https://console.groq.com/playground?model=canopylabs%2Forpheus-v1-english"


def _is_terms_required(err: groq.BadRequestError) -> bool:
    body = err.body
    return isinstance(body, dict) and body.get("error", {}).get("code") == "model_terms_required"


def _speak(client: Groq, text: str, **kwargs):
    # response_format has no server-side default that Orpheus itself accepts
    # (discovered by this test file initially omitting it: every call failed
    # with "response_format must be one of [wav]", not the error each test
    # actually meant to provoke) -- always pass it explicitly, exactly like
    # services/tts.py's own _synthesize_chunk does.
    kwargs.setdefault("response_format", "wav")
    try:
        return client.audio.speech.create(
            model=_settings.groq_tts_model,
            voice=_settings.groq_tts_voice,
            input=text,
            **kwargs,
        )
    except groq.BadRequestError as err:
        if _is_terms_required(err):
            pytest.skip(
                f"Orpheus model terms not accepted for this Groq org -- visit {_TERMS_URL} "
                "and accept them, then re-run with -m llm."
            )
        raise


def test_synthesizing_a_short_response_returns_playable_wav_audio():
    client = Groq(api_key=_settings.groq_api_key)
    response = _speak(client, "Got it, Koramangala to Whitefield.")
    content = response.read()
    print(f"\n{len(content)} bytes returned")
    assert content[:4] == b"RIFF", "response is not a WAV file (missing RIFF header)"
    assert len(content) > 100


def test_the_configured_voice_is_accepted():
    """settings.groq_tts_voice ("hannah") must be a voice Orpheus actually
    recognises, not just a plausible-looking guess copied into config.py
    before this step existed."""
    client = Groq(api_key=_settings.groq_api_key)
    _speak(client, "Testing the configured voice.")  # must not raise


def test_a_chunk_at_our_own_configured_max_length_is_accepted():
    """_MAX_CHARS is a self-imposed chunk size, not an API-enforced one (see
    services/tts.py's module docstring) -- this just confirms our own chosen
    ceiling actually round-trips correctly, not that Orpheus would reject
    anything longer. A live sweep during this step's development found no
    length-based rejection at all up to 1000 characters; the real wall was
    the account's daily token budget, not a per-request size check."""
    text = (
        "This is a padded test sentence used only to reach exactly the "
        "configured maximum chunk length for a live check here today, ok"
    )
    text = (text + " " + "x" * _MAX_CHARS)[:_MAX_CHARS]
    assert len(text) == _MAX_CHARS
    client = Groq(api_key=_settings.groq_api_key)
    _speak(client, text)  # must not raise
