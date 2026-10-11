"""Pure message-formatting helpers for alerts. No I/O, no DB, no network.

Decimals are rendered with format(d, "f") rather than str(d): a Decimal
computed by division (e.g. a tick size like 1/1e8) prints in scientific
notation with str() but format(d, "f") always prints fixed-point, which is
what Ramon asked for ("sin notacion cientifica").
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from grid.status_view import EVENT_LABELS

MAX_FILL_GROUP_LINES = 10


def grid_label(symbol: str, grid_id: int | None) -> str:
    return f"{symbol} #{int(grid_id)}" if grid_id is not None else str(symbol)


def fmt(value: Any) -> str:
    """Render a stored price/qty/pnl string without scientific notation,
    preserving exactly the precision already stored by the engine."""
    if value is None:
        return "no disponible"
    try:
        return format(Decimal(str(value)), "f")
    except (InvalidOperation, TypeError, ValueError):
        return str(value)


def fmt_price(value: Any) -> str:
    """Like fmt(), but a missing price reads as 'precio no disponible'
    rather than the generic 'no disponible' -- this is what actually shows
    up in the 'a <price>' part of a fill message."""
    return "precio no disponible" if value is None else fmt(value)


def _qty_fragment(qty: Any) -> str:
    """'' if qty is missing (so the surrounding sentence reads naturally
    without it), else '<qty> ' with a trailing space to splice into a
    sentence like 'Compró {frag}a {price}'."""
    return "" if qty is None else f"{fmt(qty)} "


def _is_nonnegative(value: Any) -> bool:
    try:
        return Decimal(str(value)) >= 0
    except (InvalidOperation, TypeError, ValueError):
        return True


# -- D2: fills ---------------------------------------------------------------

def format_buy_fill_line(qty: Any, buy_price: Any, sell_price: Any | None) -> str:
    if sell_price is None:
        return f"\U0001F7E2 Compró {_qty_fragment(qty)}a {fmt_price(buy_price)} (sin venta colocada)"
    return f"\U0001F7E2 Compró {_qty_fragment(qty)}a {fmt_price(buy_price)}, vende a {fmt_price(sell_price)}"


def format_sell_fill_line(
    qty: Any, sell_price: Any, net_pnl: Any, commission: Any | None,
    cycles: int | None, *, is_close: bool = False,
) -> str:
    sign = "+" if _is_nonnegative(net_pnl) else ""
    tag = " (cierre)" if is_close else ""
    line = (f"\U0001F4B0 Vendió{tag} {_qty_fragment(qty)}a {fmt_price(sell_price)}: "
            f"{sign}{fmt(net_pnl)} USDT neto")
    if commission is not None:
        line += f" (com. {fmt(commission)})"
    if cycles is not None:
        line += f", ciclo {int(cycles)}"
    return line


def format_buy_fill(symbol: str, grid_id: int, qty: Any, buy_price: Any, sell_price: Any | None) -> str:
    """Full two-line message, used when this is the only fill for the grid
    in this poll pass -- matches the format Ramon specified exactly."""
    label = grid_label(symbol, grid_id)
    if sell_price is None:
        body = f"Compró {_qty_fragment(qty)}a {fmt_price(buy_price)}. No se pudo colocar la venta (revisar)."
    else:
        body = (f"Compró {_qty_fragment(qty)}a {fmt_price(buy_price)}. "
                f"El bot espera vender a {fmt_price(sell_price)}.")
    return f"\U0001F7E2 Compra ejecutada — {label}\n{body}"


def format_sell_fill(
    symbol: str, grid_id: int, qty: Any, sell_price: Any, net_pnl: Any,
    commission: Any | None, cycles: int | None, *, is_close: bool = False,
) -> str:
    """Full two-line message, used when this is the only fill for the grid
    in this poll pass."""
    label = grid_label(symbol, grid_id)
    title = (f"\U0001F4B0 Venta (cierre) — {label}" if is_close
              else f"\U0001F4B0 Venta ejecutada — {label}")
    sign = "+" if _is_nonnegative(net_pnl) else ""
    body = f"Vendió {_qty_fragment(qty)}a {fmt_price(sell_price)}. Ganancia neta: {sign}{fmt(net_pnl)} USDT"
    if commission is not None:
        body += f" (comisión {fmt(commission)})"
    body += "."
    if cycles is not None:
        body += f" Ciclos: {int(cycles)}."
    return f"{title}\n{body}"


def format_fills_group(symbol: str, grid_id: int, lines: list[str]) -> str:
    """Several fills for the same grid detected in the same monitor pass."""
    label = grid_label(symbol, grid_id)
    shown = lines[:MAX_FILL_GROUP_LINES]
    text = "\n".join(shown)
    if len(lines) > MAX_FILL_GROUP_LINES:
        text += f"\ny {len(lines) - MAX_FILL_GROUP_LINES} más."
    return f"\U0001F501 Movimientos — {label}\n{text}"


def format_fills_cap_reached() -> str:
    return "⚠️ Tope de avisos de compras/ventas alcanzado por hoy; silencio hasta mañana (UTC)."


# -- D1: operational events ---------------------------------------------------

def format_operational(event: dict, grid_label_text: str | None) -> str:
    event_type = str(event.get("event_type") or "")
    label = EVENT_LABELS.get(event_type, event_type)
    details = event.get("details") or {}
    symbol = grid_label_text or details.get("symbol")
    header = f"⚠️ {label}"
    if symbol:
        header += f" — {symbol}"
    lines = [header]
    reason = event.get("reason")
    if reason:
        lines.append(f"Motivo: {reason}")
    return "\n".join(lines)


def format_daily_cap_reached() -> str:
    return "⚠️ Tope diario de avisos operativos alcanzado; silencio hasta mañana (UTC)."


# -- D3: health ---------------------------------------------------------------

def format_monitor_stale(minutes: float) -> str:
    return f"⚠️ El monitor de grids no corre desde hace {int(minutes)} min."


def format_binance_unreachable() -> str:
    return "⚠️ Binance Testnet inalcanzable (¿túnel caído?)."


def format_binance_recovered() -> str:
    return "✅ Binance Testnet volvió a responder."


def format_loop_stale(name: str, minutes: float) -> str:
    return f"⚠️ {name} no corre desde hace {int(minutes)} min."


def format_startup() -> str:
    return "⚠️ ASPLE iniciado."


# -- D4: daily digest ----------------------------------------------------------

def format_digest(rows: list[dict], *, monitor_last_run: str | None, last_prediction: str | None,
                   last_vol: str | None, probe_ok: bool | None) -> str:
    lines = ["\U0001F4CA Resumen diario ASPLE"]
    for row in rows:
        lines.append(
            f"- {row['label']} ({row['status']}): {row.get('cycles', 0)} ciclos, "
            f"{fmt(row.get('pnl_today', 0))} USDT hoy / {fmt(row.get('pnl_total', 0))} acumulado, "
            f"{row.get('open_orders', 0)} órdenes abiertas"
        )
    if not rows:
        lines.append("- sin grids activos")
    lines.append(f"Última pasada del monitor: {monitor_last_run or 'sin datos'}")
    lines.append(f"Última predicción XRP: {last_prediction or 'sin datos'}")
    lines.append(f"Último ciclo de volatilidad: {last_vol or 'sin datos'}")
    probe_text = "desconocido" if probe_ok is None else ("OK" if probe_ok else "inalcanzable")
    lines.append(f"Sondeo Binance: {probe_text}")
    return "\n".join(lines)
