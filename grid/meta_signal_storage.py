from __future__ import annotations

import hashlib
import json


MODEL_DETAIL_FIELDS = ("realized_volatility_24h", "volatility_forecasts", "direction_predictions")


def compact_signal_payload(signals):
    """Extrae bloques repetidos de detalle de modelos sin redondear ni perder campos."""
    compact = json.loads(json.dumps(signals or {}, sort_keys=True, separators=(",", ":"), default=str))
    details, refs = {}, {}
    by_symbol = compact.get("point_in_time_by_symbol")
    if isinstance(by_symbol, dict):
        for symbol, snapshot in by_symbol.items():
            source = (snapshot or {}).get("signals")
            if not isinstance(source, dict):
                continue
            model_detail = {key: source.pop(key) for key in MODEL_DETAIL_FIELDS if key in source}
            if not model_detail:
                continue
            payload = json.dumps(model_detail, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            content_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            details[content_hash] = payload
            refs[str(symbol)] = content_hash
        if refs:
            compact["model_details_refs"] = refs
    return compact, details


def expand_signal_payload(signals, detail_payloads):
    """Restaura en memoria el formato lógico anterior para lectores existentes."""
    expanded = json.loads(json.dumps(signals or {}, sort_keys=True, separators=(",", ":"), default=str))
    refs = expanded.pop("model_details_refs", {}) or {}
    by_symbol = expanded.get("point_in_time_by_symbol") or {}
    for symbol, content_hash in refs.items():
        payload = detail_payloads.get(content_hash)
        snapshot = by_symbol.get(symbol)
        target = (snapshot or {}).get("signals") if isinstance(snapshot, dict) else None
        if payload is None or not isinstance(target, dict):
            continue
        detail = json.loads(payload) if isinstance(payload, str) else dict(payload)
        target.update(detail)
    return expanded
