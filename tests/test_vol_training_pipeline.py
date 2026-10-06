from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import scripts.download_candles as downloader
import scripts.train_vol_models as trainer
import api.routes.model_training as training_route
import api.routes.models_status as status_route
from database.db_manager import DBManager
from models.training_jobs import ActiveTrainingJob, TrainingArtifactsSaving, TrainingJobService


class _FakeVolModel:
    selected_estimator = "parkinson"
    def fit(self, data): return self
    def predict(self, data): return np.full(len(data), .1)


class FakeCandleClient:
    def __init__(self, frame=None, error=None):
        self.frame, self.error = frame, error

    def get_historical_klines(self, symbol, interval, *, lookback_days):
        assert (symbol, interval, lookback_days) == ("XRPUSDT", "5m", 3)
        if self.error:
            raise self.error
        return self.frame.copy()


def test_download_closed_candles_writes_atomically_and_removes_temp_on_error(tmp_path):
    now = pd.Timestamp.now(tz="UTC")
    frame = pd.DataFrame({"timestamp": [now - pd.Timedelta(minutes=10), now],
                          "close_time": [now - pd.Timedelta(minutes=1), now + pd.Timedelta(minutes=1)],
                          "close": [1.0, 2.0]})
    output = tmp_path / "nested" / "candles.csv"
    result = downloader.download_closed_candles("XRPUSDT", "5m", 3, output,
                                                  client=FakeCandleClient(frame))
    assert len(result) == 1 and output.is_file()
    assert len(pd.read_csv(output)) == 1 and not output.with_name("candles.csv.tmp").exists()
    before = output.read_bytes()
    with pytest.raises(RuntimeError, match="offline fixture"):
        downloader.download_closed_candles("XRPUSDT", "5m", 3, output,
                                           client=FakeCandleClient(error=RuntimeError("offline fixture")))
    assert output.read_bytes() == before and not output.with_name("candles.csv.tmp").exists()


def test_refresh_validation_rejects_stale_or_short_csv_before_training(tmp_path):
    now = datetime.now(timezone.utc)
    path = tmp_path / "candles.csv"
    pd.DataFrame({"close_time": [now.isoformat()] * 3}).to_csv(path, index=False)
    with pytest.raises(ValueError, match="insuficientes"):
        trainer.validate_refresh_csv(path, "1h", 4, now=now)
    pd.DataFrame({"close_time": [(now - timedelta(hours=3)).isoformat()] * 4}).to_csv(path, index=False)
    with pytest.raises(ValueError, match="obsoletos"):
        trainer.validate_refresh_csv(path, "5m", 4, now=now)


def test_refresh_cli_uses_isolated_paths_validates_and_emits_progress(tmp_path, monkeypatch, capsys):
    root = Path(__file__).resolve().parents[1]
    protected = [root / "data/cache/xrp_1h.csv", root / "data/cache/xrp_5m.csv"]
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None for path in protected}
    destinations = []

    def fake_download(symbol, interval, days, out):
        assert symbol == "XRPUSDT" and days == 730
        destinations.append((interval, Path(out).resolve()))
        close = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        pd.DataFrame({"close_time": [close, close]}).to_csv(out, index=False)

    phases = []
    monkeypatch.setattr(trainer, "MIN_HOURLY_ROWS", 1)
    monkeypatch.setattr(trainer, "MIN_5M_ROWS", 1)
    monkeypatch.setattr(trainer, "download_closed_candles", fake_download)
    monkeypatch.setattr(trainer, "_load_data", lambda hourly, five: (pd.DataFrame({"x": [1]}), None))

    def fake_train(candles, intraday, *, progress=None):
        if progress:
            progress("entrenando:H1:stub")
            progress("calibrando_regimen")
            progress("guardando")
        return {"horizons": {"1": {}}}

    monkeypatch.setattr(trainer, "train_volatility_models", fake_train)
    assert trainer.main(["--refresh-candles", "--candles-dir", str(tmp_path), "--progress"]) == 0
    assert [item[0] for item in destinations] == ["1h", "5m"]
    assert all(path.parent == tmp_path.resolve() for _, path in destinations)
    assert all(path.name in {"xrp_1h.csv", "xrp_5m.csv"} for _, path in destinations)
    assert all(not path.resolve().is_relative_to(root / "models/saved") for _, path in destinations)
    assert not (tmp_path / "consensus_xrp.json").exists()
    after = {path: hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None for path in protected}
    assert before == after
    output = capsys.readouterr().out
    assert "PROGRESS:descargando_1h" in output and "PROGRESS:descargando_5m" in output
    assert "PROGRESS:validando_datos" in output and "PROGRESS:entrenando:H1:stub" in output
    assert "PROGRESS:calibrando_regimen" in output and "PROGRESS:guardando" in output


def test_refresh_cli_rejects_explicit_candle_paths_and_default_cli_does_not_download(monkeypatch):
    with pytest.raises(SystemExit):
        trainer.parse_args(["--refresh-candles", "--candles", "custom.csv"])
    monkeypatch.setattr(trainer, "_load_data", lambda hourly, five: (pd.DataFrame({"x": [1]}), None))
    monkeypatch.setattr(trainer, "train_volatility_models", lambda *args, **kwargs: {"horizons": {"1": {}}})
    monkeypatch.setattr(trainer, "download_closed_candles", lambda *args, **kwargs: pytest.fail("CLI por defecto intentó descargar"))
    assert trainer.main([]) == 0


def test_train_failure_on_third_model_preserves_prior_artifacts_and_cleans_tmps(tmp_path, monkeypatch):
    artifact_dir = tmp_path / "vol"
    artifact_dir.mkdir()
    old_model, old_manifest = artifact_dir / "Fake1_1h.joblib", artifact_dir / "manifest_xrp.json"
    old_model.write_bytes(b"old model")
    old_manifest.write_bytes(b'{"trained_at":"old"}')
    before = (old_model.read_bytes(), old_manifest.read_bytes())
    monkeypatch.setattr(trainer, "feature_columns", lambda _h: ["feature"])
    stamps = pd.date_range(end=pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=1), periods=300, freq="h")

    def frame_builder(candles, horizon, intraday=None):
        return pd.DataFrame({"feature": np.ones(300), "target_logvol": np.full(300, .1),
                             "complete_hour": np.ones(300, dtype=bool), "timestamp": stamps,
                             "future_var_24": np.ones(300)})

    monkeypatch.setattr(trainer, "build_volatility_frame", frame_builder)
    monkeypatch.setattr(trainer, "score_forecast", lambda target, pred: {"n": len(target), "r2_oos": .1, "mse_log": .2, "qlike": .3})
    calls = {"models": 0}

    def new_model(name, _horizon):
        calls["models"] += 1
        if calls["models"] == 3:
            raise RuntimeError("tercer modelo simulado")
        return _FakeVolModel()

    monkeypatch.setattr(trainer, "_new_model", new_model)
    with pytest.raises(RuntimeError, match="tercer modelo simulado"):
        trainer.train_volatility_models(pd.DataFrame({"timestamp": stamps}), artifact_dir=artifact_dir,
                                       horizons=[1], model_names=["Fake1", "Fake2", "Fake3"])
    assert (old_model.read_bytes(), old_manifest.read_bytes()) == before
    assert list(artifact_dir.glob("*.tmp")) == []


def _vol_service(tmp_path):
    db = DBManager(f"sqlite:///{tmp_path / 'vol-jobs.sqlite'}")
    service = TrainingJobService(db, root=tmp_path,
                                 command_factory=lambda _job: [__import__('sys').executable, "-c", "print('PROGRESS:guardando',flush=True)"])
    return db, service


def test_vol_job_command_cleanup_single_flight_and_guardando_cancel_409(tmp_path):
    db, service = _vol_service(tmp_path)
    assert TrainingJobService._command({"models": ["vol"]}) == [
        __import__('sys').executable, "-u", "scripts/train_vol_models.py", "--refresh-candles", "--progress"]
    for path in (tmp_path / "models/saved/vol/a.tmp", tmp_path / "data/cache/vol_train/b.tmp"):
        path.parent.mkdir(parents=True, exist_ok=True); path.write_text("tmp", encoding="utf-8")
    service._cleanup_temps({"models": ["vol"]})
    assert not (tmp_path / "models/saved/vol/a.tmp").exists()
    assert not (tmp_path / "data/cache/vol_train/b.tmp").exists()
    job = service.create_job(symbol="XRPUSDT", interval="1h", models=["vol"], days=1, confirm_reset_evaluation=False)
    assert job["days"] == 730
    table = db.training_jobs
    with db.engine.begin() as conn:
        saving_id = conn.execute(table.insert().values(symbol="XRPUSDT", interval="1h", models="vol", days=730,
            status="running", phase="guardando", created_at=db._utc_now())).inserted_primary_key[0]
    with pytest.raises(TrainingArtifactsSaving, match="Guardando artefactos; espera unos segundos"):
        service.cancel(saving_id)
    db.engine.dispose()


def test_vol_training_request_validation_and_artifacts_endpoint(tmp_path, monkeypatch):
    captured = []
    class JobStub:
        conflict = False
        def create_job(self, **kwargs):
            if self.conflict:
                raise ActiveTrainingJob("Ya hay un trabajo activo")
            captured.append(kwargs)
            return {"id": 1, "models": ["vol"], "days": kwargs["days"]}
        def cancel(self, _job): raise TrainingArtifactsSaving("Guardando artefactos; espera unos segundos")
    app = FastAPI()
    app.include_router(training_route.router, prefix="/api/models")
    app.include_router(status_route.router, prefix="/api/models")
    app.state.training_job_service = JobStub()
    artifacts = tmp_path / "vol"
    artifacts.mkdir()
    (artifacts / "manifest_xrp.json").write_text(json.dumps({"trained_at":"disk-time","data_range":{"start":"a","end":"b"}}), encoding="utf-8")
    (artifacts / "consensus_xrp.json").write_text(json.dumps({"created_at":"study-time","source_csv":"xrp_5m.csv","source_csv_sha256":"123456789abcDEF"}), encoding="utf-8")
    monkeypatch.setattr(status_route, "VOL_ARTIFACT_DIR", artifacts)
    app.state.vol_predictor = SimpleNamespace(manifest={"trained_at":"loaded-time"})
    body = {"symbol":"XRPUSDT","interval":"1h","models":["vol"],"days":730,"confirm":True}
    with TestClient(app) as client:
        valid = client.post("/api/models/train", json=body)
        mixed = client.post("/api/models/train", json={**body,"models":["vol","a"]})
        wrong_days = client.post("/api/models/train", json={**body,"days":365})
        wrong_symbol = client.post("/api/models/train", json={**body,"symbol":"BTCUSDT"})
        artifacts_response = client.get("/api/models/vol/artifacts")
        cancel_saving = client.post("/api/models/train/1/cancel")
        app.state.training_job_service.conflict = True
        conflict = client.post("/api/models/train", json=body)
    assert valid.status_code == 201 and captured[0]["models"] == ["vol"] and captured[0]["days"] == 730
    assert mixed.status_code == wrong_days.status_code == wrong_symbol.status_code == 422
    assert cancel_saving.status_code == 409
    assert conflict.status_code == 409 and conflict.json()["detail"] == "Ya hay un trabajo activo"
    assert artifacts_response.status_code == 200
    assert artifacts_response.json() == {"symbol":"XRPUSDT","artifact_trained_at":"disk-time","loaded_trained_at":"loaded-time",
        "data_range":{"start":"a","end":"b"},"consensus":{"created_at":"study-time","source_csv":"xrp_5m.csv","source_csv_sha256_short":"123456789abc"}}

