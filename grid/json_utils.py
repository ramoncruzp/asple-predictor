"""Conversión segura de estructuras de dominio a JSON estricto."""
from __future__ import annotations

import json
import math


def json_safe(value):
    """Sustituye números no finitos por null y normaliza tipos compuestos."""
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if hasattr(value, "tolist"):
        return json_safe(value.tolist())
    if hasattr(value, "item"):
        return json_safe(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def json_dumps_safe(value, **kwargs):
    """Serializa estructuras sanitizadas y rechaza NaN o infinito remanentes."""
    return json.dumps(json_safe(value), allow_nan=False, **kwargs)
