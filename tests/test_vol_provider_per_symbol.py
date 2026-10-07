from datetime import datetime, timedelta, timezone
from math import log, sqrt
import numpy as np
import pandas as pd
import pytest
import grid.volatility_provider as provider_module
from grid.policy import DEFAULT_SMART_PARAMS, break_prob
from grid.volatility_provider import VolatilityProvider
NOW = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)
def row(symbol, horizon, model, sigma, *, age_h=0, champion=True):
    return {"symbol": symbol, "horizon_h": horizon, "model_name": model, "pred_logvol_cal": log(sigma), "forecast_at": NOW-timedelta(hours=age_h), "is_champion": champion}
class DB:
    def __init__(self, rows): self.rows, self.forecast_calls = rows, []
    def get_latest_vol_forecasts(self, symbol):
        self.forecast_calls.append(symbol)
        return [item for item in self.rows if item["symbol"] == symbol]
class DataClient:
    def __init__(self): self.calls=[]
    def get_historical_klines(self, symbol, interval, lookback_days):
        self.calls.append((symbol, interval, lookback_days))
        return pd.DataFrame({"close": np.exp(np.cumsum(np.sin(np.arange(120))*.001)+5)})
def patch_symbol(monkeypatch, tmp_path, symbol, champions, *, ready=True, manifest=None):
    path=tmp_path/f"manifest_{symbol.lower()}.json"
    path.write_text(__import__("json").dumps(manifest or {}),encoding="utf-8")
    monkeypatch.setattr(provider_module,"vol_champions",lambda _symbol:(champions,False))
    monkeypatch.setattr(provider_module,"coin_is_ready",lambda _db,_registry,_symbol:ready)
    monkeypatch.setattr(provider_module,"vol_manifest_path",lambda _symbol:path)
    return path
def test_xrp_horizon_24_preserves_existing_sigma_source_and_regime():
    db=DB([row("XRPUSDT",24,"NexoHAR",.01)])
    provider=VolatilityProvider(db,manifest={"regime_percentiles_24h":{"p33":.005,"p66":.02}},clock=lambda:NOW)
    view=provider.get("XRPUSDT",24)
    assert view.sigma_24h==pytest.approx(.01*sqrt(24))
    assert (view.source,view.regime,view.stale)==("model","NORMAL",False)
    assert view.horizon_h==24 and view.sigma_h==pytest.approx(.01*sqrt(24))
    assert not view.fallback and view.fallback_reason is None
def test_xrp_horizon_4_uses_its_champion_and_preserves_policy_sigma_identity():
    db=DB([row("XRPUSDT",4,"GBM",.01),row("XRPUSDT",24,"NexoHAR",.03)])
    view=VolatilityProvider(db,clock=lambda:NOW).get("XRPUSDT",4)
    assert view.sigma_24h==pytest.approx(.01*sqrt(24)) and view.sigma_h==pytest.approx(.01*2)
    assert view.horizon_h==4 and view.source=="model"
    effective={**DEFAULT_SMART_PARAMS,"horizon_h":4}
    _,_,_,policy_sigma=break_prob(100,90,110,view.sigma_24h,effective)
    assert policy_sigma==pytest.approx(view.sigma_h*effective["sigma_scale"])
def test_ready_ada_uses_ada_champion_and_ada_manifest(monkeypatch,tmp_path):
    champions={1:"ADAGBM",2:"ADAGBM",4:"ADAGBM",24:"ADAHAR"}
    patch_symbol(monkeypatch,tmp_path,"ADAUSDT",champions,manifest={"regime_percentiles_24h":{"p33":.01,"p66":.02}})
    db=DB([row("ADAUSDT",24,"ADAHAR",.03),row("ADAUSDT",24,"GBM",.001,champion=False),row("XRPUSDT",24,"NexoHAR",.9)])
    view=VolatilityProvider(db,clock=lambda:NOW).get("ADAUSDT",24)
    assert view.source=="model" and view.regime=="AGITADO"
    assert view.sigma_24h==pytest.approx(.03*sqrt(24)) and db.forecast_calls==["ADAUSDT"]
def test_unready_ada_uses_only_its_realized_history(monkeypatch,tmp_path):
    patch_symbol(monkeypatch,tmp_path,"ADAUSDT",{24:"ADAHAR"},ready=False)
    db,data=DB([row("XRPUSDT",24,"NexoHAR",.9)]),DataClient()
    view=VolatilityProvider(db,clock=lambda:NOW,data_client=data).get("ADAUSDT",4)
    assert view is not None and view.source=="realized" and view.horizon_h==4
    assert view.sigma_h==pytest.approx(view.sigma_24h*sqrt(4/24))
    assert data.calls==[("ADAUSDT","1h",7)] and db.forecast_calls==[]
@pytest.mark.parametrize("targets,reason",[([row("XRPUSDT",4,"GBM",.03,age_h=3)],"stale"),([],"forecast_unavailable")])
def test_short_horizon_falls_back_to_fresh_xrp_24h(targets,reason):
    db=DB(targets+[row("XRPUSDT",24,"NexoHAR",.01)])
    view=VolatilityProvider(db,clock=lambda:NOW).get("XRPUSDT",4)
    assert view is not None and view.fallback and view.fallback_reason==reason
    assert view.sigma_24h==pytest.approx(.01*sqrt(24)) and view.sigma_h==pytest.approx(.02)
def test_stale_xrp_24h_remains_unavailable():
    provider=VolatilityProvider(DB([row("XRPUSDT",24,"NexoHAR",.01,age_h=3)]),clock=lambda:NOW)
    assert provider.get("XRPUSDT",24) is None and provider.last_reason=="stale"
