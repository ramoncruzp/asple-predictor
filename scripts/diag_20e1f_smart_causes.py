"""Offline-only measurement of Smart-grid stop-loss and cadence effects."""
from __future__ import annotations

import importlib.util
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts" / "diag_20e1e_smart_vs_simple.py"
SPEC = importlib.util.spec_from_file_location("diag_20e1e", SOURCE)
E1E = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(E1E)
from data.exchange_filters import SymbolFilters

CAPITAL = 1000.0
FEE_PCT = 0.1
DAYS = 90
RANGES = {
    "ADA": (0.2336, 0.2762, 18),
    "PEPE": (0.000003741, 0.000004450, 19),
    # Fallback realized from the 20E-1e offline recommend() run, not a live range.
    "XRP": (1.406842013993292, 1.5940000566479335, 13),
}


def _filters_for(symbol):
    if symbol != "PEPE":
        return E1E.SIM_FILTERS
    # Offline price-scale approximation only; live PEPEUSDT exchange filters are not read.
    return SymbolFilters(
        tick_size=Decimal("0.000000001"),
        min_price=Decimal("0.000000001"),
        max_price=Decimal("1000000"),
        step_size=Decimal("1"),
        min_qty=Decimal("1"),
        max_qty=Decimal("1000000000000000"),
        min_notional=Decimal("5"),
        apply_min_to_market=True,
        max_num_orders=200,
    )


def _window(symbol: str, interval: str):
    frame = E1E.frame_for(symbol.lower(), interval)
    low, high, _n = RANGES[symbol]
    selected, meta = E1E._simulation_window(frame, low, high, DAYS)
    if selected is None:
        raise RuntimeError(f"No in-range {interval} data for {symbol}: {meta}")
    return selected.copy().reset_index(drop=True), meta


def _run(symbol, interval, strategy, resync, params=None, details=False):
    frame, meta = _window(symbol, interval)
    frame.attrs["gaps"] = 0
    low, high, n = RANGES[symbol]
    E1E.SIM_FILTERS = _filters_for(symbol)
    result = E1E.run(E1E.to_candles(frame, interval), strategy=strategy, n=n,
                     low=low, high=high, resync=resync, params=params,
                     details=details)
    return frame, meta, result


def _idx_by_ts(frame):
    return {int(ts): i for i, ts in enumerate(E1E.to_candles(frame, "1h").timestamp)}


def _sale_attribution(frame, result):
    events = result["events"]
    closes = frame.close.to_numpy(float)
    queues = defaultdict(list)
    buckets = {
        "Grid normal (SELL_FILLED)": {"count": 0, "pnl": 0.0, "fees": 0.0},
        "STOP_LOSS (mercado)": {"count": 0, "pnl": 0.0, "fees": 0.0},
        "ADJUST/reconstrucción forzada": {"count": 0, "pnl": 0.0, "fees": 0.0},
        "Otras ventas a mercado (TARGET)": {"count": 0, "pnl": 0.0, "fees": 0.0},
    }
    stop_events = []
    pairs = []
    sale_trades = []
    timestamp_index = _idx_by_ts(frame)

    def consume(level, event, kind, price, qty, sale_fee_base, event_pnl=None):
        lots = queues[level]
        lot = lots.pop(0) if lots else None
        buy_fee_usdt = lot["fee_base"] * lot["price"] if lot else 0.0
        buy_qty = lot["qty_net"] if lot else float(qty)
        sale_fee_usdt = sale_fee_base * price
        pnl = (float(event_pnl) if event_pnl is not None else
               price * float(qty) - sale_fee_usdt - buy_fee_usdt -
               (lot["qty"] * lot["price"] if lot else 0.0))
        bucket = buckets[kind]
        bucket["count"] += 1
        bucket["pnl"] += pnl
        bucket["fees"] += buy_fee_usdt + sale_fee_usdt
        sale_trades.append({"level": level, "ts": int(event["ts"]), "pnl": pnl})
        if lot:
            pairs.append({"level": level, "buy_idx": lot["idx"],
                          "sell_idx": timestamp_index.get(int(event["ts"])),
                          "buy_price": lot["price"], "buy_qty": buy_qty})

    for event in events:
        kind = event.get("type")
        level = int(event["level_idx"]) if event.get("level_idx") is not None else None
        if kind == "BUY_FILLED" and level is not None:
            price = float(event["price"])
            queues[level].append({
                "idx": timestamp_index.get(int(event["ts"])),
                "price": price,
                "qty": float(event["qty"]),
                "qty_net": float(event.get("qty_net", event["qty"])),
                "fee_base": float(event.get("fee", 0)),
            })
        elif kind == "SELL_FILLED" and level is not None:
            price, qty = float(event["price"]), float(event["qty"])
            consume(level, event, "Grid normal (SELL_FILLED)", price, qty,
                    float(event.get("fee", 0)))
        elif kind == "STOP_LOSS" and level is not None:
            idx = timestamp_index.get(int(event["ts"]))
            if idx is None:
                continue
            # STOP_LOSS sells the holding at that candle's close; fee asset is base.
            lot = queues[level].pop(0) if queues[level] else None
            price = float(closes[idx])
            qty = float(lot["qty_net"]) if lot else 0.0
            buy_fee = lot["fee_base"] * lot["price"] if lot else 0.0
            sale_fee = qty * (FEE_PCT / 100.0) * price
            pnl = float(event.get("pnl", 0.0))
            bucket = buckets["STOP_LOSS (mercado)"]
            bucket["count"] += 1
            bucket["pnl"] += pnl
            bucket["fees"] += buy_fee + sale_fee
            sale_trades.append({"level": level, "ts": int(event["ts"]), "pnl": pnl})
            stop_events.append({"level": level, "idx": idx, "ts": int(event["ts"]),
                                "pnl": pnl, "qty": qty})
            if lot:
                pairs.append({"level": level, "buy_idx": lot["idx"], "sell_idx": idx,
                              "buy_price": lot["price"], "buy_qty": qty})
        elif kind == "TARGET_REACHED":
            for fill in event.get("details", {}).get("sell_cells", []):
                lev = int(fill["level_idx"])
                qty = float(fill["qty"])
                price = float(fill["bid"])
                lot = queues[lev].pop(0) if queues[lev] else None
                buy_fee = lot["fee_base"] * lot["price"] if lot else 0.0
                sale_fee = qty * (FEE_PCT / 100.0) * price
                bucket = buckets["Otras ventas a mercado (TARGET)"]
                bucket["count"] += 1
                bucket["pnl"] += float(fill["pnl"])
                bucket["fees"] += buy_fee + sale_fee
                sale_trades.append({"level": lev, "ts": int(event["ts"]), "pnl": float(fill["pnl"])})
                if lot:
                    pairs.append({"level": lev, "buy_idx": lot["idx"],
                                  "sell_idx": timestamp_index.get(int(event["ts"])),
                                  "buy_price": lot["price"], "buy_qty": qty})

    buys_after = defaultdict(list)
    for event in events:
        if event.get("type") == "BUY_FILLED":
            idx = timestamp_index.get(int(event["ts"]))
            if idx is not None:
                buys_after[int(event["level_idx"])].append(idx)
    rebought = 0
    for stop in stop_events:
        future = [idx for idx in buys_after[stop["level"]]
                  if stop["idx"] < idx <= stop["idx"] + 12]
        rebought += bool(future)
    cell_pnl = {int(row["level_idx"]): float(row["pnl"])
                for row in result.get("details", {}).get("cells", [])}
    stop_levels = {row["level"] for row in stop_events}
    post_stop_pnl = 0.0
    for level in stop_levels:
        first_stop = min(row["ts"] for row in stop_events if row["level"] == level)
        post_stop_pnl += sum(row["pnl"] for row in sale_trades
                             if row["level"] == level and row["ts"] > first_stop)

    adverse = []
    lows = frame.low.to_numpy(float)
    for pair in pairs:
        if pair["buy_idx"] is None or pair["sell_idx"] is None or pair["sell_idx"] < pair["buy_idx"]:
            continue
        min_low = float(lows[pair["buy_idx"]:pair["sell_idx"] + 1].min())
        adverse.append((1 - min_low / pair["buy_price"]) * 100)
    return {
        "buckets": buckets,
        "stop_count": len(stop_events),
        "rebought_12": rebought,
        "rebought_pct": rebought * 100 / len(stop_events) if stop_events else None,
        "stop_cell_pnl_final": sum(cell_pnl.get(level, 0.0) for level in stop_levels),
        "post_stop_realized_pnl": post_stop_pnl,
        "stop_levels": sorted(stop_levels),
        "adverse": adverse,
    }


def _realized_sigmas(frame):
    close = frame.close.astype(float)
    logs = np.log(close.to_numpy())
    ret = np.diff(logs)
    result = {}
    for horizon in (4, 24):
        # Rolling realized log-return standard deviation over horizon-sized bars.
        vals = pd.Series(ret).rolling(horizon, min_periods=horizon).std(ddof=1).dropna()
        result[horizon] = {
            "mean": float(vals.mean()) if len(vals) else None,
            "median": float(vals.median()) if len(vals) else None,
            "n": int(len(vals)),
        }
    # The simulator's EWMA helper assumes 5-minute bars (288/day), even when called on 1h.
    ewma = E1E.ewma_sigma_24h(close.to_numpy())
    result["ewma_24h_mean"] = float(np.mean(ewma))
    result["ewma_24h_median"] = float(np.median(ewma))
    result["ewma_4h_mean"] = float(np.mean(ewma) * math.sqrt(4 / 24))
    return result


def _timeline(frame, result, resync):
    events = result["events"]
    candles = E1E.to_candles(frame, "1h") if len(frame) and (frame.timestamp.diff().dropna().dt.total_seconds().median() > 300) else E1E.to_candles(frame, "5m")
    idx_by_ts = {int(ts): i for i, ts in enumerate(candles.timestamp)}
    statuses = {}
    def trace(row):
        statuses[int(row["timestamp"])] = row["status"]
    # Re-run with a compact trace callback to recover the actual status on event candles.
    # The first result remains the authoritative metrics/events result.
    return events, idx_by_ts, statuses, trace


def _events_timeline(symbol, interval, resync, frame=None):
    if frame is None:
        frame, _meta = _window(symbol, interval)
    frame = frame.copy().reset_index(drop=True)
    # Trace status during the same deterministic run.
    low, high, n = RANGES[symbol]
    filters = _filters_for(symbol)
    frame.attrs["gaps"] = 0
    statuses = {}
    def trace(row):
        statuses[int(row["timestamp"])] = row["status"]
    traced = E1E.run_simulation(E1E.to_candles(frame, interval), strategy="smart", n=n,
                                capital=CAPITAL, low=low, high=high, fee_pct=FEE_PCT,
                                resync_candles=resync, params=None, filters=filters,
                                include_details=False, trace_callback=trace)
    events = traced["events"]
    candles = E1E.to_candles(frame, interval)
    idx_by_ts = {int(ts): i for i, ts in enumerate(candles.timestamp)}
    priority = {"PAUSE", "RESUME", "CLOSE_REPOSITORY", "ADJUST", "ADJUST_REJECTED"}
    selected = [e for e in events if e.get("type") in priority]
    chosen = events[:10] + selected
    unique = {(int(e.get("ts", -1)), e.get("type"), e.get("level_idx")): e for e in chosen}
    ordered = sorted(unique.values(), key=lambda e: (int(e.get("ts", -1)), str(e.get("type"))))
    last_state = "ACTIVE"
    rows = []
    for event in ordered:
        ts = int(event.get("ts", 0))
        idx = idx_by_ts.get(ts)
        after = statuses.get(ts, last_state)
        before = last_state
        if idx is not None and idx > 0:
            prev_ts = int(candles.timestamp[idx - 1])
            before = statuses.get(prev_ts, before)
        reason = event.get("reason") or event.get("details", {}).get("decision", {}).get("close_reasons") or "—"
        if isinstance(reason, (list, tuple)):
            reason = ", ".join(map(str, reason))
        stamp = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        rows.append({"date": stamp, "type": event.get("type"), "reason": str(reason),
                     "before": before, "after": after, "idx": idx})
        last_state = after
    close_rows = [row for row in rows if row["type"] == "CLOSE_REPOSITORY"]
    close_info = None
    if close_rows:
        close_idx = close_rows[0]["idx"]
        close_ts = int(candles.timestamp[close_idx]) if close_idx is not None else None
        later_buys = [e for e in events if e.get("type") == "BUY_FILLED" and close_ts is not None
                      and int(e["ts"]) > close_ts]
        close_info = {"date": close_rows[0]["date"], "status_after": close_rows[0]["after"],
                      "buy_fills_after_close": len(later_buys)}
    return frame, traced, rows, close_info


def main():
    payload = {"ada_pepe": {}, "xrp": [], "generated_utc": datetime.now(timezone.utc).isoformat()}
    for symbol in ("ADA", "PEPE"):
        E1E.SIM_FILTERS = _filters_for(symbol)
        frame, meta = _window(symbol, "1h")
        frame.attrs["gaps"] = 0
        low, high, n = RANGES[symbol]
        candles = E1E.to_candles(frame, "1h")
        baseline = {}
        attribution = {}
        for strategy in ("simple", "smart"):
            result = E1E.run(candles, strategy=strategy, n=n, low=low, high=high,
                             resync=3, details=True)
            baseline[strategy] = result
            attribution[strategy] = _sale_attribution(frame, result)
        sweep = []
        for stop in (5, 8, 10, 15, 25):
            result = E1E.run(candles, strategy="smart", n=n, low=low, high=high,
                             resync=3, params={"stop_loss_pct": float(stop)}, details=False)
            sweep.append({"stop": stop, "cycles": result["metrics"]["cycles_completed"],
                          "pnl": result["metrics"]["pnl_total_net_usdt"],
                          "fees": result["metrics"]["fees_usdt"],
                          "dd": result["metrics"]["max_drawdown_pct"],
                          "stops": sum(e.get("type") == "STOP_LOSS" for e in result["events"])})
        sigmas = _realized_sigmas(frame)
        payload["ada_pepe"][symbol] = {
            "window_start": frame.timestamp.iloc[0].isoformat(),
            "window_end": frame.timestamp.iloc[-1].isoformat(),
            "window_days": float(meta["sim_days"]), "candles": len(frame),
            "low": low, "high": high, "n": n,
            "metrics": {strategy: E1E.compact_metrics(result)
                        for strategy, result in baseline.items()},
            "attribution": attribution, "sweep": sweep,
            "sigma": sigmas,
        }

    xlow, xhigh, xn = RANGES["XRP"]
    hourly, hourly_meta = _window("XRP", "1h")
    start_ts = pd.to_datetime(hourly.timestamp.iloc[0], utc=True)
    end_exclusive = pd.to_datetime(hourly.timestamp.iloc[-1], utc=True) + pd.Timedelta(hours=1)
    five = E1E.frame_for("xrp", "5m")
    five = five.loc[(five.timestamp >= start_ts) & (five.timestamp < end_exclusive)].copy().reset_index(drop=True)
    first = np.flatnonzero((five.close.to_numpy(float) > xlow) & (five.close.to_numpy(float) < xhigh))
    if not len(first):
        raise RuntimeError("XRP 5m window has no close strictly within fallback range")
    five = five.iloc[int(first[0]):].reset_index(drop=True)
    five.attrs["gaps"] = 0
    for interval, resync, frame in (("1h", 1, hourly), ("5m", 3, five), ("5m", 36, five)):
        smart_frame, smart_result, timeline, close = _events_timeline("XRP", interval, resync, frame)
        simple = E1E.run_simulation(E1E.to_candles(smart_frame, interval), strategy="simple", n=xn,
                                    capital=CAPITAL, low=xlow, high=xhigh, fee_pct=FEE_PCT,
                                    resync_candles=resync, params=None, filters=_filters_for("XRP"))
        payload["xrp"].append({
            "scenario": f"{interval}/resync={resync}",
            "start": frame.timestamp.iloc[0].isoformat(),
            "end": frame.timestamp.iloc[-1].isoformat(), "candles": len(frame),
            "smart": E1E.compact_metrics(smart_result),
            "simple": E1E.compact_metrics(simple), "timeline": timeline,
            "close": close,
        })

    report = render_report(payload)
    target = ROOT / "REPORTE_FASE20E1f.md"
    target.write_text(report, encoding="utf-8", newline="\n")
    summary = {
        symbol: {"simple": row["metrics"]["simple"]["pnl_net"],
                 "smart": row["metrics"]["smart"]["pnl_net"],
                 "stops_5pct": row["sweep"][0]["stops"],
                 "sweep_pnl": {str(item["stop"]): item["pnl"] for item in row["sweep"]}}
        for symbol, row in payload["ada_pepe"].items()
    }
    print(json.dumps({"report": str(target), "summary": summary,
                      "xrp_cycles": [{"scenario": row["scenario"],
                                      "smart": row["smart"]["cycles"],
                                      "simple": row["simple"]["cycles"],
                                      "close": row["close"]} for row in payload["xrp"]]},
                     ensure_ascii=False, indent=2))


def _fmt(value, digits=2):
    return "—" if value is None else f"{float(value):,.{digits}f}"


def render_report(data):
    lines = [
        "# Fase 20E-1f — medición de causas de pérdida Smart",
        "",
        "Medición offline y de solo lectura con los CSV cacheados `data/cache/vol_train/`; no se consultó la red ni la base de datos viva. Se reutilizan `frame_for`, `to_candles`, `run`, `compact_metrics` y `_simulation_window` del diagnóstico 20E-1e. Capital 1.000 USDT, comisión 0,1 %, 90 días disponibles, resync 3 salvo donde se indica.",
        "",
        "## 1. Atribución de ventas y stop-loss",
        "",
        "P&L neto por venta reconstruido del evento y del fill de compra asociado por celda; comisiones por clase incluyen la compra asociada y su venta, por lo que no suman comisiones de compras aún abiertas ni polvo residual. STOP_LOSS usa el cierre de esa vela (así lo hace el runner). Una recompra cuenta si hay BUY_FILLED de la misma celda en las 12 velas siguientes. Se reportan P&L realizado posterior al primer stop y P&L acumulado final de las celdas afectadas.",
        "",
        "| Moneda | Estrategia | Clase de venta | Ventas | P&L neto USDT | Comisiones USDT |",
        "|---|---|---|---:|---:|---:|",
    ]
    for symbol, row in data["ada_pepe"].items():
        for strategy in ("smart", "simple"):
            attr = row["attribution"][strategy]
            for name, vals in attr["buckets"].items():
                lines.append(f"| {symbol} | {strategy} | {name} | {vals['count']} | {_fmt(vals['pnl'])} | {_fmt(vals['fees'])} |")
            lines.append(f"| {symbol} | {strategy} | STOP_LOSS: recompra misma celda ≤12 velas | {attr['stop_count']} stops; {attr['rebought_12']} ({_fmt(attr['rebought_pct'])} %) | — | — |")
            if strategy == "smart":
                lines.append(f"| {symbol} | smart | Después del primer STOP_LOSS: P&L realizado en celdas afectadas | — | {_fmt(attr['post_stop_realized_pnl'])} | — |")
                lines.append(f"| {symbol} | smart | P&L final acumulado de celdas afectadas | — | {_fmt(attr['stop_cell_pnl_final'])} | — |")
    lines += [
        "",
        "| Moneda | Estrategia | Ciclos | STOP_LOSS | P&L neto total | Comisiones total | DD máx. |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for symbol, row in data["ada_pepe"].items():
        for strategy in ("simple", "smart"):
            m = row["metrics"][strategy]
            stops = row["attribution"][strategy]["stop_count"]
            lines.append(f"| {symbol} | {strategy} | {m['cycles']} | {stops} | {_fmt(m['pnl_net'])} | {_fmt(m['fees'])} | {_fmt(m['drawdown_pct'])} % |")
    lines += [
        "",
        "`cycles_completed` cuenta únicamente `SELL_FILLED`: `grid/sim/exchange.py:105-111` incrementa `cell['cycles_completed']` al completar esa venta normal. `market_sell()` (`grid/sim/exchange.py:114-127`) registra P&L/comisión pero no incrementa ciclos; por eso STOP_LOSS y ventas de TARGET no son ciclos. `ADJUST` no vende posiciones: remapea/cancela órdenes (`grid/sim/runner.py:304-344`), por lo que no crea ventas forzadas. La categoría ADJUST/reconstrucción se conserva en tabla y debe dar cero si el motor no emitió ventas de ese tipo.",
        "",
        "## 2. Barrido de `stop_loss_pct`",
        "",
        "Cada fila cambia solo `params.stop_loss_pct`; las demás reglas Smart quedan activas. El runner comprueba stop-loss solo en cada resync, contra el cierre de la vela (`grid/sim/runner.py:153-168`), por lo que no es un stop intravela continuo. σ realizada: desviación estándar móvil de retornos logarítmicos con ventana de 4 o 24 velas horarias, expresada como fracción del precio (×100 para porcentaje). Movimiento adverso: mínimo low entre compra y venta por lote, relativo al precio de entrada; se muestran mediana/P90/máximo. Para PEPE, debido a que `SIM_FILTERS` del helper E1e tiene tick 0,0001 incompatible con este rango, se usó localmente un filtro sintético (tick 1e-9, step 1, minNotional 5 USDT); no es una lectura de filtros Binance y limita la fuerza de la comparación.",
        "",
        "| Moneda | Stop % | Ciclos | P&L neto | Comisiones | DD máx. % | STOP_LOSS |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for symbol, row in data["ada_pepe"].items():
        for item in row["sweep"]:
            lines.append(f"| {symbol} | {item['stop']} | {item['cycles']} | {_fmt(item['pnl'])} | {_fmt(item['fees'])} | {_fmt(item['dd'])} | {item['stops']} |")
    lines += ["", "| Moneda | σ realizada 4 h media / mediana | σ realizada 24 h media / mediana | EWMA interna 24 h media / mediana | Adverso mediana / P90 / máximo | Lotes medidos |", "|---|---|---|---|---|---:|"]
    for symbol, row in data["ada_pepe"].items():
        sig = row["sigma"]
        adv = row["attribution"]["smart"]["adverse"]
        def stats(vals):
            return (f"{_fmt(float(np.median(vals)))} / {_fmt(float(np.percentile(vals, 90)))} / {_fmt(float(np.max(vals)))} %" if vals else "—")
        lines.append(f"| {symbol} | {_fmt(sig[4]['mean']*100)} % / {_fmt(sig[4]['median']*100)} % | {_fmt(sig[24]['mean']*100)} % / {_fmt(sig[24]['median']*100)} % | {_fmt(sig['ewma_24h_mean']*100)} % / {_fmt(sig['ewma_24h_median']*100)} % (4 h media {_fmt(sig['ewma_4h_mean']*100)} %) | {stats(adv)} | {len(adv)} |")
    lines += ["", "En ambas monedas el movimiento adverso mediano (1,24 % ADA; 1,34 % PEPE) queda por debajo de 5 %, pero el P90 (6,00 %; 6,99 %) lo supera: el 5 % cae en la cola de excursiones y su activación puede realizar pérdidas antes de la recuperación del grid.", ""]
    lines += [
        "",
        "## 3. XRP: colapso de ciclos Smart según cadencia",
        "",
        "Rango fallback del análisis 20E-1e, no una predicción en vivo: piso 1,406842013993292; techo 1,5940000566479335; n=13. Para 5 m se usa la misma ventana horaria y se recorta al primer cierre dentro del rango, igual que 20E-1e. La línea temporal contiene los primeros diez eventos del registro y todos los eventos prioritarios (PAUSE/RESUME/CLOSE_REPOSITORY/ADJUST/ADJUST_REJECTED), sin duplicados.",
        "",
        "| Escenario | Estrategia | Ciclos | P&L neto | Cierre / estado posterior / compras posteriores |",
        "|---|---|---:|---:|---|",
    ]
    for row in data["xrp"]:
        close = row["close"]
        close_text = "sin CLOSE_REPOSITORY" if not close else f"{close['date']} / {close['status_after']} / {close['buy_fills_after_close']} BUY_FILLED después"
        for strategy in ("simple", "smart"):
            m = row[strategy]
            lines.append(f"| {row['scenario']} | {strategy} | {m['cycles']} | {_fmt(m['pnl_net'])} | {close_text if strategy == 'smart' else '—'} |")
        lines += ["", f"**Eventos Smart — {row['scenario']}**", "", "| Fecha UTC | Evento | Motivo | Estado antes → después |", "|---|---|---|---|"]
        for event in row["timeline"]:
            lines.append(f"| {event['date']} | {event['type']} | {event['reason']} | {event['before']} → {event['after']} |")
        lines.append("")
    lines += [
        "La causa medida de los recuentos bajos es el cierre temprano, no un ritmo de órdenes: 1h×1 cierra el 22-ago 04:00 UTC (CLOSED); 5m×3 cierra 04:45 UTC (CLOSED); 5m×36 cierra 03:45 UTC (HOLDING). En los tres escenarios hay cero BUY_FILLED después del cierre, mientras Simple completa 200/247/152 ciclos porque no aplica la decisión Smart CLOSE_REPOSITORY. `CLOSE_REPOSITORY` cancela compras y deja HOLDING solo para posiciones abiertas que esperan venta, o CLOSED si no queda inventario (`grid/sim/runner.py:225-267`); el bucle sigue procesando velas, pero no repone compras.",
        "",
        "## 4. Cadencia",
        "",
        "`grid_monitor_interval` tiene default de 900 s (15 min): `config/settings.py:40`; `grid/monitor.py:70-82` programa la pasada periódica y una pasada inicial. Cada pasada obtiene precios y niveles, ejecuta STOP_LOSS antes de sincronizar, sincroniza según ACTIVE/PAUSED/HOLDING, obtiene σ, evalúa la política Smart y puede pausar, ajustar, reanudar o cerrar (`grid/monitor.py:391-488`; aplicación de decisiones en las líneas siguientes). En simulación, el mercado/fills procesa cada vela (`grid/sim/runner.py:145-152`), pero evaluación de política y colocación/recolocación de órdenes ocurre en cada múltiplo de `resync_candles` (`:153-184`, `:431-444`). Cadencias equivalentes: 1 h×1=1 h; 5 m×3=15 min; 5 m×36=3 h. La corrida principal ADA/PEPE usa 1 h×3=3 h, doce veces más lenta que el monitor configurado; aun así, el monitor real opera por tiempo y recibe actualizaciones de mercado, no se equipara exactamente a velas históricas.",
        "",
        "## 5. Unidades de σ",
        "",
        "`ewma_sigma_24h` (`grid/sim/data.py:77-86`) calcula retornos logarítmicos, alpha para 72 h bajo 12 velas/h y devuelve `sqrt(variance×288)`: fracción, no porcentaje, anualizada a 24 h suponiendo velas de 5 min. `evaluate_grid` reenvía `sigma_24h` a `break_prob` (`grid/policy.py:576-602`); `break_prob` espera sigma fraccional de 24 h y la escala a `sigma_h = sigma_24h × sigma_scale × sqrt(horizon_h/24)` (`:459-486`). En estas corridas horarias, el promedio EWMA medido es 9,18 % ADA y 11,10 % PEPE, frente a σ realizada rolling 24 h de 0,85 % y 1,00 %. La causa principal del desacople es que la función fija 288 barras/día y alpha de 12 barras/h: con datos horarios, el alpha implica half-life real de 864 velas = 36 días, y el factor 288 en vez de 24 puede inflar la escala hasta √12 para varianza comparable. Por tanto no se comparan directamente como estimadores calibrados. El sigma del campeón queda NO VERIFICADO: este análisis solo lee CSV y código, no artefactos/DB.",
        "",
        "## 6. Veredicto (cinco líneas)",
        "",
        f"1. **Stop-loss 5 %:** parcial: PEPE Smart {data['ada_pepe']['PEPE']['metrics']['smart']['pnl_net']:.2f} vs Simple {data['ada_pepe']['PEPE']['metrics']['simple']['pnl_net']:.2f}; 8 % mejora {_fmt(data['ada_pepe']['PEPE']['sweep'][1]['pnl']-data['ada_pepe']['PEPE']['sweep'][0]['pnl'])} USDT y 25 % mejora {_fmt(data['ada_pepe']['PEPE']['sweep'][4]['pnl']-data['ada_pepe']['PEPE']['sweep'][0]['pnl'])}, pero baja de 238 a 68 ciclos.",
        f"2. **ADA:** 5→10 % mejora {_fmt(data['ada_pepe']['ADA']['sweep'][2]['pnl']-data['ada_pepe']['ADA']['sweep'][0]['pnl'])} USDT; aun así el barrido no demuestra una regla universal (PEPE 10 % empeora frente a 8 %).",
        "3. **Cambio candidato, no aplicado:** evaluar `stop_loss_pct` configurable por símbolo/régimen, sin subirlo globalmente; 25 % elimina estos stops pero cambia radicalmente los ciclos y no prueba seguridad.",
        "4. **Cadencia del Advisor:** sí, comparar 5m×3 (15 min, como monitor) con 1h×3 (3 h) antes de usar Smart para decidir; la propia XRP muestra sensibilidad de 25 a 6 ciclos.",
        "5. **Límites:** filtros sintéticos de PEPE, σ del campeón y equivalencia exacta con la captura UI NO VERIFICADOS; resultados de PEPE son indicativos, no certificados.",
        "",
        "## No verificado y alcance",
        "",
        "- No se comparó contra la captura exacta de PEPEUSDT de UI (piso/techo, capital y configuración sí se conservaron, pero no se confirmó que la ventana/data coincidan).",
        "- No se consultó la σ del campeón de volatilidad: requeriría artefactos/manifest o API/DB, fuera del alcance de CSV solamente.",
        "- La comisión por venta STOP_LOSS se reconstruye con qty neta × close × 0,1 %; el evento STOP_LOSS solo guarda P&L, no detalla la comisión por separado.",
        "- La atribución por celda empareja FIFO por `level_idx`; si un ADJUST reusa una celda con lotes previos no resueltos, ese emparejamiento puede no reflejar una identidad económica distinta. Se reportan los eventos ADJUST para contextualizarlo.",
        "- Para PEPEUSDT no se consultaron filtros de exchange; se usó precisión sintética suficiente para el rango de precio proporcionado, por lo que la simulación no certifica redondeo/notional reales.",
        "- No se ejecutaron pruebas; no se editó código de producción/pruebas, no se tocó la base viva ni se consultó la red.",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
