import threading
import time
from types import SimpleNamespace

from grid.monitor import GridMonitor

from tests.test_grid_control_api import build_client


def test_control_action_and_monitor_pass_share_the_same_lock(tmp_path):
    client, db, _, engine = build_client(tmp_path)
    monitor = SimpleNamespace(ops_lock=threading.RLock())
    client.app.state.grid_monitor = monitor
    inside = {"value": False, "overlap": False}
    started = threading.Event()

    def monitor_pass(trigger="SCHEDULED"):
        inside["value"] = True
        started.set()
        time.sleep(.15)
        inside["value"] = False
        return {}

    monitor._run_once_locked = monitor_pass
    original_pause = engine.pause_grid
    def checked_pause(*args):
        if inside["value"]: inside["overlap"] = True
        return original_pause(*args)
    engine.pause_grid = checked_pause
    monitor_thread = threading.Thread(target=GridMonitor.run_once, args=(monitor,))
    monitor_thread.start()
    assert started.wait(1)
    response = client.post("/api/grids/1/pause", json={"dry_run":False,"confirm":True})
    monitor_thread.join(timeout=1)
    assert response.status_code == 200
    assert inside["overlap"] is False
    assert not monitor_thread.is_alive()


def test_control_lock_timeout_returns_conflict(tmp_path):
    client, _, _, _ = build_client(tmp_path)
    lock = threading.RLock()
    client.app.state.grid_monitor = SimpleNamespace(ops_lock=lock)
    held = threading.Event()
    def hold_lock():
        with lock:
            held.set()
            time.sleep(1.2)
    holder = threading.Thread(target=hold_lock)
    holder.start(); assert held.wait(1)
    response = client.post("/api/grids/1/pause", json={"dry_run":False,"confirm":True})
    holder.join(timeout=2)
    assert response.status_code == 409
    assert "reintenta" in response.body["detail"]
