"""Live proof of Cartesia TTS -- MASTER_PLAN.md, TTS provider switch.

Marked `llm`: makes a real network call (billed against Cartesia's free-tier
character budget, not Groq's). Excluded from a plain `pytest` run; skipped
automatically if no key is configured.

Confirms the exact contract services/tts.py depends on, verified live before
trusting it (not assumed from docs): the REST shape (POST /tts/bytes,
Cartesia-Version header, a bare voice-id string), and the WAV output's own
quirk -- Cartesia's response uses the streaming convention of an
unknown-length sentinel (0xFFFFFFFF) in both the RIFF and data chunk size
fields rather than the real byte count. That was confirmed to still play
correctly in a real browser `<audio>` element (frontend/src/audio.ts's exact
mechanism) when this was first discovered; this test keeps the underlying
claim -- a valid, parseable RIFF/WAVE container despite the sentinel -- as a
permanent regression check so a future Cartesia change that broke it would
be caught by the suite, not discovered mid-demo.
"""

import struct

import httpx
import pytest

from app.config import get_settings

_settings = get_settings()

pytestmark = [
    pytest.mark.llm,
    pytest.mark.skipif(
        not _settings.cartesia_is_configured,
        reason="CARTESIA_API_KEY not configured -- see .env.example",
    ),
]

_API_VERSION = "2026-08-14"


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="https://api.cartesia.ai",
        headers={
            "Authorization": f"Bearer {_settings.cartesia_api_key}",
            "Cartesia-Version": _API_VERSION,
        },
    )


async def _speak(text: str) -> httpx.Response:
    async with _client() as client:
        response = await client.post(
            "/tts/bytes",
            json={
                "model_id": _settings.cartesia_tts_model,
                "transcript": text,
                "voice": _settings.cartesia_tts_voice_id,
                "language": "en",
                "output_format": {
                    "container": "wav",
                    "encoding": "pcm_s16le",
                    "sample_rate": 44100,
                },
            },
        )
        response.raise_for_status()
        return response


async def test_synthesizing_a_short_response_returns_playable_wav_audio():
    response = await _speak("Got it, Koramangala to Whitefield.")
    content = response.content
    print(f"\n{len(content)} bytes returned")
    assert content[:4] == b"RIFF", "response is not a WAV file (missing RIFF header)"
    assert content[8:12] == b"WAVE"
    assert len(content) > 100


async def test_the_configured_voice_and_model_are_accepted():
    """settings.cartesia_tts_voice_id must be a voice Cartesia actually
    recognises, not just a plausible-looking UUID copied into config.py
    before this was ever tested live."""
    await _speak("Testing the configured voice.")  # must not raise


async def test_a_chunk_at_our_own_configured_max_length_is_accepted():
    """_MAX_CHARS is a self-imposed chunk size, not an API-enforced one (see
    services/tts.py's module docstring) -- this just confirms our own chosen
    ceiling actually round-trips correctly."""
    from app.services.tts import _MAX_CHARS

    text = (
        "This is a padded test sentence used only to reach exactly the "
        "configured maximum chunk length for a live check here today, ok"
    )
    text = (text + " " + "x" * _MAX_CHARS)[:_MAX_CHARS]
    assert len(text) == _MAX_CHARS
    await _speak(text)  # must not raise


async def test_wav_output_uses_a_streaming_length_sentinel_not_the_real_size():
    """Documents and permanently guards the exact quirk services/tts.py's
    module docstring describes: both the RIFF and data chunk declared sizes
    are 0xFFFFFFFF (unknown/streaming length), not the real byte count. A
    naive fixed-offset parser breaks on this (there is also an extra LIST
    metadata chunk between fmt and data) -- proper chunk-walking, which is
    what actually matters here, does not. If Cartesia ever starts sending a
    real length, that is a welcome change and this assertion should be
    relaxed then, not a sign this test is wrong now."""
    response = await _speak("Hi.")
    content = response.content
    riff_declared_size = struct.unpack("<I", content[4:8])[0]
    assert riff_declared_size == 0xFFFFFFFF

    pos = 12
    found_data_chunk = False
    while pos + 8 <= len(content):
        chunk_id = content[pos : pos + 4]
        chunk_size = struct.unpack("<I", content[pos + 4 : pos + 8])[0]
        if chunk_id == b"data":
            found_data_chunk = True
            assert chunk_size == 0xFFFFFFFF
            actual_remaining = len(content) - (pos + 8)
            assert actual_remaining > 0, "data chunk header present but no audio bytes followed"
            break
        pos += 8 + chunk_size + (chunk_size % 2)  # chunks are word-aligned
    assert found_data_chunk, "no data chunk found -- chunk-walking logic or format itself changed"
