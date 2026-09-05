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


def test_missing_key_is_not_configured() -> None:
    assert _settings().is_configured is False


def test_placeholder_key_is_not_configured() -> None:
    """A copied-but-unedited .env.example must not read as configured."""
    assert _settings(groq_api_key="gsk_replace_me").is_configured is False


def test_real_key_is_configured() -> None:
    assert _settings(groq_api_key="gsk_abc123").is_configured is True
