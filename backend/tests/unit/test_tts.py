"""Chunking, the retry/fallback client, and the on-disk cache --
MASTER_PLAN.md step 3.2.

`chunk_text` is pure and gets exhaustive coverage with no mocking at all,
the same approach as test_stt.py's `is_noise`. The client and cache use a
mocked AsyncGroq plus pytest's `tmp_path` for real (but disposable) file
I/O -- the live proof that Groq's speech endpoint actually behaves this way
is test_tts_live.py.
"""

import asyncio
import time
from unittest.mock import AsyncMock

import groq
import pytest

from app.services.tts import _MAX_CHARS, chunk_text, synthesize

# --- chunk_text ---------------------------------------------------------


def test_short_text_is_a_single_chunk():
    text = "Got it, Koramangala to Whitefield."
    assert chunk_text(text) == [text]


def test_empty_text_produces_no_chunks():
    assert chunk_text("") == []
    assert chunk_text("   ") == []


def test_short_sentences_are_packed_into_one_chunk():
    text = "Got it. Whitefield, third floor. Anything else?"
    chunks = chunk_text(text)
    assert chunks == [text]


def test_a_chunk_never_exceeds_max_chars():
    sentence = "This is a normal length sentence about a booking. "
    text = sentence * 10  # comfortably over 200 chars once joined
    chunks = chunk_text(text)
    assert len(chunks) > 1
    assert all(len(c) <= _MAX_CHARS for c in chunks)


def test_chunks_split_on_sentence_boundaries_not_mid_sentence():
    first = "A" * 150 + "."
    second = "B" * 100 + "."
    chunks = chunk_text(f"{first} {second}")
    assert chunks == [first, second]


def test_a_single_oversized_sentence_falls_back_to_word_boundaries():
    words = ["word"] * 80  # one long "sentence" with no punctuation at all
    text = " ".join(words)
    chunks = chunk_text(text)
    assert len(chunks) > 1
    assert all(len(c) <= _MAX_CHARS for c in chunks)
    assert " ".join(chunks) == text  # nothing lost, nothing duplicated


def test_reassembling_all_chunks_reproduces_the_sentences():
    text = "First sentence here. Second one follows. And a third to finish."
    chunks = chunk_text(text)
    assert " ".join(chunks) == text


# --- synthesize: retry, fallback, cache -------------------------------------


def _mock_client(*, side_effects) -> AsyncMock:
    client = AsyncMock()

    async def create(**_kwargs):
        effect = side_effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        response = AsyncMock()
        response.read = AsyncMock(return_value=effect)
        return response

    client.audio.speech.create = create
    return client


async def test_synthesize_returns_one_chunk_of_audio_for_short_text(tmp_path):
    client = _mock_client(side_effects=[b"wav-bytes"])
    result = await synthesize(client, model="m", voice="hannah", text="Got it.", cache_dir=tmp_path)
    assert result == [b"wav-bytes"]


async def test_synthesize_returns_one_audio_chunk_per_text_chunk(tmp_path):
    sentence = "This is a normal length sentence about a booking. "
    text = sentence * 10
    expected_chunks = chunk_text(text)
    client = _mock_client(side_effects=[f"audio-{i}".encode() for i in range(len(expected_chunks))])
    result = await synthesize(client, model="m", voice="hannah", text=text, cache_dir=tmp_path)
    assert len(result) == len(expected_chunks)


async def test_multiple_chunks_are_synthesized_concurrently_not_sequentially(tmp_path):
    """The whole point of switching synthesize() from a sequential loop to
    asyncio.gather(): a multi-chunk response's latency should track the
    slowest single chunk, not the sum of every chunk. The other tests here
    only check chunk count/content, which a regression back to a sequential
    loop would not break -- only timing would. delay is deliberately small;
    the assertion leaves generous headroom (well under the 2x a sequential
    loop over 3 chunks would cost) so this cannot flake on a slow CI box."""
    delay = 0.05
    sentence = "This is a normal length sentence about a booking. "
    text = sentence * 10
    expected_chunks = chunk_text(text)
    assert len(expected_chunks) >= 3  # sanity check: the setup actually exercises concurrency

    async def create(**_kwargs):
        await asyncio.sleep(delay)
        response = AsyncMock()
        response.read = AsyncMock(return_value=b"audio-chunk")
        return response

    client = AsyncMock()
    client.audio.speech.create = create

    start = time.monotonic()
    result = await synthesize(client, model="m", voice="hannah", text=text, cache_dir=tmp_path)
    elapsed = time.monotonic() - start

    assert len(result) == len(expected_chunks)
    sequential_cost = delay * len(expected_chunks)
    assert elapsed < sequential_cost * 0.75, (
        f"took {elapsed:.3f}s for {len(expected_chunks)} chunks at {delay}s each "
        f"({sequential_cost:.3f}s if sequential) -- looks sequential, not concurrent"
    )


async def test_synthesize_retries_once_after_a_connection_error_then_succeeds(tmp_path):
    client = _mock_client(side_effects=[groq.APIConnectionError(request=AsyncMock()), b"wav-bytes"])
    result = await synthesize(client, model="m", voice="hannah", text="Got it.", cache_dir=tmp_path)
    assert result == [b"wav-bytes"]


async def test_synthesize_returns_none_after_the_retry_budget_is_exhausted(tmp_path):
    err = groq.APIConnectionError(request=AsyncMock())
    client = _mock_client(side_effects=[err, err])
    result = await synthesize(client, model="m", voice="hannah", text="Got it.", cache_dir=tmp_path)
    assert result is None


async def test_synthesize_does_not_retry_a_bad_request_error(tmp_path):
    """Same reasoning as services/stt.py: a 400 here (bad voice, terms not
    accepted, text over the limit) is structural, verified live against the
    real API -- it must propagate immediately, not be retried or swallowed."""
    err = groq.BadRequestError("bad voice", response=AsyncMock(status_code=400), body=None)
    client = _mock_client(side_effects=[err])
    with pytest.raises(groq.BadRequestError):
        await synthesize(client, model="m", voice="hannah", text="Got it.", cache_dir=tmp_path)


async def test_a_non_retriable_error_propagates_instead_of_being_swallowed(tmp_path):
    client = _mock_client(
        side_effects=[
            groq.AuthenticationError("bad key", response=AsyncMock(status_code=401), body=None)
        ]
    )
    with pytest.raises(groq.AuthenticationError):
        await synthesize(client, model="m", voice="hannah", text="Got it.", cache_dir=tmp_path)


async def test_a_repeated_phrase_is_served_from_cache_with_no_second_api_call(tmp_path):
    # Only one side effect: a second real API call would IndexError.
    client = _mock_client(side_effects=[b"wav-bytes"])
    first = await synthesize(client, model="m", voice="hannah", text="Got it.", cache_dir=tmp_path)
    second = await synthesize(client, model="m", voice="hannah", text="Got it.", cache_dir=tmp_path)
    assert first == second == [b"wav-bytes"]


async def test_a_different_voice_is_not_served_from_the_other_voices_cache_entry(tmp_path):
    client = _mock_client(side_effects=[b"hannah-audio", b"daniel-audio"])
    hannah = await synthesize(client, model="m", voice="hannah", text="Got it.", cache_dir=tmp_path)
    daniel = await synthesize(client, model="m", voice="daniel", text="Got it.", cache_dir=tmp_path)
    assert hannah == [b"hannah-audio"]
    assert daniel == [b"daniel-audio"]


async def test_cache_is_written_to_disk_as_wav_files(tmp_path):
    client = _mock_client(side_effects=[b"wav-bytes"])
    await synthesize(client, model="m", voice="hannah", text="Got it.", cache_dir=tmp_path)
    cached_files = list(tmp_path.glob("*.wav"))
    assert len(cached_files) == 1
    assert cached_files[0].read_bytes() == b"wav-bytes"
