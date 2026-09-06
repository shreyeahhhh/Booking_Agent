"""Chunking, the retry/fallback client, and the on-disk cache --
MASTER_PLAN.md step 3.2, moved to Cartesia in a later pass.

`chunk_text` is pure and gets exhaustive coverage with no mocking at all,
the same approach as test_stt.py's `is_noise`. The client and cache use a
mocked httpx.AsyncClient plus pytest's `tmp_path` for real (but disposable)
file I/O -- the live proof that Cartesia's REST endpoint actually behaves
this way is test_tts_live.py. Real httpx.Response objects are used for the
mocked responses rather than a loose Mock, specifically so `.raise_for_
status()` exercises httpx's own real 4xx/5xx behaviour instead of a
hand-rolled stand-in that could silently drift from it.
"""

import asyncio
import time
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services.tts import _MAX_CHARS, chunk_text, synthesize


def _response(
    status_code: int, *, content: bytes = b"", headers: dict | None = None
) -> httpx.Response:
    request = httpx.Request("POST", "https://api.cartesia.ai/tts/bytes")
    return httpx.Response(status_code, content=content, headers=headers or {}, request=request)


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
    """A fake httpx.AsyncClient whose post() yields side_effects in order --
    each entry is either raw WAV bytes (a 200 success), a real httpx.Response
    (for a specific status/headers, e.g. 429 or 400), or an Exception
    instance (a connection-level failure) to raise for that call."""
    client = AsyncMock()

    async def post(*_args, **_kwargs):
        effect = side_effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        if isinstance(effect, httpx.Response):
            return effect
        return _response(200, content=effect)

    client.post = post
    return client


async def test_synthesize_returns_one_chunk_of_audio_for_short_text(tmp_path):
    client = _mock_client(side_effects=[b"wav-bytes"])
    result = await synthesize(client, model="m", voice_id="v1", text="Got it.", cache_dir=tmp_path)
    assert result == [b"wav-bytes"]


async def test_synthesize_returns_none_immediately_when_client_is_none(tmp_path):
    """The contract get_cartesia_client/synthesize rely on when
    CARTESIA_API_KEY is not configured -- no attempt made at all, not an
    error, matching what an absent key should mean: fall back to
    speechSynthesis, don't break the turn."""
    result = await synthesize(None, model="m", voice_id="v1", text="Got it.", cache_dir=tmp_path)
    assert result is None


async def test_synthesize_returns_one_audio_chunk_per_text_chunk(tmp_path):
    sentence = "This is a normal length sentence about a booking. "
    text = sentence * 10
    expected_chunks = chunk_text(text)
    client = _mock_client(side_effects=[f"audio-{i}".encode() for i in range(len(expected_chunks))])
    result = await synthesize(client, model="m", voice_id="v1", text=text, cache_dir=tmp_path)
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

    async def post(*_args, **_kwargs):
        await asyncio.sleep(delay)
        return _response(200, content=b"audio-chunk")

    client = AsyncMock()
    client.post = post

    start = time.monotonic()
    result = await synthesize(client, model="m", voice_id="v1", text=text, cache_dir=tmp_path)
    elapsed = time.monotonic() - start

    assert len(result) == len(expected_chunks)
    sequential_cost = delay * len(expected_chunks)
    assert elapsed < sequential_cost * 0.75, (
        f"took {elapsed:.3f}s for {len(expected_chunks)} chunks at {delay}s each "
        f"({sequential_cost:.3f}s if sequential) -- looks sequential, not concurrent"
    )


async def test_synthesize_retries_once_after_a_connection_error_then_succeeds(tmp_path):
    client = _mock_client(side_effects=[httpx.ConnectError("boom"), b"wav-bytes"])
    result = await synthesize(client, model="m", voice_id="v1", text="Got it.", cache_dir=tmp_path)
    assert result == [b"wav-bytes"]


async def test_synthesize_returns_none_after_the_retry_budget_is_exhausted(tmp_path):
    err = httpx.ConnectError("boom")
    client = _mock_client(side_effects=[err, err])
    result = await synthesize(client, model="m", voice_id="v1", text="Got it.", cache_dir=tmp_path)
    assert result is None


async def test_synthesize_does_not_retry_a_structural_bad_request(tmp_path):
    """Same reasoning as services/stt.py: a 4xx here (bad voice/model id, a
    malformed request) means something structurally wrong that an identical
    retry cannot fix -- it must propagate immediately, not be retried or
    swallowed."""
    client = _mock_client(side_effects=[_response(400, content=b'{"error":"bad voice"}')])
    with pytest.raises(httpx.HTTPStatusError):
        await synthesize(client, model="m", voice_id="v1", text="Got it.", cache_dir=tmp_path)


async def test_a_non_retriable_error_propagates_instead_of_being_swallowed(tmp_path):
    client = _mock_client(side_effects=[_response(401, content=b'{"error":"bad key"}')])
    with pytest.raises(httpx.HTTPStatusError):
        await synthesize(client, model="m", voice_id="v1", text="Got it.", cache_dir=tmp_path)


async def test_synthesize_waits_out_a_short_rate_limit_then_succeeds(tmp_path):
    client = _mock_client(
        side_effects=[_response(429, headers={"retry-after": "0.01"}), b"wav-bytes"]
    )
    result = await synthesize(client, model="m", voice_id="v1", text="Got it.", cache_dir=tmp_path)
    assert result == [b"wav-bytes"]


async def test_synthesize_gives_up_immediately_on_a_long_rate_limit_wait(tmp_path):
    """No second attempt at all -- only one side_effect is provided, so a
    wrongly-attempted retry would IndexError instead of quietly passing."""
    client = _mock_client(side_effects=[_response(429, headers={"retry-after": "999"})])
    result = await synthesize(client, model="m", voice_id="v1", text="Got it.", cache_dir=tmp_path)
    assert result is None


async def test_a_repeated_phrase_is_served_from_cache_with_no_second_api_call(tmp_path):
    # Only one side effect: a second real API call would IndexError.
    client = _mock_client(side_effects=[b"wav-bytes"])
    first = await synthesize(client, model="m", voice_id="v1", text="Got it.", cache_dir=tmp_path)
    second = await synthesize(client, model="m", voice_id="v1", text="Got it.", cache_dir=tmp_path)
    assert first == second == [b"wav-bytes"]


async def test_a_different_voice_id_is_not_served_from_the_other_ones_cache_entry(tmp_path):
    client = _mock_client(side_effects=[b"voice-a-audio", b"voice-b-audio"])
    a = await synthesize(client, model="m", voice_id="voice-a", text="Got it.", cache_dir=tmp_path)
    b = await synthesize(client, model="m", voice_id="voice-b", text="Got it.", cache_dir=tmp_path)
    assert a == [b"voice-a-audio"]
    assert b == [b"voice-b-audio"]


async def test_cache_is_written_to_disk_as_wav_files(tmp_path):
    client = _mock_client(side_effects=[b"wav-bytes"])
    await synthesize(client, model="m", voice_id="v1", text="Got it.", cache_dir=tmp_path)
    cached_files = list(tmp_path.glob("*.wav"))
    assert len(cached_files) == 1
    assert cached_files[0].read_bytes() == b"wav-bytes"
