from research.scenarios import (evaluate_history, evaluate_pieces, objective_limits,
    objective_v2, rank_alternatives_v2)
from grid.engines.v2_escenarios import V2ScenarioEngine


def test_public_scenarios_exports_and_v2_stays_unavailable():
    assert callable(evaluate_history)
    assert callable(evaluate_pieces)
    assert callable(objective_limits)
    assert callable(objective_v2)
    assert callable(rank_alternatives_v2)
    assert V2ScenarioEngine.available is False
