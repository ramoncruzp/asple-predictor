"""Application settings loaded from environment variables and .env."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application configuration."""

    binance_api_key: str = Field("tu_api_key_aqui", validation_alias="BINANCE_API_KEY")
    binance_api_secret: str = Field("tu_api_secret_aqui", validation_alias="BINANCE_API_SECRET")
    testnet_api_key: str = Field("tu_testnet_api_key_aqui", validation_alias="TESTNET_API_KEY")
    testnet_api_secret: str = Field("tu_testnet_secret_aqui", validation_alias="TESTNET_SECRET")
    database_url: str = Field("sqlite:///./asple_predictor.db", validation_alias="DATABASE_URL")
    prediction_interval_minutes: int = Field(240, validation_alias="PREDICTION_INTERVAL_MINUTES")
    verification_delay_candles: int = Field(4, validation_alias="VERIFICATION_DELAY_CANDLES")
    log_level: str = Field("INFO", validation_alias="LOG_LEVEL")
    environment: str = Field("development", validation_alias="ENVIRONMENT")
    usdt_por_grid: float = Field(100.0, validation_alias="USDT_POR_GRID")
    max_grids_simultaneos: int = Field(5, validation_alias="MAX_GRIDS_SIMULTANEOS")
    capital_max_por_nivel_pct: float = Field(0.30, validation_alias="CAPITAL_MAX_POR_NIVEL_PCT")
    grid_min_step_pct: float = Field(0.003, validation_alias="GRID_MIN_STEP_PCT")
    grid_monitor_interval: int = Field(900, validation_alias="GRID_MONITOR_INTERVAL")
    grid_monitor_gap_minutes: int = Field(20, validation_alias="GRID_MONITOR_GAP_MINUTES")
    grid_monitor_enabled: bool = Field(True, validation_alias="GRID_MONITOR_ENABLED")
    grid_policy_enabled: bool = Field(True, validation_alias="GRID_POLICY_ENABLED")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
