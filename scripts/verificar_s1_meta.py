"""Resumen de verificación Meta para ejecutar localmente en S1; SQLite solo lectura."""
from __future__ import annotations
import argparse
import json
import os
import sqlite3
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote


def _connect(path: Path):
    absolute = str(path.resolve()).replace("\\", "/")
    conn = sqlite3.connect(f"file:{quote(absolute, safe='/:')}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(conn, table):
    return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}


def _json(value):
    try: return json.loads(value or "{}")
    except (TypeError, ValueError): return {}


def _rows(conn, table, columns, where="", args=(), order="", limit=None):
    if table not in _tables(conn): return []
    existing = _columns(conn, table)
    selected = [c for c in columns if c in existing]
    if not selected: return []
    sql = f'SELECT {", ".join(selected)} FROM "{table}"'
    if where: sql += " WHERE " + where
    if order: sql += " ORDER BY " + order
    if limit is not None: sql += f" LIMIT {int(limit)}"
    return [dict(row) for row in conn.execute(sql, args)]


def _ledger(conn, since):
    return _rows(conn, "meta_ledger", ("id", "meta_id", "ts", "event_type", "reason", "grid_id", "details"),
        "datetime(ts)>=datetime(?)", (since,), "id DESC", 2000)


def verify(db_path: Path, hours=2, journal=False):
    if not db_path.is_file(): raise FileNotFoundError(db_path)
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=max(0, hours))).replace(tzinfo=None).isoformat(sep=" ")
    try:
        from zoneinfo import ZoneInfo
        local = now.astimezone(ZoneInfo("America/Santo_Domingo"))
    except Exception:
        local = now.astimezone()
    print(f"Base: {db_path.resolve()} (mode=ro)")
    print(f"Hora UTC: {now.isoformat()} | Santo Domingo: {local.isoformat()}")
    try:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=Path(__file__).resolve().parents[1],
            text=True, capture_output=True, timeout=3, check=True).stdout.strip()
        print(f"Git HEAD: {head}")
    except Exception:
        print("Git HEAD: no disponible")

    with _connect(db_path) as conn:
        tables = _tables(conn)
        goals = _rows(conn, "meta_goals", ("id", "profile", "status", "capital_initial", "target_usdt", "environment"),
            "status IN ('ACTIVE','PIECES_BLOCKED','SURPLUS_WAIT')", (), "id")
        ledger = _ledger(conn, cutoff)
        latest_plans = {}
        for row in ledger:
            if row.get("event_type") == "META_PLAN_EVALUATED":
                latest_plans.setdefault(row.get("meta_id"), _json(row.get("details")))
        links = _rows(conn, "meta_piece_links", ("meta_id", "grid_id", "piece_type", "released_at"),
            "released_at IS NULL", (), "meta_id, grid_id")
        grids = {row["id"]: dict(row) for row in conn.execute("SELECT * FROM grids")} if "grids" in tables else {}
        print("\nMetas activas:")
        if not goals: print("  ninguna")
        for goal in goals:
            mid = goal["id"]
            valuation = next((_json(row.get("details")).get("valuation") for row in ledger
                if row.get("meta_id") == mid and row.get("event_type") == "META_OPERATOR_REVIEW_DECISION"), None)
            pieces = []
            for link in links:
                if link.get("meta_id") != mid: continue
                grid = grids.get(link.get("grid_id"), {})
                pieces.append({"grid_id": link["grid_id"], "symbol": grid.get("symbol"),
                    "strategy": grid.get("strategy"), "status": grid.get("status"),
                    "capital_usdt": grid.get("capital_total"), "range_low": grid.get("range_low"),
                    "range_high": grid.get("range_high"), "n_levels": grid.get("n_levels")})
            current = latest_plans.get(mid, {}).get("current_pieces", {})
            print(f"  Meta #{mid}: {goal.get('status')} {goal.get('profile')} env={goal.get('environment')} "
                  f"capital={goal.get('capital_initial')} objetivo={goal.get('target_usdt')}")
            print(f"    valoración más reciente: {json.dumps(valuation, ensure_ascii=False, separators=(',', ':')) if valuation else 'sin valoración guardada'}")
            for piece in pieces:
                estimate = current.get(str(piece["grid_id"]), current.get(piece["grid_id"])) if isinstance(current, dict) else None
                print(f"    pieza {piece['grid_id']}: {piece['symbol']} {piece['strategy']} {piece['status']} "
                      f"capital={piece['capital_usdt']} rango={piece['range_low']}–{piece['range_high']} "
                      f"n={piece['n_levels']} estimación={json.dumps(estimate, ensure_ascii=False, separators=(',', ':')) if estimate else 'sin current_pieces reciente'}")

        print(f"\nÚltimas revisiones desde {cutoff}:")
        wanted = {"META_OPERATOR_REVIEW_DECISION", "META_OPERATOR_NOOP", "META_OPERATOR_ROTATION_EVALUATED",
                  "META_OPERATOR_ALLOCATION_SHADOW", "META_OPERATOR_REVIEW_SKIPPED", "META_OPERATOR_REVIEW_CANCELLED"}
        for row in reversed(ledger):
            event = row.get("event_type")
            if event not in wanted: continue
            d = _json(row.get("details")); decision = d.get("decision") or {}
            if isinstance(decision, dict): decision = decision.get("decision") or decision.get("action")
            decision_detail = d.get("decision") if isinstance(d.get("decision"), dict) else {}
            print(f"  {row.get('ts')} meta={row.get('meta_id')} {event} decision={decision or d.get('action')} "
                  f"motivo={row.get('reason') or d.get('reason') or decision_detail.get('reason')}")
            if event == "META_OPERATOR_ROTATION_EVALUATED":
                print("    rotación:", json.dumps({k:d.get(k) for k in ("material", "streak", "improvement_pp", "destination", "reason") if k in d}, ensure_ascii=False))
            if event == "META_OPERATOR_ALLOCATION_SHADOW":
                discards=d.get("discards") or {}
                print("    asignación sombra:", json.dumps({"decision":d.get("decision"), "reason":d.get("reason"), "descartes":discards}, ensure_ascii=False))
        print("\nTiempos de los últimos planes:")
        for mid, plan in list(latest_plans.items())[:8]:
            print(f"  meta={mid}: {json.dumps(plan.get('timings_s') or {}, ensure_ascii=False)}")

        print("\nTamaños:")
        print(f"  archivo: {db_path.stat().st_size} bytes")
        try:
            sizes = conn.execute("SELECT name, SUM(pgsize) bytes FROM dbstat GROUP BY name ORDER BY bytes DESC LIMIT 12").fetchall()
            for row in sizes: print(f"  {row['name']}: {row['bytes']} bytes")
        except sqlite3.Error:
            print("  dbstat: no disponible")
        if "meta_analysis_cache" in tables:
            count = conn.execute("SELECT COUNT(*) FROM meta_analysis_cache").fetchone()[0]
            print(f"  meta_analysis_cache: {count} filas")

        print("\nAprendizaje y precálculo:")
        runs = _rows(conn, "meta_learning_runs", ("id", "started_at", "finished_at", "status", "summary_json"),
            "", (), "id DESC", 1)
        if runs:
            run=runs[0]; print(f"  última corrida: {run.get('started_at')} → {run.get('finished_at')} {run.get('status')}")
        else: print("  última corrida: no registrada")
        precompute = [(row.get("ts"), _json(row.get("details"))) for row in ledger
            if "PRECOMPUTE" in str(row.get("event_type", "")).upper() or "PRECACHE" in str(row.get("event_type", "")).upper()]
        print(f"  último precálculo registrado en ledger: {precompute[0][0] if precompute else 'no hay evento persistido'}")
        if "meta_analysis_cache" in tables:
            cols=_columns(conn,"meta_analysis_cache")
            if "created_at" in cols:
                cache=conn.execute("SELECT MAX(created_at) FROM meta_analysis_cache").fetchone()[0]
                print(f"  última escritura de caché (referencia de precálculo, no equivalente): {cache}")

        print("\nCobertura de señales (muestras recientes):")
        signal_counts=defaultdict(Counter); values=defaultdict(lambda: defaultdict(set))
        for row in _rows(conn,"meta_learning_samples",("symbol","sample_at","source_kind","signals_json"),
                "datetime(sample_at)>=datetime(?)",(cutoff,),"sample_at DESC",100000):
            payload=_json(row.get("signals_json"))
            for signal in payload.get("signals_used",[]):
                name=signal.get("name")
                if not name: continue
                signal_counts[name][row.get("symbol")]+=1
                if signal.get("normalized") is not None: values[name][row.get("symbol")].add(float(signal["normalized"]))
        for name in sorted(signal_counts):
            for symbol,count in sorted(signal_counts[name].items()):
                print(f"  {name} {symbol}: n={count}; normalizados distintos={sorted(values[name][symbol])}")
        if not signal_counts: print("  sin muestras en el intervalo")

    if journal:
        try:
            result=subprocess.run(["journalctl","-u","asple-predictor","--since",f"{hours} hours ago","--no-pager"],
                capture_output=True,text=True,timeout=30,check=True)
            lines=result.stdout.splitlines()
            exceptions=[line for line in lines if "raised an exception" in line.lower()]
            errors=[line for line in lines if "error" in line.lower() and "plotly" not in line.lower()]
            print(f"\nJournalctl ({hours} h): {len(exceptions)} 'raised an exception'; {len(errors)} ERROR (sin plotly)")
        except Exception as exc:
            print(f"\nJournalctl no disponible: {type(exc).__name__}: {exc}")
    return 0


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db",required=True,type=Path,help="archivo SQLite; abre URI mode=ro")
    parser.add_argument("--horas",type=float,default=2)
    parser.add_argument("--journal",action="store_true")
    args=parser.parse_args(argv)
    try: return verify(args.db,args.horas,args.journal)
    except (OSError,sqlite3.Error,ValueError) as exc:
        parser.exit(2,f"Error de verificación: {type(exc).__name__}: {exc}\n")

if __name__ == "__main__": raise SystemExit(main())
