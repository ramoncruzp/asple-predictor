"""Application settings loaded from environment variables and .env."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application configuration."""

    binance_api_key: str = Field("tu_api_key_aqui", validation_alias="BINANCE_API_KEY")
    binance_api_secret: str = Field("tu_api_secret_aqui", validation_alias="BINANCE_API_SECRET")
    database_url: str = Field("sqlite:///./asple_predictor.db", validation_alias="DATABASE_URL")
    prediction_interval_minutes: int = Field(240, validation_alias="PREDICTION_INTERVAL_MINUTES")
    verification_delay_candles: int = Field(4, validation_alias="VERIFICATION_DELAY_CANDLES")
    log_level: str = Field("INFO", validation_alias="LOG_LEVEL")
    environment: str = Field("development", validation_alias="ENVIRONMENT")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
