import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("reetiquetar_meta", ROOT / "scripts" / "reetiquetar_meta.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def _db(path):
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE meta_learning_samples(id INTEGER PRIMARY KEY,symbol TEXT,sample_at TEXT,
                provenance_json TEXT,parameters_json TEXT);
            CREATE TABLE meta_learning_labels(id INTEGER PRIMARY KEY,sample_id INTEGER,horizon TEXT,
                label_json TEXT,labeled_at TEXT);
        """)
        conn.executemany("INSERT INTO meta_learning_samples VALUES(?,?,?,?,?)", [
            (1, "XRPUSDT", "2026-10-20 12:00:00", "{}", '{"tick_size":0.00001}'),
            (2, "BTCUSDT", "2026-10-20 12:00:00", "{}", '{"tick_size":0.0001}'),
            (3, "XRPUSDT", "2026-10-29 12:00:00", "{}", '{"tick_size":0.00001}')])
        conn.executemany("INSERT INTO meta_learning_labels VALUES(?,?,?,?,?)", [
            (1, 1, "72h", '{"reached":true}', "2026-10-21"),
            (2, 2, "72h", '{"reached":false}', "2026-10-21"),
            (3, 3, "72h", '{"reached":false}', "2026-10-29")])


def test_dry_run_does_not_write_and_reports_tick_difference(tmp_path):
    db = tmp_path / "meta.sqlite"
    _db(db)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    rows = module.inspect(db)
    assert {row["symbol"]: row["tick_status"] for row in rows} == {
        "BTCUSDT": "igual", "XRPUSDT": "difiere"}
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


def test_apply_requires_backup_confirmation(tmp_path):
    db = tmp_path / "meta.sqlite"
    _db(db)
    rows = module.inspect(db)
    with pytest.raises(RuntimeError, match="respaldo"):
        module.apply(db, rows, backup_check=lambda: False)


def test_apply_marks_only_affected_old_labels_and_daily_job_can_replace(tmp_path):
    db = tmp_path / "meta.sqlite"
    _db(db)
    rows = module.inspect(db)
    assert module.apply(db, rows, backup_ok=True) == 1
    with sqlite3.connect(db) as conn:
        labels = {int(row[0]): json.loads(row[1]) for row in conn.execute(
            "SELECT id,label_json FROM meta_learning_labels")}
    assert labels[1]["status"] == "superseded"
    assert labels[1]["superseded_previous_label"] == {"reached": True}
    assert labels[2] == {"reached": False}
    assert labels[3] == {"reached": False}


def test_superseded_label_is_replaced_by_daily_label_persistence(tmp_path):
    from datetime import datetime, timezone
    from sqlalchemy import select
    from database.db_manager import DBManager
    db = DBManager(f"sqlite:///{tmp_path / 'learning.sqlite'}")
    with db.engine.begin() as conn:
        sample_id = conn.execute(db.meta_learning_samples.insert().values(
            sample_key="old", symbol="XRPUSDT", profile="RAPIDO", sample_at=datetime.now(timezone.utc),
            source_kind="live", parameters_json="{}", signals_json="{}", provenance_json="{}",
            created_at=datetime.now(timezone.utc))).inserted_primary_key[0]
        conn.execute(db.meta_learning_labels.insert().values(sample_id=sample_id, horizon="72h",
            label_json=json.dumps({"status": "superseded", "reached": True}),
            labeled_at=datetime.now(timezone.utc)))
    from grid.meta_operator import MetaOperator
    operator = object.__new__(MetaOperator)
    operator.db = db
    assert operator._persist_learning_labels(sample_id, {"72h": {"status": "labeled", "reached": False}},
        datetime.now(timezone.utc)) == 1
    with db.engine.connect() as conn:
        value = json.loads(conn.execute(select(db.meta_learning_labels.c.label_json)).scalar_one())
    assert value == {"reached": False}
