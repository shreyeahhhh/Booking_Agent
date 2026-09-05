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
from groq import Groq

from app.config import get_settings
from app.services.stt import is_noise

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


def test_digital_silence_transcribes_as_a_known_hallucination_and_is_flagged_noise():
    client = Groq(api_key=_settings.groq_api_key)
    result = client.audio.transcriptions.create(
        model=_settings.groq_stt_model,
        file=("silence.wav", _silent_wav()),
        response_format="json",
        temperature=0,
    )
    print(f"\nsilence transcript: {result.text!r}")
    assert is_noise(result.text), (
        f"silence transcribed as {result.text!r}, which is_noise did not catch -- "
        "either a new Whisper hallucination was observed (add it to "
        "_SILENCE_HALLUCINATIONS) or the deployment changed behaviour."
    )


def test_random_noise_transcribes_as_punctuation_and_is_flagged_noise():
    client = Groq(api_key=_settings.groq_api_key)
    result = client.audio.transcriptions.create(
        model=_settings.groq_stt_model,
        file=("noise.wav", _white_noise_wav()),
        response_format="json",
        temperature=0,
    )
    print(f"\nnoise transcript: {result.text!r}")
    assert is_noise(result.text), (
        f"noise transcribed as {result.text!r}, which is_noise did not catch"
    )


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
