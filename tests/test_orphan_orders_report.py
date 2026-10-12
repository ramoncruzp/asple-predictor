from __future__ import annotations

import ast
import hashlib
from pathlib import Path

from scripts.orphan_orders_report import classify_open_order, classify_open_orders


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
    namespace = {"__name__": "orphan_orders_report_mutation"}
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
