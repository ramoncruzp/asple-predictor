"""Inspecciona o compacta la BD Meta SQLite sin cargar configuración de la aplicación."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from grid.meta_operator import MetaOperator
from grid.meta_learning import REPLAY_METHOD_VERSION, stable_template
from grid.meta_signal_storage import compact_signal_payload

ADVISOR_VERSION = MetaOperator.ADVISOR_CACHE_METHOD_VERSION
TOUCH_VERSION = MetaOperator.TOUCH_CACHE_METHOD_VERSION
EVENTS_V1 = {"META_PLAN_EVALUATED", "META_OPERATOR_REVIEW_DECISION",
    "META_OPERATOR_NOOP", "META_OPERATOR_ROTATION_EVALUATED"}


def _connect(path: Path, *, read_only: bool):
    if read_only:
        uri = f"file:{path.resolve().as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=30)
        conn.execute("PRAGMA query_only=ON")
    else:
        conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _settings(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "meta_profile_settings" not in tables:
        return {"cache_retention_days": 7, "ledger_plan_detail_days": 14}
    row = conn.execute("SELECT settings FROM meta_profile_settings WHERE profile='GLOBAL'").fetchone()
    values = json.loads(row[0] or "{}") if row else {}
    return {"cache_retention_days": max(1, int(values.get("cache_retention_days", 7))),
        "ledger_plan_detail_days": max(1, int(values.get("ledger_plan_detail_days", 14)))}


def _v1_compact(event_type, details):
    if event_type == "META_PLAN_EVALUATED":
        source = details.get("plan") if isinstance(details.get("plan"), dict) else details
        result = MetaOperator._plan_ledger_payload(source, source.get("candidates") or [],
            source.get("selected") if isinstance(source.get("selected"), dict) else None,
            plan_kind=source.get("plan_kind") or "analisis")
    elif event_type == "META_OPERATOR_REVIEW_DECISION":
        decision = details.get("decision") if isinstance(details.get("decision"), dict) else details
        result = {"ledger_schema_version": 2,
            "plan_decision_id": details.get("plan_decision_id"),
            "decision": MetaOperator._compact_review_result(decision)}
        for key in ("decision_choices", "decision_choices_error", "shadow_decision", "valuation",
                    "comparison_version", "current_alternative_missing"):
            if key in details:
                result[key] = details[key]
    elif event_type == "META_OPERATOR_NOOP":
        result = MetaOperator._noop_payload(details, details.get("valuation"),
            details.get("plan_decision_id"))
    else:
        result = {key: details[key] for key in ("review_id", "alternative_key", "material", "streak",
            "reason", "probability_improvement_pp", "progress_improvement_pct", "shadow_decision")
            if key in details}
        for key in ("alternative", "current"):
            value = details.get(key)
            if isinstance(value, dict):
                result[key] = {field: value[field] for field in ("alternative_id", "symbol", "strategy", "estimate")
                    if field in value}
        result["ledger_schema_version"] = 2
    result["compacted_from_v1"] = True
    return result


def _compact_plan_v3(details, referenced):
    return MetaOperator._summarize_old_plan(details, referenced)


def _referenced_ids(conn, details):
    found = set()
    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"alternative_id", "alternativeId"} and item:
                    found.add(str(item))
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(details.get("decision_choices") or {})
    visit(details.get("selected") or {})
    # --apply ejecuta la importación legacy antes de resumir la fila; el dry-run
    # anticipa esas referencias para no prometer un ahorro que descartaría muestras.
    for alternative in details.get("alternatives") or []:
        if alternative.get("alternative_id") and alternative.get("pieces"):
            found.add(str(alternative["alternative_id"]))
    decision_id = details.get("decision_id")
    if decision_id:
        try:
            found.update(str(row[0]) for row in conn.execute(
                "SELECT alternative_id FROM meta_learning_samples WHERE decision_id=? AND alternative_id IS NOT NULL",
                (str(decision_id),)).fetchall())
        except sqlite3.OperationalError:
            pass
    return found


def _import_plan_samples(conn, details, meta_id, goal, stamp):
    """Importa muestras de una fila antes de retirar sus detalles históricos."""
    decision_id = str(details.get("decision_id") or "")
    if not decision_id or "meta_learning_samples" not in {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
        return 0
    config = json.loads(goal.get("config_snapshot") or "{}")
    created = 0
    base_method = details.get("base_method_version") or "rolling_7d_v2"
    for alternative in details.get("alternatives") or []:
        alternative_id = str(alternative.get("alternative_id") or "")
        if not alternative_id:
            continue
        parameters = MetaOperator._learning_parameters({"id": meta_id, "profile": goal.get("profile"),
            "capital_initial": goal.get("capital_initial"), "target_usdt": goal.get("target_usdt"),
            "config_snapshot": config}, alternative)
        pieces = parameters["pieces"]
        if not pieces:
            continue
        key = hashlib.sha256(f"live:{decision_id}:{alternative_id}".encode()).hexdigest()
        raw_probability = alternative.get("replay_probability")
        estimate = alternative.get("estimate") or {}
        windows = int(estimate.get("rolling_windows") or alternative.get("independent_windows") or 0)
        independent = int(estimate.get("independent_windows") or alternative.get("independent_windows") or 0)
        smooth = ((round(float(raw_probability) * windows) + 1) / (windows + 2)
            if raw_probability is not None and windows else None)
        signals = {"signals_version": 1, "signals_used": alternative.get("signals_used", []),
            "touch_signal": alternative.get("touch_signal"),
            "shadow_signals": alternative.get("shadow_signals", [])}
        provenance = {"source": "legacy_meta_ledger", "decision_id": decision_id,
            "alternative_id": alternative_id, "base_replay": {"raw_probability": raw_probability,
            "smoothed_probability": smooth, "step_days": 7, "window_count": windows,
            "independent_windows": independent, "method_version": base_method}}
        conn.execute("""INSERT OR IGNORE INTO meta_learning_samples
            (sample_key, meta_id, decision_id, alternative_id, symbol, profile, sample_at,
             source_kind, base_probability, base_probability_smoothed, base_window_count,
             base_independent_windows, base_method_version, recalculate_base,
             training_exclusion_reason, parameters_json, signals_json, provenance_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'live', ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)""",
            (key, int(meta_id), decision_id, alternative_id, str(pieces[0].get("symbol") or "").upper(),
             parameters["profile"], stamp.isoformat(sep=" "), raw_probability, smooth, windows, independent,
             base_method, "sin base suficiente" if independent < 3 else None,
             json.dumps(parameters, ensure_ascii=False, sort_keys=True, default=str),
             json.dumps(signals, ensure_ascii=False, sort_keys=True, default=str),
             json.dumps(provenance, ensure_ascii=False, sort_keys=True, default=str),
             datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")))
        created += max(0, conn.execute("SELECT changes()").fetchone()[0])
    return created


def _transform_event(conn, event_type, details, stamp, plan_days):
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone(timezone.utc).replace(tzinfo=None)
    compact = details
    if event_type in EVENTS_V1 and int(details.get("ledger_schema_version") or 1) < 2:
        compact = _v1_compact(event_type, details)
    if event_type == "META_PLAN_EVALUATED" and stamp < datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=plan_days):
        if int(compact.get("ledger_schema_version") or 0) >= 2 and compact.get("ledger_detail") != "resumido":
            compact = _compact_plan_v3(compact, _referenced_ids(conn, compact))
    if event_type in EVENTS_V1 and int(details.get("ledger_schema_version") or 1) < 2:
        compact["compacted_from_v1"] = True
    return compact


def _existing_templates(conn):
    try:
        rows = conn.execute("SELECT parameters_json FROM meta_learning_samples").fetchall()
    except sqlite3.OperationalError:
        return set()
    result = set()
    for row in rows:
        try:
            params = json.loads(row[0] or "{}")
            template, _ = stable_template(params, meta_capital_usdt=params.get("capital_usdt"))
            result.add(template)
        except (TypeError, ValueError, KeyError):
            continue
    return result


def _cache_stale(conn, settings):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(days=settings["cache_retention_days"])
    current = {"replay": REPLAY_METHOD_VERSION, "advisor": ADVISOR_VERSION,
        "touch_signal": TOUCH_VERSION}
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    result = {"meta_analysis_cache": [], "meta_learning_replay_cache": []}
    if "meta_analysis_cache" in tables:
        for row in conn.execute("SELECT cache_key, cache_kind, value_json, created_at FROM meta_analysis_cache"):
            stale = False
            try:
                created = datetime.fromisoformat(str(row["created_at"]))
                if created.tzinfo is not None:
                    created = created.astimezone(timezone.utc).replace(tzinfo=None)
            except ValueError:
                created = datetime.min
            if created < cutoff:
                stale = True
            expected = current.get(str(row["cache_kind"]))
            if expected:
                try:
                    stale = stale or json.loads(row["value_json"] or "{}").get("method_version") != expected
                except (TypeError, ValueError):
                    stale = True
            if stale:
                result["meta_analysis_cache"].append((row["cache_key"], row["value_json"] or ""))
    active_templates = _existing_templates(conn)
    if "meta_learning_replay_cache" in tables:
        for row in conn.execute("SELECT template_id, window_start, created_at FROM meta_learning_replay_cache"):
            try:
                created = datetime.fromisoformat(str(row["created_at"]))
                if created.tzinfo is not None:
                    created = created.astimezone(timezone.utc).replace(tzinfo=None)
            except ValueError:
                created = datetime.min
            if created < cutoff or (active_templates and row["template_id"] not in active_templates):
                result["meta_learning_replay_cache"].append((row["template_id"], row["window_start"]))
    return result


def inspect_changes(conn):
    settings = _settings(conn)
    event_actions = []
    errors = []
    if "meta_ledger" in {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
        for row in conn.execute("SELECT id, meta_id, event_type, details, ts FROM meta_ledger WHERE event_type IN "
                "('META_PLAN_EVALUATED','META_OPERATOR_REVIEW_DECISION','META_OPERATOR_NOOP',"
                "'META_OPERATOR_ROTATION_EVALUATED') ORDER BY id"):
            try:
                details = json.loads(row["details"] or "{}")
                stamp = datetime.fromisoformat(str(row["ts"]))
                new = _transform_event(conn, row["event_type"], details, stamp, settings["ledger_plan_detail_days"])
                before = len((row["details"] or "").encode("utf-8"))
                after = len(json.dumps(new, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8"))
                if new != details:
                    event_actions.append({"id": int(row["id"]), "event_type": row["event_type"],
                        "meta_id": int(row["meta_id"]), "ts": row["ts"],
                        "before_bytes": before, "after_bytes": after,
                        "original_details": details, "new_details": new})
            except (ValueError, TypeError, KeyError) as exc:
                errors.append({"ledger_id": int(row["id"]), "event_type": row["event_type"],
                    "error": f"{type(exc).__name__}: {exc}"})
    caches = _cache_stale(conn, settings)
    cache_bytes = sum(len(value.encode("utf-8")) for _key, value in caches["meta_analysis_cache"])
    cache_bytes += len(caches["meta_learning_replay_cache"]) * 96
    return {"settings": settings, "events": event_actions, "errors": errors, "cache_rows": caches,
        "estimated_savings_bytes": sum(max(0, item["before_bytes"] - item["after_bytes"]) for item in event_actions)
            + cache_bytes}


def _apply(conn, report):
    event_counts = {}
    legacy_samples_imported = 0
    actions = report["events"]
    for offset in range(0, len(actions), 20):
        batch = actions[offset:offset + 20]
        try:
            conn.execute("BEGIN")
            for item in batch:
                if item["event_type"] == "META_PLAN_EVALUATED":
                    stamp = datetime.fromisoformat(str(item["ts"]))
                    if stamp.tzinfo is not None:
                        stamp = stamp.astimezone(timezone.utc).replace(tzinfo=None)
                    goal = conn.execute("SELECT profile, capital_initial, target_usdt, config_snapshot "
                        "FROM meta_goals WHERE id=?", (item["meta_id"],)).fetchone()
                    if goal:
                        legacy_samples_imported += _import_plan_samples(
                            conn, item["original_details"], item["meta_id"], dict(goal), stamp)
                payload = dict(item["new_details"])
                conn.execute("UPDATE meta_ledger SET details=? WHERE id=?",
                    (json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str), item["id"]))
                event_counts[item["event_type"]] = event_counts.get(item["event_type"], 0) + 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    cache_deleted = {}
    for table, rows in report["cache_rows"].items():
        for offset in range(0, len(rows), 100):
            batch = rows[offset:offset + 100]
            try:
                conn.execute("BEGIN")
                if table == "meta_analysis_cache":
                    conn.executemany("DELETE FROM meta_analysis_cache WHERE cache_key=?", [(row[0],) for row in batch])
                elif table == "meta_learning_replay_cache":
                    conn.executemany("DELETE FROM meta_learning_replay_cache WHERE template_id=? AND window_start=?", batch)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        cache_deleted[table] = len(rows)
    summary = {"event_rows_compacted": event_counts, "legacy_samples_imported": legacy_samples_imported,
        "cache_rows_deleted": cache_deleted,
        "estimated_bytes_reclaimed": report["estimated_savings_bytes"]}
    try:
        row = conn.execute("SELECT id FROM meta_goals ORDER BY id DESC LIMIT 1").fetchone()
        if row:
            conn.execute("INSERT INTO meta_ledger(meta_id, run_id, ts, event_type, details) VALUES(?, NULL, ?, ?, ?)",
                (row[0], datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" "),
                 "META_MAINTENANCE", json.dumps(summary, ensure_ascii=False, sort_keys=True)))
            conn.commit()
        else:
            summary["ledger_event"] = "omitido: no existe una meta para asociar la FK"
    except sqlite3.OperationalError:
        summary["ledger_event"] = "omitido: esquema de bitácora no disponible"
    return summary


def _db_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _integrity(conn):
    result = [row[0] for row in conn.execute("PRAGMA integrity_check")]
    if result != ["ok"]:
        raise RuntimeError("PRAGMA integrity_check no devolvió ok: " + "; ".join(result[:10]))


def _check_service_stopped(*, allow_override=False, runner=subprocess.run):
    if allow_override:
        return "omitido por bandera de prueba"
    try:
        result = runner(["systemctl", "is-active", "asple-predictor"], capture_output=True,
            text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"no se pudo consultar systemd: {exc}") from exc
    state = (result.stdout or "").strip().lower()
    if state == "active":
        raise RuntimeError("asple-predictor está activo; detén el servicio antes de VACUUM")
    return state or "sin estado informado"


def _backup_exists(today=None, home=None):
    today = today or datetime.now().date()
    root = Path(home or Path.home()) / "respaldos"
    if not root.is_dir():
        return False
    stamps = {today.isoformat(), today.strftime("%Y%m%d"), today.strftime("%Y-%m-%d")}
    return any(path.is_file() and any(stamp in path.name for stamp in stamps)
        for path in root.iterdir())


def compact_signals(path: Path, *, apply=False, backup_ok=False):
    """Vista previa o deduplicación explícita de los detalles de modelo en señales."""
    if not path.is_file():
        raise FileNotFoundError(f"No existe la BD indicada: {path}")
    if apply and not backup_ok and not _backup_exists():
        raise RuntimeError("falta respaldo de hoy en ~/respaldos/; usa --backup-ok tras verificarlo")
    before_hash = _db_hash(path) if not apply else None
    total_before = total_after_inline = rows_with_details = 0
    blobs = {}
    with _connect(path, read_only=not apply) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "meta_learning_samples" not in tables:
            raise RuntimeError("la BD no contiene meta_learning_samples")
        if apply:
            conn.execute("CREATE TABLE IF NOT EXISTS meta_learning_signal_details ("
                "content_hash TEXT PRIMARY KEY, payload_json TEXT NOT NULL, created_at TEXT NOT NULL)")
        rows = conn.execute("SELECT id, signals_json, created_at FROM meta_learning_samples ORDER BY id").fetchall()
        for row in rows:
            raw = row["signals_json"] or "{}"
            compact, details = compact_signal_payload(json.loads(raw))
            if not details:
                continue
            rows_with_details += 1
            total_before += len(raw.encode("utf-8"))
            compact_raw = json.dumps(compact, sort_keys=True, separators=(",", ":"), default=str)
            total_after_inline += len(compact_raw.encode("utf-8"))
            blobs.update(details)
            if apply:
                for content_hash, payload in details.items():
                    conn.execute("INSERT OR IGNORE INTO meta_learning_signal_details "
                        "(content_hash,payload_json,created_at) VALUES(?,?,?)",
                        (content_hash, payload, row["created_at"]))
                conn.execute("UPDATE meta_learning_samples SET signals_json=? WHERE id=?",
                    (compact_raw, row["id"]))
    detail_bytes = sum(len(value.encode("utf-8")) for value in blobs.values())
    after = total_after_inline + detail_bytes
    return {"mode": "apply" if apply else "dry-run", "rows_with_model_details": rows_with_details,
        "unique_detail_blobs": len(blobs), "bytes_before": total_before,
        "bytes_after_estimated": after, "bytes_saved_estimated": max(0, total_before-after),
        "sha256_unchanged": before_hash == _db_hash(path) if not apply else None}


def vacuum(path: Path, *, backup_ok=False, service_override=False, runner=subprocess.run):
    if not backup_ok and not _backup_exists():
        raise RuntimeError("falta respaldo de hoy en ~/respaldos/; usa --backup-ok tras verificarlo")
    service_state = _check_service_stopped(allow_override=service_override, runner=runner)
    with _connect(path, read_only=False) as conn:
        _integrity(conn)
        before = path.stat().st_size
        conn.execute("VACUUM")
        _integrity(conn)
    return {"service_state": service_state, "bytes_before_vacuum": before,
        "bytes_after_vacuum": path.stat().st_size}


def run(path: Path, *, apply=False, vacuum_after=False, backup_ok=False,
        service_override=False, runner=subprocess.run):
    if not path.is_file():
        raise FileNotFoundError(f"No existe la BD indicada: {path}")
    if vacuum_after and not apply:
        raise ValueError("--vacuum requiere --apply")
    before_size = path.stat().st_size
    before_hash = _db_hash(path) if not apply else None
    with _connect(path, read_only=not apply) as conn:
        report = inspect_changes(conn)
        if report["errors"]:
            raise RuntimeError("hay filas de bitácora que no se pudieron analizar: " +
                json.dumps(report["errors"], ensure_ascii=False))
        response = {"db": str(path), "mode": "apply" if apply else "dry-run",
            "database_mb_before": round(before_size / 1024 / 1024, 3),
            "estimated_savings_mb": round(report["estimated_savings_bytes"] / 1024 / 1024, 3),
            "rows_by_event": {event: sum(1 for row in report["events"] if row["event_type"] == event)
                for event in sorted(EVENTS_V1 | {"META_PLAN_EVALUATED"})},
            "estimated_savings_bytes_by_event": {event: sum(max(0, row["before_bytes"] - row["after_bytes"])
                for row in report["events"] if row["event_type"] == event)
                for event in sorted({row["event_type"] for row in report["events"]})},
            "cache_rows": {key: len(value) for key, value in report["cache_rows"].items()},
            "estimated_savings_bytes_by_table": {
                "meta_ledger": sum(max(0, row["before_bytes"] - row["after_bytes"]) for row in report["events"]),
                "meta_analysis_cache": sum(len(value.encode("utf-8"))
                    for _key, value in report["cache_rows"]["meta_analysis_cache"]),
                "meta_learning_replay_cache": len(report["cache_rows"]["meta_learning_replay_cache"]) * 96},
            "settings": report["settings"]}
        if apply:
            response["applied"] = _apply(conn, report)
        else:
            response["sha256_unchanged"] = before_hash == _db_hash(path)
        response["database_mb_after_apply"] = round(path.stat().st_size / 1024 / 1024, 3)
    if vacuum_after:
        response["vacuum"] = vacuum(path, backup_ok=backup_ok,
            service_override=service_override, runner=runner)
    response["database_mb_after"] = round(path.stat().st_size / 1024 / 1024, 3)
    return response


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path, help="archivo SQLite explícito; nunca lee .env")
    parser.add_argument("--apply", action="store_true", help="compacta y aplica retención")
    parser.add_argument("--vacuum", action="store_true", help="VACUUM después de --apply")
    parser.add_argument("--backup-ok", action="store_true", help="confirma que Ramón verificó el respaldo de hoy")
    parser.add_argument("--compactar-senales", action="store_true",
        help="mide por defecto; con --apply deduplica detalles de modelos en meta_learning_samples")
    parser.add_argument("--i-know-service-is-stopped", action="store_true",
        help="omitir systemctl solo en pruebas controladas")
    args = parser.parse_args(argv)
    try:
        if args.compactar_senales:
            if args.vacuum:
                raise ValueError("--vacuum no se combina con --compactar-senales")
            result = compact_signals(args.db, apply=args.apply, backup_ok=args.backup_ok)
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        result = run(args.db, apply=args.apply, vacuum_after=args.vacuum,
            backup_ok=args.backup_ok, service_override=args.i_know_service_is_stopped)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"Error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
