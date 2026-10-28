from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from database.db_manager import DBManager
from grid.meta_learning import extract_learning_features
from grid.meta_operator import MetaOperator
from grid.meta_signal_storage import compact_signal_payload, expand_signal_payload
from scripts.mantenimiento_bd_meta import compact_signals


def _signals():
    detail = {"realized_volatility_24h": .03,
        "volatility_forecasts": [{"model": "v1", "sigma": .04, "made_at": "cut"}],
        "direction_predictions": [{"model": "model_a_down", "interval": "1h",
            "probability_up": .7, "predicted_at": "cut"}]}
    return {"signals_version": 2,
        "signals_used": [{"name": "volatility", "normalized": .2}],
        "point_in_time_by_symbol": {"XRPUSDT": {"signals": {
            **detail, "touch_buy_to_sell": .5}}}}


def test_model_detail_dedup_round_trip_preserves_learning_features(tmp_path):
    db = DBManager(f"sqlite:///{tmp_path / 'signal-store.sqlite'}")
    operator = MetaOperator(SimpleNamespace(monitor=None), db, None, None,
        SimpleNamespace(meta_mode_enabled=True))
    original = _signals()
    for index in range(2):
        operator._store_learning_sample({"sample_key": f"sample-{index}", "meta_id": None,
            "decision_id": f"decision-{index}", "alternative_id": f"alt-{index}",
            "symbol": "XRPUSDT", "profile": "RAPIDO", "sample_at": datetime.now(timezone.utc),
            "source_kind": "historical_reconstructed", "parameters_json": "{}",
            "signals_json": json.dumps(original), "provenance_json": "{}",
            "created_at": datetime.now(timezone.utc)})
    with db.engine.connect() as conn:
        samples = conn.execute(select(db.meta_learning_samples)).mappings().all()
        detail_rows = conn.execute(select(db.meta_learning_signal_details)).mappings().all()
    assert len(detail_rows) == 1
    assert all("model_details_refs" in json.loads(row["signals_json"]) for row in samples)
    restored = operator._expand_learning_signal_rows(samples)
    assert all(json.loads(row["signals_json"]) == original for row in restored)
    for row in restored:
        assert extract_learning_features(json.loads(row["signals_json"])) == extract_learning_features(original)
    assert len(samples[0]["signals_json"]) < len(json.dumps(original))


def test_compact_signals_preview_is_read_only_and_apply_requires_backup(tmp_path):
    path = tmp_path / "legacy.sqlite"
    import sqlite3
    original = _signals()
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE meta_learning_samples(id INTEGER PRIMARY KEY, signals_json TEXT, created_at TEXT)")
        conn.executemany("INSERT INTO meta_learning_samples VALUES(?,?,?)", [
            (1, json.dumps(original), "2026-01-01"), (2, json.dumps(original), "2026-01-01")])
    before = path.read_bytes()
    preview = compact_signals(path)
    assert preview["mode"] == "dry-run" and preview["unique_detail_blobs"] == 1
    assert preview["sha256_unchanged"] is True and path.read_bytes() == before
    with pytest.raises(RuntimeError, match="falta respaldo"):
        compact_signals(path, apply=True, backup_ok=False)
    result = compact_signals(path, apply=True, backup_ok=True)
    assert result["mode"] == "apply"
    with sqlite3.connect(path) as conn:
        payloads = [json.loads(row[0]) for row in conn.execute(
            "SELECT signals_json FROM meta_learning_samples ORDER BY id")]
        details = dict(conn.execute("SELECT content_hash,payload_json FROM meta_learning_signal_details"))
    assert len(details) == 1
    assert [expand_signal_payload(payload, details) for payload in payloads] == [original, original]
