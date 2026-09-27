"""Load CPU PyTorch before XGBoost to avoid Windows DLL initialization conflicts."""

try:
    import torch as _torch
except (ImportError, OSError):
    _torch = None
