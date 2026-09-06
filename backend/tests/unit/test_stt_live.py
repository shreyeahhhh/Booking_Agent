"""Live proof of the silence/noise behaviour `services/stt.is_noise` is built
against -- MASTER_PLAN.md step 3.1.

Marked `llm`: makes a real network call (Groq bills audio transcription per
second, so a couple of 1-2s clips costs a fraction of a cent). Excluded from
a plain `pytest` run; skipped automatically if no key is configured.

This is not a synthetic assumption -- it is the exact probe that shaped
`is_noise`, kept as a permanent test so a future change to Groq's Whisper
deployment that silently invalidates the hallucination list is caught by
the suite instead of discovered live in a demo.
"""

import io
import struct
import wave

import groq
import pytest
from groq import AsyncGroq, Groq

from app.config import get_settings
from app.conversation.machine import start
from app.llm.extractor import extract
from app.services.stt import is_noise, transcribe

_settings = get_settings()

pytestmark = [
    pytest.mark.llm,
    pytest.mark.skipif(
        not _settings.is_configured,
        reason="GROQ_API_KEY not configured -- see .env.example",
    ),
]


def _silent_wav(seconds: float = 1.5, sample_rate: int = 16000) -> bytes:
    n = int(sample_rate * seconds)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(struct.pack(f"<{n}h", *([0] * n)))
    return buf.getvalue()


def _white_noise_wav(seconds: float = 1.5, amplitude: int = 500, sample_rate: int = 16000) -> bytes:
    import random

    n = int(sample_rate * seconds)
    samples = [random.randint(-amplitude, amplitude) for _ in range(n)]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(struct.pack(f"<{n}h", *samples))
    return buf.getvalue()


async def _assert_never_a_confident_silent_write(client: AsyncGroq, text: str | None) -> None:
    """The safety property this design actually depends on, asserted directly.

    is_noise() is a best-effort filter, not a guarantee -- see its own
    docstring for the accepted, live-verified gap _LOCALITY_PROMPT opened
    up (samples like "If you feel like a" and "Kerenaga, Ejiti, Gopal."
    match neither of its two checks). Asserting is_noise() alone made this
    a permanently-flaky test for a gap the design already accepts. What
    must actually never happen is downstream: reducer.py's _write_scalar
    only ever accepts a patch as PROVIDED fact when `ambiguity is None`, so
    a hallucinated transcript reaching the real extractor and coming back
    as an unflagged patch is the one outcome that would silently corrupt
    booking state. Fed live during this investigation, the two garbage
    samples above were both extracted with an `ambiguity` reason attached
    instead (`vague_location` / `city_level_only`) -- confirm that holds
    here rather than re-assuming it.
    """
    if is_noise(text):
        return  # caught at the cheap, no-LLM-call layer -- the common case
    result = await extract(
        client,
        model=_settings.groq_llm_model,
        state=start().booking,
        last_question="Where are you moving from, and what are you sending?",
        recent_turns=[],
        utterance=text,
    )
    unsafe = [p for p in result.patches if p.ambiguity is None]
    assert not unsafe, (
        f"transcript {text!r} slipped past is_noise() AND the extractor wrote "
        f"{unsafe!r} with no ambiguity flag -- _write_scalar accepts that as a "
        "confirmed fact, so this is a real silent-corruption risk, not a "
        "cosmetic one."
    )


async def test_digital_silence_never_reaches_conversation_state_as_a_confirmed_fact():
    """Goes through services/stt.transcribe() itself, not a hand-rolled API
    call -- _LOCALITY_PROMPT is only ever sent by the real function, and a
    live sweep showed it makes the silence hallucination considerably less
    predictable than the plain "Thank you." an earlier version of this test
    asserted. Repeats several times: the hallucination varies enough now
    that one sample is not a confident signal either way.
    """
    async with AsyncGroq(api_key=_settings.groq_api_key) as client:
        for seconds in (0.5, 1.5, 3.0):
            text = await transcribe(
                client, model=_settings.groq_stt_model, audio=_silent_wav(seconds), filename="s.wav"
            )
            print(f"\nsilence ({seconds}s) transcript: {text!r}")
            await _assert_never_a_confident_silent_write(client, text)


async def test_random_noise_never_reaches_conversation_state_as_a_confirmed_fact():
    async with AsyncGroq(api_key=_settings.groq_api_key) as client:
        for amplitude in (500, 4000):
            text = await transcribe(
                client,
                model=_settings.groq_stt_model,
                audio=_white_noise_wav(amplitude=amplitude),
                filename="n.wav",
            )
            print(f"\nnoise (amplitude={amplitude}) transcript: {text!r}")
            await _assert_never_a_confident_silent_write(client, text)


def test_malformed_audio_is_a_structural_400_not_a_transient_one():
    """services/stt.py excludes BadRequestError from its retry set on the
    reasoning that a 400 here means something structurally wrong with the
    request, unlike llm/extractor.py's 400 (an observed, sometimes-transient
    schema-violation quirk). That reasoning was originally by analogy, not
    verified -- confirm it directly: the identical malformed input must
    produce the identical error on a second attempt. If it ever doesn't,
    the exclusion in services/stt.py needs revisiting, not this test."""
    client = Groq(api_key=_settings.groq_api_key)
    garbage = b"not a real audio file at all" * 5

    def _attempt() -> str:
        with pytest.raises(groq.BadRequestError) as exc_info:
            client.audio.transcriptions.create(
                model=_settings.groq_stt_model,
                file=("garbage.wav", garbage),
            )
        return exc_info.value.body["error"]["message"]

    first = _attempt()
    second = _attempt()
    print(f"\nmalformed-audio 400 message: {first!r}")
    assert first == second, "the same malformed audio produced two different 400 messages"
