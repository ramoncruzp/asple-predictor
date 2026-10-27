"""Tabla v2/v3 con velas sintéticas y simuladores reales, sin red."""
import ast
import json
from pathlib import Path
import subprocess
import types
import numpy as np

import grid.meta_operator as module
from grid.sim.data import CandleData
from data.exchange_filters import SymbolFilters
from tests.grid_fakes import fake_symbol_info


def compare():
    source = subprocess.check_output(["git", "show", "a062e09:grid/meta_operator.py"]).decode("utf-8")
    cls = next(node for node in ast.parse(source).body if isinstance(node, ast.ClassDef) and node.name == "MetaOperator")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "_replay_window")
    namespace = dict(vars(module))
    exec(compile(ast.Module(body=[method], type_ignores=[]), "a062e09:_replay_window", "exec"), namespace)
    operator = module.MetaOperator(object(), object(), None, None, None)
    info = fake_symbol_info()
    info["filters"][1].update({"minQty": "0.001", "stepSize": "0.001"})
    filters = SymbolFilters.from_symbol_info(info)
    old = types.MethodType(namespace["_replay_window"], operator)
    pieces = {"JUP70": ("smart", 70, 90, 110), "JUP50": ("smart", 50, 90, 110),
        "ZRO30": ("simple", 30, 94, 106), "XRP30": ("simple", 30, 90, 110),
        "botXRP20": ("individual_bot", 20, 99, 100.2)}
    portfolios = [("JUP grid 70", ["JUP70"]), ("JUP 70 + ZRO 30", ["JUP70", "ZRO30"]),
        ("JUP 70 + bot XRP 20", ["JUP70", "botXRP20"]),
        ("XRP 30 + JUP 70", ["XRP30", "JUP70"]),
        ("JUP 50 + ZRO 30 + bot XRP 20", ["JUP50", "ZRO30", "botXRP20"])]
    memo = {}
    def run(name, version, count):
        key = (name, version, count if version == "v2" else 0)
        if key in memo:
            return memo[key]
        strategy, capital, low, high = pieces[name]
        rows = []
        for window in range(3):
            ts = np.arange(window * 7 * 86400, (window * 7 + 20) * 86400, 300)
            prices = 100 + 8 * np.sin(np.arange(len(ts)) * 2 * np.pi / (12 * 8) + window * .2)
            sample = CandleData(timestamp=ts, open=prices, high=prices * 1.001,
                low=prices * .999, close=prices, gaps=0)
            replay = old if version == "v2" else operator._replay_window
            rows.append(replay(sample, str(window), strategy, 1 if strategy == "individual_bot" else 4,
                capital, 3 / count if version == "v2" else 3, low, high,
                {"max_loss_pct": 18, "min_margin_pct": .3, "_meta_capital_initial": 100},
                filters=filters))
        memo[key] = {"window_results": rows}
        return memo[key]
    table = []
    for name, ids in portfolios:
        record = {"cartera": name}
        for version in ("v2", "v3"):
            alternatives = [run(piece, version, len(ids)) for piece in ids]
            kwargs = {"capital_initial": 100, "max_loss_pct": 18} if version == "v3" else {}
            summary, _ = module.combine_aligned_replays(alternatives, 3, 20, **kwargs)
            record[version] = {key: summary[key] for key in ("probability", "median_progress_pct", "days_median")}
        table.append(record)
    return table


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    table = compare()
    args.output.write_text(json.dumps(table, indent=2), encoding="utf-8")
    print(json.dumps(table, indent=2))
