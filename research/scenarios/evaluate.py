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


def _log_width_from_sigma_24h(sigma_24h, horizon_days, multiplier):
    """Convierte sigma logarítmica acumulada de 24 h a un horizonte en días."""
    return float(multiplier) * float(sigma_24h) * np.sqrt(float(horizon_days))


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
        library_dir="data/derived/scenario_lib", workers=2, mode="alternativas"):
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
    if not pieces or capital <= 0 or n_scenarios <= 0:
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
    data = {symbol: store.load(symbol, "5m") for symbol in symbols}
    price = {symbol: float(data[symbol].close[-1]) for symbol in symbols}
    sigma = {symbol: _sigma_estimates(data[symbol]) for symbol in symbols}
    calibration = _calibration_factors(db_path, symbols)
    today = {symbol: sigma[symbol][0] for symbol in symbols}
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
                width = _log_width_from_sigma_24h(today[symbol], horizon,
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
                    n_smart=smart_count, workers=workers, max_loss_pct=loss_pct)
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
                    target_usdt=meta, initial_timestamp=START_TS, pack_curves=True)
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
                    initial_timestamp=START_TS, pack_curves=True)
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
        piece_results.append({"piece": piece, "metrics": metrics,
            "fills_mean": float(np.mean(values["fills"])) if values["fills"] else 0.0,
            "aviso": warning, "zero_fill_fraction": zero_fill_fraction})
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
    return {"profile": str(perfil).upper(), "mode": mode, "horizon_days": horizon,
        "n_scenarios": target_count, "pieces": piece_results, "portfolio": portfolio,
        "smart_paired": comparisons, "calibration_factors": calibration}


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
