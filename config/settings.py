import logging
from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # API
    app_name: str = Field(default="Mai Chi English API", alias="APP_NAME")
    debug: bool = Field(default=False, alias="DEBUG")
    port: int = Field(default=8000, alias="PORT")

    # Database
    database_url: str = Field(..., alias="DATABASE_URL")

    # CORS
    cors_origins: str = Field(
        default="http://localhost:3000",
        alias="CORS_ORIGINS",
    )
    cors_origin_regex: str = Field(
        # Matches every Vercel deploy of the frontend Vercel projects:
        #   - maichienglish              (production)
        #   - dev-maichienglish          (dev)
        #   - maichienglish-frontend     (legacy)
        # plus their preview URLs (e.g. maichienglish-<hash>-<team>.vercel.app).
        default=r"^https://(dev-)?maichienglish(-[\w-]+)?\.vercel\.app$",
        alias="CORS_ORIGIN_REGEX",
    )

    # JWT
    jwt_secret_key: str = Field(default="change-me-in-prod", alias="JWT_SECRET_KEY")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_access_exp_minutes: int = Field(default=60, alias="JWT_ACCESS_EXP_MINUTES")
    jwt_refresh_exp_days: int = Field(default=7, alias="JWT_REFRESH_EXP_DAYS")
    jwt_issuer: str = Field(default="maichienglish", alias="JWT_ISSUER")
    jwt_audience: str = Field(default="maichienglish", alias="JWT_AUDIENCE")

    # Supabase Storage (audio + images buckets — used from B3.4+)
    supabase_url: Optional[str] = Field(default=None, alias="SUPABASE_URL")
    supabase_service_role_key: Optional[str] = Field(
        default=None, alias="SUPABASE_SERVICE_ROLE_KEY"
    )
    storage_provider: str = Field(default="supabase", alias="STORAGE_PROVIDER")

    # AI generation — all via OpenRouter (1 key, many models). Model is an
    # OpenRouter slug, swappable by env. `anthropic` stays as an alt provider.
    openrouter_api_key: Optional[str] = Field(default=None, alias="OPENROUTER_API_KEY")
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1", alias="OPENROUTER_BASE_URL"
    )

    # Groq — also OpenAI-compatible (direct, not via OpenRouter)
    groq_api_key: Optional[str] = Field(default=None, alias="GROQ_API_KEY")
    groq_base_url: str = Field(
        default="https://api.groq.com/openai/v1", alias="GROQ_BASE_URL"
    )

    # Gemini — Google's OpenAI-compatible endpoint (direct, "real" Gemini)
    gemini_api_key: Optional[str] = Field(default=None, alias="GEMINI_API_KEY")
    gemini_base_url: str = Field(
        default="https://generativelanguage.googleapis.com/v1beta/openai/",
        alias="GEMINI_BASE_URL",
    )

    # Text generation (docs/exam-ai-generation/)
    ai_provider: str = Field(default="openrouter", alias="AI_PROVIDER")  # openrouter | groq | anthropic
    anthropic_api_key: Optional[str] = Field(default=None, alias="ANTHROPIC_API_KEY")
    # Pinned to Opus 4.8 (client request, newest model). NOTE: the DB
    # ai_generation_settings row overrides this (per-request > DB > env) — change
    # that row too if it pins an older slug. A/B numbers were on sonnet-4.5 →
    # re-baseline after this change.
    ai_model: str = Field(default="anthropic/claude-opus-4.8", alias="AI_MODEL")
    ai_max_tokens: int = Field(default=8000, alias="AI_MAX_TOKENS")
    ai_self_review_rounds: int = Field(default=2, alias="AI_SELF_REVIEW_ROUNDS")
    # Per-request hardening (avoid the multi-minute stall seen in A/B: the SDK
    # default is 600s/request). Applied to every AI call via the adapters.
    ai_request_timeout: float = Field(default=180.0, alias="AI_REQUEST_TIMEOUT")
    ai_max_retries: int = Field(default=2, alias="AI_MAX_RETRIES")

    # Image generation (docs/exam-image-generation/) — off by default
    image_generation_enabled: bool = Field(
        default=False, alias="IMAGE_GENERATION_ENABLED"
    )
    image_provider: str = Field(default="openrouter", alias="IMAGE_PROVIDER")
    # NOTE: the old "-preview" slug was retired from OpenRouter (404 "No
    # endpoints found") — this GA slug is its replacement (verified live
    # 2026-06-11 via scripts/smoke_image_gen.py).
    image_model: str = Field(
        default="google/gemini-2.5-flash-image", alias="IMAGE_MODEL"
    )
    image_verify_model: str = Field(
        default="google/gemini-2.5-flash", alias="IMAGE_VERIFY_MODEL"
    )
    image_verify_rounds: int = Field(default=2, alias="IMAGE_VERIFY_ROUNDS")

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.jwt_secret_key == "change-me-in-prod":
        logger.warning(
            "JWT_SECRET_KEY is still the default 'change-me-in-prod' — set a real "
            "secret in the environment before serving real traffic."
        )
    else:
        logger.info(
            "JWT secret loaded (length: %d chars)", len(settings.jwt_secret_key)
        )
    return settings
