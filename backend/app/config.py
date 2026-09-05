"""Application configuration.

The Groq API key is the only secret this project holds, and it never leaves the
server: the browser talks to our own backend, and the backend talks to Groq.
That is a requirement of the assessment brief, not just good hygiene.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_PLACEHOLDER_KEY = "gsk_replace_me"


class Settings(BaseSettings):
    """Settings resolved from the environment, or a local .env file in development."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Credentials -------------------------------------------------------
    groq_api_key: str = ""

    # --- Models ------------------------------------------------------------
    # One vendor covers speech-to-text, the LLM and text-to-speech, so the
    # whole project needs exactly one key. See docs/architecture.md.
    groq_llm_model: str = "openai/gpt-oss-120b"
    groq_stt_model: str = "whisper-large-v3-turbo"
    groq_tts_model: str = "canopylabs/orpheus-v1-english"
    groq_tts_voice: str = "hannah"

    # --- Behaviour ---------------------------------------------------------
    session_ttl_seconds: int = 3600
    max_clarify_attempts: int = 2
    tts_cache_dir: Path = Path(".tts_cache")
    log_level: str = "INFO"

    @property
    def is_configured(self) -> bool:
        """True when a usable Groq key is present.

        The app deliberately still starts without one so that the deterministic
        core can be developed and tested with no network access at all.
        """
        return bool(self.groq_api_key) and self.groq_api_key != _PLACEHOLDER_KEY


@lru_cache
def get_settings() -> Settings:
    """Cached accessor. Settings are read once per process."""
    return Settings()
