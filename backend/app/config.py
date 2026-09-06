"""Application configuration.

Two secrets, both server-side only: the browser talks to our own backend, never
directly to a vendor. That is a requirement of the assessment brief, not just good
hygiene.

Groq covers speech-to-text and the LLM. Text-to-speech moved to Cartesia (see
services/tts.py's own module docstring for why): Groq's Orpheus TTS free tier is
3600 tokens/*day*, confirmed live to exhaust from ordinary development testing
alone -- a real risk for a submission whose whole point is a human evaluator
testing the live deploy. Cartesia's free tier is a monthly, not daily, allowance,
roughly 5x larger, with no card required to sign up.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_PLACEHOLDER_KEY = "gsk_replace_me"
_CARTESIA_PLACEHOLDER_KEY = "sk_car_replace_me"


class Settings(BaseSettings):
    """Settings resolved from the environment, or a local .env file in development."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Credentials -------------------------------------------------------
    groq_api_key: str = ""
    cartesia_api_key: str = ""

    # --- Models ------------------------------------------------------------
    groq_llm_model: str = "openai/gpt-oss-120b"
    groq_stt_model: str = "whisper-large-v3-turbo"
    cartesia_tts_model: str = "sonic-latest"
    # "Skylar" -- an approachable, professional voice per Cartesia's own voice
    # library, a reasonable fit for a booking assistant. `sonic-latest` (not a
    # dated version like `sonic-3.6`) is deliberate given the one-week timeline
    # here: an alias that keeps working is worth more than perfect
    # reproducibility if the dated model it points to is ever retired.
    cartesia_tts_voice_id: str = "db6b0ed5-d5d3-463d-ae85-518a07d3c2b4"

    # --- Behaviour ---------------------------------------------------------
    session_ttl_seconds: int = 3600
    max_clarify_attempts: int = 2
    tts_cache_dir: Path = Path(".tts_cache")
    log_level: str = "INFO"

    @property
    def is_configured(self) -> bool:
        """True when a usable Groq key is present.

        The app deliberately still starts without one so that the deterministic
        core can be developed and tested with no network access at all. This is
        about STT/the LLM specifically -- see `cartesia_is_configured` for TTS,
        which is allowed to be missing without blocking a turn at all (the
        browser speechSynthesis fallback already exists for exactly that).
        """
        return bool(self.groq_api_key) and self.groq_api_key != _PLACEHOLDER_KEY

    @property
    def cartesia_is_configured(self) -> bool:
        """True when a usable Cartesia key is present.

        Deliberately a soft, non-blocking check, unlike `is_configured` above --
        services/tts.py treats an unconfigured key exactly the same as a failed
        API call: skip straight to the None -> speechSynthesis-fallback path
        that already exists, never a hard error.
        """
        return bool(self.cartesia_api_key) and self.cartesia_api_key != _CARTESIA_PLACEHOLDER_KEY


@lru_cache
def get_settings() -> Settings:
    """Cached accessor. Settings are read once per process."""
    return Settings()
