from __future__ import annotations

import gc
import tracemalloc

from database.db_manager import DBManager
from grid.intelligence_snapshot import IntelligenceSnapshotService

def test_snapshot_replacement_keeps_one_bounded_payload_over_ten_refreshes(tmp_path, capsys):
    db = DBManager(f"sqlite:///{tmp_path / 'snapshot-memory.sqlite'}")
    counter = []

    def build():
        counter.append(len(counter))
        return {"rows": [f"fila-{counter[-1]}-" + ("x" * 100) for _ in range(2500)]}

    service = IntelligenceSnapshotService(db, build)
    gc.collect()
    tracemalloc.start()
    before, _ = tracemalloc.get_traced_memory()
    for _ in range(10):
        service.request_refresh()
        assert service.wait(5)
        assert service.status()["status"] == "ready"
        gc.collect()
    after, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    retained_mb = max(0, after - before) / (1024 * 1024)
    peak_mb = max(0, peak - before) / (1024 * 1024)
    print(f"C38c snapshot 10 refreshes: retenido={retained_mb:.2f} MB, pico={peak_mb:.2f} MB, "
        f"bytes_final={service.status()['snapshot_bytes']}")
    assert len(counter) == 10
    assert service.status()["snapshot_bytes"] < 16 * 1024 * 1024
    assert retained_mb < 8


def test_oversized_snapshot_does_not_replace_the_last_valid_one(tmp_path, monkeypatch):
    import grid.intelligence_snapshot as snapshot_module

    db = DBManager(f"sqlite:///{tmp_path / 'snapshot-limit.sqlite'}")
    values = [{"ok": True}]
    service = IntelligenceSnapshotService(db, lambda: values[0])
    service.request_refresh()
    assert service.wait(2)
    previous = service.snapshot
    monkeypatch.setattr(snapshot_module, "INTELLIGENCE_SNAPSHOT_MAX_BYTES", 64)
    values[0] = {"too_big": "x" * 1000}
    service.request_refresh()
    assert service.wait(2)
    assert service.snapshot is previous
    assert "supera el l\u00edmite" in service.status()["last_error"]


