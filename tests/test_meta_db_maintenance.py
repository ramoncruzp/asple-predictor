import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.mantenimiento_bd_meta import run, vacuum


def _fixture(path):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with sqlite3.connect(path) as conn:
        conn.executescript("""
        CREATE TABLE meta_profile_settings(profile TEXT PRIMARY KEY, settings TEXT, updated_at TEXT);
        CREATE TABLE meta_analysis_cache(cache_key TEXT PRIMARY KEY, cache_kind TEXT,
            analysis_day TEXT, value_json TEXT, created_at TEXT);
        CREATE TABLE meta_learning_replay_cache(template_id TEXT, window_start INTEGER,
            reached INTEGER, created_at TEXT, PRIMARY KEY(template_id, window_start));
        CREATE TABLE meta_learning_samples(sample_key TEXT UNIQUE, meta_id INTEGER, decision_id TEXT,
            alternative_id TEXT, symbol TEXT, profile TEXT, sample_at TEXT, source_kind TEXT,
            base_probability REAL, base_probability_smoothed REAL, base_window_count INTEGER,
            base_independent_windows INTEGER, base_method_version TEXT, recalculate_base INTEGER,
            training_exclusion_reason TEXT, parameters_json TEXT, signals_json TEXT,
            provenance_json TEXT, created_at TEXT);
        CREATE TABLE meta_goals(id INTEGER PRIMARY KEY, profile TEXT, capital_initial REAL,
            target_usdt REAL, config_snapshot TEXT);
        CREATE TABLE meta_ledger(id INTEGER PRIMARY KEY, meta_id INTEGER, run_id INTEGER,
            ts TEXT, event_type TEXT, reason TEXT, grid_id INTEGER, level_idx INTEGER, details TEXT);
        """)
        conn.execute("INSERT INTO meta_profile_settings VALUES ('GLOBAL', ?, ?)",
            (json.dumps({"cache_retention_days": 7, "ledger_plan_detail_days": 14}), now.isoformat()))
        conn.execute("INSERT INTO meta_goals VALUES (1, 'RAPIDO', 100, 10, '{}')")
        alternatives = [{"alternative_id": f"a{i}", "symbol": "XRPUSDT", "strategy": "simple",
            "pieces": [{"symbol": "XRPUSDT", "strategy": "simple", "capital_usdt": 10}],
            "estimate": {"probability_replay": 1 - i / 100},
            "signals_omitted": [{"name": "touch_buy_to_sell", "reason": "sin velas"}],
            "advisor_recommendations": [{"large": "x" * 100}]}
            for i in range(12)]
        conn.execute("INSERT INTO meta_learning_samples (sample_key, decision_id, alternative_id, parameters_json) "
            "VALUES (?, ?, ?, ?)", ("existing-sample", "decision-old", "a11", "{}"))
        details = {"decision_id": "decision-old", "profile": "RAPIDO", "alternatives": alternatives,
            "selected": alternatives[0], "decision_choices": {"real": {"alternative_id": "a10"}},
            "advisor_comparisons": [{"large": "x" * 2000}], "capital_usage": {"used_usdt": 100}}
        old = (now - timedelta(days=20)).isoformat(sep=" ")
        conn.execute("INSERT INTO meta_ledger VALUES (1,1,NULL,?,'META_PLAN_EVALUATED',NULL,NULL,NULL,?)",
            (old, json.dumps(details)))
        noop = {"decision": "no_hacer_nada", "reason": "sin opción", "valuation": {},
            "plan": {"large": "x" * 10000}}
        conn.execute("INSERT INTO meta_ledger VALUES (2,1,NULL,?,'META_OPERATOR_NOOP',NULL,NULL,NULL,?)",
            (now.isoformat(sep=" "), json.dumps(noop)))
        conn.execute("INSERT INTO meta_analysis_cache VALUES ('old-cache','advisor','2020-01-01',?,?)",
            (json.dumps({"method_version": "old", "body": "x" * 400}),
             (now - timedelta(days=10)).isoformat(sep=" ")))


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_dry_run_keeps_database_hash_unchanged_and_reports_savings(tmp_path):
    path = tmp_path / "fixture.db"
    _fixture(path)
    before = _sha(path)
    result = run(path)
    assert result["mode"] == "dry-run"
    assert result["sha256_unchanged"] is True
    assert result["estimated_savings_mb"] > 0
    assert result["rows_by_event"]["META_PLAN_EVALUATED"] == 1
    assert result["cache_rows"]["meta_analysis_cache"] == 1
    assert _sha(path) == before


def test_apply_compacts_v1_and_resumes_readers_from_summary(tmp_path):
    path = tmp_path / "fixture-apply.db"
    _fixture(path)
    result = run(path, apply=True)
    assert result["mode"] == "apply"
    with sqlite3.connect(path) as conn:
        rows = {event: json.loads(details) for event, details in conn.execute(
            "SELECT event_type, details FROM meta_ledger WHERE event_type IN "
            "('META_PLAN_EVALUATED','META_OPERATOR_NOOP')")}
        plan = rows["META_PLAN_EVALUATED"]
        assert plan["ledger_schema_version"] == 3 and plan["ledger_detail"] == "resumido"
        assert plan["alternatives_total"] == 12
        assert {row["alternative_id"] for row in plan["alternatives"]} >= {"a0", "a10", "a11"}
        assert plan["signals_omitted_summary"][0]["count"] == 12
        assert rows["META_OPERATOR_NOOP"]["compacted_from_v1"] is True
        assert "plan" not in rows["META_OPERATOR_NOOP"]
        assert conn.execute("SELECT COUNT(*) FROM meta_analysis_cache").fetchone()[0] == 0
        assert conn.execute("SELECT details FROM meta_ledger WHERE event_type='META_MAINTENANCE'").fetchone()


def test_vacuum_requires_apply_and_aborts_when_service_is_active(tmp_path):
    path = tmp_path / "fixture-vacuum.db"
    _fixture(path)
    with pytest.raises(ValueError, match="requiere --apply"):
        run(path, vacuum_after=True)
    class Active:
        stdout = "active\n"
    with pytest.raises(RuntimeError, match="está activo"):
        vacuum(path, backup_ok=True, runner=lambda *_args, **_kwargs: Active())


def test_vacuum_override_runs_integrity_check_and_compacts(tmp_path):
    path = tmp_path / "fixture-vacuum-override.db"
    _fixture(path)
    result = run(path, apply=True, vacuum_after=True, backup_ok=True, service_override=True)
    assert result["vacuum"]["service_state"] == "omitido por bandera de prueba"
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_script_source_does_not_read_environment_or_application_database(tmp_path):
    source = (Path(__file__).resolve().parents[1] / "scripts" / "mantenimiento_bd_meta.py").read_text(
        encoding="utf-8")
    assert "load_dotenv" not in source and "dotenv_values" not in source
    assert "DBManager(" not in source and "settings.database_url" not in source
