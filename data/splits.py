"""Leakage-resistant chronological dataset partitions."""

from __future__ import annotations

from config.models_config import TARGET_HORIZON_CANDLES


def chronological_split(
    n_rows: int,
    train: float = 0.70,
    val: float = 0.15,
    embargo: int = TARGET_HORIZON_CANDLES,
) -> tuple[slice, slice, slice]:
    """Return half-open train, validation, and test slices in time order.

    ``embargo`` rows are excluded between each partition so labels whose
    forward horizon crosses a partition boundary cannot enter model fitting.
    Each returned partition must contain at least 50 rows.
    """
    if n_rows < 0:
        raise ValueError("n_rows no puede ser negativo")
    if not 0 < train < 1 or not 0 < val < 1 or train + val >= 1:
        raise ValueError("train y val deben ser proporciones positivas cuya suma sea menor que 1")
    if embargo < 0:
        raise ValueError("embargo no puede ser negativo")

    train_end = int(n_rows * train)
    val_start = train_end + embargo
    val_end = val_start + int(n_rows * val)
    test_start = val_end + embargo
    parts = (slice(0, train_end), slice(val_start, val_end), slice(test_start, n_rows))
    sizes = tuple(part.stop - part.start for part in parts)
    if any(size < 50 for size in sizes):
        raise ValueError(
            "Cada tramo train/val/test debe tener al menos 50 filas "
            f"después del embargo; tamaños={sizes}"
        )
    return parts
