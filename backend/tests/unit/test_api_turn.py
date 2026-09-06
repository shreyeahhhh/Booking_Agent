"""The /session and /turn endpoints, exercised as real HTTP requests --
MASTER_PLAN.md step 3.4.

test_process_turn.py already covers every branch of the actual pipeline
logic directly and precisely. This file's job is different and narrower:
prove the FastAPI wiring itself is correct -- multipart form parsing,
dependency injection and overriding, response-model serialisation, route
paths -- which only a real request through the app can actually exercise.
"""

import json
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.routes import get_cartesia_client, get_groq_client
from app.config import get_settings
from app.main import app

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


def _mock_client(*, stt_text="Koramangala", llm_content=_VALID_LOCALITY_RESPONSE):
    client = AsyncMock()

    async def transcribe(**_kwargs):
        return AsyncMock(text=stt_text)

    async def chat_create(**_kwargs):
        response = AsyncMock()
        response.choices = [AsyncMock(message=AsyncMock(content=llm_content))]
        return response

    client.audio.transcriptions.create = transcribe
    client.chat.completions.create = chat_create
    return client


def _mock_cartesia_client():
    """TTS moved to Cartesia -- a separate client/dependency from the Groq
    one above, so it needs its own override here too, otherwise TestClient's
    real app.main lifespan would build a real httpx.AsyncClient from
    whatever CARTESIA_API_KEY happens to be configured locally and make a
    real network call from what should be an offline test."""
    client = AsyncMock()

    async def post(*_args, **_kwargs):
        request = httpx.Request("POST", "https://api.cartesia.ai/tts/bytes")
        return httpx.Response(200, content=b"wav-bytes", request=request)

    client.post = post
    return client


@pytest.fixture
def client(tmp_path):
    """A TestClient with the Groq client, Cartesia client and settings all
    overridden -- always cleared afterwards, since app.dependency_overrides
    mutates the same module-level `app` object every other test file
    (test_health.py included) also imports."""
    app.dependency_overrides[get_groq_client] = _mock_client
    app.dependency_overrides[get_cartesia_client] = _mock_cartesia_client
    app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(
        update={"tts_cache_dir": tmp_path}
    )
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def test_session_returns_an_id_and_speaks_the_greeting(client):
    response = client.post("/api/session")

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"]
    assert "moving from" in body["agent_text"].lower()
    assert body["audio_chunks"]  # at least one base64-encoded chunk
    assert body["tts_fallback"] is False


def test_a_full_turn_via_multipart_upload_updates_the_booking_state(client):
    session_id = client.post("/api/session").json()["session_id"]

    response = client.post(
        "/api/turn",
        data={"session_id": session_id},
        files={"audio": ("clip.webm", b"fake-audio-bytes", "audio/webm")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["user_text"] == "Koramangala"
    assert body["state"]["pickup"]["locality"]["value"] == "Koramangala"
    assert body["phase"] == "gathering"
    assert body["done"] is False


def test_turn_with_an_unknown_session_id_returns_404(client):
    response = client.post(
        "/api/turn",
        data={"session_id": "does-not-exist"},
        files={"audio": ("clip.webm", b"fake-audio-bytes", "audio/webm")},
    )

    assert response.status_code == 404


def test_get_groq_client_raises_503_when_no_key_is_configured():
    request = AsyncMock()
    request.app.state.groq_client = None
    with pytest.raises(HTTPException) as exc_info:
        get_groq_client(request)
    assert exc_info.value.status_code == 503


def test_get_cartesia_client_returns_none_without_raising_when_not_configured():
    """Deliberately different from get_groq_client above: an absent Cartesia
    key degrades a turn's voice, not its correctness, so this must never
    raise -- services/tts.synthesize() is the layer that turns a None client
    into the existing speechSynthesis-fallback behaviour."""
    request = AsyncMock()
    request.app.state.cartesia_client = None
    assert get_cartesia_client(request) is None
