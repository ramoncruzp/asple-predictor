import pytest

from grid.engines import DEFAULT_ENGINE, ENGINES, EngineUnavailable, get_engine


def test_engine_registry_and_unknown_name_default_to_v1():
    assert DEFAULT_ENGINE == "v1"
    assert set(ENGINES) == {"v1", "v2"}
    assert get_engine("v1").available is True
    assert get_engine("v2").available is False
    assert get_engine("desconocido") is get_engine("v1")
    assert get_engine("") is get_engine("v1")


def test_v2_plan_and_review_are_unavailable_but_shadow_is_safe():
    engine = get_engine("v2")
    with pytest.raises(EngineUnavailable, match="motor v2 no disponible todavía"):
        engine.plan(None, {}, plan_kind="revision")
    with pytest.raises(EngineUnavailable, match="motor v2 no disponible todavía"):
        engine.review(None, {}, {}, {})
    assert engine.shadow_review(None, {}, {}, {}) == {
        "decision": "no_disponible", "reason": "motor v2 no disponible todavía"}
