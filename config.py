
"""Configuration for the new PH Greybot."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    bot_token: str
    owner_id: int
    database_url: str
    ai_provider: str
    gemini_api_key: str
    gemini_model: str
    groq_api_key: str
    groq_model: str
    log_level: str


def load_settings() -> Settings:
    token = (
        os.getenv("TELEGRAM_BOT_TOKEN")
        or os.getenv("BOT_TOKEN")
        or ""
    ).strip()

    owner_value = os.getenv("TELEGRAM_OWNER_ID", "").strip()

    if not token:
        raise RuntimeError(
            "Missing TELEGRAM_BOT_TOKEN environment variable."
        )

    if not owner_value.isdigit() or int(owner_value) <= 0:
        raise RuntimeError(
            "TELEGRAM_OWNER_ID must be your numeric Telegram user ID."
        )

    provider = os.getenv("AI_PROVIDER", "gemini").strip().lower()

    if provider not in {"gemini", "groq"}:
        raise RuntimeError("AI_PROVIDER must be gemini or groq.")

    return Settings(
        bot_token=token,
        owner_id=int(owner_value),
        database_url=os.getenv(
            "DATABASE_URL",
            "sqlite:///./ph_greybot.db",
        ).strip(),
        ai_provider=provider,
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        gemini_model=os.getenv(
            "GEMINI_MODEL",
            "gemini-2.5-flash",
        ).strip(),
        groq_api_key=os.getenv("GROQ_API_KEY", "").strip(),
        groq_model=os.getenv(
            "GROQ_MODEL",
            "llama-3.3-70b-versatile",
        ).strip(),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )


def validate_ai_settings(settings: Settings) -> None:
    """Check credentials when AI analysis is required."""
    if settings.ai_provider == "gemini" and not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is missing.")

    if settings.ai_provider == "groq" and not settings.groq_api_key:
        raise RuntimeError("GROQ_API_KEY is missing.")
