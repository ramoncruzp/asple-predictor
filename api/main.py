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
from models.model_a_xgboost import ModelA
from models.shadow_predictor import ShadowPredictor
from config.models_config import ACTIVE_INTERVAL, ACTIVE_SYMBOL, SHADOW_ARTIFACT, VOL_ARTIFACT_DIR
from scheduler.prediction_loop import PredictionLoop
from scheduler.verification_loop import VerificationLoop
from api.routes import coins, grid_advisor, models_status, predictions, volatility
from models.volatility.live import VolPredictor
from scheduler.vol_loop import VolLoop

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    key = "" if settings.binance_api_key.startswith("tu_") else settings.binance_api_key
    secret = "" if settings.binance_api_secret.startswith("tu_") else settings.binance_api_secret
    client, db = BinanceClient(key, secret), DBManager(settings.database_url)
    db.seed_coin_if_missing(ACTIVE_SYMBOL, "Símbolo activo del predictor (sembrado)")
    model_a = ModelA()
    artifact = Path(SHADOW_ARTIFACT)
    try:
        if not artifact.is_file():
            raise FileNotFoundError(f"No existe el artefacto activo de modo sombra: {artifact}")
        model_a.load(str(artifact))
    except Exception:
        logging.getLogger(__name__).exception("No se pudo cargar el modelo sombra Model A desde %s", artifact)
        raise
    ensemble = ShadowPredictor(model_a, db)
    prediction_loop, verification_loop = PredictionLoop(client, ensemble, db_manager=db), VerificationLoop(LearningEngine(db, client))
    volatility_manifest = Path(VOL_ARTIFACT_DIR) / "manifest_xrp.json"
    vol_predictor = None
    vol_loop = None
    if volatility_manifest.is_file():
        try:
            vol_predictor = VolPredictor()
            vol_loop = VolLoop(client, vol_predictor, db)
        except Exception:
            logging.getLogger(__name__).exception(
                "No se pudo inicializar volatilidad desde %s; la app seguirá sin ese módulo",
                volatility_manifest,
            )
            vol_predictor = None
            vol_loop = None
    else:
        logging.getLogger(__name__).warning(
            "Volatilidad deshabilitada: no existe el manifest %s", volatility_manifest
        )
    app.state.settings, app.state.db, app.state.client = settings, db, client
    app.state.models, app.state.ensemble = {"model_a": model_a}, ensemble
    app.state.prediction_loop, app.state.verification_loop = prediction_loop, verification_loop
    app.state.vol_predictor, app.state.vol_loop = vol_predictor, vol_loop
    app.state.started_at = time.monotonic()
    prediction_loop.start()
    verification_loop.start()
    if vol_loop is not None:
        vol_loop.start()
    try:
        yield
    finally:
        if vol_loop is not None:
            vol_loop.stop()
        prediction_loop.stop(); verification_loop.stop()

app = FastAPI(title="ASPLE Predictor API", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000", "http://localhost:5173"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.include_router(predictions.router, prefix="/api/predictions", tags=["predictions"])
app.include_router(models_status.router, prefix="/api/models", tags=["models"])
app.include_router(grid_advisor.router, prefix="/api/grid", tags=["grid"])
app.include_router(volatility.router, prefix="/api/volatility", tags=["volatility"])
app.include_router(coins.router, prefix="/api/coins", tags=["coins"])

@app.get("/api/candles")
def candles(symbol: str = ACTIVE_SYMBOL, interval: str = ACTIVE_INTERVAL):
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
