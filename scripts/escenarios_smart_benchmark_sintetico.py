#!/usr/bin/env python3
"""Mide el replay Smart en 200 escenarios FHS sintéticos de veinte días."""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.scenarios.generator import generate
from research.scenarios.smart_exact import evaluate_smart_scenarios
from scripts.escenarios_velocidad import _rss_mb


def main():
    rng = np.random.default_rng(43)
    with tempfile.TemporaryDirectory(prefix="asple-f43e4-sintetica-") as temporary:
        directory = Path(temporary)
        returns = rng.standard_t(5, (400, 288)).astype(np.float32)
        values = np.empty((400, 288, 3), dtype=np.float32)
        values[:, :, 0] = returns
        values[:, :, 1] = np.maximum(returns, 0) + np.abs(rng.normal(0, .5, (400, 288)))
        values[:, :, 2] = np.minimum(returns, 0) - np.abs(rng.normal(0, .5, (400, 288)))
        first_day = 1_700_000_000 // 86400
        dates = np.arange(first_day, first_day + 400, dtype=np.int64) * 86400
        np.savez_compressed(directory / "SYNTHUSDT.npz", values=values, dates=dates,
            daily_z_return_sum=returns.sum(axis=1, dtype=np.float64), mean_z_return=np.float32(0))
        started = time.perf_counter()
        batches = list(generate(["SYNTHUSDT"], horizon_days=20, n_scenarios=200,
            seed=43, price_now=100, sigma_today_24h=.01, sigma_long_24h=.01,
            library_directory=directory))
        scenarios = {key: np.concatenate([batch["symbols"]["SYNTHUSDT"][key]
            for batch in batches]) for key in ("open", "high", "low", "close")}
        sigmas = np.concatenate([batch["symbols"]["SYNTHUSDT"]["sigma_path"]
            for batch in batches])
        width = .01 * np.sqrt(20 * 24) * 2
        result = evaluate_smart_scenarios(scenarios, n_levels=6, capital=1000,
            low=100 * np.exp(-width), high=100 * np.exp(width), sigma_path=sigmas,
            n_smart=200, workers=2)
        print(f"SMART_SINTETICO escenarios={len(result)} días=20 segundos={time.perf_counter()-started:.3f} "
            f"rss_max_mb={_rss_mb()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
