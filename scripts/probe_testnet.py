"""Sondeo de Binance Testnet para la Fase 14 (Grid Engine).

Mide, contra el exchange real de Testnet, los hechos que el diseno del grid
asume y no puede verificar con fakes:

  1. Filtros reales de XRPUSDT (tick, step, minNotional, bandas de precio, max ordenes).
  2. Precio de Testnet frente al de produccion, spread y profundidad del libro.
  3. Tasas de comision y en QUE moneda se cobran.
  4. Como reacciona el motor ante: precio con ruido de float, precio fuera de banda,
     cantidad bajo el minimo, y ordenes TAKE_PROFIT_LIMIT con trailingDelta.
  5. (solo con --execute) Un ciclo real minimo: compra a mercado -> comision real ->
     venta limit con cantidad BRUTA (debe fallar si la comision es en base) -> venta limit
     con cantidad NETA (debe quedar en el libro) -> cancelar -> vender a mercado el residual.

Uso (desde la raiz del repo, con el venv activado):
    python scripts/probe_testnet.py                 # solo lectura + /order/test (no mueve saldo)
    python scripts/probe_testnet.py --execute       # ademas hace el ciclo real minimo en Testnet
    python scripts/probe_testnet.py --execute --max-usdt 20

Resultado: imprime un resumen y guarda el detalle completo en
    data/cache/probe_testnet_output.json      (data/cache esta en .gitignore)

No imprime ni guarda claves. Solo opera en Testnet: el cliente se construye con
testnet=True y TestnetClient rechaza cualquier otro endpoint.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUTPUT_PATH = ROOT / "data" / "cache" / "probe_testnet_output.json"
SYMBOL = "XRPUSDT"
BASE_ASSET = "XRP"
QUOTE_ASSET = "USDT"


# ----------------------------------------------------------------- utilidades
def D(value: Any) -> Decimal:
    return Decimal(str(value))


def floor_to(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def ceil_to(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_UP) * step


def plain(value: Decimal) -> str:
    return format(value.normalize(), "f") if value != 0 else "0"


def describe_error(error: Exception) -> dict[str, Any]:
    return {
        "error_type": type(error).__name__,
        "code": getattr(error, "code", None),
        "status": getattr(error, "status_code", None),
        "message": str(getattr(error, "message", error)),
    }


def attempt(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Ejecuta fn y devuelve {'ok': True, 'value': ...} o {'ok': False, error...}."""
    try:
        return {"ok": True, "value": fn(*args, **kwargs)}
    except Exception as error:  # noqa: BLE001 - el sondeo debe seguir y registrar
        return {"ok": False, **describe_error(error)}


def jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return plain(value)
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


# ------------------------------------------------------------------- filtros
def parse_filters(symbol_info: dict[str, Any]) -> dict[str, Any]:
    by_type = {f["filterType"]: f for f in symbol_info.get("filters", [])}
    price = by_type.get("PRICE_FILTER", {})
    lot = by_type.get("LOT_SIZE", {})
    notional = by_type.get("NOTIONAL") or by_type.get("MIN_NOTIONAL") or {}
    band_side = by_type.get("PERCENT_PRICE_BY_SIDE")
    band_plain = by_type.get("PERCENT_PRICE")
    parsed: dict[str, Any] = {
        "tick_size": D(price.get("tickSize", "0")),
        "price_min": D(price.get("minPrice", "0")),
        "price_max": D(price.get("maxPrice", "0")),
        "step_size": D(lot.get("stepSize", "0")),
        "min_qty": D(lot.get("minQty", "0")),
        "max_qty": D(lot.get("maxQty", "0")),
        "min_notional": D(notional.get("minNotional", "0")),
        "min_notional_source": "NOTIONAL" if "NOTIONAL" in by_type else (
            "MIN_NOTIONAL" if "MIN_NOTIONAL" in by_type else None),
        "notional_apply_to_market": notional.get("applyMinToMarket"),
        "max_num_orders": (by_type.get("MAX_NUM_ORDERS") or {}).get("maxNumOrders"),
        "band_kind": None,
        "raw_filter_types": sorted(by_type),
    }
    if band_side:
        parsed["band_kind"] = "PERCENT_PRICE_BY_SIDE"
        parsed["band"] = {
            "bid_up": D(band_side["bidMultiplierUp"]),
            "bid_down": D(band_side["bidMultiplierDown"]),
            "ask_up": D(band_side["askMultiplierUp"]),
            "ask_down": D(band_side["askMultiplierDown"]),
            "avg_price_mins": band_side.get("avgPriceMins"),
        }
    elif band_plain:
        parsed["band_kind"] = "PERCENT_PRICE"
        up, down = D(band_plain["multiplierUp"]), D(band_plain["multiplierDown"])
        parsed["band"] = {
            "bid_up": up, "bid_down": down, "ask_up": up, "ask_down": down,
            "avg_price_mins": band_plain.get("avgPriceMins"),
        }
    return parsed


def buy_test_price(filters: dict[str, Any], avg: Decimal, bid: Decimal) -> Decimal:
    tick = filters["tick_size"]
    price = bid * Decimal("0.98")
    band = filters.get("band")
    if band:
        price = max(price, avg * band["bid_down"] * Decimal("1.02"))
        price = min(price, avg * band["bid_up"] * Decimal("0.98"))
    return floor_to(price, tick)


def sell_test_price(filters: dict[str, Any], avg: Decimal, ask: Decimal) -> Decimal:
    tick = filters["tick_size"]
    price = ask * Decimal("1.02")
    band = filters.get("band")
    if band:
        price = min(price, avg * band["ask_up"] * Decimal("0.98"))
        price = max(price, avg * band["ask_down"] * Decimal("1.02"))
    return ceil_to(price, tick)


def qty_for_notional(target: Decimal, price: Decimal, filters: dict[str, Any]) -> Decimal:
    qty = ceil_to(target / price, filters["step_size"])
    return max(qty, filters["min_qty"])


# ------------------------------------------------------------------ secciones
def probe_filters(raw: Any) -> dict[str, Any]:
    info = raw.get_symbol_info(SYMBOL)
    if not info:
        raise RuntimeError(f"Testnet no conoce {SYMBOL}")
    parsed = parse_filters(info)
    result: dict[str, Any] = {
        "status": info.get("status"),
        "base_asset": info.get("baseAsset"),
        "quote_asset": info.get("quoteAsset"),
        "order_types": info.get("orderTypes"),
        "allow_trailing_stop": info.get("allowTrailingStop"),
        "oco_allowed": info.get("ocoAllowed"),
        "iceberg_allowed": info.get("icebergAllowed"),
        "quote_order_qty_market_allowed": info.get("quoteOrderQtyMarketAllowed"),
        "filters": parsed,
        "raw_filters": info.get("filters"),
    }
    exchange = attempt(raw.get_exchange_info)
    if exchange["ok"]:
        result["rate_limits"] = exchange["value"].get("rateLimits")
    else:
        result["rate_limits_error"] = exchange
    return result


def probe_prices(raw: Any, market_price_fn: Callable[[str], float]) -> dict[str, Any]:
    ticker = D(raw.get_symbol_ticker(symbol=SYMBOL)["price"])
    avg = D(raw.get_avg_price(symbol=SYMBOL)["price"])
    book = raw.get_orderbook_ticker(symbol=SYMBOL)
    bid, ask = D(book["bidPrice"]), D(book["askPrice"])
    depth = raw.get_order_book(symbol=SYMBOL, limit=50)
    mid = (bid + ask) / 2

    def usdt_within(levels: list[list[str]], limit_price: Decimal, below: bool) -> Decimal:
        total = Decimal("0")
        for price_str, qty_str in levels:
            price = D(price_str)
            if (price >= limit_price) if below else (price <= limit_price):
                total += price * D(qty_str)
        return total

    result: dict[str, Any] = {
        "testnet_ticker": ticker,
        "testnet_avg_price": avg,
        "testnet_bid": bid,
        "testnet_ask": ask,
        "spread_pct": (ask - bid) / mid * 100 if mid else None,
        "bid_depth_usdt_within_1pct": usdt_within(depth.get("bids", []), mid * Decimal("0.99"), True),
        "ask_depth_usdt_within_1pct": usdt_within(depth.get("asks", []), mid * Decimal("1.01"), False),
        "book_levels_returned": {"bids": len(depth.get("bids", [])), "asks": len(depth.get("asks", []))},
    }
    prod = attempt(market_price_fn, SYMBOL)
    if prod["ok"]:
        prod_price = D(prod["value"])
        result["production_price"] = prod_price
        result["testnet_vs_production_pct"] = (ticker - prod_price) / prod_price * 100
    else:
        result["production_price_error"] = prod
    return result


def probe_account(raw: Any) -> dict[str, Any]:
    account = raw.get_account()
    wanted = {BASE_ASSET, QUOTE_ASSET, "BNB"}
    return {
        "can_trade": account.get("canTrade"),
        "account_type": account.get("accountType"),
        "permissions": account.get("permissions"),
        "maker_commission_bps": account.get("makerCommission"),
        "taker_commission_bps": account.get("takerCommission"),
        "commission_rates": account.get("commissionRates"),
        "balances": {
            b["asset"]: {"free": D(b["free"]), "locked": D(b["locked"])}
            for b in account.get("balances", []) if b["asset"] in wanted
        },
    }


def probe_order_test(raw: Any, filters: dict[str, Any], prices: dict[str, Any]) -> dict[str, Any]:
    """Usa /order/test: valida filtros como una orden real pero no toca saldo ni libro."""
    tick, step = filters["tick_size"], filters["step_size"]
    avg, bid, ask = prices["testnet_avg_price"], prices["testnet_bid"], prices["testnet_ask"]
    target = max(filters["min_notional"] * 3, Decimal("15"))
    buy_price = buy_test_price(filters, avg, bid)
    buy_qty = qty_for_notional(target, buy_price, filters)
    sell_price = sell_test_price(filters, avg, ask)
    sell_qty = qty_for_notional(target, sell_price, filters)

    def order(**params: Any) -> Any:
        return raw.create_test_order(**params)

    base = {"symbol": SYMBOL, "type": "LIMIT", "timeInForce": "GTC"}
    results: dict[str, Any] = {
        "params_used": {
            "buy_price": buy_price, "buy_qty": buy_qty,
            "sell_price": sell_price, "sell_qty": sell_qty,
        },
    }
    results["valid_limit_buy_with_commission_rates"] = attempt(
        order, **base, side="BUY", quantity=plain(buy_qty), price=plain(buy_price),
        computeCommissionRates="true",
    )
    results["valid_limit_buy_as_float"] = attempt(
        order, **base, side="BUY", quantity=float(buy_qty), price=float(buy_price),
    )
    noisy_price = float(buy_price) * (1 + 1e-9)
    results["float_noise_price"] = {
        "sent_repr": repr(noisy_price),
        **attempt(order, **base, side="BUY", quantity=plain(buy_qty), price=noisy_price),
    }
    band = filters.get("band")
    low_price = floor_to(avg * (band["bid_down"] if band else Decimal("0.5")) * Decimal("0.9"), tick)
    high_price = ceil_to(avg * (band["ask_up"] if band else Decimal("2")) * Decimal("1.1"), tick)
    if low_price >= filters["price_min"] and low_price > 0:
        low_qty = qty_for_notional(target, low_price, filters)
        results["price_below_band_buy"] = {
            "price": low_price, **attempt(order, **base, side="BUY", quantity=plain(low_qty), price=plain(low_price)),
        }
    else:
        results["price_below_band_buy"] = {"skipped": "el limite inferior de la banda cae bajo minPrice"}
    high_qty = qty_for_notional(target, high_price, filters)
    results["price_above_band_sell"] = {
        "price": high_price, **attempt(order, **base, side="SELL", quantity=plain(high_qty), price=plain(high_price)),
    }
    below_min_qty = max(floor_to((filters["min_notional"] * Decimal("0.5")) / buy_price, step), filters["min_qty"])
    results["quantity_below_min_notional"] = {
        "qty": below_min_qty, "notional": below_min_qty * buy_price,
        **attempt(order, **base, side="BUY", quantity=plain(below_min_qty), price=plain(buy_price)),
    }
    results["qty_not_multiple_of_step"] = attempt(
        order, **base, side="BUY", quantity=plain(buy_qty + step / 3), price=plain(buy_price),
    )
    results["trailing_take_profit_limit_sell"] = attempt(
        raw.create_test_order, symbol=SYMBOL, side="SELL", type="TAKE_PROFIT_LIMIT",
        timeInForce="GTC", quantity=plain(sell_qty), price=plain(sell_price), trailingDelta=100,
    )
    return results


def sum_commission(fills: list[dict[str, Any]]) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = {}
    for fill in fills:
        asset = str(fill.get("commissionAsset", "?"))
        totals[asset] = totals.get(asset, Decimal("0")) + D(fill.get("commission", "0"))
    return totals


def balances_now(raw: Any) -> dict[str, dict[str, Decimal]]:
    wanted = {BASE_ASSET, QUOTE_ASSET, "BNB"}
    return {
        b["asset"]: {"free": D(b["free"]), "locked": D(b["locked"])}
        for b in raw.get_account().get("balances", []) if b["asset"] in wanted
    }


def probe_execute(raw: Any, testnet: Any, filters: dict[str, Any], prices: dict[str, Any],
                  max_usdt: Decimal) -> dict[str, Any]:
    """Ciclo real minimo. Toda orden limit creada se cancela en el bloque finally."""
    tick, step = filters["tick_size"], filters["step_size"]
    avg, ask = prices["testnet_avg_price"], prices["testnet_ask"]
    target = min(max(filters["min_notional"] * 3, Decimal("15")), max_usdt)
    steps: dict[str, Any] = {"target_usdt": target}
    created_limit_ids: list[int] = []
    inventory_left = Decimal("0")
    try:
        buy_qty = qty_for_notional(target, ask, filters)
        steps["buy_qty_requested"] = buy_qty
        steps["balances_before"] = balances_now(raw)

        buy = raw.create_order(
            symbol=SYMBOL, side="BUY", type="MARKET", quantity=plain(buy_qty),
            newOrderRespType="FULL",
        )
        steps["market_buy"] = {
            "order_id": buy.get("orderId"), "status": buy.get("status"),
            "executed_qty": D(buy.get("executedQty", "0")),
            "cummulative_quote_qty": D(buy.get("cummulativeQuoteQty", "0")),
            "fills": buy.get("fills"),
            "commission_from_response": sum_commission(buy.get("fills") or []),
        }
        executed = D(buy.get("executedQty", "0"))
        inventory_left = executed

        trades = raw.get_my_trades(symbol=SYMBOL, orderId=buy["orderId"])
        commission_trades = sum_commission(trades)
        steps["my_trades_for_buy"] = {
            "trades": trades, "commission_by_asset": commission_trades,
        }
        steps["balances_after_buy"] = balances_now(raw)
        base_before = steps["balances_before"].get(BASE_ASSET, {}).get("free", Decimal("0"))
        base_after = steps["balances_after_buy"].get(BASE_ASSET, {}).get("free", Decimal("0"))
        steps["base_balance_delta"] = base_after - base_before
        base_fee = commission_trades.get(BASE_ASSET, Decimal("0"))
        steps["fee_charged_in_base"] = base_fee > 0
        net_qty = floor_to(executed - base_fee, step)
        steps["net_qty_after_fee"] = net_qty
        steps["gross_minus_delta"] = executed - steps["base_balance_delta"]

        sell_price = sell_test_price(filters, avg, ask)
        steps["sell_price"] = sell_price
        steps["net_notional_at_sell_price"] = net_qty * sell_price

        gross_qty = floor_to(executed, step)
        gross_attempt = attempt(
            raw.create_order, symbol=SYMBOL, side="SELL", type="LIMIT", timeInForce="GTC",
            quantity=plain(gross_qty), price=plain(sell_price),
        )
        steps["limit_sell_gross_qty"] = {"qty": gross_qty, **gross_attempt}
        if gross_attempt["ok"]:
            gross_id = gross_attempt["value"]["orderId"]
            created_limit_ids.append(gross_id)
            steps["limit_sell_gross_qty"]["value"] = {"orderId": gross_id}
            # Si se acepto, liberar el saldo bloqueado antes de probar la cantidad neta.
            gross_cancel = attempt(raw.cancel_order, symbol=SYMBOL, orderId=gross_id)
            steps["limit_sell_gross_qty"]["cancelled"] = gross_cancel["ok"]
            if gross_cancel["ok"]:
                created_limit_ids.remove(gross_id)

        net_attempt = attempt(
            testnet.place_order, SYMBOL, "SELL", float(net_qty), float(sell_price), "LIMIT",
        )
        steps["limit_sell_net_qty_via_TestnetClient"] = {"qty": net_qty, **net_attempt}
        if net_attempt["ok"]:
            order_id = net_attempt["value"]["order_id"]
            created_limit_ids.append(order_id)
            open_orders = attempt(testnet.get_open_orders, SYMBOL)
            steps["net_sell_visible_in_open_orders"] = bool(
                open_orders["ok"] and any(o["order_id"] == order_id for o in open_orders["value"])
            )
            cancel = attempt(testnet.cancel_order, SYMBOL, order_id)
            steps["cancel_net_sell"] = cancel
            if cancel["ok"] and order_id in created_limit_ids:
                created_limit_ids.remove(order_id)
    finally:
        for order_id in list(created_limit_ids):
            steps.setdefault("cleanup_cancels", []).append(
                {"order_id": order_id, **attempt(raw.cancel_order, symbol=SYMBOL, orderId=order_id)}
            )
        balances = balances_now(raw)
        steps["balances_before_cleanup_sell"] = balances
        sellable = floor_to(balances.get(BASE_ASSET, {}).get("free", Decimal("0")), step)
        if inventory_left > 0 and sellable >= filters["min_qty"]:
            sell = attempt(
                raw.create_order, symbol=SYMBOL, side="SELL", type="MARKET",
                quantity=plain(min(sellable, floor_to(inventory_left, step))),
                newOrderRespType="FULL",
            )
            steps["cleanup_market_sell"] = sell
            if sell["ok"]:
                fills = sell["value"].get("fills") or []
                steps["cleanup_market_sell_commission"] = sum_commission(fills)
        steps["balances_final"] = balances_now(raw)

    buy_info = steps.get("market_buy") or {}
    sell_info = (steps.get("cleanup_market_sell") or {}).get("value") or {}
    if buy_info and sell_info:
        spent = D(buy_info["cummulative_quote_qty"])
        received = D(sell_info.get("cummulativeQuoteQty", "0"))
        quote_fees = steps.get("cleanup_market_sell_commission", {}).get(QUOTE_ASSET, Decimal("0"))
        steps["round_trip_market_cost_usdt"] = spent - (received - quote_fees)
        if spent:
            steps["round_trip_market_cost_pct"] = (spent - (received - quote_fees)) / spent * 100
    return steps


# ------------------------------------------------------------------- resumen
def line(label: str, value: Any) -> str:
    return f"  {label:<44} {value}"


def summarize(report: dict[str, Any]) -> str:
    out = ["", "=== RESUMEN DEL SONDEO (detalle completo en el JSON) ==="]
    sections = report["sections"]
    flt = sections.get("filters", {})
    if flt.get("ok"):
        v = flt["value"]
        f = v["filters"]
        out += ["", "FILTROS " + SYMBOL,
                line("tickSize / stepSize / minQty", f"{plain(f['tick_size'])} / {plain(f['step_size'])} / {plain(f['min_qty'])}"),
                line("minNotional (filtro)", f"{plain(f['min_notional'])} ({f['min_notional_source']})"),
                line("MAX_NUM_ORDERS", f["max_num_orders"]),
                line("banda de precio", f["band_kind"]),
                line("orderTypes incluye TAKE_PROFIT_LIMIT", "TAKE_PROFIT_LIMIT" in (v.get("order_types") or [])),
                line("allowTrailingStop", v.get("allow_trailing_stop"))]
        if "band" in f:
            b = f["band"]
            out.append(line("bid up/down, ask up/down", f"{plain(b['bid_up'])}/{plain(b['bid_down'])}, {plain(b['ask_up'])}/{plain(b['ask_down'])}"))
    else:
        out += ["", "FILTROS: ERROR", line("detalle", flt)]
    pr = sections.get("prices", {})
    if pr.get("ok"):
        v = pr["value"]
        out += ["", "PRECIOS",
                line("Testnet ticker / avgPrice", f"{plain(v['testnet_ticker'])} / {plain(v['testnet_avg_price'])}"),
                line("Testnet bid / ask / spread %", f"{plain(v['testnet_bid'])} / {plain(v['testnet_ask'])} / {v['spread_pct']:.4f}%"),
                line("Produccion", plain(v["production_price"]) if "production_price" in v else v.get("production_price_error")),
                line("Testnet vs produccion", f"{v['testnet_vs_production_pct']:.3f}%" if "testnet_vs_production_pct" in v else "n/d"),
                line("profundidad bid/ask +-1% (USDT)", f"{plain(v['bid_depth_usdt_within_1pct'].quantize(Decimal('0.01')))} / {plain(v['ask_depth_usdt_within_1pct'].quantize(Decimal('0.01')))}")]
    else:
        out += ["", "PRECIOS: ERROR", line("detalle", pr)]
    ac = sections.get("account", {})
    if ac.get("ok"):
        v = ac["value"]
        out += ["", "CUENTA",
                line("canTrade / tipo", f"{v['can_trade']} / {v['account_type']}"),
                line("maker/taker (bps)", f"{v['maker_commission_bps']}/{v['taker_commission_bps']}"),
                line("commissionRates", v["commission_rates"]),
                line("saldos", {k: {kk: plain(vv) for kk, vv in b.items()} for k, b in v["balances"].items()})]
    ot = sections.get("order_test", {})
    if ot.get("ok"):
        out += ["", "/order/test (no mueve saldo)"]
        for name, res in ot["value"].items():
            if name == "params_used" or not isinstance(res, dict):
                continue
            status = "ACEPTADA" if res.get("ok") else f"RECHAZADA code={res.get('code')} {str(res.get('message'))[:90]}"
            out.append(line(name, status))
    else:
        out += ["", "/order/test: ERROR", line("detalle", ot)]
    ex = sections.get("execute")
    if ex is None:
        out += ["", "EJECUCION REAL: no se corrio (usa --execute para medir comision real y el rechazo por cantidad bruta)"]
    elif ex.get("ok"):
        v = ex["value"]
        gross = v.get("limit_sell_gross_qty", {})
        out += ["", "CICLO REAL MINIMO",
                line("comision por compra (my_trades)", {k: plain(x) for k, x in v.get("my_trades_for_buy", {}).get("commission_by_asset", {}).items()}),
                line("comision cobrada en la moneda base", v.get("fee_charged_in_base")),
                line("cantidad comprada -> neta vendible", f"{plain(v['market_buy']['executed_qty'])} -> {plain(v.get('net_qty_after_fee', Decimal('0')))}"),
                line("venta limit con cantidad BRUTA", "ACEPTADA (inesperado si hay comision en base)" if gross.get("ok") else f"RECHAZADA code={gross.get('code')} {str(gross.get('message'))[:70]}"),
                line("venta limit con cantidad NETA", "en libro" if v.get("net_sell_visible_in_open_orders") else "NO quedo en libro"),
                line("notional neto al precio de venta", plain(v.get("net_notional_at_sell_price", Decimal('0')).quantize(Decimal('0.0001')))),
                line("costo ida y vuelta a mercado (USDT / %)", f"{plain(v['round_trip_market_cost_usdt'].quantize(Decimal('0.0001'))) if 'round_trip_market_cost_usdt' in v else 'n/d'} / {plain(v['round_trip_market_cost_pct'].quantize(Decimal('0.001'))) + '%' if 'round_trip_market_cost_pct' in v else 'n/d'}")]
    else:
        out += ["", "CICLO REAL MINIMO: ERROR", line("detalle", ex)]
    out += ["", f"JSON completo: {OUTPUT_PATH}", ""]
    return "\n".join(out)


# ---------------------------------------------------------------------- main
def run_probe(raw: Any, testnet: Any, market_price_fn: Callable[[str], float],
              execute: bool, max_usdt: Decimal) -> dict[str, Any]:
    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbol": SYMBOL, "execute": execute, "sections": {},
    }
    sections = report["sections"]
    sections["filters"] = attempt(probe_filters, raw)
    sections["prices"] = attempt(probe_prices, raw, market_price_fn)
    sections["account"] = attempt(probe_account, raw)
    if sections["filters"]["ok"] and sections["prices"]["ok"]:
        filters = sections["filters"]["value"]["filters"]
        prices = sections["prices"]["value"]
        sections["order_test"] = attempt(probe_order_test, raw, filters, prices)
        if execute:
            sections["execute"] = attempt(probe_execute, raw, testnet, filters, prices, max_usdt)
    else:
        sections["order_test"] = {"ok": False, "message": "omitido: fallaron filtros o precios"}
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--execute", action="store_true",
                        help="hace un ciclo real minimo en Testnet (compra a mercado, ventas limit, limpieza)")
    parser.add_argument("--max-usdt", default="15", help="tope de USDT para la compra de prueba (defecto 15)")
    args = parser.parse_args(argv)

    from config.settings import Settings
    from data.binance_client import BinanceClient
    from data.testnet_client import TestnetClient

    settings = Settings()
    key = (settings.testnet_api_key or "").strip()
    secret = (settings.testnet_api_secret or "").strip()
    if not key or not secret or key.casefold().startswith("tu_") or secret.casefold().startswith("tu_"):
        print("Faltan credenciales de Testnet (TESTNET_API_KEY / TESTNET_SECRET en .env).", file=sys.stderr)
        return 2

    testnet = TestnetClient(key, secret, production_api_key=settings.binance_api_key)
    market = BinanceClient(None, None)
    report = run_probe(
        testnet.client, testnet, lambda symbol: market.get_current_price(symbol)["price"],
        execute=args.execute, max_usdt=D(args.max_usdt),
    )
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(jsonable(report), indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(summarize(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
