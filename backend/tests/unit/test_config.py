"""Configuration smoke tests.

These matter more than they look: a mistyped default model name would not
surface until the first live API call, which is a slow and confusing way to
find a typo.
"""

from app.config import Settings


def _settings(**overrides: object) -> Settings:
    # _env_file=None isolates the test from any developer's local .env.
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def test_defaults_point_at_production_tier_models() -> None:
    settings = _settings()
    # gpt-oss-120b is the only production-tier Groq model with strict
    # structured outputs; see docs/architecture.md for the comparison.
    assert settings.groq_llm_model == "openai/gpt-oss-120b"
    assert settings.groq_stt_model == "whisper-large-v3-turbo"
    assert settings.cartesia_tts_model == "sonic-latest"
    assert settings.cartesia_tts_voice_id  # a real UUID, not left blank


def test_missing_key_is_not_configured() -> None:
    assert _settings().is_configured is False


def test_placeholder_key_is_not_configured() -> None:
    """A copied-but-unedited .env.example must not read as configured."""
    assert _settings(groq_api_key="gsk_replace_me").is_configured is False


def test_real_key_is_configured() -> None:
    assert _settings(groq_api_key="gsk_abc123").is_configured is True


def test_missing_cartesia_key_is_not_configured() -> None:
    assert _settings().cartesia_is_configured is False


def test_placeholder_cartesia_key_is_not_configured() -> None:
    assert _settings(cartesia_api_key="sk_car_replace_me").cartesia_is_configured is False


def test_real_cartesia_key_is_configured() -> None:
    assert _settings(cartesia_api_key="sk_car_abc123").cartesia_is_configured is True


def test_cartesia_is_configured_is_independent_of_groq() -> None:
    """A deploy can have one key without the other -- each vendor's
    configuration state must not leak into the other's."""
    groq_only = _settings(groq_api_key="gsk_abc123")
    assert groq_only.is_configured is True
    assert groq_only.cartesia_is_configured is False

    cartesia_only = _settings(cartesia_api_key="sk_car_abc123")
    assert cartesia_only.is_configured is False
    assert cartesia_only.cartesia_is_configured is True
