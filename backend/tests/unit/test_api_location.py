"""POST /session/{id}/location, exercised as real HTTP requests -- the same
"prove the FastAPI wiring is correct" job test_api_turn.py does for /turn.
test_maps.py already covers link-parsing and reverse-geocoding themselves in
full; this file's job is narrower: routing, session lookup, and response-
shape reuse of TurnResponse.
"""

from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.routes import get_cartesia_client, get_groq_client, get_http_client
from app.config import get_settings
from app.main import app

_KORAMANGALA_LINK = "https://www.google.com/maps/place/Koramangala/@12.9352,77.6146,15z/data=!3m1"
_INDIRANAGAR_LINK = "https://www.google.com/maps/@12.9716,77.5946,15z"
_UNGEOCODABLE_LINK = "https://www.google.com/maps/@0.0000,0.0000,15z"

# Keyed by the exact coordinates the two links above embed -- reverse_geocode
# formats to 6 decimals, but the regex-extracted value is exactly these
# literals, so an exact-match table (no tolerance handling needed) is enough.
_GEOCODE_NAMES = {(12.9352, 77.6146): "Koramangala", (12.9716, 77.5946): "Indiranagar"}


def _mock_groq_client():
    # Never actually called: resolving a map link is deliberately LLM-free
    # (see services/maps.py's module docstring) -- present only so
    # TestClient's real app.main lifespan does not build a real AsyncGroq
    # client from whatever key happens to be configured locally.
    return AsyncMock()


def _mock_cartesia_client():
    client = AsyncMock()

    async def post(*_args, **_kwargs):
        request = httpx.Request("POST", "https://api.cartesia.ai/tts/bytes")
        return httpx.Response(200, content=b"wav-bytes", request=request)

    client.post = post
    return client


def _mock_http_client():
    """Handles the one leg of _process_location_link this file's URLs ever
    reach: Nominatim's reverse-geocode call (a small coordinate -> name
    table). None of these URLs are shortened, so the redirect-following
    branch is never exercised here -- test_maps.py covers that directly."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert "nominatim.openstreetmap.org" in str(request.url)
        lat = round(float(request.url.params["lat"]), 4)
        lon = round(float(request.url.params["lon"]), 4)
        name = _GEOCODE_NAMES.get((lat, lon))
        address = {"suburb": name} if name else {}
        return httpx.Response(200, json={"address": address})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture
def client(tmp_path):
    app.dependency_overrides[get_groq_client] = _mock_groq_client
    app.dependency_overrides[get_cartesia_client] = _mock_cartesia_client
    app.dependency_overrides[get_http_client] = _mock_http_client
    app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(
        update={"tts_cache_dir": tmp_path}
    )
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def _new_session(client: TestClient) -> str:
    return client.post("/api/session").json()["session_id"]


def test_a_valid_map_link_sets_the_pickup_locality_to_a_geocoded_name(client):
    session_id = _new_session(client)

    response = client.post(
        "/api/session/" + session_id + "/location",
        json={"field": "pickup", "url": _KORAMANGALA_LINK},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["state"]["pickup"]["locality"]["value"] == "Koramangala"
    assert body["state"]["pickup"]["locality"]["status"] == "provided"
    assert body["done"] is False


def test_a_valid_map_link_sets_the_drop_locality_to_a_geocoded_name(client):
    session_id = _new_session(client)

    response = client.post(
        "/api/session/" + session_id + "/location",
        json={"field": "drop", "url": _KORAMANGALA_LINK},
    )

    assert response.status_code == 200
    assert response.json()["state"]["drop"]["locality"]["value"] == "Koramangala"


def test_a_map_link_does_not_populate_raw_text_or_a_heard_as_evidence(client):
    """The whole point of the fix this test locks in: a pasted link has
    nothing comparable to "heard as" show for -- a URL is not something a
    human can visually compare against a place name -- so unlike a spoken
    locality correction, this must never populate raw_text at all."""
    session_id = _new_session(client)

    response = client.post(
        "/api/session/" + session_id + "/location",
        json={"field": "pickup", "url": _KORAMANGALA_LINK},
    )

    assert response.json()["state"]["pickup"]["raw_text"]["value"] is None


def test_reverse_geocode_failure_falls_back_to_plain_coordinates(client):
    """No address data at all for this point (the mock's empty-address
    branch) -- must still succeed with the one thing that's always known:
    the coordinates themselves, not block the turn on a non-essential
    lookup failing."""
    session_id = _new_session(client)

    response = client.post(
        "/api/session/" + session_id + "/location",
        json={"field": "pickup", "url": _UNGEOCODABLE_LINK},
    )

    assert response.status_code == 200
    assert response.json()["state"]["pickup"]["locality"]["value"] == "0.00000, 0.00000"


def test_an_unparseable_link_leaves_the_locality_unset_and_explains_why(client):
    session_id = _new_session(client)

    response = client.post(
        "/api/session/" + session_id + "/location",
        json={"field": "pickup", "url": "https://example.com/not-a-map"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["state"]["pickup"]["locality"]["value"] is None
    assert "couldn't read" in body["agent_text"].lower()


def test_a_second_link_for_an_already_set_locality_is_a_correction(client):
    session_id = _new_session(client)
    client.post(
        "/api/session/" + session_id + "/location",
        json={"field": "pickup", "url": _KORAMANGALA_LINK},
    )

    response = client.post(
        "/api/session/" + session_id + "/location",
        json={"field": "pickup", "url": _INDIRANAGAR_LINK},
    )

    body = response.json()
    assert body["state"]["pickup"]["locality"]["value"] == "Indiranagar"
    revisions = body["state"]["pickup"]["locality"]["revisions"]
    assert revisions and revisions[-1]["value"] == "Koramangala"


def test_location_link_with_unknown_session_id_returns_404(client):
    response = client.post(
        "/api/session/does-not-exist/location",
        json={"field": "pickup", "url": _KORAMANGALA_LINK},
    )

    assert response.status_code == 404
