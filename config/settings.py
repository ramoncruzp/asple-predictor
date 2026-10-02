"""Application settings loaded from environment variables and .env."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

SCANNER_DEFAULTS = {
    "min_volume_24h": 1_000_000.0,
    "max_spread_bps": 15.0,
    "min_spacing_pct": 0.8,
    "fee_pct": 0.1,
    "history_days": 30,
    "cache_ttl_seconds": 300,
    "rate_limit_seconds": 0.2,
    "retries": 2,
    "timeout_seconds": 60.0,
    "weights": {"cost_headroom": 0.35, "liquidity": 0.20,
                "historical_oscillation": 0.35, "trend_penalty": 0.10},
    "min_cell_floor_usdt": 5.5,
}


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
    scanner_min_volume_24h: float = Field(SCANNER_DEFAULTS["min_volume_24h"], gt=0, validation_alias="SCANNER_MIN_VOLUME_24H")
    scanner_max_spread_bps: float = Field(SCANNER_DEFAULTS["max_spread_bps"], gt=0, validation_alias="SCANNER_MAX_SPREAD_BPS")
    scanner_min_spacing_pct: float = Field(SCANNER_DEFAULTS["min_spacing_pct"], gt=0, validation_alias="SCANNER_MIN_SPACING_PCT")
    scanner_fee_pct: float = Field(SCANNER_DEFAULTS["fee_pct"], ge=0, validation_alias="SCANNER_FEE_PCT")
    scanner_history_days: int = Field(SCANNER_DEFAULTS["history_days"], gt=0, validation_alias="SCANNER_HISTORY_DAYS")
    scanner_min_cell_floor_usdt: float = Field(SCANNER_DEFAULTS["min_cell_floor_usdt"], gt=0, validation_alias="SCANNER_MIN_CELL_FLOOR_USDT")
    scanner_cache_ttl_seconds: int = Field(SCANNER_DEFAULTS["cache_ttl_seconds"], ge=0, validation_alias="SCANNER_CACHE_TTL_SECONDS")
    scanner_rate_limit_seconds: float = Field(SCANNER_DEFAULTS["rate_limit_seconds"], ge=0, validation_alias="SCANNER_RATE_LIMIT_SECONDS")
    scanner_retries: int = Field(SCANNER_DEFAULTS["retries"], ge=0, validation_alias="SCANNER_RETRIES")
    scanner_timeout_seconds: float = Field(SCANNER_DEFAULTS["timeout_seconds"], gt=0, validation_alias="SCANNER_TIMEOUT_SECONDS")
    scanner_weight_cost_headroom: float = Field(SCANNER_DEFAULTS["weights"]["cost_headroom"], ge=0, le=1, validation_alias="SCANNER_WEIGHT_COST_HEADROOM")
    scanner_weight_liquidity: float = Field(SCANNER_DEFAULTS["weights"]["liquidity"], ge=0, le=1, validation_alias="SCANNER_WEIGHT_LIQUIDITY")
    scanner_weight_historical_oscillation: float = Field(SCANNER_DEFAULTS["weights"]["historical_oscillation"], ge=0, le=1, validation_alias="SCANNER_WEIGHT_HISTORICAL_OSCILLATION")
    scanner_weight_trend_penalty: float = Field(SCANNER_DEFAULTS["weights"]["trend_penalty"], ge=0, le=1, validation_alias="SCANNER_WEIGHT_TREND_PENALTY")
    scanner_auto_open: bool = Field(False, validation_alias="SCANNER_AUTO_OPEN")
    scanner_auto_open_interval_hours: int = Field(6, gt=0, validation_alias="SCANNER_AUTO_OPEN_INTERVAL_HOURS")
    scanner_auto_open_max_per_run: int = Field(1, ge=0, validation_alias="SCANNER_AUTO_OPEN_MAX_PER_RUN")
    scanner_auto_open_min_score: float = Field(0.6, ge=0, le=1, validation_alias="SCANNER_AUTO_OPEN_MIN_SCORE")
    scanner_auto_open_daily_cap: int = Field(2, ge=0, validation_alias="SCANNER_AUTO_OPEN_DAILY_CAP")
    scanner_auto_open_strategy: str = Field("simple", pattern="^(simple|smart)$", validation_alias="SCANNER_AUTO_OPEN_STRATEGY")
    scanner_auto_open_target_pct: float | None = Field(None, gt=0, le=100, validation_alias="SCANNER_AUTO_OPEN_TARGET_PCT")
    scanner_auto_open_max_days: float | None = Field(None, gt=0, validation_alias="SCANNER_AUTO_OPEN_MAX_DAYS")
    grid_api_token: str = Field("", validation_alias="GRID_API_TOKEN")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
