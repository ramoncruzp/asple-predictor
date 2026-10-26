"""Estima el costo pendiente del backfill Meta. Solo consulta una BD existente."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from sqlalchemy import MetaData, create_engine, select
from sqlalchemy.engine import make_url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True, help="URL existente; no se lee .env")
    parser.add_argument("--segundos-por-ventana", type=float, default=0.05,
        help="estimaci?n local por replay; no ejecuta simulaciones")
    args = parser.parse_args()
    url = make_url(args.database_url)
    if url.get_backend_name() == "sqlite":
        path = Path(url.database or "").resolve()
        if not path.exists():
            raise SystemExit("La base indicada no existe; no se cre? ninguna base.")
        db_url = f"sqlite+pysqlite:///file:{path.as_posix()}?mode=ro&uri=true"
        engine = create_engine(db_url, future=True, connect_args={"uri": True})
    else:
        engine = create_engine(args.database_url, future=True)
        if engine.dialect.name == "postgresql":
            from sqlalchemy import event
            @event.listens_for(engine, "connect")
            def read_only(connection, _record):
                cursor = connection.cursor()
                cursor.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
                cursor.close()
    metadata = MetaData()
    metadata.reflect(engine, only=["meta_learning_samples", "meta_learning_replay_cache"])
    samples, cache = metadata.tables["meta_learning_samples"], metadata.tables.get("meta_learning_replay_cache")
    with engine.connect() as conn:
        live = conn.execute(select(samples.c.sample_key, samples.c.profile, samples.c.sample_at,
            samples.c.parameters_json).where(samples.c.source_kind == "live").order_by(
            samples.c.sample_at.desc())).all()
        historical = conn.execute(select(samples.c.sample_key, samples.c.provenance_json,
            samples.c.sample_at).where(samples.c.source_kind == "historical_reconstructed")).all()
        cache_count = conn.execute(select(cache.c.template_id).distinct()).all() if cache is not None else []
    templates = {}
    for row in live:
        params = json.loads(row.parameters_json or "{}")
        pieces = []
        for piece in params.get("pieces") or []:
            mid = max(float(piece.get("mid_at_plan") or 0), 1e-12)
            pieces.append({"symbol": str(piece.get("symbol") or "").upper(),
                "type": piece.get("piece_type"), "strategy": piece.get("strategy"),
                "n": piece.get("n_levels"),
                "floor_mid_q": round(round(float(piece.get("floor", 0)) / mid / .0025) * .0025, 4),
                "ceiling_mid_q": round(round(float(piece.get("ceiling", 0)) / mid / .0025) * .0025, 4),
                "capital_fraction": round(float(piece.get("capital_usdt") or 0) /
                    max(float(params.get("capital_usdt") or 1), 1e-12), 4)})
        shape = json.dumps({"profile": row.profile, "days": params.get("profile_days"),
            "pieces": pieces}, sort_keys=True, separators=(",", ":"))
        templates.setdefault(hashlib.sha256(shape.encode()).hexdigest(), {"profile_days":
            int(params.get("profile_days") or 20), "latest": str(row.sample_at), "existing": 0})
    for row in historical:
        try:
            template_id = json.loads(row.provenance_json or "{}").get("template_id")
        except (TypeError, ValueError):
            template_id = None
        if template_id in templates:
            templates[template_id]["existing"] += 1
    cuts_per_template = 60
    windows_per_cut = {key: max(0, int((180 - value["profile_days"]) / 7))
        for key, value in templates.items()}
    pending_cuts = sum(max(0, cuts_per_template - value["existing"]) for value in templates.values())
    windows_needed = sum(max(0, cuts_per_template - value["existing"]) * windows_per_cut[key]
        for key, value in templates.items())
    print(json.dumps({"read_only": True, "market_access": False, "database_writes": False,
        "templates": len(templates), "pending_cuts_estimate": pending_cuts,
        "replay_windows_needed_estimate": windows_needed,
        "cached_templates": len(cache_count),
        "estimated_seconds": round(windows_needed * max(0, args.segundos_por_ventana), 2),
        "assumptions": {"history_days": 180, "cut_stride_hours": 72,
            "replay_window_stride_days": 7, "seconds_per_window": args.segundos_por_ventana}},
        ensure_ascii=False, indent=2))
    engine.dispose()


if __name__ == "__main__":
    main()
