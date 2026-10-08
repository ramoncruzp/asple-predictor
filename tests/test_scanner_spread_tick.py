from __future__ import annotations

from decimal import Decimal

import pytest

from data.exchange_filters import SymbolFilters
from grid.scanner import score_symbol
from tests.test_grid_scanner import FILTERS, market
from tests.grid_fakes import fake_symbol_info


def test_one_tick_spread_passes_with_minimum_possible_reason():
    value=market(); value.update(symbol="PEPEUSDT", bid=0.00000427, ask=0.00000428,
        quote_asset="USDT", status="TRADING")
    info=fake_symbol_info(); info["symbol"]="PEPEUSDT"
    info["filters"][0].update(minPrice="0.00000001", maxPrice="1", tickSize="0.00000001")
    info["filters"][1].update(stepSize="1", minQty="1", maxQty="1000000000")
    filters=SymbolFilters.from_symbol_info(info)
    result=score_symbol(value,filters)
    spread=next(row for row in result["hard_filters"] if row["name"]=="spread")
    assert spread["passed"] is True
    assert "1 tick" in spread["reason"]


def test_two_ticks_above_bps_limit_still_fail_and_xrp_unchanged():
    value=market(); value.update(symbol="PEPEUSDT", bid=0.00000426, ask=0.00000428)
    info=fake_symbol_info(); info["filters"][0].update(minPrice="0.00000001",maxPrice="1",tickSize="0.00000001")
    info["filters"][1].update(stepSize="1",minQty="1",maxQty="1000000000")
    spread=next(row for row in score_symbol(value,SymbolFilters.from_symbol_info(info))["hard_filters"] if row["name"]=="spread")
    assert spread["passed"] is False
    xrp=score_symbol(market(),FILTERS)
    assert xrp["hard_filters"][next(i for i,r in enumerate(xrp["hard_filters"]) if r["name"]=="spread")]["passed"]


def test_scanner_score_defaults_are_pinned_and_low_edge_warning_travels(monkeypatch):
    import config.settings as settings
    cfg=settings.SCANNER_DEFAULTS
    assert cfg["max_spread_bps"] == 15
    assert cfg["weights"] == {"cost_headroom":0.0,"liquidity":.20,"historical_oscillation":.35,"trend_penalty":.10}
    import grid.scanner as scanner
    original=scanner.suggest_structure
    monkeypatch.setattr(scanner,"suggest_structure",lambda *a,**k:{**original(*a,**k),
        "edge_gross_pct":.05,"net_edge_pct_per_cycle":-.2})
    result=scanner.score_symbol(market(),FILTERS)
    assert result["edge_warning"] and "bajo" in result["edge_warning"]
