from database.db_manager import DBManager
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier


def test_merge_grid_params_preserves_unrelated_live_counters(tmp_path):
    db = DBManager(f"sqlite:///{tmp_path}/params.db")
    grid = db.create_grid_with_levels({"symbol": "XRPUSDT", "range_low": 90, "range_high": 110,
        "n_levels": 2, "capital_total": 100, "status": "ACTIVE",
        "params": {"dust_cash_proceeds": "1.25", "dust_sweep_seq": 4,
                    "target_close_plan": {"phase": "STARTED"}, "max_days": 30}},
        [{"level_idx": 0, "price": 90, "sell_price": 91, "capital": 50},
         {"level_idx": 1, "price": 100, "sell_price": 101, "capital": 50}])
    begin_calls = []
    original_begin = db.engine.begin
    def counted_begin():
        begin_calls.append(True)
        return original_begin()
    db.engine.begin = counted_begin
    merged = db.merge_grid_params(grid["id"], {"max_days": 45, "target_pct": 2},
        remove={"target_usdt"}, allowed=frozenset({"max_days", "target_pct", "target_usdt"}))
    assert len(begin_calls) == 1
    assert merged["max_days"] == 45
    assert merged["dust_cash_proceeds"] == "1.25"
    assert merged["dust_sweep_seq"] == 4
    assert merged["target_close_plan"] == {"phase": "STARTED"}
    assert "target_usdt" not in merged


def test_merge_grid_params_rejects_non_whitelisted_keys(tmp_path):
    db = DBManager(f"sqlite:///{tmp_path}/params-reject.db")
    grid = db.create_grid_with_levels({"symbol": "XRPUSDT", "range_low": 90, "range_high": 110,
        "n_levels": 2, "capital_total": 100},
        [{"level_idx": 0, "price": 90, "sell_price": 91, "capital": 50}])
    try:
        db.merge_grid_params(grid["id"], {"dust_sweep_seq": 99}, allowed=frozenset({"max_days"}))
    except ValueError:
        pass
    else:
        raise AssertionError("non-whitelisted key was accepted")


def test_merge_grid_params_preserves_second_connection_write(tmp_path):
    db = DBManager(f"sqlite:///{tmp_path}/params-concurrent.db")
    grid = db.create_grid_with_levels({"symbol": "XRPUSDT", "range_low": 90, "range_high": 110,
        "n_levels": 2, "capital_total": 100, "params": {"dust_cash_proceeds": 0}},
        [{"level_idx": 0, "price": 90, "sell_price": 91, "capital": 50}])
    barrier = Barrier(2)
    def merge(key, value):
        barrier.wait()
        db.merge_grid_params(grid["id"], {key:value}, allowed=frozenset({key}))
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(merge, "max_days", 30)
        b = pool.submit(merge, "dust_cash_proceeds", 12)
        a.result(timeout=5); b.result(timeout=5)
    params = db.get_grid(grid["id"])["params"]
    assert params["max_days"] == 30
    assert params["dust_cash_proceeds"] == 12
