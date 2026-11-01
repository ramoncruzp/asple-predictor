"""Guardas contra efectos laterales durante evaluaciones en sombra."""
from contextlib import contextmanager
from contextvars import ContextVar


SHADOW_MODE = ContextVar("asple_meta_shadow_mode", default=False)


class ShadowSideEffectError(RuntimeError):
    """Señala que una evaluación en sombra intentó modificar el sistema."""


@contextmanager
def shadow_execution_context():
    token = SHADOW_MODE.set(True)
    try:
        yield
    finally:
        SHADOW_MODE.reset(token)


def reject_shadow_side_effect(action):
    if SHADOW_MODE.get():
        raise ShadowSideEffectError(f"shadow_violation: operación bloqueada ({action})")
