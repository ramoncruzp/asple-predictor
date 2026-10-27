"""Curvas compactas de piezas y evaluación conjunta de una Meta simulada."""
import base64
import numpy as np


def pack_curve(timestamps, cash, equity, capital):
    times = np.asarray(timestamps, dtype=np.int64)
    if not len(times) or len(cash) != len(times) or len(equity) != len(times):
        raise ValueError("curva vacía o desalineada")
    gaps = np.diff(times)
    step = int(gaps[0]) if len(gaps) and np.all(gaps == gaps[0]) else None
    encode = lambda values, dtype: base64.b64encode(np.asarray(values, dtype=dtype).tobytes()).decode("ascii")
    return {"start_ts": int(times[0]), "step_s": step, "count": len(times),
        "offsets_i32": None if step else encode(times - times[0], "<i4"),
        "cash_f32": encode(cash, "<f4"), "equity_f32": encode(equity, "<f4"),
        "capital_usdt": float(capital)}


def unpack_curve(curve):
    decode = lambda key, dtype: np.frombuffer(base64.b64decode(curve[key]), dtype=dtype)
    offsets = (np.arange(curve["count"], dtype=np.int64) * curve["step_s"]
        if curve.get("step_s") else decode("offsets_i32", "<i4"))
    return (int(curve["start_ts"]) + offsets,
        decode("cash_f32", "<f4"), decode("equity_f32", "<f4"))


def evaluate_portfolio(curves, *, capital_initial, target_usdt, max_loss_pct=None):
    """Éxito por efectivo; pérdida y métricas por patrimonio, con efectivo ocioso."""
    decoded = [unpack_curve(curve) for curve in curves]
    if not decoded:
        raise ValueError("cartera sin curvas")
    common = decoded[0][0]
    for times, _, _ in decoded[1:]:
        common = np.intersect1d(common, times, assume_unique=True)
    if not len(common):
        raise ValueError("las piezas no comparten timestamps")
    capital = float(capital_initial)
    used = sum(float(curve["capital_usdt"]) for curve in curves)
    if capital <= 0 or used > capital + 1e-6:
        raise ValueError("capital de piezas superior al de la meta")
    cash = np.full(len(common), capital - used, dtype=np.float64)
    equity = cash.copy()
    for times, piece_cash, piece_equity in decoded:
        indices = np.searchsorted(times, common)
        cash += piece_cash[indices]
        equity += piece_equity[indices]
    hits = np.flatnonzero(cash >= capital + float(target_usdt))
    losses = (np.flatnonzero(equity <= capital * (1 - float(max_loss_pct) / 100))
        if max_loss_pct is not None and float(max_loss_pct) > 0 else np.array([], dtype=int))
    hit = int(hits[0]) if len(hits) else None
    loss = int(losses[0]) if len(losses) else None
    cut = loss is not None and (hit is None or loss <= hit)
    path = equity[:loss + 1] if cut else equity
    peaks = np.maximum.accumulate(np.r_[capital, path])[1:]
    drawdown = np.divide(peaks - path, peaks, out=np.zeros_like(path), where=peaks > 0) * 100
    return {"reached": hit is not None and not cut,
        "days": float(common[hit] - max(int(curve["start_ts"]) for curve in curves)) / 86400
            if hit is not None and not cut else None,
        "pnl_usdt": float(path[-1] - capital),
        "max_advance_usdt": float(np.max(path) - capital),
        "drawdown_pct": float(max(0, np.max(drawdown))),
        "worst_loss_vs_capital_pct": float(max(0, (capital - np.min(path)) / capital * 100)),
        "loss_cut": bool(cut), "step_days": 7,
        "cash_final": float(cash[loss] if cut else cash[-1]),
        "capital_unused_usdt": capital - used}


def simulate_piece_curve(candles, *, strategy, n, capital, low, high, filters=None, min_margin_pct=.3):
    """Sin objetivo ni corte de pérdida por pieza; resolución de quince minutos."""
    times, cash, equity = [], [], []
    count = 0
    def trace(row):
        nonlocal count
        include = (strategy != "individual_bot" or int(row["timestamp"]) % 900 == 0 or count == 0
            or row["timestamp"] == int(candles.timestamp[-1]))
        count += 1
        if include:
            times.append(int(row["timestamp"]))
            cash.append(float(row.get("cash", (row.get("balances") or {}).get("USDT", 0))))
            equity.append(float(row.get("equity_incl_dust", row["equity"])))
    if strategy == "individual_bot":
        from grid.sim.meta_bot import run_meta_bot
        replay = run_meta_bot(candles, capital=capital, buy_price=low, sell_price=high,
            target_usdt=None, fee_pct=.1, max_loss_pct=None, filters=filters,
            min_margin_pct=min_margin_pct, trace_callback=trace)
        extra = {"bought": replay["bought"], "sold": replay["sold"]}
    else:
        from grid.sim.runner import run_simulation
        run_simulation(candles, strategy=strategy, n=n, capital=capital, low=low, high=high,
            width_pct=None, params={}, fee_pct=.1, filters=filters,
            close_wait_enabled=True, adjust_enabled_global=False, stoploss_enabled=False,
            meta_max_loss_pct=None, meta_portfolio_piece=True, trace_callback=trace, resync_candles=3)
        extra = {}
    return {"curve": pack_curve(times, cash, equity, capital), **extra}
