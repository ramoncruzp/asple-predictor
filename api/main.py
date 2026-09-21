"""ASPLE Predictor FastAPI application."""
from __future__ import annotations
import time
import logging
import requests
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from config.settings import Settings
from data.binance_client import BinanceClient
from database.db_manager import DBManager
from database.learning_engine import LearningEngine
from models.model_b_gru import ModelB
from models.model_a_xgboost import ModelA
from models.model_c_prophet import ModelC
from models.ensemble import EnsemblePredictor
try:
    from models.model_d_tft import ModelD
except (ImportError, OSError, Exception) as e:
    print(f"[API] Model D no disponible: {e}")
    ModelD = None
from scheduler.prediction_loop import PredictionLoop
from scheduler.verification_loop import VerificationLoop
from api.routes import grid_advisor, models_status, predictions

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    key = "" if settings.binance_api_key.startswith("tu_") else settings.binance_api_key
    secret = "" if settings.binance_api_secret.startswith("tu_") else settings.binance_api_secret
    client, db = BinanceClient(key, secret), DBManager(settings.database_url)
    models = [ModelA(), ModelB(), ModelC()]
    for model, filename in zip(models, ("model_a_xrp_4h.joblib", "model_b_xrp_4h.pt", "model_c_xrp_4h.joblib")):
        model.load(str(Path("models/saved") / filename))
    model_d = None
    model_d_path = Path("models/saved/model_d_xrp_4h.pt")
    if ModelD is not None and model_d_path.exists():
        model_d = ModelD()
        model_d.load(str(model_d_path))
    ensemble = EnsemblePredictor(*models, db, model_d=model_d)
    prediction_loop, verification_loop = PredictionLoop(client, ensemble), VerificationLoop(LearningEngine(db, client))
    app.state.settings, app.state.db, app.state.client = settings, db, client
    app.state.models, app.state.ensemble = dict(zip(("model_a", "model_b", "model_c"), models)), ensemble
    if model_d is not None:
        app.state.models["model_d"] = model_d
    app.state.prediction_loop, app.state.verification_loop = prediction_loop, verification_loop
    app.state.started_at = time.monotonic()
    prediction_loop.start()
    verification_loop.start()
    try:
        yield
    finally:
        prediction_loop.stop(); verification_loop.stop()

app = FastAPI(title="ASPLE Predictor API", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000", "http://localhost:5173"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.include_router(predictions.router, prefix="/api/predictions", tags=["predictions"])
app.include_router(models_status.router, prefix="/api/models", tags=["models"])
app.include_router(grid_advisor.router, prefix="/api/grid", tags=["grid"])

@app.get("/api/candles")
def candles(symbol: str = "XRPUSDT", interval: str = "4h"):
    normalized_symbol = symbol.strip().upper().replace("/", "")
    normalized_interval = interval.strip().lower()
    if not normalized_symbol.endswith("USDT"):
        raise ValueError("symbol debe ser un par USDT")
    if normalized_interval not in {"1h", "4h", "12h", "1d", "1w"}:
        raise ValueError("interval debe ser uno de: 1h, 4h, 12h, 1d, 1w")
    response = requests.get(
        "https://api.binance.com/api/v3/klines",
        params={"symbol": normalized_symbol, "interval": normalized_interval, "limit": 300},
        timeout=20,
    )
    response.raise_for_status()
    return [
        {"time": int(row[0] / 1000), "open": float(row[1]), "high": float(row[2]),
         "low": float(row[3]), "close": float(row[4]), "volume": float(row[5])}
        for row in response.json()
    ]

@app.get("/", response_model=None)
def root(request: Request):
    if "text/html" in request.headers.get("accept", ""):
        return FileResponse("frontend/index.html")
    return {"status": "running", "models_loaded": len(getattr(app.state, "models", {})), "uptime": f"{int(time.monotonic() - app.state.started_at)}s"}

app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
