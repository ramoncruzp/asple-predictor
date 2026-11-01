import json
import os
import sqlite3
import subprocess
import sys

import pytest
from sqlalchemy import select

from data.market_store import CandleStore
from database.db_manager import DBManager
from grid.sim.runner import FILTERS
from scheduler.scenario_cache_job import ScenarioCacheJob
import scripts.escenarios_cache as cache_script


@pytest.fixture
def cache_databases(tmp_path):
    app_path = tmp_path / "app.sqlite"
    market_path = tmp_path / "market.sqlite"
    db = DBManager(f"sqlite:///{app_path.as_posix()}")
    CandleStore(market_path)
    yield db, market_path
    db.engine.dispose()


def _set_enabled(db, enabled):
    with db.engine.begin() as conn:
        raw = conn.execute(select(db.meta_profile_settings.c.settings).where(
            db.meta_profile_settings.c.profile == "GLOBAL")).scalar_one()
        values = json.loads(raw)
        values["scenario_cache_enabled"] = enabled
        conn.execute(db.meta_profile_settings.update().where(
            db.meta_profile_settings.c.profile == "GLOBAL").values(
                settings=json.dumps(values)))


def test_job_apagado_no_inicia_subproceso(cache_databases):
    db, market_path = cache_databases
    calls = []
    job = ScenarioCacheJob(db, market_path, run=lambda *args, **kwargs: calls.append(args))

    result = job.run_if_enabled()

    assert result["status"] == "disabled"
    assert calls == []


def test_json_cache_convierte_no_finitos_a_null():
    value = cache_script._json_dumps({"nan": float("nan"), "inf": float("inf"),
        "nested": [float("-inf"), 1.0]})
    assert value == '{"nan": null, "inf": null, "nested": [null, 1.0]}'
    assert json.loads(value) == {"nan": None, "inf": None, "nested": [None, 1.0]}


def test_cache_excluye_rol_datos_y_registra_motivos(tmp_path):
    market_path = tmp_path / "roles.sqlite"
    store = CandleStore(market_path)
    with store._connect() as conn:
        conn.execute("INSERT INTO symbols(symbol,role,active,added_at) "
            "VALUES('DATOSUSDT','datos',1,'sintetico')")
        conn.execute("INSERT INTO symbols(symbol,role,active,added_at) "
            "VALUES('SINBIBLIOTECAUSDT','operar',1,'sintetico')")

    filters, prices, sigmas, skipped = cache_script._read_market_inputs(store,
        library_dir=tmp_path, horizon_days=1)

    assert not filters and not prices and not sigmas
    assert skipped["DATOSUSDT"] == "rol datos; se requiere operar"
    assert skipped["SINBIBLIOTECAUSDT"] == "biblioteca de escenarios ausente"


def test_cache_evalua_todas_las_piezas_vivas_antes_de_alternativas(
        cache_databases, monkeypatch):
    import numpy as np
    db, market_path = cache_databases
    current = [
        {"candidate_key": "mantener:X", "symbol": "XUSDT", "strategy": "simple",
            "capital": 10, "n": 6, "low": .8, "high": 1.2, "is_current": True,
            "grid_id": 11, "grid_status": "PAUSED"},
        {"candidate_key": "mantener:Y", "symbol": "YUSDT", "strategy": "simple",
            "capital": 10, "n": 6, "low": .8, "high": 1.2, "is_current": True,
            "grid_id": 12, "grid_status": "ACTIVE"},
    ]
    alternatives = [
        {"candidate_key": "alternativa:X", "symbol": "XUSDT", "strategy": "simple",
            "capital": 10, "n": 6, "low": .7, "high": 1.3},
        {"candidate_key": "alternativa:Y", "symbol": "YUSDT", "strategy": "simple",
            "capital": 10, "n": 6, "low": .7, "high": 1.3},
    ]
    monkeypatch.setattr(cache_script, "_active_grid_pieces", lambda *_args: current)
    monkeypatch.setattr(cache_script, "build_candidates", lambda **_kwargs: {
        "candidates": [*alternatives, {"strategy": "usdt_quieto"}], "discarded": {}})
    monkeypatch.setattr(cache_script, "build_smart_candidates", lambda _rows: [])
    monkeypatch.setattr(cache_script, "rank_alternatives_v2", lambda rows, **_kwargs: [
        {"piece": row.get("piece", row), "objetivo": {"factible": True,
            "motivos": [], "clave_orden": [0, 0, 0]}} for row in rows])
    calls = []

    def evaluate(pieces, **_kwargs):
        calls.extend(piece["candidate_key"] for piece in pieces)
        rows = []
        for piece in pieces:
            pnl = [-.5] * 3 if piece.get("is_current") else [1.5] * 3
            base = float(piece["capital"])
            path = np.repeat(np.asarray([base + pnl[0]], dtype=np.float32)[:, None], 3, axis=1)
            rows.append({"piece": piece, "metrics": {"pnl_mean_usdt": 0,
                "p_target": .5, "p_loss": 0}, "scenario_outcomes": {
                    "pnl_usdt": pnl, "loss_cut": [False] * 3,
                    "reached": [False] * 3, "days": [None if piece.get("is_current") else 2] * 3,
                    "inventory_qty": [1 if piece.get("is_current") else 0] * 3,
                    "inventory_value": [10 if piece.get("is_current") else 0] * 3,
                    "trajectory_cash_f32": path, "trajectory_equity_f32": path,
                    "trajectory_timestamps": np.asarray([0, 3600, 7200])}})
        return {"pieces": rows}

    result = cache_script._evaluate_context({"goal_id": 7, "profile": "RAPIDO",
        "free_cash": 20, "capital": 20, "target": 1.1, "horizon": 15,
        "max_loss_pct": 18, "balance_approximate": False},
        store=CandleStore(market_path), app_db=db.engine.url.database,
        market_path=market_path, library_dir="unused", filters={"XUSDT": object(),
            "YUSDT": object()}, prices={"XUSDT": 1, "YUSDT": 1},
        sigmas={"XUSDT": .01, "YUSDT": .01}, skipped={}, evaluator=evaluate)

    assert calls == ["mantener:X", "mantener:Y", "alternativa:X", "alternativa:Y"]
    assert result["live_piece_count"] == 2
    assert result["live_pieces"] == [{"grid_id": 11, "status": "PAUSED"},
        {"grid_id": 12, "status": "ACTIVE"}]
    alternative = next(row for row in result["results"]
        if row["candidate_key"] == "alternativa:X")
    assert alternative["metrics"]["p_target"] == 0
    assert alternative["metrics"]["metrics_pieza_sola"]["p_target"] == .5


def test_cache_guarda_solo_doce_mejores_trayectorias_y_mide_rss(
        cache_databases, monkeypatch, tmp_path):
    import numpy as np
    db, market_path = cache_databases
    pieces = [{"candidate_key": f"candidate-{index}", "symbol": "XUSDT",
        "strategy": "simple", "capital": 10, "n": 6, "low": .8, "high": 1.2}
        for index in range(13)]
    monkeypatch.setattr(cache_script, "build_candidates", lambda **_kwargs: {
        "candidates": [*pieces, {"strategy": "usdt_quieto"}], "discarded": {}})
    monkeypatch.setattr(cache_script, "build_smart_candidates", lambda _rows: [])
    timestamps = np.arange(480, dtype=np.int64) * 3600

    def evaluate(batch, **_kwargs):
        rows = []
        for index, piece in enumerate(batch):
            path = np.full((1000, 480), 10 + index / 100,
                dtype=np.float32)
            rows.append({"piece": piece, "metrics": {"n": 1000,
                "p_target": index / 20, "p_loss": 0,
                "p_loss_ci95": [0, .01], "pnl_mean_usdt": 1 + index,
                "pnl_mean_ci95": [.5 + index, 1.5 + index],
                "cut_slippage_mean_usdt": 0, "days_to_target_p50": 7},
                "scenario_outcomes": {"pnl_usdt": np.ones(1000),
                    "loss_cut": np.zeros(1000, dtype=bool),
                    "reached": np.zeros(1000, dtype=bool),
                    "days": np.full(1000, 7.0), "inventory_qty": np.zeros(1000),
                    "inventory_value": np.zeros(1000),
                    "trajectory_cash_f32": path,
                    "trajectory_equity_f32": path,
                    "trajectory_timestamps": timestamps}})
        return {"pieces": rows}

    rss_before = cache_script._rss_current_mb()
    result = cache_script._evaluate_context({"goal_id": 77, "profile": "RAPIDO",
        "capital": 100, "capital_initial": 100, "free_cash": 100,
        "target": 3, "horizon": 15, "max_loss_pct": 18},
        store=CandleStore(market_path), app_db=db.engine.url.database,
        market_path=market_path, library_dir="unused", filters={"XUSDT": object()},
        prices={"XUSDT": 1}, sigmas={"XUSDT": .02}, skipped={}, evaluator=evaluate,
        trajectory_path=tmp_path / "run.npz")
    rss_after = cache_script._rss_current_mb()
    print(f"RSS durante archivo sintético: inicio {rss_before:.1f} MB, final {rss_after:.1f} MB")

    assert result["trajectory_file"]
    with np.load(result["trajectory_file"], allow_pickle=False) as archive:
        metadata = json.loads(str(archive["metadata_json"].item()))
        assert len(metadata) == 12
        assert archive["equity_0"].shape == (1000, 480)
        assert not archive["equity_0"].dtype.hasobject
    assert max(rss_before, rss_after) < 600


def test_perfil_libera_por_moneda_y_conserva_doce_finalistas_globales(
        cache_databases, monkeypatch, tmp_path):
    import time
    import numpy as np
    db, market_path = cache_databases
    symbols = [f"SYN{index}USDT" for index in range(5)]
    candidates = [{"candidate_key": f"{symbol}-{index}", "symbol": symbol,
        "strategy": "simple", "capital": 10, "n": 6, "low": 90, "high": 110}
        for symbol in symbols for index in range(3)]
    monkeypatch.setattr(cache_script, "build_candidates", lambda **_kwargs: {
        "candidates": [*candidates, {"strategy": "usdt_quieto"}], "discarded": {}})
    monkeypatch.setattr(cache_script, "build_smart_candidates", lambda _rows: [])
    monkeypatch.setattr(cache_script, "rank_alternatives_v2", lambda rows, **_kwargs: [
        {"piece": row.get("piece", row), "objetivo": {"factible": True,
            "motivos": [], "clave_orden": [0, 0, 0]}} for row in rows])
    timestamps = np.arange(361, dtype=np.int64) * 3600
    timestamps_5m = np.arange(4320, dtype=np.int64) * 300

    def evaluate(pieces, **_kwargs):
        assert len(pieces) <= cache_script.MAX_PIECES_PER_BATCH
        rows = []
        for piece in pieces:
            value = float(piece["capital"])
            rows.append({"piece": piece, "metrics": {"n": 1000,
                "p_target": .5, "p_loss": 0, "p_loss_ci95": [0, 0],
                "pnl_mean_usdt": 1, "pnl_mean_ci95": [1, 1],
                "cut_slippage_mean_usdt": 0, "cut_slippage_p90_usdt": 0,
                "days_to_target_p50": 5, "p_inventory": .2},
                "scenario_outcomes": {"pnl_usdt": np.ones(1000, dtype=np.float32),
                    "loss_cut": np.zeros(1000, dtype=bool),
                    "reached": np.zeros(1000, dtype=bool),
                    "days": np.full(1000, 5, dtype=np.float32),
                    "inventory_qty": np.zeros(1000, dtype=np.float32),
                    "inventory_value": np.zeros(1000, dtype=np.float32),
                    "trajectory_cash_f32": np.full((1000, 361), value, dtype=np.float32),
                    "trajectory_equity_f32": np.full((1000, 361), value, dtype=np.float32),
                    "trajectory_timestamps": timestamps,
                    "trajectory_low_equity_5m_f32": np.full((1000, 4320), value,
                        dtype=np.float32), "trajectory_timestamps_5m": timestamps_5m}})
        return {"pieces": rows}

    started = time.perf_counter()
    result = cache_script._evaluate_context({"goal_id": None, "profile": "RAPIDO",
        "free_cash": 100, "capital": 100, "capital_initial": 100,
        "target": 3, "horizon": 15, "max_loss_pct": 18,
        "profile_config": {}}, store=CandleStore(market_path),
        app_db=db.engine.url.database, market_path=market_path, library_dir=tmp_path,
        filters={symbol: object() for symbol in symbols},
        prices={symbol: 1 for symbol in symbols}, sigmas={symbol: .01 for symbol in symbols},
        skipped={}, evaluator=evaluate, trajectory_path=tmp_path / "profile.npz")
    elapsed = time.perf_counter() - started

    assert set(result["rss_por_moneda"]) == set(symbols)
    assert all(row["rss_hwm_mb"] < 700 for row in result["rss_por_moneda"].values())
    assert result["evaluated_count"] == 15
    assert elapsed < 30
    with np.load(result["trajectory_file"], allow_pickle=False) as archive:
        assert len(json.loads(str(archive["metadata_json"].item()))) == 12
    print(f"RSS por moneda perfil 5 monedas: {result['rss_por_moneda']}; "
        f"pico={max(row['rss_hwm_mb'] for row in result['rss_por_moneda'].values()):.2f} MB; "
        f"tiempo={elapsed:.3f} s; finalistas=12")


def test_mantener_fallido_marca_alternativas_sin_baseline(cache_databases, monkeypatch):
    db, market_path = cache_databases
    current = {"candidate_key": "mantener:JUP", "symbol": "JUPUSDT",
        "strategy": "smart", "capital": 40, "n": 6, "low": .254,
        "high": .318, "is_current": True, "grid_id": 91, "grid_status": "PAUSED",
        "initial_cells": {"cells": []}}
    alternative = {"candidate_key": "alternativa:JUP", "symbol": "JUPUSDT",
        "strategy": "simple", "capital": 10, "n": 6, "low": .25, "high": .32}
    monkeypatch.setattr(cache_script, "_active_grid_pieces", lambda *_args: [current])
    monkeypatch.setattr(cache_script, "build_candidates", lambda **_kwargs: {
        "candidates": [alternative, {"strategy": "usdt_quieto"}], "discarded": {}})
    monkeypatch.setattr(cache_script, "build_smart_candidates", lambda _rows: [])
    monkeypatch.setattr(cache_script, "rank_alternatives_v2", lambda rows, **_kwargs: [
        {"piece": row.get("piece", row), "objetivo": {"factible": True,
            "motivos": [], "clave_orden": [0, 0, 0]}} for row in rows])
    calls = []

    def evaluate(pieces, **_kwargs):
        calls.extend(piece["candidate_key"] for piece in pieces)
        raise ValueError("exchange mid price must be strictly inside the grid range")

    result = cache_script._evaluate_context({"goal_id": 7, "profile": "RAPIDO",
        "free_cash": 50, "capital": 90, "target": 3, "horizon": 15,
        "max_loss_pct": 18, "balance_approximate": False},
        store=CandleStore(market_path), app_db=db.engine.url.database,
        market_path=market_path, library_dir="unused", filters={"JUPUSDT": FILTERS},
        prices={"JUPUSDT": .24}, sigmas={"JUPUSDT": .01}, skipped={}, evaluator=evaluate)

    assert calls == ["mantener:JUP"]
    maintain = next(row for row in result["results"] if row["candidate_key"] == "mantener:JUP")
    alternative_result = next(row for row in result["results"]
        if row["candidate_key"] == "alternativa:JUP")
    assert "mantener_fallido" in maintain["metrics"]
    assert alternative_result["metrics"]["sin_baseline"] is True
    assert alternative_result["objective"]["factible"] is False
    assert "sin_baseline" in alternative_result["objective"]["motivos"]


def test_fixture_s1_separa_ordenes_historicas_y_piezas_cerradas_vivas(cache_databases):
    db, _market_path = cache_databases
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with db.engine.begin() as conn:
        goal_id = conn.execute(db.meta_goals.insert().values(
            capital_initial=100, target_usdt=3, target_input_type="USDT",
            target_input_value=3, profile="RAPIDO", status="ACTIVE",
            environment="testnet", created_at=now, started_at=now,
            config_snapshot=json.dumps({"max_loss_pct": 18}))).inserted_primary_key[0]
        run_id = conn.execute(db.meta_runs.insert().values(meta_id=goal_id,
            status="ACTIVE", started_at=now, realized_extra_usdt=0)).inserted_primary_key[0]
    grid12_levels = [{"level_idx": idx, "price": .25, "capital": 50 / 6,
        "sell_price": None, "entry_price": None, "held_qty": 0, "state": "DONE",
        "order_id": 1200 + idx, "client_order_id": f"historico-s-{idx}",
        "buy_client_order_id": f"historico-b-{idx}"} for idx in range(6)]
    grid12 = db.create_grid_with_levels({"symbol": "JUPUSDT", "strategy": "smart",
        "range_low": .2, "range_high": .3, "n_levels": 6, "capital_total": 70,
        "status": "CLOSED", "environment": "testnet"}, grid12_levels)
    grid13_levels = [{"level_idx": idx, "price": .254, "capital": 70 / 6,
        "sell_price": (.2646, .2753, .286, .2966)[idx] if idx < 4 else None,
        "entry_price": .254 if idx < 4 else None,
        "held_qty": 42.575 if idx < 4 else 0,
        "state": "SELL_OPEN" if idx < 4 else "IDLE",
        "order_id": 1300 + idx if idx < 4 else None} for idx in range(6)]
    paused = db.create_grid_with_levels({"symbol": "JUPUSDT", "strategy": "smart",
        "range_low": .254, "range_high": .318, "n_levels": 6, "capital_total": 70,
        "status": "PAUSED", "environment": "testnet"}, grid13_levels)
    released = db.create_grid_with_levels({"symbol": "JUPUSDT", "strategy": "smart",
        "range_low": .2, "range_high": .3, "n_levels": 4, "capital_total": 50,
        "status": "PAUSED", "environment": "testnet"}, [{"level_idx": 0,
        "price": .25, "capital": 12.5, "sell_price": None, "entry_price": None,
        "held_qty": 0, "state": "IDLE"}])
    with db.engine.begin() as conn:
        link_ids = []
        for grid in (grid12, paused, released):
            link_ids.append(conn.execute(db.meta_piece_links.insert().values(
                meta_id=goal_id, run_id=run_id, grid_id=grid["id"],
                piece_type="grid", created_at=now)).inserted_primary_key[0])
        conn.execute(db.meta_piece_links.update().where(
            db.meta_piece_links.c.id == link_ids[2]).values(released_at=now))
        conn.execute(db.grid_events.insert().values(grid_id=paused["id"],
            source="fixture", ts=now, event_type="BUY_FILLED", price=.254,
            details=json.dumps({"quote_qty": 44, "fee_usdt": .044})))

    pieces = cache_script._active_grid_pieces(db.engine.url.database, goal_id,
        {"JUPUSDT": .24}, {"JUPUSDT": FILTERS})

    assert [piece["grid_id"] for piece in pieces] == [paused["id"]]
    assert pieces[0]["grid_status"] == "PAUSED"
    assert "Smart pausado" in pieces[0]["simulation_approximation"]
    assert pieces[0]["initial_cells"]["wait_outside_range"] is True
    assert pieces[0]["initial_cells"]["cash_usdt"] == pytest.approx(25.956)
    assert pieces[0]["capital"] == pytest.approx(25.956 + 170.3 * .24 * .999)
    assert [cell["sell_price"] for cell in pieces[0]["initial_cells"]["cells"][:4]] == [
        .2646, .2753, .286, .2966]
    assert pieces[0]["inventory_value_executable"] == pytest.approx(170.3 * .24 * .999)
    assert "mejor bid no disponible" in pieces[0]["valuation_warning"]


def test_grid_terminal_con_inventario_o_venta_abierta_sigue_vivo(cache_databases):
    db, _market_path = cache_databases
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with db.engine.begin() as conn:
        goal_id = conn.execute(db.meta_goals.insert().values(
            capital_initial=100, target_usdt=3, target_input_type="USDT",
            target_input_value=3, profile="RAPIDO", status="ACTIVE",
            environment="testnet", created_at=now, started_at=now,
            config_snapshot=json.dumps({"max_loss_pct": 18}))).inserted_primary_key[0]
        run_id = conn.execute(db.meta_runs.insert().values(meta_id=goal_id,
            status="ACTIVE", started_at=now, realized_extra_usdt=0)).inserted_primary_key[0]
    terminal_grids = [db.create_grid_with_levels({"symbol": "JUPUSDT",
        "strategy": "smart", "range_low": .2, "range_high": .3,
        "n_levels": 1, "capital_total": 10, "status": "CLOSED",
        "environment": "testnet"}, [level]) for level in (
            {"level_idx": 0, "price": .25, "capital": 10, "sell_price": None,
                "entry_price": .25, "held_qty": 2, "state": "DONE"},
            {"level_idx": 0, "price": .25, "capital": 10, "sell_price": .27,
                "entry_price": None, "held_qty": 0, "state": "SELL_OPEN"})]
    with db.engine.begin() as conn:
        for grid in terminal_grids:
            conn.execute(db.meta_piece_links.insert().values(meta_id=goal_id,
                run_id=run_id, grid_id=grid["id"], piece_type="grid", created_at=now))
    pieces = cache_script._active_grid_pieces(db.engine.url.database, goal_id,
        {"JUPUSDT": .26})
    assert [piece["grid_id"] for piece in pieces] == [
        grid["id"] for grid in terminal_grids]


def test_capital_vivo_excedido_usa_liquidacion_y_registra_aviso():
    pieces = [{"capital": 70, "liquidation_value": 22},
        {"capital": 70, "liquidation_value": 22}]

    warning = cache_script._fit_live_piece_capital(pieces, capital=99, free_cash=55)

    assert [piece["capital"] for piece in pieces] == pytest.approx([22, 22])
    assert [piece["capital_nominal"] for piece in pieces] == [70, 70]
    assert warning["capital_nominal_usdt"] == 140
    assert warning["capital_liquidacion_usdt"] == 44
    assert warning["capital_aplicado_usdt"] == 44


def test_corrida_persiste_ids_y_estado_de_piezas_vivas(cache_databases, monkeypatch):
    db, market_path = cache_databases
    context = {"goal_id": 7, "profile": "RAPIDO", "free_cash": 55,
        "capital": 99, "target": 1.1, "horizon": 15,
        "max_loss_pct": 18, "balance_approximate": False}
    current = {"candidate_key": "mantener:7:31", "symbol": "JUPUSDT",
        "strategy": "smart", "capital": 140, "liquidation_value": 44,
        "n": 4, "low": .2, "high": .3,
        "grid_id": 31, "grid_status": "PAUSED", "is_current": True}
    alternative = {"candidate_key": "alternativa:jup", "symbol": "JUPUSDT",
        "strategy": "simple", "capital": 5, "n": 6, "low": .21, "high": .29}
    monkeypatch.setattr(cache_script, "_contexts", lambda *_args: [context])
    monkeypatch.setattr(cache_script, "_read_market_inputs", lambda *_args, **_kwargs:
        ({"JUPUSDT": object()}, {"JUPUSDT": .25}, {"JUPUSDT": .02}, {}))
    monkeypatch.setattr(cache_script, "_active_grid_pieces", lambda *_args: [current])
    monkeypatch.setattr(cache_script, "build_candidates", lambda **_kwargs: {
        "candidates": [alternative, {"strategy": "usdt_quieto"}], "discarded": {}})
    monkeypatch.setattr(cache_script, "build_smart_candidates", lambda _rows: [])
    monkeypatch.setattr(cache_script, "rank_alternatives_v2", lambda rows, **_kwargs: [
        {"piece": row.get("piece", row), "objetivo": {"factible": True,
            "motivos": [], "clave_orden": [0, 0, 0]}} for row in rows])

    def evaluate(pieces, **_kwargs):
        rows = []
        for piece in pieces:
            pnl = [-.5] * 3 if piece.get("is_current") else [1.5] * 3
            rows.append({"piece": piece, "metrics": {"n": 3, "p_target": .5,
                "p_loss": 0, "pnl_mean_usdt": 0}, "scenario_outcomes": {
                    "pnl_usdt": pnl, "loss_cut": [False] * 3,
                    "reached": [False] * 3, "days": [None] * 3,
                    "inventory_qty": [1 if piece.get("is_current") else 0] * 3,
                    "inventory_value": [10 if piece.get("is_current") else 0] * 3}})
        return {"pieces": rows}

    result = cache_script.run_cache(db.engine.url.database, market_path,
        library_dir="unused", evaluator=evaluate, max_seconds=30)

    assert result["runs"][0]["status"] == "ok"
    assert result["runs"][0]["live_piece_count"] == 1
    with sqlite3.connect(market_path) as conn:
        status, error, rss_current, rss_hwm = conn.execute(
            "SELECT status,error,rss_current_mb,rss_hwm_mb FROM scenario_eval_runs").fetchone()
    details = json.loads(error)
    assert status == "ok"
    assert details["live_piece_count"] == 1
    assert details["live_pieces"] == [{"grid_id": 31, "status": "PAUSED"}]
    assert details["live_capital_warning"]["capital_aplicado_usdt"] == 44
    assert rss_current is not None and rss_hwm is not None
    assert rss_hwm >= rss_current


def test_cache_omite_smart_si_quedan_menos_de_120_segundos(
        cache_databases, monkeypatch):
    db, market_path = cache_databases
    candidates = [
        {"candidate_key": "simple-a", "symbol": "XUSDT", "strategy": "simple",
            "capital": 10, "n": 6, "low": .8, "high": 1.2},
        {"candidate_key": "simple-b", "symbol": "XUSDT", "strategy": "simple",
            "capital": 10, "n": 6, "low": .7, "high": 1.3},
    ]
    monkeypatch.setattr(cache_script, "_active_grid_pieces", lambda *_args: [])
    monkeypatch.setattr(cache_script, "build_candidates", lambda **_kwargs: {
        "candidates": [*candidates, {"strategy": "usdt_quieto"}], "discarded": {}})
    monkeypatch.setattr(cache_script, "build_smart_candidates", lambda _rows: [
        {"candidate_key": "smart-a", "symbol": "XUSDT", "strategy": "smart",
            "capital": 10, "n": 6, "low": .8, "high": 1.2},
        {"candidate_key": "smart-b", "symbol": "XUSDT", "strategy": "smart",
            "capital": 10, "n": 6, "low": .7, "high": 1.3},
    ])
    monkeypatch.setattr(cache_script, "rank_alternatives_v2", lambda rows, **_kwargs: [
        {"piece": row.get("piece", row), "objetivo": {"factible": True,
            "motivos": [], "clave_orden": [0, 0, 0]}} for row in rows])

    result = cache_script._evaluate_context({"goal_id": None, "profile": "RAPIDO",
        "free_cash": 20, "capital": 20, "target": 1, "horizon": 15,
        "max_loss_pct": 18, "balance_approximate": False},
        store=CandleStore(market_path), app_db=db.engine.url.database,
        market_path=market_path, library_dir="unused", filters={"XUSDT": object()},
        prices={"XUSDT": 1}, sigmas={"XUSDT": .01}, skipped={},
        evaluator=lambda pieces, **_kwargs: {"pieces": [
            {"piece": piece, "metrics": {"pnl_mean_usdt": 0,
                "p_target": .5, "p_loss": 0}} for piece in pieces]},
        remaining_seconds=lambda: 119)

    assert result["evaluated_count"] == 2
    assert result["smart_omitido_por_tiempo"] == 2


def test_timeout_queda_registrado_en_la_corrida(cache_databases, monkeypatch):
    db, market_path = cache_databases
    _set_enabled(db, True)
    monkeypatch.setattr(ScenarioCacheJob, "_context_selectors", lambda _self: ["1"])
    with sqlite3.connect(market_path) as conn:
        conn.execute("INSERT INTO scenario_eval_runs(run_id,as_of,profile,horizon_days,"
            "n_scenarios,status) VALUES('run-timeout','2026-10-30T00:00:00+00:00',"
            "'RAPIDO',20,1000,'running')")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    job = ScenarioCacheJob(db, market_path, run=timeout)
    result = job.run_if_enabled()

    assert result["status"] == "timeout"
    with sqlite3.connect(market_path) as conn:
        status = conn.execute("SELECT status FROM scenario_eval_runs WHERE run_id='run-timeout'").fetchone()[0]
    assert status == "timeout"


def test_importar_job_no_importa_numpy_ni_evaluador_en_servicio():
    code = ("import sys; import scheduler.scenario_cache_job; "
        "assert 'numpy' not in sys.modules; "
        "assert 'research.scenarios.evaluate' not in sys.modules")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True,
        text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_job_fija_hilos_y_no_usa_rlimit_ni_preexec(cache_databases, monkeypatch):
    db, market_path = cache_databases
    _set_enabled(db, True)
    monkeypatch.setattr(ScenarioCacheJob, "_context_selectors", lambda _self: ["1"])
    captured = {}

    def run(args, **kwargs):
        captured.update(kwargs)
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, "ok", "")

    result = ScenarioCacheJob(db, market_path, run=run).run_if_enabled()

    assert result["status"] == "completed"
    assert captured["env"]["OPENBLAS_NUM_THREADS"] == "1"
    assert captured["env"]["OMP_NUM_THREADS"] == "1"
    assert captured["env"]["MALLOC_ARENA_MAX"] == "2"
    assert "preexec_fn" not in captured
    assert float(captured["args"][captured["args"].index("--max-segundos-total") + 1]) > 1498
    assert captured["args"][captured["args"].index("--contexto") + 1] == "1"
    assert "RLIMIT_AS" not in open("scheduler/scenario_cache_job.py", encoding="utf-8").read()


@pytest.mark.parametrize(("returncode", "expected"), [(3, "memoria"), (4, "timeout")])
def test_job_conserva_estado_de_limite_del_script(cache_databases, returncode, expected, monkeypatch):
    db, market_path = cache_databases
    _set_enabled(db, True)
    monkeypatch.setattr(ScenarioCacheJob, "_context_selectors", lambda _self: ["1"])
    def run(args, **_kwargs):
        return subprocess.CompletedProcess(args, returncode, "", "límite del script")

    result = ScenarioCacheJob(db, market_path, run=run).run_if_enabled()

    assert result["status"] == expected


@pytest.mark.parametrize(("returncode", "expected"), [(3, "memoria"), (4, "timeout")])
def test_job_conserva_estado_de_limite_del_script(cache_databases, returncode, expected, monkeypatch):
    db, market_path = cache_databases
    _set_enabled(db, True)
    monkeypatch.setattr(ScenarioCacheJob, "_context_selectors", lambda _self: ["1"])
    def run(args, **_kwargs):
        return subprocess.CompletedProcess(args, returncode, "", "límite del script")

    result = ScenarioCacheJob(db, market_path, run=run).run_if_enabled()

    assert result["status"] == expected


def test_rss_superior_al_limite_marca_corrida_memoria(cache_databases, tmp_path, monkeypatch):
    db, market_path = cache_databases
    _set_enabled(db, True)
    monkeypatch.setenv("ASPLE_SCENARIO_CACHE_MAX_RSS_MB", "600")
    monkeypatch.setattr(ScenarioCacheJob, "_context_selectors", lambda _self: ["1"])
    with sqlite3.connect(market_path) as conn:
        conn.execute("INSERT INTO scenario_eval_runs(run_id,as_of,profile,horizon_days,"
            "n_scenarios,status) VALUES('run-memory','2026-10-30T00:00:00+00:00',"
            "'RAPIDO',20,1000,'running')")
    child = tmp_path / "slow_child.py"
    child.write_text("import time; time.sleep(10)\n", encoding="utf-8", newline="\n")
    job = ScenarioCacheJob(db, market_path, script_path=child,
        rss_reader=lambda _pid: 601 * 1024 * 1024)

    result = job.run_if_enabled()

    assert result["status"] == "memoria"
    with sqlite3.connect(market_path) as conn:
        status = conn.execute("SELECT status FROM scenario_eval_runs "
            "WHERE run_id='run-memory'").fetchone()[0]
    assert status == "memoria"


def test_tope_memoria_es_900_por_defecto_y_configurable(monkeypatch):
    monkeypatch.delenv("ASPLE_SCENARIO_CACHE_MAX_RSS_MB", raising=False)
    assert cache_script._configured_max_rss_mb() == 900
    from scheduler import scenario_cache_job
    assert scenario_cache_job._memory_limit_bytes() == 900 * 1024 * 1024
    monkeypatch.setenv("ASPLE_SCENARIO_CACHE_MAX_RSS_MB", "750")
    assert cache_script._configured_max_rss_mb() == 750
    assert scenario_cache_job._memory_limit_bytes() == 750 * 1024 * 1024


def test_contexto_perfil_admite_alias_sin_prefijo(monkeypatch, capsys):
    seen = {}
    def run_cache(_app_db, _market_db, **kwargs):
        seen["context_selector"] = kwargs["context_selector"]
        return {"contexts": 1, "runs": [{"status": "ok"}],
            "seconds": .01, "rss_mb": 10}
    monkeypatch.setattr(cache_script, "run_cache", run_cache)
    assert cache_script.main(["--app-db", "app.sqlite", "--market-db", "market.sqlite",
        "--contexto", "RAPIDO"]) == 0
    assert seen["context_selector"] == "RAPIDO"
    assert "estado=completed" in capsys.readouterr().out


def test_contexto_meta_calcula_efectivo_valor_inventario_y_objetivo(cache_databases):
    db, _market_path = cache_databases
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with db.engine.begin() as conn:
        goal_id = conn.execute(db.meta_goals.insert().values(
            capital_initial=100, target_usdt=10, target_input_type="USDT",
            target_input_value=10, profile="RAPIDO", status="ACTIVE",
            environment="testnet", created_at=now, started_at=now,
            config_snapshot=json.dumps({"max_loss_pct": 18}))).inserted_primary_key[0]
    grid = db.create_grid_with_levels({"symbol": "XRPUSDT", "range_low": .8,
        "range_high": 1.2, "n_levels": 6, "capital_total": 40,
        "status": "active", "environment": "testnet"}, [{"level_idx": 0,
        "price": 1, "capital": 40, "sell_price": 1.1,
        "entry_price": 1, "held_qty": 40, "state": "HOLDING"}])
    with db.engine.begin() as conn:
        run_id = conn.execute(db.meta_runs.insert().values(meta_id=goal_id,
            status="ACTIVE", started_at=now, realized_extra_usdt=0)).inserted_primary_key[0]
        conn.execute(db.meta_piece_links.insert().values(meta_id=goal_id,
            run_id=run_id, grid_id=grid["id"], piece_type="grid", created_at=now))
    db.add_grid_event(run_id=grid.get("run_id"), source="MONITOR",
        event_type="BUY_FILLED", grid_id=grid["id"], price=1,
        details={"quote_qty": 40, "fee_usdt": .04})

    context = cache_script._contexts(db.engine.url.database, {"XRPUSDT": 1})[0]

    assert context["free_cash"] == pytest.approx(59.96)
    assert context["capital"] == pytest.approx(99.92004)
    assert context["target"] == pytest.approx(10.08)
    assert context["horizon"] == 20
    assert context["balance_approximate"] is False


def test_meta_objetivo_restante_suma_perdidas_y_resta_ganancias():
    assert cache_script._remaining_target(3, 99.1, 100) == pytest.approx(3.9)
    assert cache_script._remaining_target(3, 101.5, 100) == pytest.approx(1.5)
    assert cache_script._remaining_target(3, 104, 100) == pytest.approx(0)


def test_alternativa_usa_cartera_pareada_con_pieza_viva():
    import numpy as np
    current = {"piece": {"capital": 40, "is_current": True},
        "metrics": {"p_target": 0}, "scenario_outcomes": {
            "pnl_usdt": [-1.0, -1.0, -1.0], "loss_cut": [False] * 3,
            "reached": [False] * 3, "days": [None] * 3,
                "inventory_qty": [1, 1, 1], "inventory_value": [10, 10, 10],
                "trajectory_cash_f32": np.full((3, 2), 39, dtype=np.float32),
                "trajectory_equity_f32": np.full((3, 2), 39, dtype=np.float32),
                "trajectory_timestamps": np.asarray([0, 3600])}}
    candidate = {"piece": {"capital": 50, "is_current": False},
        "metrics": {"p_target": 1}, "scenario_outcomes": {
            "pnl_usdt": [3.0, 3.0, 3.0], "loss_cut": [False] * 3,
            "reached": [True] * 3, "days": [2, 2, 2],
                "inventory_qty": [0, 0, 0], "inventory_value": [0, 0, 0],
                "trajectory_cash_f32": np.full((3, 2), 53, dtype=np.float32),
                "trajectory_equity_f32": np.full((3, 2), 53, dtype=np.float32),
                "trajectory_timestamps": np.asarray([0, 3600])}}

    cache_script._apply_live_portfolio_metrics([current, candidate], [current],
        capital=100, target=2.1, max_loss_pct=18, horizon=15)

    assert candidate["metrics_pieza_sola"]["p_target"] == 1
    assert candidate["metrics"]["p_target"] == 0
    assert candidate["metrics"]["pnl_mean_usdt"] == pytest.approx(2)


def test_corrida_completa_persiste_candidatos_y_usdt_quieto(cache_databases, tmp_path,
        monkeypatch):
    db, market_path = cache_databases
    with db.engine.begin() as conn:
        row = conn.execute(select(db.meta_profile_settings.c.settings).where(
            db.meta_profile_settings.c.profile == "PRUDENTE")).scalar_one()
        values = json.loads(row)
        values["decision_engine"] = "v2"
        conn.execute(db.meta_profile_settings.update().where(
            db.meta_profile_settings.c.profile == "PRUDENTE").values(
                settings=json.dumps(values)))
    piece = {"candidate_key": "simple-test", "symbol": "JUPUSDT",
        "strategy": "simple", "capital": 50.0, "n": 6,
        "low": .2, "high": .3}
    invalid_piece = {"candidate_key": "simple-invalid", "symbol": "JUPUSDT",
        "strategy": "simple", "capital": 25.0, "n": 10, "low": .2, "high": .3}
    monkeypatch.setattr(cache_script, "_read_market_inputs",
        lambda _store, **_kwargs: ({"JUPUSDT": object()}, {"JUPUSDT": .25},
            {"JUPUSDT": .04}, {}))
    monkeypatch.setattr(cache_script, "build_candidates",
        lambda **_kwargs: {"candidates": [piece, invalid_piece, {"strategy": "usdt_quieto"}],
            "discarded": {}})
    monkeypatch.setattr(cache_script, "build_smart_candidates", lambda _rows: [])
    monkeypatch.setattr(cache_script, "rank_alternatives_v2",
        lambda rows, **_kwargs: [{"piece": row.get("piece", row),
            "objetivo": {"factible": True, "motivos": [], "clave_orden": [0, 0, 0]}}
            for row in rows])

    def evaluate(pieces, **_kwargs):
        if any(candidate["candidate_key"] == "simple-invalid" for candidate in pieces):
            raise ValueError("level 5 net sell does not meet quantity/notional filters")
        return {"pieces": [{"piece": candidate, "metrics": {"n": 1000,
            "pnl_mean_usdt": 1.2, "p_target": .4, "p_loss": .1,
            "p_loss_ci95": [.08, .12], "cvar_5_usdt": -2,
            "cut_slippage_mean_usdt": .1, "cut_slippage_p90_usdt": .3,
            "extra_no_finito": float("nan")}}
            for candidate in pieces], "skipped_symbols": {}}

    result = cache_script.run_cache(db.engine.url.database, market_path,
        library_dir=tmp_path, evaluator=evaluate,
        n_scenarios=17,
        now=__import__("datetime").datetime(2026, 10, 30, tzinfo=__import__(
            "datetime").timezone.utc))

    assert result["contexts"] == 1
    assert result["runs"][0]["status"] == "ok"
    assert result["runs"][0]["evaluated_count"] == 1
    assert result["runs"][0]["discarded"]["level 5 net sell does not meet quantity/notional filters"] == 1
    with sqlite3.connect(market_path) as conn:
        rows = conn.execute("SELECT strategy FROM scenario_eval_results ORDER BY strategy").fetchall()
        status, error, n_scenarios = conn.execute(
            "SELECT status,error,n_scenarios FROM scenario_eval_runs").fetchone()
        null_metric = conn.execute("SELECT json_extract(metrics_json,'$.extra_no_finito') "
            "FROM scenario_eval_results WHERE strategy='simple'").fetchone()[0]
    assert rows == [("simple",), ("usdt_quieto",)]
    assert status == "ok"
    assert n_scenarios == 17
    assert null_metric is None
    assert json.loads(error)["evaluated_count"] == 1
    assert json.loads(error)["discarded"]["level 5 net sell does not meet quantity/notional filters"] == 1


def test_corrida_running_antigua_se_marca_abandonada(cache_databases):
    from datetime import datetime, timedelta, timezone

    _db, market_path = cache_databases
    old = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
    with sqlite3.connect(market_path) as conn:
        conn.execute("INSERT INTO scenario_eval_runs(run_id,as_of,profile,horizon_days,"
            "n_scenarios,status) VALUES('run-antigua',?,'RAPIDO',20,1000,'running')",
            (old,))

    result = cache_script.run_cache(_db.engine.url.database, market_path,
        evaluator=lambda *_args, **_kwargs: {"pieces": []})

    assert result["contexts"] == 0
    with sqlite3.connect(market_path) as conn:
        status = conn.execute("SELECT status FROM scenario_eval_runs "
            "WHERE run_id='run-antigua'").fetchone()[0]
    assert status == "abandonada"


def test_cache_prioriza_v2_antes_v1_y_deja_perfiles_al_final():
    contexts = [
        {"goal_id": 3, "decision_engine": "v1"},
        {"goal_id": 2, "decision_engine": "v2"},
        {"goal_id": None, "profile": "RAPIDO"},
        {"goal_id": 1, "decision_engine": "v1"},
    ]


def test_job_inicia_un_subproceso_por_contexto_en_orden(cache_databases, monkeypatch):
    db, market_path = cache_databases
    _set_enabled(db, True)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with db.engine.begin() as conn:
        conn.execute(db.meta_profile_settings.update().where(
            db.meta_profile_settings.c.profile == "GLOBAL").values(
                settings=json.dumps({"max_active": 3, "scenario_cache_enabled": True})))
        conn.execute(db.meta_profile_settings.update().where(
            db.meta_profile_settings.c.profile == "RAPIDO").values(
                settings=json.dumps({"decision_engine": "v2"})))
        goal_ids = []
        for engine in ("v1", "v2"):
            goal_ids.append(conn.execute(db.meta_goals.insert().values(
                capital_initial=100, target_usdt=3, target_input_type="USDT",
                target_input_value=3, profile="RAPIDO", status="ACTIVE",
                environment="testnet", created_at=now, started_at=now,
                config_snapshot=json.dumps({"decision_engine": engine}))).inserted_primary_key[0])
    calls = []
    def run(args, **_kwargs):
        calls.append(args[args.index("--contexto") + 1])
        return subprocess.CompletedProcess(args, 0, "ok", "")
    result = ScenarioCacheJob(db, market_path, run=run).run_if_enabled()
    assert result["contexts"] == 3
    assert calls == [str(goal_ids[1]), str(goal_ids[0]), "perfil:RAPIDO"]


def test_script_manual_orquesta_un_proceso_por_contexto(monkeypatch, capsys):
    contexts = [
        {"goal_id": 7, "profile": "RAPIDO", "decision_engine": "v1"},
        {"goal_id": 3, "profile": "RAPIDO", "decision_engine": "v2"},
    ]
    monkeypatch.setattr(cache_script, "_contexts", lambda *_args: contexts)
    calls = []
    def run(args, **_kwargs):
        calls.append(args[args.index("--contexto") + 1])
        return subprocess.CompletedProcess(args, 0, "hijo\n", "")
    monkeypatch.setattr(cache_script.subprocess, "run", run)
    result = cache_script.main(["--app-db", "app.sqlite", "--market-db", "market.sqlite"])
    assert result == 0
    assert calls == ["3", "7"]
    assert "estado=completed contextos=2" in capsys.readouterr().out


def test_contexto_perfil_se_omite_con_maximo_de_activas(cache_databases):
    db, _market_path = cache_databases
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with db.engine.begin() as conn:
        for profile, values in (("GLOBAL", {"max_active": 2}),
                ("RAPIDO", {"decision_engine": "v2"})):
            conn.execute(db.meta_profile_settings.update().where(
                db.meta_profile_settings.c.profile == profile).values(settings=json.dumps(values)))
        for _ in range(2):
            conn.execute(db.meta_goals.insert().values(capital_initial=100,
                target_usdt=3, target_input_type="USDT", target_input_value=3,
                profile="RAPIDO", status="ACTIVE", environment="testnet",
                created_at=now, started_at=now, config_snapshot=json.dumps({"decision_engine": "v2"})))
    contexts = cache_script._contexts(db.engine.url.database, {})
    assert len([row for row in contexts if row.get("goal_id") is not None]) == 2
    assert not [row for row in contexts if row.get("goal_id") is None]
    assert len(ScenarioCacheJob(db, _market_path)._context_selectors()) == 2


@pytest.mark.skipif(os.name != "posix", reason="VmHWM separado del padre se verifica en Linux")
def test_vmhwm_del_hijo_no_hereda_pico_del_padre():
    import subprocess
    import sys
    parent_memory = bytearray(160 * 1024 * 1024)
    for index in range(0, len(parent_memory), 4096):
        parent_memory[index] = 1
    code = "from scripts.escenarios_cache import _rss_max_mb; print(_rss_max_mb())"
    child = subprocess.run([sys.executable, "-c", code], capture_output=True,
        text=True, check=True)
    assert float(child.stdout.strip()) < 100
    assert sorted(contexts, key=cache_script._context_priority) == [
        contexts[1], contexts[3], contexts[0], contexts[2]]


def test_presupuesto_total_omite_contexto_y_lo_reintenta_en_siguiente_corrida(
        cache_databases, monkeypatch, tmp_path):
    import time

    db, market_path = cache_databases
    contexts = [
        {"goal_id": 1, "profile": "RAPIDO", "decision_engine": "v2",
            "horizon": 15, "capital": 100},
        {"goal_id": 2, "profile": "RAPIDO", "decision_engine": "v1",
            "horizon": 15, "capital": 100},
    ]
    monkeypatch.setattr(cache_script, "_contexts", lambda *_args: contexts)
    monkeypatch.setattr(cache_script, "_read_market_inputs", lambda *_args, **_kwargs:
        ({}, {}, {}, {}))
    attempted = []

    def over_budget(context, **kwargs):
        attempted.append(context["goal_id"])
        time.sleep(.8)
        kwargs["check_limits"]()

    monkeypatch.setattr(cache_script, "_evaluate_context", over_budget)
    first = cache_script.run_cache(db.engine.url.database, market_path,
        library_dir=tmp_path, max_seconds=None, max_total_seconds=.6)
    assert first["runs"][0]["status"] == "timeout"
    assert first["skipped_contexts"] == [2]

    def complete(context, **_kwargs):
        attempted.append(context["goal_id"])
        return {"status": "ok", "results": [], "generated_count": 0,
            "evaluated_count": 0, "discarded": {}, "skipped_symbols": {},
            "profile_times": {}}

    monkeypatch.setattr(cache_script, "_evaluate_context", complete)
    second = cache_script.run_cache(db.engine.url.database, market_path,
        library_dir=tmp_path, max_seconds=5, max_total_seconds=10)
    assert [row["status"] for row in second["runs"]] == ["ok", "ok"]
    assert attempted == [1, 1, 2]


def test_vigilante_distingue_memoria_y_tiempo(monkeypatch):
    monkeypatch.setattr(cache_script, "_rss_current_mb", lambda: 51)
    with pytest.raises(cache_script.CacheLimitExceeded) as memory:
        cache_script._check_limits(0, 50, None)
    assert memory.value.status == "memoria"

    monkeypatch.setattr(cache_script, "_rss_current_mb", lambda: 1)
    with pytest.raises(cache_script.CacheLimitExceeded) as timeout:
        cache_script._check_limits(0, None, .001)
    assert timeout.value.status == "timeout"


def test_corte_cartera_usa_primera_trayectoria_y_coincide_con_evaluador():
    import numpy as np
    from grid.sim.meta_portfolio import evaluate_portfolio, pack_curve

    times = np.arange(5, dtype=np.int64) * 3600
    equity = np.asarray([100, 90, 80, 90, 100], dtype=np.float32)
    cash = equity.copy()
    curve = pack_curve(times, cash, equity, 100)
    expected = evaluate_portfolio([curve], capital_initial=100,
        target_usdt=3, max_loss_pct=17)
    row = {"piece": {"capital": 100}, "scenario_outcomes": {
        "pnl_usdt": np.asarray([0], dtype=np.float32),
        "inventory_qty": np.asarray([0], dtype=np.float32),
        "inventory_value": np.asarray([0], dtype=np.float32),
        "trajectory_cash_f32": cash[None, :],
        "trajectory_equity_f32": equity[None, :],
        "trajectory_timestamps": times}}
    actual = cache_script._portfolio_outcome_metrics([row], capital=100,
        target=3, max_loss_pct=17, horizon=1)

    assert expected["loss_cut"] is True
    assert actual["p_loss"] == 1
    assert actual["p_target"] == 0
    assert actual["pnl_mean_usdt"] == pytest.approx(expected["pnl_usdt"], abs=1e-9)
    assert actual["cut_slippage_mean_usdt"] == pytest.approx(
        max(0, -17 - expected["pnl_usdt"]), abs=1e-9)


def test_dos_contextos_sinteticos_quedan_bajo_700_mb(cache_databases, monkeypatch,
        tmp_path):
    db, market_path = cache_databases
    contexts = [{"goal_id": idx, "profile": "RAPIDO", "decision_engine": "v1",
        "horizon": 1, "capital": 100} for idx in (1, 2)]
    monkeypatch.setattr(cache_script, "_contexts", lambda *_args: contexts)
    monkeypatch.setattr(cache_script, "_read_market_inputs", lambda *_args, **_kwargs:
        ({}, {}, {}, {}))
    monkeypatch.setattr(cache_script, "_evaluate_context", lambda *_args, **_kwargs: {
        "status": "ok", "results": [], "generated_count": 0,
        "evaluated_count": 0, "discarded": {}, "skipped_symbols": {},
        "profile_times": {}})

    result = cache_script.run_cache(db.engine.url.database, market_path,
        library_dir=tmp_path, max_seconds=5, max_total_seconds=10)

    assert len(result["runs"]) == 2
    assert result["rss_mb"] < 700


def test_perfil_con_20_resultados_y_3_monedas_se_guarda_parcial(
        cache_databases, monkeypatch, tmp_path):
    import time

    db, market_path = cache_databases
    context = {"goal_id": None, "profile": "RAPIDO", "horizon": 15,
        "capital": 100}
    monkeypatch.setattr(cache_script, "_contexts", lambda *_args: [context])
    monkeypatch.setattr(cache_script, "_read_market_inputs", lambda *_args, **_kwargs:
        ({}, {}, {}, {}))
    monkeypatch.setattr(cache_script, "rank_alternatives_v2", lambda rows, **_kwargs: [
        {"piece": row.get("piece", row), "objetivo": {}} for row in rows])

    def interrupted(_context, **kwargs):
        rows = [{"piece": {"candidate_key": f"{symbol}-{idx}", "symbol": symbol,
            "strategy": "simple"}, "metrics": {"p_target": 0}, "objective": {}}
            for symbol in ("A", "B", "C") for idx in range(7)]
        kwargs["on_progress"](rows[:20])
        time.sleep(.4)

    monkeypatch.setattr(cache_script, "_evaluate_context", interrupted)
    result = cache_script.run_cache(db.engine.url.database, market_path,
        library_dir=tmp_path, max_seconds=.15, max_total_seconds=5)

    assert result["runs"][0]["status"] == "parcial"
    with sqlite3.connect(market_path) as conn:
        status, count = conn.execute("SELECT status,(SELECT COUNT(*) FROM "
            "scenario_eval_results WHERE run_id=scenario_eval_runs.run_id) "
            "FROM scenario_eval_runs ORDER BY rowid DESC LIMIT 1").fetchone()
    assert status == "parcial"
    assert count == 20


@pytest.mark.parametrize(("limit_kwargs", "expected_status"), [
    ({"max_rss_mb": 0, "max_seconds": None}, "memoria"),
    ({"max_rss_mb": None, "max_seconds": 1}, "timeout"),
])
def test_limite_de_corrida_persiste_resultados_y_status(cache_databases, tmp_path,
        monkeypatch, limit_kwargs, expected_status):
    db, market_path = cache_databases
    with db.engine.begin() as conn:
        raw = conn.execute(select(db.meta_profile_settings.c.settings).where(
            db.meta_profile_settings.c.profile == "PRUDENTE")).scalar_one()
        settings = json.loads(raw)
        settings["decision_engine"] = "v2"
        conn.execute(db.meta_profile_settings.update().where(
            db.meta_profile_settings.c.profile == "PRUDENTE").values(
                settings=json.dumps(settings)))
    piece = {"candidate_key": "partial", "symbol": "JUPUSDT",
        "strategy": "simple", "capital": 50, "n": 6, "low": .2, "high": .3}
    monkeypatch.setattr(cache_script, "_read_market_inputs",
        lambda _store, **_kwargs: ({"JUPUSDT": object()}, {"JUPUSDT": .25}, {"JUPUSDT": .04}, {}))
    monkeypatch.setattr(cache_script, "build_candidates",
        lambda **_kwargs: {"candidates": [piece, {"strategy": "usdt_quieto"}],
            "discarded": {}, "invalid_count": 0})
    monkeypatch.setattr(cache_script, "build_smart_candidates", lambda _rows: [])
    monkeypatch.setattr(cache_script, "rank_alternatives_v2",
        lambda rows, **_kwargs: [{"piece": row.get("piece", row),
            "objetivo": {"factible": True, "motivos": [], "clave_orden": [0, 0, 0]}}
            for row in rows])
    def evaluator(pieces, **_kwargs):
        if expected_status == "timeout":
            import time
            time.sleep(3)
        return {"pieces": [{"piece": row, "metrics": {"n": 1000,
            "pnl_mean_usdt": .1, "p_target": .2, "p_loss": .1,
            "p_loss_ci95": [.05, .15], "cut_slippage_mean_usdt": 0,
            "cut_slippage_p90_usdt": 0}} for row in pieces]}

    result = cache_script.run_cache(db.engine.url.database, market_path,
        library_dir=tmp_path, evaluator=evaluator, **limit_kwargs)

    run_id = result["runs"][0]["run_id"]
    assert result["runs"][0]["status"] == expected_status
    with sqlite3.connect(market_path) as conn:
        status, error, count = conn.execute("SELECT status,error,(SELECT COUNT(*) FROM "
            "scenario_eval_results WHERE run_id=scenario_eval_runs.run_id) "
            "FROM scenario_eval_runs WHERE run_id=?", (run_id,)).fetchone()
    assert status == expected_status
    assert count == (1 if expected_status == "memoria" else 0)
    details = json.loads(error)
    assert details["generated_count"] == 1
    assert details["evaluated_count"] == count


@pytest.mark.parametrize(("status", "exit_code"), [("memoria", 3), ("timeout", 4)])
def test_main_devuelve_codigo_de_salida_del_limite(monkeypatch, capsys, status, exit_code):
    monkeypatch.setattr(cache_script, "run_cache", lambda *_args, **_kwargs: {
        "contexts": 1, "runs": [{"status": status}], "seconds": 1.0, "rss_mb": 50.0})

    result = cache_script.main(["--app-db", "temporal.sqlite", "--market-db", "market.sqlite",
        "--contexto", "1"])

    assert result == exit_code
    assert f"estado={status}" in capsys.readouterr().out
