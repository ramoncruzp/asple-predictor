from __future__ import annotations

from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from data.exchange_filters import SymbolFilters
from grid.scanner import score_symbol
from tests.grid_fakes import fake_symbol_info


FILTERS = SymbolFilters.from_symbol_info(fake_symbol_info())


def market():
    i5 = np.arange(30 * 24 * 12)
    i1 = np.arange(30 * 24)
    return {"symbol": "XRPUSDT", "active": True, "quote_asset": "USDT", "status": "TRADING",
            "volume_24h_quote": 5_000_000, "bid": 99.99, "ask": 100.01, "capital": 1000,
            "klines_5m": pd.DataFrame({"close": 100 + 8 * np.sin(i5 / 55)}),
            "klines_1h": pd.DataFrame({"close": 100 + 8 * np.sin(i1 / 4)})}


def test_eligible_result_is_reproducible_weighted_and_exposes_components():
    value = market()
    first = score_symbol(value, FILTERS)
    second = score_symbol(value, FILTERS)
    assert first == second
    assert first["eligible"]
    assert abs(first["score"] - sum(row["contribution"] for row in first["components"])) < 2e-6
    assert sum(row["weight"] for row in first["components"]) == pytest.approx(1.0)
    assert {row["name"] for row in first["components"]} == {
        "cost_headroom", "liquidity", "historical_oscillation", "trend_penalty"}
    assert first["historical_oscillation"]["crossings"] >= 0
    assert first["suggested_structure"]["net_edge_pct_per_cycle"] > 0
    assert set(first) <= {"symbol", "eligible", "score", "hard_filters", "components", "reasons",
                          "suggested_structure", "historical_oscillation", "efficiency_ratio_30d", "warnings"}


@pytest.mark.parametrize("field,value,name", [
    ("active", False, "active_registry"), ("quote_asset", "BTC", "quote_asset"),
    ("status", "HALT", "trading_status"), ("volume_24h_quote", 1, "volume_24h"),
    ("ask", 100.5, "spread"), ("capital", 1, "structure"),
    ("klines_5m", pd.DataFrame({"close": [100] * 12}), "history"),
])
def test_each_hard_gate_fails_independently_with_reason(field, value, name):
    value_market = market()
    value_market[field] = value
    result = score_symbol(value_market, FILTERS)
    failed = next(row for row in result["hard_filters"] if row["name"] == name)
    assert not failed["passed"]
    assert failed["reason"]
    assert result["eligible"] is False
    assert result["score"] is None


def test_small_cell_margin_is_warning_not_a_fixed_large_capital_exclusion():
    value = market()
    value["capital"] = 40
    fine_step_filters = SymbolFilters(Decimal("0.0001"), Decimal("0.0001"), Decimal("100000"),
        Decimal("0.005"), Decimal("0.005"), Decimal("100000000"), Decimal("5"), True, 200)
    result = score_symbol(value, fine_step_filters)
    # A $10 cell is allowed when its own filters and net edge pass.
    assert result["eligible"]
    assert result["suggested_structure"]["cell_usdt"] == Decimal("10")
    assert "probability" not in str(result).casefold()
    assert "predict" not in " ".join(item["explanation"] for item in result["components"]).casefold()


def test_feasible_structure_with_zero_net_edge_is_still_ineligible(monkeypatch):
    import grid.scanner as scanner
    monkeypatch.setattr(scanner, "suggest_structure", lambda *a, **k: {
        "feasible": True, "range_low": Decimal("90"), "range_high": Decimal("110"),
        "n_levels": 4, "spacing_pct": 1.0, "cell_usdt": Decimal("250"),
        "net_edge_pct_per_cycle": 0.0, "reasons": []})
    result = scanner.score_symbol(market(), FILTERS)
    structure_gate = next(row for row in result["hard_filters"] if row["name"] == "structure")
    assert structure_gate["passed"] is False
    assert result["eligible"] is False
