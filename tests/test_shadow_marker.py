import json
import sqlite3
from datetime import datetime, timezone

from sqlalchemy import select

from data.market_store import CandleStore
from database.db_manager import DBManager
from grid.meta_operator import MetaOperator
from scripts.marcador_sombra import mark_mature_decisions
from scheduler.shadow_marker_job import ShadowMarkerJob


def _shadow_row(db, *, review_at, decision, pieces=None):
    with db.engine.begin() as conn:
        goal_id = conn.execute(db.meta_goals.insert().values(
            capital_initial=100, target_usdt=3, target_input_type="USDT",
            target_input_value=3, profile="RAPIDO", status="ACTIVE",
            environment="testnet", created_at=review_at.replace(tzinfo=None),
            config_snapshot=json.dumps({"max_days": 1, "max_loss_pct": 5}))).inserted_primary_key[0]
        decision_id = conn.execute(db.engine_shadow_decisions.insert().values(
            goal_id=goal_id, review_at=review_at,
            active_engine="v1", active_decision_json="{}", shadow_engine="v2",
            shadow_decision_json=json.dumps({"name": "v2", "decision": decision,
                "result": {"decision": decision, "pieces": pieces or []}}))).inserted_primary_key[0]
    return int(decision_id)


def test_marker_completa_decisiones_maduras_no_marca_inmaduras_y_es_idempotente(tmp_path):
    app_path = tmp_path / "app.sqlite"
    market_path = tmp_path / "market.sqlite"
    db = DBManager(f"sqlite:///{app_path.as_posix()}")
    store = CandleStore(market_path)
    start = int(datetime(2026, 10, 30, tzinfo=timezone.utc).timestamp())
    rows = []
    for index in range(2 * 288 + 1):
        stamp = start + index * 300
        rows.append({"open_ts": stamp, "open": 1.0, "high": 1.001,
            "low": .999, "close": 1.0, "volume": 1, "quote_volume": 1, "trades": 1})
    store.upsert("SYNTHUSDT", "5m", rows, "test")
    store.save_symbol_filters("SYNTHUSDT", {"filters": [
        {"filterType": "PRICE_FILTER", "minPrice": "0.0001", "maxPrice": "100000", "tickSize": "0.0001"},
        {"filterType": "LOT_SIZE", "minQty": "0.0001", "maxQty": "100000000", "stepSize": "0.0001"},
        {"filterType": "MIN_NOTIONAL", "minNotional": "0.01", "applyToMarket": True},
    ]})
    review = datetime.fromtimestamp(start, timezone.utc)
    selected_id = _shadow_row(db, review_at=review,
        decision="seleccionar_alternativa", pieces=[{"symbol": "SYNTHUSDT",
            "strategy": "simple", "capital_usdt": 25, "floor": .9,
            "ceiling": 1.1, "n_levels": 6}, {"symbol": "SYNTHUSDT",
            "strategy": "simple", "piece_type": "individual_bot_grid",
            "capital_usdt": 25, "floor": .98, "ceiling": 1.02, "n_levels": 1}])
    quiet_id = _shadow_row(db, review_at=review, decision="mantener_usdt")
    immature_id = _shadow_row(db, review_at=datetime.fromtimestamp(start + 6 * 3600,
        timezone.utc), decision="mantener_usdt")
    now = datetime.fromtimestamp(start + 26 * 3600, timezone.utc)

    result = mark_mature_decisions(app_path, market_path, now=now)
    assert set(result["marked"]) == {selected_id, quiet_id}
    assert immature_id in result["pending"]
    with db.engine.connect() as conn:
        rows = conn.execute(select(db.engine_shadow_decisions.c.id,
            db.engine_shadow_decisions.c.shadow_outcome_json)).all()
    outcomes = {int(row.id): json.loads(row.shadow_outcome_json)
        for row in rows if row.shadow_outcome_json}
    assert outcomes[selected_id]["simulator"] == "FastSim"
    assert "pnl_usdt" in outcomes[selected_id]
    assert outcomes[quiet_id]["source"] == "usdt_quieto"
    assert outcomes[quiet_id]["pnl_usdt"] == 0

    repeated = mark_mature_decisions(app_path, market_path, now=now)
    assert repeated["marked"] == []
    assert immature_id in repeated["pending"]
    db.engine.dispose()


def test_resultado_real_de_no_hacer_nada_usa_equity_real_de_la_meta(tmp_path):
    app_path = tmp_path / "app.sqlite"
    market_path = tmp_path / "market.sqlite"
    db = DBManager(f"sqlite:///{app_path.as_posix()}")
    store = CandleStore(market_path)
    start = int(datetime(2026, 10, 30, tzinfo=timezone.utc).timestamp())
    candles = [{"open_ts": start + index * 300, "open": 1, "high": 1,
        "low": 1, "close": 1, "volume": 1, "quote_volume": 1, "trades": 1}
        for index in range(289)]
    store.upsert("REALUSDT", "5m", candles, "test")
    decision_id = _shadow_row(db, review_at=datetime.fromtimestamp(start, timezone.utc),
        decision="no_hacer_nada")
    with db.engine.begin() as conn:
        goal_id = conn.execute(select(db.engine_shadow_decisions.c.goal_id).where(
            db.engine_shadow_decisions.c.id == decision_id)).scalar_one()
        conn.execute(db.grids.insert().values(symbol="REALUSDT", strategy="simple",
            status="active", capital_total=50, range_low=.9, range_high=1.1,
            n_levels=6, created_at=datetime.fromtimestamp(start - 100, timezone.utc)))
        grid_id = conn.execute(select(db.grids.c.id).order_by(db.grids.c.id.desc()).limit(1)).scalar_one()
        conn.execute(db.meta_piece_links.insert().values(meta_id=goal_id, grid_id=grid_id,
            piece_type="grid", created_at=datetime.fromtimestamp(start - 100, timezone.utc)))

    result = mark_mature_decisions(app_path, market_path,
        now=datetime.fromtimestamp(start + 26 * 3600, timezone.utc))
    assert result["marked"] == [decision_id]
    with db.engine.connect() as conn:
        raw = conn.execute(select(db.engine_shadow_decisions.c.shadow_outcome_json).where(
            db.engine_shadow_decisions.c.id == decision_id)).scalar_one()
    outcome = json.loads(raw)
    assert outcome["source"] == "meta_real"
    assert outcome["pnl_usdt"] == 0
    db.engine.dispose()


def test_job_diario_del_marcador_apagado_por_defecto_y_a_las_0420_utc(tmp_path):
    db = DBManager(f"sqlite:///{(tmp_path / 'app.sqlite').as_posix()}")
    operator = object.__new__(MetaOperator)
    operator.db = db
    assert operator.shadow_marker_status()["enabled"] is False
    assert operator.shadow_marker_status()["completed"] == 0

    class Scheduler:
        running = False

        def add_job(self, fn, trigger, **kwargs):
            self.job = (fn, trigger, kwargs)

        def start(self):
            self.running = True

        def shutdown(self, wait=True):
            self.running = False

    scheduler = Scheduler()
    calls = []
    job = ShadowMarkerJob(db, tmp_path / "market.sqlite", scheduler=scheduler,
        run=lambda *args, **kwargs: calls.append(args))
    assert job.enabled() is False
    assert job.run_if_enabled()["status"] == "disabled"
    assert calls == []
    operator.set_shadow_marker_enabled(True, confirm=True, actor="prueba")
    assert operator.shadow_marker_status()["enabled"] is True
    job.start()
    _fn, trigger, options = scheduler.job
    assert trigger == "cron"
    assert options["hour"] == 4 and options["minute"] == 20
    assert options["timezone"] == "UTC"
    job.stop()
    db.engine.dispose()
