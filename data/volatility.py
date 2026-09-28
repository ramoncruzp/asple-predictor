"""Causal realized-volatility targets and OHLC range estimators."""

from __future__ import annotations

import numpy as np
import pandas as pd

EPSILON = 1e-8
WINDOWS = (1, 24, 168)
RANGE_ESTIMATORS = ("parkinson", "garman_klass", "rogers_satchell")


def log_returns(df: pd.DataFrame) -> pd.Series:
    """Close-to-close log returns, aligned to the candle whose close completes them."""
    _require_columns(df, ("close",))
    close = pd.to_numeric(df["close"], errors="coerce").astype("float64")
    if (close.dropna() <= 0).any():
        raise ValueError("Los precios close deben ser positivos")
    return np.log(close).diff().rename("log_return")


def range_variance_estimators(df: pd.DataFrame) -> pd.DataFrame:
    """Return per-candle Parkinson, Garman-Klass, and Rogers-Satchell estimates."""
    _require_columns(df, ("open", "high", "low", "close"))
    prices = df[["open", "high", "low", "close"]].astype("float64")
    if (prices <= 0).any().any():
        raise ValueError("Los precios OHLC deben ser positivos")
    log_hl = np.log(prices["high"] / prices["low"])
    log_co = np.log(prices["close"] / prices["open"])
    log_hc = np.log(prices["high"] / prices["close"])
    log_ho = np.log(prices["high"] / prices["open"])
    log_lc = np.log(prices["low"] / prices["close"])
    log_lo = np.log(prices["low"] / prices["open"])
    return pd.DataFrame(
        {
            "parkinson_bar": log_hl.pow(2) / (4.0 * np.log(2.0)),
            "garman_klass_bar": (
                0.5 * log_hl.pow(2) - (2.0 * np.log(2.0) - 1.0) * log_co.pow(2)
            ).clip(lower=0.0),
            "rogers_satchell_bar": (log_hc * log_ho + log_lc * log_lo).clip(lower=0.0),
        },
        index=df.index,
    )


def feature_columns(horizon: int) -> list[str]:
    """Feature columns required by the volatility model family."""
    windows = tuple(dict.fromkeys((*WINDOWS, horizon)))
    columns = [
        "log_return", "vol_ratio", "abs_return_24", "hour_utc", "day_of_week_utc",
        "sqrt_rq_24",
    ]
    for window in windows:
        columns.extend((f"rv_{window}", f"log_rv_{window}", f"semivar_neg_{window}"))
        columns.append(f"log_semivar_neg_{window}")
        for estimator in RANGE_ESTIMATORS:
            columns.extend((f"{estimator}_{window}", f"log_{estimator}_{window}"))
    return list(dict.fromkeys(columns))


def utc_timestamps(df: pd.DataFrame) -> pd.Series:
    """Candle time in UTC, preferring its close time when available."""
    if "close_time" in df:
        return pd.to_datetime(df["close_time"], utc=True)
    if "timestamp" in df:
        return pd.to_datetime(df["timestamp"], utc=True)
    raise ValueError("Se requiere timestamp o close_time para features de calendario")


def seasonal_bucket_ids(times: pd.Series) -> pd.Series:
    """Map UTC timestamps to 48 hour-of-day x weekday/weekend buckets."""
    utc = pd.to_datetime(times, utc=True)
    return ((utc.dt.dayofweek >= 5).astype("int64") * 24 + utc.dt.hour).astype("int64")


def aggregate_intraday_to_hourly(df_5m: pd.DataFrame) -> pd.DataFrame:
    """Aggregate 5-minute candles to UTC hours with realized intraday measures.

    Returns are computed before grouping so the first return in each hour includes
    the close-to-close move from the preceding 5-minute candle (including across
    hour boundaries).
    """
    _require_columns(df_5m, ("timestamp", "open", "high", "low", "close", "volume"))
    frame = df_5m.copy(deep=True)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    frame = frame.sort_values("timestamp", kind="stable").reset_index(drop=True)
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("float64")
    if (frame[["open", "high", "low", "close"]].dropna() <= 0).any().any():
        raise ValueError("Los precios OHLC deben ser positivos")

    frame["_hour"] = frame["timestamp"].dt.floor("h")
    frame["_return"] = np.log(frame["close"] / frame["close"].shift(1))
    range_bars = range_variance_estimators(frame)
    frame = frame.join(range_bars)
    frame["_r2"] = frame["_return"].pow(2)
    frame["_r4"] = frame["_return"].pow(4)
    frame["_negative_r2"] = frame["_r2"].where(frame["_return"] < 0.0, 0.0)

    grouped = frame.groupby("_hour", sort=True)
    hourly = grouped.agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum"), n_bars=("close", "size"),
        rv_intra=("_r2", "sum"), _sum_r4=("_r4", "sum"),
        parkinson_intra=("parkinson_bar", "sum"),
        semivar_neg_intra=("_negative_r2", "sum"),
    ).rename_axis("timestamp").reset_index()
    hourly["rq_intra"] = (hourly["n_bars"] / 3.0) * hourly.pop("_sum_r4")
    hourly["complete_hour"] = hourly["n_bars"] >= 10
    hourly["close_time"] = hourly["timestamp"] + pd.Timedelta(hours=1) - pd.Timedelta(milliseconds=1)
    return hourly


def build_volatility_frame(
    df: pd.DataFrame, horizon: int, intraday: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Add causal lagged features and the next-H-hours log realized-vol target.

    ``target_logvol`` at row t uses only returns t+1 through t+H. All feature
    columns at row t use candles and returns no later than t.
    """
    if horizon < 1:
        raise ValueError("horizon debe ser positivo")
    _require_columns(df, ("open", "high", "low", "close", "volume"))
    raw_columns = [
        column for column in ("timestamp", "close_time", "open", "high", "low", "close", "volume")
        if column in df
    ]
    result = df[raw_columns].copy(deep=True)
    returns = log_returns(result)
    result["log_return"] = returns
    returns_sq = returns.pow(2)
    intraday_aligned = None
    if intraday is not None:
        required_intraday = {
            "rv_intra", "rq_intra", "parkinson_intra", "semivar_neg_intra", "complete_hour"
        }
        missing = required_intraday.difference(intraday.columns)
        if missing:
            raise ValueError(f"Faltan medidas intradÃ­a: {sorted(missing)}")
        if "timestamp" in result and "timestamp" in intraday:
            result_keys = pd.to_datetime(result["timestamp"], utc=True).dt.floor("h")
            intraday_keys = pd.to_datetime(intraday["timestamp"], utc=True).dt.floor("h")
            keyed = intraday.assign(_hour=intraday_keys).drop_duplicates("_hour", keep="last").set_index("_hour")
            intraday_aligned = keyed.reindex(pd.DatetimeIndex(result_keys))
            intraday_aligned.index = result.index
        elif len(intraday) == len(result):
            intraday_aligned = intraday.copy(deep=True)
            intraday_aligned.index = result.index
        else:
            raise ValueError("intraday debe alinearse por timestamp o tener el mismo largo que df")
        hourly_rv = pd.to_numeric(intraday_aligned["rv_intra"], errors="coerce").astype("float64")
        hourly_semivar = pd.to_numeric(intraday_aligned["semivar_neg_intra"], errors="coerce").astype("float64")
        result["complete_hour"] = intraday_aligned["complete_hour"].fillna(False).astype(bool)
        result["rv_intra"] = hourly_rv
        result["rq_intra"] = pd.to_numeric(intraday_aligned["rq_intra"], errors="coerce")
        result["parkinson_intra"] = pd.to_numeric(intraday_aligned["parkinson_intra"], errors="coerce")
    else:
        hourly_rv = returns_sq
        hourly_semivar = returns_sq.where(returns < 0.0, 0.0)
        result["complete_hour"] = True
    result["rq_24"] = (
        result["rq_intra"].rolling(24, min_periods=24).sum()
        if intraday_aligned is not None
        else (24.0 / 3.0) * returns.pow(4).rolling(24, min_periods=24).sum()
    )
    result["sqrt_rq_24"] = np.sqrt(result["rq_24"].clip(lower=0.0))
    negative_sq = hourly_semivar
    ranges = range_variance_estimators(result)
    result = result.join(ranges)
    if intraday_aligned is not None:
        ranges["parkinson_bar"] = result["parkinson_intra"]
        result["parkinson_bar"] = result["parkinson_intra"]
    result["variance_per_hour"] = hourly_rv if intraday_aligned is not None else result["parkinson_bar"]
    windows = tuple(dict.fromkeys((*WINDOWS, horizon)))

    for window in windows:
        result[f"rv_{window}"] = hourly_rv.rolling(window, min_periods=window).mean()
        result[f"log_rv_{window}"] = np.log(result[f"rv_{window}"].clip(lower=0.0) + EPSILON)
        result[f"semivar_neg_{window}"] = negative_sq.rolling(
            window, min_periods=window
        ).mean()
        result[f"log_semivar_neg_{window}"] = np.log(
            result[f"semivar_neg_{window}"].clip(lower=0.0) + EPSILON
        )
        for estimator in RANGE_ESTIMATORS:
            per_bar = f"{estimator}_bar"
            column = f"{estimator}_{window}"
            result[column] = ranges[per_bar].rolling(window, min_periods=window).mean()
            result[f"log_{column}"] = np.log(result[column].clip(lower=0.0) + EPSILON)

    result["abs_return_24"] = (result["close"].astype("float64") / result["close"].shift(24) - 1.0).abs()
    volume = pd.to_numeric(result["volume"], errors="coerce").astype("float64")
    volume_mean = volume.rolling(20, min_periods=20).mean()
    result["vol_ratio"] = volume / volume_mean

    time_values = utc_timestamps(result)
    result["hour_utc"] = time_values.dt.hour.astype("float64")
    result["day_of_week_utc"] = time_values.dt.dayofweek.astype("float64")

    target_rv = hourly_rv.where(result["complete_hour"])
    future_rv = target_rv.shift(-1).iloc[::-1].rolling(
        horizon, min_periods=horizon
    ).mean().iloc[::-1]
    result[f"future_vol_{horizon}"] = np.sqrt(future_rv)
    result[f"future_var_{horizon}"] = future_rv
    result["target_logvol"] = (
        0.5 * np.log(future_rv + 1e-12)
        if intraday_aligned is not None
        else np.log(result[f"future_vol_{horizon}"] + EPSILON)
    )
    result["volatility_horizon"] = horizon
    for step in range(1, horizon + 1):
        result[f"future_r2_{step}"] = target_rv.shift(-step)
    return result


def _require_columns(df: pd.DataFrame, columns: tuple[str, ...]) -> None:
    missing = set(columns).difference(df.columns)
    if missing:
        raise ValueError(f"Faltan columnas requeridas: {sorted(missing)}")
