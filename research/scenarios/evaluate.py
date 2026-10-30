"""Evaluación offline de piezas sobre escenarios FHS compartidos."""
from __future__ import annotations

import sqlite3

import numpy as np

from data.market_store import CandleStore, market_db_path
from grid.meta import DEFAULT_PROFILES
from research.scenarios.fastsim import simulate_bot, simulate_simple
from research.scenarios.generator import generate
from research.scenarios.metrics import summarize, summarize_portfolio
from research.scenarios.smart_exact import START_TS, evaluate_smart_scenarios, paired_smart_simple


def _sigma_estimates(data):
    returns = np.diff(np.log(np.asarray(data.close, dtype=np.float64)))
    if len(returns) < 12 * 24 * 30:
        raise ValueError("faltan 30 días para estimar volatilidad")
    lam = .97 ** (1 / 12)
    variance = float(np.mean(returns[:12 * 24 * 30] ** 2))
    for value in returns[12 * 24 * 30:]:
        variance = lam * variance + (1 - lam) * float(value * value)
    daily = []
    days = np.asarray(data.timestamp[1:] // 86400)
    for day in np.unique(days)[-180:]:
        values = returns[days == day]
        if len(values) == 288:
            daily.append(float(np.sqrt(np.sum(values * values))))
    return float(np.sqrt(max(variance, 1e-16) * 288)), float(np.median(daily)) if daily else 0.0


def _calibration_factors(path, symbols):
    factors = {}
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"scorecard_runs", "calibration_factors"} <= tables:
                return factors
            for symbol in symbols:
                row = conn.execute("SELECT f.factor FROM calibration_factors f JOIN scorecard_runs r "
                    "ON r.id=f.run_id WHERE r.status='completed' AND f.symbol=? AND f.kind='sigma' "
                    "AND f.horizon_h=24 ORDER BY r.finished_at DESC,f.computed_at DESC LIMIT 1",
                    (symbol,)).fetchone()
                if row and row[0] is not None and float(row[0]) > 0:
                    factors[symbol] = float(row[0])
    except sqlite3.Error:
        return factors
    return factors


def evaluate_pieces(pieces, *, perfil, capital, meta, n_scenarios=1000,
        n_smart=200, seed=43, before=None, db_path="market_data.db",
        library_dir="data/derived/scenario_lib", workers=2):
    """Evalúa piezas Simple/bot/Smart y su cartera en fechas FHS pareadas.

    Cada pieza es un dict: `symbol`, `strategy`, `capital`, `low`, `high`, `n`;
    bot usa `buy_price`/`sell_price`. Si indica `width_sigma`, arma ancho
    log-simétrico en unidades de sigma del horizonte del perfil.
    """
    if not pieces or capital <= 0 or n_scenarios <= 0:
        raise ValueError("piezas, capital y escenarios válidos requeridos")
    profile = DEFAULT_PROFILES[str(perfil).upper()]
    horizon = int(profile["max_days"])
    loss_pct = float(profile["max_loss_pct"])
    symbols = sorted({str(piece["symbol"]).upper() for piece in pieces})
    db_path = market_db_path(db_path)
    store = CandleStore(db_path)
    data = {symbol: store.load(symbol, "5m") for symbol in symbols}
    price = {symbol: float(data[symbol].close[-1]) for symbol in symbols}
    sigma = {symbol: _sigma_estimates(data[symbol]) for symbol in symbols}
    calibration = _calibration_factors(db_path, symbols)
    today = {symbol: sigma[symbol][0] for symbol in symbols}
    long = {symbol: sigma[symbol][1] for symbol in symbols}
    target_count = min(int(n_scenarios), int(n_smart)) if any(
        str(piece["strategy"]).lower() == "smart" for piece in pieces) else int(n_scenarios)
    piece_curves = [[] for _ in pieces]
    raw = [{"cycles": [], "inventory_qty": [], "inventory_value": []} for _ in pieces]
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
                width = float(piece["width_sigma"]) * today[symbol] * np.sqrt(horizon * 24)
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
                results = evaluate_smart_scenarios(common, n_levels=int(piece.get("n", 6)),
                    capital=float(piece["capital"]), low=low, high=high,
                    filters=store.filters(symbol), sigma_path=market["sigma_path"],
                    n_smart=len(market["close"]), workers=workers, max_loss_pct=loss_pct)
                for offset, result in enumerate(results):
                    piece_curves[index].append(result["curve"])
                    raw[index]["cycles"].append(result["metrics"]["cycles_completed"])
                    raw[index]["inventory_qty"].append(result["inventory_qty"])
                    raw[index]["inventory_value"].append(result["inventory_value"])
                    per_index[offset][index] = ("smart", result)
            elif strategy == "bot":
                fast = simulate_bot(common, capital=float(piece["capital"]), buy_price=low,
                    sell_price=high, filters=store.filters(symbol), max_loss_pct=loss_pct,
                    target_usdt=meta, initial_timestamp=START_TS, pack_curves=True)
                piece_curves[index].extend(fast["curves"])
                raw[index]["cycles"].extend(fast["cycles"].tolist())
                raw[index]["inventory_qty"].extend(fast["inventory_qty"].tolist())
                raw[index]["inventory_value"].extend(fast["inventory_value"].tolist())
                for offset, curve in enumerate(fast["curves"]): per_index[offset][index] = ("bot", curve)
            elif strategy == "simple":
                fast = simulate_simple(common, n_levels=int(piece.get("n", 6)),
                    capital=float(piece["capital"]), low=low, high=high,
                    filters=store.filters(symbol), max_loss_pct=loss_pct, target_usdt=meta,
                    initial_timestamp=START_TS, pack_curves=True)
                piece_curves[index].extend(fast["curves"])
                raw[index]["cycles"].extend(fast["cycles"].tolist())
                raw[index]["inventory_qty"].extend(fast["inventory_qty"].tolist())
                raw[index]["inventory_value"].extend(fast["inventory_value"].tolist())
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
        piece_results.append({"piece": piece, "metrics": summarize(piece_curves[index],
            capital_initial=float(piece["capital"]), target_usdt=meta, max_loss_pct=loss_pct,
            horizon_days=horizon, cycles=values["cycles"], inventory_qty=values["inventory_qty"],
            inventory_value=values["inventory_value"], seed=seed)})
    portfolio = summarize_portfolio(piece_curves, capital_initial=capital, target_usdt=meta,
        max_loss_pct=loss_pct, horizon_days=horizon, seed=seed)
    comparisons = []
    for smart_index, (smart_results, simple_curves) in paired.items():
        comparisons.append({"piece_index": smart_index, **paired_smart_simple(smart_results, simple_curves,
            capital_initial=float(pieces[smart_index]["capital"]), target_usdt=meta, max_loss_pct=loss_pct,
            replicas=1000, seed=seed)})
    return {"profile": str(perfil).upper(), "horizon_days": horizon,
        "n_scenarios": target_count, "pieces": piece_results, "portfolio": portfolio,
        "smart_paired": comparisons, "calibration_factors": calibration}
