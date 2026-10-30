"""Evaluación offline de piezas sobre escenarios FHS compartidos."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np

from data.market_store import CandleStore, market_db_path
from grid.meta import DEFAULT_PROFILES
from grid.sim.data import CandleData
from research.scenarios.fastsim import simulate_bot, simulate_simple
from research.scenarios.generator import generate
from research.scenarios.metrics import summarize, summarize_portfolio
from research.scenarios.smart_exact import START_TS, evaluate_smart_scenarios, paired_smart_simple


def _log_width_from_sigma_24h(sigma_24h, horizon_days, multiplier):
    """Convierte sigma logarítmica acumulada de 24 h a un horizonte en días."""
    return float(multiplier) * float(sigma_24h) * np.sqrt(float(horizon_days))


def causal_sigma_24h(close, timestamp, upto_index=None, calibration=1.0):
    """Sigma EWMA logarítmica de 24 h con datos estrictamente anteriores al índice."""
    close = np.asarray(close, dtype=np.float64)
    stamps = np.asarray(timestamp, dtype=np.int64)
    stop = len(close) if upto_index is None else min(int(upto_index), len(close))
    if stop < 30 * 288 + 1:
        raise ValueError("faltan 30 días para estimar volatilidad causal")
    returns = np.diff(np.log(close[:stop]))
    if len(stamps) < stop:
        raise ValueError("timestamps y precios desalineados")
    lam = .97 ** (1 / 12)
    variance = float(np.mean(returns[:12 * 24 * 30] ** 2))
    for value in returns[12 * 24 * 30:]:
        variance = lam * variance + (1 - lam) * float(value * value)
    return float(np.sqrt(max(variance, 1e-16) * 288) * float(calibration))


def _sigma_estimates(data, *, upto_index=None):
    close = np.asarray(data.close, dtype=np.float64)
    stop = len(close) if upto_index is None else min(int(upto_index), len(close))
    returns = np.diff(np.log(close[:stop]))
    sigma_24h = causal_sigma_24h(close, data.timestamp, stop)
    daily = []
    days = np.asarray(data.timestamp[1:stop] // 86400)
    for day in np.unique(days)[-180:]:
        values = returns[days == day]
        if len(values) == 288:
            daily.append(float(np.sqrt(np.sum(values * values))))
    return sigma_24h, float(np.median(daily)) if daily else 0.0


def _calibration_factors(path, symbols, *, before=None):
    factors = {}
    try:
        path = Path(path).expanduser().resolve()
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"scorecard_runs", "calibration_factors"} <= tables:
                return factors
            for symbol in symbols:
                sql = ("SELECT f.factor FROM calibration_factors f JOIN scorecard_runs r "
                    "ON r.id=f.run_id WHERE r.status='completed' AND f.symbol=? AND f.kind='sigma' "
                    "AND f.horizon_h=24")
                params = [symbol]
                if before is not None:
                    cutoff = float(before) if isinstance(before, (float, int)) else _to_timestamp(before)
                    sql += " AND julianday(r.finished_at) < julianday(?)"
                    params.append(cutoff / 86400 + 2440587.5)
                sql += " ORDER BY r.finished_at DESC,f.computed_at DESC LIMIT 1"
                row = conn.execute(sql, params).fetchone()
                if row and row[0] is not None and float(row[0]) > 0:
                    factors[symbol] = float(row[0])
    except sqlite3.Error:
        return factors
    return factors


def _calibration_factor_timeline(path, symbol):
    """Carga una vez factores verificados para seleccionarlos causalmente por fecha."""
    try:
        path = Path(path).expanduser().resolve()
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"scorecard_runs", "calibration_factors"} <= tables:
                return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.float64)
            rows = conn.execute("SELECT r.finished_at,f.factor FROM calibration_factors f "
                "JOIN scorecard_runs r ON r.id=f.run_id WHERE r.status='completed' "
                "AND f.symbol=? AND f.kind='sigma' AND f.horizon_h=24 "
                "AND f.factor>0 ORDER BY r.finished_at,f.computed_at", (symbol,)).fetchall()
        dates, factors = [], []
        for stamp, factor in rows:
            try:
                dates.append(_to_timestamp(stamp))
                factors.append(float(factor))
            except (TypeError, ValueError):
                continue
        return np.asarray(dates, dtype=np.int64), np.asarray(factors, dtype=np.float64)
    except (OSError, sqlite3.Error):
        return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.float64)


def _to_timestamp(value):
    from datetime import datetime, timezone
    if isinstance(value, (int, float)):
        return int(value)
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return int(stamp.timestamp())


def _remove_log_trend(bars):
    """Quita pendiente OLS del log-cierre y conserva el primer precio exacto."""
    close = np.asarray(bars["close"], dtype=np.float64)
    x = np.arange(close.shape[-1], dtype=np.float64)
    x = x - x.mean()
    logs = np.log(close)
    slope = np.sum((logs - logs.mean(axis=-1, keepdims=True)) * x, axis=-1) / np.sum(x * x)
    factor = np.exp(-slope[..., None] * np.arange(close.shape[-1], dtype=np.float64))
    result = {name: np.asarray(values, dtype=np.float64) * factor
        for name, values in bars.items()}
    # La normalización mantiene el close inicial de cada ventana sin alterar su retorno.
    initial_factor = close[..., :1] / result["close"][..., :1]
    return {name: values * initial_factor for name, values in result.items()}


def evaluate_pieces(pieces, *, perfil, capital, meta, n_scenarios=1000,
        n_smart=200, seed=43, before=None, db_path="market_data.db",
        library_dir="data/derived/scenario_lib", workers=2, mode="alternativas",
        fee_pct=.1):
    """Evalúa piezas Simple/bot/Smart y su cartera en fechas FHS pareadas.

    Cada pieza es un dict: `symbol`, `strategy`, `capital`, `low`, `high`, `n`;
    `sigma_24h` es sigma logarítmica acumulada de 24 horas. Bot usa
    `buy_price`/`sell_price`; `width_sigma` define ancho
    log-simétrico en unidades de sigma del horizonte del perfil. En modo
    `alternativas`, cada candidata se evalúa sola contra el capital total de la
    meta (su capital no asignado queda quieto); `cartera` suma piezas.
    """
    mode = str(mode).lower()
    if mode not in {"alternativas", "cartera"}:
        raise ValueError("modo debe ser alternativas o cartera")
    if not pieces or capital <= 0 or n_scenarios <= 0 or float(fee_pct) < 0:
        raise ValueError("piezas, capital y escenarios válidos requeridos")
    pieces = [dict(piece) for piece in pieces]
    if mode == "cartera":
        pieces_total = sum(float(piece["capital"]) for piece in pieces)
        if pieces_total > float(capital) + 1e-6:
            raise ValueError(f"las piezas suman {pieces_total:g} USDT y la meta tiene "
                f"{float(capital):g}; usa --modo alternativas para compararlas")
    profile = DEFAULT_PROFILES[str(perfil).upper()]
    horizon = int(profile["max_days"])
    loss_pct = float(profile["max_loss_pct"])
    symbols = sorted({str(piece["symbol"]).upper() for piece in pieces})
    db_path = market_db_path(db_path)
    store = CandleStore(db_path)
    loaded = {symbol: store.load(symbol, "5m") for symbol in symbols}
    cutoff = None if before is None else _to_timestamp(before)
    data = {}
    for symbol, candles in loaded.items():
        stop = (len(candles.close) if cutoff is None else
            int(np.searchsorted(candles.timestamp, cutoff, side="left")))
        if stop < 30 * 288 + 1:
            raise ValueError(f"{symbol}: historia anterior a before insuficiente")
        data[symbol] = CandleData(np.asarray(candles.timestamp[:stop]),
            np.asarray(candles.open[:stop]), np.asarray(candles.high[:stop]),
            np.asarray(candles.low[:stop]), np.asarray(candles.close[:stop]), candles.gaps)
    price = {symbol: float(data[symbol].close[-1]) for symbol in symbols}
    sigma = {symbol: _sigma_estimates(data[symbol]) for symbol in symbols}
    calibration = (_calibration_factors(db_path, symbols) if cutoff is None else
        _calibration_factors(db_path, symbols, before=cutoff))
    today = {symbol: sigma[symbol][0] for symbol in symbols}
    today_calibrated = {symbol: today[symbol] * calibration.get(symbol, 1.0)
        for symbol in symbols}
    long = {symbol: sigma[symbol][1] for symbol in symbols}
    target_count = int(n_scenarios)
    has_smart = any(str(piece["strategy"]).lower() == "smart" for piece in pieces)
    if has_smart and n_smart <= 0:
        raise ValueError("n_smart debe ser positivo cuando se evalúa Smart")
    piece_curves = [[] for _ in pieces]
    piece_scenario_curves = [{} for _ in pieces]
    raw = [{"cycles": [], "inventory_qty": [], "inventory_value": [], "fills": []}
        for _ in pieces]
    paired = {}
    batches = generate(symbols, horizon_days=horizon, n_scenarios=target_count,
        seed=seed, price_now=price, sigma_today_24h=today, sigma_long_24h=long,
        before=before, library_directory=library_dir, calibration_factors=calibration)
    for batch in batches:
        per_index = [{} for _ in range(next(iter(batch["symbols"].values()))["close"].shape[0])]
        geometry = [None] * len(pieces)
        for index, piece in enumerate(pieces):
            symbol = str(piece["symbol"]).upper()
            strategy = str(piece["strategy"]).lower()
            market = batch["symbols"][symbol]
            low, high = piece.get("low"), piece.get("high")
            if piece.get("width_sigma") is not None:
                width = _log_width_from_sigma_24h(today_calibrated[symbol], horizon,
                    piece["width_sigma"])
                low, high = price[symbol] * np.exp(-width), price[symbol] * np.exp(width)
            if strategy == "bot" and piece.get("buy_offset_pct") is not None:
                low = price[symbol] * (1 + float(piece["buy_offset_pct"]) / 100)
                margin = float(profile["min_margin_pct"]) + .2
                high = low * (1 + (margin + .2) / 100)
                if piece.get("sell_offset_pct") is not None:
                    high = price[symbol] * (1 + float(piece["sell_offset_pct"]) / 100)
            low = float(piece.get("buy_price", low)) if strategy == "bot" else float(low)
            high = float(piece.get("sell_price", high)) if strategy == "bot" else float(high)
            geometry[index] = (low, high)
            common = {key: market[key] for key in ("open", "high", "low", "close")}
            if strategy == "smart":
                smart_count = max(0, min(len(market["close"]),
                    int(n_smart) - int(batch["scenario_start"])))
                if smart_count <= 0:
                    continue
                results = evaluate_smart_scenarios(common, n_levels=int(piece.get("n", 6)),
                    capital=float(piece["capital"]), low=low, high=high,
                    filters=store.filters(symbol), sigma_path=market["sigma_path"],
                    n_smart=smart_count, workers=workers, max_loss_pct=loss_pct,
                    fee_pct=fee_pct)
                for offset, result in enumerate(results):
                    piece_curves[index].append(result["curve"])
                    scenario_index = int(batch["scenario_start"]) + offset
                    piece_scenario_curves[index][scenario_index] = result["curve"]
                    raw[index]["cycles"].append(result["metrics"]["cycles_completed"])
                    raw[index]["inventory_qty"].append(result["inventory_qty"])
                    raw[index]["inventory_value"].append(result["inventory_value"])
                    raw[index]["fills"].append(sum(event.get("type") in
                        {"BUY_FILLED", "SELL_FILLED"} for event in result.get("events", [])))
                    per_index[offset][index] = ("smart", result)
            elif strategy == "bot":
                fast = simulate_bot(common, capital=float(piece["capital"]), buy_price=low,
                    sell_price=high, filters=store.filters(symbol), max_loss_pct=loss_pct,
                    target_usdt=meta, initial_timestamp=START_TS, pack_curves=True,
                    fee_pct=fee_pct)
                piece_curves[index].extend(fast["curves"])
                start_index = int(batch["scenario_start"])
                piece_scenario_curves[index].update({start_index + offset: curve
                    for offset, curve in enumerate(fast["curves"])})
                raw[index]["cycles"].extend(fast["cycles"].tolist())
                raw[index]["inventory_qty"].extend(fast["inventory_qty"].tolist())
                raw[index]["inventory_value"].extend(fast["inventory_value"].tolist())
                raw[index]["fills"].extend((fast["buys"] + fast["sells"]).tolist())
                for offset, curve in enumerate(fast["curves"]): per_index[offset][index] = ("bot", curve)
            elif strategy == "simple":
                fast = simulate_simple(common, n_levels=int(piece.get("n", 6)),
                    capital=float(piece["capital"]), low=low, high=high,
                    filters=store.filters(symbol), max_loss_pct=loss_pct, target_usdt=meta,
                    initial_timestamp=START_TS, pack_curves=True, fee_pct=fee_pct)
                piece_curves[index].extend(fast["curves"])
                start_index = int(batch["scenario_start"])
                piece_scenario_curves[index].update({start_index + offset: curve
                    for offset, curve in enumerate(fast["curves"])})
                raw[index]["cycles"].extend(fast["cycles"].tolist())
                raw[index]["inventory_qty"].extend(fast["inventory_qty"].tolist())
                raw[index]["inventory_value"].extend(fast["inventory_value"].tolist())
                raw[index]["fills"].extend((fast["buys"] + fast["sells"]).tolist())
                for offset, curve in enumerate(fast["curves"]): per_index[offset][index] = ("simple", curve)
            else:
                raise ValueError(f"estrategia no reconocida: {strategy}")
        for row in per_index:
            smarts = [(i, value[1]) for i, value in row.items() if value[0] == "smart"]
            simples = [(i, value[1]) for i, value in row.items() if value[0] == "simple"]
            for smart_index, smart_result in smarts:
                peer = next((curve for i, curve in simples if str(pieces[i]["symbol"]).upper() == str(pieces[smart_index]["symbol"]).upper()
                    and pieces[i].get("n", 6) == pieces[smart_index].get("n", 6)
                    and pieces[i]["capital"] == pieces[smart_index]["capital"]
                    and geometry[i] == geometry[smart_index]), None)
                if peer is not None:
                    paired.setdefault(smart_index, ([], []))[0].append(smart_result)
                    paired[smart_index][1].append(peer)
    piece_results = []
    for index, piece in enumerate(pieces):
        values = raw[index]
        metrics = summarize(piece_curves[index],
            capital_initial=(float(capital) if mode == "alternativas" else float(piece["capital"])),
            target_usdt=meta, max_loss_pct=loss_pct,
            horizon_days=horizon, cycles=values["cycles"], inventory_qty=values["inventory_qty"],
            inventory_value=values["inventory_value"], seed=seed)
        zero_fill_fraction = (float(np.mean(np.asarray(values["fills"]) == 0))
            if values["fills"] else 1.0)
        warning = "la geometría casi nunca se toca" if zero_fill_fraction > .95 else None
        from grid.sim.meta_portfolio import evaluate_portfolio
        scenario_results = []
        if mode == "alternativas":
            for scenario_id, curve in sorted(piece_scenario_curves[index].items()):
                outcome = evaluate_portfolio([curve], capital_initial=float(capital),
                    target_usdt=meta, max_loss_pct=loss_pct)
                scenario_results.append({"scenario_index": int(scenario_id),
                    "pnl_usdt": outcome["pnl_usdt"], "loss_cut": outcome["loss_cut"],
                    "reached": outcome["reached"]})
        piece_results.append({"piece": piece, "metrics": metrics,
            "fills_mean": float(np.mean(values["fills"])) if values["fills"] else 0.0,
            "aviso": warning, "zero_fill_fraction": zero_fill_fraction,
            "scenario_results": scenario_results})
    portfolio = (summarize_portfolio(piece_curves, capital_initial=capital, target_usdt=meta,
        max_loss_pct=loss_pct, horizon_days=horizon, seed=seed) if mode == "cartera" else None)
    comparisons = []
    if mode == "cartera":
        for smart_index, (smart_results, simple_curves) in paired.items():
            comparisons.append({"piece_index": smart_index, **paired_smart_simple(smart_results, simple_curves,
                capital_initial=float(pieces[smart_index]["capital"]), target_usdt=meta, max_loss_pct=loss_pct,
                replicas=1000, seed=seed)})
    if mode == "alternativas":
        comparisons = _paired_alternatives(pieces, piece_scenario_curves, capital=capital,
            target=meta, loss_pct=loss_pct, seed=seed)
    effective_sigma = {symbol: today[symbol] * calibration.get(symbol, 1.0)
        for symbol in symbols}
    return {"profile": str(perfil).upper(), "mode": mode, "fee_pct": float(fee_pct),
        "horizon_days": horizon,
        "n_scenarios": target_count, "pieces": piece_results, "portfolio": portfolio,
        "smart_paired": comparisons, "calibration_factors": calibration,
        "sigma_today_24h": effective_sigma,
        "as_of_ts": cutoff if cutoff is not None else int(max(data[s].timestamp[-1] for s in symbols))}


def paired_fee_sensitivity(base_result, comparison_result, *, seed=43, replicas=1000):
    """Compara comisiones por pieza y por índice global de escenario compartido."""
    output = []
    for index, (base, comparison) in enumerate(zip(base_result["pieces"],
            comparison_result["pieces"])):
        left = {row["scenario_index"]: row for row in base.get("scenario_results", [])}
        right = {row["scenario_index"]: row for row in comparison.get("scenario_results", [])}
        ids = sorted(set(left).intersection(right))
        if not ids:
            continue
        deltas = {
            "pnl_usdt": np.asarray([right[i]["pnl_usdt"] - left[i]["pnl_usdt"] for i in ids]),
            "p_loss": np.asarray([float(right[i]["loss_cut"]) - float(left[i]["loss_cut"])
                for i in ids]),
            "p_target": np.asarray([float(right[i]["reached"]) - float(left[i]["reached"])
                for i in ids]),
        }
        rng = np.random.default_rng(seed + index)
        row = {"piece_index": index, "n": len(ids), "fee_base_pct": base_result["fee_pct"],
            "fee_comparison_pct": comparison_result["fee_pct"], "scenario_indices": ids}
        for name, values in deltas.items():
            samples = values[rng.integers(0, len(ids), size=(int(replicas), len(ids)))].mean(axis=1)
            row[name] = {"mean": float(values.mean()), "ci_low": float(np.quantile(samples, .025)),
                "ci_high": float(np.quantile(samples, .975))}
        output.append(row)
    return output


def evaluate_history(pieces, *, perfil, capital, meta, db_path="market_data.db",
        months=24, step_days=7, since=None, symbols=None, fee_pct=.1,
        library_dir="data/derived/scenario_lib", seed=43, replicas=1000,
        n_smart=200, workers=2):
    """Evalua historia real causal por lotes y una variante sin tendencia lineal."""
    from research.scenarios.fastsim import simulate_simple, simulate_bot
    from research.scenarios.metrics import summarize
    from grid.sim.meta_portfolio import evaluate_portfolio
    if not pieces or capital <= 0 or step_days <= 0 or fee_pct < 0:
        raise ValueError("piezas, capital, paso y comision deben ser validos")
    profile = DEFAULT_PROFILES[str(perfil).upper()]
    horizon_days = int(profile["max_days"])
    horizon_bars = horizon_days * 288
    symbols_used = sorted({str(p["symbol"]).upper() for p in pieces})
    store = CandleStore(market_db_path(db_path))
    start_cutoff = None if since is None else _to_timestamp(since)
    result = {"mode": "historia", "fee_pct": float(fee_pct),
        "horizon_days": horizon_days, "symbols": {}}
    for symbol in symbols_used:
        data = store.load(symbol, "5m")
        ts = np.asarray(data.timestamp, dtype=np.int64)
        close = np.asarray(data.close, dtype=np.float64)
        opn = np.asarray(data.open, dtype=np.float64)
        high = np.asarray(data.high, dtype=np.float64)
        low = np.asarray(data.low, dtype=np.float64)
        calibration_ts, calibration_values = _calibration_factor_timeline(
            getattr(store, "path", db_path), symbol)
        if len(close) < 30 * 288 + horizon_bars:
            result["symbols"][symbol] = {"estado": "historia_insuficiente", "ventanas": 0}
            continue
        cutoff = start_cutoff if start_cutoff is not None else int(ts[-1] - months * 30 * 86400)
        first = max(30 * 288 + 1, int(np.searchsorted(ts, cutoff, side="left")))
        last = len(close) - horizon_bars
        starts = np.arange(first, last + 1, int(step_days) * 288, dtype=np.int64)
        pieces_result = []
        for piece_index, piece in enumerate(p for p in pieces if str(p["symbol"]).upper() == symbol):
            strategy = str(piece["strategy"]).lower()
            if strategy not in {"simple", "bot", "smart"}:
                raise ValueError("historia solo admite simple, bot y smart")
            chosen = starts.copy()
            if strategy == "smart" and len(chosen) > int(n_smart):
                chosen = np.sort(np.random.default_rng(seed + piece_index).choice(
                    chosen, size=int(n_smart), replace=False))
            sigmas, prices0 = [], []
            for start in chosen:
                calibration_index = int(np.searchsorted(calibration_ts, ts[start], side="left")) - 1
                calibration = (float(calibration_values[calibration_index])
                    if calibration_index >= 0 else 1.0)
                sigmas.append(causal_sigma_24h(close, ts, int(start), calibration))
                prices0.append(float(opn[start]))
            sigmas = np.asarray(sigmas, dtype=np.float64)
            prices0 = np.asarray([close[i - 1] for i in chosen], dtype=np.float64)
            if not len(chosen):
                pieces_result.append({"piece": piece, "estado": "sin_ventanas"})
                continue
            widths = float(piece.get("width_sigma", 2.0)) * sigmas * np.sqrt(horizon_days)
            lower = prices0 * np.exp(-widths)
            upper = prices0 * np.exp(widths)
            if piece.get("low") is not None and piece.get("high") is not None:
                reference = float(close[-1])
                lower = float(piece["low"]) * prices0 / reference
                upper = float(piece["high"]) * prices0 / reference
            if strategy == "bot":
                buy = prices0 * (1 + float(piece.get("buy_offset_pct", -1.5)) / 100)
                margin = float(profile["min_margin_pct"]) + .2
                default_sell = buy * (1 + (margin + .2) / 100)
                sell = prices0 * (1 + float(piece["sell_offset_pct"]) / 100) \
                    if piece.get("sell_offset_pct") is not None else default_sell
                buy = np.asarray(piece.get("buy_price", buy), dtype=np.float64)
                sell = np.asarray(piece.get("sell_price", sell), dtype=np.float64)
                if buy.ndim == 0: buy = buy * prices0 / float(close[-1])
                if sell.ndim == 0: sell = sell * prices0 / float(close[-1])
            starts_bars = {"open": np.stack([opn[i:i+horizon_bars] for i in chosen]),
                "high": np.stack([high[i:i+horizon_bars] for i in chosen]),
                "low": np.stack([low[i:i+horizon_bars] for i in chosen]),
                "close": np.stack([close[i:i+horizon_bars] for i in chosen])}
            variants = {"real": starts_bars, "sin_tendencia": _remove_log_trend(starts_bars)}
            variant_results = {}
            geometry_rows = []
            if strategy == "bot":
                geometry_rows = [{"timestamp": int(ts[index]), "sigma_24h": float(sigmas[row]),
                    "buy_price": float(buy[row]),
                    "sell_price": float(sell[row])} for row, index in enumerate(chosen)]
            else:
                geometry_rows = [{"timestamp": int(ts[index]), "sigma_24h": float(sigmas[row]),
                    "low": float(lower[row]),
                    "high": float(upper[row])} for row, index in enumerate(chosen)]
            for variant, bars in variants.items():
                if strategy == "simple":
                    sim = simulate_simple(bars, n_levels=int(piece.get("n", 6)),
                        capital=float(piece["capital"]), low=lower, high=upper,
                        filters=store.filters(symbol), max_loss_pct=float(profile["max_loss_pct"]),
                        target_usdt=meta, initial_timestamp=1_700_000_100,
                        pack_curves=True, fee_pct=fee_pct)
                    curves = sim["curves"]
                    cycles, inventory, inv_value = sim["cycles"], sim["inventory_qty"], sim["inventory_value"]
                elif strategy == "bot":
                    sim = simulate_bot(bars, capital=float(piece["capital"]),
                        buy_price=buy, sell_price=sell, filters=store.filters(symbol),
                        max_loss_pct=float(profile["max_loss_pct"]), target_usdt=meta,
                        initial_timestamp=1_700_000_100, pack_curves=True, fee_pct=fee_pct)
                    curves = sim["curves"]
                    cycles, inventory, inv_value = sim["cycles"], sim["inventory_qty"], sim["inventory_value"]
                else:
                    sigma_path = np.repeat(sigmas[:, None], horizon_days, axis=1)
                    smart = evaluate_smart_scenarios(bars, n_levels=int(piece.get("n", 6)),
                        capital=float(piece["capital"]), low=lower, high=upper,
                        filters=store.filters(symbol), sigma_path=sigma_path,
                        n_smart=min(len(chosen), int(n_smart)), workers=workers,
                        max_loss_pct=float(profile["max_loss_pct"]), fee_pct=fee_pct)
                    curves = [row["curve"] for row in smart]
                    cycles = np.asarray([row["metrics"].get("cycles_completed", 0) for row in smart])
                    inventory = np.asarray([row["inventory_qty"] for row in smart])
                    inv_value = np.asarray([row["inventory_value"] for row in smart])
                summary = summarize(curves, capital_initial=float(capital), target_usdt=meta,
                    max_loss_pct=float(profile["max_loss_pct"]), horizon_days=horizon_days,
                    cycles=cycles, inventory_qty=inventory, inventory_value=inv_value,
                    seed=seed, replicas=replicas)
                rows = [evaluate_portfolio([curve], capital_initial=float(capital),
                    target_usdt=meta, max_loss_pct=float(profile["max_loss_pct"])) for curve in curves]
                pnls = np.asarray([row["pnl_usdt"] for row in rows])
                block = max(1, int(np.ceil(20 / step_days)))
                rng = np.random.default_rng(seed + piece_index)
                boot = []
                for _ in range(int(replicas)):
                    selected = []
                    while len(selected) < len(pnls):
                        pos = int(rng.integers(0, len(pnls)))
                        selected.extend((pos + np.arange(block)) % len(pnls))
                    boot.append(float(np.mean(pnls[np.asarray(selected[:len(pnls)], dtype=int)])))
                summary["pnl_mean_ci95"] = [float(np.quantile(boot, .025)), float(np.quantile(boot, .975))]
                variant_results[variant] = {"metrics": summary,
                    "starts": [{"timestamp": int(ts[i]), "sigma_24h": float(sigma),
                        "pnl_usdt": float(row["pnl_usdt"]), "reached": bool(row["reached"]),
                        "loss_cut": bool(row["loss_cut"])}
                        for i, sigma, row in zip(chosen, sigmas, rows)]}
            del starts_bars, variants, bars
            pieces_result.append({"piece": piece, "n": len(chosen), "variantes": variant_results})
            pieces_result[-1]["geometrias"] = geometry_rows
        result["symbols"][symbol] = {"ventanas_candidatas": len(starts), "pieces": pieces_result}
    return result


def _paired_alternatives(pieces, curves, *, capital, target, loss_pct, seed):
    """Compara curvas por índice global, priorizando pares Smart-Simple geométricos."""
    from grid.sim.meta_portfolio import evaluate_portfolio
    pairs = []
    used = set()
    for smart_index, smart in enumerate(pieces):
        if str(smart["strategy"]).lower() != "smart":
            continue
        simple_index = next((index for index, simple in enumerate(pieces)
            if str(simple["strategy"]).lower() == "simple"
            and str(simple["symbol"]).upper() == str(smart["symbol"]).upper()
            and int(simple.get("n", 6)) == int(smart.get("n", 6))
            and (simple.get("low"), simple.get("high"), simple.get("width_sigma"))
                == (smart.get("low"), smart.get("high"), smart.get("width_sigma"))), None)
        if simple_index is not None:
            pairs.append((simple_index, smart_index, "smart_menos_simple"))
            used.update((simple_index, smart_index))
    for index in range(1, len(pieces)):
        if index not in used:
            pairs.append((0, index, "pieza_menos_primera"))
    output = []
    for left_index, right_index, label in pairs:
        left_map = curves[left_index] if isinstance(curves[left_index], dict) else dict(
            enumerate(curves[left_index]))
        right_map = curves[right_index] if isinstance(curves[right_index], dict) else dict(
            enumerate(curves[right_index]))
        scenario_ids = sorted(set(left_map).intersection(right_map))
        count = len(scenario_ids)
        if count < 1:
            continue
        left = [evaluate_portfolio([curve], capital_initial=capital,
            target_usdt=target, max_loss_pct=loss_pct) for curve in
            (left_map[scenario_id] for scenario_id in scenario_ids)]
        right = [evaluate_portfolio([curve], capital_initial=capital,
            target_usdt=target, max_loss_pct=loss_pct) for curve in
            (right_map[scenario_id] for scenario_id in scenario_ids)]
        deltas = {
            "pnl_usdt": np.asarray([b["pnl_usdt"] - a["pnl_usdt"] for a, b in zip(left, right)]),
            "loss_probability": np.asarray([float(b["loss_cut"]) - float(a["loss_cut"])
                for a, b in zip(left, right)]),
            "target_probability": np.asarray([float(b["reached"]) - float(a["reached"])
                for a, b in zip(left, right)]),
        }
        rng = np.random.default_rng(seed + left_index * 101 + right_index)
        comparison = {"n": count, "from_index": left_index, "to_index": right_index,
            "scenario_indices": scenario_ids, "label": label,
            "from_mean_pnl_usdt": float(np.mean([row["pnl_usdt"] for row in left])),
            "to_mean_pnl_usdt": float(np.mean([row["pnl_usdt"] for row in right]))}
        if label == "smart_menos_simple":
            comparison["simple_mean_pnl_usdt"] = comparison["from_mean_pnl_usdt"]
            comparison["smart_mean_pnl_usdt"] = comparison["to_mean_pnl_usdt"]
        for name, values in deltas.items():
            samples = values[rng.integers(0, count, size=(1000, count))].mean(axis=1)
            comparison[name] = {"mean": float(values.mean()),
                "ci_low": float(np.quantile(samples, .025)),
                "ci_high": float(np.quantile(samples, .975))}
        output.append(comparison)
    return output
