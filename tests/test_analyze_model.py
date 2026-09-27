from scripts.analyze_model import (
    binomial_p_value, nonoverlapping_signal_indices, parse_args, resolve_model_class,
)
from models.model_b_gru import ModelB
from models.model_c_prophet import ModelC


def test_nonoverlapping_signal_indices_skip_horizon_rows():
    signals = [True, True, True, True, True, False, False, False, True]

    assert nonoverlapping_signal_indices(signals, horizon=4) == [0, 4, 8]


def test_binomial_p_value_matches_known_upper_tail():
    assert binomial_p_value(10, 10, 0.5) == 1 / 1024


def test_model_cli_selects_b_and_c_classes_without_training():
    args_b = parse_args(["--candles", "candles.csv", "--model", "b"])
    args_c = parse_args(["--candles", "candles.csv", "--model", "c"])

    assert resolve_model_class(args_b.model) is ModelB
    assert resolve_model_class(args_c.model) is ModelC
