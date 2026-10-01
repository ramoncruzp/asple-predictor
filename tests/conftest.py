"""Shared pytest fixtures, including the opt-in offline network guard."""

import ipaddress
import os
import socket

import pytest


@pytest.fixture(autouse=True)
def block_network_for_offline_tests(request, monkeypatch):
    """Reject accidental networking in unmarked tests when ASPLE_OFFLINE=1."""
    if os.environ.get("ASPLE_OFFLINE") != "1":
        return
    if request.node.get_closest_marker("live") is not None:
        return

    message = (
        f"ASPLE_OFFLINE=1 blocked network access from {request.node.nodeid}; "
        "mark genuine network integration tests with @pytest.mark.live"
    )

    def is_loopback(host):
        if host is None or str(host).casefold() == "localhost":
            return True
        try:
            return ipaddress.ip_address(str(host).strip("[]")).is_loopback
        except ValueError:
            return False

    original_connect = socket.socket.connect

    def guarded_connect(_sock, address):
        host = address[0] if isinstance(address, tuple) and address else address
        if not is_loopback(host):
            raise RuntimeError(f"{message} (socket.connect to {address!r})")
        return original_connect(_sock, address)

    original_getaddrinfo = socket.getaddrinfo

    def guarded_getaddrinfo(host, *args, **kwargs):
        if host is not None:
            if not is_loopback(host):
                raise RuntimeError(f"{message} (socket.getaddrinfo for {host!r})")
        return original_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)

try:
    import torch as _torch
except (ImportError, OSError):
    _torch = None
