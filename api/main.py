"""ASPLE Predictor FastAPI application."""
from __future__ import annotations

try:
    import torch  # noqa: F401 - debe cargarse antes que otras librerias nativas (WinError 1114 en c10.dll)
except (ImportError, OSError):
    pass

import time
import logging
import threading
import requests
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from config.settings import Settings, apply_app_flag_overrides
from data.binance_client import BinanceClient
from data.testnet_client import TestnetClient
from database.db_manager import DBManager
from database.learning_engine import LearningEngine
from models.model_a_xgboost import ModelA
from models.shadow_predictor import ShadowPredictor
from config.models_config import (ACTIVE_INTERVAL, ACTIVE_SYMBOL, MODELS_CONFIG,
                                  SHADOW_ARTIFACT, VOL_ARTIFACT_DIR, VOL_SYMBOL, VOL_WIDEN_COMPUTE_HOUR_UTC)
from scheduler.prediction_loop import PredictionLoop
from scheduler.verification_loop import VerificationLoop
from api.routes import coins, grid_advisor, grid_status, grids, grid_control, grid_account, grid_structure, models_status, model_training, predictions, volatility, app_settings, alerts, metas, intelligence
from models.volatility.live import VolPredictorRegistry
from models.shadow_loader import load_optional_shadow_models
from models.training_jobs import TrainingJobService
from models.coin_onboarding import CoinOnboardingService
from scheduler.vol_loop import VolLoop
from scheduler.backup_loop import BackupLoop
from scheduler.widen_factor_loop import WidenFactorLoop
from api.auth import authorize
from grid.engine import GridEngine
from grid.monitor import GridMonitor
from grid.scan_service import GridScanService
from grid.auto_open import GridAutoOpen
from grid.volatility_provider import VolatilityProvider
from alerts.dispatcher import AlertDispatcher
from grid.meta import MetaCore
from grid.meta_operator import MetaOperator
from grid.intelligence_readiness import IntelligenceReadinessJob
from grid.grid_intelligence import GridIntelligenceJob

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    key = "" if settings.binance_api_key.startswith("tu_") else settings.binance_api_key
    secret = "" if settings.binance_api_secret.startswith("tu_") else settings.binance_api_secret
    client, db = BinanceClient(key, secret), DBManager(settings.database_url)
    apply_app_flag_overrides(settings, db)
    training_job_service = TrainingJobService(db, root=Path(__file__).resolve().parents[1])
    try:
        training_job_service.recover_interrupted()
    except Exception:
        logging.getLogger(__name__).exception("No se pudieron recuperar los trabajos de entrenamiento interrumpidos; continúa el arranque")
    db.seed_coin_if_missing(ACTIVE_SYMBOL, "Símbolo activo del predictor (sembrado)")
    model_a_load_started = time.perf_counter()
    model_a = ModelA()
    artifact = Path(SHADOW_ARTIFACT)
    try:
        if not artifact.is_file():
            raise FileNotFoundError(f"No existe el artefacto activo de modo sombra: {artifact}")
        model_a.load(str(artifact))
    except Exception:
        logging.getLogger(__name__).exception("No se pudo cargar el modelo sombra Model A desde %s", artifact)
        raise
    shadow_models, model_load_report = load_optional_shadow_models()
    ensemble = ShadowPredictor(
        model_a, db, shadow_models=shadow_models,
        shadow_thresholds={name: MODELS_CONFIG[name]["signal_threshold"] for name in shadow_models},
        validation_status={name: MODELS_CONFIG[name]["validation_status"] for name in shadow_models},
    )
    prediction_loop = PredictionLoop(client, ensemble, db_manager=db)
    verification_loop = VerificationLoop(LearningEngine(db, client), model_names=["model_a", *shadow_models])
    volatility_manifest = Path(VOL_ARTIFACT_DIR) / "manifest_xrp.json"
    vol_registry = VolPredictorRegistry()
    vol_registry.load_available()
    coin_onboarding_service = CoinOnboardingService(db, vol_registry, training_job_service,
        root=Path(__file__).resolve().parents[1])
    try:
        coin_onboarding_service.start()
    except Exception:
        logging.getLogger(__name__).exception("No se pudo iniciar el servicio de preparación de monedas")
    vol_predictor = vol_registry.get(VOL_SYMBOL)
    vol_loop = VolLoop(client, vol_registry, db) if vol_registry.ready_symbols() else None
    if vol_predictor is None:
        logging.getLogger(__name__).warning(
            "Volatilidad XRP deshabilitada: no existe un predictor cargado desde %s", volatility_manifest
        )
    grid_monitor = None
    backup_loop = BackupLoop(db, settings)
    widen_factor_loop = WidenFactorLoop(db, hour_utc=VOL_WIDEN_COMPUTE_HOUR_UTC)
    testnet_client = None
    grid_engine = None
    grid_vol_provider = VolatilityProvider(db, data_client=client, registry=vol_registry)
    testnet_key = (settings.testnet_api_key or "").strip()
    testnet_secret = (settings.testnet_api_secret or "").strip()
    valid_testnet_credentials = (
        bool(testnet_key and testnet_secret)
        and not testnet_key.casefold().startswith("tu_")
        and not testnet_secret.casefold().startswith("tu_")
    )
    if valid_testnet_credentials:
        try:
            testnet_client = TestnetClient(
                testnet_key, testnet_secret,
                production_api_key=settings.binance_api_key,
            )
            grid_engine = GridEngine(db, testnet_client, settings)
            grid_engine.vol_provider = grid_vol_provider
            if settings.grid_monitor_enabled:
                grid_monitor = GridMonitor(db, testnet_client, grid_engine, settings,
                                           vol_provider=grid_vol_provider)
        except Exception:
            logging.getLogger(__name__).exception(
                "Grid Testnet integration disabled: client initialization failed"
            )
            testnet_client, grid_engine, grid_monitor = None, None, None
    elif settings.grid_monitor_enabled:
        logging.getLogger(__name__).warning(
            "Grid monitor disabled: faltan credenciales válidas de Binance Testnet"
        )
    if not settings.grid_monitor_enabled:
        logging.getLogger(__name__).warning("Grid monitor disabled by GRID_MONITOR_ENABLED")
    app.state.settings, app.state.db, app.state.client = settings, db, client
    app.state.training_job_service = training_job_service
    app.state.coin_onboarding_service = coin_onboarding_service
    public_market_client = BinanceClient("", "")
    grid_scan_service = GridScanService(db, public_market_client, settings)
    grid_auto_open = GridAutoOpen(grid_scan_service, db, grid_engine, testnet_client, settings)
    alert_dispatcher = AlertDispatcher(db, Settings)
    meta_core = MetaCore(db, testnet_client, grid_engine, settings=settings,
                         monitor=grid_monitor)
    def meta_advisor(symbol, capital, margin_target_pct, days):
        """Llama al servicio Python del Advisor sin solicitud HTTP interna."""
        scope = {"type": "http", "method": "GET", "path": "/api/grid/recommend",
                 "query_string": b"", "headers": [], "app": app,
                 "scheme": "http", "server": ("localhost", 80), "client": ("127.0.0.1", 0)}
        return grid_advisor.recommend(Request(scope), symbol=symbol, capital=capital,
            risk="high", days=days, margin_target_pct=margin_target_pct, range_mode="centrado")

    meta_operator = MetaOperator(meta_core, db, grid_scan_service, grid_engine, settings,
                                 advisor=meta_advisor)
    def apply_grid_intelligence(current_grid, action, candidate):
        if grid_engine is None:
            return {"ok": False, "reason": "motor Testnet no disponible"}
        geometry = candidate.get("geometry") or {}
        grid_id = int(current_grid["id"])
        if str(action).startswith(("mover_rango", "cambiar_n_")):
            return grid_engine.adjust_grid(grid_id, geometry["low"], geometry["high"],
                int(geometry.get("n") or current_grid["n_levels"]),
                reason="grid_intelligence_shadow", details={"source": "GRID_INTEL",
                    "method_version": "grid_shadow_42a_v1", "action": action})
        if str(action).startswith("agregar_bot_"):
            capital = float(geometry.get("affected_capital") or 0)
            if capital < 5.5:
                return {"ok": False, "reason": "efectivo libre inferior al mínimo del bot"}
            from contextlib import nullcontext
            guard = grid_monitor.ops_lock if grid_monitor is not None else nullcontext()
            with guard:
                source_cells = db.get_grid_levels(grid_id)
                reserved = [row for row in source_cells if int(row["level_idx"]) in
                    set(geometry.get("source_level_idxs") or []) and row.get("order_id") is None
                    and float(row.get("held_qty") or 0) <= 0 and str(row.get("state", "")).upper() in {"IDLE", "DONE"}]
                available = sum(float(row.get("capital") or 0) for row in reserved)
                if available + 1e-8 < capital:
                    return {"ok": False, "reason": "el efectivo libre cambió desde el análisis"}
                allocations = [{"level_idx": int(row["level_idx"]), "state": row["state"],
                    "capital": float(row.get("capital") or 0),
                    "capital_base": float(row.get("capital_base") or row.get("capital") or 0)}
                    for row in reserved]
                try:
                    for allocation in allocations:
                        db.update_level(grid_id, allocation["level_idx"], state="INTEL_ALLOCATED",
                            capital=0, capital_base=0)
                    opened = grid_engine.create_grid(str(current_grid["symbol"]), geometry["low"],
                        geometry["high"], 1, capital=capital, strategy="simple",
                        meta_piece_type="individual_bot_grid", origin_grid_id=grid_id,
                        origin_level_allocations=allocations)
                except Exception:
                    for allocation in allocations:
                        db.update_level(grid_id, allocation["level_idx"], state=allocation["state"],
                            capital=allocation["capital"], capital_base=allocation["capital_base"])
                    raise
                return {"ok": True, "bot_grid_id": opened.get("id")}
        if action == "pausar_compras":
            canceled = grid_engine.cancel_grid_orders(grid_id, finalize=False, buy_only=True)
            if canceled.get("cancel_errors") or canceled.get("pending_order_ids"):
                return {"ok": False, "reason": "no se confirmaron todas las cancelaciones de compra"}
            db.merge_grid_params(grid_id, {"intel_pause_buys": True,
                "intel_pause_reason": "break_prob_v2_or_high_volatility"},
                allowed=frozenset({"intel_pause_buys", "intel_pause_reason"}))
            return {"ok": True}
        return {"ok": False, "reason": "acción no reconocida"}

    def grid_intelligence_valuation_known(current_grid):
        try:
            if grid_engine is None:
                return False
            from grid.market_book import book_is_two_sided
            _filters, snapshot, _mid = grid_engine._market_context(str(current_grid["symbol"]))
            return bool(book_is_two_sided(snapshot))
        except Exception:
            logging.getLogger(__name__).exception("No se pudo validar el libro Testnet antes de aplicar Inteligencia")
            return False

    grid_intel_shadow_job = GridIntelligenceJob(
        db, public_market_client, meta_operator._symbol_filters, advisor=meta_advisor,
        settings=grid_engine.settings if grid_engine is not None else settings,
        vol_provider=grid_vol_provider,
        apply_callback=apply_grid_intelligence,
        valuation_checker=grid_intelligence_valuation_known,
        ops_lock=grid_monitor.ops_lock if grid_monitor is not None else None)
    app.state.grid_scan_service = grid_scan_service
    app.state.public_market_client = public_market_client
    app.state.grid_engine, app.state.testnet_client = grid_engine, testnet_client
    app.state.vol_provider = grid_vol_provider
    app.state.grid_auto_open = grid_auto_open
    app.state.alert_dispatcher = alert_dispatcher
    app.state.meta_core = meta_core
    app.state.meta_operator = meta_operator
    app.state.grid_intel_shadow_job = grid_intel_shadow_job
    app.state.models, app.state.ensemble = {"model_a": model_a, **shadow_models}, ensemble
    app.state.model_load_report = {
        "model_a": {"available": True, "artifact": str(artifact),
                    "duration_ms": round((time.perf_counter() - model_a_load_started) * 1000, 2),
                    "rss_delta_bytes": None},
        **model_load_report,
    }
    app.state.prediction_loop, app.state.verification_loop = prediction_loop, verification_loop
    app.state.vol_predictor, app.state.vol_loop = vol_predictor, vol_loop
    app.state.vol_registry = vol_registry
    app.state.grid_monitor = grid_monitor
    app.state.backup_loop = backup_loop
    app.state.widen_factor_loop = widen_factor_loop
    app.state.started_at = time.monotonic()
    def evaluate_grid_readiness():
        scope = {"type": "http", "method": "GET", "path": "/api/intelligence",
            "query_string": b"", "headers": [], "app": app, "scheme": "http",
            "server": ("localhost", 80), "client": ("127.0.0.1", 0)}
        summary = intelligence._intelligence_summary(Request(scope), page=1, page_size=1)
        return summary.get("readiness_switches") or {}
    grid_intelligence_job = IntelligenceReadinessJob(evaluate_grid_readiness, alert_dispatcher)
    app.state.grid_intelligence_job = grid_intelligence_job
    grid_intelligence_job.start()
    grid_intel_shadow_job.start()
    prediction_loop.start()
    verification_loop.start()
    if vol_loop is not None:
        vol_loop.start()
    if vol_predictor is not None:
        widen_factor_loop.start()
    backup_loop.start()
    if grid_monitor is not None:
        grid_monitor.start()
    if bool(getattr(settings, "meta_mode_enabled", False)) and testnet_client is not None:
        meta_core.start()
        meta_operator.start()
    grid_auto_open.start()
    alerts_token = str(getattr(settings, "telegram_bot_token", "") or "").strip()
    alerts_chat_id = str(getattr(settings, "telegram_chat_id", "") or "").strip()
    if settings.alerts_enabled and alerts_token and alerts_chat_id:
        alert_dispatcher.start(settings.alerts_poll_seconds)
    elif settings.alerts_enabled:
        logging.getLogger(__name__).warning(
            "Alert dispatcher disabled: faltan TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID"
        )
    try:
        yield
    finally:
        try:
            coin_onboarding_service.stop()
        except Exception:
            logging.getLogger(__name__).exception("No se pudo detener el servicio de preparación de monedas")
        if grid_monitor is not None:
            grid_monitor.stop()
        grid_intelligence_job.stop()
        grid_intel_shadow_job.stop()
        meta_core.stop()
        meta_operator.stop()
        grid_auto_open.stop()
        alert_dispatcher.stop()
        if vol_loop is not None:
            vol_loop.stop()
        if vol_predictor is not None:
            widen_factor_loop.stop()
        backup_loop.stop()
        prediction_loop.stop(); verification_loop.stop()

app = FastAPI(title="ASPLE Predictor API", version="1.0.0", lifespan=lifespan)
PROCESS_STARTED_AT = time.monotonic()
SLOW_REQUEST_THRESHOLD_SECONDS = 3.0
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000", "http://localhost:5173"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.middleware("http")
async def protect_api_routes(request: Request, call_next):
    if request.url.path.startswith("/api/") and request.url.path != "/api/health":
        try:
            authorize(request)
        except Exception as exc:
            from fastapi import HTTPException
            if isinstance(exc, HTTPException):
                return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
            raise
    return await call_next(request)

@app.middleware("http")
async def log_slow_requests(request: Request, call_next):
    started, status_code = time.perf_counter(), 500
    try:
        response = await call_next(request); status_code = response.status_code; return response
    finally:
        elapsed = time.perf_counter() - started
        if elapsed > SLOW_REQUEST_THRESHOLD_SECONDS:
            logging.getLogger("asple.slow").warning("%s %s status=%s duration_ms=%.1f active_threads=%d", request.method, request.url.path, status_code, elapsed * 1000, threading.active_count())

app.include_router(predictions.router, prefix="/api/predictions", tags=["predictions"])
app.include_router(models_status.router, prefix="/api/models", tags=["models"])
app.include_router(model_training.router, prefix="/api/models", tags=["model-training"])
app.include_router(grid_advisor.router, prefix="/api/grid", tags=["grid"])
app.include_router(volatility.router, prefix="/api/volatility", tags=["volatility"])
app.include_router(coins.router, prefix="/api/coins", tags=["coins"])
app.include_router(grids.router, prefix="/api/grids", tags=["grids"])
app.include_router(grid_status.router, tags=["grid-status"])
app.include_router(grid_control.router, prefix="/api/grids", tags=["grid-control"])
app.include_router(grid_structure.router, prefix="/api/grids", tags=["grid-structure"])
app.include_router(grid_account.router)
app.include_router(app_settings.router, prefix="/api/settings", tags=["app-settings"])
app.include_router(alerts.router, prefix="/api/alerts", tags=["alerts"])
app.include_router(metas.router, prefix="/api/metas", tags=["metas"])
app.include_router(intelligence.router, prefix="/api/intelligence", tags=["intelligence"])

@app.get("/api/health")
async def health(request: Request):
    started = getattr(request.app.state, "started_at", PROCESS_STARTED_AT)
    return {"status": "ok", "uptime_s": max(0, int(time.monotonic() - started))}

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
