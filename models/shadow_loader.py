"""Best-effort loading of optional, non-operational shadow models."""
from __future__ import annotations

import importlib
import logging
import time
from pathlib import Path


_MODEL_FACTORIES = {
    "model_b": ("model_b_xrp_1h.pt", "models.model_b_gru", "ModelB"),
    "model_c": ("model_c_xrp_1h.joblib", "models.model_c_prophet", "ModelC"),
}


def load_optional_shadow_models(artifact_dir="models/saved", model_factories=None, logger=None):
    """Load B/C independently; one missing/broken artifact never blocks startup.

    Tests may inject tiny fake factories. No prediction or Testnet code runs here.
    """
    log = logger or logging.getLogger(__name__)
    root = Path(artifact_dir)
    factories = model_factories or {}
    loaded, report = {}, {}
    for name, (filename, module_name, class_name) in _MODEL_FACTORIES.items():
        started = time.perf_counter()
        path = root / filename
        item = {"available": False, "artifact": str(path), "duration_ms": None, "reason": None}
        try:
            if not path.is_file():
                raise FileNotFoundError(f"No existe el artefacto: {path}")
            factory = factories.get(name)
            if factory is None:
                factory = getattr(importlib.import_module(module_name), class_name)
            model = factory()
            result = model.load(str(path))
            if result is False:
                if name == "model_b":
                    model_module = importlib.import_module(module_name)
                    torch_error = getattr(model_module, "TORCH_IMPORT_ERROR", None)
                    if torch_error:
                        raise RuntimeError(f"torch no cargó: {torch_error}")
                    raise RuntimeError("load() devolvió False")
                raise RuntimeError(f"{name} no pudo cargar el artefacto")
            loaded[name] = model
            item["available"] = True
        except Exception as exc:
            item["reason"] = f"{type(exc).__name__}: {exc}"
            log.warning("Modelo de sombra %s no disponible: %s", name, item["reason"])
        finally:
            item["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
            item["rss_delta_bytes"] = None
            report[name] = item
    return loaded, report
