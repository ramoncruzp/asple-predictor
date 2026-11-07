"""Resolución acotada del proxy usado por el servicio de Binance."""
from __future__ import annotations

import os
import shlex
import subprocess
from urllib.parse import urlsplit


SERVICE_UNIT = "asple-predictor"


def _proxy_from_environment(environment):
    values = {str(key).upper(): str(value).strip() for key, value in environment.items()}
    return values.get("HTTPS_PROXY") or values.get("HTTP_PROXY")


def resolve_binance_proxy(*, environ=None, runner=subprocess.run, unit=SERVICE_UNIT):
    """Devuelve (URL, origen), leyendo de systemd solo las dos claves permitidas."""
    environment = os.environ if environ is None else environ
    proxy = _proxy_from_environment(environment)
    if proxy:
        return proxy, "entorno"
    try:
        result = runner(["systemctl", "show", str(unit), "-p", "Environment", "--value"],
            capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None, None
    if getattr(result, "returncode", 1) != 0:
        return None, None
    try:
        entries = shlex.split(result.stdout or "")
    except ValueError:
        return None, None
    allowed = {}
    for entry in entries:
        key, separator, value = entry.partition("=")
        if separator and key.upper() in {"HTTPS_PROXY", "HTTP_PROXY"} and value.strip():
            allowed[key.upper()] = value.strip()
    proxy = allowed.get("HTTPS_PROXY") or allowed.get("HTTP_PROXY")
    return (proxy, "servicio") if proxy else (None, None)


def proxy_label(proxy, source):
    """Describe el endpoint sin exponer credenciales ni otras variables."""
    if not proxy:
        return "Proxy: ninguno — puede fallar desde S1"
    parsed = urlsplit(proxy if "://" in proxy else f"http://{proxy}")
    host = parsed.hostname or "proxy configurado"
    if ":" in host:
        host = f"[{host}]"
    endpoint = f"{host}:{parsed.port}" if parsed.port else host
    return f"Proxy: {endpoint} ({source or 'configurado'})"
