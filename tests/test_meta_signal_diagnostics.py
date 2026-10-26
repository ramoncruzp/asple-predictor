import pytest
import json
import sqlite3

from grid.policy import DEFAULT_SMART_PARAMS, break_prob
from scripts.diagnostico_senales_meta import compare_volatility_scales, diagnose, summarize


def test_hourly_volatility_is_miscompared_to_24h_sigma_and_saturates_signal():
    pair = compare_volatility_scales(.01, .05, 24)
    assert pair["normalized_current"] == -1.0
    assert pair["forecast_sigma_24h_equivalent"] == pytest.approx(.01 * 24 ** .5)
    assert pair["normalized_if_scaled_to_24h"] == pytest.approx(-.0202, abs=.001)

    # La ruptura también subestima el movimiento al pasar sigma por hora como sigma 24 h.
    hourly = .04
    price, low, high = 108.0, 90.0, 108.0
    old_probability = break_prob(price, low, high, hourly, DEFAULT_SMART_PARAMS)[0]
    comparable_probability = break_prob(price, low, high, hourly * 24 ** .5,
        DEFAULT_SMART_PARAMS)[0]
    assert 1 - 2 * old_probability > .95
    assert .05 < comparable_probability < .95
    assert 1 - 2 * comparable_probability < .5


def test_signal_summary_reports_quantiles_and_both_saturation_tails():
    result = summarize([-1, -1, 0, 1])
    assert result["n"] == 4
    assert result["min"] == -1
    assert result["median"] == pytest.approx(-.5)
    assert result["max"] == 1
    assert result["at_minus_one_pct"] == 50
    assert result["at_plus_one_pct"] == 25


def test_diagnostic_reads_sqlite_without_writing(tmp_path, capsys):
    database = tmp_path / "diagnostic.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE meta_learning_samples "
            "(symbol TEXT, source_kind TEXT, sample_at TEXT, profile TEXT, signals_json TEXT)")
        connection.execute("INSERT INTO meta_learning_samples VALUES (?, ?, ?, ?, ?)",
            ("XRPUSDT", "live", "2026-10-26T00:00:00+00:00", "RAPIDO",
             json.dumps({"signals_used": [{"name": "direction", "value": .7,
                 "normalized": 1.0}]})))
    before = database.read_bytes()
    diagnose(database, days=36500)
    output = capsys.readouterr().out
    assert "mode=ro" in output
    assert "probability_up de model_a_down = P(baja)" in output
    assert "cobertura 1/1" in output
    assert database.read_bytes() == before


def test_diagnostic_counts_normalized_signal_without_raw_value_and_versions(tmp_path, capsys):
    database = tmp_path / "diagnostic-normalized.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE meta_learning_samples "
            "(symbol TEXT, source_kind TEXT, sample_at TEXT, profile TEXT, signals_json TEXT)")
        for version, signals in ((1, [{"name": "direction", "normalized": 0.0}]),
                                 (2, [{"name": "direction", "normalized": -0.5}])):
            connection.execute("INSERT INTO meta_learning_samples VALUES (?, ?, ?, ?, ?)",
                ("XRPUSDT", "live", "2026-10-26T00:00:00+00:00", "RAPIDO",
                 json.dumps({"signals_version": version, "signals_used": signals})))
    diagnose(database, days=36500)
    output = capsys.readouterr().out
    assert "v1=1, v2=1" in output
    assert "cobertura 2/2" in output
    assert "v1 1/1; v2 1/1" in output
    assert "valores normalizados distintos: -0.5, 0" in output


def test_diagnostic_reads_touch_omissions_from_ledger_v1_and_v2(tmp_path, capsys):
    database = tmp_path / "diagnostic-ledger-schemas.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE meta_learning_samples "
            "(symbol TEXT, source_kind TEXT, sample_at TEXT, profile TEXT, signals_json TEXT)")
        connection.execute("CREATE TABLE meta_goals (id INTEGER PRIMARY KEY, profile TEXT)")
        connection.execute("CREATE TABLE meta_ledger "
            "(id INTEGER PRIMARY KEY, meta_id INTEGER, ts TEXT, event_type TEXT, details TEXT)")
        connection.execute("INSERT INTO meta_goals VALUES (1, 'RAPIDO')")
        for version in (None, 2):
            details = {"decision_id": f"plan-{version}", "alternatives": [{"symbol": "XRPUSDT",
                "signals_omitted": [{"name": "touch_buy_to_sell", "reason": "velas no disponibles"}]}]}
            if version is not None:
                details["ledger_schema_version"] = version
                details["advisor_comparisons"] = []
            connection.execute("INSERT INTO meta_ledger (meta_id, ts, event_type, details) VALUES (?, ?, ?, ?)",
                (1, "2026-10-26T00:00:00+00:00", "META_PLAN_EVALUATED", json.dumps(details)))
    diagnose(database, days=36500, profile="RAPIDO")
    output = capsys.readouterr().out
    assert "XRPUSDT: 2 alternativas — velas no disponibles" in output
