"""Muestreo histórico filtrado con los mismos bloques para todas las monedas."""
from __future__ import annotations

import numpy as np

from research.scenarios.library import load_library


def _as_symbol_map(value, symbols):
    if isinstance(value, dict):
        return {symbol: float(value[symbol]) for symbol in symbols}
    return {symbol: float(value) for symbol in symbols}


def generate(symbols, *, horizon_days, n_scenarios, seed, price_now,
        sigma_today_24h, sigma_long_24h, tau_days=3.0, block_days=3,
        before=None, batch_size=None, library_directory="data/derived/scenario_lib",
        calibration_factors=None):
    """Produce lotes reproducibles; cada lote contiene dicts por símbolo.

    `price_now` y sigmas pueden ser escalares o mapas por símbolo. Los lotes
    devuelven OHLC `(n,T)`, `sigma_path` `(n,días)` y fechas de origen.
    """
    symbols = tuple(str(symbol).upper() for symbol in symbols)
    if not symbols or horizon_days <= 0 or n_scenarios <= 0 or block_days <= 0:
        raise ValueError("símbolos, horizonte, escenarios y bloques deben ser positivos")
    price_map = _as_symbol_map(price_now, symbols)
    today_map = _as_symbol_map(sigma_today_24h, symbols)
    long_map = _as_symbol_map(sigma_long_24h, symbols)
    calibration_factors = calibration_factors or {}
    today_map = {s: today_map[s] * float(calibration_factors.get(s, 1.0)) for s in symbols}
    libraries = {s: load_library(s, before=before, directory=library_directory) for s in symbols}
    date_maps = {s: {int(day): i for i, day in enumerate(libraries[s][1])} for s in symbols}
    common_starts = []
    candidate_days = sorted(set.intersection(*(set(map(int, libraries[s][1])) for s in symbols)))
    for start in candidate_days:
        if all(all(start + offset * 86400 in date_maps[s] for offset in range(block_days))
                for s in symbols):
            common_starts.append(start)
    if not common_starts:
        raise ValueError("no hay bloques de fechas comunes completos para las monedas")
    batch_size = int(batch_size or max(1, min(n_scenarios,
        (100 * 1024 * 1024) // max(1, len(symbols) * horizon_days * 288 * 8 * 4))))
    if batch_size < 1:
        raise ValueError("batch_size debe ser positivo")
    rng = np.random.default_rng(seed)
    days_needed = int(horizon_days)
    blocks_needed = int(np.ceil(days_needed / block_days))
    scale_days = np.arange(days_needed, dtype=float)
    sigma_paths = {s: long_map[s] + (today_map[s] - long_map[s]) *
        np.exp(-scale_days / max(float(tau_days), 1e-9)) for s in symbols}
    for offset in range(0, int(n_scenarios), batch_size):
        count = min(batch_size, int(n_scenarios) - offset)
        block_origins = rng.choice(np.asarray(common_starts, dtype=np.int64),
            size=(count, blocks_needed), replace=True)
        batch = {"scenario_start": offset, "symbols": {}}
        day_origin = np.concatenate([block_origins[:, :, None] +
            np.arange(block_days, dtype=np.int64)[None, None, :] * 86400], axis=2)
        day_origin = day_origin.reshape(count, -1)[:, :days_needed]
        for symbol in symbols:
            values, dates = libraries[symbol]
            date_to_index = date_maps[symbol]
            z = np.stack([values[[date_to_index[int(day)] for day in day_origin[:, d]], :, :]
                for d in range(days_needed)], axis=1).reshape(count, days_needed * 288, 3)
            sigma_path = np.broadcast_to(sigma_paths[symbol], (count, days_needed)).copy().astype(np.float64)
            candle_scale = (sigma_path[:, :, None] / np.sqrt(288)).repeat(288, axis=2).reshape(count, -1)
            scaled = z.astype(np.float64) * candle_scale[:, :, None].astype(np.float64)
            close = (np.float64(price_map[symbol]) * np.exp(
                np.cumsum(scaled[:, :, 0], axis=1, dtype=np.float64))).astype(np.float64)
            previous = np.concatenate((np.full((count, 1), price_map[symbol], np.float64),
                close[:, :-1]), axis=1)
            high = previous * np.exp(scaled[:, :, 1])
            low = previous * np.exp(scaled[:, :, 2])
            high = np.maximum(high, np.maximum(previous, close))
            low = np.minimum(low, np.minimum(previous, close))
            open_prices = previous.copy()
            batch["symbols"][symbol] = {"open": open_prices, "high": high, "low": low,
                "close": close, "sigma_path": sigma_path,
                "source_dates": day_origin.copy()}
        yield batch
