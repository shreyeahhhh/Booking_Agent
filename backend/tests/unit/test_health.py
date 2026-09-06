"""The app boots and answers.

Cheap insurance: catches import errors, route wiring mistakes and a broken
lifespan hook in milliseconds, rather than at deploy time.
"""

from fastapi.testclient import TestClient

from app.main import app


def test_health_reports_ok() -> None:
    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["llm_model"] == "openai/gpt-oss-120b"


def test_health_never_leaks_the_api_key() -> None:
    with TestClient(app) as client:
        response = client.get("/api/health")

    # It reports whether a key exists, never the key itself -- for both
    # credentials this app holds now, not just Groq's.
    body = response.json()
    assert "llm_configured" in body
    assert "tts_configured" in body
    assert "gsk_" not in response.text
    assert "sk_car_" not in response.text
