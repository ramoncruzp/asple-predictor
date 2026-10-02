"""Pure, read-only view functions for the Fase 17A grid status screen.

No I/O here: every function takes rows already read from the database (or an
already-fetched market price) and returns plain dicts/lists ready to serialize.
Nothing here writes to the database, calls an exchange, or predicts anything.

Money travels through this module as ``Decimal`` and is only converted to
``float`` at the very end, when building the JSON-ready output, so precision
is never lost for low-priced assets like PEPE.

Honesty rules (inherited from Fase 15A / this phase's rule 4): realized profit
is always shown next to unrealized and "total with inventory"; nothing is
invented when the underlying tables do not carry a field -- those come back as
``None`` with an ``unavailable_reason`` string instead.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from grid.policy import evaluate_max_days

LEVEL_STATE_LABELS = {
    "IDLE": "esperando compra",
    "BUY_OPEN": "esperando compra",
    "SELL_OPEN": "esperando venta",
    "DONE": "detenido",
    "ERROR": "error",
}

# Real event types emitted by grid/engine.py, grid/monitor.py, grid/auto_open.py
# and api/routes/grids.py, plus the literal names the Fase 17A brief used that
# do not exist verbatim in this codebase (kept here so they display sensibly if
# they are ever introduced; see DISCREPANCIAS_FASE17A.md for the exact mapping).
EVENT_LABELS: dict[str, str] = {
    "BUY_FILLED": "Compra ejecutada",
    "SELL_FILLED": "Venta ejecutada",
    "BUY_PLACED": "Orden de compra colocada",
    "BUY_REJECTED": "Orden de compra rechazada",
    "BUY_DEFERRED_FUNDS": "Compra diferida por fondos insuficientes",
    "LOAN_DEFERRED_FUNDS": "Préstamo interno diferido por fondos insuficientes",
    "LOAN_CREATED": "Préstamo interno creado",
    "LOAN_CANCELLED": "Préstamo interno cancelado",
    "COMPOUND_APPLIED": "Interés compuesto aplicado",
    "COMPOUND_SKIPPED": "Interés compuesto omitido",
    "GRID_PAUSED": "Grid pausado",
    "GRID_RESUMED": "Grid reanudado",
    "GRID_ADJUSTED": "Rango del grid ajustado",
    "ADJUST_SKIPPED_CELL": "Celda omitida al ajustar",
    "ADJUST_REJECTED": "Ajuste de rango rechazado",
    "CELL_STOPLOSS": "Stop-loss de celda ejecutado",
    "GRID_AUTO_CLOSE": "Cierre automático a repositorio iniciado",
    "GRID_CLOSE_STARTED": "Cierre del grid iniciado",
    "GRID_CLOSED": "Grid cerrado",
    "REPOSITORY_CREATED": "Repositorio creado",
    "REPOSITORY_EMPTY_CLOSED": "Repositorio vacío cerrado",
    "CELL_MOVED_TO_REPOSITORY": "Celda movida al repositorio",
    "CELL_DUST": "Celda marcada como polvo",
    "DUST_SWEPT": "Polvo barrido",
    "DUST_SWEEP_DEFERRED": "Barrido de polvo diferido",
    "TARGET_REACHED": "Meta de ganancia alcanzada",
    "TARGET_MARKET_SELL_FAILED": "Venta a mercado de la meta falló",
    "MAX_DAYS_REACHED": "Plazo máximo alcanzado",
    "RUN_GAP": "Hueco entre pasadas del monitor",
    "SNAPSHOT_FAILED": "Snapshot del monitor falló",
    "VOL_UNAVAILABLE": "Volatilidad no disponible para la política",
    "POLICY_ACTION_FAILED": "Acción de la política falló",
    "GRID_OPEN_API": "Grid abierto desde la API",
    "GRID_OPEN_REJECTED": "Apertura de grid rechazada",
    "AUTO_OPEN": "Auto-open abrió un grid",
    "AUTO_OPEN_STARTED": "Pasada de auto-open iniciada",
    # Literal names from the Fase 17A brief that this codebase does not emit
    # verbatim today -- kept so the UI still shows something sensible if they
    # ever appear, see DISCREPANCIAS_FASE17A.md.
    "PAUSE": "Grid pausado",
    "RESUME": "Grid reanudado",
    "ADJUST": "Rango del grid ajustado",
    "CLOSE_REPOSITORY": "Cierre a repositorio",
    "invariant_violation": "Violación de invariante detectada",
    "TESTNET_RESET_DETECTED": "Reinicio de Testnet detectado",
}

SEVERITY_BY_EVENT = {
    "POLICY_ACTION_FAILED": "error", "SNAPSHOT_FAILED": "error",
    "invariant_violation": "error", "ERROR": "error",
    "CELL_STOPLOSS": "warning", "GRID_AUTO_CLOSE": "warning",
    "MAX_DAYS_REACHED": "warning", "RUN_GAP": "warning",
    "VOL_UNAVAILABLE": "warning", "ADJUST_REJECTED": "warning",
    "GRID_OPEN_REJECTED": "warning", "TESTNET_RESET_DETECTED": "warning",
    "BUY_FILLED": "info", "SELL_FILLED": "success", "TARGET_REACHED": "success",
    "GRID_PAUSED": "info", "GRID_RESUMED": "info", "GRID_ADJUSTED": "info",
}

OPEN_STATUSES = {"OPENING", "ACTIVE", "PAUSED", "CLOSING"}


def _d(value: Any) -> Decimal:
    if value is None:
        return Decimal(0)
    try:
        return value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(0)


def _f(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _utc(value: Any) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def is_recovery_mode(levels: list[dict], price: float | None) -> bool | None:
    """True if every held position's sell price is above the current price.

    ``None`` (not False) when there is no price to compare against -- do not
    silently claim "not in recovery" when we simply cannot tell.
    """
    if price is None:
        return None
    held = [row for row in levels if _d(row.get("held_qty")) > 0 and row.get("sell_price") is not None]
    if not held:
        return False
    return all(Decimal(str(price)) < _d(row["sell_price"]) for row in held)


def inventory_view(levels: list[dict], price: float | None, fee_pct: float) -> dict:
    """Open inventory: cost, mark-to-market (net of an estimated exit fee), unrealized."""
    held = [row for row in levels if _d(row.get("held_qty")) > 0]
    qty_total = sum((_d(row.get("held_qty")) for row in held), Decimal(0))
    cost_total = sum(
        (_d(row.get("held_qty")) * _d(row.get("entry_price")) for row in held), Decimal(0)
    )
    if price is None:
        return {
            "qty": _f(qty_total), "cost_usdt": _f(cost_total), "market_value_usdt": None,
            "unrealized_pnl_usdt": None, "cells_with_position": len(held),
            "unavailable_reason": "precio actual no disponible" if held else None,
        }
    fee_rate = _d(fee_pct) / Decimal(100)
    market_value = qty_total * Decimal(str(price)) * (Decimal(1) - fee_rate)
    return {
        "qty": _f(qty_total), "cost_usdt": _f(cost_total), "market_value_usdt": _f(market_value),
        "unrealized_pnl_usdt": _f(market_value - cost_total), "cells_with_position": len(held),
        "unavailable_reason": None,
    }


def fees_view(levels: list[dict], fee_pct: float) -> dict:
    """Real (exchange-reported) fees vs. an estimated round-trip fee.

    Testnet charges 0 fees, so ``fee_paid`` alone understates the real cost of
    running the grid live. ``fees_estimated_usdt`` approximates what a live
    exchange would have charged: ``fee_pct`` applied to both legs of every
    completed cycle, using each cell's allocated capital as the per-cycle
    notional (an approximation -- cells resized by ADJUST keep their latest
    capital, not the historical one; see DISCREPANCIAS_FASE17A.md).
    """
    fee_real_total = sum((_d(row.get("fee_paid")) for row in levels), Decimal(0))
    net_realized_total = sum((_d(row.get("pnl")) for row in levels), Decimal(0))
    gross_realized_total = net_realized_total + fee_real_total
    fee_rate = _d(fee_pct) / Decimal(100)
    fees_estimated = sum(
        (_d(row.get("capital")) * Decimal(int(row.get("cycles_completed") or 0)) * fee_rate * Decimal(2)
         for row in levels), Decimal(0)
    )
    return {
        "fee_real_usdt": _f(fee_real_total),
        "fee_real_label": "comisión real del exchange",
        "fees_estimated_usdt": _f(fees_estimated),
        "fees_estimated_label": f"comisión estimada {float(fee_pct):.2f}% (Testnet cobra 0)",
        "gross_realized_usdt": _f(gross_realized_total),
        "net_realized_usdt": _f(net_realized_total),
        "net_after_estimated_fees_usdt": _f(gross_realized_total - fees_estimated),
    }


def cash_now_view(grid: dict, levels: list[dict]) -> Decimal:
    """Estimate grid cash using the monitor's held-basis convention."""
    capital = _d(grid.get("capital_total"))
    realized = sum((_d(row.get("pnl")) for row in levels), Decimal(0))
    held_basis = sum((
        _d(row.get("entry_price")) * _d(row.get("held_qty")) * Decimal("1.001")
        for row in levels if _d(row.get("held_qty")) > 0
    ), Decimal(0))
    dust_proceeds = _d((grid.get("params") or {}).get("dust_cash_proceeds"))
    return capital + realized - held_basis + dust_proceeds


def progress_view(grid: dict, levels: list[dict], now: datetime, *, price: float | None = None) -> dict:
    """Estimated grid cash progress toward target_pct/target_usdt and max_days."""
    params = grid.get("params") or {}
    capital_total = _d(grid.get("capital_total"))
    cash_now = cash_now_view(grid, levels)
    cash_profit = cash_now - capital_total
    held_qty = sum((_d(row.get("held_qty")) for row in levels), Decimal(0))
    target_pct, target_usdt = params.get("target_pct"), params.get("target_usdt")
    target_info = None
    if target_pct is not None or target_usdt is not None:
        goals = []
        if target_pct is not None:
            goals.append(capital_total * _d(target_pct) / Decimal(100))
        if target_usdt is not None:
            goals.append(_d(target_usdt))
        target_profit = min(goals)
        progress_pct = (float(cash_profit / target_profit) * 100.0) if target_profit > 0 else None
        target_basis = params.get("target_basis", "cash")
        equity_now = None
        if target_basis == "equity" and price is not None:
            equity_now = cash_now + held_qty * _d(price) * Decimal("0.999")
        target_info = {
            "target_profit_usdt": _f(target_profit), "basis": target_basis,
            "cash_now_usdt": _f(cash_now), "cash_profit_usdt": _f(cash_profit),
            "equity_now_usdt": _f(equity_now),
            "progress_pct": progress_pct,
            "note": ("caja estimada con la misma fórmula que el monitor (capital + realizado - "
                     "costo de posiciones retenidas + polvo vendido); al evaluar la meta el monitor "
                     "además cuenta lo que se obtendría vendiendo a mercado las celdas con ganancia"),
        }
    max_days_eval = evaluate_max_days(params, grid.get("created_at"), now)
    days_remaining = None
    if max_days_eval.get("max_days") is not None and max_days_eval.get("age_days") is not None:
        days_remaining = max(0.0, float(max_days_eval["max_days"]) - float(max_days_eval["age_days"]))
    return {
        "target": target_info,
        "max_days": {
            "max_days": max_days_eval.get("max_days"), "age_days": max_days_eval.get("age_days"),
            "days_remaining": days_remaining, "expired": max_days_eval.get("expired", False),
        } if max_days_eval.get("max_days") is not None else None,
    }


def grid_summary(
    grid: dict, levels: list[dict], price: float | None, *, now: datetime | None = None,
    fee_pct: float = 0.1, price_as_of: datetime | None = None,
) -> dict:
    """One grid's full summary card: identity, PnL, inventory, progress, flags."""
    now = now or datetime.now(timezone.utc)
    created_at = _utc(grid["created_at"])
    age_seconds = max(0.0, (now - created_at).total_seconds())
    capital_total = _d(grid.get("capital_total"))
    capital_deployed = sum(
        (_d(row.get("capital")) for row in levels
         if row.get("state") in {"BUY_OPEN", "SELL_OPEN"} or _d(row.get("held_qty")) > 0),
        Decimal(0),
    )
    cycles_total = sum(int(row.get("cycles_completed") or 0) for row in levels)
    fees = fees_view(levels, fee_pct)
    inventory = inventory_view(levels, price, fee_pct)
    held_qty_total = sum((_d(row.get("held_qty")) for row in levels), Decimal(0))
    dust_qty = _d(grid.get("dust_qty"))
    net_realized = fees["net_realized_usdt"]
    total_with_inventory = (
        None if inventory["unrealized_pnl_usdt"] is None
        else (net_realized or 0.0) + inventory["unrealized_pnl_usdt"]
    )
    profit_per_cycle_pct = None
    if cycles_total > 0 and capital_total > 0 and net_realized is not None:
        profit_per_cycle_pct = (net_realized / cycles_total) / float(capital_total) * 100.0
    params = grid.get("params") or {}
    return {
        "id": int(grid["id"]), "symbol": grid["symbol"], "strategy": grid.get("strategy", "simple"),
        "status": grid["status"], "created_at": created_at.isoformat(),
        "age_days": age_seconds / 86400.0, "compound_enabled": bool(params.get("compound_enabled", False)),
        "price": price, "price_as_of": price_as_of.isoformat() if price_as_of else None,
        "capital_total_usdt": _f(capital_total), "capital_deployed_usdt": _f(capital_deployed),
        "capital_deployed_pct": (float(capital_deployed / capital_total) * 100.0) if capital_total > 0 else None,
        "cycles_completed": cycles_total,
        "gross_realized_usdt": fees["gross_realized_usdt"],
        "fee_real_usdt": fees["fee_real_usdt"], "fee_real_label": fees["fee_real_label"],
        "fees_estimated_usdt": fees["fees_estimated_usdt"], "fees_estimated_label": fees["fees_estimated_label"],
        "net_realized_usdt": net_realized, "net_realized_label": "realizada",
        "net_after_estimated_fees_usdt": fees["net_after_estimated_fees_usdt"],
        "profit_per_cycle_pct": profit_per_cycle_pct,
        "profit_per_cycle_label": "promedio sobre ciclos completados; el neto incluye ventas a mercado",
        "inventory": inventory, "total_with_inventory_usdt": total_with_inventory,
        "held_qty": _f(held_qty_total), "dust_qty": _f(dust_qty),
        "dust_value_usdt": _f(dust_qty * Decimal(str(price))) if price is not None else None,
        "progress": progress_view(grid, levels, now, price=price),
        "recovery_mode": is_recovery_mode(levels, price),
        "paused": grid["status"] == "PAUSED", "closing": grid["status"] in {"CLOSING", "HOLDING"},
    }


def _distance_pct(price: float | None, target: float | None) -> float | None:
    if price is None or target is None or price == 0:
        return None
    return (target - price) / price * 100.0


def cells_view(levels: list[dict], price: float | None, fee_pct: float = 0.1, *, grid_paused: bool = False) -> dict:
    """Ladder of cells, highest price first, with a 'current price' marker row inserted."""
    ordered = sorted(levels, key=lambda row: float(row.get("price") or 0), reverse=True)
    fee_rate = _d(fee_pct) / Decimal(100)
    rows = []
    expected_per_cycle_total = Decimal(0)
    live_pnl_total = Decimal(0)
    for row in ordered:
        held_qty = _d(row.get("held_qty"))
        state = str(row.get("state") or "")
        if state == "ERROR":
            label = "error"
        elif held_qty > 0:
            label = "pausado con posición" if grid_paused else LEVEL_STATE_LABELS.get(state, state)
        else:
            label = "pausado" if grid_paused else LEVEL_STATE_LABELS.get(state, state)
        waiting_buy = label == "esperando compra"
        waiting_sell = label == "esperando venta"
        distance_pct = (
            _distance_pct(price, float(row["price"])) if waiting_buy else
            _distance_pct(price, float(row["sell_price"])) if waiting_sell and row.get("sell_price") is not None
            else None
        )
        live_pnl = None
        if held_qty > 0 and price is not None and row.get("entry_price") is not None:
            live_pnl = _f(held_qty * (Decimal(str(price)) * (Decimal(1) - fee_rate) - _d(row["entry_price"])))
            live_pnl_total += Decimal(str(live_pnl))
        buy_price, sell_price, capital = row.get("price"), row.get("sell_price"), _d(row.get("capital"))
        if buy_price is not None and sell_price is not None and float(buy_price) > 0:
            planned_qty = capital / _d(buy_price)
            expected_per_cycle_total += planned_qty * (_d(sell_price) - _d(buy_price)) * (Decimal(1) - fee_rate * Decimal(2))
        rows.append({
            "level_idx": int(row["level_idx"]), "buy_price": _f(_d(buy_price)) if buy_price is not None else None,
            "sell_price": _f(_d(sell_price)) if sell_price is not None else None,
            "capital_usdt": _f(capital), "state": state, "state_label": label,
            "held_qty": _f(held_qty), "cycles_completed": int(row.get("cycles_completed") or 0),
            "live_pnl_usdt": live_pnl, "distance_to_fill_pct": distance_pct,
        })
    marker_index = len(rows)
    if price is not None:
        marker_index = next(
            (i for i, row in enumerate(rows) if row["buy_price"] is not None and price >= row["buy_price"]),
            len(rows),
        )
    result_rows = [*rows[:marker_index], {"marker": "precio_actual", "price": price}, *rows[marker_index:]] \
        if price is not None else rows
    return {
        "rows": result_rows, "expected_profit_per_cycle_usdt": _f(expected_per_cycle_total),
        "live_pnl_total_usdt": _f(live_pnl_total),
    }


def operations_view(events: list[dict], limit: int = 50, *, levels: list[dict] | None = None) -> list[dict]:
    """Show cycle sells and market exits as separate operation kinds.

    Stable cell limit prices are used when no ADJUST mapping moved that cell;
    market exits use their recorded execution prices. Otherwise event mid is
    shown with ``prices_approx`` set. Per-operation gross and sell fee remain
    unavailable because the event stores net realized PnL only.
    """
    by_ts = sorted(events, key=lambda event: (event["ts"], event.get("id", 0)))
    level_by_idx = {int(row["level_idx"]): row for row in (levels or [])}
    adjusted_levels: set[int] = set()
    for event in by_ts:
        if event.get("event_type") != "GRID_ADJUSTED":
            continue
        details = event.get("details") or {}
        fixed = {int(idx) for idx in details.get("fixed_cells", [])}
        for mapping in details.get("mapping", []):
            idx = mapping.get("level_idx")
            if idx is not None and int(idx) not in fixed:
                adjusted_levels.add(int(idx))
    last_buy_by_level: dict[int, dict] = {}
    operations = []
    for event in by_ts:
        event_type = event.get("event_type")
        level_idx = event.get("level_idx")
        if event_type == "BUY_FILLED" and level_idx is not None:
            last_buy_by_level[int(level_idx)] = event
        elif event_type in {"SELL_FILLED", "CELL_STOPLOSS", "CELL_LIQUIDATED"}:
            details = event.get("details") or {}
            is_cycle = event_type == "SELL_FILLED"
            buy_event = last_buy_by_level.pop(int(level_idx), None) if is_cycle and level_idx is not None else None
            duration_hours = None
            if buy_event is not None:
                duration_hours = (_utc(event["ts"]) - _utc(buy_event["ts"])).total_seconds() / 3600.0
            level = level_by_idx.get(int(level_idx)) if level_idx is not None else None
            exact_prices = level is not None and int(level_idx) not in adjusted_levels
            if event_type == "CELL_STOPLOSS":
                buy_price = details.get("entry_price") or (level.get("price") if exact_prices else None)
                sell_price = details.get("execution_price")
                net_pnl = details.get("realized_pnl")
            elif event_type == "CELL_LIQUIDATED":
                buy_price = level.get("price") if exact_prices else None
                sell_price = details.get("price")
                net_pnl = details.get("cycle_pnl", details.get("realized_pnl"))
            else:
                buy_price = level.get("price") if exact_prices else (buy_event.get("price") if buy_event else None)
                sell_price = level.get("sell_price") if exact_prices else event.get("price")
                net_pnl = details.get("cycle_pnl")
            operations.append({
                "ts": _utc(event["ts"]).isoformat(), "level_idx": level_idx,
                "kind": {"SELL_FILLED": "ciclo", "CELL_STOPLOSS": "stop_loss",
                         "CELL_LIQUIDATED": "liquidacion"}[event_type],
                "buy_qty": (buy_event or {}).get("details", {}).get("executed_qty"),
                "sell_qty": details.get("executed_qty", details.get("qty", details.get("held_qty"))),
                "net_pnl_usdt": net_pnl,
                "buy_price_approx": buy_price,
                "sell_price_approx": sell_price,
                "prices_approx": not exact_prices,
                "buy_fee_usdt": (buy_event or {}).get("details", {}).get("fee_usdt"),
                "sell_fee_usdt": None,
                "gross_pnl_usdt": None,
                "duration_hours": duration_hours,
                "unavailable_reason": (
                    None if not is_cycle or buy_event is not None
                    else "no se encontró el BUY_FILLED correspondiente en la ventana de eventos consultada"
                ),
            })
    operations.sort(key=lambda row: row["ts"], reverse=True)
    for row in operations:
        if row["gross_pnl_usdt"] is None:
            row["gross_fee_unavailable_reason"] = (
                "SELL_FILLED no registra el bruto ni la comisión de venta por separado, solo "
                "cycle_pnl neto (grid/engine.py:1154-1159)"
            )
    return operations[:limit]


def daily_profit_view(events: list[dict], *, tz_name: str = "America/Santo_Domingo",
                      net_realized_usdt: Any = None, truncated: bool = False,
                      reconcile_available: bool = True) -> dict:
    """Net realized PnL per day, including cycle sells and market liquidations."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc
    by_day: dict[str, dict[str, Decimal]] = {}
    pnl_keys = {"SELL_FILLED": ("cycle_pnl", "cycles"),
                "CELL_STOPLOSS": ("realized_pnl", "stoploss"),
                "CELL_LIQUIDATED": ("cycle_pnl", "liquidations")}
    for event in events:
        event_type = event.get("event_type")
        if event_type not in pnl_keys:
            continue
        pnl_key, source = pnl_keys[event_type]
        details = event.get("details") or {}
        pnl = details.get(pnl_key)
        if pnl is None and event_type in {"CELL_STOPLOSS", "CELL_LIQUIDATED"}:
            pnl = details.get("realized_pnl", details.get("pnl_realized"))
        if pnl is None:
            continue
        local_day = _utc(event["ts"]).astimezone(tz).date().isoformat()
        bucket = by_day.setdefault(local_day, {"cycles": Decimal(0), "stoploss": Decimal(0),
                                                "liquidations": Decimal(0)})
        bucket[source] += _d(pnl)
    series = []
    for day, parts in sorted(by_day.items()):
        series.append({"date": day, "cycles": _f(parts["cycles"]),
            "stoploss": _f(parts["stoploss"]), "liquidations": _f(parts["liquidations"]),
            "net_pnl_usdt": _f(sum(parts.values(), Decimal(0)))})
    daily_sum = sum((_d(row["net_pnl_usdt"]) for row in series), Decimal(0))
    reconcile_available = reconcile_available and not truncated
    unattributed = (None if net_realized_usdt is None or not reconcile_available
                    else _d(net_realized_usdt) - daily_sum)
    if not reconcile_available:
        unattributed_note = "conciliación solo disponible cuando el rango cubre toda la vida del grid"
    elif unattributed is not None and unattributed != 0:
        unattributed_note = "polvo barrido, ajustes o eventos fuera de la ventana consultada"
    else:
        unattributed_note = None
    return {"timezone": tz_name, "daily": series, "net_realized_usdt": _f(_d(net_realized_usdt))
            if net_realized_usdt is not None else None,
            "net_realized_label": "total de vida del grid" if net_realized_usdt is not None else None,
            "unattributed_usdt": _f(unattributed), "unattributed_note": unattributed_note,
            "truncated": bool(truncated)}


def equity_curve_view(snapshots: list[dict], *, points: int = 200) -> dict:
    """Equity-with-inventory curve: capital + realized-to-date + mark-to-market, one point per run.

    Reduced to at most ``points`` entries (most recent kept) when there are more.
    """
    by_run: dict[Any, dict] = {}
    for row in snapshots:
        key = row.get("run_id")
        bucket = by_run.setdefault(key, {"ts": row["ts"], "cell_realized": Decimal(0),
            "summary_realized": None, "unrealized": Decimal(0)})
        if "level_idx" in row and row.get("level_idx") is None:
            bucket["summary_realized"] = _d(row.get("pnl_realized"))
        else:
            bucket["cell_realized"] += _d(row.get("pnl_realized"))
            bucket["unrealized"] += _d(row.get("unrealized_pnl"))
    for bucket in by_run.values():
        if bucket["summary_realized"] is None:
            bucket["realized"] = bucket["cell_realized"]
            bucket["source"] = "cell_rows_fallback"
        else:
            bucket["realized"] = bucket["summary_realized"]
            bucket["source"] = "summary_row"
    points_list = sorted(
        ({"ts": _utc(bucket["ts"]).isoformat(),
          "realized_usdt": _f(bucket["realized"]),
          "with_inventory_usdt": _f(bucket["realized"] + bucket["unrealized"]),
          "source": bucket["source"]}
         for bucket in by_run.values()),
        key=lambda row: row["ts"],
    )
    if len(points_list) > points:
        points_list = points_list[-points:]
    return {"points": points_list, "note": "realizado de resumen (o celdas si falta) + no realizado de celdas"}


def events_view(events: list[dict]) -> list[dict]:
    """Human-readable events in Spanish; unknown types keep their raw type, never hidden."""
    result = []
    for event in events:
        event_type = event.get("event_type") or ""
        result.append({
            "ts": _utc(event["ts"]).isoformat(), "type": event_type,
            "message": EVENT_LABELS.get(event_type, event_type),
            "severity": SEVERITY_BY_EVENT.get(event_type, "info"),
            "reason": event.get("reason"), "grid_id": event.get("grid_id"),
            "level_idx": event.get("level_idx"),
        })
    return result


def account_totals(grids_summaries: list[dict], free_usdt: float | None, unavailable_reason: str | None) -> dict:
    """Totals across all open/repository grids, plus free exchange balance if available."""
    net_total = sum((row["net_realized_usdt"] or 0.0) for row in grids_summaries)
    with_inventory_total = sum((row["total_with_inventory_usdt"] or 0.0) for row in grids_summaries)
    capital_in_grids = sum((row["capital_total_usdt"] or 0.0) for row in grids_summaries)
    return {
        "net_realized_usdt": net_total, "total_with_inventory_usdt": with_inventory_total,
        "capital_in_grids_usdt": capital_in_grids,
        "free_usdt": free_usdt, "free_usdt_unavailable_reason": unavailable_reason,
    }


def same_symbol_warning(grids_summaries: list[dict]) -> str | None:
    """Non-blocking warning when two or more open grids share a symbol (their orders can cross, STP)."""
    open_rows = [row for row in grids_summaries if row["status"] in OPEN_STATUSES]
    symbols = [row["symbol"] for row in open_rows]
    if len(symbols) != len(set(symbols)):
        return "varios grids en el mismo símbolo: sus órdenes pueden cruzarse entre sí (STP)"
    return None
