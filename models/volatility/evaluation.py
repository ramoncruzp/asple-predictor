"""Loss metrics and moving-block bootstrap comparisons."""

from __future__ import annotations

import numpy as np


def moving_block_bootstrap_difference(
    loss_reference,
    loss_candidate,
    block_size: int = 168,
    n_bootstrap: int = 2000,
    seed: int = 42,
) -> dict:
    """Bootstrap mean(reference loss - candidate loss) with circular blocks."""
    reference = np.asarray(loss_reference, dtype="float64")
    candidate = np.asarray(loss_candidate, dtype="float64")
    if reference.ndim != 1 or candidate.ndim != 1 or reference.shape != candidate.shape:
        raise ValueError("Las pérdidas deben ser vectores unidimensionales alineados")
    if not len(reference):
        raise ValueError("Se requieren pérdidas para el bootstrap")
    if block_size < 1 or n_bootstrap < 1:
        raise ValueError("block_size y n_bootstrap deben ser positivos")
    difference = reference - candidate
    n = len(difference)
    blocks_needed = int(np.ceil(n / block_size))
    offsets = np.arange(block_size)
    rng = np.random.default_rng(seed)
    means = np.empty(n_bootstrap, dtype="float64")
    for sample in range(n_bootstrap):
        starts = rng.integers(0, n, size=blocks_needed)
        indices = ((starts[:, None] + offsets[None, :]) % n).reshape(-1)[:n]
        means[sample] = difference[indices].mean()
    p_value = float((np.count_nonzero(means <= 0.0) + 1) / (n_bootstrap + 1))
    return {
        "mean_difference": float(difference.mean()),
        "ci95_low": float(np.quantile(means, 0.025)),
        "ci95_high": float(np.quantile(means, 0.975)),
        "beats": bool(np.quantile(means, 0.025) > 0.0),
        "p_value_one_sided": p_value,
        "n_rows": int(n),
        "block_size": int(block_size),
        "n_bootstrap": int(n_bootstrap),
        "seed": int(seed),
    }
