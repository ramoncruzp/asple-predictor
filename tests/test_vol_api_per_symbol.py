from __future__ import annotations
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
import api.routes.models_status as models_status
import api.routes.volatility as volatility
import config.models_config as models_config
from config.models_config import VOL_CHAMPIONS

class FakeDB:
    def get_latest_vol_forecasts(self, symbol):
        now=datetime.now(timezone.utc)
        return [{"symbol":symbol,"horizon_h":h,"model_name":m,"is_champion":True,"forecast_at":now,"made_at":now,"pred_logvol_cal":-5.0} for h,m in VOL_CHAMPIONS.items()]
    def get_vol_model_stats_rows(self,*_): return []
    def get_vol_model_stats_dispersion_rows(self,*_): return []
    def get_vol_battle(self,*_): return []
    def get_vol_history(self,symbol,horizon,model,limit): return [{"symbol":symbol,"horizon_h":horizon,"model":model,"limit":limit}]

class FakeRegistry:
    def __init__(self): self.items={s:SimpleNamespace(symbol=s,manifest={"trained_at":f"loaded-{s}","horizons":{},"regime_percentiles_24h":{}}) for s in ("XRPUSDT","ADAUSDT")}
    def ready_symbols(self): return list(self.items)
    def get(self,symbol): return self.items.get(symbol)

@pytest.fixture
def api_client(tmp_path,monkeypatch):
    manifests,consensuses={},{}
    for symbol,base in (("XRPUSDT","xrp"),("ADAUSDT","ada")):
        d=tmp_path/("legacy" if base=="xrp" else base);d.mkdir(parents=True,exist_ok=True)
        m,c=d/f"manifest_{base}.json",d/f"consensus_{base}.json"
        m.write_text(json.dumps({"symbol":symbol,"trained_at":f"artifact-{symbol}","data_range":{"start":"a","end":"b"}}),encoding="utf-8")
        c.write_text(json.dumps({"created_at":f"consensus-{symbol}","source_csv":f"{base}.csv","source_csv_sha256":base*16}),encoding="utf-8")
        manifests[symbol],consensuses[symbol]=m,c
    for module in (volatility,models_status):
        monkeypatch.setattr(module,"vol_manifest_path",lambda symbol:manifests[symbol])
        monkeypatch.setattr(module,"vol_consensus_path",lambda symbol:consensuses[symbol])
    monkeypatch.setattr(models_config,"vol_consensus_path",lambda symbol:consensuses[symbol])
    monkeypatch.setattr(volatility,"_load_horizon_report",lambda horizon,symbol="XRPUSDT":{})
    app=FastAPI();app.include_router(volatility.router,prefix="/api/volatility");app.include_router(models_status.router,prefix="/api/models")
    app.state.vol_registry=FakeRegistry();app.state.vol_predictor=app.state.vol_registry.get("XRPUSDT")
    app.state.vol_loop=SimpleNamespace(latest={"price":51.0},latest_by_symbol={"XRPUSDT":{"price":51.0},"ADAUSDT":{"price":101.0}})
    app.state.db=FakeDB()
    with TestClient(app) as client: yield client

def test_symbol_routes_selection_and_xrp_compatibility(api_client):
    for route in ("forecast","model-stats","battle","history"):
        ada=api_client.get(f"/api/volatility/{route}",params={"symbol":"ada/usdt","horizon":4,"model":"GBM"})
        assert ada.status_code==200,ada.text
        if route == "history":
            assert isinstance(ada.json(), list)
            assert ada.headers["x-vol-selection"] == "provisional"
        else:
            assert ada.json()["selection"]=="provisional"
            assert ada.json()["selection_note"]=="Campeones globales de XRP; sin estudio de consenso propio"
    assert api_client.get("/api/volatility/forecast",params={"symbol":"ADAUSDT"}).json()["price"]==101.0
    xrp=api_client.get("/api/volatility/forecast",params={"symbol":"XRPUSDT"})
    assert xrp.status_code==200 and "selection" not in xrp.json()
    hist=api_client.get("/api/volatility/history",params={"symbol":"XRPUSDT"})
    assert hist.status_code==200 and isinstance(hist.json(),list)
    assert hist.headers["x-vol-selection"]=="consensus"

def test_invalid_and_unloaded_symbols_have_expected_status(api_client):
    assert api_client.get("/api/volatility/forecast",params={"symbol":"SOLUSDT"}).status_code==404
    for symbol in ("../x","xrp"):
        r=api_client.get("/api/volatility/forecast",params={"symbol":symbol})
        assert r.status_code==422 and "Símbolo" in r.json()["detail"] and "válido" in r.json()["detail"]

def test_symbols_endpoint_reports_registry_and_consensus_state(api_client):
    r=api_client.get("/api/volatility/symbols");assert r.status_code==200
    items={x["symbol"]:x for x in r.json()["symbols"]}
    assert r.json()["default"]=="XRPUSDT"
    assert items["XRPUSDT"]["selection"]=="champions" and items["ADAUSDT"]["selection"]=="provisional"
    assert items["ADAUSDT"]["has_consensus"] is False
    assert items["ADAUSDT"]["artifact_trained_at"]=="artifact-ADAUSDT" and items["ADAUSDT"]["loaded_trained_at"]=="loaded-ADAUSDT"

def test_artifacts_endpoint_uses_ada_manifest_and_registry_predictor(api_client):
    r=api_client.get("/api/models/vol/artifacts",params={"symbol":"ADAUSDT"})
    assert r.status_code==200 and r.json()["artifact_trained_at"]=="artifact-ADAUSDT"
    assert r.json()["loaded_trained_at"]=="loaded-ADAUSDT" and r.json()["consensus"]["created_at"]=="consensus-ADAUSDT"

def test_widen_factor_remains_xrp_only(api_client):
    assert api_client.get("/api/volatility/widen-factor",params={"symbol":"ADAUSDT"}).status_code==404


def test_ada_valid_consensus_selection_and_history_header(api_client, tmp_path):
    path=tmp_path/"ada"/"consensus_ada.json"
    horizons={str(h):{"champion":"GBM"} for h in (1,2,4,24)}
    path.write_text(json.dumps({"symbol":"ADAUSDT","report":{"horizons":horizons}}),encoding="utf-8")
    symbols=api_client.get("/api/volatility/symbols").json()["symbols"]
    ada=next(item for item in symbols if item["symbol"]=="ADAUSDT")
    assert ada["selection"]=="consensus" and ada["has_consensus"] is True
    history=api_client.get("/api/volatility/history",params={"symbol":"ADAUSDT"})
    assert history.status_code==200 and isinstance(history.json(),list)
    assert history.headers["x-vol-selection"]=="consensus"
