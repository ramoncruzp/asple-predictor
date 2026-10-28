"""Mide sesgo de resolución 5m/1m usando los simuladores existentes; no opera."""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from math import asinh, exp, sqrt
from statistics import NormalDist
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from grid.sim.data import CandleData, ewma_sigma_24h
from grid.sim.meta_bot import run_meta_bot
from grid.sim.runner import FILTERS, run_simulation
from grid.structure import select_levels
from data.exchange_filters import SymbolFilters

DAY = 86400
FEE_PCT = 0.1
CAPITAL = 70.0
HORIZON_DAYS = 20
STEP_DAYS = 7
TARGET_USDT = 3.0


def _load_csv(path: Path, expected_seconds: int) -> CandleData:
    rows = []
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        required = {"timestamp", "open", "high", "low", "close"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError(f"{path}: se requieren timestamp, open, high, low y close")
        for row in reader:
            stamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            values = [float(row[k]) for k in ("open", "high", "low", "close")]
            if not np.isfinite(values).all() or min(values) <= 0:
                raise ValueError(f"{path}: precio no positivo o no finito")
            if values[2] > min(values[0], values[3]) or values[1] < max(values[0], values[3]):
                raise ValueError(f"{path}: relación OHLC inválida")
            rows.append((int(stamp.timestamp()), *values))
    array = np.asarray(rows, dtype=float)
    if (len(array) < 2 or np.any(array[:, 0] % expected_seconds != 0)
            or np.any(np.diff(array[:, 0]) != expected_seconds)):
        raise ValueError(f"{path}: se esperaban al menos dos velas consecutivas de {expected_seconds}s")
    return CandleData(array[:, 0].astype(np.int64), array[:, 1], array[:, 2],
                      array[:, 3], array[:, 4], 0)


def _from_frame(frame, interval_seconds: int) -> CandleData:
    if frame is None or frame.empty:
        raise ValueError("Binance devolvió cero velas")
    timestamps = frame["timestamp"]
    if getattr(timestamps.dtype, "tz", None) is not None:
        ts = (timestamps.astype("int64") // 1_000_000_000).to_numpy(dtype=np.int64)
    else:
        ts = (timestamps.astype("datetime64[ns]").astype("int64") // 1_000_000_000)
    data = CandleData(ts, *(frame[name].to_numpy(dtype=float) for name in ("open", "high", "low", "close")), 0)
    if len(ts) < 2 or np.any(ts % interval_seconds != 0) or np.any(np.diff(ts) != interval_seconds):
        raise ValueError(f"historial Binance incompleto o desalineado a {interval_seconds}s")
    return data


def _download_1m(client, symbol: str, start_time: datetime):
    """Descarga 1m con la instancia pública: el wrapper del repo no lista ese intervalo."""
    end_time = datetime.now(timezone.utc)
    cursor_ms = int(start_time.timestamp() * 1000)
    end_ms = int(end_time.timestamp() * 1000)
    rows = []
    while cursor_ms < end_ms:
        page = client._request_with_retries(
            lambda: client.client.get_klines(symbol=symbol, interval="1m",
                startTime=cursor_ms, endTime=end_ms, limit=1000),
            symbol=symbol, interval="1m")
        if not page:
            break
        rows.extend(page)
        next_cursor = int(page[-1][0]) + 1
        if next_cursor <= cursor_ms or len(page) < 1000:
            break
        cursor_ms = next_cursor
    return client._klines_to_dataframe(rows)


def _slice(candles: CandleData, begin: int, end: int) -> CandleData | None:
    mask = (candles.timestamp >= begin) & (candles.timestamp < end)
    idx = np.flatnonzero(mask)
    if len(idx) < 2:
        return None
    return CandleData(*(getattr(candles, key)[idx] for key in
                        ("timestamp", "open", "high", "low", "close")), gaps=0)


def _advisor_geometry(candles: CandleData, filters=FILTERS, *, start_index: int = 0,
                      bars_per_hour: int = 12) -> dict:
    """Reproduce el rango centrado moderado y el selector puro de niveles del Advisor."""
    start_index = int(start_index)
    if start_index < 0 or start_index >= len(candles.close):
        raise ValueError("índice inicial fuera de las velas disponibles")
    # Sigma usa estrictamente velas anteriores al primer instante simulado.
    # En el inicio global no hay precalentamiento descargado; se usa el fallback σ=0.
    if start_index < 2:
        sigma = 0.0
    else:
        sigma = float(ewma_sigma_24h(candles.close[:start_index],
            bars_per_hour=bars_per_hour)[-1])
    mid = float(candles.close[start_index])
    if sigma > 0:
        # Fórmula de rango centrado moderado del Advisor: objetivo de toque 30 %,
        # sigma de 72 h y amplitud máxima del perfil 45 %.
        sigma_72h = sigma * sqrt(3.0)
        d = min(NormalDist().inv_cdf(.85) * sigma_72h, asinh(45.0 / 200.0))
        low, high = mid * exp(-d), mid * exp(d)
    else:
        # σ nula no permite rango centrado; fallback de 2 % a cada lado para
        # que una serie sintética plana aún pueda probar el medidor.
        low, high = mid * .98, mid * 1.02
    selected = select_levels(low, high, mid, CAPITAL, filters, FEE_PCT,
                              margin_target_pct=.3, sigma_24h=sigma,
                              vol_spacing=False, k=2.0)
    return {"low": float(low), "high": float(high), "reference": mid,
            "n": max(4, int(selected["n_levels"])), "sigma_24h": sigma,
            "source": "rango centrado moderado del Advisor y grid.structure.select_levels; "
                      f"σ realizada previa con {bars_per_hour} barras/h, margen 0,3 %"}


def _shared_geometry(candles5: CandleData, kind: str, filters=FILTERS, *, start_index: int = 0,
                     offset_pct: float | None = None) -> dict:
    """Una geometría causal derivada de 5m que se reutiliza sin cambios en 1m."""
    if kind == "bot":
        if start_index < 0 or start_index >= len(candles5.close):
            raise ValueError("índice inicial fuera de las velas disponibles")
        reference = float(candles5.close[start_index])
        buy = reference * (1 + float(offset_pct or 0) / 100)
        return {"reference": reference, "low": buy,
                "high": buy * (1 + .95 / 100), "n": 1,
                "geometry_source_interval": "5m"}
    shape = _advisor_geometry(candles5, filters, start_index=start_index, bars_per_hour=12)
    return {**shape, "geometry_source_interval": "5m"}


def _run_grid(candles: CandleData, geometry: dict, strategy: str, target: bool = False,
              filters=FILTERS, trace_callback=None) -> dict:
    n = int(geometry["n"])
    capital = 100.0 if target else CAPITAL
    params = {"target_usdt": TARGET_USDT, "target_basis": "cash"} if target else {}
    return run_simulation(candles, strategy=strategy, n=n, capital=capital,
        low=geometry["low"], high=geometry["high"], params=params, fee_pct=FEE_PCT, filters=filters,
        close_wait_enabled=True, adjust_enabled_global=False, stoploss_enabled=False,
        meta_max_loss_pct=None, meta_portfolio_piece=True,
        sigma_values=np.full(len(candles.timestamp), np.nan), resync_candles=3,
        trace_callback=trace_callback)


def _run_bot(candles: CandleData, geometry: dict, offset_pct: float, target: bool = False,
             filters=FILTERS, trace_callback=None) -> dict:
    buy = geometry["reference"] * (1 + offset_pct / 100)
    sell = buy * (1 + .95 / 100)
    return run_meta_bot(candles, capital=100.0 if target else CAPITAL, buy_price=buy, sell_price=sell,
        target_usdt=TARGET_USDT if target else None, fee_pct=FEE_PCT,
        min_margin_pct=.30, filters=filters, trace_callback=trace_callback)


def _simulated_fill_counts(result: dict, trace: list[dict], kind: str) -> tuple[int, int]:
    """Cuenta fills reales de la traza del simulador, sin inferirlos de los ciclos agregados."""
    if kind != "bot":
        events = result.get("events") or []
        return (sum(event.get("type") == "BUY_FILLED" for event in events),
                sum(event.get("type") == "SELL_FILLED" for event in events))
    buys = sells = 0
    previous_inventory = 0.0
    for sample in trace:
        inventory = float(sample.get("inventory") or 0.0)
        if previous_inventory <= 0 < inventory:
            buys += 1
        elif previous_inventory > 0 >= inventory:
            sells += 1
        previous_inventory = inventory
    return buys, sells


def ambiguous_and_exact(candles: CandleData, geometry: dict, kind: str,
                        bot_offset_pct: float | None = None) -> tuple[int, int]:
    """Cuenta contactos en órdenes que estarían vigentes al comienzo de cada vela."""
    if kind == "bot":
        offset = -.5 if bot_offset_pct is None else float(bot_offset_pct)
        buy_levels = [geometry["reference"] * (1 + offset / 100)]
        sell_levels = [p * (1 + .95 / 100) for p in buy_levels]
    else:
        count = int(geometry["n"])
        points = np.linspace(geometry["low"], geometry["high"], count + 1)
        buy_levels, sell_levels = points[:-1], points[1:]
    state = ["IDLE"] * len(buy_levels)
    activate = [0] * len(buy_levels)
    ambiguous = exact = 0
    for i, (low, high) in enumerate(zip(candles.low, candles.high)):
        ambiguous_here = False
        for j, (buy, sell) in enumerate(zip(buy_levels, sell_levels)):
            if state[j] == "BUY" and i >= activate[j]:
                if low <= buy <= high and low <= sell <= high:
                    ambiguous_here = True
                if kind != "bot" and low == buy:
                    exact += 1
                elif low < buy:
                    state[j], activate[j] = "SELL", i + 1
            elif state[j] == "SELL" and i >= activate[j]:
                if kind != "bot" and high == sell:
                    exact += 1
                elif high > sell:
                    state[j] = "IDLE"
            if state[j] == "IDLE" and buy < float(candles.close[i]):
                state[j], activate[j] = "BUY", i + 1
        ambiguous += int(ambiguous_here)
    return ambiguous, exact


def _windows(candles: CandleData):
    spacing = int(np.median(np.diff(candles.timestamp)))
    start, end = int(candles.timestamp[0]), int(candles.timestamp[-1]) + spacing
    cursor = start
    while cursor + HORIZON_DAYS * DAY <= end:
        yield cursor, cursor + HORIZON_DAYS * DAY
        cursor += STEP_DAYS * DAY


def measure(symbols: list[str], days: int, source: str, csv_dir: Path, out: Path) -> list[dict]:
    loaded = {}
    if source == "binance":
        from data.binance_client import BinanceClient
        # Cliente público sin claves; requests/python-binance respetan HTTPS_PROXY.
        client = BinanceClient(None, None)
        begin = datetime.now(timezone.utc).replace(microsecond=0)
        from datetime import timedelta
        begin -= timedelta(days=days)
        for symbol in symbols:
            print(f"Descargando {symbol}: 5m y 1m…", flush=True)
            info = client.client.get_symbol_info(symbol)
            if not info:
                raise ValueError(f"Binance no reconoce {symbol}")
            filters = SymbolFilters.from_symbol_info(info)
            loaded[symbol] = (_from_frame(client.get_historical_klines(symbol, "5m", days, start_time=begin), 300),
                              _from_frame(_download_1m(client, symbol, begin), 60),
                              filters)
    else:
        for symbol in symbols:
            loaded[symbol] = (_load_csv(csv_dir / f"{symbol}_5m.csv", 300),
                              _load_csv(csv_dir / f"{symbol}_1m.csv", 60), FILTERS)
    rows = []
    total = len(symbols) * 5
    done = 0
    for symbol, (candles5, candles1, filters) in loaded.items():
        cutoff = int(candles5.timestamp[-1]) - days * DAY
        candles5 = _slice(candles5, cutoff, int(candles5.timestamp[-1]) + 1) or candles5
        candles1 = _slice(candles1, cutoff, int(candles1.timestamp[-1]) + 1) or candles1
        specs = [("smart", None), ("simple", None)] + [("bot", offset) for offset in (-.5, -1.5, -3.0)]
        for kind, offset in specs:
            done += 1
            label = f"{symbol} {kind}" + (f" compra {offset:+.1f}%" if offset is not None else "")
            print(f"[{done}/{total}] {label}", flush=True)
            t0 = time.perf_counter()
            row = {"symbol": symbol,
                "geometry": kind + (f"_buy_{offset:+.1f}%" if offset is not None else "")}
            try:
                shape5 = _shared_geometry(candles5, kind, filters, start_index=0, offset_pct=offset)
                shape1 = dict(shape5)
                def run(candles, shape, target=False, trace_callback=None):
                    return (_run_bot(candles, shape, offset, target, filters) if kind == "bot"
                        else _run_grid(candles, shape, kind, target, filters, trace_callback))
                trace5, trace1 = [], []
                if kind == "bot":
                    result5 = _run_bot(candles5, shape5, offset, filters=filters, trace_callback=trace5.append)
                    result1 = _run_bot(candles1, shape1, offset, filters=filters, trace_callback=trace1.append)
                else:
                    result5 = _run_grid(candles5, shape5, kind, filters=filters, trace_callback=trace5.append)
                    result1 = _run_grid(candles1, shape1, kind, filters=filters, trace_callback=trace1.append)
                if shape5 != shape1:
                    raise ValueError("la geometría intravela 5m/1m no coincide")
                metrics5 = result5.get("metrics", {})
                metrics1 = result1.get("metrics", {})
                cycles5 = int(metrics5.get("cycles", int(result5.get("sold", False))))
                cycles1 = int(metrics1.get("cycles", int(result1.get("sold", False))))
                buys5, sells5 = _simulated_fill_counts(result5, trace5, kind)
                buys1, sells1 = _simulated_fill_counts(result1, trace1, kind)
                pnl5 = float(metrics5.get("pnl_total_net_usdt", result5.get("pnl_usdt", 0)))
                pnl1 = float(metrics1.get("pnl_total_net_usdt", result1.get("pnl_usdt", 0)))
                ambiguous, exact = ambiguous_and_exact(candles5, shape5, kind, offset)
                windows5 = windows1 = 0
                successes5 = successes1 = 0
                window_errors = []
                for begin, end in _windows(candles5):
                    w5, w1 = _slice(candles5, begin, end), _slice(candles1, begin, end)
                    if w5 is None or w1 is None:
                        window_errors.append({"start": begin, "error": "ValueError: ventana incompleta en 5m o 1m"})
                        continue
                    try:
                        ix5 = int(np.searchsorted(candles5.timestamp, begin, side="left"))
                        window_shape5 = _shared_geometry(candles5, kind, filters,
                            start_index=ix5, offset_pct=offset)
                        # La geometría de cada ventana se calcula con 5m y se aplica
                        # idéntica a 1m, incluso si el cierre de la primera vela difiere.
                        window_shape1 = dict(window_shape5)
                        if window_shape1 != window_shape5:
                            raise ValueError("la geometría intravela no coincide dentro de la ventana")
                        result_w5, result_w1 = run(w5, window_shape5, True), run(w1, window_shape1, True)
                        windows5 += 1
                        windows1 += 1
                        successes5 += bool(result_w5.get("metrics", {}).get("target_reached", False)
                            if kind != "bot" else result_w5.get("reached"))
                        successes1 += bool(result_w1.get("metrics", {}).get("target_reached", False)
                            if kind != "bot" else result_w1.get("reached"))
                    except Exception as exc:
                        window_errors.append({"start": begin, "error": f"{type(exc).__name__}: {exc}"})
                row.update({"cycles_5m": cycles5, "cycles_1m": cycles1,
                    "buy_fills_5m": buys5, "sell_fills_5m": sells5,
                    "buy_fills_1m": buys1, "sell_fills_1m": sells1,
                    "cycles_lost_pct": (max(0, cycles1 - cycles5) / cycles1 * 100 if cycles1 else 0),
                    "pnl_5m_usdt": pnl5, "pnl_1m_usdt": pnl1,
                    "ambiguous_5m_candles": ambiguous, "exact_touches_5m": exact,
                    "target_probability_5m_pct": successes5 / windows5 * 100 if windows5 else None,
                    "target_probability_1m_pct": successes1 / windows1 * 100 if windows1 else None,
                    "window_successes_5m": successes5, "window_count_5m": windows5,
                    "window_successes_1m": successes1, "window_count_1m": windows1,
                    "same_geometry": (shape5 == shape1),
                    "window_errors": window_errors,
                    "elapsed_s": round(time.perf_counter() - t0, 4),
                    "range_low_5m": shape5.get("low"), "range_high_5m": shape5.get("high"),
                    "range_low_1m": shape1.get("low"), "range_high_1m": shape1.get("high"),
                    "reference_5m": shape5.get("reference"), "reference_1m": shape1.get("reference"),
                    "n_levels_5m": shape5.get("n"), "n_levels_1m": shape1.get("n"),
                    "advisor_geometry_source": "geometría calculada con 5m al inicio y compartida con 1m"})
                row["measured"] = True
                if window_errors:
                    row["error"] = window_errors[0]["error"]
            except Exception as exc:
                row["error"] = f"{type(exc).__name__}: {exc}"
            rows.append(row)
            if not row.get("measured"):
                print(f"[{done}/{total}] {label}: error {row['error']}", flush=True)
            else:
                suffix = f"; error de ventana: {row['error']}" if row.get("error") else ""
                print(f"[{done}/{total}] {label}: ciclos 5m {row['cycles_5m']}, 1m {row['cycles_1m']}{suffix}", flush=True)
    out.mkdir(parents=True, exist_ok=True)
    payload = {"method": "run_simulation/run_meta_bot sin cambios; fee=0,1%; éxito por efectivo atribuible",
               "source": source, "days": days, "horizon_days": HORIZON_DAYS,
               "step_days": STEP_DAYS, "rows": rows}
    (out / "medicion_intravela.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out / "medicion_intravela.csv").open("w", newline="", encoding="utf-8") as stream:
        fieldnames = list(dict.fromkeys(key for row in rows for key in row))
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        if rows:
            writer.writeheader(); writer.writerows(rows)
    print("\nSímbolo/geometría | ciclos 5m/1m | fills 5m compra/venta | fills 1m compra/venta | perdidos % | P(+3) 5m/1m")
    for row in rows:
        if not row.get("measured"):
            continue
        print(f"{row['symbol']} {row['geometry']} | {row['cycles_5m']}/{row['cycles_1m']} | "
              f"{row['buy_fills_5m']}/{row['sell_fills_5m']} | {row['buy_fills_1m']}/{row['sell_fills_1m']} | "
              f"{row['cycles_lost_pct']:.1f} | "
              f"{row['target_probability_5m_pct']}/{row['target_probability_1m_pct']}")
    print(f"Resultados: {out.resolve()}")
    return rows


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", default="JUPUSDT,ZROUSDT,XRPUSDT")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--source", choices=("binance", "csv"), default="binance")
    parser.add_argument("--csv-dir", type=Path, default=Path("."))
    parser.add_argument("--out", type=Path, default=Path("./medicion_intravela/"))
    args = parser.parse_args(argv)
    if args.days < HORIZON_DAYS:
        parser.error("--days debe ser al menos 20 para crear ventanas de probabilidad")
    try:
        rows = measure([s.strip().upper().replace("/", "") for s in args.symbols.split(",") if s.strip()],
                args.days, args.source, args.csv_dir, args.out)
    except Exception as exc:
        parser.exit(2, f"Error de medición: {type(exc).__name__}: {exc}\n")
    return 0 if any(row.get("measured") for row in rows) else 2


if __name__ == "__main__":
    raise SystemExit(main())
