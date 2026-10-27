from __future__ import annotations

import ast
import csv
import importlib.util
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "medir_orden_intravela.py"
spec = importlib.util.spec_from_file_location("medir_orden_intravela", SCRIPT)
measurement = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = measurement
spec.loader.exec_module(measurement)


def _candle_data(times, opens, highs, lows, closes, seconds):
    from grid.sim.data import CandleData
    return CandleData(np.asarray(times, dtype=np.int64), np.asarray(opens, float),
                      np.asarray(highs, float), np.asarray(lows, float),
                      np.asarray(closes, float), 0)


def test_5m_misses_intrabar_buy_then_sell_that_1m_simulator_completes():
    # Grid 8–12, nivel de compra 9 y venta 10. La segunda vela 5m
    # contiene ambos precios, pero la venta no está activa hasta la vela siguiente.
    five = _candle_data([0, 300, 600], [10, 9, 9], [10, 10.5, 9.5],
                        [10, 8.5, 9], [10, 10, 9.2], 300)
    one = _candle_data([0, 60, 120, 180, 240, 300, 360],
                       [10, 10, 9, 9, 10, 10, 9],
                       [10, 10, 10, 10.5, 10.2, 10, 9.5],
                       [10, 8.5, 8.5, 9, 9.5, 9.5, 9],
                       [10, 9, 9.2, 10, 10, 9.8, 9.2], 60)
    geometry = {"low": 8, "high": 12, "n": 4}
    five_result = measurement._run_grid(five, geometry, "simple")
    one_result = measurement._run_grid(one, geometry, "simple")
    assert five_result["metrics"]["cycles"] == 0
    assert one_result["metrics"]["cycles"] >= 1


def test_exact_buy_touch_is_counted_but_does_not_fill():
    data = _candle_data([0, 300], [10.5, 10.5], [10.5, 10.5], [10.5, 10], [10.5, 10.2], 300)
    geometry = {"low": 9, "high": 11, "n": 4}
    _, exact = measurement.ambiguous_and_exact(data, geometry, "simple")
    result = measurement._run_grid(data, geometry, "simple")
    assert exact >= 1
    assert result["metrics"]["cycles"] == 0
    assert not any(event.get("type") == "BUY_FILLED" for event in result["events"])


def test_script_import_has_no_database_or_dotenv_dependency():
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert not any(name == "dotenv" or name.startswith("database") for name in imported)
    assert "database" not in imported and "config.settings" not in imported
    assert "grid.meta_operator" not in imported
    assert ".env" not in SCRIPT.read_text(encoding="utf-8")
    isolated = r"""
import builtins, importlib.util, pathlib, sys
def guard(path, *args, **kwargs):
    if pathlib.Path(str(path)).name.lower() == '.env':
        raise AssertionError('se intentó abrir .env')
    return real_open(path, *args, **kwargs)
real_open = builtins.open
builtins.open = guard
real_path_open = pathlib.Path.open
def guard_path(self, *args, **kwargs):
    if self.name.lower() == '.env':
        raise AssertionError('se intentó abrir .env')
    return real_path_open(self, *args, **kwargs)
pathlib.Path.open = guard_path
spec = importlib.util.spec_from_file_location('isolated_measure', r'%s')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert not any(name == 'database' or name.startswith('database.') for name in sys.modules)
""" % SCRIPT
    checked = subprocess.run([sys.executable, "-c", isolated], cwd=ROOT,
                             capture_output=True, text=True)
    assert checked.returncode == 0, checked.stderr


def test_public_1m_download_uses_existing_client_without_network():
    class PublicAPI:
        def __init__(self):
            self.kwargs = None

        def get_klines(self, **kwargs):
            self.kwargs = kwargs
            return [[kwargs["startTime"], "1", "2", "0.5", "1.5", "0", 0,
                     "0", 0, "0", "0"]]

    class Client:
        def __init__(self):
            self.client = PublicAPI()

        @staticmethod
        def _request_with_retries(operation, **_kwargs):
            return operation()

        @staticmethod
        def _klines_to_dataframe(rows):
            return rows

    client = Client()
    start = measurement.datetime(2026, 1, 1, tzinfo=measurement.timezone.utc)
    rows = measurement._download_1m(client, "TESTUSDT", start)
    assert len(rows) == 1
    assert client.client.kwargs["interval"] == "1m"
    assert client.client.kwargs["symbol"] == "TESTUSDT"


def test_csv_mode_runs_synthetic_inputs_and_writes_json_and_csv(tmp_path):
    csv_dir, out = tmp_path / "velas", tmp_path / "salida"
    csv_dir.mkdir()
    start = 1_700_000_100
    for interval, count in ((300, 20 * 24 * 12), (60, 20 * 24 * 60)):
        path = csv_dir / f"TESTUSDT_{'5m' if interval == 300 else '1m'}.csv"
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(("timestamp", "open", "high", "low", "close"))
            for i in range(count):
                # Repite un recorrido intrabar sencillo, sin datos ni red externos.
                price = 10.0
                low, high = ((8.8, 10.6) if i % 5 == 2 else (9.2, 10.2))
                writer.writerow((measurement.datetime.fromtimestamp(start + i * interval,
                    measurement.timezone.utc).isoformat(), price, high, low, price))
    rows = measurement.measure(["TESTUSDT"], 20, "csv", csv_dir, out)
    assert len(rows) == 5
    assert (out / "medicion_intravela.json").is_file()
    assert (out / "medicion_intravela.csv").is_file()
