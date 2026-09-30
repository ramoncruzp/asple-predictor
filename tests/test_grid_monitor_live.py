"""Real Testnet mechanics checks. Testnet commission is zero; fee deductions are not tested."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from config.settings import Settings
from data.exchange_filters import SymbolFilters
from data.testnet_client import TestnetClient as BinanceTestnetClient
from database.db_manager import DBManager
from grid.engine import GridEngine
from grid.monitor import GridMonitor


def _safe_client(client: BinanceTestnetClient) -> BinanceTestnetClient:
    """Suppress transport exception reprs, which can contain signed request headers."""
    for name in (
        "get_balance", "get_open_orders", "get_avg_price", "get_symbol_info",
        "get_book_ticker", "get_order", "cancel_order", "place_order", "get_my_trades",
        "find_order_by_client_id",
    ):
        original = getattr(client, name)

        def safe_call(*args, _original=original, _name=name, **kwargs):
            try:
                return _original(*args, **kwargs)
            except Exception as exc:
                raise RuntimeError(f"Binance Testnet {_name} failed ({type(exc).__name__})") from None

        setattr(client, name, safe_call)
    return client


def _balance(client: BinanceTestnetClient, asset: str) -> tuple[Decimal, Decimal]:
    row = client.get_balance(asset).get(asset, {"free": 0.0, "locked": 0.0})
    return Decimal(str(row["free"])), Decimal(str(row["locked"]))


def _context():
    settings = Settings()
    key, secret = (settings.testnet_api_key or "").strip(), (settings.testnet_api_secret or "").strip()
    if not key or not secret or key.casefold().startswith("tu_") or secret.casefold().startswith("tu_"):
        pytest.skip("faltan credenciales de Testnet")
    try:
        client = _safe_client(BinanceTestnetClient(key, secret, production_api_key=settings.binance_api_key))
    except Exception as exc:
        pytest.fail(f"Binance Testnet client initialization failed ({type(exc).__name__})", pytrace=False)
    db = DBManager("sqlite:///:memory:")
    db.add_or_reactivate_coin("XRPUSDT")
    # Binance keeps client-order IDs in account history after a test finishes.
    # Seed a unique local ID so independent in-memory DBs never reuse a live CID.
    unique_floor = int(time.time() * 1000)
    with db.engine.begin() as conn:
        conn.execute(db.grids.insert().values(
            id=unique_floor, symbol="XRPUSDT", range_low=1.0, range_high=2.0,
            n_levels=1, capital_total=0.0, status="CLOSED",
            created_at=datetime.now(timezone.utc).replace(tzinfo=None), environment="testnet",
        ))
    live_settings = SimpleNamespace(
        usdt_por_grid=120.0, max_grids_simultaneos=5,
        capital_max_por_nivel_pct=0.30, grid_min_step_pct=0.003,
        grid_monitor_interval=900, grid_monitor_gap_minutes=20,
        grid_monitor_enabled=True,
    )
    engine = GridEngine(db, client, live_settings)
    baseline_free, baseline_locked = _balance(client, "XRP")
    baseline_usdt, _ = _balance(client, "USDT")
    baseline_open = {int(order["order_id"]) for order in client.get_open_orders("XRPUSDT")}
    return {
        "settings": live_settings, "client": client, "db": db, "engine": engine,
        "baseline_free": baseline_free, "baseline_total": baseline_free + baseline_locked,
        "baseline_usdt": baseline_usdt,
        "baseline_open": baseline_open, "grid": None, "repository_id": None,
    }


def _create_and_fill_one_buy(ctx):
    client, db, engine = ctx["client"], ctx["db"], ctx["engine"]
    avg = client.get_avg_price("XRPUSDT")
    filters = SymbolFilters.from_symbol_info(client.get_symbol_info("XRPUSDT"))
    low = filters.round_price(avg * Decimal("0.985"), "up")
    high = filters.round_price(avg * Decimal("1.105"), "up")
    grid = engine.create_grid("XRPUSDT", low, high, 4, capital=Decimal("120"))
    ctx["grid"] = grid
    candidates = [row for row in db.get_grid_levels(grid["id"]) if row["state"] == "BUY_OPEN"]
    assert candidates, "grid must place at least one resting test buy"
    level = max(candidates, key=lambda row: Decimal(str(row["price"])))
    old_order = client.get_order("XRPUSDT", order_id=level["order_id"])
    canceled = client.cancel_order("XRPUSDT", level["order_id"])
    assert canceled["status"] == "CANCELED"
    book = client.get_book_ticker("XRPUSDT")
    crossing_price = filters.round_price(book["ask_price"] + filters.tick_size * 2, "up")
    assert crossing_price < Decimal(str(level["sell_price"])), "test buy price must remain below its sell level"
    replacement = client.place_order(
        "XRPUSDT", "BUY", Decimal(str(old_order["quantity"])), crossing_price,
        client_order_id=level["client_order_id"],
    )
    replacement_id = int(replacement["order_id"])
    ctx["buy_order_id"] = replacement_id
    db.update_level(grid["id"], level["level_idx"], state="BUY_OPEN", order_id=replacement_id)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        order = client.get_order("XRPUSDT", order_id=replacement_id)
        if order["status"] == "FILLED":
            ctx["bought_qty"] = Decimal(str(order["executed_qty"]))
            ctx["buy_quote"] = Decimal(str(order["cummulative_quote_qty"]))
            ctx["level_idx"] = int(level["level_idx"])
            return grid
        time.sleep(2)
    raise AssertionError("marketable test LIMIT BUY was not FILLED before the 45-second deadline")


def _assert_clean(ctx):
    client, db, engine = ctx["client"], ctx["db"], ctx["engine"]
    grid = ctx.get("grid")
    try:
        if grid is not None:
            current = db.get_grid(int(grid["id"]))
            if current and current["status"] in {"ACTIVE", "PAUSED", "CLOSING"}:
                for attempt in range(2):
                    try:
                        engine.close_grid(int(grid["id"]), "liquidate")
                        break
                    except Exception as exc:
                        if attempt:
                            raise RuntimeError(f"Testnet grid cleanup failed ({type(exc).__name__})") from None
            repo_id = ctx.get("repository_id")
            if repo_id is not None and db.get_grid(repo_id) and db.get_grid(repo_id)["status"] == "HOLDING":
                for level in db.get_grid_levels(repo_id):
                    if level.get("order_id") is not None:
                        try:
                            client.cancel_order("XRPUSDT", level["order_id"])
                        except Exception:
                            pass
                    held = Decimal(str(level.get("held_qty") or 0))
                    free, locked = _balance(client, "XRP")
                    delta = max(Decimal(0), free + locked - ctx["baseline_total"])
                    qty = SymbolFilters.from_symbol_info(client.get_symbol_info("XRPUSDT")).round_qty_down(min(held, delta))
                    if qty > 0:
                        sold = client.place_order(
                            "XRPUSDT", "SELL", qty, order_type="MARKET",
                            client_order_id=f"g{repo_id}L{level['level_idx']}X{level['cycles_completed']}",
                        )
                        db.update_level(repo_id, level["level_idx"], state="SELL_OPEN", order_id=sold["order_id"])
                        engine.sync_repository(repo_id)

        # Last-resort cleanup uses only the base-asset increase observed since
        # this test's own baseline; it never estimates from account total alone.
        free, locked = _balance(client, "XRP")
        excess = max(Decimal(0), free + locked - ctx["baseline_total"])
        filters = SymbolFilters.from_symbol_info(client.get_symbol_info("XRPUSDT"))
        cleanup_qty = filters.round_qty_down(excess)
        if cleanup_qty >= filters.min_qty:
            cleanup = client.place_order(
                "XRPUSDT", "SELL", cleanup_qty, order_type="MARKET",
                client_order_id=f"grid15Aclean{int(time.time())}",
            )
            cleanup = client.get_order("XRPUSDT", order_id=cleanup["order_id"])
            if cleanup["status"] != "FILLED":
                raise RuntimeError("delta-only market cleanup did not fill")

        final_orders = {int(order["order_id"]) for order in client.get_open_orders("XRPUSDT")}
        assert final_orders == ctx["baseline_open"], "Testnet cleanup left non-baseline XRPUSDT orders"
        free, locked = _balance(client, "XRP")
        final_total = free + locked
        assert abs(final_total - ctx["baseline_total"]) <= Decimal("0.00000001"), (
            f"Testnet XRP inventory differs from baseline: baseline={ctx['baseline_total']} final={final_total}"
        )
        print(f"TESTNET CLEANUP XRP baseline={ctx['baseline_total']} final={final_total}; open_orders={len(final_orders)}")
    except Exception as exc:
        pytest.fail(f"Testnet cleanup or verification failed ({type(exc).__name__})", pytrace=False)


def test_live_l4_binance_monitor_records_real_filled_buy_and_snapshot():
    """Testnet commission is zero; this does not validate fee deductions."""
    ctx = _context()
    try:
        grid = _create_and_fill_one_buy(ctx)
        monitor = GridMonitor(ctx["db"], ctx["client"], ctx["engine"], ctx["settings"])
        run = monitor.run_once("SCHEDULED")
        assert run["status"] in {"OK", "PARTIAL"}
        assert ctx["db"].get_monitor_run(run["run_id"])["finished_at"] is not None
        events = ctx["db"].list_grid_events(grid_id=grid["id"])
        buy_filled = next(event for event in events if event["event_type"] == "BUY_FILLED")
        sell_placed = next(event for event in events if event["event_type"] == "SELL_PLACED")
        assert buy_filled["run_id"] == sell_placed["run_id"] == run["run_id"]
        assert buy_filled["level_idx"] == sell_placed["level_idx"] == ctx["level_idx"]
        assert buy_filled["order_id"] == ctx["buy_order_id"]
        snapshots = ctx["db"].list_grid_snapshots(grid_id=grid["id"], run_id=run["run_id"])
        cell = next(row for row in snapshots if row["level_idx"] == ctx["level_idx"])
        assert cell["held_qty"] > 0 and cell["market_mid"] is not None
        free, locked = _balance(ctx["client"], "XRP")
        print(
            f"LIVE L4 grid={grid['id']} buy_order={ctx['buy_order_id']} bought_qty={ctx['bought_qty']} "
            f"xrp_free={free} xrp_locked={locked} monitor_run={run['run_id']} snapshots={len(snapshots)}"
        )
    finally:
        _assert_clean(ctx)


def test_live_l5_binance_liquidate_sells_only_grid_inventory():
    """Testnet commission is zero; this does not validate fee deductions."""
    ctx = _context()
    try:
        grid = _create_and_fill_one_buy(ctx)
        result = ctx["engine"].close_grid(grid["id"], "liquidate")
        print(
            f"LIVE L5 CLOSE status={result['status']} liquidated={result['liquidated_cells']} "
            f"level_states={[(row['level_idx'], row['state'], row['held_qty']) for row in ctx['db'].get_grid_levels(grid['id'])]}"
        )
        assert result["status"] == "CLOSED"
        assert not ctx["client"].get_open_orders("XRPUSDT")
        market_sells = result["liquidated_cells"]
        assert market_sells
        sold = ctx["client"].get_order("XRPUSDT", order_id=market_sells[0]["order_id"])
        after_usdt, _ = _balance(ctx["client"], "USDT")
        after_free, after_locked = _balance(ctx["client"], "XRP")
        assert abs((after_free + after_locked) - ctx["baseline_total"]) <= Decimal("0.00000001")
        measured_pnl = (after_usdt - ctx["baseline_usdt"]) 
        engine_pnl = Decimal(str(result["pnl_realized"]))
        assert abs(measured_pnl - engine_pnl) <= Decimal("0.02"), (
            f"USDT delta PnL {measured_pnl} differs from engine {engine_pnl} beyond 0.02 USDT"
        )
        assert all(row["state"] == "DONE" for row in ctx["db"].get_grid_levels(grid["id"]))
        print(
            f"LIVE L5 grid={grid['id']} buy_qty={ctx['bought_qty']} sell_qty={sold['executed_qty']} "
            f"xrp_final={after_free + after_locked} measured_pnl_usdt={measured_pnl} engine_pnl_usdt={engine_pnl}"
        )
    finally:
        _assert_clean(ctx)


def test_live_l6_binance_repository_keeps_and_finishes_owned_sell():
    """Testnet commission is zero; this does not validate fee deductions."""
    ctx = _context()
    try:
        grid = _create_and_fill_one_buy(ctx)
        result = ctx["engine"].close_grid(grid["id"], "repository")
        assert result["status"] == "CLOSED" and result["moved_cells"]
        repository_id = int(result["repository_grid_id"])
        ctx["repository_id"] = repository_id
        moved = next(row for row in ctx["db"].get_grid_levels(repository_id) if row["state"] == "SELL_OPEN")
        old_order_id, old_cid = moved["order_id"], moved["client_order_id"]
        live_before = ctx["client"].get_open_orders("XRPUSDT")
        assert any(int(row["order_id"]) == int(old_order_id) for row in live_before)
        create_calls_before = len(ctx["db"].get_grid_levels(repository_id))
        sync_result = ctx["engine"].sync_repository(repository_id)
        moved_again = next(row for row in ctx["db"].get_grid_levels(repository_id) if row["level_idx"] == moved["level_idx"])
        assert moved_again["order_id"] == old_order_id and moved_again["client_order_id"] == old_cid
        assert moved_again["state"] == "SELL_OPEN"
        assert len(ctx["db"].get_grid_levels(repository_id)) == create_calls_before
        assert sync_result["errors"] == 0

        canceled = ctx["client"].cancel_order("XRPUSDT", old_order_id)
        assert canceled["status"] == "CANCELED"
        available, _ = _balance(ctx["client"], "XRP")
        delta = max(Decimal(0), available - ctx["baseline_free"])
        qty = SymbolFilters.from_symbol_info(ctx["client"].get_symbol_info("XRPUSDT")).round_qty_down(
            min(Decimal(str(moved["held_qty"])), delta)
        )
        assert qty >= SymbolFilters.from_symbol_info(ctx["client"].get_symbol_info("XRPUSDT")).min_qty
        market = ctx["client"].place_order(
            "XRPUSDT", "SELL", qty, order_type="MARKET",
            client_order_id=f"g{repository_id}L{moved['level_idx']}X{moved['cycles_completed']}",
        )
        market = ctx["client"].get_order("XRPUSDT", order_id=market["order_id"])
        ctx["db"].update_level(repository_id, moved["level_idx"], order_id=market["order_id"])
        closed = ctx["engine"].sync_repository(repository_id)
        final_level = next(row for row in ctx["db"].get_grid_levels(repository_id) if row["level_idx"] == moved["level_idx"])
        assert final_level["state"] == "DONE" and final_level["held_qty"] == 0
        assert ctx["db"].get_grid(repository_id)["status"] == "CLOSED"
        assert closed["cycles_completed"] == 1
        print(
            f"LIVE L6 grid={grid['id']} repository={repository_id} kept_sell_order={old_order_id} "
            f"client_order_id={old_cid} cleanup_market_order={market['order_id']} sold_qty={market['executed_qty']} "
            f"repository_status={ctx['db'].get_grid(repository_id)['status']}"
        )
    finally:
        _assert_clean(ctx)
