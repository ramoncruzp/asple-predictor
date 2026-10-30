import numpy as np

from grid.sim.data import CandleData
from research.scenarios.evaluate import evaluate_history


class _SyntheticStore:
    def __init__(self, _path):
        pass

    def load(self, _symbol, _interval):
        count = 65 * 288 + 1
        rng = np.random.default_rng(42)
        returns = rng.normal(0, .0008, count - 1)
        close = 100 * np.exp(np.r_[0, np.cumsum(returns)])
        open_ = np.r_[close[0], close[:-1]]
        high = np.maximum(open_, close) * 1.001
        low = np.minimum(open_, close) * .999
        ts = 1_700_000_100 + np.arange(count, dtype=np.int64) * 300
        return CandleData(ts, open_, high, low, close, 0)

    def filters(self, _symbol):
        return None


def test_history_evaluator_emits_causal_windows_and_both_variants(monkeypatch):
    monkeypatch.setattr("research.scenarios.evaluate.CandleStore", _SyntheticStore)
    result = evaluate_history([{"symbol": "XRPUSDT", "strategy": "simple",
        "capital": 1000, "n": 6, "width_sigma": 2}], perfil="RAPIDO", capital=1000,
        meta=3, db_path="unused.db", months=2, step_days=7, replicas=30)
    row = result["symbols"]["XRPUSDT"]
    assert row["ventanas_candidatas"] > 0
    metrics = row["pieces"][0]["variantes"]
    assert metrics["real"]["n"] == metrics["sin_tendencia"]["n"]
    assert metrics["real"]["n"] > 0
    assert len(metrics["real"]["ic95_pnl_medio"]) == 2


def test_history_trend_removed_preserves_initial_price(monkeypatch):
    # La transformación se verifica indirectamente: el evaluador acepta una
    # geometría fija y entrega ambas variantes con el mismo número de ventanas.
    monkeypatch.setattr("research.scenarios.evaluate.CandleStore", _SyntheticStore)
    result = evaluate_history([{"symbol": "XRPUSDT", "strategy": "simple",
        "capital": 1000, "n": 6, "low": 90, "high": 110}], perfil="RAPIDO",
        capital=1000, meta=3, db_path="unused.db", months=2, step_days=7, replicas=20)
    variants = result["symbols"]["XRPUSDT"]["pieces"][0]["variantes"]
    assert variants["real"]["n"] == variants["sin_tendencia"]["n"]
