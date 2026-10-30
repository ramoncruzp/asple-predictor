#!/usr/bin/env python3
"""Ejecuta el benchmark 20/45 días con una biblioteca FHS sintética temporal."""
from __future__ import annotations

import tempfile
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.escenarios_velocidad import benchmark


def main():
    rng = np.random.default_rng(43)
    with tempfile.TemporaryDirectory(prefix="asple-f43e3e-sintetica-") as temporary:
        directory = Path(temporary)
        returns = rng.standard_t(5, (400, 288)).astype(np.float32)
        values = np.empty((400, 288, 3), dtype=np.float32)
        values[:, :, 0] = returns
        values[:, :, 1] = np.maximum(returns, 0) + np.abs(rng.normal(0, .5, (400, 288)))
        values[:, :, 2] = np.minimum(returns, 0) - np.abs(rng.normal(0, .5, (400, 288)))
        first_day = 1_700_000_000 // 86400
        dates = np.arange(first_day, first_day + len(values), dtype=np.int64) * 86400
        np.savez_compressed(directory / "SYNTHUSDT.npz", values=values, dates=dates,
            daily_z_return_sum=returns.sum(axis=1, dtype=np.float64), mean_z_return=np.float32(0))
        for row in benchmark(library_directory=directory, symbol="SYNTHUSDT", price=100,
                sigma_today=.01, sigma_long=.01, n_scenarios=1000):
            print(f"VELOCIDAD_SINTETICA días={row['days']} escenarios={row['scenarios']} "
                f"segundos={row['seconds']:.3f} fills_promedio={row['fills_per_scenario']:.3f} "
                f"rss_max_mb={row['rss_max_mb']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
