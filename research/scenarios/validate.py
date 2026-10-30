"""Validacion predictiva walk-forward de escenarios, sin conexion operativa."""
from __future__ import annotations

import math
from decimal import Decimal
from datetime import datetime, timezone

import numpy as np

from data.market_store import CandleStore, market_db_path
from research.scenarios.evaluate import evaluate_pieces

HORIZON_DAYS = 20
DEFAULT_PIECES = (
    ("simple", 6, 1.0, 70.0), ("simple", 6, 2.0, 70.0),
    ("simple", 10, .5, 70.0), ("bot", 1, None, 30.0),
    ("smart", 6, 2.0, 70.0),
)


def v1_reference_price(low, high, market_mid, *, strategy="simple"):
    """Replica el precio de referencia de meta_operator.py:3236-3250.

    Para grids, un precio medio fuera de piso/techo usa la media geométrica;
    para bot individual se conserva el medio de mercado, como en v1.
    """
    low, high, market_mid = float(low), float(high), float(market_mid)
    if low <= 0 or high <= low or market_mid <= 0:
        raise ValueError("rango v1 o precio de referencia inválido")
    if str(strategy).lower() != "bot" and not low < market_mid < high:
        return float(np.sqrt(low * high))
    return market_mid


def scale_v1_geometry(low, high, window_open, reference_price):
    """Escala piso/techo absolutos de hoy por open[0]/ref (v1 3236-3250)."""
    low, high = float(low), float(high)
    window_open, reference_price = float(window_open), float(reference_price)
    if low <= 0 or high <= low or window_open <= 0 or reference_price <= 0:
        raise ValueError("geometría o precios v1 inválidos")
    scale = window_open / reference_price
    return low * scale, high * scale


def _v1_baseline(data, window_indexes, *, piece_spec, low, high, reference_price,
        filters, capital_initial, meta, max_loss_pct, fee_pct, calibration_timeline):
    """Reproduce 23 replays v1 con geometría de hoy reescalada al open histórico."""
    from grid.sim.data import CandleData
    from grid.sim.meta_portfolio import evaluate_portfolio, simulate_piece_curve
    from grid.levels import GridConfigError, compute_lines, plan_cells
    from research.scenarios.evaluate import causal_sigma_24h

    strategy, levels, _width, piece_capital = piece_spec
    horizon_bars = HORIZON_DAYS * 288
    timestamps = np.asarray(data.timestamp, dtype=np.int64)
    windows, scaled_lows, scaled_highs, sigma_values = [], [], [], []
    dates, calibrations = calibration_timeline
    excluded = 0
    for index in window_indexes:
        index = int(index)
        end = index + horizon_bars
        if end > len(timestamps) or not np.all(np.diff(timestamps[index:end]) == 300):
            excluded += 1
            continue
        window_low, window_high = scale_v1_geometry(
            low, high, data.open[index], reference_price)
        try:
            if strategy == "bot":
                fee = float(fee_pct) / 100
                qty = filters.round_qty_down(Decimal(str(piece_capital)) /
                    (Decimal(str(window_low)) * (1 + Decimal(str(fee)))))
                sell_qty = filters.round_qty_down(qty * (1 - Decimal(str(fee))))
                if (qty < filters.min_qty or qty * Decimal(str(window_low)) < filters.min_notional or
                        sell_qty < filters.min_qty or sell_qty * Decimal(str(window_high)) < filters.min_notional or
                        (window_high / window_low - 1) * 100 - 2 * fee_pct < .3):
                    raise ValueError("bot no pasa filtros o margen mínimo")
            else:
                lines = compute_lines(str(window_low), str(window_high), int(levels), filters)
                plan_cells(lines, str(piece_capital),
                    {"bid_price": str(float(data.open[index]) * (1 - 1e-8)),
                     "ask_price": str(float(data.open[index]) * (1 + 1e-8)),
                     "avg_price": str(float(data.open[index]))}, filters,
                    {"grid_min_step_pct": .003, "capital_max_por_nivel_pct": .30})
        except (GridConfigError, ValueError, ArithmeticError):
            excluded += 1
            continue
        windows.append(CandleData(timestamps[index:end], data.open[index:end],
            data.high[index:end], data.low[index:end], data.close[index:end], 0))
        scaled_lows.append(window_low)
        scaled_highs.append(window_high)
        calibration_index = int(np.searchsorted(dates, timestamps[index], side="left")) - 1
        calibration = float(calibrations[calibration_index]) if calibration_index >= 0 else 1.0
        sigma_values.append(causal_sigma_24h(data.close, timestamps, index, calibration))
    if not windows:
        return {"n": 0, "excluded_windows": excluded,
            "p_target": None, "p_loss": None}
    outcomes = []
    for window, window_low, window_high, sigma24 in zip(
            windows, scaled_lows, scaled_highs, sigma_values):
        try:
            result = simulate_piece_curve(window,
                strategy="individual_bot" if strategy == "bot" else strategy,
                n=int(levels), capital=float(piece_capital), low=window_low,
                high=window_high, filters=filters, sigma_values=(
                    np.full(len(window.close), sigma24, dtype=np.float64)
                    if strategy == "smart" else None), fee_pct=fee_pct)
            outcomes.append(evaluate_portfolio([result["curve"]],
                capital_initial=float(capital_initial), target_usdt=float(meta),
                max_loss_pct=float(max_loss_pct)))
        except (ValueError, ArithmeticError, GridConfigError):
            excluded += 1
    if not outcomes:
        return {"n": 0, "excluded_windows": excluded,
            "p_target": None, "p_loss": None}
    return {"n": len(outcomes), "excluded_windows": excluded,
        "p_target": float(np.mean([row["reached"] for row in outcomes])),
        "p_loss": float(np.mean([row["loss_cut"] for row in outcomes]))}


def _run_actual_piece(data, piece_spec, low, high, filters, fee_pct, sigma24,
        *, capital_initial, meta, max_loss_pct):
    """Mide una ventana real con la geometría absoluta que se pronosticó en t."""
    from grid.sim.meta_portfolio import evaluate_portfolio, simulate_piece_curve
    strategy, levels, _width, piece_capital = piece_spec
    result = simulate_piece_curve(data,
        strategy="individual_bot" if strategy == "bot" else strategy,
        n=int(levels), capital=float(piece_capital), low=float(low), high=float(high),
        filters=filters, sigma_values=(np.full(len(data.close), sigma24)
            if strategy == "smart" else None), fee_pct=fee_pct)
    return evaluate_portfolio([result["curve"]], capital_initial=float(capital_initial),
        target_usdt=float(meta), max_loss_pct=float(max_loss_pct))


def _brier(probability, outcome):
    p, y = np.asarray(probability, float), np.asarray(outcome, float)
    return float(np.mean((p - y) ** 2)) if len(p) else None


def _bootstrap_by_date(rows, value_fn, *, seed=43, replicas=1000):
    dates = sorted({row["date"] for row in rows})
    if not dates:
        return [None, None]
    groups = {date: [row for row in rows if row["date"] == date] for date in dates}
    rng = np.random.default_rng(seed)
    estimates = []
    for _ in range(int(replicas)):
        sample = rng.choice(dates, size=len(dates), replace=True)
        picked = [row for date in sample for row in groups[date]]
        value = value_fn(picked)
        if value is not None and np.isfinite(value):
            estimates.append(float(value))
    if not estimates:
        return [None, None]
    return [float(np.quantile(estimates, .025)), float(np.quantile(estimates, .975))]


def baseline_window_indices(timestamps, target_ts, *, horizon_days=20, step_days=7, count=23):
    """Selecciona inicios con ventana completa y espaciados hacia atras desde t."""
    stamps = np.asarray(timestamps, dtype=np.int64)
    start_limit = int(target_ts) - int(horizon_days) * 86400
    step = int(step_days) * 86400
    selected = []
    for offset in range(int(count)):
        start_ts = start_limit - offset * step
        index = int(np.searchsorted(stamps, start_ts, side="left"))
        if index < len(stamps) and stamps[index] == start_ts:
            selected.append(index)
    return np.asarray(sorted(set(selected)), dtype=np.int64)


def summarize_validation(pairs, *, seed=43, replicas=1000):
    """Calcula cobertura, PIT, sesgo, Brier y calibracion sobre pares guardados."""
    rows = list(pairs)
    if not rows:
        return {"n": 0, "estado": "sin_pares"}
    predicted = np.asarray([row["predicted_pnl"] for row in rows], dtype=float)
    actual = np.asarray([row["actual_pnl"] for row in rows], dtype=float)
    target = np.asarray([row["actual_target"] for row in rows], dtype=float)
    p2_all = np.asarray([row["v2_p_target"] for row in rows], dtype=float)
    actual_loss = np.asarray([row["actual_loss"] for row in rows], dtype=float)
    lows = np.asarray([row["predicted_p10"] for row in rows], dtype=float)
    highs = np.asarray([row["predicted_p90"] for row in rows], dtype=float)
    coverage = float(np.mean((actual >= lows) & (actual <= highs)))
    pit = np.mean(actual[:, None] >= np.asarray([row["predicted_samples"] for row in rows]), axis=1)
    histogram, _ = np.histogram(pit, bins=np.linspace(0, 1, 11))
    expected = len(rows) / 10
    chi2 = float(np.sum((histogram - expected) ** 2 / expected)) if expected else None
    bias = float(np.mean(predicted - actual))
    bias_ci = _bootstrap_by_date(rows,
        lambda sample: float(np.mean([r["predicted_pnl"] - r["actual_pnl"] for r in sample])),
        seed=seed, replicas=replicas)
    paired_rows = [row for row in rows if row.get("v1_p_target") is not None
        and row.get("v1_p_loss") is not None]
    p2_paired = np.asarray([row["v2_p_target"] for row in paired_rows], dtype=float)
    p1 = np.asarray([row["v1_p_target"] for row in paired_rows], dtype=float)
    target_paired = np.asarray([row["actual_target"] for row in paired_rows], dtype=float)
    p2_loss = np.asarray([row["v2_p_loss"] for row in paired_rows], dtype=float)
    p1_loss = np.asarray([row["v1_p_loss"] for row in paired_rows], dtype=float)
    loss_paired = np.asarray([row["actual_loss"] for row in paired_rows], dtype=float)
    brier_v2 = _brier(p2_paired, target_paired)
    brier_v1 = _brier(p1, target_paired)
    delta_ci = _bootstrap_by_date(paired_rows,
        lambda sample: _brier([r["v2_p_target"] for r in sample],
            [r["actual_target"] for r in sample]) - _brier(
            [r["v1_p_target"] for r in sample], [r["actual_target"] for r in sample]),
        seed=seed + 1, replicas=replicas)
    calibration = []
    for lo, hi in zip(np.linspace(0, 1, 6)[:-1], np.linspace(0, 1, 6)[1:]):
        mask = (p2_all >= lo) & ((p2_all < hi) if hi < 1 else (p2_all <= hi))
        if np.any(mask):
            calibration.append({"predicted_mean": float(p2_all[mask].mean()),
                "observed_mean": float(target[mask].mean()), "n": int(mask.sum())})
    interval_ok = delta_ci[0] is not None and delta_ci[0] <= 0
    passes = (len(paired_rows) > 0 and .70 <= coverage <= .90 and
        brier_v2 <= brier_v1 and interval_ok)
    return {"n": len(rows), "coverage_p10_p90": coverage,
        "pit_deciles": histogram.astype(int).tolist(), "pit_chi2": chi2,
        "pnl_bias_predicted_minus_actual": bias, "pnl_bias_ci95": bias_ci,
        "brier_target_v2": brier_v2, "brier_target_v1": brier_v1,
        "brier_target_delta_v2_minus_v1_ci95": delta_ci,
        "brier_loss_v2": _brier(p2_loss, loss_paired),
        "brier_loss_v1": _brier(p1_loss, loss_paired),
        "v1_pairs": len(paired_rows), "v1_excluded_pairs": len(rows) - len(paired_rows),
        "target_calibration_5_bins": calibration,
        "verdict": "pasa" if passes else "no pasa"}


def _prediction_piece(symbol, spec):
    strategy, levels, width, capital = spec
    piece = {"symbol": symbol, "strategy": strategy, "n": levels, "capital": capital}
    if strategy == "bot":
        piece.update(buy_offset_pct=-1.5, sell_offset_pct=1.5)
    else:
        piece["width_sigma"] = width
    return piece


def validate_scenarios(*, db_path="market_data.db", months=12, step_days=7,
        symbols=None, commission=.1, n_scenarios=1000, n_smart=200,
        profile="RAPIDO", capital=100.0, meta=3.0,
        library_dir="data/derived/scenario_lib", seed=43, replicas=1000,
        progress=None, evaluation_timestamp=None, strategies=None):
    """Evalua distribuciones FHS contra la realidad y baseline v1 de 23 ventanas.

    Cada prediccion usa evaluate_pieces(before=t). La realidad es el siguiente
    horizonte real; la referencia v1 usa hasta 23 ventanas de 20 dias que
    terminan antes de t. `progress` recibe (fecha_actual, total_fechas).
    """
    if months < 1 or step_days < 1 or n_scenarios < 1 or n_smart < 1:
        raise ValueError("meses, paso y escenarios deben ser positivos")
    from grid.meta import DEFAULT_PROFILES
    from research.scenarios.evaluate import _calibration_factor_timeline
    store = CandleStore(market_db_path(db_path))
    if symbols:
        selected = [str(item).upper() for item in symbols]
    else:
        with store._connect() as conn:
            selected = [row[0] for row in conn.execute(
                "SELECT symbol FROM symbols WHERE active=1 AND role='operar' ORDER BY symbol")]
    now_rows = {}
    dates = set()
    for symbol in selected:
        data = store.load(symbol, "5m")
        stamps = np.asarray(data.timestamp, dtype=np.int64)
        first = max(30 * 288 + 1, int(np.searchsorted(stamps, stamps[-1] - months * 30 * 86400)))
        last = len(stamps) - HORIZON_DAYS * 288
        if evaluation_timestamp is None:
            indices = np.arange(first, last + 1, step_days * 288, dtype=int)
        else:
            at = int(np.searchsorted(stamps, int(evaluation_timestamp), side="left"))
            indices = (np.asarray([at], dtype=int) if at < len(stamps) and
                int(stamps[at]) == int(evaluation_timestamp) and first <= at <= last
                else np.empty(0, dtype=int))
        now_rows[symbol] = (data, indices)
        dates.update(int(stamps[index]) for index in indices)
    all_dates = sorted(dates)
    output = {"horizon_days": HORIZON_DAYS, "months": months, "step_days": step_days,
        "n_scenarios": n_scenarios, "n_smart": n_smart, "symbols": {}, "pairs": []}
    selected_strategies = None if strategies is None else set(strategies)
    selected_specs = tuple(spec for spec in DEFAULT_PIECES
        if selected_strategies is None or spec[0] in selected_strategies)
    by_geometry = {}
    for date_index, stamp in enumerate(all_dates):
        date_key = datetime.fromtimestamp(stamp, timezone.utc).date().isoformat()
        for symbol, (data, indices) in now_rows.items():
            at = int(np.searchsorted(data.timestamp, stamp))
            if at not in indices or at + HORIZON_DAYS * 288 > len(data.close):
                continue
            specs = selected_specs
            if not specs:
                continue
            pieces = [_prediction_piece(symbol, spec) for spec in specs]
            prediction = evaluate_pieces(pieces, perfil=profile, capital=capital, meta=meta,
                n_scenarios=n_scenarios, n_smart=n_smart, seed=seed + stamp,
                before=stamp, db_path=db_path, library_dir=library_dir,
                mode="alternativas", fee_pct=commission)
            baseline_indexes = baseline_window_indices(data.timestamp, stamp)
            calibration_timeline = _calibration_factor_timeline(store.path, symbol)
            profile_settings = DEFAULT_PROFILES[profile]
            price_now = float(data.close[at - 1]) if at > 0 else float(data.open[at])
            for piece_spec, predicted_piece in zip(specs, prediction["pieces"]):
                from grid.sim.runner import FILTERS
                strategy, levels, width_sigma, _piece_capital = piece_spec
                sigma_today = float(prediction["sigma_today_24h"][symbol])
                if strategy == "bot":
                    today_low, today_high = price_now * .985, price_now * 1.015
                else:
                    width = float(width_sigma) * sigma_today * math.sqrt(HORIZON_DAYS)
                    today_low, today_high = price_now * math.exp(-width), price_now * math.exp(width)
                ref_price = v1_reference_price(today_low, today_high, price_now,
                    strategy="bot" if strategy == "bot" else strategy)
                baseline = _v1_baseline(data, baseline_indexes, piece_spec=piece_spec,
                    low=today_low, high=today_high, reference_price=ref_price,
                    filters=store.filters(symbol) or FILTERS, capital_initial=capital,
                    meta=meta, max_loss_pct=profile_settings["max_loss_pct"],
                    fee_pct=commission, calibration_timeline=calibration_timeline)
                end = at + HORIZON_DAYS * 288
                real_data = type(data)(np.asarray(data.timestamp[at:end]),
                    np.asarray(data.open[at:end]), np.asarray(data.high[at:end]),
                    np.asarray(data.low[at:end]), np.asarray(data.close[at:end]), 0)
                real_result = _run_actual_piece(real_data, piece_spec, today_low,
                    today_high, store.filters(symbol) or FILTERS, commission,
                    sigma_today, capital_initial=capital, meta=meta,
                    max_loss_pct=profile_settings["max_loss_pct"])
                actual = real_result
                samples = np.asarray([row["pnl_usdt"] for row in predicted_piece["scenario_results"]], float)
                if not len(samples):
                    continue
                pair = {"symbol": symbol, "date": date_key, "geometry": f"{piece_spec[0]}_n{piece_spec[1]}_w{piece_spec[2]}",
                    "predicted_samples": samples.tolist(), "predicted_p10": float(np.quantile(samples, .1)),
                    "predicted_p90": float(np.quantile(samples, .9)), "predicted_pnl": float(samples.mean()),
                    "v2_p_target": float(np.mean([row["reached"] for row in predicted_piece["scenario_results"]])),
                    "v2_p_loss": float(np.mean([row["loss_cut"] for row in predicted_piece["scenario_results"]])),
                    "v1_p_target": baseline["p_target"], "v1_p_loss": baseline["p_loss"],
                    "actual_pnl": float(actual["pnl_usdt"]), "actual_target": float(actual["reached"]),
                    "actual_loss": float(actual["loss_cut"]), "v1_windows": baseline["n"],
                    "v1_excluded_windows": baseline["excluded_windows"]}
                output["pairs"].append(pair)
                by_geometry.setdefault(pair["geometry"], []).append(pair)
        if progress and ((date_index + 1) % 10 == 0 or date_index + 1 == len(all_dates)):
            progress(date_index + 1, len(all_dates))
    output["summary"] = {key: summarize_validation(rows, seed=seed, replicas=replicas)
        for key, rows in by_geometry.items()}
    output["summary"]["agregado"] = summarize_validation(
        output["pairs"], seed=seed, replicas=replicas)
    return output
