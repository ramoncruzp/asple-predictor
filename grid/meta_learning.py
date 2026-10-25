"""Etiquetas causales para el aprendizaje en sombra del operador Meta."""

from __future__ import annotations

from datetime import datetime, timezone
import numpy as np


HOURS_72 = 72
BACKFILL_DAYS = 180


def _utc_seconds(value):
    if isinstance(value, (int, float, np.integer, np.floating)):
        return int(value)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.astimezone(timezone.utc).timestamp())


def non_overlapping_72h_starts(timestamps, *, start=None, end=None):
    """Devuelve inicios alineados a UTC cada 72 h; los bloques no se solapan."""
    times = np.asarray(timestamps, dtype=np.int64)
    if not len(times):
        return []
    lo = int(times[0]) if start is None else _utc_seconds(start)
    hi = int(times[-1]) if end is None else _utc_seconds(end)
    step = HOURS_72 * 3600
    cursor = ((lo + step - 1) // step) * step
    return list(range(cursor, hi - step + 1, step))


def proportional_target_usdt(target_usdt, profile_days, horizon_hours=HOURS_72):
    duration_h = max(1.0, float(profile_days) * 24.0)
    return float(target_usdt) * min(1.0, max(0.0, float(horizon_hours) / duration_h))


def make_learning_label(*, horizon, net_advance_usdt, target_usdt,
                        profile_days, capital_usdt, reached_target=None,
                        elapsed_hours=None, drawdown_pct=None):
    """Forma una etiqueta inmutable; el avance neto y la meta parcial son explícitos."""
    horizon = str(horizon)
    if horizon == "72h":
        required = proportional_target_usdt(target_usdt, profile_days, 72)
        reached = (float(net_advance_usdt) >= required if reached_target is None
                   else bool(reached_target))
    else:
        required = float(target_usdt)
        reached = (float(net_advance_usdt) >= required if reached_target is None
                   else bool(reached_target))
    capital = max(0.0, float(capital_usdt))
    return {
        "horizon": horizon,
        "net_advance_usdt": float(net_advance_usdt),
        "net_advance_pct": (float(net_advance_usdt) / capital * 100.0) if capital else None,
        "required_progress_usdt": required,
        "reached": bool(reached),
        "elapsed_hours": None if elapsed_hours is None else float(elapsed_hours),
        "drawdown_pct": None if drawdown_pct is None else float(drawdown_pct),
        "profile_days": int(profile_days),
        "target_usdt": float(target_usdt),
    }


def causal_replay_probability(candles_by_symbol, *, at, parameters, lookback_days=BACKFILL_DAYS):
    """Estima p_replay solo con simulaciones de bloques previos ya terminados."""
    at_s = _utc_seconds(at)
    days = max(1, int(parameters.get("profile_days", 20)))
    width = days * 86400
    earliest = max(at_s - int(lookback_days) * 86400,
        max((int(candles.timestamp[0]) for candles in candles_by_symbol.values()), default=at_s))
    starts = range(at_s - width, earliest - 1, -width)
    outcomes = []
    label_key = f"{days}d"
    for start_s in starts:
        labels = replay_alternative_labels(candles_by_symbol, start_ts=start_s,
            parameters=parameters, include_horizons=(label_key,))
        label = labels.get(label_key, {})
        if label.get("status") == "labeled":
            outcomes.append(bool(label.get("reached")))
    return {"probability": (sum(outcomes) / len(outcomes)) if outcomes else None,
            "independent_windows": len(outcomes), "successes": sum(outcomes),
            "window_days": days, "through_ts": at_s}


def point_in_time_signals(*, at, candles, buy_price=None, sell_price=None,
                          volatility_forecasts=(), direction_predictions=()):
    """Reconstruye solo señales cuya fuente tiene timestamp <= instante de decisión."""
    at_s = _utc_seconds(at)
    times = np.asarray(candles.timestamp, dtype=np.int64)
    closes = np.asarray(candles.close, dtype=float)
    highs = np.asarray(candles.high, dtype=float)
    lows = np.asarray(candles.low, dtype=float)
    end = int(np.searchsorted(times, at_s, side="right"))
    prior_times, prior_closes = times[:end], closes[:end]
    result = {"signals": {}, "provenance": {}}

    # Volatilidad realizada usa exclusivamente cierres del día anterior al corte.
    cutoff = at_s - 24 * 3600
    lo = int(np.searchsorted(prior_times, cutoff, side="left"))
    trail = prior_closes[lo:]
    if len(trail) >= 2 and np.all(trail > 0):
        returns = np.diff(np.log(trail))
        result["signals"]["realized_volatility_24h"] = float(np.sqrt(np.sum(returns ** 2)))
        result["provenance"]["realized_volatility_24h"] = {
            "source": "velas previas", "through_ts": int(prior_times[-1])}

    def dated(row, names):
        for name in names:
            if row.get(name) is not None:
                try:
                    return _utc_seconds(row[name])
                except (TypeError, ValueError, OverflowError):
                    return None
        return None

    forecasts_by_model = {}
    for row in volatility_forecasts:
        observed_at = dated(row, ("made_at", "forecast_at"))
        if observed_at is None or observed_at > at_s:
            continue
        key = (str(row.get("model_name") or ""), int(row.get("horizon_h") or 0))
        if key not in forecasts_by_model or observed_at > forecasts_by_model[key][0]:
            forecasts_by_model[key] = (observed_at, row)
    if forecasts_by_model:
        forecasts = []
        provenance = []
        for (_model, _horizon), (observed_at, row) in sorted(forecasts_by_model.items()):
            forecast = {"model": row.get("model_name"), "value": row.get("pred_logvol_cal"),
                "sigma": (float(np.exp(row["pred_logvol_cal"]))
                    if row.get("pred_logvol_cal") is not None else None),
                "horizon_h": row.get("horizon_h")}
            forecasts.append(forecast)
            provenance.append({"source": "vol_forecasts", "forecast_at": row.get("forecast_at"),
                "made_at": row.get("made_at"), "effective_at_ts": observed_at})
        result["signals"]["volatility_forecasts"] = forecasts
        result["provenance"]["volatility_forecasts"] = provenance

    predictions_by_model = {}
    for row in direction_predictions:
        observed_at = dated(row, ("predicted_at",))
        if observed_at is None or observed_at > at_s:
            continue
        key = (str(row.get("model_name") or ""), str(row.get("interval") or ""))
        if key not in predictions_by_model or observed_at > predictions_by_model[key][0]:
            predictions_by_model[key] = (observed_at, row)
    if predictions_by_model:
        predictions, provenance = [], []
        for (_model, _interval), (observed_at, row) in sorted(predictions_by_model.items()):
            predictions.append({"signal": row.get("signal"),
                "probability_up": row.get("probability_up"), "model": row.get("model_name"),
                "interval": row.get("interval")})
            provenance.append({"source": "predictions", "predicted_at": row.get("predicted_at"),
                "effective_at_ts": observed_at})
        result["signals"]["direction_predictions"] = predictions
        result["provenance"]["direction_predictions"] = provenance

    # No se reutiliza la tasa de toques retrospectiva de 40b-5: se deriva solo
    # del tramo anterior al corte y la geometría relativa de la alternativa.
    if end and buy_price is not None and sell_price is not None and float(buy_price) < float(sell_price):
        touch_start = int(np.searchsorted(times, at_s - 30 * 86400, side="left"))
        from grid.sim.data import CandleData
        from grid.sim.touch_stats import touch_stats
        touch_sample = CandleData(timestamp=times[touch_start:end],
            open=np.asarray(candles.open, dtype=float)[touch_start:end],
            high=highs[touch_start:end], low=lows[touch_start:end],
            close=closes[touch_start:end], gaps=0)
        stats = touch_stats(touch_sample, float(buy_price), float(sell_price),
                            min(24 * 12, max(1, len(touch_sample.timestamp) - 1)))
        if stats.get("sell_rate") is not None:
            result["signals"]["touch_buy_to_sell"] = float(stats["sell_rate"])
            result["provenance"]["touch_buy_to_sell"] = {
                "source": "velas previas", "lookback_days": 30,
                "through_ts": int(prior_times[-1]), "stats": stats}
    return result


def reconstruct_historical_samples(templates, *, candles_by_symbol, source_rows_by_symbol,
                                   now=None, lookback_days=BACKFILL_DAYS):
    """Reconstruye snapshots contrafactuales; cada feature queda cortada en su fecha.

    La geometría sirve solo como plantilla de la alternativa. Valores de Scanner,
    Advisor y break_prob no se inventan si no hay un registro fechado anterior.
    """
    now_s = _utc_seconds(now or datetime.now(timezone.utc))
    earliest = now_s - int(lookback_days) * 86400
    output = []
    seen = set()
    for template in templates:
        params = template["parameters"]
        pieces = list(params.get("pieces") or [])
        if not pieces:
            continue
        primary = str(pieces[0].get("symbol") or "").upper()
        primary_candles = candles_by_symbol.get(primary)
        if primary_candles is None:
            continue
        starts = non_overlapping_72h_starts(primary_candles.timestamp,
            start=max(earliest, int(primary_candles.timestamp[0])), end=now_s)
        for start_s in starts:
            key = (primary, params.get("profile"), tuple(sorted(
                (str(p.get("symbol")), str(p.get("piece_type")), str(p.get("strategy")),
                 int(p.get("n_levels", 1)), round(float(p.get("floor", 0)) /
                     max(float(p.get("mid_at_plan") or 1), 1e-12), 4),
                 round(float(p.get("ceiling", 0)) /
                     max(float(p.get("mid_at_plan") or 1), 1e-12), 4)) for p in pieces)), start_s)
            if key in seen:
                continue
            seen.add(key)
            rebuilt_pieces, snapshots = [], {}
            valid = True
            for piece in pieces:
                symbol = str(piece.get("symbol") or "").upper()
                candles = candles_by_symbol.get(symbol)
                if candles is None:
                    valid = False
                    break
                times = np.asarray(candles.timestamp, dtype=np.int64)
                idx = int(np.searchsorted(times, start_s, side="right")) - 1
                if idx < 0 or idx + 1 >= len(times):
                    valid = False
                    break
                ref = float(candles.close[idx])
                old_ref = float(piece.get("mid_at_plan") or ref)
                floor_ratio = float(piece.get("floor", piece.get("range_low", 0))) / old_ref
                ceiling_ratio = float(piece.get("ceiling", piece.get("range_high", 0))) / old_ref
                rebuilt = {**piece, "floor": floor_ratio * ref, "ceiling": ceiling_ratio * ref,
                           "mid_at_plan": ref}
                rebuilt_pieces.append(rebuilt)
                forecasts, predictions = source_rows_by_symbol.get(symbol, ([], []))
                snapshots[symbol] = point_in_time_signals(at=start_s, candles=candles,
                    buy_price=rebuilt["floor"], sell_price=rebuilt["ceiling"],
                    volatility_forecasts=forecasts, direction_predictions=predictions)
            if not valid:
                continue
            historical_params = {**params, "pieces": rebuilt_pieces}
            baseline = causal_replay_probability(candles_by_symbol, at=start_s,
                                                 parameters=historical_params,
                                                 lookback_days=lookback_days)
            output.append({"template_sample_key": template.get("sample_key"),
                "profile": str(params.get("profile", "RAPIDO")).upper(),
                "symbol": primary, "sample_at": start_s, "parameters": historical_params,
                "base_probability": baseline["probability"],
                "signals": {"point_in_time_by_symbol": snapshots,
                    "omitted": ["scanner", "advisor", "break_prob"]},
                "provenance": {"source_kind": "historical_reconstructed",
                    "signal_cutoff_ts": start_s, "template_sample_key": template.get("sample_key"),
                    "base_replay": baseline,
                    "template_is_counterfactual": True,
                    "note": "Señales reconstruidas solo con registros y velas anteriores al corte."}})
    return output


def replay_alternative_labels(candles_by_symbol, *, start_ts, parameters, include_horizons=None):
    """Simula la misma cartera desde un corte y crea etiquetas 72 h y al plazo.

    `parameters` es la instantánea persistida en la evaluación: perfil, capital,
    objetivo, pérdida máxima y piezas. Cada pieza conserva su geometría relativa.
    """
    from grid.sim.data import CandleData
    from grid.sim.meta_bot import run_meta_bot
    from grid.sim.runner import run_simulation

    start_s = _utc_seconds(start_ts)
    profile_days = max(1, int(parameters.get("profile_days", 20)))
    horizons = {"72h": 72, f"{profile_days}d": profile_days * 24}
    if include_horizons is not None:
        allowed = {str(value) for value in include_horizons}
        horizons = {key: value for key, value in horizons.items() if key in allowed}
    total_capital = max(0.0, float(parameters.get("capital_usdt", 0)))
    target = max(0.0, float(parameters.get("target_usdt", 0)))
    pieces = list(parameters.get("pieces") or [])
    if not pieces or total_capital <= 0:
        return {name: {"status": "insufficient", "reason": "sin piezas o capital"}
                for name in horizons}
    result = {}
    for label_horizon, hours in horizons.items():
        end_s = start_s + int(hours) * 3600
        samples = {}
        immature = False
        for piece in pieces:
            symbol = str(piece.get("symbol") or "").upper()
            candles = candles_by_symbol.get(symbol)
            if candles is None:
                immature = True
                break
            timestamps = np.asarray(candles.timestamp, dtype=np.int64)
            lo = int(np.searchsorted(timestamps, start_s, side="left"))
            hi = int(np.searchsorted(timestamps, end_s, side="right"))
            if (hi - lo < 2 or int(timestamps[lo]) > start_s + 300
                    or int(timestamps[hi - 1]) < end_s - 300):
                immature = True
                break
            samples[symbol] = CandleData(timestamp=timestamps[lo:hi],
                open=np.asarray(candles.open)[lo:hi], high=np.asarray(candles.high)[lo:hi],
                low=np.asarray(candles.low)[lo:hi], close=np.asarray(candles.close)[lo:hi], gaps=0)
        if immature:
            result[label_horizon] = {"status": "immature", "required_until": end_s}
            continue
        net_advance, drawdowns = 0.0, []
        piece_paths = []
        required_total = proportional_target_usdt(target, profile_days, hours)
        for piece in pieces:
            capital = float(piece.get("capital_usdt", 0))
            if capital <= 0:
                continue
            piece_target = required_total * capital / total_capital
            path = {}
            trace = lambda row, path=path: path.__setitem__(int(row["timestamp"]),
                float(row["equity"]) - capital)
            sample = samples[str(piece.get("symbol") or "").upper()]
            ref = float(piece.get("mid_at_plan") or piece.get("reference_price") or sample.open[0])
            scale = float(sample.open[0]) / ref if ref > 0 else 1.0
            floor = float(piece.get("floor", piece.get("range_low", 0))) * scale
            ceiling = float(piece.get("ceiling", piece.get("range_high", 0))) * scale
            if floor <= 0 or ceiling <= floor:
                continue
            if piece.get("piece_type") == "individual_bot_grid":
                replay = run_meta_bot(sample, capital=capital, buy_price=floor,
                    sell_price=ceiling, target_usdt=piece_target, fee_pct=.1,
                    max_loss_pct=parameters.get("max_loss_pct"), min_margin_pct=.3,
                    trace_callback=trace)
                net_advance += float(replay.get("pnl_usdt", 0))
                drawdowns.append(float(replay.get("drawdown_pct", 0)))
                piece_paths.append(path)
            else:
                sim = run_simulation(sample, strategy=piece.get("strategy", "simple"),
                    n=max(4, int(piece.get("n_levels", 4))), capital=capital,
                    low=floor, high=ceiling, width_pct=None,
                    params={"target_usdt": piece_target, "target_basis": "cash"},
                    fee_pct=.1, close_wait_enabled=True, adjust_enabled_global=False,
                    stoploss_enabled=False, meta_max_loss_pct=parameters.get("max_loss_pct"),
                    trace_callback=trace, resync_candles=1)
                metrics = sim.get("metrics", {})
                if any(event.get("type") == "META_LOSS_CUT" for event in sim.get("events", [])):
                    ending = float(metrics.get("cash_final", capital))
                else:
                    ending = float(metrics.get("equity_final_after_repository_liquidation", capital))
                net_advance += ending - capital
                drawdowns.append(float(metrics.get("max_drawdown_pct", 0)))
                piece_paths.append(path)
        portfolio_path = {}
        for path in piece_paths:
            for timestamp, pnl in path.items():
                portfolio_path[timestamp] = portfolio_path.get(timestamp, 0.0) + pnl
        crossings = [timestamp for timestamp, pnl in portfolio_path.items()
                     if pnl >= required_total]
        max_advance = max(portfolio_path.values(), default=net_advance)
        result[label_horizon] = make_learning_label(horizon=label_horizon,
            net_advance_usdt=net_advance, target_usdt=target,
            profile_days=profile_days, capital_usdt=total_capital,
            reached_target=bool(crossings),
            elapsed_hours=((min(crossings) - start_s) / 3600) if crossings else None,
            drawdown_pct=max(drawdowns, default=0.0))
        result[label_horizon]["reached"] = bool(crossings)
        result[label_horizon]["max_advance_usdt"] = float(max_advance)
        result[label_horizon]["status"] = "labeled"
        result[label_horizon]["sample_start"] = start_s
        result[label_horizon]["sample_end"] = end_s
    return result

