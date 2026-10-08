from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import joblib
import numpy as np
import pandas as pd
import pytest

import config.models_config as models_config
import models.volatility.live as live_module
import scripts.train_vol_models as trainer
from config.models_config import VOL_ARTIFACT_DIR, vol_artifact_dir, vol_base, vol_consensus_path, vol_manifest_path
from models.volatility.live import VolPredictor, VolPredictorRegistry
from scheduler.vol_loop import VolLoop


def _write_symbol_artifacts(directory: Path, symbol: str, version: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    manifest = {
        "symbol": symbol,
        "version": version,
        "horizons": {"1": {"Persistence": {"var_factor": 1.0}}},
        "champions": {"1": "Persistence"},
    }
    path = directory / f"manifest_{vol_base(symbol)}.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    joblib.dump({"model": SimpleNamespace(version=version), "var_factor": 1.0}, directory / "Persistence_1h.joblib")


def _touch_manifest_later(path: Path, current_ns: int) -> None:
    later = current_ns + 2_000_000_000
    os.utime(path, ns=(later, later))


def _synthetic_hourly(rows: int = 850) -> pd.DataFrame:
    rng = np.random.default_rng(918)
    returns = rng.normal(0.0, 0.002, rows)
    close = 100.0 * np.exp(np.cumsum(returns))
    open_ = np.r_[100.0, close[:-1]]
    stamps = pd.date_range("2025-01-01", periods=rows, freq="h", tz="UTC")
    return pd.DataFrame({
        "timestamp": stamps,
        "close_time": stamps + pd.Timedelta(hours=1) - pd.Timedelta(milliseconds=1),
        "open": open_, "high": np.maximum(open_, close) * 1.001,
        "low": np.minimum(open_, close) * 0.999, "close": close,
        "volume": rng.uniform(10.0, 100.0, rows),
    })


def _synthetic_5m(hourly: pd.DataFrame) -> pd.DataFrame:
    stamps, opens, highs, lows, closes = [], [], [], [], []
    for row in hourly.itertuples(index=False):
        start, end = float(row.open), float(row.close)
        for step in range(12):
            left = start * np.exp(np.log(end / start) * step / 12.0)
            right = start * np.exp(np.log(end / start) * (step + 1) / 12.0)
            stamps.append(row.timestamp + pd.Timedelta(minutes=5 * step))
            opens.append(left); closes.append(right)
            highs.append(max(left, right) * 1.0001); lows.append(min(left, right) * 0.9999)
    return pd.DataFrame({
        "timestamp": stamps, "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": 1.0,
    })


def test_symbol_paths_validation_and_xrp_cli_defaults():
    assert vol_base("XRPUSDT") == "xrp"
    assert vol_base("ADAUSDT") == "ada"
    assert vol_artifact_dir("XRPUSDT") == VOL_ARTIFACT_DIR
    assert Path(vol_manifest_path("XRPUSDT")) == Path(VOL_ARTIFACT_DIR) / "manifest_xrp.json"
    assert Path(vol_artifact_dir("ADAUSDT")) == Path(VOL_ARTIFACT_DIR) / "ada"
    assert Path(vol_manifest_path("ADAUSDT")) == Path(VOL_ARTIFACT_DIR) / "ada" / "manifest_ada.json"
    assert Path(vol_consensus_path("XRPUSDT")) == Path(VOL_ARTIFACT_DIR) / "consensus_xrp.json"
    assert Path(vol_consensus_path("ADAUSDT")) == Path(VOL_ARTIFACT_DIR) / "ada" / "consensus_ada.json"
    with pytest.raises(ValueError) as invalid:
        vol_base("../x")
    assert "Símbolo" in str(invalid.value) and "válido" in str(invalid.value)
    defaults = trainer.parse_args([])
    assert defaults.candles == Path("data/cache/xrp_1h.csv")
    assert defaults.candles_5m == Path("data/cache/xrp_5m.csv")
    ada_defaults = trainer.parse_args(["--symbol", "ADAUSDT"])
    assert ada_defaults.candles == Path("data/cache/ada_1h.csv")
    assert ada_defaults.candles_5m == Path("data/cache/ada_5m.csv")
    for symbol in ("../x", "xrp", "BTCUSD", "XRP/USDT", "AUSDT"):
        with pytest.raises(ValueError):
            vol_base(symbol)


def test_consensus_cache_reuses_json_until_mtime_changes(tmp_path, monkeypatch):
    path = tmp_path / "consensus_ada.json"
    def payload(champion):
        return {"symbol": "ADAUSDT", "report": {"horizons": {
            str(h): {"champion": champion} for h in models_config.VOL_HORIZONS
        }}}
    path.write_text(json.dumps(payload("GBM")), encoding="utf-8")
    monkeypatch.setattr(models_config, "vol_consensus_path", lambda _symbol: path)
    models_config._VOL_CONSENSUS_CACHE.clear()
    original_read = Path.read_text
    reads = []
    def counted_read(target, *args, **kwargs):
        if target == path:
            reads.append(target)
        return original_read(target, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", counted_read)
    first = models_config.load_vol_consensus("ADAUSDT")
    second = models_config.load_vol_consensus("ADAUSDT")
    assert second == first and second is not first
    second["report"]["horizons"]["1"]["champion"] = "MUTATED"
    assert models_config.load_vol_consensus("ADAUSDT")["report"]["horizons"]["1"]["champion"] == "GBM"
    assert len(reads) == 1
    path.write_text(json.dumps(payload("HAR")), encoding="utf-8")
    changed_ns = path.stat().st_mtime_ns + 2_000_000_000
    os.utime(path, ns=(changed_ns, changed_ns))
    updated = models_config.load_vol_consensus("ADAUSDT")
    assert updated["report"]["horizons"]["1"]["champion"] == "HAR"
    assert len(reads) == 2


def test_refresh_paths_and_min_days_are_symbol_scoped_and_offline(tmp_path, monkeypatch):
    calls, validations, trained = [], [], []
    cache = tmp_path / "cache"
    monkeypatch.setattr(trainer, "vol_artifact_dir", lambda symbol: str(tmp_path / "artifacts" / vol_base(symbol)))

    def download(symbol, interval, days, output):
        calls.append((symbol, interval, days, Path(output)))
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"close_time": [datetime.now(timezone.utc).isoformat()]}).to_csv(output, index=False)

    monkeypatch.setattr(trainer, "download_closed_candles", download)
    monkeypatch.setattr(trainer, "validate_refresh_csv", lambda path, interval, rows: validations.append((Path(path), interval, rows)))
    monkeypatch.setattr(trainer, "_load_data", lambda *_: (pd.DataFrame({"synthetic": [1]}), None))

    def fake_train(candles, intraday, **kwargs):
        trained.append(kwargs)
        return {"horizons": {"1": {}}}

    monkeypatch.setattr(trainer, "train_volatility_models", fake_train)
    assert trainer.main(["--symbol", "ADAUSDT", "--refresh-candles", "--days", "730", "--min-days", "540", "--candles-dir", str(cache)]) == 0
    assert [(symbol, interval, days) for symbol, interval, days, _ in calls] == [
        ("ADAUSDT", "1h", 730), ("ADAUSDT", "5m", 730),
    ]
    assert [path.name for _, _, _, path in calls] == ["ada_1h.csv", "ada_5m.csv"]
    assert all(path.parent == cache for _, _, _, path in calls)
    assert [rows for _, _, rows in validations] == [int(540 * 24 * 0.95), int(540 * 288 * 0.95)]
    assert trained[0]["symbol"] == "ADAUSDT"
    assert Path(trained[0]["artifact_dir"]) == tmp_path / "artifacts" / "ada"
    assert trainer.parse_args(["--min-days", "540"]).min_days == 540
    defaults = trainer.parse_args([])
    assert defaults.days == defaults.min_days == 730
    assert trainer.parse_args(["--days", "730", "--min-days", "540"]).days == 730
    with pytest.raises(SystemExit):
        trainer.parse_args(["--days", "730", "--min-days", "800"])


def test_ada_cli_training_writes_only_ada_artifacts_and_preserves_xrp(tmp_path, monkeypatch):
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    xrp_files = {"manifest_xrp.json": b"xrp-manifest-sentinel", "legacy.bin": b"protected-xrp-artifact"}
    for name, data in xrp_files.items():
        (artifact_root / name).write_bytes(data)
    xrp_before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in artifact_root.iterdir() if p.is_file()}
    hourly, five = _synthetic_hourly(), _synthetic_5m(_synthetic_hourly())
    hourly_path, five_path = tmp_path / "candles.csv", tmp_path / "candles5.csv"
    hourly.to_csv(hourly_path, index=False); five.to_csv(five_path, index=False)
    monkeypatch.setattr(trainer, "VOL_HORIZONS", [1])
    monkeypatch.setattr(trainer, "VOL_MODELS", ["Persistence"])
    monkeypatch.setattr(trainer, "vol_artifact_dir", lambda symbol: str(artifact_root if symbol == "XRPUSDT" else artifact_root / vol_base(symbol)))

    assert trainer.main(["--symbol", "ADAUSDT", "--candles", str(hourly_path), "--candles-5m", str(five_path)]) == 0
    ada_dir = artifact_root / "ada"
    assert (ada_dir / "manifest_ada.json").is_file()
    assert (ada_dir / "Persistence_1h.joblib").is_file()
    assert json.loads((ada_dir / "manifest_ada.json").read_text(encoding="utf-8"))["symbol"] == "ADAUSDT"
    assert "champions" not in json.loads((ada_dir / "manifest_ada.json").read_text(encoding="utf-8"))
    xrp_after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in artifact_root.iterdir() if p.is_file()}
    assert xrp_after == xrp_before
    assert {p.name for p in ada_dir.iterdir()} == {"manifest_ada.json", "Persistence_1h.joblib"}


def test_ada_predictor_registry_reload_and_corrupt_manifest_preserve_snapshot(tmp_path):
    _write_symbol_artifacts(tmp_path, "XRPUSDT", "xrp")
    ada_dir = tmp_path / "ada"
    _write_symbol_artifacts(ada_dir, "ADAUSDT", "ada-v1")
    registry = VolPredictorRegistry(tmp_path, horizons=[1], model_names=["Persistence"])
    assert registry.load_available() == ["XRPUSDT", "ADAUSDT"]
    predictor = registry.get("ADAUSDT")
    assert predictor is not None and predictor.symbol == "ADAUSDT"
    assert predictor.manifest["version"] == "ada-v1"
    assert predictor.models[(1, "Persistence")]["model"].version == "ada-v1"
    assert registry.ready_symbols() == ["ADAUSDT", "XRPUSDT"]
    assert registry.reload_if_changed("ADAUSDT") is False

    _write_symbol_artifacts(ada_dir, "ADAUSDT", "ada-v2")
    _touch_manifest_later(predictor.manifest_path, predictor.manifest_mtime)
    assert registry.reload_if_changed("ADAUSDT") is True
    assert predictor.manifest["version"] == "ada-v2"
    assert predictor.models[(1, "Persistence")]["model"].version == "ada-v2"
    prior_manifest, prior_model = predictor.manifest, predictor.models[(1, "Persistence")]["model"]

    predictor.manifest_path.write_text("{broken", encoding="utf-8")
    _touch_manifest_later(predictor.manifest_path, predictor.manifest_mtime)
    assert predictor.reload() is False
    assert predictor.manifest is prior_manifest
    assert predictor.models[(1, "Persistence")]["model"] is prior_model
    assert predictor.reload_error


def test_reload_holds_lock_until_complete_snapshot_is_swapped(tmp_path, monkeypatch):
    _write_symbol_artifacts(tmp_path, "ADAUSDT", "old")
    predictor = VolPredictor(tmp_path, symbol="ADAUSDT", horizons=[1], model_names=["Persistence"])
    _write_symbol_artifacts(tmp_path, "ADAUSDT", "new")
    _touch_manifest_later(predictor.manifest_path, predictor.manifest_mtime)
    artifact = tmp_path / "Persistence_1h.joblib"
    original_load = live_module.joblib.load
    loading, release, read_done = threading.Event(), threading.Event(), threading.Event()
    observed = []

    def blocking_load(path, *args, **kwargs):
        if Path(path) == artifact:
            loading.set()
            assert release.wait(5)
        return original_load(path, *args, **kwargs)

    monkeypatch.setattr(live_module.joblib, "load", blocking_load)
    reload_result = []
    worker = threading.Thread(target=lambda: reload_result.append(predictor.reload()))
    worker.start()
    assert loading.wait(3)

    def read_snapshot():
        with predictor._lock:
            observed.append((predictor.manifest["version"], predictor.models[(1, "Persistence")]["model"].version))
        read_done.set()

    reader = threading.Thread(target=read_snapshot)
    reader.start()
    try:
        assert not read_done.wait(0.15)
    finally:
        release.set()
        worker.join(5); reader.join(5)
    assert not worker.is_alive() and not reader.is_alive()
    assert reload_result == [True]
    assert observed == [("new", "new")]


def _cycle_frames():
    now = pd.Timestamp.now(tz="UTC").floor("h")
    hourly_stamps = pd.date_range(end=now - pd.Timedelta(hours=1), periods=2, freq="h")
    hourly = pd.DataFrame({
        "timestamp": hourly_stamps,
        "close_time": hourly_stamps + pd.Timedelta(hours=1) - pd.Timedelta(milliseconds=1),
        "open": [100.0, 100.1], "high": [101.0, 101.1], "low": [99.0, 99.1],
        "close": [100.1, 100.2], "volume": [1.0, 1.0],
    })
    five_stamps = pd.date_range(end=now - pd.Timedelta(minutes=5), periods=24, freq="5min")
    five = pd.DataFrame({
        "timestamp": five_stamps, "open": 100.0, "high": 101.0, "low": 99.0,
        "close": 100.1, "volume": 1.0,
    })
    return hourly, five


def test_vol_loop_continues_other_symbol_after_one_symbol_fails():
    hourly, five = _cycle_frames()
    calls, saved, reloaded = [], [], []

    class Predictor:
        def __init__(self, symbol): self.symbol = symbol
        def predict_latest(self, _hourly, _five, *, now):
            return [{
                "symbol": self.symbol, "horizon_h": 1, "model_name": "Persistence",
                "forecast_at": now - timedelta(hours=2), "made_at": now,
                "pred_logvol_raw": -5.0, "pred_logvol_cal": -5.0,
                "var_factor": 1.0, "is_champion": False,
                "price": 100.0,
            }]

    class Registry:
        predictors = {s: Predictor(s) for s in ("ADAUSDT", "XRPUSDT")}
        def ready_symbols(self): return ["ADAUSDT", "XRPUSDT"]
        def reload_if_changed(self, symbol): reloaded.append(symbol); return False
        def get(self, symbol): return self.predictors[symbol]

    class Client:
        def get_historical_klines(self, symbol, interval, lookback_days):
            calls.append((symbol, interval, lookback_days))
            if symbol == "ADAUSDT": raise RuntimeError("fallo sintético ADA")
            return hourly if interval == "1h" else five

    class Database:
        def save_vol_forecasts(self, rows): saved.extend(rows); return len(rows)
        def get_pending_vol_verifications(self, _now): return []
        def save_vol_realized(self, *_): raise AssertionError("no pending verification expected")

    registry = Registry()
    loop = VolLoop(Client(), registry, Database())
    loop.run_cycle()
    assert reloaded == ["ADAUSDT", "XRPUSDT"]
    assert calls[0][0] == "ADAUSDT" and any(symbol == "XRPUSDT" for symbol, _, _ in calls)
    assert [row["symbol"] for row in saved] == ["XRPUSDT"]


def test_vol_loop_forecasts_after_ada_becomes_ready_without_network(tmp_path):
    from unittest.mock import create_autospec
    from data.binance_client import BinanceClient
    from database.db_manager import DBManager

    db = DBManager(f"sqlite:///{tmp_path / 'onboarding.db'}")
    db.add_or_reactivate_coin("ADAUSDT")
    db.set_readiness("ADAUSDT", "lista", stage_detail="lista", progress_pct=100.0)
    assert db.get_readiness("ADAUSDT")["state"] == "lista"

    artifact_root = tmp_path / "vol_artifacts"
    _write_symbol_artifacts(artifact_root / "ada", "ADAUSDT", "onboarded-ada")
    registry = VolPredictorRegistry(artifact_root, horizons=[1], model_names=["Persistence"])
    assert registry.load_available() == ["ADAUSDT"]
    assert registry.ready_symbols() == ["ADAUSDT"]
    predictor = registry.get("ADAUSDT")
    now = datetime.now(timezone.utc)
    predictor.predict_latest = create_autospec(predictor.predict_latest, return_value=[{
        "symbol":"ADAUSDT", "horizon_h":1, "model_name":"Persistence",
        "forecast_at":now-timedelta(hours=2), "made_at":now,
        "pred_logvol_raw":-5.0, "pred_logvol_cal":-5.0,
        "var_factor":1.0, "is_champion":True, "price":100.0,
    }])
    client = create_autospec(BinanceClient, instance=True)
    hourly, five = _cycle_frames()
    client.get_historical_klines.side_effect = lambda symbol, interval, lookback_days: hourly if interval == "1h" else five

    VolLoop(client, registry, db).run_cycle()

    predictor.predict_latest.assert_called_once()
    forecasts = db.get_latest_vol_forecasts("ADAUSDT")
    assert forecasts and forecasts[0]["symbol"] == "ADAUSDT"
