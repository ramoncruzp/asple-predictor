"""Shared API token and loopback authorization rule."""
from __future__ import annotations

import hmac
import ipaddress

from fastapi import HTTPException, Request


def authorize(request: Request) -> None:
    settings = request.app.state.settings
    expected = str(getattr(settings, "grid_api_token", "") or "").strip()
    supplied = request.headers.get("X-API-Token", "")
    if expected:
        if not hmac.compare_digest(expected, supplied):
            raise HTTPException(403, "Credencial X-API-Token inválida o ausente.")
        return
    host = request.client.host if request.client else ""
    try:
        is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = False
    if not is_loopback:
        raise HTTPException(403, "API solo acepta conexiones loopback si GRID_API_TOKEN no está configurado.")
