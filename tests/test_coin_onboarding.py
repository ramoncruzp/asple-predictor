from __future__ import annotations
import json, threading, time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
import config.models_config as config
import models.coin_onboarding as onboarding
from api.routes import coins
from database.db_manager import DBManager
from models.coin_onboarding import CoinOnboardingService, coin_is_ready
from models.training_jobs import ActiveTrainingJob, TrainingJobService


def valid_consensus(symbol):
    return {"symbol":symbol,"report":{"horizons":{str(h):{"champion":"GBM"} for h in (1,2,4,24)}}}

class Predictor:
    def is_champion(self,h,name): return name=="GBM"
class Registry:
    def __init__(self): self.reloads=[]
    def reload(self,symbol): self.reloads.append(symbol); return Predictor()
    def load(self,symbol): return Predictor()


def service_fixture(tmp_path,monkeypatch,runner=None,pid_alive=None):
    db=DBManager(f"sqlite:///{tmp_path/'onboarding.db'}")
    paths={}
    def for_symbol(symbol):
        base=symbol[:-4].lower();root=tmp_path/'vol'/base;root.mkdir(parents=True,exist_ok=True)
        paths[symbol]=(root/f"manifest_{base}.json",root/f"consensus_{base}.json",root)
        return paths[symbol]
    monkeypatch.setattr(config,"vol_manifest_path",lambda s:for_symbol(s)[0])
    monkeypatch.setattr(config,"vol_consensus_path",lambda s:for_symbol(s)[1])
    monkeypatch.setattr(onboarding,"vol_manifest_path",lambda s:for_symbol(s)[0])
    monkeypatch.setattr(onboarding,"vol_consensus_path",lambda s:for_symbol(s)[1])
    monkeypatch.setattr(onboarding,"vol_manifest_path",lambda s:for_symbol(s)[0])
    monkeypatch.setattr(onboarding,"vol_artifact_dir",lambda s:str(for_symbol(s)[2]))
    def read_consensus(symbol):
        path=for_symbol(symbol)[1]
        try:return json.loads(path.read_text(encoding="utf-8"))
        except (OSError,ValueError):return None
    monkeypatch.setattr(onboarding,"load_vol_consensus",read_consensus)
    monkeypatch.setattr(config,"load_vol_consensus",read_consensus)
    for symbol in ("ADAUSDT", "SOLUSDT"):
        for_symbol(symbol)
    registry=Registry()
    training=TrainingJobService(db,root=tmp_path,command_factory=lambda job:["unused"])
    svc=CoinOnboardingService(db,registry,training,root=tmp_path,step_runner=runner,
        pid_alive=pid_alive or (lambda pid,*args:False),poll_seconds=.01)
    return db,svc,registry,paths


def add_coin(db,symbol="ADAUSDT"):
    db.add_or_reactivate_coin(symbol)
    db.set_readiness(symbol,"pendiente",stage_detail="pendiente",progress_pct=0.0)


def test_coin_readiness_table_is_idempotent_and_ready_requires_artifacts(tmp_path,monkeypatch):
    db=DBManager(f"sqlite:///{tmp_path/'ready.db'}");add_coin(db)
    row=db.set_readiness("ADAUSDT","lista",stage_detail="lista",progress_pct=100.0,ready_at=datetime.now(timezone.utc))
    db2=DBManager(f"sqlite:///{tmp_path/'ready.db'}")
    manifest=tmp_path/"manifest.json";consensus=tmp_path/"consensus.json"
    monkeypatch.setattr(onboarding,"vol_manifest_path",lambda s:manifest)
    monkeypatch.setattr(onboarding,"load_vol_consensus",lambda s:json.loads(consensus.read_text()) if consensus.exists() else None)
    assert db2.get_readiness("ADAUSDT")["state"]=="lista"
    assert db2.list_readiness()[0]["symbol"]=="ADAUSDT"
    assert not coin_is_ready(db2,None,"ADAUSDT")
    manifest.write_text("{}",encoding="utf-8");consensus.write_text(json.dumps(valid_consensus("ADAUSDT")),encoding="utf-8")
    assert coin_is_ready(db2,None,"ADAUSDT")
    db2.set_readiness("ADAUSDT","error",error="corrupt")
    assert not coin_is_ready(db2,None,"ADAUSDT")
    assert coin_is_ready(db2,None,"XRPUSDT")


def test_pipeline_happy_path_runs_download_train_once_then_consensus_and_reload(tmp_path,monkeypatch):
    db,svc,registry,paths=service_fixture(tmp_path,monkeypatch)
    add_coin(db)
    for artifact in paths["ADAUSDT"][:2]: artifact.unlink(missing_ok=True)
    seen=[]
    def runner(command,symbol,progress):
        seen.append(command)
        if "train_vol_models.py" in command:
            progress("PROGRESS:descargando_1h");progress("PROGRESS:entrenando:GBM")
            paths[symbol][0].write_text(json.dumps({"trained_at":"now"}),encoding="utf-8")
        else:
            paths[symbol][0].write_text(json.dumps({"trained_at":"now"}),encoding="utf-8")
            progress("PROGRESS:consensuando");progress("PROGRESS:guardando")
            paths[symbol][1].write_text(json.dumps(valid_consensus(symbol)),encoding="utf-8")
        return 0
    svc.step_runner=runner;svc.start();svc.prepare("ADAUSDT")
    row=svc.wait_for("ADAUSDT")
    assert row["state"]=="lista" and row["progress_pct"]==100.0 and row["ready_at"]
    assert registry.reloads==["ADAUSDT"] and config.vol_champions("ADAUSDT")[1] is False
    assert len(seen)==2 and "--min-days" in seen[0] and seen[0][seen[0].index("--min-days")+1]=="540"
    assert "--candles" in seen[1] and "data/cache/vol_train/ada_1h.csv" in " ".join(seen[1]).replace("\\", "/")
    assert coin_is_ready(db,None,"ADAUSDT")
    svc.stop()


def test_short_history_is_data_insufficient_and_does_not_run_consensus(tmp_path,monkeypatch):
    root=tmp_path/"data/cache/vol_train";root.mkdir(parents=True)
    stamps=["2025-01-01T00:00:00Z","2025-04-11T00:00:00Z"]
    (root/"ada_1h.csv").write_text("timestamp\n"+"\n".join(stamps),encoding="utf-8")
    db,svc,_registry,_paths=service_fixture(tmp_path,monkeypatch)
    add_coin(db);calls=[]
    def runner(command,symbol,progress):
        calls.append(command)
        return __import__('subprocess').CompletedProcess(command,2,"Datos insuficientes en 1h: se requieren 540 días","")
    svc.step_runner=runner;svc.start();svc.prepare("ADAUSDT")
    row=svc.wait_for("ADAUSDT")
    assert row["state"]=="datos_insuficientes" and row["history_days"]==100
    assert len(calls)==1 and "vol_consensus_eval.py" not in calls[0]
    assert not list((tmp_path/"vol").rglob("*.joblib"))
    svc.stop()

@pytest.mark.parametrize("failing_phase",["train","consensus"])
def test_each_subprocess_failure_sets_error_and_cleans_symbol_temporaries(tmp_path,monkeypatch,failing_phase):
    db,svc,_registry,paths=service_fixture(tmp_path,monkeypatch,pid_alive=lambda pid,*args:False)
    add_coin(db);calls=[]
    def runner(command,symbol,progress):
        calls.append(command)
        if "train_vol_models.py" in command:
            paths[symbol][0].write_text("{}",encoding="utf-8")
            (paths[symbol][2]/"artifact.tmp").write_text("tmp",encoding="utf-8")
            if failing_phase=="train":return __import__('subprocess').CompletedProcess(command,1,"fallo descarga","stderr")
            return 0
        paths[symbol][1].with_name(paths[symbol][1].name+".lock").write_text("987654321",encoding="ascii")
        (paths[symbol][2]/"consensus.tmp").write_text("tmp",encoding="utf-8")
        return __import__('subprocess').CompletedProcess(command,1,"fallo consenso","")
    svc.step_runner=runner;svc.start();svc.prepare("ADAUSDT")
    row=svc.wait_for("ADAUSDT")
    assert row["state"]=="error" and "fallo" in row["error"]
    assert not list((tmp_path/"vol").rglob("*.tmp"))
    if failing_phase=="consensus":assert not paths["ADAUSDT"][1].with_name(paths["ADAUSDT"][1].name+".lock").exists()
    svc.stop()


def test_startup_marks_interrupted_state_error_without_auto_resuming(tmp_path,monkeypatch):
    db,svc,_registry,_paths=service_fixture(tmp_path,monkeypatch,pid_alive=lambda pid,*args:False)
    add_coin(db);db.set_readiness("ADAUSDT","entrenando",stage_detail="entrenando",pid=999999)
    calls=[];svc.step_runner=lambda *args:calls.append(args) or 0
    svc.start()
    row=db.get_readiness("ADAUSDT")
    assert row["state"]=="error" and row["error"]=="Interrumpido por reinicio; reintentar"
    assert calls==[]
    svc.stop()


def test_single_flight_and_training_job_conflict_are_transactional(tmp_path,monkeypatch):
    db,svc,registry,paths=service_fixture(tmp_path,monkeypatch)
    add_coin(db,"ADAUSDT");add_coin(db,"SOLUSDT")
    active=0;maximum=0;guard=threading.Lock()
    def runner(command,symbol,progress):
        nonlocal active,maximum
        with guard:active+=1;maximum=max(maximum,active)
        time.sleep(.03)
        if "train_vol_models.py" in command:paths[symbol][0].write_text("{}",encoding="utf-8")
        else:paths[symbol][1].write_text(json.dumps(valid_consensus(symbol)),encoding="utf-8")
        with guard:active-=1
        return 0
    svc.step_runner=runner;svc.start();svc.enqueue("ADAUSDT");svc.enqueue("SOLUSDT")
    assert svc.wait_for("ADAUSDT")["state"]=="lista"
    assert svc.wait_for("SOLUSDT")["state"]=="lista"
    assert maximum==1
    db.set_readiness("ADAUSDT","entrenando",stage_detail="entrenando")
    with pytest.raises(ActiveTrainingJob):
        TrainingJobService(db,root=tmp_path,command_factory=lambda job:["never"]).create_job(
            symbol="XRPUSDT",interval="1h",models=["b"],days=730,confirm_reset_evaluation=False)
    svc.stop()


def test_two_service_claims_cannot_overlap(tmp_path):
    db=DBManager(f"sqlite:///{tmp_path/'claims.db'}");add_coin(db,"ADAUSDT");add_coin(db,"SOLUSDT")
    assert db.try_claim_coin_onboarding("ADAUSDT") is True
    assert db.try_claim_coin_onboarding("SOLUSDT") is False
    assert db.get_readiness("SOLUSDT")["state"]=="pendiente"


class Market:
    def get_symbol_status(self,symbol):return "TRADING"
    def get_24h_stats(self,symbol):raise RuntimeError("offline")
class Testnet:
    def get_symbol_info(self,symbol):return {"symbol":symbol,"status":"TRADING"}
class ApiService:
    def __init__(self,db):self.db=db;self.queued=[]
    def enqueue(self,symbol):self.queued.append(symbol);return True
    def is_training_active(self):return False
    def prepare(self,symbol):self.db.set_readiness(symbol,"pendiente",stage_detail="pendiente",progress_pct=0);self.queued.append(symbol);return True
    def in_progress(self,symbol):return (self.db.get_readiness(symbol) or {}).get("state") in {"descargando","entrenando","consensuando"} or symbol in self.queued
    def cancel(self,symbol):
        if (self.db.get_readiness(symbol) or {}).get("stage_detail")=="guardando":raise onboarding.OnboardingArtifactsSaving("Guardando artefactos; espera unos segundos")
        return True


def test_coin_api_create_readiness_prepare_cancel_delete_and_backfill_contract(tmp_path):
    db=DBManager(f"sqlite:///{tmp_path/'api.db'}");app=FastAPI();app.include_router(coins.router,prefix="/api/coins")
    app.state.db=db;app.state.client=Market();app.state.testnet_client=Testnet();service=ApiService(db);app.state.coin_onboarding_service=service
    with TestClient(app) as client:
        created=client.post("/api/coins",json={"symbol":"ada/usdt"})
        assert created.status_code==201 and created.json()["readiness"]["state"]=="pendiente"
        assert service.queued==["ADAUSDT"]
        assert client.get("/api/coins").json()[0]["ready"] is False
        assert client.get("/api/coins/ADAUSDT/readiness").json()["ready"] is False
        assert client.post("/api/coins/ADAUSDT/prepare").status_code==409
        db.set_readiness("ADAUSDT","error",stage_detail="error",error="fallo previo")
        service.queued.clear()
        assert client.post("/api/coins/ADAUSDT/prepare").status_code==202
        assert client.post("/api/coins/ADAUSDT/prepare").status_code==409
        db.set_readiness("ADAUSDT","entrenando",stage_detail="entrenando")
        assert client.delete("/api/coins/ADAUSDT").status_code==409
        db.set_readiness("ADAUSDT","consensuando",stage_detail="guardando")
        cancel=client.post("/api/coins/ADAUSDT/prepare/cancel")
        assert cancel.status_code==409 and cancel.json()["detail"]=="Guardando artefactos; espera unos segundos"
        assert client.post("/api/coins/MISSINGUSDT/prepare").status_code==404


def test_cancel_queued_job_does_not_start_after_cancel(tmp_path,monkeypatch):
    db,svc,_registry,_paths=service_fixture(tmp_path,monkeypatch)
    add_coin(db);called=[];svc.step_runner=lambda *args:called.append(args) or 0
    svc._queued.add("ADAUSDT");svc.db.set_readiness("ADAUSDT","pendiente",stage_detail="pendiente")
    svc.enqueue("ADAUSDT")
    svc.cancel("ADAUSDT")
    row=svc.wait_for("ADAUSDT")
    assert row["state"]=="error" and called==[]
    svc.stop()


def test_coins_service_methods_match_real_onboarding_service():
    route_methods = {"enqueue", "is_training_active", "prepare", "in_progress", "cancel"}
    assert all(callable(getattr(CoinOnboardingService, name, None)) for name in route_methods)
    assert all(callable(getattr(ApiService, name, None)) for name in route_methods)
    assert not hasattr(ApiService, "in_training_active")


def test_prepare_endpoint_uses_real_service_and_returns_202(tmp_path, monkeypatch):
    db, service, registry, paths = service_fixture(tmp_path, monkeypatch)
    add_coin(db)

    def runner(command, symbol, progress):
        if "train_vol_models.py" in command:
            paths[symbol][0].write_text("{}", encoding="utf-8")
        else:
            paths[symbol][1].write_text(json.dumps(valid_consensus(symbol)), encoding="utf-8")
        return 0

    service.step_runner = runner
    app = FastAPI()
    app.include_router(coins.router, prefix="/api/coins")
    app.state.db = db
    app.state.client = Market()
    app.state.testnet_client = Testnet()
    app.state.coin_onboarding_service = service
    with TestClient(app) as client:
        response = client.post("/api/coins/ADAUSDT/prepare")
        assert response.status_code == 202
    assert service.wait_for("ADAUSDT")["state"] == "lista"
    service.stop()


def test_retry_archives_prior_consensus_and_preserves_its_bytes(tmp_path, monkeypatch):
    import hashlib

    db, service, registry, paths = service_fixture(tmp_path, monkeypatch)
    add_coin(db)
    db.set_readiness("ADAUSDT", "error", error="previous attempt failed")
    old_bytes = json.dumps(valid_consensus("ADAUSDT")).encode("utf-8")
    paths["ADAUSDT"][1].write_bytes(old_bytes)

    def runner(command, symbol, progress):
        if "train_vol_models.py" in command:
            paths[symbol][0].write_text("{}", encoding="utf-8")
        else:
            paths[symbol][1].write_text(json.dumps(valid_consensus(symbol)), encoding="utf-8")
        return 0

    service.step_runner = runner
    service.start()
    assert service.prepare("ADAUSDT")
    result = service.wait_for("ADAUSDT")
    archived = list(paths["ADAUSDT"][1].parent.glob("consensus_ada.archivado_*.json"))
    assert result["state"] == "lista"
    assert len(archived) == 1
    assert hashlib.sha256(archived[0].read_bytes()).digest() == hashlib.sha256(old_bytes).digest()
    assert paths["ADAUSDT"][1].is_file()
    log_text = (service.log_dir / "onboarding_adausdt.log").read_text(encoding="utf-8")
    assert f"CONSENSUS_ARCHIVED: {archived[0]}" in log_text
    service.stop()


@pytest.mark.parametrize("cancel_phase", ["train", "consensus"])
def test_cancelled_nonzero_step_uses_cancellation_message(tmp_path, monkeypatch, cancel_phase):
    db, service, registry, paths = service_fixture(tmp_path, monkeypatch)
    add_coin(db)

    def runner(command, symbol, progress):
        is_train = "train_vol_models.py" in command
        if is_train and cancel_phase == "consensus":
            paths[symbol][0].write_text("{}", encoding="utf-8")
            return 0
        if not is_train:
            paths[symbol][1].write_text(json.dumps(valid_consensus(symbol)), encoding="utf-8")
        service._cancelled.add(symbol)
        return __import__("subprocess").CompletedProcess(command, 1, "raw failure", "")

    service.step_runner = runner
    service.start()
    service.prepare("ADAUSDT")
    result = service.wait_for("ADAUSDT")
    assert result["state"] == "error"
    assert result["error"] == "Preparación cancelada por el usuario"
    service.stop()


def test_ready_coin_prepare_rejects_without_archiving_existing_consensus(tmp_path, monkeypatch):
    db, service, registry, paths = service_fixture(tmp_path, monkeypatch)
    add_coin(db)
    paths["ADAUSDT"][0].write_text("{}", encoding="utf-8")
    paths["ADAUSDT"][1].write_text(json.dumps(valid_consensus("ADAUSDT")), encoding="utf-8")
    db.set_readiness("ADAUSDT", "lista", ready_at=datetime.now(timezone.utc))
    app = FastAPI()
    app.include_router(coins.router, prefix="/api/coins")
    app.state.db = db
    app.state.client = Market()
    app.state.testnet_client = Testnet()
    app.state.coin_onboarding_service = service
    with TestClient(app) as client:
        response = client.post("/api/coins/ADAUSDT/prepare")
    assert response.status_code == 409
    assert paths["ADAUSDT"][1].read_text(encoding="utf-8") == json.dumps(valid_consensus("ADAUSDT"))
    assert not list(paths["ADAUSDT"][1].parent.glob("consensus_ada.archivado_*.json"))
