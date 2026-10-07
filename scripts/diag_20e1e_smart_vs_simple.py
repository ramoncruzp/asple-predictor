from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api.routes.grid_advisor import _simulation_window, recommend
from config.models_config import VOL_SYMBOL
from grid.sim.data import CandleData, ewma_sigma_24h
from grid.sim.runner import FILTERS as SIM_FILTERS, run_simulation

CACHE = ROOT / "data" / "cache" / "vol_train"
ADA_RANGE = (0.2336, 0.2762, 18)
CAPITAL = 1000.0
FEE_PCT = 0.1
DAYS = 90


def frame_for(symbol: str, interval: str) -> pd.DataFrame:
    path = CACHE / f"{symbol.lower()}_{interval}.csv"
    frame = pd.read_csv(path, parse_dates=["timestamp"])
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    frame = frame.sort_values("timestamp").reset_index(drop=True)
    frame.attrs["path"] = str(path.relative_to(ROOT))
    frame.attrs["rows_total"] = len(frame)
    frame.attrs["first_total"] = frame.timestamp.iloc[0].isoformat()
    frame.attrs["last_total"] = frame.timestamp.iloc[-1].isoformat()
    cutoff = frame.timestamp.iloc[-1] - pd.Timedelta(days=DAYS)
    return frame.loc[frame.timestamp >= cutoff].reset_index(drop=True)


def to_candles(frame: pd.DataFrame, interval: str) -> CandleData:
    stamps = pd.to_datetime(frame.timestamp, utc=True).astype("int64").to_numpy() // 1_000_000_000
    gaps = int(frame.attrs.get("gaps", 0))
    return CandleData(
        timestamp=stamps.astype(np.int64),
        open=frame.open.to_numpy(dtype=float),
        high=frame.high.to_numpy(dtype=float),
        low=frame.low.to_numpy(dtype=float),
        close=frame.close.to_numpy(dtype=float),
        gaps=gaps,
    )


def advisor_xrp(advisor_frame: pd.DataFrame) -> dict:
    class CacheClient:
        def get_historical_klines(self, symbol, interval, lookback_days=90):
            return advisor_frame.copy()

    state = SimpleNamespace(
        db=None,
        vol_registry=None,
        client=CacheClient(),
        settings=SimpleNamespace(scanner_fee_pct=FEE_PCT),
        grid_scan_service=None,
        prediction_loop=SimpleNamespace(latest={}),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    import api.routes.volatility as volatility_route
    original_forecast = volatility_route.forecast
    try:
        # Keep recommendation calculation offline: no DB-backed forecast lookup.
        volatility_route.forecast = lambda *_args, **_kwargs: {"forecasts": []}
        return recommend(request, symbol="XRPUSDT", capital=CAPITAL, risk="medium", days=DAYS, margin_target_pct=0.7, range_mode="centrado")
    finally:
        volatility_route.forecast = original_forecast


def compact_metrics(result: dict) -> dict:
    m = result["metrics"]
    cycles = int(m["cycles_completed"])
    return {
        "cycles": cycles,
        "pnl_net": m["pnl_total_net_usdt"],
        "fees": m["fees_usdt"],
        "drawdown_pct": m["max_drawdown_pct"],
        "buy_hold": m["buy_hold_pnl_usdt"],
        "pnl_per_cycle": m["pnl_total_net_usdt"] / cycles if cycles else None,
        "fees_per_cycle": m["fees_usdt"] / cycles if cycles else None,
        "cycles_completed_definition": "sum(c['cycles_completed'] for c in cells)",
        "cycles_metric": "grid/sim/metrics.py:20",
    }


def event_analysis(result: dict) -> dict:
    events = result["events"]
    counts = {
        "buy_fills": sum(e.get("type") == "BUY_FILLED" for e in events),
        "sell_fills": sum(e.get("type") == "SELL_FILLED" for e in events),
        "stop_loss": sum(e.get("type") == "STOP_LOSS" for e in events),
        "adjust": sum(e.get("type") == "ADJUST" for e in events),
        "adjust_rejected": sum(e.get("type") == "ADJUST_REJECTED" for e in events),
        "pause": sum(e.get("type") == "PAUSE" for e in events),
        "resume": sum(e.get("type") == "RESUME" for e in events),
        "target": sum(e.get("type") in {"TARGET", "TARGET_REACHED"} for e in events),
        "max_days": sum(e.get("type") in {"MAX_DAYS", "MAX_DAYS_REACHED"} for e in events),
    }
    queues = {}
    paired = same_candle = 0
    for event in events:
        level = event.get("level_idx")
        if level is None:
            continue
        if event.get("type") == "BUY_FILLED":
            queues.setdefault(level, []).append(event.get("ts"))
        elif event.get("type") == "SELL_FILLED" and queues.get(level):
            buy_ts = queues[level].pop(0)
            paired += 1
            same_candle += buy_ts == event.get("ts")
    counts["other"] = len(events) - sum(counts[key] for key in counts)
    counts["same_candle_pairs"] = same_candle
    counts["paired_cycles_from_fills"] = paired
    counts["same_candle_pct_of_paired"] = same_candle * 100.0 / paired if paired else None
    return counts


def run(candles: CandleData, *, strategy: str, n: int, low: float, high: float,
        resync: int, params: dict | None = None, details: bool = False) -> dict:
    return run_simulation(
        candles, strategy=strategy, n=n, capital=CAPITAL, low=low, high=high,
        fee_pct=FEE_PCT, resync_candles=resync, params=params,
        filters=SIM_FILTERS, include_details=details,
    )


def window_stats(frame: pd.DataFrame, low: float, high: float) -> dict:
    close = frame.close.to_numpy(dtype=float)
    inside = (close >= low) & (close <= high)
    outside = ~inside
    exits = int(np.sum(inside[:-1] & outside[1:])) if len(inside) > 1 else 0
    reentries = int(np.sum(outside[:-1] & inside[1:])) if len(inside) > 1 else 0
    return {
        "start": frame.timestamp.iloc[0].isoformat(),
        "end": frame.timestamp.iloc[-1].isoformat(),
        "candles": int(len(frame)),
        "days": (frame.timestamp.iloc[-1] - frame.timestamp.iloc[0]).total_seconds() / 86400.0,
        "close_start": float(close[0]),
        "close_end": float(close[-1]),
        "close_min": float(close.min()),
        "close_max": float(close.max()),
        "outside_pct": float(outside.mean() * 100.0),
        "outside_candles": int(outside.sum()),
        "exits": exits,
        "reentries": reentries,
        "range_low": low,
        "range_high": high,
    }


def event_counts_by_type(events: list[dict]) -> dict:
    counts = {}
    for event in events:
        kind = str(event.get("type", "other"))
        counts[kind] = counts.get(kind, 0) + 1
    return dict(sorted(counts.items()))


def nfmt(value, digits=2):
    return "NO VERIFICADO" if value is None else f"{float(value):,.{digits}f}"


def render_report(payload: dict) -> str:
    scenarios = payload["scenarios"]
    source = payload["source_csvs"]
    lines = [
        "# Fase 20E-1e - diagnóstico Smart vs Simple",
        "",
        "Diagnóstico de solo lectura: usa CSV de `data/cache/vol_train/` y código. No consulta la base viva ni modifica producción.",
        "",
        "## Datos de entrada",
        "",
        "| CSV | Filas | Rango de fechas UTC | Filas en últimos 90 días |",
        "|---|---:|---|---:|",
    ]
    for key in ("ada_1h", "ada_5m", "xrp_1h", "xrp_5m"):
        row=source[key]
        lines.append(f"| `{row['path']}` | {row['rows_total']} | {row['first_total']} a {row['last_total']} | {row['rows_90d']} ({row['first_90d']} a {row['last_90d']}) |")
    lines += ["", "Los CSV terminan en fechas distintas: ADA llega a 2026-10-07 y XRP a 2026-10-06. La ventana de simulación empieza en la primera vela horaria cuyo cierre entra estrictamente en el rango, según `api/routes/grid_advisor.py:50-69`.", ""]
    for symbol in ("ada", "xrp"):
        sc=scenarios[symbol]
        lines.append(f"- **{symbol.upper()}**: piso {nfmt(sc['low'],6)}, techo {nfmt(sc['high'],6)}, n={sc['n']}; rango obtenido de: {sc['range_source']}.")
        lines.append(f"  Ventana 1 h: {sc['window_start']} a {sc['window_end']} ({nfmt(sc['window_days'],2)} días, {sc['hourly_candles']} velas).")
        if symbol == "xrp":
            lines.append(f"  `recommend()` devolvió `range_mode={sc['range_mode']}`, precio actual {nfmt(sc['current_price'],6)}, fuente de sigma `{sc['vol_source_effective']}`; se hizo inaccesible el pronóstico para mantener la llamada sin DB y usar el fallback realizado. No equivale a una recomendación con artefactos/consenso en vivo.")
    lines += ["", "## M0 - reproducción 1 h, `resync_candles=3`", "", "| Moneda | Estrategia | Ciclos | P&L neto | Comisiones | Drawdown máx. | Comprar y mantener | P&L/ciclo | Comisión/ciclo |", "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for symbol in ("ada", "xrp"):
        sc=scenarios[symbol]
        for strat in ("simple","smart"):
            m=sc["M0"][strat]
            lines.append(f"| {symbol.upper()} | {strat} | {m['cycles']} | {nfmt(m['pnl_net'])} | {nfmt(m['fees'])} | {nfmt(m['drawdown_pct'])}% | {nfmt(m['buy_hold'])} | {nfmt(m['pnl_per_cycle'],4)} | {nfmt(m['fees_per_cycle'],4)} |")
    lines += ["", "Comparación con la pantalla de ADA: la pantalla reportó Simple 107 ciclos / P&L +24,97 / fees 12,44 / DD 3,56%; Smart 326 / -3,22 / 35,95 / 3,51%; buy-and-hold +49,15. La reproducción caché conserva 107/326 ciclos, pero los P&L y buy-and-hold difieren; el CSV llega más tarde (2026-10-07) y por eso cambian los cierres de la ventana. No se interpretan como divergencia del motor.", "", "## M1-M2 - eventos y ciclos", "", "`cycles_completed` es `sum(c['cycles_completed'] for c in cells)` (`grid/sim/metrics.py:20`), es decir, suma los contadores de ciclo por celda. El porcentaje same-candle empareja cada `SELL_FILLED` con el `BUY_FILLED` previo de la misma celda y compara sus timestamps; el denominador son los pares de fills reconstruidos.", "", "| Moneda | Estrategia | BUY/SELL fills | STOP_LOSS | ADJUST / rechazado | PAUSE / RESUME | TARGET | MAX_DAYS | Otros | Ciclos same-candle | P&L/ciclo | Fees/ciclo |", "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for symbol in ("ada","xrp"):
        sc=scenarios[symbol]
        for strat in ("simple","smart"):
            e=sc["M1_M2_events"][strat]; m=sc["M0"][strat]
            lines.append(f"| {symbol.upper()} | {strat} | {e['buy_fills']}/{e['sell_fills']} | {e['stop_loss']} | {e['adjust']} / {e['adjust_rejected']} | {e['pause']} / {e['resume']} | {e['target']} | {e['max_days']} | {e['other']} | {e['same_candle_pct_of_paired'] or 0:.2f}% ({e['same_candle_pairs']}/{e['paired_cycles_from_fills']}) | {nfmt(m['pnl_per_cycle'],4)} | {nfmt(m['fees_per_cycle'],4)} |")
    lines += ["", "## M3 - cadencia en velas de 1 h", "", "| Moneda | resync | Simple ciclos / P&L / fees | Smart ciclos / P&L / fees |", "|---|---:|---|---|"]
    for symbol in ("ada","xrp"):
        for cadence in ("1","3","6","12"):
            row=scenarios[symbol]["M3"][cadence]
            a,b=row["simple"],row["smart"]
            lines.append(f"| {symbol.upper()} | {cadence} | {a['cycles']} / {nfmt(a['pnl_net'])} / {nfmt(a['fees'])} | {b['cycles']} / {nfmt(b['pnl_net'])} / {nfmt(b['fees'])} |")
    lines += ["", "## M4 - velas de 5 min en la misma ventana", "", "La ventana UTC coincide con la de 1 h; por el requisito de inicialización del simulador, se recorta el prefijo hasta el primer cierre de 5 min estrictamente dentro del rango (diferencia máxima inferior a 5 min). `resync=3` equivale a 15 min y `resync=36` a 3 h.", "", "| Moneda | resync 5m | Simple ciclos / P&L / fees | Smart ciclos / P&L / fees |", "|---|---:|---|---|"]
    for symbol in ("ada","xrp"):
        sc=scenarios[symbol]
        lines.append(f"Ventana {symbol.upper()}: {sc['window_5m_stats']['start']} a {sc['window_5m_stats']['end']}, {sc['window_5m_stats']['candles']} velas.")
        for cadence in ("3","36"):
            row=sc["M4"][cadence]; a,b=row["simple"],row["smart"]
            lines.append(f"| {symbol.upper()} | {cadence} | {a['cycles']} / {nfmt(a['pnl_net'])} / {nfmt(a['fees'])} | {b['cycles']} / {nfmt(b['pnl_net'])} / {nfmt(b['fees'])} |")
    lines += ["", "## M5 - ablación de Smart, 1 h / resync=3", "", "Solo cambia el parámetro mostrado. `pause_enter_prob=None` y `pause_exit_prob=None` desactivan las reglas probabilísticas de pausa; otras condiciones de política (capital atrapado/celdas libres) siguen activas. `stop_loss_pct` no admite `None` porque `validate_params` exige que sea >0 (`grid/policy.py:154-161`); se usa 1.000.000% como umbral centinela, NO como interruptor semántico.", "", "| Moneda | Variante | Ciclos | P&L neto | Fees | Δ ciclos vs default | Δ P&L vs default | Eventos Smart |", "|---|---|---:|---:|---:|---:|---:|---|"]
    for symbol in ("ada","xrp"):
        sc=scenarios[symbol]
        m=sc["M5"]["default"]["metrics"]
        lines.append(f"| {symbol.upper()} | default | {m['cycles']} | {nfmt(m['pnl_net'])} | {nfmt(m['fees'])} | 0 | 0,00 | {sc['M5']['default']['event_types']} |")
        for name,row in sc["M5"]["ablations"].items():
            m=row["metrics"]
            lines.append(f"| {symbol.upper()} | {name} | {m['cycles']} | {nfmt(m['pnl_net'])} | {nfmt(m['fees'])} | {row['delta_cycles_vs_default']} | {nfmt(row['delta_pnl_vs_default'])} | {row['event_types']} |")
    lines += ["", "## M6 - recorrido del precio en la ventana 1 h", "", "| Moneda | Inicio / fin | Mín. / máx. cierre | Fuera del rango | Salidas / reentradas |", "|---|---|---|---:|---:|"]
    for symbol in ("ada","xrp"):
        w=scenarios[symbol]["M6"]
        lines.append(f"| {symbol.upper()} | {w['close_start']} / {w['close_end']} | {w['close_min']} / {w['close_max']} | {nfmt(w['outside_pct'])}% ({w['outside_candles']}/{w['candles']}) | {w['exits']} / {w['reentries']} |")
    lines += ["", "## M7 - sigma interna", "", "| Moneda | Media / mín. / máx. sigma 24 h | Primeras seis | NaN iniciales | Comparación campeón/consenso |", "|---|---|---|---:|---|"]
    for symbol in ("ada","xrp"):
        w=scenarios[symbol]["M7"]
        initial=', '.join(nfmt(x,6) for x in w['sigma_initial'])
        lines.append(f"| {symbol.upper()} | {nfmt(w['sigma_mean'],6)} / {nfmt(w['sigma_min'],6)} / {nfmt(w['sigma_max'],6)} | {initial} | {w['initial_nan_count']} (cero iniciales: {w['initial_zero_count']}) | NO VERIFICADO: no se consultó forecast, artefactos de modelo ni DB. |")
    lines += ["", "EWMA interna: halflife 72 h; `run_simulation` no recibe `sigma_values`, construye la sigma de las velas recortadas (`grid/sim/runner.py:126-131`, `grid/sim/data.py:77-86`). Así, no hay NaN pero sí un cero inicial y calentamiento desde el inicio de la ventana.", "", "## Respuestas", ""]
    ada=scenarios['ada']; xrp=scenarios['xrp']
    lines.append(f"1. Con los inputs medidos, ADA Smart completa {ada['M0']['smart']['cycles']-ada['M0']['simple']['cycles']} ciclos más que Simple. XRP da {xrp['M0']['simple']['cycles']} Simple y {xrp['M0']['smart']['cycles']} Smart; NO reproduce la captura anterior de 142/126. Se usó un rango obtenido por recommend() con fallback realizado offline, así que la diferencia vieja no queda explicada por esta reproducción. M1/M5 sí muestran mecanismos bajo este rango medido.")
    lines.append("2. Los ciclos no se explican por compras y ventas del mismo timestamp: M2 informa el porcentaje observado. Son fills emparejados entre velas. M3 y M4 muestran la sensibilidad a la frecuencia de evaluación; como varía con la cadencia, no se puede atribuir toda la diferencia a la política sin fijar esa cadencia.")
    ada_base=ada["M0"]["smart"]; ada_sl=ada["M5"]["ablations"]["stop_loss_1000000pct_sentinel"]["metrics"]; ada_adj=ada["M5"]["ablations"]["adjust_off"]["metrics"]
    xrp_base=xrp["M0"]["smart"]; xrp_sl=xrp["M5"]["ablations"]["stop_loss_1000000pct_sentinel"]["metrics"]; xrp_adj=xrp["M5"]["ablations"]["adjust_off"]["metrics"]
    xrp_pause=xrp["M5"]["ablations"]["pause_off"]["metrics"]
    lines.append(f"3. Stop-loss centinela: ADA P&L {nfmt(ada_base['pnl_net'])} a {nfmt(ada_sl['pnl_net'])} y ciclos {ada_base['cycles']} a {ada_sl['cycles']}; XRP {nfmt(xrp_base['pnl_net'])} a {nfmt(xrp_sl['pnl_net'])} y ciclos {xrp_base['cycles']} a {xrp_sl['cycles']}. Desactivar ajuste reduce el P&L ADA a {nfmt(ada_adj['pnl_net'])} y XRP a {nfmt(xrp_adj['pnl_net'])}; quitar pausa deja XRP en {nfmt(xrp_pause['pnl_net'])}. Los efectos son ablaciones individuales, no aditivos. Las fees explican parte del coste, pero el P&L neto por ciclo y los STOP_LOSS muestran que el resultado no se atribuye solo a comisiones. Stop-loss no admite apagado estricto por params.")
    lines.append("4. La sigma interna empieza en cero, sin NaN, y converge causalmente dentro de la ventana recortada; su calentamiento inicial puede alterar decisiones Smart de las primeras horas. La comparación cuantitativa con campeón/consenso queda NO VERIFICADA.")
    lines.append("5. Los parámetros Smart de Advisor vienen de `DEFAULT_SMART_PARAMS` al no pasar `params` (`api/routes/grid_advisor.py:295-296`, `grid/sim/runner.py:79-99`, `grid/policy.py:12-44`). La apertura Smart también aplica esos defaults y fija `horizon_h=4` si no se envía (`api/routes/grids.py:184-190`). Diferencia material: Advisor usa `SIM_FILTERS` salvo que su servicio de scan aporte filtros de símbolo (`api/routes/grid_advisor.py:239-246`); en esta reproducción offline quedó en `SIM_FILTERS`. No se prueba aquí la equivalencia operacional completa con el monitor en vivo.")
    lines.append("6. **Parcial**: sirve para comparar resultados históricos del mismo simulador y los escenarios medidos, pero no basta para decidir que Smart sea superior; la cadencia altera resultados, las reglas de ajuste/stop-loss generan actividad adicional y la sigma de modelo para la recomendación XRP no se verificó.")
    lines += ["", "## Limitaciones y no verificado", "", "- El XRP se obtuvo invocando la función real `recommend()` con el CSV cacheado, parámetros Moderado/90 días y el pronóstico sustituido por respuesta vacía para impedir accesos a DB; la sigma cae al fallback realizado. No representa el artefacto campeón/consenso usado por una instancia viva.", "- La reproducción ADA conserva piso/techo/n entregados por Ramón; no rederiva esos valores de una recomendación.", "- Los CSV son snapshots distintos en fecha y no se ejecutó una suite de pruebas; esta fase solo mide.", "- `M2` same-candle es un emparejamiento por nivel y timestamp de fills; porcentaje = pares same-candle / ventas emparejadas.", "- El stop-loss no posee interruptor por parámetros; resultado M5 usa un umbral centinela y está marcado como tal.", "- No se usó ni modificó la base viva, ni se consultó Binance/Testnet.", ""]
    return "\n".join(lines)


def main() -> None:
    frames = {}
    source = {}
    for symbol in ("ada", "xrp"):
        for interval in ("1h", "5m"):
            path = CACHE / f"{symbol}_{interval}.csv"
            frame = frame_for(symbol, interval)
            source[f"{symbol}_{interval}"] = {
                "path": str(path.relative_to(ROOT)),
                "rows_total": frame.attrs["rows_total"],
                "first_total": frame.attrs["first_total"],
                "last_total": frame.attrs["last_total"],
                "rows_90d": int(len(frame)),
                "first_90d": frame.timestamp.iloc[0].isoformat(),
                "last_90d": frame.timestamp.iloc[-1].isoformat(),
            }
            frames[(symbol, interval)] = frame

    scenarios = {
        "ada": {"low": ADA_RANGE[0], "high": ADA_RANGE[1], "n": ADA_RANGE[2],
                "range_source": "Advisor screen values supplied in prompt"},
    }
    for symbol in ("ada", "xrp"):
        hourly = frames[(symbol, "1h")]
        if symbol == "xrp":
            recommendation = advisor_xrp(hourly)
            scenarios["xrp"] = {
                "low": float(recommendation["recommended_floor"]),
                "high": float(recommendation["recommended_ceiling"]),
                "n": int(recommendation["suggested_grids"]),
                "current_price": float(recommendation["current_price"]),
                "range_mode": recommendation["range_mode"],
                "vol_source_effective": recommendation["vol_source_effective"],
                "range_source": "recommend() on cached CSV; forecast deliberately unavailable offline",
                "recommend_sim_start": recommendation["simulations"]["sim_start"],
                "recommend_sim_days": recommendation["simulations"]["sim_days"],
            }
        sc = scenarios[symbol]
        start_frame, meta = _simulation_window(hourly, sc["low"], sc["high"], DAYS)
        if start_frame is None:
            raise RuntimeError(f"No in-range hourly window for {symbol}: {meta}")
        start_ts = pd.to_datetime(start_frame.timestamp.iloc[0], utc=True)
        end_exclusive = pd.to_datetime(start_frame.timestamp.iloc[-1], utc=True) + pd.Timedelta(hours=1)
        start_frame = start_frame.copy().reset_index(drop=True)
        start_frame.attrs["gaps"] = 0
        sc.update({"window_start": start_frame.timestamp.iloc[0].isoformat(),
                   "window_end": start_frame.timestamp.iloc[-1].isoformat(),
                   "window_days": meta["sim_days"],
                   "window_warning": meta["window_warning"],
                   "hourly_candles": int(len(start_frame))})
        sc["window_1h_stats"] = window_stats(start_frame, sc["low"], sc["high"])
        candles_1h = to_candles(start_frame, "1h")
        hourly_results = {}
        for strategy in ("simple", "smart"):
            result = run(candles_1h, strategy=strategy, n=sc["n"], low=sc["low"],
                         high=sc["high"], resync=3, details=True)
            hourly_results[strategy] = result
        sc["M0"] = {name: compact_metrics(result) for name, result in hourly_results.items()}
        sc["M1_M2_events"] = {name: event_analysis(result) for name, result in hourly_results.items()}
        sc["M1_event_types"] = {name: event_counts_by_type(result["events"])
                                for name, result in hourly_results.items()}

        cadence = {}
        for resync in (1, 3, 6, 12):
            cadence[str(resync)] = {}
            for strategy in ("simple", "smart"):
                res = run(candles_1h, strategy=strategy, n=sc["n"], low=sc["low"],
                          high=sc["high"], resync=resync)
                cadence[str(resync)][strategy] = compact_metrics(res)
        sc["M3"] = cadence

        five = frames[(symbol, "5m")]
        five_window = five.loc[(five.timestamp >= start_ts) & (five.timestamp < end_exclusive)].copy().reset_index(drop=True)
        first_inside = np.flatnonzero((five_window.close.to_numpy(dtype=float) > sc["low"])
                                      & (five_window.close.to_numpy(dtype=float) < sc["high"]))
        if not len(first_inside):
            raise RuntimeError(f"No in-range 5m close in the 1h simulation window for {symbol}")
        five_window = five_window.iloc[int(first_inside[0]):].reset_index(drop=True)
        five_window.attrs["gaps"] = 0
        if len(five_window) < 2:
            raise RuntimeError(f"Fewer than two 5m candles in the 1h simulation window for {symbol}")
        sc["window_5m_stats"] = window_stats(five_window, sc["low"], sc["high"])
        candles_5m = to_candles(five_window, "5m")
        five_results = {}
        for resync in (3, 36):
            five_results[str(resync)] = {}
            for strategy in ("simple", "smart"):
                res = run(candles_5m, strategy=strategy, n=sc["n"], low=sc["low"],
                          high=sc["high"], resync=resync)
                five_results[str(resync)][strategy] = compact_metrics(res)
        sc["M4"] = five_results

        base_smart = hourly_results["smart"]["metrics"]
        ablations = {
            "adjust_off": {"adjust_enabled": False},
            "stop_loss_1000000pct_sentinel": {"stop_loss_pct": 1000000.0},
            "pause_off": {"pause_enter_prob": None, "pause_exit_prob": None},
        }
        ablation_results = {}
        for name, params in ablations.items():
            res = run(candles_1h, strategy="smart", n=sc["n"], low=sc["low"],
                      high=sc["high"], resync=3, params=params, details=True)
            ablation_results[name] = {
                "params_changed_only": params,
                "metrics": compact_metrics(res),
                "event_types": event_counts_by_type(res["events"]),
                "delta_cycles_vs_default": int(res["metrics"]["cycles_completed"] - base_smart["cycles_completed"]),
                "delta_pnl_vs_default": float(res["metrics"]["pnl_total_net_usdt"] - base_smart["pnl_total_net_usdt"]),
            }
        sc["M5"] = {
            "default": {"metrics": compact_metrics(hourly_results["smart"]),
                        "event_types": event_counts_by_type(hourly_results["smart"]["events"])},
            "ablations": ablation_results,
            "stop_loss_note": "validate_params requires stop_loss_pct > 0; 1000000% sentinel is not a semantic disable flag.",
        }

        closes = candles_1h.close
        sigmas = ewma_sigma_24h(closes, halflife_h=72.0)
        sc["M7"] = {
            "sigma_mean": float(np.mean(sigmas)),
            "sigma_min": float(np.min(sigmas)),
            "sigma_max": float(np.max(sigmas)),
            "sigma_initial": [float(x) for x in sigmas[:6]],
            "initial_nan_count": int(np.isnan(sigmas).sum()),
            "initial_zero_count": int(np.sum(sigmas == 0)),
            "champion_consensus_comparison": "NO VERIFICADO: no se consulto forecast/API ni DB; run_simulation recibe sigma_values=None.",
            "halflife_h": 72.0,
            "runner_default_sigma_scale_argument": 1.0,
            "smart_policy_sigma_scale": 1.15,
        }
        sc["M6"] = sc["window_1h_stats"]
        sc["filter_source"] = "api/routes/grid_advisor.py uses SIM_FILTERS unless grid_scan_service supplies symbol filters; direct recommendation has no grid_scan_service."
        sc["sim_csv_note"] = "run_simulation receives no csv_hash; 1h window from _simulation_window; 5m timestamps aligned to the same UTC start/end interval."

    payload = {
        "source_csvs": source,
        "advisor_reference": {
            "advisor_sim_call": "api/routes/grid_advisor.py:295-296",
            "advisor_window": "api/routes/grid_advisor.py:50-69",
            "policy_cadence": "grid/sim/runner.py:153",
            "internal_sigma": "grid/sim/runner.py:126-131 and grid/sim/data.py:77-86",
            "cycles_definition": "grid/sim/metrics.py:20",
            "smart_defaults": "grid/policy.py:12-44",
        },
        "scenarios": scenarios,
    }
    report = render_report(payload)
    report_path = ROOT / "REPORTE_FASE20E1e.md"
    report_path.write_text(report, encoding="utf-8", newline="\n")
    print(json.dumps({"report": str(report_path.relative_to(ROOT)),
                      "scenarios": {key: {"range": [value["low"], value["high"], value["n"]],
                          "M0": value["M0"], "M1_M2_events": value["M1_M2_events"],
                          "M3": value["M3"], "M4": value["M4"], "M5": value["M5"],
                          "M6": value["M6"], "M7": value["M7"]}
                          for key, value in scenarios.items()}}, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
