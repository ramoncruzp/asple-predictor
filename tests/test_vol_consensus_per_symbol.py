from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import config.models_config as config
import scripts.vol_consensus_eval as study
from models.volatility.live import VolPredictor
import models.volatility.live as live_module
import joblib


def complete_report(symbol: str, champions=None):
    champions = champions or {1: "GBM", 2: "GBM", 4: "GBM", 24: "NexoHAR"}
    return {"symbol": symbol, "report": {"horizons": {str(h): {"champion": champions[h]} for h in config.VOL_HORIZONS}}}


def test_symbol_study_writes_only_ada_and_never_overwrites(tmp_path, monkeypatch):
    data = tmp_path / "data"; data.mkdir()
    hourly, five = data / "ada_1h.csv", data / "ada_5m.csv"
    hourly.write_text("hourly", encoding="utf-8"); five.write_text("five", encoding="utf-8")
    output = tmp_path / "vol" / "consensus_ada.json"
    xrp = tmp_path / "consensus_xrp.json"; xrp.write_bytes(b"sentinel")
    xrp_hash = hashlib.sha256(xrp.read_bytes()).hexdigest()
    seen = {}
    monkeypatch.setattr(study, "vol_consensus_path", lambda symbol: output if symbol == "ADAUSDT" else xrp)
    def fake_load(hour, five_min):
        seen["paths"] = (hour, five_min)
        import pandas as pd
        return pd.DataFrame(), None
    monkeypatch.setattr(study, "_load_data", fake_load)
    monkeypatch.setattr(study, "evaluate_dataset", lambda candles, intra, symbol: complete_report(symbol)["report"])
    assert study.main(["--symbol", "ADAUSDT", "--candles", str(hourly), "--candles-5m", str(five)]) == 0
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["symbol"] == "ADAUSDT" and saved["test_is_virgin"] is True
    assert saved["source_csv_sha256"] == hashlib.sha256(b"five").hexdigest()
    assert seen["paths"] == (hourly, five)
    assert hashlib.sha256(xrp.read_bytes()).hexdigest() == xrp_hash
    with pytest.raises(FileExistsError, match="ADAUSDT"):
        study.main(["--symbol", "ADAUSDT", "--candles", str(hourly), "--candles-5m", str(five)])
    assert hashlib.sha256(xrp.read_bytes()).hexdigest() == xrp_hash


def test_xrp_cli_defaults_are_unchanged_and_short_data_writes_nothing(tmp_path, monkeypatch):
    hourly=tmp_path/"xrp_1h.csv"; five=tmp_path/"xrp_5m.csv"
    hourly.write_text("h",encoding="utf-8");five.write_text("m",encoding="utf-8")
    out=tmp_path/"consensus_xrp.json"; seen={}
    monkeypatch.chdir(tmp_path)
    (tmp_path/"data/cache").mkdir(parents=True)
    (tmp_path/"data/cache/xrp_1h.csv").write_text("h",encoding="utf-8")
    (tmp_path/"data/cache/xrp_5m.csv").write_text("m",encoding="utf-8")
    monkeypatch.setattr(study,"vol_consensus_path",lambda symbol:out)
    def fake_load(h,m):
        seen["paths"]=(h,m)
        import pandas as pd
        return pd.DataFrame(),None
    monkeypatch.setattr(study,"_load_data",fake_load)
    monkeypatch.setattr(study,"evaluate_dataset",lambda *args: (_ for _ in ()).throw(ValueError("datos insuficientes")))
    with pytest.raises(ValueError,match="insuficientes"):
        study.main([])
    assert seen["paths"] == (Path("data/cache/xrp_1h.csv"),Path("data/cache/xrp_5m.csv"))
    assert not out.exists()


def test_symbol_champions_validate_complete_consensus_and_keep_xrp_global(tmp_path, monkeypatch):
    xrp = dict(config.VOL_CHAMPIONS)
    assert config.vol_champions("XRPUSDT") == (xrp, False)
    path=tmp_path/"consensus_ada.json"
    monkeypatch.setattr(config,"vol_consensus_path",lambda symbol:path)
    path.write_text(json.dumps(complete_report("ADAUSDT",{1:"HAR",2:"EWMA",4:"GBM",24:"HAR_asym"})),encoding="utf-8")
    assert config.vol_champions("ADAUSDT") == ({1:"HAR",2:"EWMA",4:"GBM",24:"HAR_asym"},False)
    payload=complete_report("ADAUSDT");payload["report"]["horizons"].pop("24")
    path.write_text(json.dumps(payload),encoding="utf-8")
    assert config.vol_champions("ADAUSDT") == (xrp,True)
    path.write_text("{broken",encoding="utf-8")
    assert config.vol_champions("ADAUSDT") == (xrp,True)


def test_predictor_reloads_symbol_champions(tmp_path, monkeypatch):
    consensus=tmp_path/"consensus_ada.json"
    manifest=tmp_path/"manifest_ada.json"
    monkeypatch.setattr(config,"vol_consensus_path",lambda symbol:consensus)
    monkeypatch.setattr(live_module,"vol_manifest_path",lambda symbol:manifest)
    consensus.write_text(json.dumps(complete_report("ADAUSDT",{1:"GBM",2:"GBM",4:"GBM",24:"NexoHAR"})),encoding="utf-8")
    manifest.write_text(json.dumps({"horizons":{"1":{"GBM":{"var_factor":1.0}}}}),encoding="utf-8")
    joblib.dump({"model":object(),"var_factor":1.0},tmp_path/"GBM_1h.joblib")
    predictor=VolPredictor(tmp_path,symbol="ADAUSDT",horizons=[1],model_names=["GBM"])
    assert predictor.is_champion(1,"GBM") and not predictor.is_champion(1,"HAR")
    consensus.write_text(json.dumps(complete_report("ADAUSDT",{1:"HAR",2:"GBM",4:"GBM",24:"NexoHAR"})),encoding="utf-8")
    assert predictor.reload() is True
    assert predictor.is_champion(1,"HAR") and not predictor.is_champion(1,"GBM")


def test_existing_consensus_created_during_evaluation_is_not_overwritten(tmp_path, monkeypatch):
    hourly=tmp_path/"ada_1h.csv";five=tmp_path/"ada_5m.csv";out=tmp_path/"consensus_ada.json"
    hourly.write_text("h",encoding="utf-8");five.write_text("m",encoding="utf-8")
    monkeypatch.setattr(study,"vol_consensus_path",lambda symbol:out)
    monkeypatch.setattr(study,"_load_data",lambda *args:(None,None))
    monkeypatch.setattr(study,"evaluate_dataset",lambda *args:{"horizons":{}})
    original_dump=study.json.dump
    def racing_dump(*args,**kwargs):
        original_dump(*args,**kwargs)
        out.write_bytes(b"other process consensus")
    monkeypatch.setattr(study.json,"dump",racing_dump)
    with pytest.raises(FileExistsError,match="ADAUSDT"):
        study.main(["--symbol","ADAUSDT","--candles",str(hourly),"--candles-5m",str(five)])
    assert out.read_bytes()==b"other process consensus"


def test_xrp_eval_keeps_legacy_paths_and_test_is_not_virgin(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cache=tmp_path/"data/cache";cache.mkdir(parents=True)
    (cache/"xrp_1h.csv").write_text("h",encoding="utf-8")
    (cache/"xrp_5m.csv").write_text("m",encoding="utf-8")
    out=tmp_path/"consensus_xrp.json";seen={}
    monkeypatch.setattr(study,"vol_consensus_path",lambda symbol:out)
    def fake_load(h,m):
        seen["paths"]=(h,m)
        return None,None
    monkeypatch.setattr(study,"_load_data",fake_load)
    monkeypatch.setattr(study,"evaluate_dataset",lambda *args:{"horizons":{}})
    assert study.main([])==0
    payload=json.loads(out.read_text(encoding="utf-8"))
    assert payload["symbol"]=="XRPUSDT" and payload["test_is_virgin"] is False
    assert seen["paths"]==(Path("data/cache/xrp_1h.csv"),Path("data/cache/xrp_5m.csv"))


def test_short_synthetic_dataset_fails_before_creating_consensus(tmp_path, monkeypatch):
    import numpy as np
    import pandas as pd
    hourly=tmp_path/"short_1h.csv";five=tmp_path/"short_5m.csv";out=tmp_path/"consensus_ada.json"
    hourly.write_text("placeholder",encoding="utf-8");five.write_text("placeholder",encoding="utf-8")
    timestamps=pd.date_range("2025-01-01",periods=100,freq="h",tz="UTC")
    close=100*np.exp(np.cumsum(np.random.default_rng(42).normal(0,0.001,100)))
    candles=pd.DataFrame({"timestamp":timestamps,"open":close,"high":close*1.001,"low":close*.999,"close":close,"volume":1.0})
    monkeypatch.setattr(study,"vol_consensus_path",lambda symbol:out)
    monkeypatch.setattr(study,"_load_data",lambda *args:(candles,None))
    with pytest.raises(ValueError,match="Datos insuficientes para ADAUSDT"):
        study.main(["--symbol","ADAUSDT","--candles",str(hourly),"--candles-5m",str(five)])
    assert not out.exists()
