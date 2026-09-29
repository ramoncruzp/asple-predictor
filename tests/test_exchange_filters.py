from decimal import Decimal
import random

import pytest

from data.exchange_filters import FilterViolation, SymbolFilters


def symbol_info(notional_type="NOTIONAL", band_type="PERCENT_PRICE_BY_SIDE"):
    filters = [
        {"filterType": "PRICE_FILTER", "minPrice": "0.01", "maxPrice": "1000", "tickSize": "0.01"},
        {"filterType": "LOT_SIZE", "minQty": "0.1", "maxQty": "10000", "stepSize": "0.1"},
        {"filterType": "MAX_NUM_ORDERS", "maxNumOrders": 200},
    ]
    filters.append(
        {"filterType": notional_type, "minNotional": "5", "applyToMarket": True,
         "applyMinToMarket": True, "maxNotional": "0"}
    )
    if band_type == "PERCENT_PRICE_BY_SIDE":
        filters.append({
            "filterType": band_type, "bidMultiplierUp": "1.2", "bidMultiplierDown": "0.5",
            "askMultiplierUp": "2", "askMultiplierDown": "0.8",
        })
    elif band_type == "PERCENT_PRICE":
        filters.append({"filterType": band_type, "multiplierUp": "1.2", "multiplierDown": "0.5"})
    return {"symbol": "XRPUSDT", "filters": filters}


def test_parses_notional_and_side_specific_band():
    filters = SymbolFilters.from_symbol_info(symbol_info())
    assert filters.min_notional == Decimal("5")
    assert filters.apply_min_to_market is True
    assert filters.max_num_orders == 200
    assert filters.band.bid_up == Decimal("1.2")
    assert filters.band.ask_down == Decimal("0.8")


def test_parses_min_notional_and_common_percent_band():
    filters = SymbolFilters.from_symbol_info(symbol_info("MIN_NOTIONAL", "PERCENT_PRICE"))
    assert filters.min_notional == Decimal("5")
    assert filters.band.bid_up == filters.band.ask_up == Decimal("1.2")
    assert filters.band.bid_down == filters.band.ask_down == Decimal("0.5")


def test_rounding_modes_and_qty_floor():
    filters = SymbolFilters.from_symbol_info(symbol_info())
    assert filters.round_price("1.235", "nearest") == Decimal("1.24")
    assert filters.round_price("1.239", "down") == Decimal("1.23")
    assert filters.round_price("1.231", "up") == Decimal("1.24")
    assert filters.round_qty_down("1.29") == Decimal("1.2")
    assert filters.round_qty_down("0.09") == Decimal("0.0")


def test_seeded_rounding_outputs_exact_tick_and_step_multiples():
    filters = SymbolFilters.from_symbol_info(symbol_info())
    rng = random.Random(42)
    for _ in range(200):
        price = Decimal(str(rng.uniform(0, 1000)))
        qty = Decimal(str(rng.uniform(0, 1000)))
        assert filters.round_price(price, "down") % filters.tick_size == 0
        rounded_qty = filters.round_qty_down(qty)
        assert rounded_qty % filters.step_size == 0
        assert rounded_qty <= qty


@pytest.mark.parametrize(
    "side,price,qty,avg,filter_name",
    [
        ("BUY", "1.001", "10", "1", "PRICE_FILTER"),
        ("BUY", "0", "10", "1", "PRICE_FILTER"),
        ("BUY", "1", "0.05", "1", "LOT_SIZE"),
        ("BUY", "1", "10001", "1", "LOT_SIZE"),
        ("BUY", "1", "1", "1", "NOTIONAL"),
        ("BUY", "1.3", "10", "1", "PERCENT_PRICE_BY_SIDE"),
        ("SELL", "0.7", "10", "1", "PERCENT_PRICE_BY_SIDE"),
    ],
)
def test_validate_order_rejects_each_filter(side, price, qty, avg, filter_name):
    filters = SymbolFilters.from_symbol_info(symbol_info())
    with pytest.raises(FilterViolation) as error:
        filters.validate_order(side, price, qty, avg)
    assert error.value.filter_name == filter_name


def test_price_band_uses_exchange_average_price():
    filters = SymbolFilters.from_symbol_info(symbol_info())
    # Price is outside the band around 1.0 but inside the band around 1.1.
    with pytest.raises(FilterViolation, match="average-price band"):
        filters.validate_order("BUY", "1.25", "10", "1.0")
    filters.validate_order("BUY", "1.25", "10", "1.1")
