from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

from tests.test_grid_engine import create, make_engine


ROOT = Path(__file__).parents[1]


def _load_mutation(path: Path, old: str, new: str, module_name: str):
    original = path.read_bytes()
    original_sha = hashlib.sha256(original).hexdigest()
    source = path.read_text(encoding="utf-8")
    mutated, count = source.replace(old, new, 1), source.count(old)
    assert count == 1
    mutated_bytes = mutated.encode("utf-8")
    mutated_sha = hashlib.sha256(mutated_bytes).hexdigest()
    assert mutated_sha != original_sha
    module = ModuleType(module_name)
    module.__file__ = str(path)
    module.__package__ = module_name.rpartition(".")[0]
    sys.modules[module_name] = module
    try:
        exec(compile(mutated, str(path), "exec"), module.__dict__)
    finally:
        sys.modules.pop(module_name, None)
    restored_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    assert restored_sha == original_sha
    return module


def test_mutation_ignoring_liquidate_pending_order_reproduces_bad_close():
    path = ROOT / "grid" / "engine.py"
    old = (
        "pending_order_ids = {\n"
        "                int(order_id) for order_id in cancellation.get(\"pending_order_ids\", [])\n"
        "                if order_id is not None\n"
        "            }"
    )
    mutant = _load_mutation(path, old, "pending_order_ids = set()",
                            "grid._phase28b_engine_liquidate_mutation")
    base, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(base)
    pending_id = int(db.get_grid_levels(grid["id"])[0]["order_id"])
    engine = mutant.GridEngine(db, exchange, base.settings)
    engine.cancel_grid_orders = lambda *_args, **_kwargs: {
        "status": "CLOSING", "canceled_order_ids": [], "cancel_errors": [],
        "pending_order_ids": [pending_id], "remaining_inventory": [],
    }

    result = engine.close_grid(grid["id"], "liquidate")

    assert result["status"] == "CLOSED"
    assert db.get_grid_levels(grid["id"])[0]["state"] == "DONE"


def test_mutation_skipping_repository_verification_leaves_live_buy_unnoticed():
    path = ROOT / "grid" / "engine.py"
    old = "if belongs_to_grid and is_grid_buy:"
    mutant = _load_mutation(path, old, "if False:", "grid._phase28b_engine_repository_mutation")
    base, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(base)
    order = exchange.place_order("XRPUSDT", "BUY", "0.1", "95",
                                 client_order_id=f"g{grid['id']}L3B0")
    engine = mutant.GridEngine(db, exchange, base.settings)

    result = engine._close_repository(grid["id"])

    assert result["status"] == "CLOSED"
    assert exchange.get_order("XRPUSDT", order_id=order["order_id"])["status"] == "NEW"


def test_mutation_preserving_decimal_zeros_breaks_dust_message():
    path = ROOT / "alerts" / "formatters.py"
    old = 'return format(amount.normalize(), "f")'
    mutant = _load_mutation(path, old, 'return format(amount, "f")',
                            "alerts._phase28b_formatters_mutation")
    event = {
        "event_type": "DUST_RECONCILIATION", "reason": "drift",
        "details": {"asset": "GRAM", "drift": "4.75000000",
                    "drift_usdt": "6.69", "diff": "28.0600000"},
    }

    message = mutant.format_operational(event, None)

    assert "Deriva: +4.75000000 GRAM" in message


def test_mutation_sharing_dust_cooldown_suppresses_other_asset():
    path = ROOT / "alerts" / "dispatcher.py"
    old = 'alert_key = f"{event_type}:{asset or \'none\'}"'
    new = 'alert_key = f"{event_type}:{grid_id if grid_id is not None else \'none\'}"'
    mutant = _load_mutation(path, old, new, "alerts._phase28b_dispatcher_mutation")
    _base, db, _exchange = make_engine(fee_rate="0", fee_asset="USDT")
    sent = []

    class Sender:
        def send(self, text):
            sent.append(text)
            return {"ok": True, "error": None}

    sender = Sender()
    dispatcher = mutant.AlertDispatcher(db, lambda: None, sender_factory=lambda *_: sender,
                                        probe=lambda: True, sleep=lambda _seconds: None)
    config = type("Config", (), {"alerts_cooldown_min": 60, "alerts_daily_cap": 30})()
    now = __import__("datetime").datetime(2026, 10, 12, tzinfo=__import__("datetime").timezone.utc)
    for event_id, asset in enumerate(("GRAM", "XRP"), 1):
        dispatcher._maybe_send_operational(sender, config, {
            "id": event_id, "event_type": "DUST_RECONCILIATION", "grid_id": None,
            "reason": "drift", "details": {"asset": asset},
        }, now)

    assert len(sent) == 1


def test_mutation_weakening_symbol_parser_accepts_malformed_pair():
    path = ROOT / "scripts" / "orphan_orders_report.py"
    old = '_SYMBOL = re.compile(r"[A-Z0-9]{6,20}")'
    mutant = _load_mutation(path, old, '_SYMBOL = re.compile(r".+")',
                            "scripts._phase28b_orphan_report_mutation")

    assert mutant.parse_symbols("ada-usdt") == ["ada-usdt"]


def test_mutation_removing_repository_root_breaks_import_from_other_cwd():
    path = ROOT / "scripts" / "orphan_orders_report.py"
    original = path.read_bytes()
    original_sha = hashlib.sha256(original).hexdigest()
    source = path.read_text(encoding="utf-8")
    old, new = "sys.path.insert(0, ROOT_STRING)", "sys.path.insert(0, str(Path.cwd()))"
    mutated, count = source.replace(old, new, 1), source.count(old)
    assert count == 1
    mutated_sha = hashlib.sha256(mutated.encode("utf-8")).hexdigest()
    assert mutated_sha != original_sha
    child_code = (
        "namespace={'__name__':'orphan_report_root_mutation','__file__':" + repr(str(path)) + "}; "
        "exec(compile(" + repr(mutated) + "," + repr(str(path)) + ",'exec'),namespace); "
        "import config.settings; import data.testnet_client"
    )
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-c", child_code], cwd=ROOT / "tests", env=environment,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0
    assert "No module named 'config'" in result.stderr
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original_sha


def test_mutation_dropping_missing_module_name_hides_import_diagnostic():
    path = ROOT / "scripts" / "orphan_orders_report.py"
    old = 'return f"ModuleNotFoundError (módulo faltante: {missing})"'
    mutant = _load_mutation(path, old, 'return "ModuleNotFoundError"',
                            "scripts._phase28b_missing_module_mutation")

    assert mutant._safe_error_label(ModuleNotFoundError("missing", name="missing_pkg")) == "ModuleNotFoundError"


def test_mutation_printing_api_exception_message_exposes_signed_details():
    path = ROOT / "scripts" / "orphan_orders_report.py"
    old = 'return f"BinanceAPIException (code={code})"'
    mutant = _load_mutation(path, old, "return str(exc)",
                            "scripts._phase28b_api_error_mutation")
    api_exception = type("BinanceAPIException", (Exception,), {"code": -2013})

    label = mutant._safe_error_label(api_exception("signed URL private details"))

    assert "signed URL" in label


def test_mutation_removing_order_id_owner_match_loses_adjustment_grid():
    path = ROOT / "scripts" / "orphan_orders_report.py"
    old = '(order_id is not None and row.get("order_id") is not None'
    mutant = _load_mutation(path, old, '(False and row.get("order_id") is not None',
                            "scripts._phase28c_order_id_mutation")
    result = mutant.classify_open_order(
        {"symbol": "GRAMUSDT", "client_order_id": "gA" + "a" * 30, "order_id": 279464},
        {2: {"status": "ACTIVE", "symbol": "GRAMUSDT"}},
        {(2, 0): [{"order_id": 279464, "client_order_id": "old-cid"}]},
    )

    assert result["classification"] == "PROPIA_SIN_REGISTRO"


def test_mutation_removing_loan_cid_recognition_loses_registered_loan():
    path = ROOT / "scripts" / "orphan_orders_report.py"
    old = 'is_loan_id = _SYSTEM_LOAN_CLIENT_ID.fullmatch(client_id) is not None'
    mutant = _load_mutation(path, old, "is_loan_id = False",
                            "scripts._phase28c_loan_id_mutation")
    cid = "gL" + "a" * 30
    result = mutant.classify_open_order(
        {"symbol": "GRAMUSDT", "client_order_id": cid, "order_id": 256913},
        {2: {"status": "ACTIVE", "symbol": "GRAMUSDT"}}, {},
        loans={cid: [{"grid_id": 2}]},
    )

    assert result["classification"] == "DESCONOCIDA"
