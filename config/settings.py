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

    # AI exam generation (docs/exam-ai-generation/) — used from Phase 1+
    ai_provider: str = Field(default="anthropic", alias="AI_PROVIDER")
    anthropic_api_key: Optional[str] = Field(default=None, alias="ANTHROPIC_API_KEY")
    ai_model: str = Field(default="claude-sonnet-4-6", alias="AI_MODEL")
    ai_max_tokens: int = Field(default=8000, alias="AI_MAX_TOKENS")
    ai_self_review_rounds: int = Field(default=2, alias="AI_SELF_REVIEW_ROUNDS")

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
