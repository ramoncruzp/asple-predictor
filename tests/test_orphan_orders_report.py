from __future__ import annotations

import ast
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from scripts.orphan_orders_report import (
    _read_database, _safe_error_label, classify_open_order, classify_open_orders, parse_symbols,
)


def _snapshot(status: str = "ACTIVE", symbol: str = "GRAMUSDT"):
    grids = {3: {"status": status, "symbol": symbol}}
    levels = {(3, 2): [{
        "order_id": 29771,
        "client_order_id": "g3L2B0",
        "buy_client_order_id": None,
    }]}
    return grids, levels


def test_active_grid_order_referenced_by_client_id_is_ok():
    grids, levels = _snapshot()
    result = classify_open_order({"symbol": "GRAMUSDT", "client_order_id": "g3L2B0",
                                  "order_id": 29771, "side": "BUY"}, grids, levels)
    assert result["classification"] == "OK"
    assert (result["grid_id"], result["level_idx"], result["side"]) == (3, 2, "B")


def test_closed_grid_order_is_orphaned():
    grids, levels = _snapshot("CLOSED")
    result = classify_open_order({"symbol": "GRAMUSDT", "client_order_id": "g3L2B0",
                                  "order_id": 29771}, grids, levels)
    assert result["classification"] == "HUÉRFANA"


def test_failed_grid_order_is_orphaned():
    grids, levels = _snapshot("FAILED")
    result = classify_open_order({"symbol": "GRAMUSDT", "client_order_id": "g3L2B0",
                                  "order_id": 29771}, grids, levels)
    assert result["classification"] == "HUÉRFANA"


def test_unrelated_client_id_is_unknown():
    grids, levels = _snapshot()
    result = classify_open_order({"symbol": "GRAMUSDT", "client_order_id": "manual-order"}, grids, levels)
    assert result["classification"] == "DESCONOCIDA"


def test_order_reference_can_match_by_exchange_order_id():
    grids, levels = _snapshot()
    levels[(3, 2)][0]["client_order_id"] = "old-cycle-id"
    result = classify_open_order({"symbol": "GRAMUSDT", "client_order_id": "g3L2B1",
                                  "order_id": 29771}, grids, levels)
    assert result["classification"] == "OK"


def test_same_order_id_on_another_symbol_is_orphaned():
    grids, levels = _snapshot(symbol="OTHERUSDT")
    result = classify_open_order({"symbol": "GRAMUSDT", "client_order_id": "g3L2B0",
                                  "order_id": 29771}, grids, levels)
    assert result["classification"] == "HUÉRFANA"


def test_empty_open_order_inventory_is_empty():
    grids, levels = _snapshot()
    assert classify_open_orders([], grids, levels) == []


def test_grid_closed_mutation_would_invert_classification_rule():
    source_path = Path(__file__).parents[1] / "scripts" / "orphan_orders_report.py"
    original = source_path.read_bytes()
    original_sha = hashlib.sha256(original).hexdigest()
    source = original.decode("utf-8")
    rule = 'if grid and str(grid.get("status") or "").upper() == "ACTIVE" and referenced and same_symbol:'
    mutated, count = source.replace(rule, rule.replace('== "ACTIVE"', '!= "ACTIVE"'), 1), source.count(rule)
    assert count == 1
    mutated_bytes = mutated.encode("utf-8")
    mutated_sha = hashlib.sha256(mutated_bytes).hexdigest()
    assert mutated_sha != original_sha
    namespace = {"__name__": "orphan_orders_report_mutation", "__file__": str(source_path)}
    exec(compile(mutated, str(source_path), "exec"), namespace)
    grids, levels = _snapshot("CLOSED")
    mutant_result = namespace["classify_open_order"](
        {"symbol": "GRAMUSDT", "client_order_id": "g3L2B0", "order_id": 29771}, grids, levels)
    assert mutant_result["classification"] == "OK"
    restored_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
    assert restored_sha == original_sha


def test_report_script_has_no_order_mutation_imports_or_calls():
    source_path = Path(__file__).parents[1] / "scripts" / "orphan_orders_report.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    forbidden = {"cancel_order", "new_order"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imported = {alias.name.rsplit(".", 1)[-1] for alias in node.names}
            assert not imported.intersection(forbidden)
        if isinstance(node, ast.Call):
            called = node.func.attr if isinstance(node.func, ast.Attribute) else (
                node.func.id if isinstance(node.func, ast.Name) else "")
            assert called not in forbidden


def test_parse_symbols_accepts_uppercase_pairs_and_deduplicates():
    assert parse_symbols("ADAUSDT,BTCUSDT,ADAUSDT") == ["ADAUSDT", "BTCUSDT"]


@pytest.mark.parametrize("value", ["adausdt", "", "  ", "ADAUSDT,,BTCUSDT", "ADA-USDT", "ADA/USDT"])
def test_parse_symbols_rejects_lowercase_empty_and_malformed_values(value):
    with pytest.raises(argparse.ArgumentTypeError):
        parse_symbols(value)


def test_safe_error_label_reports_missing_module_and_numeric_api_code_only():
    missing = ModuleNotFoundError("private detail", name="missing_pkg")
    assert _safe_error_label(missing) == "ModuleNotFoundError (módulo faltante: missing_pkg)"

    api_exception = type("BinanceAPIException", (Exception,), {"code": -2013})
    error = api_exception("signed URL and private details")
    label = _safe_error_label(error)
    assert label == "BinanceAPIException (code=-2013)"
    assert "signed" not in label and "private" not in label


def test_script_imports_project_modules_from_another_cwd_without_pythonpath():
    script = Path(__file__).parents[1] / "scripts" / "orphan_orders_report.py"
    code = (
        "import runpy,sys; "
        "runpy.run_path(sys.argv[1], run_name='orphan_report_import_test'); "
        "import config.settings; import data.testnet_client"
    )
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-c", code, str(script)], cwd=Path(__file__).parents[1] / "tests", env=environment,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_adjustment_client_id_resolves_owner_by_order_id():
    grids, levels = _snapshot()
    levels[(3, 2)][0]["order_id"] = 279464
    levels[(3, 2)][0]["client_order_id"] = "old-cycle"
    result = classify_open_order(
        {"symbol": "GRAMUSDT", "client_order_id": "gA" + "a" * 30, "order_id": 279464},
        grids, levels,
    )
    assert result["classification"] == "OK"
    assert result["grid_id"] == 3


def test_loan_client_id_is_distinct_from_legacy_pattern_and_resolves_loan_owner():
    cid = "gL" + "60baa" + "a" * 25
    assert len(cid) == 32
    assert classify_open_order({"client_order_id": cid}, {}, {})["classification"] == "PROPIA_SIN_REGISTRO"
    result = classify_open_order(
        {"symbol": "GRAMUSDT", "client_order_id": cid, "order_id": 256913},
        {2: {"status": "ACTIVE", "symbol": "GRAMUSDT"}}, {},
        loans={cid: [{"grid_id": 2}]},
    )
    assert result["classification"] == "OK"
    assert result["grid_id"] == 2


def test_closed_loan_is_orphaned_and_adjustment_without_record_is_own_unregistered():
    cid = "gL" + "b" * 30
    grids = {2: {"status": "CLOSED", "symbol": "GRAMUSDT"}}
    loan_result = classify_open_order(
        {"symbol": "GRAMUSDT", "client_order_id": cid, "order_id": 256913},
        grids, {}, loans={cid: [{"grid_id": 2}]},
    )
    own_result = classify_open_order(
        {"symbol": "GRAMUSDT", "client_order_id": "gA" + "c" * 30}, grids, {},
    )
    assert loan_result["classification"] == "HUÉRFANA"
    assert own_result["classification"] == "PROPIA_SIN_REGISTRO"


def test_legacy_client_id_for_missing_grid_remains_orphaned_and_manual_is_unknown():
    legacy = classify_open_order({"symbol": "GRAMUSDT", "client_order_id": "g6L0B0"}, {}, {})
    manual = classify_open_order({"symbol": "GRAMUSDT", "client_order_id": "web_123"}, {}, {})
    ambiguous = classify_open_order({"symbol": "GRAMUSDT", "client_order_id": "gL60baa" + "0" * 25}, {}, {})
    assert legacy["classification"] == "HUÉRFANA"
    assert manual["classification"] == "DESCONOCIDA"
    assert ambiguous["classification"] == "PROPIA_SIN_REGISTRO"


def test_read_database_loads_loan_cids_by_column_name_and_ignores_bad_details(tmp_path):
    path = tmp_path / "loans.db"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE grids (id INTEGER, symbol TEXT, status TEXT);
        INSERT INTO grids VALUES (2, 'GRAMUSDT', 'ACTIVE');
        CREATE TABLE grid_levels (
            grid_id INTEGER, level_idx INTEGER, order_id INTEGER,
            client_order_id TEXT, buy_client_order_id TEXT
        );
        INSERT INTO grid_levels VALUES (2, 0, 279464, 'gAaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', NULL);
        CREATE TABLE grid_loans (details TEXT, status TEXT, id INTEGER, grid_id INTEGER);
        INSERT INTO grid_loans VALUES ('{broken', 'PENDING', 2, 2);
        INSERT INTO grid_loans VALUES ('{}', 'PENDING', 3, 2);
    """)
    connection.execute(
        "INSERT INTO grid_loans VALUES (?, 'PENDING', 1, 2)",
        (json.dumps({"lender_replacement_cid": "gL" + "d" * 30,
                     "borrower_replacement_cid": "gL" + "e" * 30}),),
    )
    connection.commit()
    connection.close()

    grids, levels, symbols, loans = _read_database(path)

    assert grids[2]["status"] == "ACTIVE"
    assert levels[(2, 0)][0]["order_id"] == 279464
    assert symbols == ["GRAMUSDT"]
    assert loans["gL" + "d" * 30] == [{"grid_id": 2, "status": "PENDING"}]
    assert loans["gL" + "e" * 30] == [{"grid_id": 2, "status": "PENDING"}]


def test_real_incident_ids_classify_from_fake_snapshot():
    grids = {2: {"status": "ACTIVE", "symbol": "GRAMUSDT"}}
    adjustment_ids = ["gA" + format(value, "030x") for value in range(279464, 279468)]
    levels = {(2, idx): [{"order_id": order_id, "client_order_id": cid,
                          "buy_client_order_id": None}]
              for idx, (order_id, cid) in enumerate(zip(range(279464, 279468), adjustment_ids))}
    loan_cid = "gL60baa" + "a" * 25
    loans = {loan_cid: [{"grid_id": 2, "status": "PENDING"}]}
    orders = [{"symbol": "GRAMUSDT", "order_id": order_id, "client_order_id": cid}
              for order_id, cid in zip(range(279464, 279468), adjustment_ids)]
    orders.append({"symbol": "GRAMUSDT", "order_id": 256913, "client_order_id": loan_cid})

    assert [row["classification"] for row in classify_open_orders(orders, grids, levels, loans=loans)] == [
        "OK", "OK", "OK", "OK", "OK",
    ]
